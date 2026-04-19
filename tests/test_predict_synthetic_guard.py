"""predict_synthetic_guard: block reasons from 24h + swarm card JSON."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PA = os.path.join(_ROOT, "prediction_agent")
if _PA not in sys.path:
    sys.path.insert(0, _PA)

from predict_synthetic_guard import evaluate_synthetic_entry_block
from swarm_btc_flow_constants import K_ORDER_SIGNAL
from swarm_btc_flow_constants import K_SIDE


def test_no_files_fail_open(tmp_path: Path) -> None:
    blk, reason = evaluate_synthetic_entry_block(tmp_path)
    assert blk is False
    assert reason == ""


def test_neutral_24h_blocks(tmp_path: Path) -> None:
    pa = tmp_path / "prediction_agent"
    pa.mkdir(parents=True)
    (pa / "btc_24h_movement_prediction.json").write_text(
        json.dumps({"synthesis": {"bias_24h": "NEUTRAL"}}),
        encoding="utf-8",
    )
    blk, reason = evaluate_synthetic_entry_block(tmp_path)
    assert blk is True
    assert "24h_NEUTRAL" in reason


def test_swarm_hold_flat_blocks(tmp_path: Path) -> None:
    pa = tmp_path / "prediction_agent"
    pa.mkdir(parents=True)
    (pa / "swarm_btc_synth.json").write_text(
        json.dumps({K_ORDER_SIGNAL: "HOLD", K_SIDE: "FLAT"}),
        encoding="utf-8",
    )
    blk, reason = evaluate_synthetic_entry_block(tmp_path)
    assert blk is True
    assert "swarm_card_HOLD_FLAT" in reason
