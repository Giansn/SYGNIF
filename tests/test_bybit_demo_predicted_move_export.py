"""Tests for prediction_agent/bybit_demo_predicted_move_export.py."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PA = ROOT / "prediction_agent"
sys.path.insert(0, str(PA))

import bybit_demo_predicted_move_export as exp  # noqa: E402


def test_governance_probability_prefers_channel() -> None:
    train = {"recognition": {"last_bar_probability_up_pct": 80.0, "last_bar_probability_down_pct": 20.0}}
    pred = {"predictions": {"direction_logistic": {"confidence": 50.0}}}
    p, src = exp.governance_probability_pct(train, pred)
    assert p == 80.0
    assert "training" in src


def test_signal_inactive_below_min_prob(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_BYBIT_DEMO_GOVERNANCE_MIN_PROB", "75")
    swarm = {
        "swarm_mean": 0.5,
        "swarm_label": "SWARM_BULL",
        "swarm_conflict": False,
        "sources_n": 3,
    }
    train = {"recognition": {"last_bar_probability_up_pct": 50.0, "last_bar_probability_down_pct": 30.0}}
    pred = {"predictions": {"consensus": "BULLISH"}}
    payload = exp.build_signal_payload(swarm=swarm, training=train, pred=pred)
    assert payload["signal_active"] is False
    assert payload["governance"]["passed"] is False


def test_signal_active_when_swarm_bull_and_prob_high(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_BYBIT_DEMO_GOVERNANCE_MIN_PROB", "75")
    swarm = {
        "swarm_mean": 0.6,
        "swarm_label": "SWARM_BULL",
        "swarm_conflict": False,
        "sources_n": 4,
    }
    train = {"recognition": {"last_bar_probability_up_pct": 76.0, "last_bar_probability_down_pct": 24.0}}
    pred = {}
    payload = exp.build_signal_payload(swarm=swarm, training=train, pred=pred)
    assert payload["predicted_move"] == "up"
    assert payload["signal_active"] is True
    assert payload["consumer_tag"] == "bybitapidemo"
    assert payload["venue"] == "bybit_api_demo"


def test_flat_swarm_blocks_even_with_high_prob(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_BYBIT_DEMO_GOVERNANCE_MIN_PROB", "75")
    swarm = {
        "swarm_mean": 0.0,
        "swarm_label": "SWARM_MIXED",
        "swarm_conflict": False,
        "sources_n": 4,
    }
    train = {"recognition": {"last_bar_probability_up_pct": 99.0, "last_bar_probability_down_pct": 1.0}}
    payload = exp.build_signal_payload(swarm=swarm, training=train, pred={})
    assert payload["predicted_move"] == "flat"
    assert payload["signal_active"] is False
