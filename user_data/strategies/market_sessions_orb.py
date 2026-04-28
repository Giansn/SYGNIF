"""
Session labels (UTC) + Opening Range (ORB) on the Sygnif **5m** base timeframe,
with optional swing-failure-at-session-level overlay.

Sessions (crypto-liquidity proxy, not US equity RTH):
  - asia:      00:00–08:00 UTC
  - eu_london: 08:00–13:00 UTC
  - us:        13:00–22:00 UTC
  - pacific:   22:00–24:00 UTC (AU/NZ tail)

ORB: first ``orb_minutes`` of each session define ``orb_high`` / ``orb_low``;
``orb_break_long`` (resp. ``orb_break_short``) = first close above ``orb_high``
(below ``orb_low``) after the range is **formed** and range width ≥
``min_range_pct`` of price.

Swing-failure overlay (``swing_failure_check=True``, default; ATR-scaled,
post-OR-formed only):
  - ``orb_sfp_long``    : low ≤ ``orb_low`` and close back inside (bullish SFP).
  - ``orb_sfp_short``   : high ≥ ``orb_high`` and close back inside (bearish SFP).
  - ``orb_failed_long`` : a recent ``orb_break_long`` reverses back below
    ``orb_high`` within ``failed_break_bars`` (ACD B-failure / Turtle Soup → short).
  - ``orb_failed_short``: mirror of ``orb_failed_long`` (→ long).

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


def _wilder_atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    # min_periods=2 (not n): ATR must be defined within the first ~10 bars of a
    # session so SFP / failed-break checks can fire on early-session sweeps.
    # Wilder smoothing converges quickly; the close-back-inside + overshoot
    # filters absorb the remaining instability.
    return tr.ewm(alpha=1.0 / max(1, n), adjust=False, min_periods=2).mean()


def attach_orb_columns(
    df: pd.DataFrame,
    *,
    metadata_pair: str,
    timeframe_minutes: int = 5,
    orb_minutes: int = 30,
    min_range_pct: float = 0.05,
    swing_failure_check: bool = True,
    failed_break_bars: int = 3,
    sfp_overshoot_atr_min: float = 0.10,
    sfp_overshoot_atr_max: float = 5.0,
    sfp_reclaim_atr_min: float = 0.05,
) -> pd.DataFrame:
    """
    Add columns: ``orb_session``, ``orb_seg_start``, ``orb_high``, ``orb_low``,
    ``orb_formed``, ``orb_range_pct``, ``orb_range_ok``, ``orb_break_long``,
    ``orb_break_short``.

    When ``swing_failure_check`` is True, also adds ``orb_atr``,
    ``orb_sfp_long``, ``orb_sfp_short``, ``orb_failed_long``,
    ``orb_failed_short``.

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
        df["orb_break_short"] = zf
        if swing_failure_check:
            df["orb_atr"] = znan
            df["orb_sfp_long"] = zf
            df["orb_sfp_short"] = zf
            df["orb_failed_long"] = zf
            df["orb_failed_short"] = zf
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
    df["orb_break_short"] = (
        df["orb_range_ok"]
        & (df["close"] < df["orb_low"])
        & (prev >= df["orb_low"])
    ).fillna(False)

    if not swing_failure_check:
        return df

    atr = _wilder_atr(df, n=14)
    df["orb_atr"] = atr.astype(np.float64)
    atr_safe = atr.replace(0, np.nan)

    overshoot_long = (df["orb_low"] - df["low"]) / atr_safe
    overshoot_short = (df["high"] - df["orb_high"]) / atr_safe
    reclaim_long = (df["close"] - df["orb_low"]) / atr_safe
    reclaim_short = (df["orb_high"] - df["close"]) / atr_safe

    df["orb_sfp_long"] = (
        df["orb_formed"]
        & (df["low"] <= df["orb_low"])
        & (df["close"] > df["orb_low"])
        & (overshoot_long >= float(sfp_overshoot_atr_min))
        & (overshoot_long <= float(sfp_overshoot_atr_max))
        & (reclaim_long >= float(sfp_reclaim_atr_min))
    ).fillna(False).astype(bool)
    df["orb_sfp_short"] = (
        df["orb_formed"]
        & (df["high"] >= df["orb_high"])
        & (df["close"] < df["orb_high"])
        & (overshoot_short >= float(sfp_overshoot_atr_min))
        & (overshoot_short <= float(sfp_overshoot_atr_max))
        & (reclaim_short >= float(sfp_reclaim_atr_min))
    ).fillna(False).astype(bool)

    df["orb_failed_long"] = False
    df["orb_failed_short"] = False
    K = max(1, int(failed_break_bars))
    for _, g in df.groupby("orb_seg_start", sort=False):
        pos_idx = g.sort_values("date").index
        if len(pos_idx) == 0:
            continue
        sub = df.loc[pos_idx]
        bl = sub["orb_break_long"].to_numpy(dtype=bool)
        bs = sub["orb_break_short"].to_numpy(dtype=bool)
        cl = sub["close"].to_numpy(dtype=float)
        oh = sub["orb_high"].to_numpy(dtype=float)
        ol = sub["orb_low"].to_numpy(dtype=float)
        m = len(sub)
        fl = np.zeros(m, dtype=bool)
        fs = np.zeros(m, dtype=bool)
        for i in range(1, m):
            lo_i = max(0, i - K)
            if bl[lo_i:i].any() and cl[i] < oh[i]:
                fl[i] = True
            if bs[lo_i:i].any() and cl[i] > ol[i]:
                fs[i] = True
        df.loc[pos_idx, "orb_failed_long"] = fl
        df.loc[pos_idx, "orb_failed_short"] = fs

    return df
