"""Backtest the lowered-threshold momentum trigger alone.

If fib_sfp_confluence fails the gate, the momentum tightening (Part B of the
task) might still be worth shipping on its own.

Tests TRIGGER_MOMENTUM_PCT=0.2 and TRIGGER_MOMENTUM_VOLX=1.2 against the
same 7d × 1m BTCUSDT data.
"""
import json, time, urllib.request, statistics, sys
from collections import Counter
from pathlib import Path

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
         "low": float(b[3]), "close": float(b[4]), "volume": float(b[5])} for b in all_bars]
print(f"  {len(bars)} bars")

# Compute momentum signals (long + short) at lowered thresholds
def momentum_signals(bars, move_pct, vol_x, dir_filter=None):
    out = []
    for i in range(10, len(bars)):
        b = bars[i]
        if b["open"] <= 0: continue
        move = (b["close"] - b["open"]) / b["open"] * 100
        avg_vol = sum(bars[k]["volume"] for k in range(i - 10, i)) / 10
        if avg_vol <= 0: continue
        vol_ratio = b["volume"] / avg_vol
        if abs(move) < move_pct or vol_ratio < vol_x: continue
        direction = "long" if move > 0 else "short"
        if dir_filter and direction != dir_filter: continue
        out.append((i, direction))
    return out

def sim_with_fees(signals_with_dir, tp_pct=0.004, sl_pct=0.0025, max_hold=60, fee_pct=0.0010):
    trades = []
    for i, direction in signals_with_dir:
        entry = bars[i]["close"]
        if direction == "long":
            tp = entry * (1 + tp_pct); sl = entry * (1 - sl_pct)
        else:
            tp = entry * (1 - tp_pct); sl = entry * (1 + sl_pct)
        out_idx = None; out_px = None; reason = "timeout"
        for j in range(i + 1, min(i + 1 + max_hold, len(bars))):
            high = bars[j]["high"]; low = bars[j]["low"]
            if direction == "long":
                if low <= sl:   out_idx, out_px, reason = j, sl, "sl"; break
                if high >= tp:  out_idx, out_px, reason = j, tp, "tp"; break
            else:
                if high >= sl:  out_idx, out_px, reason = j, sl, "sl"; break
                if low <= tp:   out_idx, out_px, reason = j, tp, "tp"; break
        if out_idx is None:
            out_idx = min(i + max_hold, len(bars) - 1)
            out_px = bars[out_idx]["close"]
        ret_gross = (out_px - entry) / entry * 100 * (1 if direction == "long" else -1)
        trades.append({"i": i, "direction": direction, "ret_gross": ret_gross,
                        "ret_net": ret_gross - fee_pct * 100, "reason": reason})
    return trades

span_days = (bars[-1]["ts"] - bars[0]["ts"]) / 86400

# Original thresholds
sigs_old = momentum_signals(bars, 0.4, 1.5)
trades_old = sim_with_fees(sigs_old)
print(f"\n  Original momentum (0.4% / vol×1.5):  {len(sigs_old)} signals → {len(trades_old)} trades")

# New (lowered) thresholds
sigs_new = momentum_signals(bars, 0.2, 1.2)
trades_new = sim_with_fees(sigs_new)
per_week = len(sigs_new) / span_days * 7
print(f"  Lowered momentum (0.2% / vol×1.2):   {len(sigs_new)} signals → {len(trades_new)} trades  ({per_week:.0f}/week)")

if trades_new:
    n = len(trades_new)
    wins = [t for t in trades_new if t["ret_gross"] > 0]
    ev_gross = statistics.mean(t["ret_gross"] for t in trades_new)
    ev_net = ev_gross - 0.10
    win_rate = len(wins) / n * 100
    reasons = Counter(t["reason"] for t in trades_new)
    by_dir = Counter(t["direction"] for t in trades_new)
    print(f"\n  Lowered momentum results:")
    print(f"    direction split:  {dict(by_dir)}")
    print(f"    trades:           {n}")
    print(f"    win rate:         {win_rate:.1f}%       (gate: >=50%)")
    print(f"    exits:            tp={reasons.get('tp',0)}  sl={reasons.get('sl',0)}  timeout={reasons.get('timeout',0)}")
    print(f"    EV gross:         {ev_gross:+.4f}%/trade")
    print(f"    EV net:           {ev_net:+.4f}%/trade   (gate: >0)")

    # Split by direction
    for direction in ("long", "short"):
        d_trades = [t for t in trades_new if t["direction"] == direction]
        if not d_trades: continue
        d_wins = [t for t in d_trades if t["ret_gross"] > 0]
        d_ev = statistics.mean(t["ret_gross"] for t in d_trades)
        print(f"    {direction:5s} only:        WR {len(d_wins)/len(d_trades)*100:.1f}%  EV gross {d_ev:+.4f}%  EV net {d_ev - 0.10:+.4f}%")

    gates_pass = win_rate >= 50 and 5 <= per_week <= 30 and ev_net > 0
    print(f"\n  GATE (combined long+short): {'PASS' if gates_pass else 'FAIL'}")

# Save
Path(__file__).parent.joinpath("momentum_lowered_results.json").write_text(json.dumps({
    "params":  {"move_pct": 0.2, "vol_x": 1.2, "tp_pct": 0.004, "sl_pct": 0.0025},
    "signals": len(sigs_new),
    "per_week": round(per_week, 1) if trades_new else 0,
    "win_rate_pct": round(win_rate, 2) if trades_new else None,
    "ev_gross_pct": round(ev_gross, 5) if trades_new else None,
    "ev_net_pct": round(ev_net, 5) if trades_new else None,
}, indent=2))
