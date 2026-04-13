"""Unit tests for trade_overseer.bybit_linear_hedge (no live API)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "trade_overseer"))

import bybit_linear_hedge as blh  # noqa: E402


def test_switch_position_mode_rejects_empty_symbol():
    r = blh.switch_position_mode("", blh.MODE_HEDGE)
    assert r["retCode"] == -1


def test_create_market_order_rejects_bad_side():
    r = blh.create_market_order("BTCUSDT", "Long", "0.001", 1)
    assert r["retCode"] == -1


def test_create_market_order_rejects_bad_position_idx():
    r = blh.create_market_order("BTCUSDT", "Buy", "0.001", 5)
    assert r["retCode"] == -1


def test_cancel_all_open_orders_linear_rejects_empty_symbol():
    r = blh.cancel_all_open_orders_linear("")
    assert r["retCode"] == -1


def test_sign_post_deterministic(monkeypatch):
    monkeypatch.delenv("OVERSEER_BYBIT_HEDGE_MAINNET", raising=False)
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "k")
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "s")
    ts = "1700000000000"
    recv = "5000"
    body_str = '{"a":1}'
    sig = blh._sign_post("s", ts, "k", recv, body_str)
    assert len(sig) == 64
    assert sig == blh._sign_post("s", ts, "k", recv, body_str)


def test_credentials_mainnet_requires_live_ok(monkeypatch):
    monkeypatch.setenv("OVERSEER_BYBIT_HEDGE_MAINNET", "YES")
    monkeypatch.delenv("OVERSEER_HEDGE_LIVE_OK", raising=False)
    monkeypatch.setenv("BYBIT_API_KEY", "x")
    monkeypatch.setenv("BYBIT_API_SECRET", "y")
    with pytest.raises(RuntimeError, match="OVERSEER_HEDGE_LIVE_OK"):
        blh._credentials()
