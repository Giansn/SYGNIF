"""Tests for ``swarm_weak_points_solution`` (no live Bybit when creds missing)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_analyze_predict_dataset_tail(tmp_path: Path) -> None:
    from finance_agent import swarm_weak_points_solution as sws

    pa = tmp_path / "prediction_agent"
    pa.mkdir()
    lines = []
    for i in range(5):
        lines.append(
            json.dumps(
                {
                    "predict_protocol_line": {
                        "iter": i,
                        "ts_utc": f"2026-01-0{i+1}T00:00:00Z",
                        "target_side": "short" if i % 2 == 0 else "long",
                        "swarm_gate_ok": i % 3 != 0,
                        "swarm_reason": "ok" if i % 3 != 0 else "nautilus_contra_short vote_nautilus=1",
                    }
                }
            )
        )
    (pa / "swarm_predict_protocol_dataset.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = sws.analyze_predict_dataset_tail(tmp_path, max_lines=10)
    assert out["ok"] is True
    assert out["rows"] == 5
    assert out["swarm_gate_ok_false"] >= 1


def test_build_recommendations_hivemind() -> None:
    from finance_agent import swarm_weak_points_solution as sws

    recs = sws.build_recommendations(
        swarm_live={
            "ok": True,
            "votes": {"hm": {"vote": 0, "detail": "hivemind_unreachable"}, "bf": {"vote": 0}},
        },
        swarm_file={"ok": True},
        cpnl={"ok": True, "last_50_legs": {"n": 50, "wins": 5, "losses": 45, "sum": -100.0}},
        dataset={
            "ok": True,
            "gate_ok_rate": 0.3,
            "top_block_reasons": [("nautilus_contra_short vote_nautilus=1", 40), ("swarm_bf_vote=1_need_short_or_flat_ok", 35)],
        },
        ft_state={"ok": True, "state": {"symbols": {"BTCUSDT": {"consec_open_fails": 0}}}},
    )
    ids = {r["id"] for r in recs}
    assert "hivemind_unreachable" in ids
    assert "nautilus_model_tension" in ids
    assert "venue_churn" in ids
