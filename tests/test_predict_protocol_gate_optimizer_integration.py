"""Optional slow smoke: gate optimizer + Bybit klines + fit_predict (set SYGNIF_GATE_OPT_SMOKE=1)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_gate_optimizer_random_one_trial_smoke() -> None:
    if os.environ.get("SYGNIF_GATE_OPT_SMOKE") != "1":
        pytest.skip("set SYGNIF_GATE_OPT_SMOKE=1 to run (network + ML)")

    script = REPO / "scripts" / "predict_protocol_gate_optimizer.py"
    r = subprocess.run(
        [
            sys.executable,
            str(script),
            "--engine",
            "random",
            "--trials",
            "1",
            "--hours",
            "8",
            "--step",
            "12",
            "--seed",
            "7",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert r.returncode == 0, r.stdout + "\n" + r.stderr
    assert "best_pnl_usdt_approx" in r.stdout
