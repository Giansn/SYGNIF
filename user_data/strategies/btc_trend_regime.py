"""
BTC trend regime (shared by strategy + offline ML scripts).

**Definition (v1):** aligned multi-TF bull + 5m trend strength + price above 1h EMA200.

- ``RSI_14_1h`` > ``RSI_BULL_MIN`` (default 50)
- ``RSI_14_4h`` > ``RSI_BULL_MIN``
- ``close`` > ``EMA_200_1h`` (informative merge on 5m dataframe)
- ``ADX_14`` (5m) > ``ADX_MIN`` (default 25)

Requires merged columns from ``MarketStrategy2._populate_indicators_inner`` (or the same
feature pipeline as ``scripts/train_ml_ensemble.py`` after indicator compute).

Not investment advice — explicit rules for backtesting / ablation only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Tunable defaults (strategy may override via class attrs in future)
RSI_BULL_MIN = 50.0
ADX_MIN = 25.0


def btc_trend_long_row(row: pd.Series) -> bool:
    """True if last row satisfies BTC trend-long regime."""
    r1 = float(row.get("RSI_14_1h", 50) or 50)
    r4 = float(row.get("RSI_14_4h", 50) or 50)
    adx = float(row.get("ADX_14", 0) or 0)
    close = float(row.get("close", 0) or 0)
    ema1h = float(row.get("EMA_200_1h", np.nan) or np.nan)
    if not np.isfinite(ema1h) or ema1h <= 0 or close <= 0:
        return False
    return bool(
        r1 > RSI_BULL_MIN
        and r4 > RSI_BULL_MIN
        and close > ema1h
        and adx > ADX_MIN
    )


def btc_trend_long_series(df: pd.DataFrame) -> pd.Series:
    """Vectorized regime column (float 0/1 for ML exports)."""
    r1 = df.get("RSI_14_1h", pd.Series(50.0, index=df.index)).astype(float).fillna(50.0)
    r4 = df.get("RSI_14_4h", pd.Series(50.0, index=df.index)).astype(float).fillna(50.0)
    adx = df.get("ADX_14", pd.Series(0.0, index=df.index)).astype(float).fillna(0.0)
    close = df["close"].astype(float)
    ema1h = df.get("EMA_200_1h", pd.Series(np.nan, index=df.index)).astype(float)
    ok = (
        (r1 > RSI_BULL_MIN)
        & (r4 > RSI_BULL_MIN)
        & (close > ema1h)
        & (adx > ADX_MIN)
        & ema1h.notna()
        & (ema1h > 0)
    )
    return ok.astype(float)


def sygnif_profile() -> str:
    import os

    return (os.environ.get("SYGNIF_PROFILE") or "").strip().lower()


def is_btc_pair(pair: str) -> bool:
    base = (pair or "").split("/")[0].replace(":", "")
    return base.upper() == "BTC"
