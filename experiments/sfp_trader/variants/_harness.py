"""Shared backtest harness for SFP signal variants.

Variants plug in by implementing `evaluate(state, bar) -> payload | None`
in their own `evaluate.py`. The harness handles:
  - Loading 30d x 1m BTCUSDT klines from cache
  - Walking bars through the variant's evaluate function
  - Simulating fills with fixed TP/SL OR trailing exit + timeout
  - 0.10% round-trip taker fees (env-overridable)
  - Producing standardized results.json (gate-pass/fail)

Usage from a variant directory:
  python ../_harness.py --variant <name>

Env-var overrides (all optional, useful for sweeps):
  SFP_TP_PCT     fixed TP percent          (default 0.004)
  SFP_SL_PCT     fixed SL percent          (default 0.0025)
  SFP_MAX_HOLD   max-hold bars (1m)        (default 60)
  SFP_FEE_PCT    round-trip fee            (default 0.0010)
  SFP_TRAIL_PCT  if > 0, switch to trail   (default 0)
  SFP_TRAIL_ACT  trail activation pct      (default 0.001)

Variant module API (`evaluate.py`):
  class State:
      def __init__(self): ...
  def evaluate(state, bar) -> dict | None:
      # bar: {ts_ms_open, open, high, low, close, volume, confirm}
      # returns: {"direction": "long"|"short", "mid": float, "meta": dict} or None

Acceptance gates (per AGENTS.md):
  - win_rate >= 50%
  - fires/week >= 5 AND <= 30
  - ev_net_pct > 0 (after fees)
"""
from __future__ import annotations
import argparse
import gzip
import importlib.util
import json
import os
import pathlib
import statistics
import sys
import time
from collections import Counter

HERE = pathlib.Path(__file__).parent
DATA_PATH = HERE / "_data" / "btc_1m_30d.jsonl.gz"

# ── Simulation params (env-overridable) ──────────────────────────────────
TP_PCT          = float(os.environ.get("SFP_TP_PCT",   "0.004"))
SL_PCT          = float(os.environ.get("SFP_SL_PCT",   "0.0025"))
MAX_HOLD_BARS   = int(  os.environ.get("SFP_MAX_HOLD", "60"))
FEE_PCT_RT      = float(os.environ.get("SFP_FEE_PCT", "0.0010"))
TRAIL_PCT       = float(os.environ.get("SFP_TRAIL_PCT","0"))
TRAIL_ACT_PCT   = float(os.environ.get("SFP_TRAIL_ACT","0.001"))
AGGREGATE_TF_MIN = int( os.environ.get("SFP_AGGREGATE_TF", "1"))  # 1=1m, 5=5m, 15=15m

# ── Acceptance gates ──
GATE_MIN_WR       = 50.0
GATE_MIN_FIRES_WK = 5.0
GATE_MAX_FIRES_WK = 30.0
GATE_MIN_EV_NET   = 0.0


def load_bars() -> list:
    if not DATA_PATH.exists():
        raise SystemExit(f"  ! cached klines missing at {DATA_PATH}\n"
                         f"  ! run variants/_fetch_klines.py first")
    bars = []
    with gzip.open(DATA_PATH, "rt") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            bars.append(json.loads(line))
    if AGGREGATE_TF_MIN > 1:
        bars = _aggregate_bars(bars, AGGREGATE_TF_MIN)
    return bars


