"""fib_sr_v1_relaxed — same fib_bounce_long logic, tunable RSI + tolerance.

Tightening RSI from 30 to 35/40 should produce more fires; goal is to
find whether the 59% WR at 5m survives more aggressive sampling.
"""
from __future__ import annotations
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
from fib_sr_trigger import FibSrV1State

RSI_MAX     = float(os.environ.get("FIBSR_RSI_MAX",   "30"))
FIB_TOL_PCT = float(os.environ.get("FIBSR_FIB_TOL",   "0.005"))

VARIANT_INFO = {
    "name":        "fib_sr_v1_relaxed",
    "description": f"fib_bounce_long with RSI<{RSI_MAX:.0f}, tol={FIB_TOL_PCT*100:.2f}%",
    "expected":    "Sweep RSI threshold to find fires/WR trade-off",
}


class State:
    def __init__(self):
        self.s = FibSrV1State(rsi_max=RSI_MAX, fib_tol_pct=FIB_TOL_PCT)


def evaluate(state, bar):
    return state.s.evaluate(bar)
