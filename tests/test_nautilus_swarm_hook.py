"""Tests for prediction_agent/nautilus_swarm_hook.py."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

PA = Path(__file__).resolve().parents[1] / "prediction_agent"
sys.path.insert(0, str(PA))

import nautilus_swarm_hook as nsh  # noqa: E402


def test_hook_skipped_when_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("NAUTILUS_SWARM_HOOK", raising=False)
    monkeypatch.delenv("NAUTILUS_FUSION_SIDECAR_SYNC", raising=False)
    out = nsh.run_nautilus_swarm_hook(phase="training_feed", repo_root=tmp_path)
    assert out["skipped"] is True


def test_fusion_via_legacy_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data = tmp_path / "finance_agent" / "btc_specialist" / "data"
    data.mkdir(parents=True)
    (data / "nautilus_strategy_signal.json").write_text(
        json.dumps({"bias": "neutral", "generated_utc": "2026-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    pred_dir = tmp_path / "prediction_agent"
    pred_dir.mkdir(parents=True)
    (pred_dir / "btc_prediction_output.json").write_text(
        json.dumps({"generated_utc": "2026-01-01T01:00:00Z", "predictions": {"consensus": "MIXED"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("NAUTILUS_FUSION_SIDECAR_SYNC", "1")
    monkeypatch.delenv("NAUTILUS_SWARM_HOOK", raising=False)
    monkeypatch.setenv("NAUTILUS_BTC_OHLCV_DIR", str(data))
    monkeypatch.setenv("SYGNIF_NAUTILUS_FUSION_PATH", str(pred_dir / "fused.json"))

    out = nsh.run_nautilus_swarm_hook(phase="training_feed", repo_root=tmp_path)
    assert out["skipped"] is False
    assert out.get("fusion_ok") is True
    assert (pred_dir / "fused.json").is_file()


def test_bybit_demo_export_only_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYGNIF_BYBIT_DEMO_PREDICTED_MOVE_EXPORT", "1")
    monkeypatch.delenv("NAUTILUS_SWARM_HOOK", raising=False)
    monkeypatch.delenv("NAUTILUS_FUSION_SIDECAR_SYNC", raising=False)
    monkeypatch.setenv("SYGNIF_BYBIT_DEMO_SIGNAL_JSON", str(tmp_path / "sig.json"))
    monkeypatch.setenv("SYGNIF_BYBIT_DEMO_GOVERNANCE_MIN_PROB", "99")
    (tmp_path / "prediction_agent").mkdir(parents=True)
    # swarm_knowledge needs repo layout — use real ROOT for this branch
    from pathlib import Path as P

    root = P(__file__).resolve().parents[1]
    out = nsh.run_nautilus_swarm_hook(phase="manual", repo_root=root)
    assert out.get("skipped") is False
    assert out.get("bybit_demo_signal_ok") is True
    assert (tmp_path / "sig.json").is_file()


def test_swarm_hook_master_fusion(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data = tmp_path / "finance_agent" / "btc_specialist" / "data"
    data.mkdir(parents=True)
    (data / "nautilus_strategy_signal.json").write_text(
        json.dumps({"bias": "short", "generated_utc": "2026-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    pred_dir = tmp_path / "prediction_agent"
    pred_dir.mkdir(parents=True)
    (pred_dir / "btc_prediction_output.json").write_text(
        json.dumps({"generated_utc": "2026-01-01T01:00:00Z", "predictions": {"consensus": "BEARISH"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("NAUTILUS_SWARM_HOOK", "1")
    monkeypatch.delenv("NAUTILUS_FUSION_SIDECAR_SYNC", raising=False)
    monkeypatch.setenv("NAUTILUS_BTC_OHLCV_DIR", str(data))
    monkeypatch.setenv("SYGNIF_NAUTILUS_FUSION_PATH", str(pred_dir / "fused2.json"))

    out = nsh.run_nautilus_swarm_hook(phase="sidecar", repo_root=tmp_path)
    assert out["skipped"] is False
    assert out.get("fusion_ok") is True