def _aggregate_bars(bars: list, tf_min: int) -> list:
    """Roll up 1m OHLCV bars into tf_min-minute bars.

    Groups by floor(ts_ms_open / (tf_min * 60_000)). Drops any partial
    bucket so the result is consistent with a closed-bar feed.
    """
    if not bars: return []
    bucket_ms = tf_min * 60_000
    groups: dict = {}
    for b in bars:
        key = (b["ts_ms_open"] // bucket_ms) * bucket_ms
        if key not in groups:
            groups[key] = {
                "ts_ms_open": key,
                "open":   b["open"],
                "high":   b["high"],
                "low":    b["low"],
                "close":  b["close"],
                "volume": b["volume"],
                "_n":     1,
            }
        else:
            g = groups[key]
            if b["high"] > g["high"]: g["high"] = b["high"]
            if b["low"]  < g["low"]:  g["low"]  = b["low"]
            g["close"]  = b["close"]
            g["volume"] += b["volume"]
            g["_n"]    += 1
    out = []
    for key in sorted(groups):
        g = groups[key]
        if g["_n"] == tf_min:  # only emit complete buckets
            g.pop("_n", None)
            out.append(g)
    return out


def load_variant(name: str):
    var_dir = HERE / name
    eval_path = var_dir / "evaluate.py"
    if not eval_path.exists():
        raise SystemExit(f"  ! variant '{name}' has no evaluate.py at {eval_path}")
    spec = importlib.util.spec_from_file_location(f"variant_{name}", eval_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "State") or not hasattr(mod, "evaluate"):
        raise SystemExit(f"  ! variant '{name}' must export class State + function evaluate")
    info = getattr(mod, "VARIANT_INFO", {"name": name, "description": ""})
    return mod.State, mod.evaluate, info


def simulate_fixed(signals: list, bars: list) -> list:
    """Fixed TP/SL/timeout."""
    trades = []
    for i, direction, payload in signals:
        entry = bars[i]["close"]
        if direction == "long":
            tp = entry * (1 + TP_PCT); sl = entry * (1 - SL_PCT)
        else:
            tp = entry * (1 - TP_PCT); sl = entry * (1 + SL_PCT)
        exit_idx = exit_px = None; reason = "timeout"
        for j in range(i + 1, min(i + 1 + MAX_HOLD_BARS, len(bars))):
            high = bars[j]["high"]; low = bars[j]["low"]
            if direction == "long":
                if low <= sl:  exit_idx, exit_px, reason = j, sl, "sl"; break
                if high >= tp: exit_idx, exit_px, reason = j, tp, "tp"; break
            else:
                if high >= sl: exit_idx, exit_px, reason = j, sl, "sl"; break
                if low <= tp:  exit_idx, exit_px, reason = j, tp, "tp"; break
        if exit_idx is None:
            exit_idx = min(i + MAX_HOLD_BARS, len(bars) - 1)
            exit_px = bars[exit_idx]["close"]
        ret_gross = (exit_px - entry) / entry * 100 * (1 if direction == "long" else -1)
        ret_net = ret_gross - FEE_PCT_RT * 100
        hold_min = (bars[exit_idx]["ts_ms_open"] - bars[i]["ts_ms_open"]) / 60_000
        trades.append({
            "i": i, "direction": direction, "entry": entry, "exit": exit_px,
            "ret_gross": round(ret_gross, 4), "ret_net": round(ret_net, 4),
            "reason": reason, "hold_min": round(hold_min, 1),
            "meta_thesis": (payload or {}).get("thesis", ""),
        })
    return trades


def simulate_trail(signals: list, bars: list) -> list:
    """Trailing stop: initial SL fixed; activation at TRAIL_ACT_PCT;
    once activated, exit when price retraces TRAIL_PCT from the peak."""
    trades = []
    for i, direction, payload in signals:
        entry = bars[i]["close"]
        if direction == "long":
            init_sl = entry * (1 - SL_PCT)
            activation = entry * (1 + TRAIL_ACT_PCT)
        else:
            init_sl = entry * (1 + SL_PCT)
            activation = entry * (1 - TRAIL_ACT_PCT)
        peak = entry  # tracks best price after activation
        activated = False
        exit_idx = exit_px = None; reason = "timeout"
        for j in range(i + 1, min(i + 1 + MAX_HOLD_BARS, len(bars))):
            high = bars[j]["high"]; low = bars[j]["low"]
            if direction == "long":
                if not activated and high >= activation: activated = True
                if activated:
                    if high > peak: peak = high
                    trail_sl = peak * (1 - TRAIL_PCT)
                    if low <= trail_sl:
                        exit_idx, exit_px, reason = j, trail_sl, "trail"; break
                if low <= init_sl:
                    exit_idx, exit_px, reason = j, init_sl, "sl_initial"; break
            else:
                if not activated and low <= activation: activated = True
                if activated:
                    if low < peak: peak = low
                    trail_sl = peak * (1 + TRAIL_PCT)
                    if high >= trail_sl:
                        exit_idx, exit_px, reason = j, trail_sl, "trail"; break
                if high >= init_sl:
                    exit_idx, exit_px, reason = j, init_sl, "sl_initial"; break
        if exit_idx is None:
            exit_idx = min(i + MAX_HOLD_BARS, len(bars) - 1)
            exit_px = bars[exit_idx]["close"]
        ret_gross = (exit_px - entry) / entry * 100 * (1 if direction == "long" else -1)
        ret_net = ret_gross - FEE_PCT_RT * 100
        hold_min = (bars[exit_idx]["ts_ms_open"] - bars[i]["ts_ms_open"]) / 60_000
        trades.append({
            "i": i, "direction": direction, "entry": entry, "exit": exit_px,
            "ret_gross": round(ret_gross, 4), "ret_net": round(ret_net, 4),
            "reason": reason, "hold_min": round(hold_min, 1),
            "activated": activated,
            "meta_thesis": (payload or {}).get("thesis", ""),
        })
    return trades


def evaluate_gates(r: dict) -> dict:
    return {
        "win_rate_ge_50":  r["win_rate_pct"]   >= GATE_MIN_WR,
        "fires_wk_ge_5":   r["fires_per_week"] >= GATE_MIN_FIRES_WK,
        "fires_wk_le_30":  r["fires_per_week"] <= GATE_MAX_FIRES_WK,
        "ev_net_gt_0":     r["ev_net_pct"]     >  GATE_MIN_EV_NET,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", required=True)
    args = parser.parse_args()

    print(f"=== variant: {args.variant} ===", flush=True)
    print(f"  params: TP={TP_PCT:.4f}  SL={SL_PCT:.4f}  hold={MAX_HOLD_BARS}bars  "
          f"fee={FEE_PCT_RT:.4f}  trail={TRAIL_PCT:.4f}  trail_act={TRAIL_ACT_PCT:.4f}  "
          f"tf={AGGREGATE_TF_MIN}m")
    bars = load_bars()
    span_days = (bars[-1]["ts_ms_open"] - bars[0]["ts_ms_open"]) / 86_400_000
    print(f"  loaded {len(bars)} bars spanning {span_days:.1f} days")

    State, evaluate, info = load_variant(args.variant)
    print(f"  variant info: {info.get('description','(no description)')}")

    state = State()
    signals = []
    direction_map = Counter()
    t0 = time.perf_counter()
    for i, bar in enumerate(bars):
        bar = dict(bar); bar["confirm"] = True
        payload = evaluate(state, bar)
        if payload is None: continue
        direction = payload.get("direction")
        if direction not in ("long", "short"): continue
        signals.append((i, direction, payload))
        direction_map[direction] += 1
    elapsed_s = time.perf_counter() - t0
    print(f"  evaluator processed {len(bars)} bars in {elapsed_s:.2f}s")
    print(f"  raw signals fired: {len(signals)}  "
          f"(long={direction_map['long']} short={direction_map['short']})")

    if TRAIL_PCT > 0:
        print(f"  exit model: trailing (act={TRAIL_ACT_PCT:.4f} trail={TRAIL_PCT:.4f})")
        trades = simulate_trail(signals, bars)
    else:
        print(f"  exit model: fixed TP={TP_PCT:.4f} SL={SL_PCT:.4f}")
        trades = simulate_fixed(signals, bars)

    if not trades:
        print(f"  ! 0 trades")
        out = {"variant": args.variant, "info": info, "trades": 0,
                "verdict": "FAIL (no signals)", "gates": {}}
        (HERE / args.variant / "results.json").write_text(json.dumps(out, indent=2))
        return 2

    n = len(trades)
    wins = [t for t in trades if t["ret_gross"] > 0]
    losses = [t for t in trades if t["ret_gross"] < 0]
    ev_gross = statistics.mean(t["ret_gross"] for t in trades)
    ev_net   = statistics.mean(t["ret_net"]   for t in trades)
    win_rate = len(wins) / n * 100
    fires_per_week = n / span_days * 7
    reasons = Counter(t["reason"] for t in trades)
    avg_hold = statistics.mean(t["hold_min"] for t in trades)

    results = {
        "variant":         args.variant,
        "info":            info,
        "backtest_utc":    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bars":            len(bars),
        "span_days":       round(span_days, 2),
        "signals":         len(signals),
        "trades":          n,
        "direction_split": dict(direction_map),
        "wins":            len(wins),
        "losses":          len(losses),
        "win_rate_pct":    round(win_rate, 2),
        "fires_per_week":  round(fires_per_week, 2),
        "ev_gross_pct":    round(ev_gross, 5),
        "ev_net_pct":      round(ev_net, 5),
        "avg_hold_min":    round(avg_hold, 1),
        "exit_reasons":    dict(reasons),
        "params": {
            "tp_pct": TP_PCT, "sl_pct": SL_PCT, "max_hold_bars": MAX_HOLD_BARS,
            "fee_pct_rt": FEE_PCT_RT,
            "trail_pct": TRAIL_PCT, "trail_act_pct": TRAIL_ACT_PCT,
            "exit_model": "trailing" if TRAIL_PCT > 0 else "fixed",
        },
        "elapsed_s":       round(elapsed_s, 3),
    }
    gates = evaluate_gates(results)
    results["gates"]   = gates
    results["verdict"] = "PASS" if all(gates.values()) else "FAIL"

    print(f"\n  --- results ---")
    print(f"  trades:         {n}    rate {fires_per_week:.1f}/wk    avg-hold {avg_hold:.0f}min")
    print(f"  wins:           {len(wins):>3d} ({win_rate:.1f}%)")
    print(f"  exits:          {dict(reasons)}")
    print(f"  EV gross:       {ev_gross:+.4f}%/trade")
    print(f"  EV net:         {ev_net:+.4f}%/trade   (after {FEE_PCT_RT*100:.2f}% RT fees)")
    print(f"\n  gates:")
    for g, ok in gates.items():
        print(f"    {'+' if ok else '-'}  {g}")
    print(f"\n  VERDICT: {results['verdict']}")

    out_path = HERE / args.variant / "results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"  saved: {out_path}")
    return 0 if all(gates.values()) else 3


if __name__ == "__main__":
    sys.exit(main())
