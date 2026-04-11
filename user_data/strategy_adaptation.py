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
    "fa_long_score_low": 40,
    "fa_long_score_high": 64,
    "fa_short_score_low": 30,
    "fa_short_score_high": 60,
    "vol_strong_mult": 1.2,
    # Failure swing (Heavy91-style) — see .cursor/rules/sygnif-swing-tuning.mdc
    "sf_lookback_bars": 48,
    "sf_vol_filter_min": 0.03,
    "sf_sl_base": 0.02,
    "sf_sl_vol_scale": 0.02,
    "sf_tp_vol_scale": 0.05,
    "sf_ta_split": 50.0,
    # Session ORB (BTC/ETH, 5m) — see user_data/strategies/market_sessions_orb.py
    "orb_entry_enabled": 0,
    "max_slots_orb": 2,
    "orb_range_minutes": 30,
    "orb_min_range_pct": 0.05,
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
    "fa_long_score_low": (35, 50),
    "fa_long_score_high": (55, 75),
    "fa_short_score_low": (22, 42),
    "fa_short_score_high": (52, 68),
    "vol_strong_mult": (1.0, 2.0),
    "sf_lookback_bars": (24, 96),
    "sf_vol_filter_min": (0.015, 0.10),
    "sf_sl_base": (0.01, 0.045),
    "sf_sl_vol_scale": (0.0, 0.06),
    "sf_tp_vol_scale": (0.02, 0.12),
    "sf_ta_split": (40.0, 60.0),
    "orb_entry_enabled": (0, 1),
    "max_slots_orb": (0, 4),
    "orb_range_minutes": (15, 120),
    "orb_min_range_pct": (0.01, 0.20),
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
            "sf_vol_filter_min",
            "sf_sl_base",
            "sf_sl_vol_scale",
            "sf_tp_vol_scale",
            "sf_ta_split",
            "orb_min_range_pct",
        ):
            v = float(value)
            return max(lo, min(hi, v))
        v = int(round(float(value)))
        return int(max(lo, min(hi, v)))
    except (TypeError, ValueError):
        logger.warning("strategy_adaptation: invalid value for %r", key)
        return None


_LEGACY_ADAPT_KEYS: dict[str, str] = {
    "claude_long_score_low": "fa_long_score_low",
    "claude_long_score_high": "fa_long_score_high",
    "claude_short_score_low": "fa_short_score_low",
    "claude_short_score_high": "fa_short_score_high",
}


def validate_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    """Return only valid, clamped overrides."""
    merged = dict(raw)
    for old_k, new_k in _LEGACY_ADAPT_KEYS.items():
        if old_k in merged and new_k not in merged:
            merged[new_k] = merged[old_k]
    out: dict[str, Any] = {}
    for k, v in merged.items():
        if k not in DEFAULTS:
            continue
        c = _clamp(k, v)
        if c is not None:
            out[k] = c
    # Ensure FA (ambiguous) zone ordering
    lo_l = out.get("fa_long_score_low", DEFAULTS["fa_long_score_low"])
    hi_l = out.get("fa_long_score_high", DEFAULTS["fa_long_score_high"])
    if lo_l >= hi_l:
        out["fa_long_score_low"] = int(DEFAULTS["fa_long_score_low"])
        out["fa_long_score_high"] = int(DEFAULTS["fa_long_score_high"])
    lo_s = out.get("fa_short_score_low", DEFAULTS["fa_short_score_low"])
    hi_s = out.get("fa_short_score_high", DEFAULTS["fa_short_score_high"])
    if lo_s >= hi_s:
        out["fa_short_score_low"] = int(DEFAULTS["fa_short_score_low"])
        out["fa_short_score_high"] = int(DEFAULTS["fa_short_score_high"])
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
