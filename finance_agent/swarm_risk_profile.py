"""
Conservative **risk profiles** for ``scripts/swarm_auto_predict_protocol_loop.py``.

Profiles apply a coherent bundle of ``os.environ`` overrides (after launcher ``setdefault``s)
so demo trading uses smaller notional, lower leverage, tighter max qty, calmer cadence,
and TP/SL targets scaled for smaller risk.

CLI: ``--risk-profile demo_safe`` or env ``SYGNIF_SWARM_RISK_PROFILE=demo_safe``.
"""
from __future__ import annotations

import os
from collections.abc import MutableMapping
from typing import Final

_CANONICAL: Final[tuple[str, ...]] = ("default", "demo_safe")

# Env keys -> values applied when profile is active (assignment, not setdefault).
_DEMO_SAFE: Final[dict[str, str]] = {
    "SYGNIF_PREDICT_DEFAULT_NOTIONAL_USDT": "5000",
    "SYGNIF_PREDICT_DEFAULT_MANUAL_LEVERAGE": "10",
    "BYBIT_DEMO_ORDER_MAX_QTY": "0.05",
    "SYGNIF_SWARM_LOOP_INTERVAL_SEC": "120",
    "PREDICT_LOOP_REFRESH_ALIGNED_EVERY_N": "0",
    "SWARM_PORTFOLIO_AUTHORITY": "0",
    "SYGNIF_PREDICT_HOLD_UNTIL_PROFIT": "0",
    "SYGNIF_PREDICT_MIN_UPNL_TO_CLOSE_USDT": "25",
    "SYGNIF_PREDICT_MIN_EDGE_PROFIT_USDT": "8",
    "SYGNIF_PREDICT_MIN_DISCRETIONARY_CLOSE_SEC": "120",
    "SYGNIF_PREDICT_OPPOSITE_SIGNAL_CONFIRM_ITER": "2",
    "SWARM_BYBIT_ENTRY_COOLDOWN_SEC": "120",
    "SWARM_ORDER_ML_LOGREG_MIN_CONF": "59",
    "SYGNIF_SWARM_TP_USDT_TARGET": "150",
    "SYGNIF_SWARM_SL_USDT_TARGET": "90",
}

_PROFILES: Final[dict[str, dict[str, str]]] = {
    "default": {},
    "demo_safe": dict(_DEMO_SAFE),
}


def normalize_risk_profile(raw: str | None) -> str:
    """Return canonical profile name or raise ``ValueError``."""
    s = (raw or "default").strip().lower()
    if s in ("", "default", "legacy"):
        return "default"
    if s in _CANONICAL:
        return s
    if s in ("demo-safe", "safe", "conservative"):
        return "demo_safe"
    raise ValueError(f"unknown risk profile: {raw!r} (expected one of {_CANONICAL})")


def risk_profile_names() -> tuple[str, ...]:
    return _CANONICAL


def risk_profile_env_overrides(profile: str) -> dict[str, str]:
    """Return env overrides for ``profile`` (copy; empty for ``default``)."""
    name = normalize_risk_profile(profile)
    return dict(_PROFILES.get(name, {}))


def apply_swarm_risk_profile(
    profile: str, *, environ: MutableMapping[str, str] | None = None
) -> list[tuple[str, str]]:
    """
    Apply profile overrides to ``environ`` (default ``os.environ``).

    Returns the list of ``(key, value)`` pairs applied (empty for ``default``).
    """
    name = normalize_risk_profile(profile)
    overrides = _PROFILES.get(name) or {}
    target: MutableMapping[str, str] = os.environ if environ is None else environ
    applied: list[tuple[str, str]] = []
    for k, v in overrides.items():
        target[k] = str(v)
        applied.append((k, str(v)))
    return applied


def resolve_effective_risk_profile(cli_value: str | None, environ: MutableMapping[str, str] | None = None) -> str:
    """CLI wins, then ``SYGNIF_SWARM_RISK_PROFILE``, then ``default``."""
    envmap = os.environ if environ is None else environ
    if cli_value is not None and str(cli_value).strip():
        return normalize_risk_profile(str(cli_value))
    raw = str(envmap.get("SYGNIF_SWARM_RISK_PROFILE") or "").strip()
    if not raw:
        return "default"
    try:
        return normalize_risk_profile(raw)
    except ValueError:
        return "default"
