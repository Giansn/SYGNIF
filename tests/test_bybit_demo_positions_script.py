"""scripts/bybit_demo_positions.py — mocked Bybit (no network)."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent


def _load_script():
    spec = importlib.util.spec_from_file_location("bybit_demo_positions", REPO / "scripts" / "bybit_demo_positions.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_run_json_with_stub_hedge_module() -> None:
    mod = _load_script()

    fake_pos = {
        "retCode": 0,
        "result": {
            "list": [
                {
                    "symbol": "BTCUSDT",
                    "side": "Buy",
                    "size": "0.002",
                    "positionIdx": 0,
                    "leverage": "3",
                    "unrealisedPnl": "1.2",
                    "avgPrice": "70000",
                }
            ]
        },
    }
    fake_orders = {"retCode": 0, "result": {"list": []}}

    stub = types.ModuleType("bybit_linear_hedge")

    def _get(path: str, params: dict) -> dict:
        assert path == "/v5/position/list"
        assert params.get("settleCoin") == "USDT"
        return fake_pos

    stub._get = _get
    stub.get_open_orders_realtime_linear = lambda sym: fake_orders  # noqa: ARG005

    sys.modules.pop("bybit_linear_hedge", None)
    with patch.object(mod, "load_env"):
        with patch.dict(sys.modules, {"bybit_linear_hedge": stub}):
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = mod.run(settle_coin="USDT", extra_order_symbols=[], as_json=True)
            assert rc == 0
            doc = json.loads(buf.getvalue())
            assert doc["ok"] is True
            assert doc["host"] == "api-demo.bybit.com"
            assert len(doc["positions_open"]) == 1
            assert doc["positions_open"][0]["symbol"] == "BTCUSDT"
