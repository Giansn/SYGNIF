"""finance_agent/swarm_knowledge.py — Bybit open (unrealised) PnL attachment."""
from __future__ import annotations

import pytest

from finance_agent.swarm_knowledge import build_bybit_open_pnl_report


def test_open_pnl_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_BYBIT_OPEN_PNL", "0")
    r = build_bybit_open_pnl_report(
        btc_future_meta={"enabled": False},
        account_meta={"enabled": False},
        resp_ac=None,
    )
    assert r.get("enabled") is False


def test_open_pnl_demo_flat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYGNIF_SWARM_BYBIT_OPEN_PNL", raising=False)
    r = build_bybit_open_pnl_report(
        btc_future_meta={
            "enabled": True,
            "ok": True,
            "symbol": "BTCUSDT",
            "position": {"flat": True, "open": False},
        },
        account_meta={"enabled": False},
        resp_ac=None,
    )
    assert r["venues"]["demo"]["unrealised_pnl_usdt"] == 0.0
    assert r["venues"]["demo"]["flat"] is True
    assert r["sum_unrealised_pnl_usdt"] == 0.0


def test_open_pnl_trade_venue_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYGNIF_SWARM_BYBIT_OPEN_PNL", raising=False)
    r = build_bybit_open_pnl_report(
        btc_future_meta={
            "enabled": True,
            "ok": True,
            "profile": "trade",
            "symbol": "BTCUSDT",
            "position": {"flat": True, "open": False},
        },
        account_meta={"enabled": False},
        resp_ac=None,
    )
    assert r["venues"]["trade"]["unrealised_pnl_usdt"] == 0.0
    assert r["venues"]["trade"]["flat"] is True


def test_open_pnl_fused_trade_ac_skips_mainnet_venue_sum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYGNIF_SWARM_BYBIT_OPEN_PNL", raising=False)
    r = build_bybit_open_pnl_report(
        btc_future_meta={
            "enabled": True,
            "ok": True,
            "profile": "trade",
            "symbol": "BTCUSDT",
            "position": {"flat": False, "open": True, "unrealisedPnl": "3.0"},
        },
        account_meta={
            "enabled": True,
            "ok": True,
            "fused_with_btc_future_trade": True,
            "symbol": "BTCUSDT",
        },
        resp_ac={"retCode": 0, "result": {"list": [{"size": "0.01", "side": "Buy", "unrealisedPnl": "3.0"}]}},
    )
    assert r["venues"]["trade"]["unrealised_pnl_usdt"] == 3.0
    assert r["venues"]["mainnet"].get("skipped") is True
    assert r["sum_unrealised_pnl_usdt"] == 3.0


def test_open_pnl_demo_and_mainnet_sum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYGNIF_SWARM_BYBIT_OPEN_PNL", raising=False)
    r = build_bybit_open_pnl_report(
        btc_future_meta={
            "enabled": True,
            "ok": True,
            "symbol": "BTCUSDT",
            "position": {"flat": False, "open": True, "unrealisedPnl": "10.5"},
        },
        account_meta={"enabled": True, "ok": True, "symbol": "BTCUSDT"},
        resp_ac={
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "Buy",
                        "size": "0.01",
                        "unrealisedPnl": "2.25",
                    }
                ]
            },
        },
    )
    assert r["venues"]["demo"]["unrealised_pnl_usdt"] == 10.5
    assert r["venues"]["mainnet"]["unrealised_pnl_usdt"] == 2.25
    assert r["sum_unrealised_pnl_usdt"] == pytest.approx(12.75)
