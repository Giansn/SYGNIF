"""finance_agent/swarm_activate_bundle.py"""

from __future__ import annotations

import os

import pytest

from finance_agent.swarm_activate_bundle import apply_swarm_activate_improvements_defaults


def test_bundle_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYGNIF_SWARM_ACTIVATE_IMPROVEMENTS", raising=False)
    assert apply_swarm_activate_improvements_defaults() == []


def test_bundle_sets_expected(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_ACTIVATE_IMPROVEMENTS", "1")
    for k in (
        "SYGNIF_PREDICT_HIVEMIND_FUSION",
        "SYGNIF_SWARM_BYBIT_CLOSED_PNL",
        "SWARM_ORDER_SYGNIF_STRATEGY_GUIDELINES",
        "SWARM_ORDER_GUIDELINE_HIVEMIND_FUSION",
        "SWARM_ORDER_GUIDELINE_HIVEMIND_UNREACHABLE_ML",
        "SYGNIF_TRUTHCOIN_DC_ROOT",
        "SYGNIF_TRUTHCOIN_DC_CLI",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SYGNIF_TRUTHCOIN_DC_ROOT", str(tmp_path))
    (tmp_path / "target" / "debug").mkdir(parents=True, exist_ok=True)
    debug_cli = tmp_path / "target" / "debug" / "truthcoin_dc_app_cli"
    debug_cli.write_text("#!/bin/sh\necho '{}'\n")
    debug_cli.chmod(0o755)

    got = apply_swarm_activate_improvements_defaults()
    assert "SYGNIF_PREDICT_HIVEMIND_FUSION" in got
    assert "SWARM_ORDER_GUIDELINE_HIVEMIND_FUSION" in got
    assert "SWARM_ORDER_GUIDELINE_HIVEMIND_UNREACHABLE_ML" in got
    assert os.environ["SYGNIF_PREDICT_HIVEMIND_FUSION"] == "1"
    assert os.environ["SWARM_ORDER_GUIDELINE_HIVEMIND_FUSION"] == "1"
    assert os.environ["SWARM_ORDER_GUIDELINE_HIVEMIND_UNREACHABLE_ML"] == "1"
    assert "SYGNIF_TRUTHCOIN_DC_CLI" in got
    assert os.environ.get("SYGNIF_TRUTHCOIN_DC_CLI")


def test_bundle_respects_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_ACTIVATE_IMPROVEMENTS", "1")
    monkeypatch.setenv("SYGNIF_PREDICT_HIVEMIND_FUSION", "0")
    monkeypatch.setenv("SYGNIF_TRUTHCOIN_DC_ROOT", "/tmp")
    apply_swarm_activate_improvements_defaults()
    assert os.environ["SYGNIF_PREDICT_HIVEMIND_FUSION"] == "0"
