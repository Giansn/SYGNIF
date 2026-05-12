"""perp_runner backtest.

Three phases against last 30d of BTCUSDT 5m klines (Bybit mainnet, public):

  Phase 1: Outcome-score the historical sfp.shadow LONG signals.
           Forward-walk klines from signal_ts up to MAX_HOLD_HOURS, mark
           win (target hit), loss (stop hit), or timeout. Compute R per
           trade and aggregate expectancy.

  Phase 2: Replay perp_runner's composite-confidence gate retroactively
           for each signal, using the closest-in-time discovery baseline_*.json
           for option metrics. Compare expectancy of EXECUTED subset
           (final_conf >= MIN_CONF) vs ALL signals — does the multi-source
           filter actually improve edge?

  Phase 3: Walk-forward run scan_psych_barrier_fade_short on the 30d kline
           series. The scanner was env-gated OFF until 2026-05-10 so we
           have no live signals — sim what it would have done. Score
           outcomes the same way.

  Sizing: 0.5% equity per trade, equity = $1933 (current). $ P&L per trade
          is roughly risk_usd × R, with risk_usd ≈ $9.66.

Run on EC2 with: SYGNIF_PSYCH_BARRIER_FADE_SHORT=1 python3 backtest.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np

# --- config -----------------------------------------------------------------
ROOT          = Path("/home/ubuntu/sygnif-agent-mirror")
SWARM_DB      = "/var/lib/sygnif/swarm.db"
BACKTEST_DAYS = int(os.environ.get("BACKTEST_DAYS", "30"))
MAX_HOLD_HRS  = 4.0   # signals time-out after 4h if neither stop nor target hit
EQUITY_USD    = 1933.0
RISK_PCT      = 0.5   # per perp_runner default
MIN_CONF      = 0.60  # composite-gate threshold

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# --- kline fetch ------------------------------------------------------------
def fetch_klines(symbol: str, interval_min: int, days_back: int) -> np.ndarray | None:
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days_back * 86400 * 1000
    bars: list[list[float]] = []
    cursor_end = end_ms
    pages = 0
    while cursor_end > start_ms and pages < 30:
        qs = urllib.parse.urlencode({
            "category": "linear", "symbol": symbol,
            "interval": str(interval_min),
            "start": start_ms, "end": cursor_end, "limit": "1000",
        })
        url = f"https://api.bybit.com/v5/market/kline?{qs}"
        try:
            r = json.loads(urllib.request.urlopen(url, timeout=15).read())
        except Exception as e:
            print(f"  kline fetch err: {e}", file=sys.stderr)
            break
        rows = r.get("result", {}).get("list", []) or []
        if not rows:
            break
        for row in rows:
            bars.append([float(x) for x in row[:6]])
        oldest_ts = int(rows[-1][0])
        if oldest_ts <= start_ms or oldest_ts >= cursor_end:
            break
        cursor_end = oldest_ts - 1
        pages += 1
    if not bars:
        return None
    bars.sort(key=lambda r: r[0])
    return np.array(bars)  # cols: ts_ms, o, h, l, c, v


# --- signal outcome scoring -------------------------------------------------
def score_outcome(sig_ts_ms: int, side: str, entry: float, stop: float,
                  target: float, ts: np.ndarray, h: np.ndarray, l: np.ndarray,
                  max_hold_hrs: float = MAX_HOLD_HRS) -> dict:
    """Forward-walk klines. Return outcome details. Both stop and target hit
    in same bar → conservative pessimistic = LOSS (assumes stop fills first)."""
    starts = int(np.searchsorted(ts, sig_ts_ms))
    if starts >= len(ts):
        return {"outcome": "no_data", "R": 0.0, "exit_ts": None}
    end_ts = sig_ts_ms + int(max_hold_hrs * 3600 * 1000)
    is_long = (side == "Buy")
    risk_per_unit = abs(entry - stop) if entry != stop else 1e-9
    reward = abs(target - entry)
    R_target = reward / risk_per_unit  # R-multiple if target hits

    for i in range(starts, len(ts)):
        if ts[i] > end_ts:
            break
        bar_h, bar_l = float(h[i]), float(l[i])
        if is_long:
            stop_hit = bar_l <= stop
            target_hit = bar_h >= target
        else:
            stop_hit = bar_h >= stop
            target_hit = bar_l <= target
        if stop_hit and target_hit:
            # Both touched — conservative loss (assume stop fills first)
            return {"outcome": "loss_ambig", "R": -1.0, "exit_ts": int(ts[i])}
        if stop_hit:
            return {"outcome": "loss", "R": -1.0, "exit_ts": int(ts[i])}
        if target_hit:
            return {"outcome": "win", "R": float(R_target), "exit_ts": int(ts[i])}
    return {"outcome": "timeout", "R": 0.0, "exit_ts": end_ts}


# --- discovery baseline lookup (for retroactive composite gate) -------------
_BASELINE_FILES: list[tuple[int, Path]] = []  # (ts_ms, path)
def _load_baseline_index():
    if _BASELINE_FILES:
        return
    bdir = ROOT / "discovery"
    for f in bdir.glob("baseline_*.json"):
        # filename pattern baseline_20260502T134617Z.json
        try:
            stem = f.stem.replace("baseline_", "")
            t = time.strptime(stem, "%Y%m%dT%H%M%SZ")
            ts_ms = int(time.mktime(t)) * 1000
            # mktime treats as local; baselines are UTC. Adjust:
            ts_ms -= int(time.timezone) * 1000
            _BASELINE_FILES.append((ts_ms, f))
        except Exception:
            continue
    _BASELINE_FILES.sort()


def nearest_baseline(ts_ms: int) -> dict | None:
    _load_baseline_index()
    if not _BASELINE_FILES:
        return None
    # find largest ts <= signal_ts (most-recent before signal)
    best = None
    for bts, bpath in _BASELINE_FILES:
        if bts <= ts_ms:
            best = bpath
        else:
            break
    if best is None:
        return None
    try:
        return json.loads(best.read_text())
    except Exception:
        return None


# --- option enrichment (mirrors perp_runner.enrich_options) -----------------
def enrich_options(side: str, entry: float, baseline: dict | None) -> dict:
    if not baseline:
        return {"max_pain_align": 0.0, "rr_25d_align": 0.0, "gex_trend_bias": 0.0,
                "atm_iv": 0, "rr_25d": 0, "max_pain_strike": 0, "gex_total": 0}
    options = baseline.get("options", {}) or {}
    spot = entry
    is_long = side == "Buy"

    atm_iv      = float(options.get("atm_iv_nearest") or 0)
    max_pain    = float((options.get("max_pain") or {}).get("strike") or 0)
    rr          = (options.get("rr_25d") or {}).get("value")
    rr_25d      = float(rr) if rr is not None else 0.0
    gex_total   = float((options.get("gex") or {}).get("total_usd") or 0)

    if max_pain > 0 and spot > 0:
        pain_dist_pct = (spot - max_pain) / spot
        raw = -pain_dist_pct * 5
        max_pain_align = raw if is_long else -raw
    else:
        max_pain_align = 0.0
    rr_align = (rr_25d * 10) if is_long else (-rr_25d * 10)
    gex_trend_bias = -1.0 if gex_total < -1000 else (1.0 if gex_total > 1000 else 0.0)
    return {
        "atm_iv": atm_iv, "rr_25d": rr_25d, "max_pain_strike": max_pain,
        "gex_total": gex_total, "max_pain_align": round(max_pain_align, 3),
        "rr_25d_align": round(rr_align, 3), "gex_trend_bias": gex_trend_bias,
    }


def composite_confidence(scanner_conf: float, side: str, opt: dict,
                          imbalance: float = 0.0) -> dict:
    """Mirror perp_runner.compute_confidence. Without time-aligned microstructure
    history we use imbalance=0 (neutral) for the historical replay."""
    base = float(scanner_conf or 0.5)
    opt_score = max(-0.20, min(0.20,
                    (opt["max_pain_align"] + opt["rr_25d_align"]) / 2.0 * 0.40))
    is_long = side == "Buy"
    micro_score = max(-0.10, min(0.10, (imbalance if is_long else -imbalance) * 0.20))
    gex_bias = 0.0
    if opt["gex_trend_bias"] == -1 and is_long:
        gex_bias = 0.05
    elif opt["gex_trend_bias"] == 1 and not is_long:
        gex_bias = 0.05
    final = max(0.0, min(1.0, base + opt_score + micro_score + gex_bias))
    return {"base": round(base, 3), "opt_score": round(opt_score, 3),
            "micro_score": round(micro_score, 3), "gex_bias": round(gex_bias, 3),
            "final": round(final, 3)}


# --- phase 1+2 --------------------------------------------------------------
def fetch_historical_signals(days: int) -> list[dict]:
    conn = sqlite3.connect(f"file:{SWARM_DB}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT created, content FROM swarm_entries "
        "WHERE topic='sfp.shadow' AND created > strftime('%s','now',?) "
        "ORDER BY created ASC",
        (f"-{days} days",),
    ).fetchall()
    conn.close()
    out = []
    for created, content in rows:
        try:
            d = json.loads(content)
            d["_swarm_created"] = float(created)
            out.append(d)
        except json.JSONDecodeError:
            continue
    return out


def phase1_phase2(klines: np.ndarray) -> None:
    print("\n" + "=" * 78)
    print("PHASE 1+2  outcome-score historical signals + composite-gate replay")
    print("=" * 78)
    signals = fetch_historical_signals(BACKTEST_DAYS)
    print(f"\nfound {len(signals)} sfp.shadow signals in last {BACKTEST_DAYS}d")
    if not signals:
        print("  no signals to score")
        return

    ts = klines[:, 0]
    h, l = klines[:, 2], klines[:, 3]

    risk_usd = EQUITY_USD * RISK_PCT / 100.0
    print(f"  sizing: ${risk_usd:.2f} risk per trade ({RISK_PCT}% of ${EQUITY_USD:.0f})\n")
    print(f"  {'shadow_id':36s} {'sig':22s} {'side':4s} {'scanner_conf':>13s} "
          f"{'final_conf':>11s} {'outcome':10s} {'R':>6s} {'$P&L':>8s}")
    print("  " + "-" * 130)

    rows_all = []
    rows_exec = []
    for s in signals:
        scanner_conf = float(s.get("calibrated_setup_conf") or s.get("raw_setup_conf") or 0)
        sig_ts_ms = int(float(s.get("ts") or 0) * 1000)
        side = s.get("side", "Buy")
        entry = float(s.get("entry", 0))
        stop  = float(s.get("stop", 0))
        target = float(s.get("target", 0))

        baseline = nearest_baseline(sig_ts_ms)
        opt = enrich_options(side, entry, baseline)
        conf = composite_confidence(scanner_conf, side, opt)

        outcome = score_outcome(sig_ts_ms, side, entry, stop, target, ts, h, l)
        pnl_usd = outcome["R"] * risk_usd
        rows_all.append({
            "scanner_conf": scanner_conf, "final_conf": conf["final"],
            "outcome": outcome["outcome"], "R": outcome["R"], "pnl": pnl_usd,
            "signal": s.get("signal"), "side": side,
        })
        if conf["final"] >= MIN_CONF:
            rows_exec.append(rows_all[-1])
        print(f"  {s.get('shadow_id', '?'):36s} {s.get('signal', '?'):22s} {side:4s} "
              f"{scanner_conf:>13.2f} {conf['final']:>11.2f} {outcome['outcome']:10s} "
              f"{outcome['R']:>6.2f} {pnl_usd:>+8.2f}")

    print("\n  " + "-" * 130)
    summarize("ALL signals (no composite gate)", rows_all, risk_usd)
    summarize(f"PERP_RUNNER would have EXECUTED (final_conf >= {MIN_CONF})", rows_exec, risk_usd)
    skipped = [r for r in rows_all if r["final_conf"] < MIN_CONF]
    summarize(f"PERP_RUNNER would have SKIPPED  (final_conf <  {MIN_CONF})", skipped, risk_usd)


def summarize(label: str, rows: list[dict], risk_usd: float) -> None:
    print(f"\n  {label}: n={len(rows)}")
    if not rows:
        return
    by_outcome = defaultdict(int)
    for r in rows:
        by_outcome[r["outcome"]] += 1
    wins = by_outcome.get("win", 0)
    losses = by_outcome.get("loss", 0) + by_outcome.get("loss_ambig", 0)
    timeouts = by_outcome.get("timeout", 0)
    no_data = by_outcome.get("no_data", 0)
    decisive = wins + losses
    win_rate = (wins / decisive * 100) if decisive else 0.0
    sum_R = sum(r["R"] for r in rows)
    avg_R = sum_R / len(rows)
    sum_pnl = sum(r["pnl"] for r in rows)
    print(f"    win={wins}  loss={losses}  timeout={timeouts}  no_data={no_data}  "
          f"win_rate={win_rate:.0f}%  total_R={sum_R:+.2f}  avg_R/trade={avg_R:+.2f}  "
          f"total_P&L=${sum_pnl:+.2f}")


# --- phase 3 ----------------------------------------------------------------
def phase3(klines: np.ndarray) -> None:
    print("\n" + "=" * 78)
    print("PHASE 3  walk-forward replay of scan_psych_barrier_fade_short")
    print(f"          (was env-gated off in production until 2026-05-10)")
    print("=" * 78)

    # Ensure env flag is set before importing — the scanner short-circuits otherwise
    os.environ["SYGNIF_PSYCH_BARRIER_FADE_SHORT"] = "1"
    from sygnif_predict import scan_psych_barrier_fade_short

    ts = klines[:, 0]
    o, h, l, c, v = klines[:, 1], klines[:, 2], klines[:, 3], klines[:, 4], klines[:, 5]

    risk_usd = EQUITY_USD * RISK_PCT / 100.0
    fires: list[dict] = []
    warmup = 60  # need ATR + range + lookback
    for i in range(warmup, len(klines)):
        # slice up to bar i (inclusive)
        win_o = o[:i+1]; win_h = h[:i+1]; win_l = l[:i+1]; win_c = c[:i+1]; win_v = v[:i+1]
        try:
            sig = scan_psych_barrier_fade_short(win_o, win_h, win_l, win_c, win_v)
        except Exception as e:
            continue
        if sig is None:
            continue
        # Skip if regime would block (would be TREND_UP) — approximate without full predict
        # The regime check happens in predict-loop, not in the scanner. We replicate
        # a coarse approximation: skip if last 12 bars (1h) closed >+1% from open.
        ret_1h = (win_c[-1] - win_c[-13]) / win_c[-13] if i >= 13 else 0
        approx_trend_up = ret_1h > 0.005  # 0.5% in 1h ≈ TREND_UP
        if approx_trend_up:
            continue
        sig_ts_ms = int(ts[i])
        outcome = score_outcome(sig_ts_ms, "Sell",
                                  float(sig.entry), float(sig.stop), float(sig.target),
                                  ts, h, l)
        fires.append({
            "ts": sig_ts_ms, "entry": float(sig.entry), "stop": float(sig.stop),
            "target": float(sig.target), "outcome": outcome["outcome"],
            "R": outcome["R"], "pnl": outcome["R"] * risk_usd,
            "scanner_conf": getattr(sig, "raw_setup_conf", 0.55),
        })

    if not fires:
        print("\n  zero psych_barrier_fade_short setups in last 30d")
        print("  → either BTC was rarely near a $10k boundary with the right candle shape,")
        print("    or 30d isn't a long enough window to catch one. Try BACKTEST_DAYS=90.")
        return

    print(f"\n  {len(fires)} psych_barrier_fade_short setups found in last {BACKTEST_DAYS}d")
    print(f"  {'when':17s} {'entry':>10s} {'stop':>10s} {'target':>10s} {'outcome':12s} {'R':>6s} {'$P&L':>8s}")
    print("  " + "-" * 80)
    for f in fires:
        when = time.strftime("%m-%d %H:%M UTC", time.gmtime(f["ts"]/1000))
        print(f"  {when:17s} {f['entry']:>10.2f} {f['stop']:>10.2f} {f['target']:>10.2f} "
              f"{f['outcome']:12s} {f['R']:>6.2f} {f['pnl']:>+8.2f}")
    summarize(f"PSYCH_SHORT scanner — counterfactual {BACKTEST_DAYS}d backtest", fires, risk_usd)


# --- main -------------------------------------------------------------------
def main() -> int:
    print(f"=== perp_runner backtest — {BACKTEST_DAYS}d window, BTCUSDT 5m, "
          f"max_hold={MAX_HOLD_HRS}h ===")
    t0 = time.time()
    klines = fetch_klines("BTCUSDT", 5, BACKTEST_DAYS)
    if klines is None:
        print("  kline fetch failed")
        return 1
    span_h = (klines[-1, 0] - klines[0, 0]) / 3600 / 1000
    print(f"  fetched {len(klines)} 5m bars spanning {span_h:.1f}h "
          f"({klines[0, 0]/1000:.0f} → {klines[-1, 0]/1000:.0f})")
    print(f"  fetch took {time.time()-t0:.1f}s")

    phase1_phase2(klines)
    phase3(klines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
