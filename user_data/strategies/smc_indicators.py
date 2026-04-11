"""
Smart Money Concepts (SMC) indicators for Sygnif strategies.

Uses `smartmoneyconcepts` (pip install smartmoneyconcepts).
Source: https://github.com/joshyattridge/smart-money-concepts

Exposes ICT-style market-structure signals:
  - Swing Highs / Lows
  - Break of Structure (BOS) / Change of Character (CHoCH)
  - Fair Value Gaps (FVG)
  - Order Blocks (OB)
  - Liquidity sweeps
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_smc = None

def _get_smc():
    global _smc
    if _smc is None:
        from smartmoneyconcepts.smc import smc
        _smc = smc
    return _smc


def _ohlc_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Build the OHLC DataFrame the SMC library expects (lowercase cols)."""
    return pd.DataFrame({
        "open": df["open"].values,
        "high": df["high"].values,
        "low": df["low"].values,
        "close": df["close"].values,
        "volume": df["volume"].values,
    }, index=df.index)


def attach_smc_indicators(
    df: pd.DataFrame,
    swing_length: int = 10,
    close_break: bool = True,
    fvg_join: bool = False,
    range_percent: float = 0.01,
) -> None:
    """Compute SMC columns in-place.

    Columns added (all prefixed ``smc_``):
      smc_swing_hl          – 1 = swing high, -1 = swing low, 0 = neither
      smc_bos_choch         – BOS / CHoCH detection per bar
      smc_fvg               – Fair Value Gap detection
      smc_ob                – Order Block detection
      smc_liquidity         – Liquidity sweep detection
      smc_bullish_struct    – consolidated bullish structure bool
      smc_bearish_struct    – consolidated bearish structure bool
    """
    lib = _get_smc()
    ohlc = _ohlc_frame(df)

    try:
        swing_hl = lib.swing_highs_lows(ohlc, swing_length=swing_length)
        if isinstance(swing_hl, pd.DataFrame):
            df["smc_swing_high"] = swing_hl.get("HighLow", pd.Series(0, index=df.index)).fillna(0)
        else:
            s = pd.Series(swing_hl, index=df.index) if swing_hl is not None else pd.Series(0, index=df.index)
            df["smc_swing_high"] = s.fillna(0)
    except Exception as e:
        logger.debug("SMC swing_highs_lows failed: %s", e)
        df["smc_swing_high"] = 0
        swing_hl = pd.DataFrame({"HighLow": pd.Series(0, index=df.index), "Level": np.nan})

    if not isinstance(swing_hl, pd.DataFrame):
        swing_hl = pd.DataFrame({"HighLow": df["smc_swing_high"], "Level": np.nan}, index=df.index)

    try:
        bos = lib.bos_choch(ohlc, swing_hl, close_break=close_break)
        if isinstance(bos, pd.DataFrame):
            df["smc_bos"] = bos.get("BOS", pd.Series(0, index=df.index)).fillna(0)
            df["smc_choch"] = bos.get("CHOCH", pd.Series(0, index=df.index)).fillna(0)
        else:
            s = pd.Series(bos, index=df.index) if bos is not None else pd.Series(0, index=df.index)
            df["smc_bos"] = s.fillna(0)
            df["smc_choch"] = 0
    except Exception as e:
        logger.debug("SMC bos_choch failed: %s", e)
        df["smc_bos"] = 0
        df["smc_choch"] = 0

    try:
        fvg = lib.fvg(ohlc, join_consecutive=fvg_join)
        if isinstance(fvg, pd.DataFrame):
            df["smc_fvg"] = fvg.get("FVG", pd.Series(0, index=df.index)).fillna(0)
        else:
            s = pd.Series(fvg, index=df.index) if fvg is not None else pd.Series(0, index=df.index)
            df["smc_fvg"] = s.fillna(0)
    except Exception as e:
        logger.debug("SMC fvg failed: %s", e)
        df["smc_fvg"] = 0

    try:
        ob = lib.ob(ohlc, swing_hl, close_mitigation=False)
        if isinstance(ob, pd.DataFrame):
            df["smc_ob"] = ob.get("OB", pd.Series(0, index=df.index)).fillna(0)
        else:
            s = pd.Series(ob, index=df.index) if ob is not None else pd.Series(0, index=df.index)
            df["smc_ob"] = s.fillna(0)
    except Exception as e:
        logger.debug("SMC ob failed: %s", e)
        df["smc_ob"] = 0

    try:
        liq = lib.liquidity(ohlc, swing_hl, range_percent=range_percent)
        if isinstance(liq, pd.DataFrame):
            df["smc_liquidity"] = liq.get("Liquidity", pd.Series(0, index=df.index)).fillna(0)
        else:
            s = pd.Series(liq, index=df.index) if liq is not None else pd.Series(0, index=df.index)
            df["smc_liquidity"] = s.fillna(0)
    except Exception as e:
        logger.debug("SMC liquidity failed: %s", e)
        df["smc_liquidity"] = 0

    df["smc_bullish_struct"] = (
        (df["smc_bos"] > 0)
        | (df["smc_choch"] > 0)
        | (df["smc_fvg"] > 0)
        | (df["smc_ob"] > 0)
    )
    df["smc_bearish_struct"] = (
        (df["smc_bos"] < 0)
        | (df["smc_choch"] < 0)
        | (df["smc_fvg"] < 0)
        | (df["smc_ob"] < 0)
    )
