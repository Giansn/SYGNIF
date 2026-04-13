"""
Read ``tuning.r01_governance`` from ``letscrash/btc_strategy_0_1_rule_registry.json``.

Keeps prediction_agent + training_pipeline aligned with ``btc_strategy_0_1_engine``
without importing Freqtrade strategy code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def registry_path() -> Path:
    return _repo_root() / "letscrash" / "btc_strategy_0_1_rule_registry.json"


def load_r01_governance() -> tuple[bool, float, str]:
    """
    Returns ``(enabled, p_down_min_pct, runner_consensus_equals)``.
    On missing/invalid registry, matches engine defaults (enabled, 90.0, BEARISH).
    """
    enabled = True
    p_min = 90.0
    cons_need = "BEARISH"
    try:
        raw: dict[str, Any] = json.loads(registry_path().read_text(encoding="utf-8"))
    except Exception:
        return enabled, p_min, cons_need
    g = (raw.get("tuning") or {}).get("r01_governance") or {}
    if g.get("enabled") is False:
        enabled = False
    try:
        p_min = float(g.get("p_down_min_pct", 90.0))
    except (TypeError, ValueError):
        p_min = 90.0
    cons_need = str(g.get("runner_consensus_equals", "BEARISH") or "BEARISH").upper()
    return enabled, p_min, cons_need


def runner_consensus_from_snapshot(snap: dict[str, Any]) -> str:
    """
    Runner JSON nests under ``predictions.consensus``; allow legacy flat ``consensus`` on snapshot.
    """
    if not isinstance(snap, dict):
        return ""
    pred = snap.get("predictions") or {}
    if isinstance(pred, dict):
        c = str(pred.get("consensus", "") or "").strip().upper()
        if c:
            return c
    return str(snap.get("consensus", "") or "").strip().upper()
