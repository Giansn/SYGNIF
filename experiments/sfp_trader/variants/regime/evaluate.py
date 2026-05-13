"""Regime-filtered variant — fire SFP only in range-bound regimes.

Hypothesis: SFP is a mean-reversion signal that bleeds in trending bars.
Filter via Bollinger Band width: tight band = range; wide = trend.

  bb_width = (mean(close,20) + 2*std - (mean - 2*std)) / mean
           = 4*std / mean
"""
from __future__ import annotations
import collections
import math
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
from fib_sfp_trigger import FibSfpState as _Underlying

BB_THRESHOLD = float(os.environ.get("SFP_BB_THRESHOLD", "0.005"))
BB_WINDOW = 20

VARIANT_INFO = {
    "name":        "regime",
    "description": f"SFP gated by BB-width < {BB_THRESHOLD:.4f} ({BB_WINDOW}-bar window)",
    "expected":    "PASS-candidate — filters trending bars",
    "params":      {"bb_window": BB_WINDOW, "bb_threshold": BB_THRESHOLD},
}


class State:
    def __init__(self):
        self.s = _Underlying()
        self.closes: collections.deque[float] = collections.deque(maxlen=BB_WINDOW)

    def bb_width(self):
        if len(self.closes) < BB_WINDOW:
            return None
        n = len(self.closes)
        mean = sum(self.closes) / n
        var = sum((x - mean) ** 2 for x in self.closes) / n
        std = math.sqrt(var)
        return (4 * std) / mean if mean > 0 else None


def evaluate(state, bar):
    state.closes.append(float(bar["close"]))
    payload = state.s.evaluate(bar)
    if payload is None:
        return None
    width = state.bb_width()
    if width is None or width >= BB_THRESHOLD:
        return None
    meta = dict(payload.get("meta", {}))
    meta["bb_width"] = round(width, 5)
    meta["bb_threshold"] = BB_THRESHOLD
    payload = dict(payload); payload["meta"] = meta
    return payload
