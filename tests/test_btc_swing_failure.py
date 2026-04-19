"""Swing failure snapshot (48-bar S/R) for predict JSON."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
PA = ROOT / "prediction_agent"
sys.path.insert(0, str(PA))

import btc_swing_failure as sf  # noqa: E402


def test_swing_failure_rejects_short_df() -> None:
    df = pd.DataFrame({"High": [1.0], "Low": [1.0], "Close": [1.0]})
    snap = sf.swing_failure_snapshot(df)
    assert snap.get("ok") is False


def test_swing_failure_ok_shape() -> None:
    n = 120
    close = pd.Series(range(100, 100 + n), dtype=float)
    df = pd.DataFrame(
        {
            "High": close + 2.0,
            "Low": close - 2.0,
            "Close": close,
        }
    )
    snap = sf.swing_failure_snapshot(df)
    assert snap.get("ok") is True
    assert "sf_long" in snap and "sf_short" in snap
