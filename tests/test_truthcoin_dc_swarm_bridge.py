from __future__ import annotations

import os

import pytest


def test_hivemind_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYGNIF_SWARM_TRUTHCOIN_DC", raising=False)
    from finance_agent.truthcoin_dc_swarm_bridge import hivemind_explore_snapshot

    doc = hivemind_explore_snapshot()
    assert doc.get("enabled") is False


def test_hivemind_cli_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_TRUTHCOIN_DC", "1")
    monkeypatch.setenv("SYGNIF_TRUTHCOIN_DC_CLI", "/nonexistent/truthcoin_dc_app_cli")
    monkeypatch.setenv("SYGNIF_TRUTHCOIN_DC_CACHE_SEC", "0")
    from finance_agent import truthcoin_dc_swarm_bridge as mod

    mod._CACHE = None  # noqa: SLF001
    from finance_agent.truthcoin_dc_swarm_bridge import hivemind_explore_snapshot

    doc = hivemind_explore_snapshot()
    assert doc.get("enabled") is True
    assert doc.get("ok") is False
    assert "readme" in doc
