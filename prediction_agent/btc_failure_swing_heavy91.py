"""
Failure Swing Strategy — Pine v3 port (Heavy91-style counter-trade on false S/R breaks).

Reference: ``TradingView_Indicators`` Failure Swing logic (support/resistance from shifted
high/low, EMA volatility filter, flat level + failed reclaim).

Causal: uses only rows ``<=`` the evaluation bar (last closed bar in live OHLC).
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import pandas as pd


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = (os.environ.get(name) or str(default)).strip() or str(default)
    try:
        v = int(float(raw))
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or str(default)).strip() or str(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def failure_swing_heavy91_snapshot(df: pd.DataFrame) -> dict[str, Any]:
    """
    Last-bar snapshot aligned with Pine ``Failure Swing Strategy V1`` entry rules.

    Returns ``entry_long`` / ``entry_short`` when volatility filter + flat S/R + failed swing
    conditions match on the **last** row.
    """
    need = {"Open", "High", "Low", "Close"}
    if not need.issubset(set(df.columns)):
        return {
            "ok": False,
            "detail": f"missing_columns need={sorted(need)}",
        }
    period = _env_int("SYGNIF_FS_HEAVY91_PERIOD", 84, lo=5, hi=500)
    ema_p = _env_int("SYGNIF_FS_HEAVY91_EMA", 120, lo=5, hi=500)
    thresh_pct = _env_float("SYGNIF_FS_HEAVY91_VOL_THRESHOLD", 5.0)  # Pine: Threshold/100
    vol_floor = max(1e-12, thresh_pct / 100.0)

    d = df.sort_values("Date").reset_index(drop=True) if "Date" in df.columns else df.reset_index(drop=True)
    if len(d) < max(period, ema_p) + 5:
        return {"ok": False, "detail": f"too_few_rows={len(d)}"}

    high = pd.to_numeric(d["High"], errors="coerce")
    low = pd.to_numeric(d["Low"], errors="coerce")
    close = pd.to_numeric(d["Close"], errors="coerce")
    open_ = pd.to_numeric(d["Open"], errors="coerce")

    # resistance = highest(high[1], period)  — rolling max of prior bar highs
    h1 = high.shift(1)
    resistance = h1.rolling(period, min_periods=period).max()
    l1 = low.shift(1)
    support = l1.rolling(period, min_periods=period).min()

    ema_base = close.ewm(span=ema_p, adjust=False).mean()
    # Pine: abs(open - emaBase) / emaBase  (sign trick collapses to abs for entries)
    eb = ema_base.replace(0, np.nan)
    volatility = ((open_ - ema_base) / eb).abs().fillna(0.0)

    i = len(d) - 1
    if i < 2:
        return {"ok": False, "detail": "need_at_least_3_rows"}

    res_prev = float(resistance.iloc[i - 1])
    res_prev2 = float(resistance.iloc[i - 2])
    sup_prev = float(support.iloc[i - 1])
    sup_prev2 = float(support.iloc[i - 2])

    hi = float(high.iloc[i])
    lo = float(low.iloc[i])
    cl = float(close.iloc[i])
    opn = float(open_.iloc[i])
    vol = float(volatility.iloc[i])
    ema_b = float(ema_base.iloc[i])

    res_flat = abs(res_prev - res_prev2) < 1e-12 * max(1.0, abs(res_prev))
    sup_flat = abs(sup_prev - sup_prev2) < 1e-12 * max(1.0, abs(sup_prev))

    vol_ok = vol > vol_floor
    short_sig = bool(
        vol_ok
        and res_flat
        and hi >= res_prev
        and cl < res_prev
    )
    long_sig = bool(
        vol_ok
        and sup_flat
        and lo <= sup_prev
        and cl > sup_prev
    )

    # Volatility-adjusted EMA band (Pine) — informational for TP-style overlays
    sign_o = 0.0 if abs(opn - ema_b) < 1e-18 else (1.0 if opn > ema_b else -1.0)
    coeff_tp = _env_float("SYGNIF_FS_HEAVY91_COEFF_TP", 0.05)
    coeff_sl = _env_float("SYGNIF_FS_HEAVY91_COEFF_SL", 0.02)
    ema_adj = ema_b * (1.0 + vol * coeff_tp * sign_o) if ema_b == ema_b else float("nan")

    return {
        "ok": True,
        "period": period,
        "ema_period": ema_p,
        "volatility_pct": round(vol * 100.0, 4),
        "volatility_filter_pct": round(vol_floor * 100.0, 4),
        "volatility_ok": vol_ok,
        "resistance": round(res_prev, 2),
        "support": round(sup_prev, 2),
        "resistance_flat": res_flat,
        "support_flat": sup_flat,
        "ema_base": round(ema_b, 2),
        "ema_vol_adjusted": None if ema_adj != ema_adj else round(ema_adj, 2),
        "entry_short": short_sig,
        "entry_long": long_sig,
    }
