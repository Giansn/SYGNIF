"""notification_handler.py — POST /webhook/exchange auth + JSONL append."""

from __future__ import annotations

import json
import os
import sys
from io import BytesIO

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

import notification_handler as nh  # noqa: E402


class _Hdr(dict):
    def get(self, key, default=None):  # type: ignore[override]
        return super().get(key, default)


class _FakeExchangeHandler:
    __slots__ = ("headers", "client_address", "wfile", "_code")

    def __init__(self, headers: dict[str, str], *, client_address=("203.0.113.7", 12345)):
        self.headers = _Hdr(headers)
        self.client_address = client_address
        self.wfile = BytesIO()
        self._code: int | None = None

    def send_response(self, code, message=None):
        self._code = code

    def send_header(self, k, v):
        pass

    def end_headers(self):
        pass


def test_exchange_auth_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYGNIF_EXCHANGE_WEBHOOK_TOKEN", raising=False)
    h = _FakeExchangeHandler({"Authorization": "Bearer x"})
    ok, why = nh._exchange_webhook_auth_ok(h)
    assert ok is False
    assert "unset" in why


def test_exchange_auth_ok(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("SYGNIF_EXCHANGE_WEBHOOK_TOKEN", "tok-ex")
    monkeypatch.setenv("SYGNIF_PREDICTION_AGENT_DIR", str(tmp_path))
    h = _FakeExchangeHandler({"Authorization": "Bearer tok-ex"})
    nh._handle_exchange_webhook(h, b'{"exchange":"bybit","x":1}')
    assert h._code == 200
    jl = (tmp_path / "exchange_webhook_events.jsonl").read_text(encoding="utf-8").strip()
    row = json.loads(jl.splitlines()[-1])
    assert row["remote_ip"] == "203.0.113.7"
    assert row["payload"]["exchange"] == "bybit"


def test_exchange_x_forwarded_for(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("SYGNIF_EXCHANGE_WEBHOOK_TOKEN", "abc")
    monkeypatch.setenv("SYGNIF_PREDICTION_AGENT_DIR", str(tmp_path))
    h = _FakeExchangeHandler(
        {"X-Sygnif-Exchange-Webhook-Token": "abc", "X-Forwarded-For": "198.51.100.2, 10.0.0.1"},
    )
    nh._handle_exchange_webhook(h, b"{}")
    jl = (tmp_path / "exchange_webhook_events.jsonl").read_text(encoding="utf-8").strip()
    row = json.loads(jl.splitlines()[-1])
    assert row["remote_ip"] == "198.51.100.2"
