from __future__ import annotations

import pytest

from finance_agent.truthcoin_hivemind_swarm_core import (
    vote_hivemind_from_explore,
)


def test_vote_hivemind_active_slots() -> None:
    v, d = vote_hivemind_from_explore({"ok": True, "slots_voting_n": 2, "markets_trading_n": 1})
    assert v == 1
    assert "hivemind_active" in d


def test_vote_hivemind_unreachable() -> None:
    v, d = vote_hivemind_from_explore({"ok": False})
    assert v == 0
    assert "unreachable" in d


def test_vote_hivemind_unreachable_bybit_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_HIVEMIND_BYBIT_VOTE_FALLBACK", "1")
    doc = {
        "ok": False,
        "bybit_reference": {"price24hPcnt": 0.02},
    }
    v, d = vote_hivemind_from_explore(doc)
    assert v == 1
    assert "unreachable_bybit" in d


def test_vote_hivemind_quiet_bybit_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_HIVEMIND_BYBIT_VOTE_FALLBACK", "1")
    doc = {
        "ok": True,
        "slots_voting_n": 0,
        "markets_trading_n": 0,
        "bybit_reference": {"price24hPcnt": -0.02},
    }
    v, d = vote_hivemind_from_explore(doc)
    assert v == -1
    assert "truthcoin_quiet_bybit" in d


def test_vote_hivemind_bybit_fallback_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_HIVEMIND_BYBIT_VOTE_FALLBACK", "0")
    doc = {
        "ok": True,
        "slots_voting_n": 0,
        "markets_trading_n": 0,
        "bybit_reference": {"price24hPcnt": 0.05},
    }
    v, d = vote_hivemind_from_explore(doc)
    assert v == 0
    assert "quiet" in d


def test_gate_hivemind_vote_long(monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_agent.swarm_order_gate import swarm_fusion_allows

    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_HIVEMIND_VOTE", "1")
    monkeypatch.setenv("SWARM_ORDER_HIVEMIND_VOTE_FLAT_PASS", "0")
    swarm = {
        "swarm_mean": 0.5,
        "swarm_conflict": False,
        "swarm_label": "SWARM_BULL",
        "sources": {"hm": {"vote": 0, "detail": "quiet"}},
    }
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc={})
    assert ok is False
    assert "swarm_hm_vote" in r


def test_gate_hivemind_vote_long_flat_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_agent.swarm_order_gate import swarm_fusion_allows

    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_HIVEMIND_VOTE", "1")
    monkeypatch.setenv("SWARM_ORDER_HIVEMIND_VOTE_FLAT_PASS", "1")
    swarm = {
        "swarm_mean": 0.5,
        "swarm_conflict": False,
        "swarm_label": "SWARM_BULL",
        "sources": {"hm": {"vote": 0, "detail": "quiet"}},
    }
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc={})
    assert ok is True
