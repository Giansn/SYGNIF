"""Hybrid variant — BB-width regime filter + trailing exit.

This is the most promising config from the prior backtest evidence:
  - regime (BB<0.005) lifted baseline WR 45% → 52%   (selectivity)
  - trailing alone lifted baseline WR 45% → 69%      (let-winners-run)
Combine: tighter regime filter shouldn't reduce WR much, and trailing
should boost it further. With fewer fires, fee/EV math improves.

Tune via env:
  SFP_BB_THRESHOLD (default 0.005) — regime filter
  SFP_TRAIL_PCT    (default 0.0015) — trail distance, must be > fee/2 to net
  SFP_TRAIL_ACT    (default 0.001)  — activation threshold
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
    "name":        "regime_trail",
    "description": (f"BB-width<{BB_THRESHOLD:.4f} regime filter + trailing exit "
                    f"(activation/trail set via SFP_TRAIL_ACT / SFP_TRAIL_PCT)"),
    "expected":    "PASS-candidate — selectivity × let-winners-run",
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
