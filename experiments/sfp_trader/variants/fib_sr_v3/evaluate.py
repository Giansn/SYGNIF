"""fib_sr_v3 — v1 winner core + RSRS regime gate + 3-pivot cluster.

Env knobs (test filters in isolation):
  FIBSRV3_RSRS=1/0       enable RSRS regime gate (default 1)
  FIBSRV3_CLUSTER=1/0    enable 3-pivot cluster gate (default 1)
  FIBSRV3_RSRS_THR=0.7   z-score threshold
  FIBSRV3_CLU_MIN=2      cluster min touches
  FIBSRV3_RSI_MAX=35     RSI threshold (v1 winner)
  FIBSRV3_FIB_TOL=0.005  fib proximity (v1 winner)
"""
from __future__ import annotations
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
from fib_sr_v3_trigger import FibSrV3State

RSRS_ENABLED    = os.environ.get("FIBSRV3_RSRS",    "1") != "0"
CLUSTER_ENABLED = os.environ.get("FIBSRV3_CLUSTER", "1") != "0"
RSRS_THR        = float(os.environ.get("FIBSRV3_RSRS_THR", "0.7"))
CLU_MIN         = int(  os.environ.get("FIBSRV3_CLU_MIN",  "2"))
RSI_MAX         = float(os.environ.get("FIBSRV3_RSI_MAX",  "35"))
FIB_TOL         = float(os.environ.get("FIBSRV3_FIB_TOL",  "0.005"))

VARIANT_INFO = {
    "name":        "fib_sr_v3",
    "description": (f"v1 core (RSI<{RSI_MAX:.0f}, fib_tol={FIB_TOL*100:.2f}%) + "
                    f"RSRS={RSRS_ENABLED} (thr={RSRS_THR}) + "
                    f"Cluster={CLUSTER_ENABLED} (min={CLU_MIN})"),
    "expected":    "v1 + audit findings: improvement if filters add signal",
}


class State:
    def __init__(self):
        self.s = FibSrV3State(
            rsi_max         = RSI_MAX,
            fib_tol_pct     = FIB_TOL,
            rsrs_enabled    = RSRS_ENABLED,
            rsrs_threshold  = RSRS_THR,
            cluster_enabled = CLUSTER_ENABLED,
            cluster_min     = CLU_MIN,
        )


def evaluate(state, bar):
    return state.s.evaluate(bar)
