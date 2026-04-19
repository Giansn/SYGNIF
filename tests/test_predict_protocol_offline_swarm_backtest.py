"""scripts/predict_protocol_offline_swarm_backtest.py helpers."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_fusion_label_from_sum() -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    import predict_protocol_offline_swarm_backtest as mod  # noqa: E402

    assert mod.fusion_label_from_sum(3) == "strong_long"
    assert mod.fusion_label_from_sum(-3) == "strong_short"
    assert mod.fusion_label_from_sum(0) == "neutral"


def test_build_offline_swarm_and_fusion_shape() -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    sys.path.insert(0, str(REPO / "prediction_agent"))
    import predict_protocol_offline_swarm_backtest as mod  # noqa: E402

    out = {
        "current_close": 70000.0,
        "predictions": {
            "consensus": "BULLISH",
            "random_forest": {"next_mean": 70100.0, "delta": 50.0},
            "xgboost": {"next_mean": 70100.0, "delta": 50.0},
        },
    }
    naut = {"bias": "long"}
    swarm, fusion = mod.build_offline_swarm_and_fusion(out, nautilus=naut, sim_bf_vote=0)
    assert "swarm_mean" in swarm
    assert fusion.get("btc_prediction") is out
    assert fusion.get("fusion", {}).get("vote_btc_future") == 0
    assert swarm.get("sources", {}).get("hm", {}).get("vote") == 0
    assert swarm.get("sources", {}).get("hm", {}).get("detail") == "offline_synth"

    swarm2, _ = mod.build_offline_swarm_and_fusion(
        out, nautilus=naut, sim_bf_vote=1, hm_vote=-1, hm_detail="bybit_demo_once:demo_short"
    )
    assert swarm2.get("sources", {}).get("hm", {}).get("detail") == "bybit_demo_once:demo_short"


def test_default_eval_bar_bounds_trailing() -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    import predict_protocol_offline_swarm_backtest as mod  # noqa: E402

    es, ee = mod.default_eval_bar_bounds(n=1000, hours=12)
    assert es == 1000 - 144  # ceil(12 * 12)
    assert ee == 999


def test_walk_forward_bar_slices_partitions() -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    import predict_protocol_offline_swarm_backtest as mod  # noqa: E402

    slices = mod.walk_forward_bar_slices(100, 200, folds=4)
    assert len(slices) == 4
    assert slices[0][0] == 100 and slices[-1][1] == 200
    for (a, b), (c, d) in zip(slices, slices[1:]):
        assert b == c
    with pytest.raises(ValueError):
        mod.walk_forward_bar_slices(0, 3, folds=5)


def test_normalize_sim_state() -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    import predict_protocol_offline_swarm_backtest as mod  # noqa: E402

    assert mod.normalize_sim_state(None) == {"pos": 0, "qty": 0.0, "entry_px": 0.0, "sim_bf": 0}
    assert mod.normalize_sim_state({}) == {"pos": 0, "qty": 0.0, "entry_px": 0.0, "sim_bf": 0}
    long_st = mod.normalize_sim_state({"pos": 1, "qty": 0.02, "entry_px": 70000.0, "sim_bf": 0})
    assert long_st["pos"] == 1 and long_st["sim_bf"] == 1
    short_st = mod.normalize_sim_state({"pos": -1, "qty": 0.01, "entry_px": 71000.0, "sim_bf": 0})
    assert short_st["pos"] == -1 and short_st["sim_bf"] == -1
