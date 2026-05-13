"""Fib-EXTREMES variant — only fire at the actual range extremes.

  Bull SFP → LONG only if close is in the lowest 23.6% of the 240-bar range
            (between fib_0.0 and fib_0.236) — true discount zone.
  Bear SFP → SHORT only if close is in the highest 23.6% of the range
            (between fib_0.786 and fib_1.0) — true premium zone.

This is the strictest interpretation of "SFP at fib confluence" — sweep
a key low at the BOTTOM of the range, not somewhere in the middle.
"""
from __future__ import annotations
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
from fib_sfp_trigger import (
    FibSfpState as _Underlying,
    compute_fibonacci_levels,
    detect_sfp_on_bar,
)

VARIANT_INFO = {
    "name":        "fib_extremes",
    "description": "Bull SFP fires LONG only in lowest 23.6% of range; "
                   "Bear SFP fires SHORT only in highest 23.6% of range",
    "expected":    "Real reversal setups — should boost WR if signal has any edge",
}


class State:
    def __init__(self):
        self.s = _Underlying()


def evaluate(state, bar):
    if not bar.get("confirm"):
        return None
    s = state.s
    ts = int(bar.get("ts_ms_open", 0))
    s.bars.append({
        "ts_ms_open": ts,
        "open":  float(bar["open"]),
        "high":  float(bar["high"]),
        "low":   float(bar["low"]),
        "close": float(bar["close"]),
        "volume": float(bar["volume"]),
    })
    if len(s.bars) < s.min_bars + 1:
        return None

    bars_list = list(s.bars)
    bull_sfp, bear_sfp = detect_sfp_on_bar(bars_list, lookback=s.lookback)
    if not (bull_sfp or bear_sfp):
        return None

    window = bars_list[-s.maxlen:] if len(bars_list) >= s.maxlen else bars_list
    high_w = max(b["high"] for b in window)
    low_w  = min(b["low"]  for b in window)
    if high_w <= low_w:
        return None

    cur = bars_list[-1]
    close = cur["close"]
    diff = high_w - low_w
    fib_236 = low_w + 0.236 * diff
    fib_786 = low_w + 0.786 * diff

    # Position-in-range as 0..1: 0 = at low, 1 = at high
    range_pos = (close - low_w) / diff

    if bull_sfp and range_pos < 0.236 and ts > s._last_fire_ts_long:
        s._last_fire_ts_long = ts
        return {"direction": "long", "trigger": "fib_extremes",
                "mid": close, "meta": {"range_pos": round(range_pos, 3),
                                       "fib_threshold": 0.236}}
    if bear_sfp and range_pos > 0.786 and ts > s._last_fire_ts_short:
        s._last_fire_ts_short = ts
        return {"direction": "short", "trigger": "fib_extremes",
                "mid": close, "meta": {"range_pos": round(range_pos, 3),
                                       "fib_threshold": 0.786}}
    return None
