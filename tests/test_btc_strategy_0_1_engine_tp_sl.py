"""Unit tests for BTC 0.1 engine TP/SL + entry_prediction helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

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
