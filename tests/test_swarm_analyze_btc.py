"""scripts/swarm_analyze_btc.py — read-only loop helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parent.parent


def test_run_train_phase_invokes_scripts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prediction_agent").mkdir()
    (tmp_path / "prediction_agent" / "btc_predict_runner.py").write_text("#\n", encoding="utf-8")
    (tmp_path / "training_pipeline").mkdir()
    (tmp_path / "training_pipeline" / "channel_training.py").write_text("#\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(cmd, **kw):  # noqa: ANN001
        calls.append(list(cmd))
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        return m

    monkeypatch.setattr("subprocess.run", fake_run)
    sys.path.insert(0, str(REPO / "scripts"))
    import importlib.util

    spec = importlib.util.spec_from_file_location("swab", REPO / "scripts" / "swarm_analyze_btc.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    out = mod.run_train_phase(tmp_path, timeframe="1h")
    assert out["ok"] is True
    assert len(calls) == 2
    assert "btc_predict_runner.py" in calls[0][-3]
    assert "channel_training.py" in calls[1][-1]


def test_run_analyze_iteration_writes_row_keys() -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    sys.path.insert(0, str(REPO / "finance_agent"))
    import importlib.util

    import swarm_knowledge as skm  # noqa: E402

    spec = importlib.util.spec_from_file_location("swab2", REPO / "scripts" / "swarm_analyze_btc.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    fake_swarm = {
        "swarm_mean": 0.1,
        "swarm_label": "SWARM_MIXED",
        "swarm_conflict": False,
        "swarm_engine": "python",
        "swarm_engine_detail": "python_mean",
        "sources_n": 4,
        "missing_files": [],
        "sources": {"ml": {"vote": 1, "detail": "BULLISH"}},
    }

    with patch.object(skm, "compute_swarm", return_value=fake_swarm):
        pack = mod.run_analyze_iteration(REPO)
    assert pack["row"]["swarm_engine"] == "python"
    assert pack["row"]["sources_compact"]["ml"] == "BULLISH"
