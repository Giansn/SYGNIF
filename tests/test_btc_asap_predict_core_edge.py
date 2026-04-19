"""btc_asap_predict_core: consensus edge + min-profit gate helpers."""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PA = ROOT / "prediction_agent"
sys.path.insert(0, str(PA))

import btc_asap_predict_core as core  # noqa: E402


def _sample_out() -> dict:
    return {
        "current_close": 100_000.0,
        "predictions": {
            "random_forest": {"next_mean": 100_200.0, "delta": 200.0},
            "xgboost": {"next_mean": 100_000.0, "delta": 0.0},
        },
    }


def test_consensus_mid_and_close_averages_rf_xg() -> None:
    mid, cl = core.consensus_mid_and_close(_sample_out())
    assert cl == 100_000.0
    assert mid == pytest.approx(100_100.0)


def test_modeled_edge_long_short() -> None:
    o = _sample_out()
    assert core.modeled_edge_usdt_per_btc(o, "long") == pytest.approx(100.0)
    assert core.modeled_profit_usdt_at_qty(o, "long", 0.026) == pytest.approx(2.6)
    assert core.relative_modeled_edge_pct(o, "long") == pytest.approx(0.1)
    assert core.modeled_edge_usdt_per_btc(o, "short") == 0.0
    o2 = {
        "current_close": 100_000.0,
        "predictions": {
            "random_forest": {"next_mean": 99_800.0},
            "xgboost": {"next_mean": 99_600.0},
        },
    }
    assert core.modeled_edge_usdt_per_btc(o2, "short") == pytest.approx(300.0)
    assert core.relative_modeled_edge_pct(o2, "short") == pytest.approx(0.3)
    assert core.modeled_profit_usdt_at_qty(o2, "short", 0.01) == pytest.approx(3.0)


def test_min_predict_edge_profit_usdt_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYGNIF_PREDICT_MIN_EDGE_PROFIT_USDT", raising=False)
    monkeypatch.delenv("SYGNIF_SWARM_TP_USDT_TARGET", raising=False)
    assert core.min_predict_edge_profit_usdt() == 0.0
    monkeypatch.setenv("SYGNIF_SWARM_TP_USDT_TARGET", "50")
    assert core.min_predict_edge_profit_usdt() == 50.0
    monkeypatch.setenv("SYGNIF_PREDICT_MIN_EDGE_PROFIT_USDT", "40")
    assert core.min_predict_edge_profit_usdt() == 40.0
    monkeypatch.setenv("SYGNIF_PREDICT_MIN_EDGE_PROFIT_USDT", "0")
    assert core.min_predict_edge_profit_usdt() == 0.0


def test_min_predict_edge_unset_ignores_tp_when_explicit_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_TP_USDT_TARGET", "50")
    monkeypatch.setenv("SYGNIF_PREDICT_MIN_EDGE_PROFIT_USDT", "0")
    assert core.min_predict_edge_profit_usdt() == 0.0


def test_open_modeled_edge_floor_adds_fee(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_PREDICT_MIN_EDGE_PROFIT_USDT", "10")
    monkeypatch.setenv("SYGNIF_PREDICT_PER_TRADE_COST_USDT", "1")
    monkeypatch.setenv("SYGNIF_PREDICT_EDGE_PLUS_FEE", "1")
    assert core.open_modeled_edge_floor_usdt() == pytest.approx(11.0)
    monkeypatch.setenv("SYGNIF_PREDICT_EDGE_PLUS_FEE", "0")
    assert core.open_modeled_edge_floor_usdt() == pytest.approx(10.0)


def test_effective_open_edge_floor_vol_relax(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_PREDICT_MIN_EDGE_PROFIT_USDT", "10")
    monkeypatch.setenv("SYGNIF_PREDICT_PER_TRADE_COST_USDT", "1")
    monkeypatch.setenv("SYGNIF_PREDICT_EDGE_PLUS_FEE", "1")
    monkeypatch.setenv("SYGNIF_PREDICT_EDGE_VOL_RELAX", "1")
    monkeypatch.setenv("SYGNIF_PREDICT_EDGE_VOL_REF_LO_PCT", "0.05")
    monkeypatch.setenv("SYGNIF_PREDICT_EDGE_VOL_REF_HI_PCT", "0.35")
    monkeypatch.setenv("SYGNIF_PREDICT_EDGE_VOL_RELAX_MAX", "0.5")
    monkeypatch.setenv("SYGNIF_PREDICT_EDGE_VOL_RELAX_MIN_FACTOR", "0.25")
    base = core.open_modeled_edge_floor_usdt()
    assert base == pytest.approx(11.0)
    assert core.effective_open_edge_floor_usdt(0.02) == pytest.approx(base)
    eff_hi = core.effective_open_edge_floor_usdt(0.35)
    assert eff_hi == pytest.approx(base * 0.5)
    monkeypatch.setenv("SYGNIF_PREDICT_EDGE_VOL_RELAX", "0")
    assert core.effective_open_edge_floor_usdt(0.35) == pytest.approx(base)


def test_decide_side_swing_failure_long(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_PREDICT_SWING_FAILURE_ENTRIES", "1")
    out = {
        "predictions": {
            "random_forest": {"delta": 0.0},
            "xgboost": {"delta": 0.0},
            "direction_logistic": {"label": "DOWN", "confidence": 40.0},
            "swing_failure": {"ok": True, "sf_long": True, "sf_short": False},
        }
    }
    side, why = core.decide_side(out, None)
    assert side == "long"
    assert "swing_failure" in why


def test_logreg_label_and_aligns_target() -> None:
    up = {
        "predictions": {"direction_logistic": {"label": "UP", "confidence": 72.5}},
    }
    down = {
        "predictions": {"direction_logistic": {"label": "DOWN", "confidence": 80.0}},
    }
    assert core.logreg_label(up) == "UP"
    assert core.logreg_confidence(up) == pytest.approx(72.5)
    assert core.logreg_aligns_target(up, "long") is True
    assert core.logreg_aligns_target(up, "short") is False
    assert core.logreg_aligns_target(down, "short") is True
    assert core.logreg_aligns_target(down, "long") is False
    assert core.logreg_aligns_target(up, None) is False
