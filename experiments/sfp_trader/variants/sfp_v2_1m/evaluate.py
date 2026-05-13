"""sfp_v2 at 1m — community-standard SFP, but on 1m bars.

Sanity check: if the community-standard filters at 1m STILL fail,
that confirms timeframe is the binding constraint, not the filters.
"""
from __future__ import annotations
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
from sfp_v2_trigger import SfpV2State

PIVOT_LR        = int(  os.environ.get("SFP_PIVOT_LR",       "5"))
MIN_WICK_RATIO  = float(os.environ.get("SFP_MIN_WICK_RATIO", "0.55"))
MIN_RECLAIM_ATR = float(os.environ.get("SFP_MIN_RECLAIM_ATR","0.25"))
MIN_VOL_MULT    = float(os.environ.get("SFP_MIN_VOL_MULT",   "1.15"))
REQUIRE_VOL     = os.environ.get("SFP_REQUIRE_VOL", "1") != "0"

VARIANT_INFO = {
    "name":        "sfp_v2_1m",
    "description": f"Pivot {PIVOT_LR}/{PIVOT_LR} + filters at 1m TF (sanity check)",
    "expected":    "Likely FAIL — confirms 1m is below the recognized SFP TF floor",
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
