"""TP/SL sensitivity sweep — does any parameter combo make confluence pass?

Re-runs the same 33 confluence signals with different TP/SL/hold combos and
prints a grid. Bias: most failures were timeouts (22/33), so wider TP + longer
hold should be tested first.
"""
import json, time, urllib.request, statistics, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fib_sfp_trigger import FibSfpState

print("Pulling 7d BTCUSDT bars...", flush=True)
def fetch(end_ms=None, limit=1000):
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=1&limit={limit}"
    if end_ms: url += f"&end={end_ms}"
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent":"x"}), timeout=15).read())

all_bars = []
end_ms = int(time.time() * 1000)
while len(all_bars) < 7 * 24 * 60:
    r = fetch(end_ms=end_ms)
    chunk = r.get("result", {}).get("list", [])
    if not chunk: break
    all_bars.extend(chunk)
    end_ms = int(chunk[-1][0]) - 60_000
    if len(chunk) < 1000: break
all_bars.sort(key=lambda b: int(b[0]))
bars = [{"ts": int(b[0])//1000, "open": float(b[1]), "high": float(b[2]),
         "low": float(b[3]), "close": float(b[4]), "volume": float(b[5]),
         "confirm": True} for b in all_bars]
print(f"  {len(bars)} bars")

# Re-build signal list
def intel_boost_long(i, bars):
    lookback = 720
    if i < lookback: return False
    p = bars[i - lookback]["close"]; n = bars[i]["close"]
    return p > 0 and (n - p) / p > 0.003

state = FibSfpState()
signals = []
for i, b in enumerate(bars):
    state.on_bar(b)
    sig = state.evaluate()
    if sig is None: continue
    if not intel_boost_long(i, bars): continue
    state.mark_fired()
    signals.append(i)
print(f"  signals: {len(signals)}")
span_days = (bars[-1]["ts"] - bars[0]["ts"]) / 86400
per_week = len(signals) / span_days * 7

def sim(idx, tp_pct, sl_pct, max_hold):
    trades = []
    for i in idx:
        entry = bars[i]["close"]
        tp = entry * (1 + tp_pct); sl = entry * (1 - sl_pct)
        out_idx = None; out_px = None; reason = "timeout"
        for j in range(i + 1, min(i + 1 + max_hold, len(bars))):
            if bars[j]["low"] <= sl:   out_idx, out_px, reason = j, sl, "sl"; break
            if bars[j]["high"] >= tp:  out_idx, out_px, reason = j, tp, "tp"; break
        if out_idx is None:
            out_idx = min(i + max_hold, len(bars) - 1)
            out_px = bars[out_idx]["close"]
        trades.append({"ret": (out_px - entry) / entry * 100, "reason": reason,
                        "hold_min": (bars[out_idx]["ts"] - bars[i]["ts"]) / 60})
    return trades

FEE_PCT = 0.10  # 0.10% round-trip taker

print(f"\n{'TP%':<6s}{'SL%':<6s}{'hold':<6s} | {'WR':>6s} {'EV gross':>10s} {'EV net':>10s} {'timeout':>8s} | gate")
print("-" * 80)
results = []
for tp_pct, sl_pct, hold in [
    (0.004, 0.0025, 60),    # original
    (0.006, 0.0025, 60),    # wider TP, same SL
    (0.006, 0.003,  90),    # wider TP, slightly wider SL, longer hold
    (0.008, 0.003,  120),   # very wide TP, longer hold
    (0.005, 0.0015, 90),    # tighter SL with moderate TP
    (0.004, 0.002,  90),    # tight TP, tight SL, longer hold
    (0.003, 0.002,  30),    # short scalp
    (0.010, 0.004,  240),   # widest — let it ride
]:
    trades = sim(signals, tp_pct, sl_pct, hold)
    if not trades: continue
    n = len(trades)
    wins = [t for t in trades if t["ret"] > 0]
    ev_gross = statistics.mean(t["ret"] for t in trades)
    ev_net = ev_gross - FEE_PCT
    wr = len(wins) / n * 100
    timeouts = sum(1 for t in trades if t["reason"] == "timeout")
    gate_pass = wr >= 50 and ev_net > 0 and per_week >= 5 and per_week <= 30
    marker = "✓ PASS" if gate_pass else ""
    print(f"{tp_pct*100:<6.2f}{sl_pct*100:<6.2f}{hold:<6d} | {wr:>5.1f}% {ev_gross:>+9.4f}% {ev_net:>+9.4f}% {timeouts:>5d}/{n} | {marker}")
    results.append({
        "tp_pct": tp_pct, "sl_pct": sl_pct, "max_hold": hold,
        "trades": n, "win_rate": wr, "ev_gross": ev_gross, "ev_net": ev_net,
        "timeouts": timeouts, "gate_pass": gate_pass,
    })

print(f"\n  Trade rate: {per_week:.1f}/week (gate ≥5 and ≤30 → {'PASS' if 5 <= per_week <= 30 else 'FAIL'})")

# Save sensitivity
out = Path(__file__).parent / "sensitivity_results.json"
out.write_text(json.dumps({
    "signals":         len(signals),
    "per_week":        round(per_week, 2),
    "fee_pct":         FEE_PCT,
    "param_grid":      results,
    "any_passing":     any(r["gate_pass"] for r in results),
}, indent=2))
print(f"\n  Saved: {out}")
