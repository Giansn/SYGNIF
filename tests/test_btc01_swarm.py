"""btc_strategy_0_1_engine swarm helpers."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest


def test_attach_swarm_columns_broadcasts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_PREDICTION_AGENT_DIR", str(tmp_path))
    p = tmp_path / "swarm_knowledge_output.json"
    p.write_text(
        json.dumps(
            {
                "swarm_mean": 0.25,
                "swarm_label": "SWARM_BULL",
                "swarm_conflict": False,
            }
        ),
        encoding="utf-8",
    )
    import btc_strategy_0_1_engine as b01

    b01._swarm_snap_mtime = -1.0
    b01._swarm_snap_doc = {}
    df = pd.DataFrame({"close": [1.0, 2.0]})
    b01.attach_swarm_columns(df)
    assert float(df["swarm_mean"].iloc[-1]) == 0.25
    assert df["swarm_label"].iloc[-1] == "SWARM_BULL"
    assert bool(df["swarm_conflict"].iloc[-1]) is False


def test_swarm_root_blocks_long(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYGNIF_PREDICTION_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("SYGNIF_BTC01_SWARM_ROOT", "1")
    p = tmp_path / "swarm_knowledge_output.json"
    p.write_text(
        json.dumps(
            {
                "swarm_mean": -0.5,
                "swarm_label": "SWARM_BEAR",
                "swarm_conflict": True,
            }
        ),
        encoding="utf-8",
    )
    import btc_strategy_0_1_engine as b01

    b01._swarm_snap_mtime = -1.0
    b01._swarm_snap_doc = {}
    assert b01.swarm_root_blocks_long() is True


def test_swarm_root_disabled_without_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SYGNIF_BTC01_SWARM_ROOT", raising=False)
    monkeypatch.setenv("SYGNIF_PREDICTION_AGENT_DIR", str(tmp_path))
    (tmp_path / "swarm_knowledge_output.json").write_text(
        json.dumps({"swarm_mean": -1.0, "swarm_label": "SWARM_BEAR",
                    "swarm_conflict": True}),
        encoding="utf-8",
    )
    import btc_strategy_0_1_engine as b01

    b01._swarm_snap_mtime = -1.0
    b01._swarm_snap_doc = {}
    assert b01.swarm_root_blocks_long() is False
