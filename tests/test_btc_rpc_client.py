"""finance_agent/btc_rpc_client.py (Bitcoin Core JSON-RPC)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
FA = ROOT / "finance_agent"
sys.path.insert(0, str(FA))

import btc_rpc_client as brc  # noqa: E402


class _FakeResp:
    def __init__(self, body: str) -> None:
        self._b = body.encode("utf-8")

    def read(self) -> bytes:
        return self._b

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *a: object) -> None:
        return None


def test_bitcoin_rpc_call_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BITCOIN_RPC_URL", "http://127.0.0.1:18444")
    monkeypatch.setenv("BITCOIN_RPC_USER", "u")
    monkeypatch.setenv("BITCOIN_RPC_PASSWORD", "p")

    payload = json.dumps({"result": {"blocks": 123}, "error": None, "id": "x"})

    def fake_urlopen(req, timeout=60):  # noqa: ANN001
        assert b"getblockcount" in req.data
        return _FakeResp(payload)

    monkeypatch.setenv("BITCOIN_RPC_WALLET", "")
    with patch("urllib.request.urlopen", fake_urlopen):
        r = brc.bitcoin_rpc_call("getblockcount", [])
    assert r == {"blocks": 123}


def test_bitcoin_rpc_call_rpc_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BITCOIN_RPC_URL", "http://127.0.0.1:18444")
    monkeypatch.setenv("BITCOIN_RPC_USER", "u")
    monkeypatch.setenv("BITCOIN_RPC_PASSWORD", "p")
    payload = json.dumps({"result": None, "error": {"code": -5, "message": "bad"}, "id": "x"})

    def fake_urlopen(req, timeout=60):  # noqa: ANN001
        return _FakeResp(payload)

    with patch("urllib.request.urlopen", fake_urlopen):
        with pytest.raises(brc.BitcoinRpcError, match="RPC error"):
            brc.bitcoin_rpc_call("invalid", [])


def test_cookie_file_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ck = tmp_path / ".cookie"
    ck.write_text("user_from_cookie:secret_pw\n", encoding="utf-8")
    monkeypatch.setenv("BITCOIN_RPC_URL", "http://127.0.0.1:8332")
    monkeypatch.setenv("BITCOIN_RPC_COOKIE_FILE", str(ck))
    monkeypatch.delenv("BITCOIN_RPC_USER", raising=False)
    monkeypatch.delenv("BITCOIN_RPC_PASSWORD", raising=False)

    payload = json.dumps({"result": 99, "error": None, "id": "x"})

    def fake_urlopen(req, timeout=60):  # noqa: ANN001
        return _FakeResp(payload)

    with patch("urllib.request.urlopen", fake_urlopen):
        assert brc.bitcoin_rpc_call("getblockcount", []) == 99
