"""Fib-SWAPPED variant — fires LONG at fib_0.382 (deep retrace) and
SHORT at fib_0.618 (shallow retrace). This is what classic SFP+fib
confluence would use: long at the discount zone, short at premium.

If this dramatically beats baseline, the fib levels in the original
fib_sfp_trigger.py are inverted from convention.
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
    "name":        "fib_swapped",
    "description": "LONG at fib_0.382 (deep retrace, classic discount zone) + "
                   "SHORT at fib_0.618 (shallow retrace, classic premium zone)",
    "expected":    "If baseline labels are inverted, this should beat baseline",
}


class State:
    def __init__(self):
        # Reimplement minimally — share buffer + detection helpers from underlying
        self.s = _Underlying()


def evaluate(state, bar):
    """Replicate the underlying logic but swap fib levels for the fire decision."""
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
    levels = compute_fibonacci_levels(high_w, low_w)
    fib_618 = levels["fib_0.618"]
    fib_382 = levels["fib_0.382"]

    cur = bars_list[-1]
    close = cur["close"]
    near_618 = abs(close - fib_618) / close < s.fib_proximity
    near_382 = abs(close - fib_382) / close < s.fib_proximity

    # SWAPPED: LONG near fib_0.382 (close to low), SHORT near fib_0.618 (close to high)
    if bull_sfp and near_382 and ts > s._last_fire_ts_long:
        s._last_fire_ts_long = ts
        return {"direction": "long", "trigger": "fib_sfp_swapped",
                "mid": close, "meta": {"sfp": "bull", "fib_fired": "0.382",
                                       "fib_0_382": fib_382, "fib_0_618": fib_618}}

    if bear_sfp and near_618 and ts > s._last_fire_ts_short:
        s._last_fire_ts_short = ts
        return {"direction": "short", "trigger": "fib_sfp_swapped",
                "mid": close, "meta": {"sfp": "bear", "fib_fired": "0.618",
                                       "fib_0_382": fib_382, "fib_0_618": fib_618}}

    return None
