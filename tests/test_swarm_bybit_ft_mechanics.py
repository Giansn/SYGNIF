"""swarm_bybit_ft_mechanics — Freqtrade-style protections for Bybit predict loop."""
from __future__ import annotations

from pathlib import Path

import pytest

from finance_agent import swarm_bybit_ft_mechanics as ft


def test_entry_allowed_cooldown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SWARM_BYBIT_FT_STATE_JSON", str(tmp_path / "st.json"))
    monkeypatch.setenv("SWARM_BYBIT_ENTRY_COOLDOWN_SEC", "3600")
    monkeypatch.setenv("SWARM_BYBIT_MAX_CONSEC_OPEN_FAILS", "0")
    repo = tmp_path
    assert ft.entry_allowed(repo, "BTCUSDT", iter_count=1)[0] is True
    ft.record_open_success(repo, "BTCUSDT", iter_count=1)
    allowed, reason = ft.entry_allowed(repo, "BTCUSDT", iter_count=2)
    assert allowed is False
    assert "entry_cooldown" in reason


def test_max_consec_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SWARM_BYBIT_FT_STATE_JSON", str(tmp_path / "st.json"))
    monkeypatch.setenv("SWARM_BYBIT_ENTRY_COOLDOWN_SEC", "0")
    monkeypatch.setenv("SWARM_BYBIT_MAX_CONSEC_OPEN_FAILS", "2")
    repo = tmp_path
    ft.record_open_fail(repo, "BTCUSDT")
    ft.record_open_fail(repo, "BTCUSDT")
    ok, reason = ft.entry_allowed(repo, "BTCUSDT", iter_count=1)
    assert ok is False
    assert "max_consec_open_fails" in reason
    ft.record_open_success(repo, "BTCUSDT", iter_count=1)
    assert ft.entry_allowed(repo, "BTCUSDT", iter_count=2)[0] is True


def test_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SWARM_BYBIT_FT_STATE_JSON", str(tmp_path / "st.json"))
    monkeypatch.setenv("SWARM_BYBIT_FT_MECHANICS", "0")
    monkeypatch.setenv("SWARM_BYBIT_MAX_CONSEC_OPEN_FAILS", "1")
    repo = tmp_path
    ft.record_open_fail(repo, "BTCUSDT")
    assert ft.entry_allowed(repo, "BTCUSDT", iter_count=1)[0] is True
