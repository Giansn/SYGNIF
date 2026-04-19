"""
Optional **live** shrink of ``direction_logistic.confidence`` after ``fit_predict_live``.

Reads ``prediction_agent/live_trading_calibration.json`` (typically maintained by
``scripts/sygnif_update_predict_calibration_from_ft.py`` from recent **closed** Freqtrade P/L).

Keys (all optional):

- ``direction_logistic_confidence_multiplier`` (default **1.0** if absent)
- ``direction_logistic_confidence_cap`` — hard cap 0–100 after scaling
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _repo_root(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    return Path(__file__).resolve().parents[1]


def load_live_calibration(repo_root: Path | None = None) -> dict[str, Any]:
    p = _repo_root(repo_root) / "prediction_agent" / "live_trading_calibration.json"
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def apply_direction_logistic_calibration(out: dict[str, Any], *, repo_root: Path | None = None) -> None:
    """Mutate ``out['predictions']['direction_logistic']`` in place when a calibration file exists."""
    cal = load_live_calibration(repo_root)
    if not cal:
        return
    try:
        mult = float(cal.get("direction_logistic_confidence_multiplier", 1.0) or 1.0)
    except (TypeError, ValueError):
        mult = 1.0
    mult = max(0.05, min(1.5, mult))
    cap_raw = cal.get("direction_logistic_confidence_cap")
    cap: float | None
    try:
        cap = float(cap_raw) if cap_raw is not None and str(cap_raw).strip() != "" else None
    except (TypeError, ValueError):
        cap = None
    if cap is not None:
        cap = max(0.0, min(100.0, cap))

    preds = out.get("predictions")
    if not isinstance(preds, dict):
        return
    dlr = preds.get("direction_logistic")
    if not isinstance(dlr, dict):
        return
    try:
        raw = float(dlr.get("confidence") or 0.0)
    except (TypeError, ValueError):
        raw = 0.0
    if "confidence_pre_calibration" not in dlr:
        dlr["confidence_pre_calibration"] = round(raw, 1)
    adj = raw * mult
    if cap is not None:
        adj = min(adj, cap)
    adj = max(0.0, min(100.0, adj))
    dlr["confidence"] = round(adj, 1)
    dlr["live_calibration_applied"] = True
