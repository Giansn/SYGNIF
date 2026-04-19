"""finance_agent/swarm_order_gate.py"""
from __future__ import annotations

import os

import pytest

from finance_agent.swarm_order_gate import swarm_fusion_allows


def test_gate_no_edge() -> None:
    ok, r = swarm_fusion_allows(target=None, swarm={}, fusion_doc=None)
    assert ok is False
    assert r == "no_edge"


def test_gate_liquidation_tape_blocks_long(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "0")
    monkeypatch.setenv("SWARM_ORDER_ML_LOGREG_MIN_CONF", "0")
    monkeypatch.setenv("SWARM_ORDER_LIQUIDATION_TAPE_GATE", "1")
    swarm = {"swarm_mean": 0.5, "swarm_conflict": False, "swarm_label": "SWARM_BULL"}
    fusion = {
        "fusion": {"vote_nautilus": 0},
        "btc_prediction": {
            "predictions": {"direction_logistic": {"label": "UP", "confidence": 90.0}},
        },
        "liquidation_tape": {
            "ok": True,
            "tape_pressure_vote": -1,
            "tape_label": "long_liquidation_flush",
        },
    }
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc=fusion)
    assert ok is False
    assert "liq_tape_block_long" in r


def test_gate_long_mean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_ORDER_MIN_MEAN_LONG", "0.2")
    monkeypatch.delenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", raising=False)
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    swarm = {"swarm_mean": 0.1, "swarm_conflict": False, "swarm_label": "SWARM_MIXED"}
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc=None)
    assert ok is False
    assert "swarm_mean" in r


def test_gate_btc_future_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "1")
    swarm = {
        "swarm_mean": 0.5,
        "swarm_conflict": False,
        "swarm_label": "SWARM_BULL",
        "btc_future": {"enabled": True, "ok": False},
    }
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc=None)
    assert ok is False
    assert r == "btc_future_not_ok"


def test_gate_fusion_align(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "1")
    monkeypatch.setenv("SWARM_ORDER_FUSION_ALIGN_BTC_FUTURE", "0")
    swarm = {"swarm_mean": 0.1, "swarm_conflict": False, "swarm_label": "SWARM_BULL"}
    fusion = {"fusion": {"label": "neutral"}}
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc=fusion)
    assert ok is False
    assert "fusion_label" in r


def test_gate_btc_future_vote_align_short(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "1")
    monkeypatch.setenv("SWARM_ORDER_FUSION_ALIGN_LABEL", "0")
    monkeypatch.setenv("SWARM_ORDER_FUSION_ALIGN_BTC_FUTURE", "1")
    swarm = {"swarm_mean": 0.0, "swarm_conflict": False, "swarm_label": "SWARM_MIXED"}
    fusion = {"fusion": {"label": "lean_long", "vote_btc_future": 0}}
    ok, r = swarm_fusion_allows(target="short", swarm=swarm, fusion_doc=fusion)
    assert ok is False
    assert "btc_future_vote" in r


def test_gate_fusion_bf_flat_pass_fallback_to_vote_flat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "1")
    monkeypatch.setenv("SWARM_ORDER_FUSION_ALIGN_BTC_FUTURE", "1")
    monkeypatch.delenv("SWARM_ORDER_BTC_FUTURE_FLAT_PASS", raising=False)
    monkeypatch.setenv("SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS", "1")
    swarm = {"swarm_mean": 0.5, "swarm_conflict": False, "swarm_label": "SWARM_BULL"}
    fusion = {"fusion": {"label": "lean_long", "vote_btc_future": 0}}
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc=fusion)
    assert ok is True
    assert r == "ok"


def test_gate_fusion_bf_explicit_flat_off_disables_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "1")
    monkeypatch.setenv("SWARM_ORDER_FUSION_ALIGN_BTC_FUTURE", "1")
    monkeypatch.setenv("SWARM_ORDER_BTC_FUTURE_FLAT_PASS", "0")
    monkeypatch.setenv("SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS", "1")
    swarm = {"swarm_mean": 0.5, "swarm_conflict": False, "swarm_label": "SWARM_BULL"}
    fusion = {"fusion": {"label": "lean_long", "vote_btc_future": 0}}
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc=fusion)
    assert ok is False
    assert "btc_future_vote" in r


def test_gate_btc_future_vote_align_short_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "1")
    monkeypatch.setenv("SWARM_ORDER_FUSION_ALIGN_LABEL", "0")
    monkeypatch.setenv("SWARM_ORDER_FUSION_ALIGN_BTC_FUTURE", "1")
    swarm = {"swarm_mean": 0.0, "swarm_conflict": False, "swarm_label": "SWARM_MIXED"}
    fusion = {"fusion": {"label": "lean_long", "vote_btc_future": -1}}
    ok, r = swarm_fusion_allows(target="short", swarm=swarm, fusion_doc=fusion)
    assert ok is True


