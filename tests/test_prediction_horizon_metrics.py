"""Movement probability helpers in scripts/prediction_horizon_check.py"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prediction_horizon_check as ph  # noqa: E402


def test_bars_for_horizon():
    assert ph.bars_for_horizon(24, "60") == 24
    assert ph.bars_for_horizon(24, "240") == 6
    assert ph.bars_for_horizon(48, "240") == 12


def test_compute_movement_probability_metrics_symmetric_trend():
    # Strong uptrend: most forward returns positive
    x = np.linspace(100.0, 200.0, 500)
    m = ph.compute_movement_probability_metrics(x, bars_forward=6, atr_pct_bar=0.3)
    assert m["n_samples"] > 30
    assert m["p_up"] > 0.85
    assert m["p_down"] < 0.15


def test_compute_movement_probability_metrics_flat():
    x = np.full(400, 100.0) + np.random.default_rng(0).normal(0, 0.01, 400)
    m = ph.compute_movement_probability_metrics(x, bars_forward=3, atr_pct_bar=0.5)
    assert m["p_neutral_abs"] > 0.0
    assert "median_fwd_return_pct" in m
