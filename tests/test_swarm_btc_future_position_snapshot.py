"""swarm_knowledge.linear_position_snapshot_from_response"""
from __future__ import annotations

from finance_agent.swarm_knowledge import linear_position_snapshot_from_response


def test_snapshot_open_includes_tp_sl_fields() -> None:
    resp = {
        "retCode": 0,
        "result": {
            "list": [
                {
                    "symbol": "BTCUSDT",
                    "side": "Sell",
                    "size": "0.026",
                    "leverage": "50",
                    "avgPrice": "74532.1",
                    "markPrice": "74573.5",
                    "takeProfit": "",
                    "stopLoss": "",
                    "trailingStop": "0",
                    "unrealisedPnl": "-1.07",
                    "positionValue": "1937.83",
                    "tpslMode": "Full",
                }
            ]
        },
    }
    s = linear_position_snapshot_from_response(resp)
    assert s is not None
    assert s.get("open") is True
    assert s.get("takeProfit") == ""
    assert s.get("stopLoss") == ""
    assert s.get("side") == "Sell"


def test_snapshot_flat() -> None:
    resp = {"retCode": 0, "result": {"list": []}}
    s = linear_position_snapshot_from_response(resp)
    assert s == {"flat": True, "open": False}


def test_snapshot_error() -> None:
    assert linear_position_snapshot_from_response(None) is None
    assert linear_position_snapshot_from_response({"retCode": 10001}) is None
