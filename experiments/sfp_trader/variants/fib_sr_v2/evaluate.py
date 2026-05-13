"""fib_sr_v2 — research-redesigned fib + S/R confluence detector.

Math (synthesised from Babypips, LuxAlgo, ACY 2025 backtests, QuantInsti):
  - Pivot 5/5 (Williams Fractal) for true S/R levels
  - Fib from most-recent major swing (range >= 3 x ATR)
  - Confluence: |pivot - fib| / price <= 0.5%
  - Score = count overlapping sources (fib + pivot + round number)
  - Require score >= 2 (community standard for "high probability")
  - Entry filters: bounce candle + volume >= 1.3 x SMA20 + RSI in zone

Both directions: long at support-confluence, short at resistance-confluence.
"""
from __future__ import annotations
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
from fib_sr_trigger import FibSrV2State

# Env knobs for sweeps
MIN_SCORE = int(  os.environ.get("FIBSR_MIN_SCORE",     "2"))
TOL_PCT   = float(os.environ.get("FIBSR_TOL_PCT",       "0.005"))
VOL_MULT  = float(os.environ.get("FIBSR_VOL_MULT",      "1.3"))
RSI_LONG  = float(os.environ.get("FIBSR_RSI_LONG_MAX",  "40"))
RSI_SHORT = float(os.environ.get("FIBSR_RSI_SHORT_MIN", "60"))

VARIANT_INFO = {
    "name":        "fib_sr_v2",
    "description": (f"Pivot 5/5 + fib confluence (tol {TOL_PCT*100:.1f}%, "
                    f"score>={MIN_SCORE}) + bounce + vol>={VOL_MULT}xSMA + "
                    f"RSI long<{RSI_LONG:.0f} short>{RSI_SHORT:.0f}"),
    "expected":    "Research-redesigned signal — claim is 65-75% WR per blogs",
}


class State:
    def __init__(self):
        self.s = FibSrV2State(
            confluence_min_score = MIN_SCORE,
            confluence_tol_pct   = TOL_PCT,
            vol_mult             = VOL_MULT,
            rsi_long_max         = RSI_LONG,
            rsi_short_min        = RSI_SHORT,
        )


def evaluate(state, bar):
    return state.s.evaluate(bar)
