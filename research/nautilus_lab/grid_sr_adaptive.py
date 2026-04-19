"""
Support / resistance + ATR context for adaptive grid spacing (Nautilus GridMarketMaker).

Reads the same **Bybit 1h OHLCV JSON** as ``btc_predict_runner`` / ``btc_specialist``:
``[{"t","o","h","l","c","v"}, ...]``.

S/R matches **Sygnif swing-failure style** (see ``finance_agent/bot.py`` ``calc_indicators``):
``resistance = rolling_max(high.shift(1), 48)``, ``support = rolling_min(low.shift(1), 48)``
evaluated on the **last closed bar** (no pandas dependency — pure Python).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GridSrContext:
    support: float | None
    resistance: float | None
    close: float
    atr: float
    atr_pct: float  # ATR / close * 100
    n_bars: int


def _atr14(highs: list[float], lows: list[float], closes: list[float]) -> float:
    if len(closes) < 2:
        return 0.0
    trs: list[float] = []
    for i in range(1, len(closes)):
        h, l_, c_prev = highs[i], lows[i], closes[i - 1]
        trs.append(max(h - l_, abs(h - c_prev), abs(l_ - c_prev)))
    if not trs:
        return 0.0
    tail = trs[-14:]
    return sum(tail) / len(tail)


def _sf_levels(highs: list[float], lows: list[float], lookback: int) -> tuple[float | None, float | None]:
    """
    Last-bar SF support/resistance: support = min(low[:-1][-lookback:]),
    resistance = max(high[:-1][-lookback:]). Mirrors shift(1).rolling(48) at the final index.
    """
    if len(highs) < lookback + 2 or len(lows) < lookback + 2:
        return None, None
    h_win = highs[-(lookback + 1) : -1]
    l_win = lows[-(lookback + 1) : -1]
    if not h_win or not l_win:
        return None, None
    return min(l_win), max(h_win)


def compute_sr_context_from_lists(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    lookback: int = 48,
) -> GridSrContext:
    if not closes:
        return GridSrContext(None, None, 0.0, 0.0, 0.0, 0)
    last = float(closes[-1])
    sup, res = _sf_levels(highs, lows, lookback)
    atr = _atr14(highs, lows, closes)
    atr_pct = (atr / last * 100.0) if last > 0 else 0.0
    return GridSrContext(
        support=float(sup) if sup is not None else None,
        resistance=float(res) if res is not None else None,
        close=last,
        atr=atr,
        atr_pct=atr_pct,
        n_bars=len(closes),
    )


def load_grid_sr_context(path: str | Path, *, lookback: int = 48) -> GridSrContext | None:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, list) or not raw:
        return None
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            highs.append(float(row["h"]))
            lows.append(float(row["l"]))
            closes.append(float(row["c"]))
        except (KeyError, TypeError, ValueError):
            continue
    if len(closes) < lookback + 2:
        return None
    return compute_sr_context_from_lists(highs, lows, closes, lookback=lookback)


def adaptive_step_bps(
    base_step_bps: int,
    ctx: GridSrContext | None,
    *,
    atr_k: float,
    cap_bps: int,
    floor_bps: int = 8,
) -> int:
    """Widen ladder steps when ATR% is elevated (big BTC moves)."""
    if ctx is None or ctx.atr_pct <= 0:
        return max(floor_bps, base_step_bps)
    # atr_pct = (ATR/close)*100 (e.g. 1.2 means 1.2%). extra bps ~ atr_pct * k / 2
    extra = int(round(ctx.atr_pct * float(atr_k) / 2.0))
    v = max(floor_bps, base_step_bps + max(0, extra))
    return min(cap_bps, v)


def wide_mode_breakout(
    mid: float,
    ctx: GridSrContext | None,
    *,
    breakout_bps: float,
    min_atr_pct: float,
) -> bool:
    """True → use wide ladder (big move / breakout / volatile tape)."""
    if ctx is None or mid <= 0:
        return False
    if ctx.atr_pct >= min_atr_pct:
        return True
    s, r = ctx.support, ctx.resistance
    if s is None or r is None or r <= s:
        return False
    eps = breakout_bps / 10_000.0
    return mid < s * (1.0 - eps) or mid > r * (1.0 + eps)


def grid_center_with_sr(
    mid: float,
    ctx: GridSrContext | None,
    *,
    anchor_blend: float,
) -> float:
    """
    Blend mid toward (S+R)/2 when price trades **inside** the SF band — mean-reversion MM.
    Outside band, lean toward the active side (continuation-friendly center).
    """
    if ctx is None or mid <= 0:
        return mid
    s, r = ctx.support, ctx.resistance
    if s is None or r is None or r <= s:
        return mid
    sr_mid = (s + r) / 2.0
    b = max(0.0, min(1.0, anchor_blend))
    if s <= mid <= r:
        return mid * (1.0 - b) + sr_mid * b
    if mid > r:
        return mid * 0.82 + r * 0.18
    return mid * 0.82 + s * 0.18


def bonus_sell_prices_near_resistance(
    mid: float,
    resistance: float,
    *,
    extra_bps: int,
    max_extra: int = 2,
) -> list[float]:
    """Extra post-only sell rungs just inside resistance (mean reversion at supply)."""
    if mid <= 0 or resistance <= 0 or resistance <= mid * 1.0001:
        return []
    out: list[float] = []
    for k in range(1, max_extra + 1):
        px = resistance * (1.0 - (extra_bps * k) / 10_000.0)
        if px > mid * 1.00005:
            out.append(px)
    return out
