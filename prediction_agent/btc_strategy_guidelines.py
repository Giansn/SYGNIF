"""
Sygnif **Freqtrade** entry patterns as **protocol guidelines** for Swarm-gated demo orders.

Mirrors the intent of ``SygnifStrategy`` tags:

- **sygnif_swing** (long): ``sf_long`` (``btc_swing_failure``) **and** TA confluence
  (``ta_proxy >= split``; proxy from live 5m TA columns, not identical to strategy TA score).
- **swing_failure**-only longs are **not** enough for the swing guideline (TA below split).
- **orb_long**: session ORB first breakout (``market_sessions_orb.attach_orb_columns``) for BTC/ETH.

ORB uses the same session math as ``user_data/strategies/market_sessions_orb.py`` (lowercase OHLCV names).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
_STRAT_DIR = _REPO / "user_data" / "strategies"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _import_market_sessions_orb():
    s = str(_STRAT_DIR)
    if s not in sys.path:
        sys.path.insert(0, s)
    import market_sessions_orb as mso  # noqa: PLC0415

    return mso


def ta_proxy_from_df(df: pd.DataFrame) -> float | None:
    """
    Last-bar 0–100 TA **proxy** for guideline split (RSI / MACD hist / EMA200 distance).

    Not bit-identical to ``SygnifStrategy._calculate_ta_score_vectorized`` — same role as
    ``sf_ta_split`` discriminator between ``sygnif_swing`` vs ``swing_failure``.
    """
    if len(df) < 1:
        return None
    last = df.iloc[-1]
    try:
        rsi = float(last.get("RSI_14") or 50.0)
    except (TypeError, ValueError):
        rsi = 50.0
    try:
        mh = float(last.get("MACD_hist") or 0.0)
    except (TypeError, ValueError):
        mh = 0.0
    try:
        dist = float(last.get("dist_ema200_pct") or 0.0)
    except (TypeError, ValueError):
        dist = 0.0
    # Center ~50; bullish RSI/MACD/EMA200 tail lifts score
    s = 50.0 + (rsi - 50.0) * 1.15
    s += 6.0 if mh > 0 else (-6.0 if mh < 0 else 0.0)
    s += max(-12.0, min(12.0, dist * 1.5))
    return max(0.0, min(100.0, float(s)))


def _linear_to_slash_pair(linear_symbol: str) -> str:
    u = (linear_symbol or "BTCUSDT").upper().replace("/", "")
    if u.startswith("ETH"):
        return "ETH/USDT"
    return "BTC/USDT"


def compute_strategy_guidelines(df: pd.DataFrame, *, linear_symbol: str = "BTCUSDT") -> dict[str, Any]:
    """
    Build ``strategy_guidelines`` dict for embedding in ``btc_prediction`` / fusion override.

    ``orb_long_ok`` is **False** when the pair is not ORB-eligible or ORB attach fails.
    """
    from btc_swing_failure import swing_failure_snapshot  # noqa: PLC0415

    pair_slash = _linear_to_slash_pair(linear_symbol)
    split_long = _env_float("SWARM_ORDER_GUIDELINE_TA_SPLIT_LONG", 50.0)
    split_short = _env_float("SWARM_ORDER_GUIDELINE_TA_SPLIT_SHORT", 50.0)
    orb_minutes = int(_env_float("SYGNIF_GUIDELINE_ORB_MINUTES", 30.0))
    orb_min_range = _env_float("SYGNIF_GUIDELINE_ORB_MIN_RANGE_PCT", 0.05)

    ta_proxy = ta_proxy_from_df(df)
    sf = swing_failure_snapshot(df)
    if not isinstance(sf, dict) or not sf.get("ok"):
        return {
            "ok": False,
            "detail": str(sf.get("detail") if isinstance(sf, dict) else "swing_snapshot_failed"),
            "ta_proxy": ta_proxy,
            "sf_long": False,
            "sf_short": False,
            "sygnif_swing_long_ok": False,
            "sygnif_swing_short_ok": False,
            "orb_long_ok": False,
            "split_long": split_long,
            "split_short": split_short,
            "pair": pair_slash,
        }

    sf_long = bool(sf.get("sf_long"))
    sf_short = bool(sf.get("sf_short"))
    ta = float(ta_proxy) if ta_proxy is not None else 50.0
    sygnif_swing_long_ok = sf_long and ta >= split_long
    sygnif_swing_short_ok = sf_short and ta <= split_short

    orb_long_ok = False
    try:
        mso = _import_market_sessions_orb()
        if mso.is_orb_pair(pair_slash):
            vol = df["Volume"].astype(float) if "Volume" in df.columns else pd.Series(0.0, index=df.index)
            odf = pd.DataFrame(
                {
                    "date": pd.to_datetime(df["Date"], utc=True),
                    "high": df["High"].astype(float),
                    "low": df["Low"].astype(float),
                    "close": df["Close"].astype(float),
                    "volume": vol,
                }
            )
            odf = mso.attach_orb_columns(
                odf,
                metadata_pair=pair_slash,
                timeframe_minutes=5,
                orb_minutes=orb_minutes,
                min_range_pct=orb_min_range,
            )
            orb_long_ok = bool(odf.iloc[-1].get("orb_break_long", False))
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "detail": f"orb_guideline_error:{str(exc)[:180]}",
            "ta_proxy": ta,
            "sf_long": sf_long,
            "sf_short": sf_short,
            "sygnif_swing_long_ok": sygnif_swing_long_ok,
            "sygnif_swing_short_ok": sygnif_swing_short_ok,
            "orb_long_ok": False,
            "split_long": split_long,
            "split_short": split_short,
            "pair": pair_slash,
        }

    return {
        "ok": True,
        "detail": "",
        "ta_proxy": round(ta, 4),
        "sf_long": sf_long,
        "sf_short": sf_short,
        "sygnif_swing_long_ok": sygnif_swing_long_ok,
        "sygnif_swing_short_ok": sygnif_swing_short_ok,
        "orb_long_ok": orb_long_ok,
        "split_long": split_long,
        "split_short": split_short,
        "pair": pair_slash,
    }
