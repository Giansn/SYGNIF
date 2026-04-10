"""
Single-source TA score (0–100) for finance_agent — mirrors SygnifStrategy._calculate_ta_score_vectorized (last bar).
Placed in user_data/strategies so finance_agent/bot.py can import it via sys.path.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

TA_SPEC_VERSION = "1"

_SPEC_BODY = """
rsi14_bins rsi3_bins ema_cross bb aroon stoch cmf mtf btc vol
v1 parity SygnifStrategy._calculate_ta_score_vectorized
"""


def ta_spec_fingerprint() -> str:
    return hashlib.sha256(_SPEC_BODY.encode()).hexdigest()[:16]


def _f(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def ta_score_from_indicator_dict(ind: dict) -> dict:
    """Return {score, components, spec_version, spec_fingerprint}."""
    score = 50.0
    comp: dict[str, float] = {}

    rsi = _f(ind.get("rsi"), 50.0)
    if rsi < 30:
        d = 15.0
    elif rsi < 40:
        d = 8.0
    elif rsi > 70:
        d = -15.0
    elif rsi > 60:
        d = -8.0
    else:
        d = 0.0
    score += d
    comp["rsi14"] = d

    rsi3 = _f(ind.get("rsi3"), 50.0)
    if rsi3 < 10:
        d = 10.0
    elif rsi3 < 20:
        d = 5.0
    elif rsi3 > 90:
        d = -10.0
    elif rsi3 > 80:
        d = -5.0
    else:
        d = 0.0
    score += d
    comp["rsi3"] = d

    ema_bull = bool(ind.get("ema_bull"))
    ema_cross = bool(ind.get("ema_cross"))
    if ema_cross:
        d = 10.0
    elif ema_bull:
        d = 7.0
    else:
        d = -7.0
    score += d
    comp["ema"] = d

    last = _f(ind.get("price"), 0.0)
    bbl = ind.get("bb_lower")
    bbu = ind.get("bb_upper")
    if bbl is not None and bbu is not None and last > 0:
        bblf, bbuf = _f(bbl), _f(bbu)
        if last <= bblf:
            d = 8.0
        elif last >= bbuf:
            d = -8.0
        else:
            d = 0.0
        score += d
        comp["bb"] = d
    else:
        comp["bb"] = 0.0

    aroonu = _f(ind.get("aroonu"), 50.0)
    aroond = _f(ind.get("aroond"), 50.0)
    if aroonu > 80 and aroond < 30:
        d = 8.0
    elif aroond > 80 and aroonu < 30:
        d = -8.0
    else:
        d = 0.0
    score += d
    comp["aroon"] = d

    stoch = _f(ind.get("stochrsi_k"), 50.0)
    if stoch < 20:
        d = 5.0
    elif stoch > 80:
        d = -5.0
    else:
        d = 0.0
    score += d
    comp["stochrsi"] = d

    cmf = _f(ind.get("cmf"), 0.0)
    if cmf > 0.15:
        d = 5.0
    elif cmf < -0.15:
        d = -5.0
    else:
        d = 0.0
    score += d
    comp["cmf"] = d

    r1h = ind.get("rsi_14_1h")
    r4h = ind.get("rsi_14_4h")
    if r1h is not None and r4h is not None:
        r1, r4 = _f(r1h, 50.0), _f(r4h, 50.0)
        if r1 < 35 and r4 < 40:
            d = 5.0
        elif r1 > 70 and r4 > 65:
            d = -5.0
        else:
            d = 0.0
        score += d
        comp["mtf_rsi"] = d
    else:
        comp["mtf_rsi"] = 0.0

    btc_rsi = ind.get("btc_rsi_14_1h")
    if btc_rsi is not None:
        br = _f(btc_rsi, 50.0)
        if br < 30:
            d = -5.0
        elif br > 60:
            d = 3.0
        else:
            d = 0.0
        score += d
        comp["btc_rsi"] = d
    else:
        comp["btc_rsi"] = 0.0

    vol_ratio = _f(ind.get("vol_ratio"), 1.0)
    if vol_ratio > 1.5:
        if score > 50:
            d = 3.0
        elif score < 50:
            d = -3.0
        else:
            d = 0.0
        score += d
        comp["volume"] = d
    else:
        comp["volume"] = 0.0

    score = max(0.0, min(100.0, score))
    fp = ta_spec_fingerprint()
    return {
        "score": score,
        "components": comp,
        "spec_version": TA_SPEC_VERSION,
        "spec_fingerprint": fp,
    }
