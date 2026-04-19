"""Hivemind fusion on live prediction consensus (runner helper)."""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PA = ROOT / "prediction_agent"
sys.path.insert(0, str(PA))

from btc_predict_runner import apply_hivemind_to_enhanced_consensus  # noqa: E402


def test_hivemind_no_vote_unchanged() -> None:
    e, m = apply_hivemind_to_enhanced_consensus(
        "BULLISH", "BULLISH", 3, True, 0, {"sidecar_bias": None}
    )
    assert e == "BULLISH"
    assert m["hivemind_vote"] == 0
    assert m["hivemind_prediction_note"] == "no_liveness_vote"


def test_hivemind_boost_bullish_to_strong() -> None:
    e, m = apply_hivemind_to_enhanced_consensus(
        "BULLISH", "BULLISH", 3, True, 1, {}
    )
    assert e == "STRONG_BULLISH"
    assert m["hivemind_prediction_note"] == "liveness_boost_strong_bullish"


def test_hivemind_does_not_boost_mixed_or_bearish() -> None:
    e, m = apply_hivemind_to_enhanced_consensus(
        "MIXED", "MIXED", 1, False, 1, {}
    )
    assert e == "MIXED"
    assert m["hivemind_prediction_note"] == "liveness_observed_no_label_change"


def test_hivemind_strong_bullish_unchanged() -> None:
    e, m = apply_hivemind_to_enhanced_consensus(
        "STRONG_BULLISH", "BULLISH", 3, True, 1, {}
    )
    assert e == "STRONG_BULLISH"
    assert m["hivemind_prediction_note"] == "liveness_confirms_strong_bullish"


def test_predict_hivemind_fusion_env(monkeypatch: pytest.MonkeyPatch) -> None:
    sys.path.insert(0, str(ROOT / "finance_agent"))
    import btc_predict_live as live  # noqa: E402

    monkeypatch.delenv("SYGNIF_PREDICT_HIVEMIND_FUSION", raising=False)
    monkeypatch.delenv("SYGNIF_SWARM_TRUTHCOIN_DC", raising=False)
    monkeypatch.delenv("SYGNIF_SWARM_HIVEMIND_VOTE", raising=False)
    monkeypatch.delenv("SYGNIF_SWARM_CORE_ENGINE", raising=False)
    assert live._predict_hivemind_fusion_enabled() is False
    monkeypatch.setenv("SYGNIF_SWARM_TRUTHCOIN_DC", "1")
    assert live._predict_hivemind_fusion_enabled() is True
    monkeypatch.setenv("SYGNIF_PREDICT_HIVEMIND_FUSION", "0")
    assert live._predict_hivemind_fusion_enabled() is False
