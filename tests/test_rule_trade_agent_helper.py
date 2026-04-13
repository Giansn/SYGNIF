"""Unit tests for prediction_agent/rule_trade_agent_helper.py (BTC futures whitelist + trade filter)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prediction_agent"))
import rule_trade_agent_helper as h  # noqa: E402


def test_is_btc_futures_whitelist():
    assert h.is_btc_futures_whitelist("BTC/USDT:USDT")
    assert h.is_btc_futures_whitelist("btc/usdt:usdt")
    assert h.is_btc_futures_whitelist("BTC:USDT")
    assert not h.is_btc_futures_whitelist("ETH/USDT:USDT")
    assert not h.is_btc_futures_whitelist("BTC/USDT")


def test_filter_btc_futures_trades():
    payload = {
        "trades": [
            {"pair": "BTC/USDT:USDT", "id": 1},
            {"pair": "ETH/USDT:USDT", "id": 2},
            {"pair": "BTC:USDT", "id": 3},
        ]
    }
    got = h.filter_btc_futures_trades(payload)
    assert {t["id"] for t in got} == {1, 3}


def test_filter_btc_futures_trades_invalid_payload():
    assert h.filter_btc_futures_trades(None) == []
    assert h.filter_btc_futures_trades({}) == []
    assert h.filter_btc_futures_trades({"trades": "bad"}) == []
