"""
ADX trend-strength + candlestick pattern indicators for Sygnif strategies.

Uses pandas_ta (already in Docker image). Both functions are pure DataFrame → DataFrame;
they do NOT depend on Freqtrade — safe to call from tests or scripts.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pandas_ta as pta


# ---------------------------------------------------------------------------
# ADX (Average Directional Index)
# ---------------------------------------------------------------------------

def attach_adx(df: pd.DataFrame, length: int = 14) -> None:
    """Compute ADX_14, DMP_14, DMN_14 in-place.

    ADX measures trend strength (0–100):
      >25 = trending, >50 = strong trend, <20 = weak/range-bound.
    DMP / DMN = directional movement plus / minus.
    """
    adx_df = pta.adx(df["high"], df["low"], df["close"], length=length)
    if isinstance(adx_df, pd.DataFrame):
        for col in adx_df.columns:
            df[col] = adx_df[col]
    else:
        df[f"ADX_{length}"] = np.nan
        df[f"DMP_{length}"] = np.nan
        df[f"DMN_{length}"] = np.nan


def adx_ta_score_component(df: pd.DataFrame) -> pd.Series:
    """ADX contribution to the TA score (±5).

    Strong uptrend  (ADX > 25, DMP > DMN):  +5
    Strong downtrend (ADX > 25, DMN > DMP):  -5
    Weak / range (ADX ≤ 25):                  0
    """
    adx = df.get("ADX_14", pd.Series(0, index=df.index)).fillna(0)
    dmp = df.get("DMP_14", pd.Series(0, index=df.index)).fillna(0)
    dmn = df.get("DMN_14", pd.Series(0, index=df.index)).fillna(0)

    trending = adx > 25
    return pd.Series(
        np.where(trending & (dmp > dmn), 5,
                 np.where(trending & (dmn > dmp), -5, 0)),
        index=df.index,
        dtype=np.float64,
    )


# ---------------------------------------------------------------------------
# Candlestick Patterns (top 6 via pandas_ta)
# ---------------------------------------------------------------------------

CHERRY_PICK_PATTERNS = [
    "hammer",
    "invertedhammer",
    "engulfing",
    "morningstar",
    "eveningstar",
    "doji",
]


def attach_candlestick_patterns(df: pd.DataFrame, patterns: list[str] | None = None) -> list[str]:
    """Add boolean candlestick-pattern columns in-place.

    Returns list of column names actually added (some may fail if data is
    too short for the pattern window).
    """
    patterns = patterns or CHERRY_PICK_PATTERNS
    added: list[str] = []
    for name in patterns:
        try:
            result = pta.cdl_pattern(df["open"], df["high"], df["low"], df["close"], name=name)
            if isinstance(result, pd.DataFrame):
                for col in result.columns:
                    df[col] = result[col]
                    added.append(col)
        except Exception:
            pass
    return added


def candlestick_bullish_score(df: pd.DataFrame) -> pd.Series:
    """Net bullish candlestick signal (0 / +1 / -1 per bar).

    +1 if any bullish pattern fires (hammer, inverted_hammer, engulfing > 0, morningstar).
    -1 if any bearish pattern fires (eveningstar, engulfing < 0).
     0 otherwise.

    Columns follow pandas_ta naming: CDL_HAMMER, CDL_ENGULFING, etc.
    """
    bull = pd.Series(0, index=df.index, dtype=np.int8)
    bear = pd.Series(0, index=df.index, dtype=np.int8)

    for col in ["CDL_HAMMER", "CDL_INVERTEDHAMMER", "CDL_MORNINGSTAR"]:
        if col in df.columns:
            bull |= (df[col] > 0).astype(np.int8)

    if "CDL_ENGULFING" in df.columns:
        bull |= (df["CDL_ENGULFING"] > 0).astype(np.int8)
        bear |= (df["CDL_ENGULFING"] < 0).astype(np.int8)

    for col in ["CDL_EVENINGSTAR"]:
        if col in df.columns:
            bear |= (df[col] < 0).astype(np.int8)

    if "CDL_DOJI" in df.columns:
        pass  # doji = neutral / indecision; not scored

    return (bull - bear).astype(np.int8)
