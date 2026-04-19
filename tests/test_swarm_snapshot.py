"""trade_overseer/swarm_snapshot.py — prompt line + optional ensure_entry gate."""

from __future__ import annotations

import json
import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TO = os.path.join(_REPO, "trade_overseer")
if _TO not in sys.path:
    sys.path.insert(0, _TO)

import swarm_snapshot as ss  # noqa: E402


def test_format_swarm_prompt_line() -> None:
    sk = {
        "generated_utc": "2026-04-16T12:00:00Z",
        "swarm_mean": 0.25,
        "swarm_label": "SWARM_MIXED",
        "swarm_conflict": True,
        "sources": {
            "ml": {"vote": 0, "detail": "MIXED"},
            "bf": {"vote": 1, "detail": "long"},
        },
        "btc_future": {"enabled": True, "position": {"flat": False, "side": "Buy", "size": "0.01"}},
        "open_trades": {"ok": True, "open_n": 1},
    }
    line = ss.format_swarm_prompt_line(sk)
    assert "SWARM|" in line
    assert "CONFLICT" in line
    assert "bf_pos=Buy" in line


def test_swarm_long_entry_gate_blocks_bear(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("SYGNIF_PREDICTION_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("OVERSEER_ENSURE_SWARM_GATE", "1")
    p = tmp_path / "swarm_knowledge_output.json"
    p.write_text(
        json.dumps({"swarm_mean": -0.1, "swarm_label": "SWARM_BEAR", "sources": {}}),
        encoding="utf-8",
    )
    ok, why = ss.swarm_long_entry_allowed()
    assert ok is False
    assert "bearish" in why


def test_swarm_long_entry_gate_off(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("SYGNIF_PREDICTION_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("OVERSEER_ENSURE_SWARM_GATE", "0")
    p = tmp_path / "swarm_knowledge_output.json"
    p.write_text(
        json.dumps({"swarm_mean": -0.9, "swarm_label": "SWARM_BEAR", "sources": {}}),
        encoding="utf-8",
    )
    ok, why = ss.swarm_long_entry_allowed()
    assert ok is True
