"""
Session labels (UTC) + Opening Range (ORB) on the Sygnif **5m** base timeframe.

Sessions (crypto-liquidity proxy, not US equity RTH):
  - asia:      00:00–08:00 UTC
  - eu_london: 08:00–13:00 UTC
  - us:        13:00–22:00 UTC
  - pacific:   22:00–24:00 UTC (AU/NZ tail)

ORB: first ``orb_minutes`` of each session define ``orb_high`` / ``orb_low``;
``orb_break_long`` = first close above ``orb_high`` after the range is **formed**
and range width ≥ ``min_range_pct`` of price.

Used only for **BTC/USDT** and **ETH/USDT** (spot or futures pair string).
"""

from __future__ import annotations

from typing import Final

import numpy as np
import pandas as pd

ORB_BASE_PAIRS: Final[tuple[str, ...]] = ("BTC/USDT", "ETH/USDT")


def normalize_pair(pair: str) -> str:
    return (pair or "").split(":")[0]


def is_orb_pair(pair: str) -> bool:
    return normalize_pair(pair) in ORB_BASE_PAIRS


def attach_orb_columns(
    df: pd.DataFrame,
    *,
    metadata_pair: str,
    timeframe_minutes: int = 5,
    orb_minutes: int = 30,
    min_range_pct: float = 0.05,
) -> pd.DataFrame:
    """
    Add columns: ``orb_session``, ``orb_seg_start``, ``orb_high``, ``orb_low``,
    ``orb_formed``, ``orb_range_pct``, ``orb_range_ok``, ``orb_break_long``.
    No-op (zeros/NaN) if pair is not BTC/ETH.
    """
    n = len(df)
    zf = np.zeros(n, dtype=bool)
    znan = np.full(n, np.nan)

    if not is_orb_pair(metadata_pair) or n == 0 or "date" not in df.columns:
        df["orb_session"] = ""
        df["orb_seg_start"] = pd.NaT
        df["orb_high"] = znan
        df["orb_low"] = znan
        df["orb_formed"] = zf
        df["orb_range_pct"] = znan
        df["orb_range_ok"] = zf
        df["orb_break_long"] = zf
        return df

    w = max(1, int(round(orb_minutes / max(1, timeframe_minutes))))

    dt = pd.to_datetime(df["date"], utc=True)
    h = dt.dt.hour
    d0 = dt.dt.floor("D")
    seg_start = np.select(
        [h < 8, (h >= 8) & (h < 13), (h >= 13) & (h < 22)],
        [
            d0,
            d0 + pd.Timedelta(hours=8),
            d0 + pd.Timedelta(hours=13),
        ],
        default=d0 + pd.Timedelta(hours=22),
    )
    sess = np.select(
        [h < 8, (h >= 8) & (h < 13), (h >= 13) & (h < 22)],
        ["asia", "eu_london", "us"],
        default="pacific",
    )
    df["orb_session"] = sess
    df["orb_seg_start"] = seg_start
    df["orb_high"] = np.nan
    df["orb_low"] = np.nan
    df["orb_formed"] = False

    for _, g in df.groupby("orb_seg_start", sort=False):
        pos_idx = g.sort_values("date").index
        sub = df.loc[pos_idx]
        if len(sub) == 0:
            continue
        wn = min(w, len(sub))
        hi = float(sub["high"].iloc[:wn].max())
        lo = float(sub["low"].iloc[:wn].min())
        df.loc[pos_idx, "orb_high"] = hi
        df.loc[pos_idx, "orb_low"] = lo
        df.loc[pos_idx, "orb_formed"] = np.arange(len(sub)) >= w

    df["orb_high"] = df["orb_high"].astype(np.float64)
    df["orb_low"] = df["orb_low"].astype(np.float64)
    rng = (df["orb_high"] - df["orb_low"]) / df["close"].replace(0, np.nan) * 100.0
    df["orb_range_pct"] = rng
    df["orb_range_ok"] = (rng >= float(min_range_pct)) & df["orb_formed"]
    prev = df["close"].shift(1)
    df["orb_break_long"] = (
        df["orb_range_ok"]
        & (df["close"] > df["orb_high"])
        & (prev <= df["orb_high"])
    ).fillna(False)

    return df
