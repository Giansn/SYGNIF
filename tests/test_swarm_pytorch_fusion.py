"""Optional PyTorch swarm aggregation."""

from __future__ import annotations

from pathlib import Path

import pytest

pt = pytest.importorskip("torch", reason="torch not installed")

from finance_agent import swarm_pytorch_fusion as spf  # noqa: E402


def test_aggregate_matches_python_mean() -> None:
    votes = [1, 1, 1, -1]
    st = spf.aggregate_vote_stats(votes)
    assert st["mean"] == pytest.approx(0.5)
    assert st["conflict"] is True
    assert st["spread"] == 2
    assert st["label"] == "SWARM_BULL"


def test_weighted_mean_changes_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_PT_WEIGHTS", "0.1,0.1,0.1,5")
    votes = [1, 1, 1, -1]
    st = spf.aggregate_vote_stats(votes)
    assert st["mean"] < 0
    assert st["label"] == "SWARM_BEAR"
    assert st["engine_detail"] == "pytorch_weighted_mean"


def test_compute_swarm_pytorch_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import json

    from finance_agent import swarm_knowledge as sk

    monkeypatch.setenv("SYGNIF_SWARM_PYTORCH", "1")
    monkeypatch.delenv("SYGNIF_SWARM_PT_WEIGHTS", raising=False)
    for k in (
        "SYGNIF_SWARM_BYBIT_MAINNET",
        "SYGNIF_SWARM_BYBIT_ACCOUNT",
        "SYGNIF_SWARM_BYBIT_MODE",
        "SYGNIF_SWARM_BYBIT_ADMIN",
        "SYGNIF_SWARM_TRUTHCOIN_DC",
        "SYGNIF_SWARM_CORE_ENGINE",
        "SYGNIF_SWARM_HIVEMIND_VOTE",
        "SYGNIF_SWARM_FULL_ROOT_ACCESS",
    ):
        monkeypatch.delenv(k, raising=False)

    pred = {"predictions": {"consensus": "BULLISH"}}
    train = {"recognition": {"last_bar_probability_up_pct": 55.0, "last_bar_probability_down_pct": 45.0}}
    sidecar = {"bias": "neutral"}
    ta = {"ta_score": 50.0}
    (tmp_path / "p.json").write_text(json.dumps(pred), encoding="utf-8")
    (tmp_path / "t.json").write_text(json.dumps(train), encoding="utf-8")
    (tmp_path / "s.json").write_text(json.dumps(sidecar), encoding="utf-8")
    (tmp_path / "ta.json").write_text(json.dumps(ta), encoding="utf-8")

    out = sk.compute_swarm(
        pred_path=tmp_path / "p.json",
        train_path=tmp_path / "t.json",
        sidecar_path=tmp_path / "s.json",
        ta_path=tmp_path / "ta.json",
    )
    assert out["swarm_engine"] == "pytorch"
    assert out["swarm_engine_detail"] == "pytorch_mean"
