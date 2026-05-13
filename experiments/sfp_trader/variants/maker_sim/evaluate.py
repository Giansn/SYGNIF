"""Maker-entry simulation — same baseline fires + reduced fee assumption.

Bybit demo perp VIP0 fees: maker -0.025% / taker +0.055% RT.
- Current harness default: 0.10% RT (assumes taker on both legs).
- Limit entry (post-only) + taker exit = ~0.055% + 0% = 0.055% RT.
- Limit entry (post-only) + limit exit (TP as maker order, SL as stop-limit
  with limit price ≈ stop) = ~0.025% RT but lower fill probability.

This variant uses the regime filter and tests both fee assumptions:
  SFP_FEE_PCT=0.00055 (taker-exit-only) — realistic for stop-loss fills
  SFP_FEE_PCT=0.00025 (full-maker)      — optimistic if both legs fill at limit
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
    "name":        "maker_sim",
    "description": (f"BB-width<{BB_THRESHOLD:.4f} regime + maker-entry fee model "
                    f"(set via SFP_FEE_PCT, run from this dir)"),
    "expected":    "Tests whether the SFP signal works given lower-fee execution",
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
