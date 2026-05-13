"""fib_sr_v1 — verbatim port of SygnifStrategy.fib_bounce_long.

Fires when:
  - close within 0.5% of fib_0.618 (240-bar window)
  - RSI(14) < 30
  - bull SFP (low < 50-bar key_low AND close > key_low)
"""
from __future__ import annotations
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
from fib_sr_trigger import FibSrV1State

VARIANT_INFO = {
    "name":        "fib_sr_v1",
    "description": "Port of SygnifStrategy.fib_bounce_long — fib_0.618 + RSI<30 + bull SFP",
    "expected":    "Baseline measurement of currently-coded entry tag",
}


class State:
    def __init__(self):
        self.s = FibSrV1State()


def evaluate(state, bar):
    return state.s.evaluate(bar)
