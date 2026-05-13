"""fib_sr_v4 — v3 (v1 core + RSRS) + FVG magnet filter.

Env knobs:
  FIBSRV4_FVG_MODE=off|score|strict|ce   FVG filter mode (default score)
  FIBSRV4_FVG_DISP=1.0                   displacement (C2 body / ATR ratio)
  FIBSRV4_FVG_TOL=0.005                  FVG proximity tolerance
  FIBSRV4_RSRS_THR=0.0                   RSRS z-threshold (default v3 winner)
"""
from __future__ import annotations
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
from fib_sr_v4_trigger import FibSrV4State

FVG_MODE     = os.environ.get("FIBSRV4_FVG_MODE",  "score")
FVG_DISP     = float(os.environ.get("FIBSRV4_FVG_DISP", "1.0"))
FVG_TOL      = float(os.environ.get("FIBSRV4_FVG_TOL",  "0.005"))
RSRS_THR     = float(os.environ.get("FIBSRV4_RSRS_THR", "0.0"))
RSRS_ENABLED = os.environ.get("FIBSRV4_RSRS", "1") != "0"

VARIANT_INFO = {
    "name":        "fib_sr_v4",
    "description": (f"v3 (RSRS={RSRS_ENABLED} thr={RSRS_THR}) + FVG[{FVG_MODE}] "
                    f"disp>={FVG_DISP:.2f}xATR tol={FVG_TOL*100:.2f}%"),
    "expected":    "v3 + magnet filter — should boost WR if FVG is real edge",
}


class State:
    def __init__(self):
        self.s = FibSrV4State(
            rsrs_enabled = RSRS_ENABLED,
            rsrs_threshold = RSRS_THR,
            fvg_mode = FVG_MODE,
            fvg_displacement_atr = FVG_DISP,
            fvg_tol_pct = FVG_TOL,
        )


def evaluate(state, bar):
    return state.s.evaluate(bar)
