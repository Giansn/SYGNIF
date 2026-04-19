"""Unit tests for ``research/nautilus_lab/grid_sr_adaptive.py`` (S/R + ATR helpers for grid MM)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_LAB = Path(__file__).resolve().parents[1] / "research" / "nautilus_lab"
sys.path.insert(0, str(_LAB))

from grid_sr_adaptive import GridSrContext  # noqa: E402
from grid_sr_adaptive import adaptive_step_bps  # noqa: E402
from grid_sr_adaptive import bonus_sell_prices_near_resistance  # noqa: E402
from grid_sr_adaptive import compute_sr_context_from_lists  # noqa: E402
from grid_sr_adaptive import grid_center_with_sr  # noqa: E402
from grid_sr_adaptive import load_grid_sr_context  # noqa: E402
from grid_sr_adaptive import wide_mode_breakout  # noqa: E402


def _bars_uptrend(n: int) -> tuple[list[float], list[float], list[float]]:
    """Monotonic rise: lows/highs/closes 100..100+n-1."""
    lows = [100.0 + i for i in range(n)]
    highs = [lows[i] + 1.0 for i in range(n)]
    closes = [lows[i] + 0.5 for i in range(n)]
    return highs, lows, closes


def test_compute_sr_context_support_resistance_last_window() -> None:
    highs, lows, closes = _bars_uptrend(55)
    ctx = compute_sr_context_from_lists(highs, lows, closes, lookback=48)
    # Window highs[-49:-1] / lows[-49:-1] → bar indices 6..53 (48 bars, exclude last close).
    # highs[i]=101+i → max = 154; lows[i]=100+i → min = 106.
    assert ctx.support == pytest.approx(106.0)
    assert ctx.resistance == pytest.approx(154.0)
    assert ctx.close == pytest.approx(154.5)
    assert ctx.n_bars == 55
    assert ctx.atr_pct >= 0.0


def test_adaptive_step_bps_widens_with_atr() -> None:
    base = 18
    flat = GridSrContext(100.0, 110.0, 100.0, 0.5, 0.5, 100)
    volatile = GridSrContext(100.0, 110.0, 100.0, 2.0, 2.0, 100)
    s0 = adaptive_step_bps(base, flat, atr_k=18.0, cap_bps=80, floor_bps=10)
    s1 = adaptive_step_bps(base, volatile, atr_k=18.0, cap_bps=80, floor_bps=10)
    assert s0 <= s1
    assert s1 <= 80


def test_wide_mode_breakout_beyond_resistance() -> None:
    ctx = GridSrContext(90.0, 100.0, 95.0, 0.1, 0.1, 60)
    assert not wide_mode_breakout(99.0, ctx, breakout_bps=120, min_atr_pct=10.0)
    assert wide_mode_breakout(101.5, ctx, breakout_bps=120, min_atr_pct=10.0)


def test_grid_center_with_sr_inside_band() -> None:
    ctx = GridSrContext(100.0, 200.0, 150.0, 1.0, 0.5, 60)
    mid = 150.0
    c = grid_center_with_sr(mid, ctx, anchor_blend=1.0)
    assert c == pytest.approx(150.0)  # (100+200)/2


def test_bonus_sell_prices_below_resistance_above_mid() -> None:
    px = bonus_sell_prices_near_resistance(100.0, 110.0, extra_bps=50, max_extra=2)
    assert len(px) == 2
    assert all(100.0 < p < 110.0 for p in px)
    assert px[0] > px[1]  # further inside as k increases


def test_load_grid_sr_context_tmp_file(tmp_path: Path) -> None:
    rows = []
    for i in range(55):
        o = 100.0 + i
        rows.append({"t": i, "o": o, "h": o + 1, "l": o, "c": o + 0.5, "v": 1.0})
    p = tmp_path / "btc_1h_ohlcv.json"
    p.write_text(json.dumps(rows), encoding="utf-8")
    ctx = load_grid_sr_context(str(p), lookback=48)
    assert ctx is not None
    assert ctx.n_bars == 55


def test_load_grid_sr_context_missing_returns_none(tmp_path: Path) -> None:
    assert load_grid_sr_context(str(tmp_path / "nope.json")) is None
