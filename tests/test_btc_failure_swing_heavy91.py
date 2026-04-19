"""btc_failure_swing_heavy91: Pine-style false break snapshot."""

import os
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
PA = ROOT / "prediction_agent"
sys.path.insert(0, str(PA))

import btc_failure_swing_heavy91 as fs  # noqa: E402


def _df_uptrend(n: int = 200) -> pd.DataFrame:
    """Rising closes so resistance is rarely broken downward."""
    rows = []
    base = 100_000.0
    for i in range(n):
        o = base + i * 5
        c = o + 3
        h = c + 2
        l = o - 1
        rows.append(
            {
                "Date": pd.Timestamp("2024-01-01T00:00:00Z") + pd.Timedelta(minutes=5 * i),
                "Open": o,
                "High": h,
                "Low": l,
                "Close": c,
            }
        )
    return pd.DataFrame(rows)


def test_failure_swing_returns_ok_shape() -> None:
    df = _df_uptrend(200)
    snap = fs.failure_swing_heavy91_snapshot(df)
    assert snap.get("ok") is True
    assert "entry_long" in snap
    assert "entry_short" in snap
    assert "volatility_pct" in snap


def test_false_break_support_triggers_long(monkeypatch) -> None:
    """Flat support for two bars + last bar wick below then close back above."""
    monkeypatch.setenv("SYGNIF_FS_HEAVY91_PERIOD", "8")
    monkeypatch.setenv("SYGNIF_FS_HEAVY91_EMA", "12")
    monkeypatch.setenv("SYGNIF_FS_HEAVY91_VOL_THRESHOLD", "0")
    n = 60
    rows = []
    for i in range(n - 1):
        rows.append(
            {
                "Date": pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(minutes=5 * i),
                "Open": 100.0,
                "High": 100.4,
                "Low": 100.0,
                "Close": 100.2,
            }
        )
    rows.append(
        {
            "Date": pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(minutes=5 * (n - 1)),
            "Open": 115.0,
            "High": 115.0,
            "Low": 99.5,
            "Close": 100.3,
        }
    )
    df = pd.DataFrame(rows)
    snap = fs.failure_swing_heavy91_snapshot(df)
    assert snap.get("ok") is True
    assert snap.get("entry_long") is True