def test_gate_swarm_bf_vote_long(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE", "1")
    swarm = {
        "swarm_mean": 0.5,
        "swarm_conflict": False,
        "swarm_label": "SWARM_BULL",
        "sources": {"bf": {"vote": -1, "detail": "short"}},
    }
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc={})
    assert ok is False
    assert "swarm_bf_vote" in r


def test_gate_swarm_bf_vote_long_flat_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE", "1")
    monkeypatch.setenv("SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS", "1")
    swarm = {
        "swarm_mean": 0.5,
        "swarm_conflict": False,
        "swarm_label": "SWARM_BULL",
        "sources": {"bf": {"vote": 0, "detail": "flat"}},
    }
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc={})
    assert ok is True


def test_gate_btc_future_governance_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE_GOVERNANCE", "1")
    monkeypatch.delenv("SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE", raising=False)
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "0")
    swarm = {
        "swarm_mean": 0.5,
        "swarm_conflict": False,
        "swarm_label": "SWARM_BULL",
        "sources": {"bf": {"vote": -1, "detail": "short"}},
    }
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc={})
    assert ok is False
    assert "swarm_bf_vote" in r


def test_gate_btc_future_governance_explicit_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE_GOVERNANCE", "1")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "0")
    swarm = {
        "swarm_mean": 0.5,
        "swarm_conflict": False,
        "swarm_label": "SWARM_BULL",
        "sources": {"bf": {"vote": -1, "detail": "short"}},
    }
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc={})
    assert ok is True


def test_gate_nautilus_not_contrary_blocks_long(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY", "1")
    swarm = {"swarm_mean": 0.5, "swarm_conflict": False, "swarm_label": "SWARM_BULL"}
    fusion = {"fusion": {"vote_nautilus": -1, "vote_ml": 1, "label": "lean_long"}}
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc=fusion)
    assert ok is False
    assert "nautilus_contra_long" in r


def test_gate_nautilus_not_contrary_allows_neutral_long(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY", "1")
    swarm = {"swarm_mean": 0.5, "swarm_conflict": False, "swarm_label": "SWARM_BULL"}
    fusion = {"fusion": {"vote_nautilus": 0, "vote_ml": 1, "label": "lean_long"}}
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc=fusion)
    assert ok is True


def test_gate_nautilus_max_age(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "0")
    monkeypatch.setenv("SWARM_ORDER_NAUTILUS_MAX_AGE_MIN", "0.0001")
    swarm = {"swarm_mean": 0.5, "swarm_conflict": False, "swarm_label": "SWARM_BULL"}
    fusion = {
        "nautilus_sidecar": {"generated_utc": "1999-01-01T00:00:00Z"},
        "fusion": {"vote_nautilus": 0},
    }
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc=fusion)
    assert ok is False
    assert "nautilus_sidecar_stale" in r


def test_gate_fusion_require_strong(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "1")
    monkeypatch.setenv("SWARM_ORDER_FUSION_ALIGN_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_FUSION_REQUIRE_STRONG", "1")
    swarm = {"swarm_mean": 0.5, "swarm_conflict": False, "swarm_label": "SWARM_BULL"}
    fusion = {"fusion": {"label": "lean_long", "vote_nautilus": 0, "vote_ml": 1}}
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc=fusion)
    assert ok is False
    assert "strong_long" in r


def test_gate_ml_logreg_min_conf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "0")
    monkeypatch.setenv("SWARM_ORDER_ML_LOGREG_MIN_CONF", "90")
    swarm = {"swarm_mean": 0.5, "swarm_conflict": False, "swarm_label": "SWARM_BULL"}
    fusion = {
        "fusion": {"vote_nautilus": 0},
        "btc_prediction": {
            "predictions": {"direction_logistic": {"label": "UP", "confidence": 87.0}}
        },
    }
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc=fusion)
    assert ok is False
    assert "ml_logreg_conf" in r


def test_gate_usd_btc_macro_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "0")
    monkeypatch.delenv("SWARM_ORDER_USD_BTC_MACRO_GATE", raising=False)
    swarm = {"swarm_mean": 0.5, "swarm_conflict": False, "swarm_label": "SWARM_BULL"}
    fusion = {
        "usd_btc_macro": {
            "last_usd_index_return": 0.01,
            "pearson_correlation_daily_returns": {"pearson_last_20d": -0.9},
        },
    }
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc=fusion)
    assert ok is True


def test_gate_usd_btc_macro_blocks_long(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "0")
    monkeypatch.setenv("SWARM_ORDER_USD_BTC_MACRO_GATE", "1")
    monkeypatch.setenv("SWARM_ORDER_USD_INDEX_SURGE_RET", "0.001")
    monkeypatch.setenv("SWARM_ORDER_USD_INDEX_MIN_NEG_CORR", "-0.12")
    swarm = {"swarm_mean": 0.5, "swarm_conflict": False, "swarm_label": "SWARM_BULL"}
    fusion = {
        "usd_btc_macro": {
            "last_usd_index_return": 0.002,
            "pearson_correlation_daily_returns": {"pearson_last_20d": -0.2},
        },
    }
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc=fusion)
    assert ok is False
    assert "usd_btc_macro_block_long" in r
