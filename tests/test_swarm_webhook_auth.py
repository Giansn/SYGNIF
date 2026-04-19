"""Swarm HTTP webhook auth helpers (``finance_agent/bot.py``)."""

from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def bot_mod():
    fa = os.path.join(_REPO, "finance_agent")
    if _REPO not in sys.path:
        sys.path.insert(0, _REPO)
    if fa not in sys.path:
        sys.path.insert(0, fa)
    import bot  # noqa: PLC0415 — resolved via ``finance_agent/`` on ``sys.path`` (matches Docker http_main)

    return bot


class _Hdr(dict):
    def get(self, key, default=None):  # type: ignore[override]
        return super().get(key, default)


class _FakeHandler:
    __slots__ = ("headers",)

    def __init__(self, headers: dict[str, str]):
        self.headers = _Hdr(headers)


def test_swarm_webhook_token_unset(bot_mod, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYGNIF_SWARM_WEBHOOK_TOKEN", raising=False)
    h = _FakeHandler({"Authorization": "Bearer x"})
    ok, why = bot_mod._swarm_webhook_auth_ok(h)
    assert ok is False
    assert "unset" in why


def test_swarm_webhook_missing_header(bot_mod, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_WEBHOOK_TOKEN", "secret-token")
    h = _FakeHandler({})
    ok, why = bot_mod._swarm_webhook_auth_ok(h)
    assert ok is False
    assert "missing" in why


def test_swarm_webhook_bearer_ok(bot_mod, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_WEBHOOK_TOKEN", "secret-token")
    h = _FakeHandler({"Authorization": "Bearer secret-token"})
    ok, why = bot_mod._swarm_webhook_auth_ok(h)
    assert ok is True
    assert why == ""


def test_swarm_webhook_x_header_ok(bot_mod, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_WEBHOOK_TOKEN", "abc123")
    h = _FakeHandler({"X-Sygnif-Swarm-Token": "abc123"})
    ok, why = bot_mod._swarm_webhook_auth_ok(h)
    assert ok is True


def test_swarm_webhook_wrong_token(bot_mod, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_WEBHOOK_TOKEN", "expected")
    h = _FakeHandler({"Authorization": "Bearer wrong"})
    ok, why = bot_mod._swarm_webhook_auth_ok(h)
    assert ok is False
    assert "invalid" in why
