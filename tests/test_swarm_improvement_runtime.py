"""``swarm_improvement_runtime`` — hints build + env apply."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def test_build_demo_runtime_hints_venue_churn() -> None:
    from finance_agent import swarm_improvement_runtime as sir

    bundle = {
        "recommendations": [{"id": "venue_churn", "severity": "high"}],
        "predict_loop_dataset": {"ok": True, "gate_ok_rate": 0.5},
    }
    h = sir.build_demo_runtime_hints(bundle, ttl_hours=1.0)
    assert "venue_churn" in h["triggered_by"]
    assert h["env_apply"]["SYGNIF_PREDICT_OPEN_IMMEDIATE"] == "0"
    assert float(h["env_apply"]["SYGNIF_SWARM_LOOP_INTERVAL_SEC"]) >= 60.0
    assert h["env_apply"].get("SYGNIF_PREDICT_DEFAULT_NOTIONAL_USDT") == "80000"


def test_build_demo_runtime_hints_all_recommendation_ids() -> None:
    from finance_agent import swarm_improvement_runtime as sir

    bundle = {
        "recommendations": [
            {"id": "venue_churn"},
            {"id": "hivemind_unreachable"},
            {"id": "nautilus_model_tension"},
            {"id": "logreg_direction_gate"},
            {"id": "bf_alignment"},
            {"id": "consec_open_fails"},
            {"id": "swarm_compute_fallback"},
        ],
        "predict_loop_dataset": {
            "ok": True,
            "gate_ok_rate": 0.2,
            "top_block_reasons": [("swarm_bf_vote=1_need_short_or_flat_ok", 20)],
        },
    }
    h = sir.build_demo_runtime_hints(bundle, ttl_hours=1.0)
    keys = set(h["env_apply"])
    assert "SWARM_ORDER_REQUIRE_HIVEMIND_VOTE" in keys
    assert h["env_apply"]["SWARM_ORDER_REQUIRE_HIVEMIND_VOTE"] == "0"
    assert h["env_apply"]["SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY"] == "0"
    assert h["env_apply"]["SWARM_ORDER_ML_LOGREG_MIN_CONF"] == "52"
    assert h["env_apply"]["SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE"] == "0"
    assert h["env_apply"]["SWARM_BYBIT_MAX_CONSEC_OPEN_FAILS"] == "40"
    assert "swarm_compute_fallback" in h["triggered_by"]


def test_apply_demo_runtime_hints_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from finance_agent import swarm_improvement_runtime as sir

    monkeypatch.setenv("SYGNIF_PREDICTION_AGENT_DIR", str(tmp_path))
    hints = {
        "schema_version": 1,
        "generated_utc": "2026-01-01T00:00:00Z",
        "expires_utc": "2099-01-01T00:00:00Z",
        "env_apply": {"SYGNIF_SWARM_LOOP_INTERVAL_SEC": "99", "UNKNOWN": "x"},
    }
    (tmp_path / "swarm_demo_runtime_hints.json").write_text(json.dumps(hints), encoding="utf-8")
    monkeypatch.setenv("SYGNIF_SWARM_RUNTIME_HINTS_APPLY", "1")
    out = sir.apply_demo_runtime_hints_env(REPO)
    assert out["applied"] is True
    assert os.environ.get("SYGNIF_SWARM_LOOP_INTERVAL_SEC") == "99"
    assert "UNKNOWN" not in out.get("keys", {})


def test_build_demo_runtime_hints_ttl_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_agent import swarm_improvement_runtime as sir

    monkeypatch.setenv("SYGNIF_SWARM_RUNTIME_HINTS_TTL_HOURS", "48")
    bundle = {"recommendations": [], "predict_loop_dataset": {"ok": True, "gate_ok_rate": 0.99}}
    h = sir.build_demo_runtime_hints(bundle)
    g = datetime.fromisoformat(str(h["generated_utc"]).replace("Z", "+00:00"))
    e = datetime.fromisoformat(str(h["expires_utc"]).replace("Z", "+00:00"))
    delta_h = (e - g).total_seconds() / 3600.0
    assert 47.5 < delta_h < 48.5


def test_apply_demo_runtime_hints_env_expired(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from finance_agent import swarm_improvement_runtime as sir

    monkeypatch.setenv("SYGNIF_PREDICTION_AGENT_DIR", str(tmp_path))
    hints = {
        "schema_version": 1,
        "expires_utc": "2000-01-01T00:00:00Z",
        "env_apply": {"SYGNIF_SWARM_LOOP_INTERVAL_SEC": "200"},
    }
    (tmp_path / "swarm_demo_runtime_hints.json").write_text(json.dumps(hints), encoding="utf-8")
    monkeypatch.setenv("SYGNIF_SWARM_RUNTIME_HINTS_APPLY", "1")
    out = sir.apply_demo_runtime_hints_env(REPO)
    assert out["applied"] is False
