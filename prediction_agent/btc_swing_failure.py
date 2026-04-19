"""
Swing-failure style S/R (aligned with ``finance_agent/bot.py`` TA snapshot).

48-bar rolling support/resistance from prior highs/lows, stability flags, and
``|close - EMA120| / EMA120`` as a volatility proxy. **Causal** (no future rows).
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def swing_failure_snapshot(df: pd.DataFrame) -> dict[str, Any]:
    """
    Last-bar swing-failure pattern flags (``sf_long`` / ``sf_short``).

    Expects columns ``High``, ``Low``, ``Close`` (Bybit / ``btc_predict_live`` layout).
    """
    need = {"High", "Low", "Close"}
    if not need.issubset(set(df.columns)):
        return {
            "ok": False,
            "detail": f"missing columns need={sorted(need)} have={list(df.columns)[:12]}",
        }
    if len(df) < 55:
        return {"ok": False, "detail": f"too_few_rows={len(df)}"}

    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    ema120 = close.ewm(span=120, adjust=False).mean()

    sf_resistance = high.shift(1).rolling(48, min_periods=48).max()
    sf_support = low.shift(1).rolling(48, min_periods=48).min()
    sf_resistance_stable = sf_resistance == sf_resistance.shift(1)
    sf_support_stable = sf_support == sf_support.shift(1)
    ema_d = ema120.replace(0, pd.NA)
    sf_volatility = ((close - ema120).abs() / ema_d).fillna(0.0)

    i = len(df) - 1
    sup = float(sf_support.iloc[i])
    res = float(sf_resistance.iloc[i])
    lv = float(low.iloc[i])
    hv = float(high.iloc[i])
    cl = float(close.iloc[i])
    sv = float(sf_volatility.iloc[i])
    ss_st = bool(sf_support_stable.iloc[i])
    sr_st = bool(sf_resistance_stable.iloc[i])

    sf_long = bool(lv <= sup and cl > sup and ss_st and sv > 0.03)
    sf_short = bool(hv >= res and cl < res and sr_st and sv > 0.03)

    return {
        "ok": True,
        "sf_support": round(sup, 2),
        "sf_resistance": round(res, 2),
        "sf_support_stable": ss_st,
        "sf_resistance_stable": sr_st,
        "sf_volatility": round(sv, 6),
        "sf_long": sf_long,
        "sf_short": sf_short,
    }
