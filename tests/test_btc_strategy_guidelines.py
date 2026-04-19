"""prediction_agent/btc_strategy_guidelines.py"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[1]
_PA = _ROOT / "prediction_agent"
if str(_PA) not in sys.path:
    sys.path.insert(0, str(_PA))


def _synth_df(n: int = 200) -> pd.DataFrame:
    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    dates = [t0 + timedelta(minutes=5 * i) for i in range(n)]
    rng = np.random.default_rng(0)
    close = 70000 + np.cumsum(rng.normal(0, 50, size=n))
    high = close + rng.uniform(20, 120, size=n)
    low = close - rng.uniform(20, 120, size=n)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    vol = rng.uniform(100, 500, size=n)
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": vol,
            "Mean": (high + low) / 2.0,
        }
    )


def test_ta_proxy_bounded() -> None:
    import btc_predict_runner
    import btc_strategy_guidelines as sg

    df = btc_predict_runner.add_ta_features(_synth_df(250))
    s = sg.ta_proxy_from_df(df)
    assert s is not None
    assert 0.0 <= float(s) <= 100.0


def test_compute_strategy_guidelines_shape() -> None:
    import btc_predict_runner
    import btc_strategy_guidelines as sg

    df = btc_predict_runner.add_ta_features(_synth_df(250))
    g = sg.compute_strategy_guidelines(df, linear_symbol="BTCUSDT")
    assert "sygnif_swing_long_ok" in g
    assert "sygnif_swing_short_ok" in g
    assert "orb_long_ok" in g
    assert "ta_proxy" in g
    assert g.get("pair") == "BTC/USDT"


def test_swarm_gate_guideline_long_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_agent.swarm_order_gate import swarm_fusion_allows

    monkeypatch.setenv("SWARM_ORDER_SYGNIF_STRATEGY_GUIDELINES", "1")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_HIVEMIND_VOTE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY", "0")
    monkeypatch.setenv("SWARM_ORDER_ML_LOGREG_MIN_CONF", "0")
    swarm = {"swarm_mean": 0.5, "swarm_conflict": False, "swarm_label": "SWARM_BULL"}
    pred = {
        "strategy_guidelines": {
            "ok": True,
            "sygnif_swing_long_ok": False,
            "orb_long_ok": False,
            "sygnif_swing_short_ok": False,
        }
    }
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc=None, predict_out=pred)
    assert ok is False
    assert "guideline_long" in r


def test_swarm_gate_guideline_long_pass_orb(monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_agent.swarm_order_gate import swarm_fusion_allows

    monkeypatch.setenv("SWARM_ORDER_SYGNIF_STRATEGY_GUIDELINES", "1")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_HIVEMIND_VOTE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY", "0")
    monkeypatch.setenv("SWARM_ORDER_ML_LOGREG_MIN_CONF", "0")
    swarm = {"swarm_mean": 0.5, "swarm_conflict": False, "swarm_label": "SWARM_BULL"}
    pred = {
        "strategy_guidelines": {
            "ok": True,
            "sygnif_swing_long_ok": False,
            "orb_long_ok": True,
            "sygnif_swing_short_ok": False,
        }
    }
    ok, r = swarm_fusion_allows(target="long", swarm=swarm, fusion_doc=None, predict_out=pred)
    assert ok is True
    assert r == "ok"


def test_swarm_gate_guideline_short_fusion_hivemind_markets(monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_agent.swarm_order_gate import swarm_fusion_allows

    monkeypatch.setenv("SWARM_ORDER_SYGNIF_STRATEGY_GUIDELINES", "1")
    monkeypatch.setenv("SWARM_ORDER_GUIDELINE_HIVEMIND_FUSION", "1")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_HIVEMIND_VOTE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY", "0")
    monkeypatch.setenv("SWARM_ORDER_ML_LOGREG_MIN_CONF", "0")
    swarm = {"swarm_mean": -0.5, "swarm_conflict": False, "swarm_label": "SWARM_BEAR"}
    pred = {
        "predictions": {
            "consensus_nautilus_enhanced": "STRONG_BEARISH",
            "hivemind": {
                "explore": {"ok": True, "markets_trading_n": 2, "slots_voting_n": 0},
                "vote": 0,
            },
        },
        "strategy_guidelines": {
            "ok": True,
            "sygnif_swing_long_ok": False,
            "orb_long_ok": False,
            "sygnif_swing_short_ok": False,
        },
    }
    ok, r = swarm_fusion_allows(target="short", swarm=swarm, fusion_doc=None, predict_out=pred)
    assert ok is True
    assert r == "guideline_fusion_short_hm_markets"


def test_swarm_gate_guideline_short_unreachable_ml_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_agent.swarm_order_gate import swarm_fusion_allows

    monkeypatch.setenv("SWARM_ORDER_SYGNIF_STRATEGY_GUIDELINES", "1")
    monkeypatch.setenv("SWARM_ORDER_GUIDELINE_HIVEMIND_FUSION", "1")
    monkeypatch.setenv("SWARM_ORDER_GUIDELINE_HIVEMIND_UNREACHABLE_ML", "1")
    monkeypatch.setenv("SWARM_ORDER_ML_LOGREG_MIN_CONF", "62")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_HIVEMIND_VOTE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY", "0")
    swarm = {"swarm_mean": -0.5, "swarm_conflict": False, "swarm_label": "SWARM_BEAR"}
    pred = {
        "predictions": {
            "consensus_nautilus_enhanced": "STRONG_BEARISH",
            "direction_logistic": {"label": "DOWN", "confidence": 70.0},
            "hivemind": {
                "explore": {"ok": False, "markets_trading_n": 0, "slots_voting_n": 0},
                "vote": 0,
            },
        },
        "strategy_guidelines": {
            "ok": True,
            "sygnif_swing_long_ok": False,
            "orb_long_ok": False,
            "sygnif_swing_short_ok": False,
        },
    }
    fusion_doc = {
        "btc_prediction": {
            "predictions": {
                "direction_logistic": {"label": "DOWN", "confidence": 70.0},
            }
        }
    }
    ok, r = swarm_fusion_allows(target="short", swarm=swarm, fusion_doc=fusion_doc, predict_out=pred)
    assert ok is True
    assert "unreachable_ml" in r


def test_swarm_gate_guideline_short_fusion_unreachable_still_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from finance_agent.swarm_order_gate import swarm_fusion_allows

    monkeypatch.setenv("SWARM_ORDER_SYGNIF_STRATEGY_GUIDELINES", "1")
    monkeypatch.setenv("SWARM_ORDER_GUIDELINE_HIVEMIND_FUSION", "1")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_BTC_FUTURE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_FUSION_ALIGN", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_HIVEMIND_VOTE", "0")
    monkeypatch.setenv("SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY", "0")
    monkeypatch.setenv("SWARM_ORDER_ML_LOGREG_MIN_CONF", "0")
    swarm = {"swarm_mean": -0.5, "swarm_conflict": False, "swarm_label": "SWARM_BEAR"}
    pred = {
        "predictions": {
            "consensus_nautilus_enhanced": "STRONG_BEARISH",
            "hivemind": {
                "explore": {"ok": False, "markets_trading_n": 2, "slots_voting_n": 0},
                "vote": 0,
            },
        },
        "strategy_guidelines": {
            "ok": True,
            "sygnif_swing_long_ok": False,
            "orb_long_ok": False,
            "sygnif_swing_short_ok": False,
        },
    }
    ok, r = swarm_fusion_allows(target="short", swarm=swarm, fusion_doc=None, predict_out=pred)
    assert ok is False
    assert "guideline_short" in r
