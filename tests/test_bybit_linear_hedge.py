"""Unit tests for trade_overseer.bybit_linear_hedge (no live API)."""
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "trade_overseer"))

import bybit_linear_hedge as blh  # noqa: E402

from tests.env_utils import delenv_strict


def test_switch_position_mode_rejects_empty_symbol():
    r = blh.switch_position_mode("", blh.MODE_HEDGE)
    assert r["retCode"] == -1


def test_create_market_order_rejects_bad_side():
    r = blh.create_market_order("BTCUSDT", "Long", "0.001", 1)
    assert r["retCode"] == -1


def test_create_market_order_rejects_bad_position_idx():
    r = blh.create_market_order("BTCUSDT", "Buy", "0.001", 5)
    assert r["retCode"] == -1


def test_create_limit_order_posts_body(monkeypatch):
    captured: dict = {}

    def fake_post(path: str, body: dict):
        captured["path"] = path
        captured["body"] = body
        return {"retCode": 0, "result": {}}

    monkeypatch.setattr(blh, "_post", fake_post)
    delenv_strict(monkeypatch, "OVERSEER_BYBIT_HEDGE_MAINNET")
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "k")
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "s")
    blh.create_limit_order(
        "BTCUSDT",
        "Buy",
        "0.001",
        1,
        "95000.1",
        time_in_force="PostOnly",
        order_link_id="sygPL000001OL",
    )
    assert captured["path"] == "/v5/order/create"
    b = captured["body"]
    assert b.get("orderType") == "Limit"
    assert b.get("timeInForce") == "PostOnly"
    assert b.get("price") == "95000.1"
    assert b.get("orderLinkId") == "sygPL000001OL"


def test_linear_mark_and_last_price_parses(monkeypatch):
    class Resp:
        def json(self):
            return {
                "retCode": 0,
                "result": {
                    "list": [{"markPrice": "100.5", "lastPrice": "100.6"}],
                },
            }

    def fake_get(url, timeout=15):
        assert "/v5/market/tickers" in url
        return Resp()

    monkeypatch.setattr(requests, "get", fake_get)
    delenv_strict(monkeypatch, "OVERSEER_BYBIT_HEDGE_MAINNET")
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "k")
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "s")
    mk, la = blh.linear_mark_and_last_price("BTCUSDT")
    assert mk == pytest.approx(100.5)
    assert la == pytest.approx(100.6)


def test_create_market_order_includes_order_link_id(monkeypatch):
    captured: dict = {}

    def fake_post(path: str, body: dict):
        captured["path"] = path
        captured["body"] = body
        return {"retCode": 0, "result": {}}

    monkeypatch.setattr(blh, "_post", fake_post)
    delenv_strict(monkeypatch, "OVERSEER_BYBIT_HEDGE_MAINNET")
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "k")
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "s")
    blh.create_market_order(
        "BTCUSDT",
        "Buy",
        "0.001",
        1,
        order_link_id="sygPL000001OL",
    )
    assert captured["body"].get("orderLinkId") == "sygPL000001OL"


def test_cancel_all_open_orders_linear_rejects_empty_symbol():
    r = blh.cancel_all_open_orders_linear("")
    assert r["retCode"] == -1


def test_set_trading_stop_linear_rejects_empty_symbol():
    r = blh.set_trading_stop_linear("", position_idx=0, take_profit="1")
    assert r["retCode"] == -1


def test_set_trading_stop_linear_rejects_bad_position_idx():
    r = blh.set_trading_stop_linear("BTCUSDT", position_idx=5, take_profit="1")
    assert r["retCode"] == -1


def test_set_trading_stop_linear_requires_at_least_one_field():
    r = blh.set_trading_stop_linear("BTCUSDT", position_idx=0)
    assert r["retCode"] == -1


def test_set_trading_stop_linear_posts_body(monkeypatch):
    captured: dict = {}

    def fake_post(path: str, body: dict):
        captured["path"] = path
        captured["body"] = body
        return {"retCode": 0, "result": {}}

    monkeypatch.setattr(blh, "_post", fake_post)
    delenv_strict(monkeypatch, "OVERSEER_BYBIT_HEDGE_MAINNET")
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "k")
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "s")
    blh.set_trading_stop_linear(
        "BTCUSDT",
        position_idx=2,
        take_profit="91000",
        stop_loss="94000",
        trailing_stop="120",
        tpsl_mode="Full",
    )
    assert captured["path"] == "/v5/position/trading-stop"
    b = captured["body"]
    assert b["symbol"] == "BTCUSDT"
    assert b["positionIdx"] == 2
    assert b["tpslMode"] == "Full"
    assert b["takeProfit"] == "91000"
    assert b["stopLoss"] == "94000"
    assert b["trailingStop"] == "120"
    assert b["tpTriggerBy"] == "MarkPrice"
    assert b["slTriggerBy"] == "MarkPrice"


def test_sign_post_deterministic(monkeypatch):
    delenv_strict(monkeypatch, "OVERSEER_BYBIT_HEDGE_MAINNET")
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
    delenv_strict(monkeypatch, "OVERSEER_HEDGE_LIVE_OK")
    monkeypatch.setenv("BYBIT_API_KEY", "x")
    monkeypatch.setenv("BYBIT_API_SECRET", "y")
    with pytest.raises(RuntimeError, match="OVERSEER_HEDGE_LIVE_OK"):
        blh._credentials()
