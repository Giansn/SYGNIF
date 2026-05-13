"""Baseline variant — raw bull/bear SFP at fib_0.618 / fib_0.382.

Fires both directions per PR #15 design. Expected ~22-45% WR. Reference for
all other variants.
"""
from __future__ import annotations
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
from fib_sfp_trigger import FibSfpState as _Underlying

VARIANT_INFO = {
    "name":        "baseline",
    "description": "Raw bull+bear SFP at fib levels, no filtering (PR #15 spec)",
    "expected":    "FAIL — ~22-45% WR per prior 30d backtests",
}


class State:
    def __init__(self):
        self.s = _Underlying()


def evaluate(state, bar):
    return state.s.evaluate(bar)
