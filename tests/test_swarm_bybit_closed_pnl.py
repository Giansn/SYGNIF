"""finance_agent/swarm_knowledge.py — Bybit closed PnL attachment."""
from __future__ import annotations

import pytest

from finance_agent.swarm_knowledge import build_bybit_closed_pnl_report


def test_closed_pnl_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYGNIF_SWARM_BYBIT_CLOSED_PNL", raising=False)
    r = build_bybit_closed_pnl_report()
    assert r.get("enabled") is False


def test_closed_pnl_enabled_no_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_BYBIT_CLOSED_PNL", "1")
    monkeypatch.delenv("BYBIT_DEMO_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_DEMO_API_SECRET", raising=False)
    monkeypatch.delenv("BYBIT_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_API_SECRET", raising=False)
    monkeypatch.delenv("OVERSEER_BYBIT_HEDGE_MAINNET", raising=False)
    monkeypatch.delenv("OVERSEER_HEDGE_LIVE_OK", raising=False)
    r = build_bybit_closed_pnl_report()
    assert r.get("enabled") is True
    assert r.get("ok") is False
    assert r.get("detail") == "missing_bybit_credentials_for_venue"
