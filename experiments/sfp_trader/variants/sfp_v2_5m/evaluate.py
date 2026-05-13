"""sfp_v2 at 5m — community-standard pivot-based SFP detector.

Reads from the harness with SFP_AGGREGATE_TF=5 so bars are 5-minute. Uses
the AGPro-spec filters (pivot 5/5, wick >= 55%, reclaim >= 0.25 ATR,
volume >= 1.15 x SMA20). No fib level.

Run:
  SFP_AGGREGATE_TF=5 python ../_harness.py --variant sfp_v2_5m
"""
from __future__ import annotations
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
from sfp_v2_trigger import SfpV2State

# Allow harness sweeps to tune filters
PIVOT_LR        = int(  os.environ.get("SFP_PIVOT_LR",       "5"))
MIN_WICK_RATIO  = float(os.environ.get("SFP_MIN_WICK_RATIO", "0.55"))
MIN_RECLAIM_ATR = float(os.environ.get("SFP_MIN_RECLAIM_ATR","0.25"))
MIN_VOL_MULT    = float(os.environ.get("SFP_MIN_VOL_MULT",   "1.15"))
REQUIRE_VOL     = os.environ.get("SFP_REQUIRE_VOL", "1") != "0"

VARIANT_INFO = {
    "name":        "sfp_v2_5m",
    "description": (f"Pivot {PIVOT_LR}/{PIVOT_LR} + wick>={MIN_WICK_RATIO:.2f} + "
                    f"reclaim>={MIN_RECLAIM_ATR:.2f}xATR + vol>={MIN_VOL_MULT:.2f}xSMA"
                    f" (TF set via SFP_AGGREGATE_TF, run 5/15)"),
    "expected":    "If TV-community math has edge, this passes gates",
}


class State:
    def __init__(self):
        self.s = SfpV2State(
            pivot_left      = PIVOT_LR,
            pivot_right     = PIVOT_LR,
            min_wick_ratio  = MIN_WICK_RATIO,
            min_reclaim_atr = MIN_RECLAIM_ATR,
            min_vol_mult    = MIN_VOL_MULT,
            require_volume  = REQUIRE_VOL,
        )


def evaluate(state, bar):
    return state.s.evaluate(bar)
