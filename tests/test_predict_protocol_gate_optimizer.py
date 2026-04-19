"""scripts/predict_protocol_gate_optimizer.py — search space helpers."""
from __future__ import annotations

import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_suggest_full_gate_trial_random_deterministic() -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    import predict_protocol_gate_optimizer as go  # noqa: E402

    rng = random.Random(123)
    a, ha, pa = go.suggest_full_gate_trial(rng=rng, trial=None, engine="random")
    rng = random.Random(123)
    b, hb, pb = go.suggest_full_gate_trial(rng=rng, trial=None, engine="random")
    assert a == b and ha == hb and pa == pb
    assert "SWARM_ORDER_MIN_MEAN_LONG" in a
    assert "SWARM_ORDER_REQUIRE_FUSION_ALIGN" in a


def test_bool_presets_have_core_keys() -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    import predict_protocol_gate_optimizer as go  # noqa: E402

    for name in ("relaxed", "mid", "strict"):
        p = go._bool_preset(name)
        assert p["SWARM_ORDER_REQUIRE_FUSION_ALIGN"] in ("0", "1")


def test_suggest_full_gate_trial_skips_hm_when_demo_source_mode() -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    import predict_protocol_gate_optimizer as go  # noqa: E402

    rng = random.Random(999)
    ge, hm, _pa = go.suggest_full_gate_trial(
        rng=rng, trial=None, engine="random", search_offline_hm_vote=False
    )
    assert hm == 0
    rng2 = random.Random(999)
    ge2, hm2, _pb = go.suggest_full_gate_trial(
        rng=rng2, trial=None, engine="random", search_offline_hm_vote=False
    )
    assert ge == ge2 and hm2 == 0
