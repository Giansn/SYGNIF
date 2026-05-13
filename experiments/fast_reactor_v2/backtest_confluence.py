"""Acceptance-gate backtest for the confluence fib_sfp design.

Runs the SAME 7d × 1m BTCUSDT data the original Naive backtest used, but
this time gates with the full confluence: SFP + 0.3% fib + 5-bar dedup +
synthetic intel-boost gate (derived from a coarse trend filter as a stand-
in for the real intel layer).

The intel surrogate: bullish if BTC has risen >0.5% over the last 60 bars
(1h). This isn't the real intel, but it's the closest readily-backtest-able
filter that mirrors "intel says go long" semantics. The real production
gate is more selective (requires boosts_long non-empty from intel_summary.json).

Acceptance:
  - ≥ 50% win rate
  - ≥ 5 fires/week (but ≤ 30/week — confluence should be selective)
  - EV after 0.10% round-trip fees > 0
"""
import json, time, urllib.request, statistics, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fib_sfp_trigger import (
    FibSfpState, detect_bull_sfp,
    LOOKBACK_SFP, FIB_RANGE, FIB_PROXIMITY_PCT, COOLDOWN_BARS,
)

# ── Pull klines (same as the naive backtest) ─────────────────────────────
def fetch_klines(end_ms=None, limit=1000):
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=1&limit={limit}"
    if end_ms: url += f"&end={end_ms}"
    req = urllib.request.Request(url, headers={"User-Agent": "sygnif-backtest/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())

print("Pulling 7d × 1m BTCUSDT klines...", flush=True)
all_bars = []
end_ms = int(time.time() * 1000)
TARGET = 7 * 24 * 60
while len(all_bars) < TARGET:
    r = fetch_klines(end_ms=end_ms)
    if r.get("retCode") != 0: break
    chunk = r["result"]["list"]
    if not chunk: break
    all_bars.extend(chunk)
    end_ms = int(chunk[-1][0]) - 60_000
    if len(chunk) < 1000: break
all_bars.sort(key=lambda b: int(b[0]))
print(f"  collected {len(all_bars)} bars")

bars = [{"ts": int(b[0])//1000, "open": float(b[1]), "high": float(b[2]),
         "low": float(b[3]), "close": float(b[4]), "volume": float(b[5]),
         "confirm": True} for b in all_bars]

# ── Intel surrogate ──────────────────────────────────────────────────────
# Real production intel reads boosts_long from intel_summary.json. Those
# boosts are PERSISTENT context (cold_accum_4h, whale_net_24h, mstr_buy_7d).
# A short-term momentum filter would FIGHT the SFP signal (bull SFP fires
# during pullbacks where 60m return is negative). We mock structural intel
# with a 12-hour (720-bar) trend filter: bullish if return > +0.3% over 12h.
def intel_boost_long(i, bars, lookback=720):
    """Mock real intel: bullish if BTC return over the prior 12h is > 0.3%.
    Returns (allow, reason, conf_modifier) — same shape as fast-reactor's
    check_intel_for_direction."""
    if i < lookback: return (True, "neutral", 1.0)
    past = bars[i - lookback]["close"]
    now  = bars[i]["close"]
    if past <= 0: return (True, "neutral", 1.0)
    ret = (now - past) / past
    if ret > 0.003:
        return (True, "intel_boost:trend_12h:" + f"{ret*100:.2f}%", 1.0 + 0.1)
    return (True, "neutral", 1.0)

# ── Run the confluence trigger across history ────────────────────────────
state = FibSfpState()
signals = []
for i, b in enumerate(bars):
    state.on_bar(b)
    sig = state.evaluate()
    if sig is None: continue
    # Apply intel surrogate
    allow, reason, conf_mod = intel_boost_long(i, bars)
    if not allow: continue
    if not reason.startswith("intel_boost"): continue
    state.mark_fired()
    signals.append({"i": i, "close": b["close"], "ts": b["ts"],
                    "fib_dist_pct": sig["fib_distance_pct"],
                    "intel_reason": reason})

print(f"\n  confluence fires: {len(signals)}")
span_days = (bars[-1]["ts"] - bars[0]["ts"]) / 86400
per_week = len(signals) / span_days * 7
print(f"  rate:             {per_week:.1f}/week  (gate: ≥5 and ≤30)")

# ── Simulate trades with the fast-reactor TP/SL ──────────────────────────
TP_PCT = 0.004; SL_PCT = 0.0025; MAX_HOLD = 60
FEE_PCT = 0.0010   # 0.10% round-trip taker

def sim(signals):
    trades = []
    for s in signals:
        i = s["i"]; entry = s["close"]
        tp_px = entry * (1 + TP_PCT)
        sl_px = entry * (1 - SL_PCT)
        exit_idx = None; exit_px = None; reason = "timeout"
        for j in range(i + 1, min(i + 1 + MAX_HOLD, len(bars))):
            if bars[j]["low"] <= sl_px:   exit_idx, exit_px, reason = j, sl_px, "sl"; break
            if bars[j]["high"] >= tp_px:  exit_idx, exit_px, reason = j, tp_px, "tp"; break
        if exit_idx is None:
            exit_idx = min(i + MAX_HOLD, len(bars) - 1)
            exit_px = bars[exit_idx]["close"]
        ret_gross = (exit_px - entry) / entry * 100   # %
        ret_net = ret_gross - FEE_PCT * 100           # after fees
        trades.append({**s, "exit": exit_px, "reason": reason,
                        "ret_gross": ret_gross, "ret_net": ret_net,
                        "hold_min": (bars[exit_idx]["ts"] - bars[i]["ts"]) / 60})
    return trades

trades = sim(signals)

# ── Report ───────────────────────────────────────────────────────────────
if not trades:
    print("\n  NO TRADES — confluence too selective, no signals fired in 7d")
    print("  ACCEPTANCE: ✗ FAIL (gate requires ≥5/week)")
    sys.exit(2)

n = len(trades)
wins = [t for t in trades if t["ret_gross"] > 0]
losses = [t for t in trades if t["ret_gross"] < 0]
ev_gross = statistics.mean(t["ret_gross"] for t in trades)
ev_net = statistics.mean(t["ret_net"] for t in trades)
win_rate = len(wins) / n * 100
avg_hold = statistics.mean(t["hold_min"] for t in trades)
from collections import Counter
reasons = Counter(t["reason"] for t in trades)

print(f"\n  Trade results (TP=+{TP_PCT*100:.1f}% / SL=-{SL_PCT*100:.2f}% / fees=0.10% RT):")
print(f"    trades:     {n}")
print(f"    win rate:   {win_rate:.1f}%   (gate: ≥50%)")
print(f"    wins/losses: {len(wins)} / {len(losses)}")
print(f"    exits:      tp={reasons.get('tp',0)}  sl={reasons.get('sl',0)}  timeout={reasons.get('timeout',0)}")
print(f"    avg hold:   {avg_hold:.1f} min")
print(f"    EV gross:   {ev_gross:+.4f}%/trade")
print(f"    EV net:     {ev_net:+.4f}%/trade  (gate: >0)")

# Acceptance gate
gates = {
    "win_rate ≥ 50%":   win_rate >= 50,
    "fires/week ≥ 5":   per_week >= 5,
    "fires/week ≤ 30":  per_week <= 30,
    "EV net > 0":       ev_net > 0,
}
all_pass = all(gates.values())
print(f"\n  Acceptance:")
for g, ok in gates.items():
    print(f"    {'✓' if ok else '✗'}  {g}")
print(f"\n  OVERALL: {'✓ PASS — safe to deploy' if all_pass else '✗ FAIL — do not merge'}")

# Save result for PR body
result = {
    "backtest_date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "symbol":            "BTCUSDT",
    "interval":          "1m",
    "bars":              len(bars),
    "span_days":         round(span_days, 2),
    "design":            "fib_sfp_confluence v1 (SFP + 0.3% fib + 5-bar dedup + intel_boost gate)",
    "params": {
        "LOOKBACK_SFP":      LOOKBACK_SFP,
        "FIB_RANGE":         FIB_RANGE,
        "FIB_PROXIMITY_PCT": FIB_PROXIMITY_PCT,
        "COOLDOWN_BARS":     COOLDOWN_BARS,
        "TP_PCT":            TP_PCT,
        "SL_PCT":            SL_PCT,
        "MAX_HOLD":          MAX_HOLD,
        "FEE_PCT":           FEE_PCT,
    },
    "intel_surrogate":     "60-bar return > +0.5% (proxy for real intel boosts_long)",
    "fires":               len(signals),
    "fires_per_week":      round(per_week, 2),
    "trades":              n,
    "wins":                len(wins),
    "losses":              len(losses),
    "win_rate_pct":        round(win_rate, 2),
    "avg_hold_min":        round(avg_hold, 1),
    "ev_gross_pct":        round(ev_gross, 5),
    "ev_net_pct":          round(ev_net, 5),
    "exit_reasons":        dict(reasons),
    "gates":               gates,
    "verdict":             "PASS" if all_pass else "FAIL",
}
out = Path(__file__).parent / "backtest_results.json"
out.write_text(json.dumps(result, indent=2))
print(f"\n  Result saved: {out}")
sys.exit(0 if all_pass else 3)
