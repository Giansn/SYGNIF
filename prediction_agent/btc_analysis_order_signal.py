"""
Map **BTC prediction + training channel** JSON to an optional **forceenter** intent.

Aligned with ``btc_strategy_0_1_engine.r01_training_runner_bearish`` (R01 governance: no
aggressive long timing when next-bar-down probability is extreme **and** runner consensus BEARISH).

This module does **not** call Freqtrade — only returns a small dict or ``None``.
"""

from __future__ import annotations

from typing import Any, TypedDict


class ForceenterIntent(TypedDict):
    side: str
    enter_tag: str
    reason: str


def r01_bearish_from_training(doc: dict[str, Any]) -> bool:
    """Same predicate as ``r01_training_runner_bearish`` but from an in-memory ``recognition`` dict."""
    rec = doc.get("recognition") or {}
    try:
        p_down = float(rec.get("last_bar_probability_down_pct") or 0.0)
    except (TypeError, ValueError):
        p_down = 0.0
    snap = rec.get("btc_predict_runner_snapshot") or {}
    cons = str(snap.get("consensus", "") or "").strip().upper()
    return p_down >= 90.0 and cons == "BEARISH"


def _consensus_from_prediction(pred: dict[str, Any]) -> str:
    p = pred.get("predictions") or {}
    return str(p.get("consensus", "") or "").strip().upper()


def _direction_fallback(pred: dict[str, Any], min_conf: float) -> str | None:
    d = (pred.get("predictions") or {}).get("direction_logistic") or {}
    label = str(d.get("label", "") or "").strip().upper()
    try:
        conf = float(d.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf < min_conf:
        return None
    if label == "UP":
        return "BULLISH"
    if label == "DOWN":
        return "BEARISH"
    return None


def decide_forceenter_intent(
    training_channel: dict[str, Any] | None,
    btc_prediction: dict[str, Any] | None,
    *,
    allow_short: bool = False,
    direction_min_confidence: float = 65.0,
) -> ForceenterIntent | None:
    """
    Return a **long** or **short** intent, or ``None`` if no trade should be opened from analysis alone.

    Rules (conservative):
    - **Long:** prediction consensus **BULLISH** (or direction UP with confidence), and **not** R01 bearish stack.
    - **Short:** only if ``allow_short`` and consensus **BEARISH** (or direction DOWN with confidence).
    - If consensus missing, fall back to ``direction_logistic`` when confidence ≥ ``direction_min_confidence``.
    """
    if not isinstance(btc_prediction, dict):
        return None
    train = training_channel if isinstance(training_channel, dict) else {}
    bear = r01_bearish_from_training(train) if train else False

    cons = _consensus_from_prediction(btc_prediction)
    if not cons:
        cons = _direction_fallback(btc_prediction, direction_min_confidence) or ""

    if cons == "BULLISH":
        if bear:
            return None
        return {
            "side": "long",
            "enter_tag": "btc_analysis_consensus",
            "reason": "prediction consensus BULLISH; R01 bearish stack not active",
        }
    if cons == "BEARISH":
        if not allow_short:
            return None
        return {
            "side": "short",
            "enter_tag": "btc_analysis_consensus",
            "reason": "prediction consensus BEARISH (short; use --allow-short)",
        }
    return None
