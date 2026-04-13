"""Tests for prediction_agent/btc_analysis_order_signal.py."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prediction_agent"))
import btc_analysis_order_signal as sig  # noqa: E402


def test_r01_bearish_from_training():
    # Real ``training_channel_output`` embeds full runner JSON under ``predictions``.
    snap = {"predictions": {"consensus": "BEARISH"}}
    doc = {
        "recognition": {
            "last_bar_probability_down_pct": 99.0,
            "btc_predict_runner_snapshot": snap,
        }
    }
    assert sig.r01_bearish_from_training(doc) is True
    doc2 = {
        "recognition": {
            "last_bar_probability_down_pct": 50.0,
            "btc_predict_runner_snapshot": snap,
        }
    }
    assert sig.r01_bearish_from_training(doc2) is False


def test_r01_legacy_flat_consensus_on_snapshot():
    doc = {
        "recognition": {
            "last_bar_probability_down_pct": 99.0,
            "btc_predict_runner_snapshot": {"consensus": "BEARISH"},
        }
    }
    assert sig.r01_bearish_from_training(doc) is True


def test_decide_none_when_mixed_consensus():
    train = {"recognition": {"last_bar_probability_down_pct": 10.0, "btc_predict_runner_snapshot": {}}}
    pred = {"predictions": {"consensus": "MIXED"}}
    assert sig.decide_forceenter_intent(train, pred) is None


def test_decide_long_when_bullish_and_not_r01():
    train = {"recognition": {"last_bar_probability_down_pct": 10.0, "btc_predict_runner_snapshot": {}}}
    pred = {"predictions": {"consensus": "BULLISH"}}
    got = sig.decide_forceenter_intent(train, pred)
    assert got is not None
    assert got["side"] == "long"


def test_decide_none_when_bullish_but_r01():
    train = {
        "recognition": {
            "last_bar_probability_down_pct": 99.0,
            "btc_predict_runner_snapshot": {"predictions": {"consensus": "BEARISH"}},
        }
    }
    pred = {"predictions": {"consensus": "BULLISH"}}
    assert sig.decide_forceenter_intent(train, pred) is None


def test_decide_short_bearish_when_allowed():
    train = {}
    pred = {"predictions": {"consensus": "BEARISH"}}
    got = sig.decide_forceenter_intent(train, pred, allow_short=True)
    assert got is not None
    assert got["side"] == "short"


def test_decide_no_short_without_flag():
    train = {}
    pred = {"predictions": {"consensus": "BEARISH"}}
    assert sig.decide_forceenter_intent(train, pred, allow_short=False) is None
