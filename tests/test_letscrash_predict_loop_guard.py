"""Tests for ``finance_agent/letscrash_predict_loop_guard.py``."""

from __future__ import annotations

import json
from pathlib import Path

from finance_agent.letscrash_predict_loop_guard import (
    load_guard_config,
    should_skip_iteration,
)


def test_should_skip_when_disabled() -> None:
    assert should_skip_iteration({"enabled": False}) == (False, "", 0.0)


def test_load_guard_config_missing_registry(tmp_path: Path) -> None:
    cfg = load_guard_config(tmp_path)
    assert cfg["enabled"] is False


def test_load_guard_config_from_registry(tmp_path: Path) -> None:
    reg = {
        "tuning": {
            "predict_loop_resource": {
                "enabled": True,
                "mem_available_min_mb": 99999,
                "loadavg_max": 0.01,
                "cooldown_sec": 3,
            }
        }
    }
    p = tmp_path / "letscrash" / "btc_strategy_0_1_rule_registry.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(reg), encoding="utf-8")
    cfg = load_guard_config(tmp_path)
    assert cfg["enabled"] is True
    assert cfg["mem_available_min_mb"] == 99999.0
    assert cfg["loadavg_max"] == 0.01
    assert cfg["cooldown_sec"] == 3.0
