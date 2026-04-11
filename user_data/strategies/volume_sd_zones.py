"""
Volume Supply & Demand Zones — Python port of Heavy91's TradingView indicator.

Original: https://github.com/Heavy91/TradingView_Indicators
"Volume Supply and Demand Zones Indicator" (Pine v3)

Concept: volume change normalised by its rolling stdev → when the z-score
exceeds a threshold, the prior candle's [high, low] defines a zone.
Three tiers (big / mid / small) with configurable thresholds.
The most recent N zones per tier are kept as horizontal bands.

This port works on a single DataFrame (no multi-timeframe `security()` calls).
For MTF zones, run on the higher TF and merge down — same pattern as
`merge_informative_pair` already used in Sygnif.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Core: detect volume spikes and mark S/D zone boundaries
# ---------------------------------------------------------------------------

def attach_volume_sd_zones(
    df: pd.DataFrame,
    length: int = 5,
    threshold_big: float = 15.0,
    threshold_mid: float = 10.0,
    threshold_small: float = 5.0,
    n_zones: int = 4,
) -> None:
    """Compute volume S/D zone columns in-place.

    Adds per-tier columns:
      vsd_big_hi, vsd_big_lo     (most recent big-volume zone)
      vsd_mid_hi, vsd_mid_lo
      vsd_small_hi, vsd_small_lo

    Plus convenience booleans:
      vsd_in_demand  (close ≤ any zone low → potential demand/support)
      vsd_in_supply  (close ≥ any zone high → potential supply/resistance)
      vsd_signal     (z-score of volume change vs rolling stdev)
    """
    vol = df["volume"].astype(np.float64)
    vol_prev = vol.shift(1)
    change = np.where(vol_prev > 0, vol / vol_prev - 1.0, 0.0)
    change_s = pd.Series(change, index=df.index)
    stdev = change_s.rolling(window=length, min_periods=length).std().shift(1)
    signal = np.where(stdev > 0, change_s.abs() / stdev, 0.0)
    df["vsd_signal"] = signal

    hi_prev = df["high"].shift(1)
    lo_prev = df["low"].shift(1)

    is_big = signal > threshold_big
    is_mid = (signal > threshold_mid) & (signal <= threshold_big)
    is_small = (signal > threshold_small) & (signal <= threshold_mid)

    for tier, mask in [("big", is_big), ("mid", is_mid), ("small", is_small)]:
        hi_col = f"vsd_{tier}_hi"
        lo_col = f"vsd_{tier}_lo"
        df[hi_col] = np.where(mask, hi_prev, np.nan)
        df[lo_col] = np.where(mask, lo_prev, np.nan)
        df[hi_col] = df[hi_col].ffill(limit=n_zones * 288)
        df[lo_col] = df[lo_col].ffill(limit=n_zones * 288)

    all_hi_cols = [c for c in df.columns if c.startswith("vsd_") and c.endswith("_hi")]
    all_lo_cols = [c for c in df.columns if c.startswith("vsd_") and c.endswith("_lo")]

    zone_hi_max = df[all_hi_cols].max(axis=1) if all_hi_cols else pd.Series(np.inf, index=df.index)
    zone_lo_min = df[all_lo_cols].min(axis=1) if all_lo_cols else pd.Series(0, index=df.index)

    df["vsd_in_supply"] = df["close"] >= zone_hi_max
    df["vsd_in_demand"] = df["close"] <= zone_lo_min
