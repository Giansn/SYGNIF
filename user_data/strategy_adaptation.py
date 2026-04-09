"""
Runtime strategy adaptation for SygnifStrategy.

The Sygnif / Cursor agent may update `user_data/strategy_adaptation.json` with bounded
`overrides` after market analysis. Freqtrade reloads these periodically (no restart).

Never place orders from this module — validation + attribute merge only.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Defaults must match SygnifStrategy class attributes at import time.
DEFAULTS: dict[str, Any] = {
    "max_slots_strong": 6,
    "max_slots_strong_short": 6,
    "max_slots_swing": 4,
    "premium_nonreserved_max": 10,
    "sentiment_threshold_buy": 55.0,
    "sentiment_threshold_sell": 40.0,
    "soft_sl_ratio_spot": 0.60,
    "soft_sl_ratio_futures": 0.60,
    "doom_cooldown_secs": 14400,
    "strong_ta_min_score": 65,
    "strong_ta_short_max_score": 25,
    "claude_long_score_low": 40,
    "claude_long_score_high": 64,
    "claude_short_score_low": 30,
    "claude_short_score_high": 60,
    "vol_strong_mult": 1.2,
}

# Inclusive min/max per key (safety rails for adaptive tuning).
BOUNDS: dict[str, tuple[float, float]] = {
    "max_slots_strong": (3, 10),
    "max_slots_strong_short": (3, 10),
    "max_slots_swing": (2, 8),
    "premium_nonreserved_max": (4, 14),
    "sentiment_threshold_buy": (50.0, 68.0),
    "sentiment_threshold_sell": (25.0, 48.0),
    "soft_sl_ratio_spot": (0.45, 0.85),
    "soft_sl_ratio_futures": (0.45, 0.85),
    "doom_cooldown_secs": (3600, 86400),
    "strong_ta_min_score": (58, 78),
    "strong_ta_short_max_score": (15, 35),
    "claude_long_score_low": (35, 50),
    "claude_long_score_high": (55, 75),
    "claude_short_score_low": (22, 42),
    "claude_short_score_high": (52, 68),
    "vol_strong_mult": (1.0, 2.0),
}


def _clamp(key: str, value: Any) -> Any | None:
    if key not in BOUNDS:
        logger.warning("strategy_adaptation: unknown key %r ignored", key)
        return None
    lo, hi = BOUNDS[key]
    try:
        if key in (
            "sentiment_threshold_buy",
            "sentiment_threshold_sell",
            "soft_sl_ratio_spot",
            "soft_sl_ratio_futures",
            "vol_strong_mult",
        ):
            v = float(value)
            return max(lo, min(hi, v))
        v = int(round(float(value)))
        return int(max(lo, min(hi, v)))
    except (TypeError, ValueError):
        logger.warning("strategy_adaptation: invalid value for %r", key)
        return None


def validate_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    """Return only valid, clamped overrides."""
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if k not in DEFAULTS:
            continue
        c = _clamp(k, v)
        if c is not None:
            out[k] = c
    # Ensure Claude zone ordering
    lo_l = out.get("claude_long_score_low", DEFAULTS["claude_long_score_low"])
    hi_l = out.get("claude_long_score_high", DEFAULTS["claude_long_score_high"])
    if lo_l >= hi_l:
        out["claude_long_score_low"] = int(DEFAULTS["claude_long_score_low"])
        out["claude_long_score_high"] = int(DEFAULTS["claude_long_score_high"])
    lo_s = out.get("claude_short_score_low", DEFAULTS["claude_short_score_low"])
    hi_s = out.get("claude_short_score_high", DEFAULTS["claude_short_score_high"])
    if lo_s >= hi_s:
        out["claude_short_score_low"] = int(DEFAULTS["claude_short_score_low"])
        out["claude_short_score_high"] = int(DEFAULTS["claude_short_score_high"])
    return out


def load_adaptation_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("strategy_adaptation: cannot read %s: %s", path, e)
        return {}
    overrides = data.get("overrides")
    if not isinstance(overrides, dict):
        return {}
    meta = {k: data.get(k) for k in ("version", "updated", "source", "reason") if k in data}
    validated = validate_overrides(overrides)
    if meta:
        logger.info(
            "strategy_adaptation: loaded %d overrides meta=%s keys=%s",
            len(validated),
            meta,
            list(validated.keys()),
        )
    return validated


def apply_defaults_and_overrides(strategy: Any, overrides: dict[str, Any]) -> None:
    """Reset tunables to DEFAULTS then apply overrides (mutates strategy instance)."""
    for k, v in DEFAULTS.items():
        setattr(strategy, k, v)
    for k, v in overrides.items():
        setattr(strategy, k, v)
