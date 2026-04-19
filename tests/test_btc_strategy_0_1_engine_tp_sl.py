"""Unit tests for BTC 0.1 engine TP/SL + entry_prediction helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "user_data" / "strategies"))

import btc_strategy_0_1_engine as b01  # noqa: E402


def test_entry_prediction_blocks_legacy_tags():
    assert b01.entry_prediction_blocks_long_under_bearish("orb_long") is True
    assert b01.entry_prediction_blocks_long_under_bearish("strong_ta") is True
    assert b01.entry_prediction_blocks_long_under_bearish("BTC-0.1-R01") is True
    assert b01.entry_prediction_blocks_long_under_bearish("sygnif_s2") is True
    assert b01.entry_prediction_blocks_long_under_bearish("sygnif_short_s1") is False


def test_entry_prediction_r02_when_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        b01,
        "_registry_raw",
        lambda: {"tuning": {"entry_prediction": {"enabled": True}}},
    )
    assert b01.entry_prediction_blocks_long_under_bearish("BTC-0.1-R02") is True


def test_entry_prediction_r02_when_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        b01,
        "_registry_raw",
        lambda: {"tuning": {"entry_prediction": {"enabled": False}}},
    )
    assert b01.entry_prediction_blocks_long_under_bearish("BTC-0.1-R02") is False


def test_tag_sl_return_cap_futures_long(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        b01,
        "_registry_raw",
        lambda: {
            "tuning": {
                "tp_sl": {"BTC-0.1-R01": {"sl_doom": 0.06}},
            }
        },
    )
    tr = SimpleNamespace(is_short=False, leverage=5.0)
    assert b01.tag_sl_return_cap(tr, "BTC-0.1-R01", is_futures=True) == pytest.approx(-0.012)


def test_tag_sl_return_cap_short_none(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        b01,
        "_registry_raw",
        lambda: {"tuning": {"tp_sl": {"BTC-0.1-R01": {"sl_doom": 0.06}}}},
    )
    tr = SimpleNamespace(is_short=True, leverage=5.0)
    assert b01.tag_sl_return_cap(tr, "BTC-0.1-R01", is_futures=True) is None


def test_tag_takeprofit_r01_r03(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        b01,
        "_registry_raw",
        lambda: {
            "tuning": {
                "tp_sl": {
                    "BTC-0.1-R01": {"tp_profit_pct": 0.03},
                    "BTC-0.1-R03": {"tp_profit_pct": None},
                },
                "r03_scalp": {"tp_profit_pct": 0.01},
            }
        },
    )
    assert b01.tag_takeprofit_profit_pct("BTC-0.1-R01") == pytest.approx(0.03)
    assert b01.tag_takeprofit_profit_pct("BTC-0.1-R03") == pytest.approx(0.01)


def test_swarm_adverse_bear_label(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(b01, "_registry_raw", lambda: {})
    row = pd.Series({"swarm_label": "SWARM_BEAR", "swarm_mean": 0.0, "swarm_conflict": False})
    assert b01.swarm_adverse_to_long(row) is True


def test_swarm_adverse_negative_mean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(b01, "_registry_raw", lambda: {})
    row = pd.Series({"swarm_label": "MIXED", "swarm_mean": -0.25, "swarm_conflict": False})
    assert b01.swarm_adverse_to_long(row) is True


def test_swarm_adverse_conflict_weak_mean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(b01, "_registry_raw", lambda: {})
    row = pd.Series({"swarm_label": "X", "swarm_mean": 0.0, "swarm_conflict": True})
    assert b01.swarm_adverse_to_long(row) is True


def test_swarm_not_adverse_bull(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(b01, "_registry_raw", lambda: {})
    row = pd.Series({"swarm_label": "SWARM_BULL", "swarm_mean": 0.2, "swarm_conflict": False})
    assert b01.swarm_adverse_to_long(row) is False


def test_swarm_trail_disabled_in_registry(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        b01,
        "_registry_raw",
        lambda: {"tuning": {"swarm_trail_tp": {"enabled": False}}},
    )
    row = pd.Series({"swarm_label": "SWARM_BEAR", "swarm_mean": -1.0, "swarm_conflict": False})
    assert b01.swarm_adverse_to_long(row) is False


def test_swarm_trail_callback_from_registry(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        b01,
        "_registry_raw",
        lambda: {"tuning": {"swarm_trail_tp": {"callback_pct": 0.01}}},
    )
    assert b01.swarm_trail_callback_pct() == pytest.approx(0.01)


def test_swarm_trail_min_profit_gate(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(b01, "_registry_raw", lambda: {})
    assert b01.swarm_trail_min_profit_gate(1.0) == pytest.approx(0.008)
    assert b01.swarm_trail_min_profit_gate(3.0) == pytest.approx(0.024)
