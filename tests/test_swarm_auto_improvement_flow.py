"""scripts/swarm_auto_improvement_flow.py — state + history without live swarm I/O."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_swarm_auto_improvement_flow_writes_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scripts = REPO / "scripts"
    sys.path.insert(0, str(scripts))
    import swarm_auto_improvement_flow as saf  # noqa: E402

    monkeypatch.setenv("SYGNIF_PREDICTION_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("SYGNIF_SWARM_IMPROVEMENT_HISTORY_MAX", "50")
    monkeypatch.setenv("SYGNIF_SWARM_IMPROVEMENT_WEAK_POINTS", "0")

    fake_swarm = {
        "swarm_mean": 0.5,
        "swarm_label": "SWARM_BULL",
        "swarm_conflict": False,
        "sources_n": 4,
        "missing_files": [],
        "sources": {"ml": {"vote": 1, "detail": "BULLISH"}, "ta": {"vote": 1, "detail": "s60"}},
    }

    def _fake_compute():
        return fake_swarm

    sys.path.insert(0, str(REPO / "finance_agent"))
    import swarm_knowledge as sk  # noqa: E402

    monkeypatch.setattr(sk, "compute_swarm", _fake_compute)

    out = saf.run_once(as_json=False)
    assert out["ok"] is True
    st = tmp_path / "swarm_auto_improvement_state.json"
    assert st.is_file()
    body = json.loads(st.read_text(encoding="utf-8"))
    assert body["last_swarm"]["swarm_label"] == "SWARM_BULL"
    hist = tmp_path / "swarm_auto_improvement_history.jsonl"
    assert hist.is_file()
    line = hist.read_text(encoding="utf-8").strip().splitlines()[-1]
    rec = json.loads(line)
    assert rec["swarm_mean"] == 0.5
    assert "first_run_baseline_saved" in rec.get("hints", [])


def test_swarm_auto_improvement_weak_points_merged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scripts = REPO / "scripts"
    sys.path.insert(0, str(scripts))
    import swarm_auto_improvement_flow as saf  # noqa: E402

    monkeypatch.setenv("SYGNIF_PREDICTION_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("SYGNIF_SWARM_IMPROVEMENT_HISTORY_MAX", "50")
    monkeypatch.setenv("SYGNIF_SWARM_IMPROVEMENT_WEAK_POINTS", "1")

    fake_swarm = {
        "swarm_mean": 0.1,
        "swarm_label": "SWARM_MIXED",
        "swarm_conflict": True,
        "sources_n": 3,
        "missing_files": [],
        "sources": {"bf": {"vote": 0, "detail": "flat"}},
    }

    def _fake_compute():
        return fake_swarm

    sys.path.insert(0, str(REPO / "finance_agent"))
    import swarm_knowledge as sk  # noqa: E402

    monkeypatch.setattr(sk, "compute_swarm", _fake_compute)

    def _fake_bundle(r: Path):
        return {
            "generated_utc": "2026-01-02T00:00:00Z",
            "swarm_live": {"ok": True, "swarm_label": "SWARM_MIXED"},
            "predict_loop_dataset": {
                "ok": True,
                "gate_ok_rate": 0.42,
                "target_side_counts": {"long": 1, "short": 2},
                "top_block_reasons": [("bf_block", 3)],
                "last_row": {"iter": 9},
            },
            "closed_pnl": {"ok": True, "last_50_legs": {"n": 5, "wins": 2, "losses": 3, "sum": -1.0}},
            "recommendations": [{"id": "venue_churn", "severity": "high"}],
        }

    import swarm_weak_points_solution as sws  # noqa: E402

    monkeypatch.setattr(sws, "build_swarm_weak_points_bundle", _fake_bundle)

    import swarm_improvement_runtime as sir  # noqa: E402

    monkeypatch.setattr(sir, "write_weak_points_latest", lambda *_a, **_k: tmp_path / "wp_latest.json")
    monkeypatch.setattr(sir, "write_demo_runtime_hints", lambda *_a, **_k: tmp_path / "hints.json")

    saf.run_once(as_json=False)
    st = json.loads((tmp_path / "swarm_auto_improvement_state.json").read_text(encoding="utf-8"))
    assert st.get("weak_points", {}).get("gate_ok_rate") == 0.42
    hist = (tmp_path / "swarm_auto_improvement_history.jsonl").read_text(encoding="utf-8").strip().splitlines()
    last = json.loads(hist[-1])
    assert "weak_points" in last
