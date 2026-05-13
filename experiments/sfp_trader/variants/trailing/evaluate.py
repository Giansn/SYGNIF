"""Trailing-exit variant — same fires as baseline; trailing stop replaces fixed TP.

Run with the harness's trailing mode:
  SFP_TRAIL_PCT=0.0015 SFP_TRAIL_ACT=0.001 python ../_harness.py --variant trailing
"""
from __future__ import annotations
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
from fib_sfp_trigger import FibSfpState as _Underlying

VARIANT_INFO = {
    "name":        "trailing",
    "description": "Raw SFP fires + trailing exit (env-tuned via SFP_TRAIL_PCT / SFP_TRAIL_ACT)",
    "expected":    "Higher WR than baseline; net depends on capture vs fee",
}


class State:
    def __init__(self):
        self.s = _Underlying()


def evaluate(state, bar):
    return state.s.evaluate(bar)
