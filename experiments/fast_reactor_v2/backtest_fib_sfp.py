"""Backtest the fib_sfp trigger over real BTCUSDT 1m kline history.

Pulls 30 days of data from Bybit V5 public kline endpoint (no auth needed),
replays through FibSfpState.evaluate() for every closed bar, simulates the
fast-reactor execution model, and reports the acceptance gates from
AGENTS.md §6:

    Gate A: ≥ 40% win rate
    Gate B: ≥ 5 fires per week
    Gate C: EV > $0 per trade

Execution model mirrors fast_reactor.py's FAST_TP_PCT / FAST_SL_PCT:
    TP = 0.4 % from entry
    SL = 0.25 % from entry
    Max hold = 30 bars (30 min)
    Direction: long fires = entry at close, short = same
    Walk forward bar-by-bar, exit on first TP or SL hit, else timeout at MARK.

Cost model:
    fee:      0.05 % taker × 2 sides = 0.10 % per round-trip
    slippage: 0.02 % per side          = 0.04 % per round-trip
    total:    0.14 % cost per trade
    notional: $3200 (risk $8 / SL 0.25 %)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Make the trigger module importable
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fib_sfp_trigger import FibSfpState  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SYMBOL          = "BTCUSDT"
INTERVAL        = "1"                              # 1-minute klines
TP_PCT          = 0.004                            # 0.4 %
SL_PCT          = 0.0025                           # 0.25 %
MAX_HOLD_BARS   = 30
RISK_USD        = 8.0
NOTIONAL_USD    = RISK_USD / SL_PCT                # $3200 @ 0.25 % SL
ROUNDTRIP_COST  = 0.0014                           # 0.10 % fee + 0.04 % slip
DEFAULT_DAYS    = 30
DATA_FILE       = HERE / "data" / "btc_1m_klines.jsonl"

# ---------------------------------------------------------------------------
# Bybit V5 kline fetcher
# ---------------------------------------------------------------------------
def fetch_klines_chunk(start_ms: int, end_ms: int) -> list[dict]:
    """Pull up to 1000 1m klines. Returns OLDEST→NEWEST.
    Bybit V5 returns NEWEST first; we reverse before yielding."""
    params = urllib.parse.urlencode({
        "category": "linear", "symbol": SYMBOL,
        "interval": INTERVAL,
        "start": start_ms, "end": end_ms, "limit": 1000,
    })
    url = f"https://api.bybit.com/v5/market/kline?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "sygnif-backtest/1.0"})
    body = json.loads(urllib.request.urlopen(req, timeout=20).read())
    if body.get("retCode") != 0:
        raise RuntimeError(f"Bybit retCode={body.get('retCode')}: {body.get('retMsg')}")
    raw = body["result"]["list"]
    bars = []
    for r in reversed(raw):
        bars.append({
            "ts_ms_open": int(r[0]),
            "open":       float(r[1]),
            "high":       float(r[2]),
            "low":        float(r[3]),
            "close":      float(r[4]),
            "volume":     float(r[5]),
            "turnover":   float(r[6]),
            "confirm":    True,
        })
    return bars


def fetch_klines(days: int) -> list[dict]:
    """Fetch `days` of 1m klines, oldest→newest, deduplicated."""
    end_ms   = int(time.time() * 1000)
    start_ms = end_ms - days * 24 * 3600 * 1000
    all_bars: dict[int, dict] = {}
    cursor = start_ms
    page = 0
    while cursor < end_ms:
        chunk_end = min(cursor + 999 * 60_000, end_ms)
        try:
            chunk = fetch_klines_chunk(cursor, chunk_end)
        except Exception as e:
            print(f"  ! chunk fetch err at {cursor}: {e}", file=sys.stderr)
            break
        if not chunk:
            break
        for b in chunk:
            all_bars[b["ts_ms_open"]] = b
        cursor = chunk[-1]["ts_ms_open"] + 60_000
        page += 1
        if page % 5 == 0:
            print(f"    fetched page {page} ({len(all_bars):,} bars) "
                  f"up to {dt.datetime.fromtimestamp(cursor/1000, dt.timezone.utc):%Y-%m-%d %H:%M}",
                  flush=True)
        # gentle rate-limit
        time.sleep(0.15)
    bars = sorted(all_bars.values(), key=lambda b: b["ts_ms_open"])
    return bars


def load_or_fetch(days: int) -> list[dict]:
    """Load cached klines if present and fresh enough; else fetch."""
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    if DATA_FILE.exists():
        cached = [json.loads(l) for l in DATA_FILE.open()]
        if cached:
            age_h = (time.time() - cached[-1]["ts_ms_open"] / 1000) / 3600
            span_d = (cached[-1]["ts_ms_open"] - cached[0]["ts_ms_open"]) / (86400_000)
            if age_h < 2 and span_d >= days * 0.95:
                print(f"  using cached klines: {len(cached):,} bars, span {span_d:.1f}d, "
                      f"newest {age_h:.1f}h old", flush=True)
                return cached
    print(f"  fetching {days} days of {SYMBOL} 1m klines from Bybit...", flush=True)
    bars = fetch_klines(days)
    if bars:
        with DATA_FILE.open("w") as f:
            for b in bars:
                f.write(json.dumps(b, separators=(",", ":")) + "\n")
        print(f"  cached {len(bars):,} bars to {DATA_FILE.name}", flush=True)
    return bars


# ---------------------------------------------------------------------------
# Trade simulator
# ---------------------------------------------------------------------------
def simulate_trade(bars: list[dict], i_entry: int, direction: str,
                   entry_price: float) -> dict:
    """Walk forward from bar i_entry+1 until TP, SL, or timeout. Return outcome."""
    side = 1 if direction == "long" else -1
    tp_price = entry_price * (1 + side * TP_PCT)
    sl_price = entry_price * (1 - side * SL_PCT)
    n = len(bars)
    for j in range(i_entry + 1, min(i_entry + 1 + MAX_HOLD_BARS, n)):
        bar = bars[j]
        hi, lo = bar["high"], bar["low"]
        # In a long: TP if hi ≥ tp_price, SL if lo ≤ sl_price.
        # In a short: TP if lo ≤ tp_price, SL if hi ≥ sl_price.
        # When both hit in same bar, conservative model assumes SL hit first
        if direction == "long":
            sl_hit = lo <= sl_price
            tp_hit = hi >= tp_price
        else:
            sl_hit = hi >= sl_price
            tp_hit = lo <= tp_price
        if sl_hit and tp_hit:
            outcome = "sl"; exit_price = sl_price
        elif sl_hit:
            outcome = "sl"; exit_price = sl_price
        elif tp_hit:
            outcome = "tp"; exit_price = tp_price
        else:
            continue
        # PnL
        pnl_pct  = (exit_price - entry_price) / entry_price * side
        pnl_usd  = pnl_pct * NOTIONAL_USD
        pnl_net  = pnl_usd - ROUNDTRIP_COST * NOTIONAL_USD
        hold_min = j - i_entry
        return {"outcome": outcome, "exit_price": exit_price,
                "pnl_usd_gross": pnl_usd, "pnl_usd_net": pnl_net,
                "pnl_pct": pnl_pct, "hold_min": hold_min}
    # timeout — exit at last bar close
    exit_price = bars[min(i_entry + MAX_HOLD_BARS, n - 1)]["close"]
    pnl_pct  = (exit_price - entry_price) / entry_price * side
    pnl_usd  = pnl_pct * NOTIONAL_USD
    pnl_net  = pnl_usd - ROUNDTRIP_COST * NOTIONAL_USD
    return {"outcome": "timeout", "exit_price": exit_price,
            "pnl_usd_gross": pnl_usd, "pnl_usd_net": pnl_net,
            "pnl_pct": pnl_pct, "hold_min": MAX_HOLD_BARS}


# ---------------------------------------------------------------------------
# Backtest driver
# ---------------------------------------------------------------------------
def run_backtest(bars: list[dict], maxlen=240, lookback=50,
                 fib_proximity=0.01) -> dict:
    state = FibSfpState(maxlen=maxlen, lookback=lookback,
                        fib_proximity=fib_proximity, min_bars_for_signal=lookback)
    fires = []
    cooldown_until = {"long": 0, "short": 0}   # don't refire same direction within 60s
    open_count = 0
    MAX_OPEN = 3

    for i, bar in enumerate(bars):
        payload = state.evaluate(bar)
        if not payload:
            continue
        direction = payload["direction"]
        ts = bar["ts_ms_open"]
        # Cooldown gate (matches fast-reactor's per-direction cooldown)
        if ts < cooldown_until[direction]:
            continue
        if open_count >= MAX_OPEN:
            continue
        # Simulate
        trade = simulate_trade(bars, i, direction, bar["close"])
        fires.append({
            "ts_ms": ts,
            "ts_utc": dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc)
                          .isoformat(timespec="seconds"),
            "direction": direction,
            "entry": bar["close"],
            "thesis": payload["meta"]["thesis"],
            "fib_0_618": payload["meta"]["fib_0_618"],
            "fib_0_382": payload["meta"]["fib_0_382"],
            "sfp_kind": payload["meta"]["sfp_kind"],
            **trade,
        })
        cooldown_until[direction] = ts + 60_000

    # Aggregate
    if not fires:
        return {"n": 0, "fires_per_week": 0.0, "win_rate": 0.0,
                "ev_gross": 0.0, "ev_net": 0.0, "fires": []}
    wins   = [f for f in fires if f["pnl_usd_net"] > 0]
    losses = [f for f in fires if f["pnl_usd_net"] <= 0]
    span_d = (bars[-1]["ts_ms_open"] - bars[0]["ts_ms_open"]) / (86400_000)
    return {
        "n":              len(fires),
        "span_days":      round(span_d, 2),
        "fires_per_week": round(len(fires) / max(span_d, 1) * 7, 2),
        "win_rate":       round(len(wins) / len(fires) * 100, 1),
        "wins":           len(wins),
        "losses":         len(losses),
        "tp_count":       sum(1 for f in fires if f["outcome"] == "tp"),
        "sl_count":       sum(1 for f in fires if f["outcome"] == "sl"),
        "timeout_count":  sum(1 for f in fires if f["outcome"] == "timeout"),
        "ev_gross":       round(sum(f["pnl_usd_gross"] for f in fires) / len(fires), 3),
        "ev_net":         round(sum(f["pnl_usd_net"] for f in fires) / len(fires), 3),
        "total_pnl_gross": round(sum(f["pnl_usd_gross"] for f in fires), 2),
        "total_pnl_net":   round(sum(f["pnl_usd_net"] for f in fires), 2),
        "longs":          sum(1 for f in fires if f["direction"] == "long"),
        "shorts":         sum(1 for f in fires if f["direction"] == "short"),
        "fires":          fires,
    }


# ---------------------------------------------------------------------------
# Acceptance gates (AGENTS.md §6)
# ---------------------------------------------------------------------------
def check_gates(result: dict) -> dict:
    return {
        "win_rate_>= 40":  ("PASS" if result["win_rate"] >= 40 else "FAIL",
                            f"{result['win_rate']:.1f}%"),
        "fires_per_week_>=5": ("PASS" if result["fires_per_week"] >= 5 else "FAIL",
                                f"{result['fires_per_week']:.2f}/wk"),
        "ev_net_>0":       ("PASS" if result["ev_net"] > 0 else "FAIL",
                            f"${result['ev_net']:+.3f}/trade"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--fib-proximity", type=float, default=0.01)
    ap.add_argument("--lookback", type=int, default=50)
    ap.add_argument("--maxlen", type=int, default=240)
    args = ap.parse_args()

    print("=" * 78)
    print(f"  fib_sfp BACKTEST — {SYMBOL} 1m, last {args.days} days")
    print(f"  TP={TP_PCT*100:.2f}%  SL={SL_PCT*100:.2f}%  cost={ROUNDTRIP_COST*100:.2f}%/RT")
    print(f"  lookback={args.lookback}  maxlen={args.maxlen}  "
          f"fib_proximity={args.fib_proximity*100:.1f}%")
    print("=" * 78)

    bars = load_or_fetch(args.days)
    if len(bars) < args.maxlen + 100:
        print(f"  ! not enough bars ({len(bars)}), need ≥ {args.maxlen + 100}")
        sys.exit(2)
    print(f"  data: {len(bars):,} bars from "
          f"{dt.datetime.fromtimestamp(bars[0]['ts_ms_open']/1000, dt.timezone.utc):%Y-%m-%d %H:%M} → "
          f"{dt.datetime.fromtimestamp(bars[-1]['ts_ms_open']/1000, dt.timezone.utc):%Y-%m-%d %H:%M} UTC")

    t0 = time.time()
    result = run_backtest(bars, maxlen=args.maxlen, lookback=args.lookback,
                          fib_proximity=args.fib_proximity)
    elapsed = time.time() - t0
    print(f"  replay finished in {elapsed:.1f}s\n")

    print(f"  fires:           {result['n']}  ({result['longs']} long, {result['shorts']} short)")
    print(f"  span:            {result['span_days']:.1f} days  →  {result['fires_per_week']:.2f}/week")
    print(f"  win rate:        {result['win_rate']:.1f}%  ({result['wins']}W / {result['losses']}L)")
    print(f"  outcomes:        tp={result['tp_count']}  sl={result['sl_count']}  timeout={result['timeout_count']}")
    print(f"  EV gross:        ${result['ev_gross']:+.3f}/trade   (total ${result['total_pnl_gross']:+.2f})")
    print(f"  EV net:          ${result['ev_net']:+.3f}/trade   (total ${result['total_pnl_net']:+.2f})")
    print()
    print(f"  Acceptance gates (AGENTS.md §6):")
    gates = check_gates(result)
    all_pass = True
    for g, (verdict, val) in gates.items():
        marker = "✓" if verdict == "PASS" else "✗"
        print(f"    {marker} {g:<24s} {val:<20s} [{verdict}]")
        all_pass = all_pass and verdict == "PASS"
    print()
    print(f"  OVERALL: {'PASS — safe to merge' if all_pass else 'FAIL — do not deploy'}")

    # Persist
    out = HERE / "data" / "backtest_result.json"
    snapshot = {k: v for k, v in result.items() if k != "fires"}
    snapshot["fires_sample"] = result["fires"][:20]
    snapshot["gates"] = {k: list(v) for k, v in gates.items()}
    snapshot["overall"] = "PASS" if all_pass else "FAIL"
    snapshot["params"] = {"TP_PCT": TP_PCT, "SL_PCT": SL_PCT,
                          "ROUNDTRIP_COST": ROUNDTRIP_COST,
                          "MAX_HOLD_BARS": MAX_HOLD_BARS,
                          "NOTIONAL_USD": NOTIONAL_USD,
                          "fib_proximity": args.fib_proximity,
                          "lookback": args.lookback, "maxlen": args.maxlen}
    out.write_text(json.dumps(snapshot, indent=2))
    print(f"\n  wrote {out}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
