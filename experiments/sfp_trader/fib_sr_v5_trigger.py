"""fib_sr_v5_trigger — v3 entries + FVG-as-dynamic-TP (exit) layer.

v4 showed FVG presence is anti-correlated with v3 entry edge. v5 flips
the FVG layer from filter to exit model: use the nearest unfilled
bearish FVG above entry as a dynamic take-profit target. Falls back to
fixed 0.4% TP when no FVG is within reach.

The detector is v3 (v1 core + RSRS regime gate). The TP target is set
in the meta payload so the harness/executor can use it. The variant's
`evaluate()` returns the same fire payload as v3 plus `dynamic_tp` in
meta.

Backtest walk-forward (90d 5m + full-maker fees, v3 entries):

  Mode A: fixed 0.4% TP        WR 58.3%   EV_gross +0.089%   total 2.30%
  Mode B: dynamic FVG TP       WR 80.6%   EV_gross +0.065%   total 1.44%
  Mode D: FVG TP + 0.15% SL    WR 72.2%   EV_gross +0.067%   total 1.53%

Mode B is the first statistically significant WR result in the campaign:
  95% CI [67.7%, 93.5%], z=4.64 vs 50% null (p < 0.0001).

Mode A still wins on absolute total return. Choice depends on operator
preference (Sharpe vs total return vs statistical confidence).
"""
from __future__ import annotations

import collections
import math
from typing import Optional


def compute_fib_levels(high: float, low: float) -> dict:
    diff = high - low
    return {
        "fib_0.0":   low,
        "fib_0.236": low + 0.236 * diff,
        "fib_0.382": low + 0.382 * diff,
        "fib_0.5":   low + 0.5   * diff,
        "fib_0.618": low + 0.618 * diff,
        "fib_0.786": low + 0.786 * diff,
        "fib_1.0":   high,
    }


def rsi_wilder(closes: list[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1: return None
    gains = []; losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rsrs_beta(bars_window: list[dict]) -> Optional[float]:
    n = len(bars_window)
    if n < 5: return None
    sx = sy = sxx = sxy = 0.0
    for b in bars_window:
        x = b["low"]; y = b["high"]
        sx += x; sy += y; sxx += x * x; sxy += x * y
    nf = float(n)
    denom = sxx * nf - sx * sx
    if denom <= 0: return None
    return (sxy * nf - sx * sy) / denom


def atr_simple(bars: list[dict], period: int = 14) -> Optional[float]:
    if len(bars) < period + 1: return None
    trs = []
    for i in range(len(bars) - period, len(bars)):
        h = bars[i]["high"]; l = bars[i]["low"]; pc = bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / period


class FibSrV5State:
    """v3 entry + FVG-as-dynamic-TP exit hint in meta payload."""

    def __init__(
        self,
        # v1 core
        fib_window: int = 240,
        sfp_lookback: int = 50,
        rsi_period: int = 14,
        rsi_max: float = 35.0,
        fib_tol_pct: float = 0.005,
        # RSRS
        rsrs_threshold: float = 0.0,
        rsrs_n: int = 18,
        rsrs_m: int = 600,
        # FVG-as-TP
        fvg_displacement_atr: float = 0.5,
        fvg_atr_period: int = 14,
        fvg_max_active: int = 100,
        fvg_max_age_bars: int = 500,
        fvg_max_tp_dist_pct: float = 0.010,  # max % above entry for FVG TP
        fallback_tp_pct: float = 0.004,       # used when no FVG in range
        fixed_sl_pct: float = 0.0025,
    ):
        self.fib_window      = fib_window
        self.sfp_lookback    = sfp_lookback
        self.rsi_period      = rsi_period
        self.rsi_max         = rsi_max
        self.fib_tol_pct     = fib_tol_pct
        self.rsrs_threshold  = rsrs_threshold
        self.rsrs_n          = rsrs_n
        self.rsrs_m          = rsrs_m
        self.fvg_disp        = fvg_displacement_atr
        self.fvg_atr_p       = fvg_atr_period
        self.fvg_max_active  = fvg_max_active
        self.fvg_max_age     = fvg_max_age_bars
        self.fvg_max_tp_dist = fvg_max_tp_dist_pct
        self.fallback_tp     = fallback_tp_pct
        self.fixed_sl        = fixed_sl_pct

        buf = max(fib_window, sfp_lookback + 1, rsi_period + 1,
                  rsrs_m + rsrs_n, fvg_atr_period + 3)
        self.bars: collections.deque[dict] = collections.deque(maxlen=buf)
        self.bear_fvgs: list[dict] = []
        self._bar_count = 0
        self._rsrs_betas: collections.deque[float] = collections.deque(maxlen=rsrs_m)
        self._last_fire_ts = 0

    def _rsrs_update(self):
        if len(self.bars) < self.rsrs_n: return
        beta = rsrs_beta(list(self.bars)[-self.rsrs_n:])
        if beta is not None: self._rsrs_betas.append(beta)

    def _rsrs_z(self) -> Optional[float]:
        if len(self._rsrs_betas) < min(self.rsrs_m, 50): return None
        betas = list(self._rsrs_betas)
        mu = sum(betas) / len(betas)
        sd = math.sqrt(sum((b - mu) ** 2 for b in betas) / len(betas))
        return (betas[-1] - mu) / sd if sd > 0 else None

    def _update_fvgs(self, bar):
        # Mark filled
        for f in self.bear_fvgs:
            if f["filled"]: continue
            if bar["low"] <= f["high"] and bar["high"] >= f["low"]:
                f["filled"] = True
        # Detect new (bearish only — we use as TP for longs)
        if len(self.bars) < 3: return
        bars = list(self.bars)
        c1, c2, c3 = bars[-3], bars[-2], bars[-1]
        atr = atr_simple(bars, self.fvg_atr_p)
        if atr is None or atr <= 0: return
        if abs(c2["close"] - c2["open"]) < self.fvg_disp * atr: return
        if c1["low"] > c3["high"]:
            self.bear_fvgs.append({
                "low":     c3["high"],
                "high":    c1["low"],
                "ce":      (c3["high"] + c1["low"]) / 2,
                "bar_idx": self._bar_count - 1,
                "filled":  False,
            })
        if len(self.bear_fvgs) > self.fvg_max_active:
            self.bear_fvgs = [f for f in self.bear_fvgs if not f["filled"]][-self.fvg_max_active:]

    def _nearest_bear_fvg_above(self, price: float) -> Optional[dict]:
        best = None
        for f in self.bear_fvgs:
            if f["filled"] or f["low"] <= price:
                continue
            age = self._bar_count - f["bar_idx"]
            if age > self.fvg_max_age:
                continue
            dist = (f["low"] - price) / price
            if dist > self.fvg_max_tp_dist:
                continue
            if best is None or f["low"] < best["low"]:
                best = f
        return best

    def evaluate(self, bar: dict) -> Optional[dict]:
        if not bar.get("confirm"): return None
        ts = int(bar.get("ts_ms_open", 0))
        self._bar_count += 1
        bar_norm = {
            "ts_ms_open": ts,
            "open":   float(bar["open"]),
            "high":   float(bar["high"]),
            "low":    float(bar["low"]),
            "close":  float(bar["close"]),
            "volume": float(bar["volume"]),
        }
        self.bars.append(bar_norm)
        self._rsrs_update()
        self._update_fvgs(bar_norm)

        if len(self.bars) < max(self.fib_window, self.sfp_lookback + 1, self.rsi_period + 1):
            return None
        bars_list = list(self.bars)
        cur = bars_list[-1]
        close = cur["close"]

        # v1 core
        window = bars_list[-self.sfp_lookback - 1:-1]
        key_low = min(b["low"] for b in window)
        if not (cur["low"] < key_low and cur["close"] > key_low):
            return None
        fwin = bars_list[-self.fib_window:]
        hi = max(b["high"] for b in fwin)
        lo = min(b["low"]  for b in fwin)
        if hi <= lo: return None
        fib_618 = compute_fib_levels(hi, lo)["fib_0.618"]
        if not (fib_618 * (1 - self.fib_tol_pct) <= close <= fib_618 * (1 + self.fib_tol_pct)):
            return None
        closes = [b["close"] for b in bars_list]
        rsi = rsi_wilder(closes, self.rsi_period)
        if rsi is None or rsi >= self.rsi_max: return None

        # RSRS gate
        z = self._rsrs_z()
        if z is None or z < self.rsrs_threshold:
            return None

        if ts <= self._last_fire_ts: return None
        self._last_fire_ts = ts

        # Compute dynamic TP from nearest bear FVG above
        fvg_tp = self._nearest_bear_fvg_above(close)
        if fvg_tp:
            tp_px = fvg_tp["low"]
            tp_type = "fvg_low"
            tp_dist_pct = (tp_px - close) / close
        else:
            tp_px = close * (1 + self.fallback_tp)
            tp_type = "fallback_fixed"
            tp_dist_pct = self.fallback_tp

        sl_px = close * (1 - self.fixed_sl)

        return {
            "direction": "long",
            "trigger":   "fib_sr_v5",
            "mid":       close,
            "tp":        tp_px,
            "sl":        sl_px,
            "meta": {
                "fib_618":  round(fib_618, 2),
                "rsi":      round(rsi, 2),
                "rsrs_z":   round(z, 3),
                "tp_type":  tp_type,
                "tp_dist_pct": round(tp_dist_pct, 4),
                "sl_dist_pct": round(self.fixed_sl, 4),
            },
        }


class FibSrV5StateShort:
    """SHORT mirror of FibSrV5State — NOT BACKTESTED.

    Mirror logic:
      - Bear SFP: high > 50-bar key_high AND close < key_high
      - Close within fib_tol of fib_0.382 (= low_w + 0.382 * diff, lower-mid)
      - RSI > rsi_min (mirror of < rsi_max)
      - RSRS z > rsrs_threshold (same — beta is direction-agnostic)
      - Dynamic TP: nearest unfilled BULL FVG below entry (pulls price down)
      - SL fixed % above entry

    CAVEAT: this is a structural mirror, NOT validated by backtest. Use
    with smaller size or skip entirely until short-side backtest exists.
    """

    def __init__(
        self,
        fib_window: int = 240,
        sfp_lookback: int = 50,
        rsi_period: int = 14,
        rsi_min: float = 65.0,
        fib_tol_pct: float = 0.005,
        rsrs_threshold: float = 0.0,
        rsrs_n: int = 18,
        rsrs_m: int = 600,
        fvg_displacement_atr: float = 0.5,
        fvg_atr_period: int = 14,
        fvg_max_active: int = 100,
        fvg_max_age_bars: int = 500,
        fvg_max_tp_dist_pct: float = 0.010,
        fallback_tp_pct: float = 0.004,
        fixed_sl_pct: float = 0.0025,
    ):
        self.fib_window      = fib_window
        self.sfp_lookback    = sfp_lookback
        self.rsi_period      = rsi_period
        self.rsi_min         = rsi_min
        self.fib_tol_pct     = fib_tol_pct
        self.rsrs_threshold  = rsrs_threshold
        self.rsrs_n          = rsrs_n
        self.rsrs_m          = rsrs_m
        self.fvg_disp        = fvg_displacement_atr
        self.fvg_atr_p       = fvg_atr_period
        self.fvg_max_active  = fvg_max_active
        self.fvg_max_age     = fvg_max_age_bars
        self.fvg_max_tp_dist = fvg_max_tp_dist_pct
        self.fallback_tp     = fallback_tp_pct
        self.fixed_sl        = fixed_sl_pct

        buf = max(fib_window, sfp_lookback + 1, rsi_period + 1,
                  rsrs_m + rsrs_n, fvg_atr_period + 3)
        self.bars: collections.deque[dict] = collections.deque(maxlen=buf)
        self.bull_fvgs: list[dict] = []
        self._bar_count = 0
        self._rsrs_betas: collections.deque[float] = collections.deque(maxlen=rsrs_m)
        self._last_fire_ts = 0

    def _rsrs_update(self):
        if len(self.bars) < self.rsrs_n: return
        beta = rsrs_beta(list(self.bars)[-self.rsrs_n:])
        if beta is not None: self._rsrs_betas.append(beta)

    def _rsrs_z(self) -> Optional[float]:
        if len(self._rsrs_betas) < min(self.rsrs_m, 50): return None
        betas = list(self._rsrs_betas)
        mu = sum(betas) / len(betas)
        sd = math.sqrt(sum((b - mu) ** 2 for b in betas) / len(betas))
        return (betas[-1] - mu) / sd if sd > 0 else None

    def _update_fvgs(self, bar):
        # Mark filled
        for f in self.bull_fvgs:
            if f["filled"]: continue
            if bar["low"] <= f["high"] and bar["high"] >= f["low"]:
                f["filled"] = True
        # Detect new bullish FVG (used as TP target for shorts)
        if len(self.bars) < 3: return
        bars = list(self.bars)
        c1, c2, c3 = bars[-3], bars[-2], bars[-1]
        atr = atr_simple(bars, self.fvg_atr_p)
        if atr is None or atr <= 0: return
        if abs(c2["close"] - c2["open"]) < self.fvg_disp * atr: return
        if c1["high"] < c3["low"]:
            self.bull_fvgs.append({
                "low":     c1["high"],
                "high":    c3["low"],
                "ce":      (c1["high"] + c3["low"]) / 2,
                "bar_idx": self._bar_count - 1,
                "filled":  False,
            })
        if len(self.bull_fvgs) > self.fvg_max_active:
            self.bull_fvgs = [f for f in self.bull_fvgs if not f["filled"]][-self.fvg_max_active:]

    def _nearest_bull_fvg_below(self, price: float) -> Optional[dict]:
        """For shorts: find nearest unfilled bull FVG BELOW current price."""
        best = None
        for f in self.bull_fvgs:
            if f["filled"] or f["high"] >= price: continue
            age = self._bar_count - f["bar_idx"]
            if age > self.fvg_max_age: continue
            dist = (price - f["high"]) / price
            if dist > self.fvg_max_tp_dist: continue
            if best is None or f["high"] > best["high"]: best = f
        return best

    def evaluate(self, bar: dict) -> Optional[dict]:
        if not bar.get("confirm"): return None
        ts = int(bar.get("ts_ms_open", 0))
        self._bar_count += 1
        bar_norm = {
            "ts_ms_open": ts,
            "open":   float(bar["open"]),
            "high":   float(bar["high"]),
            "low":    float(bar["low"]),
            "close":  float(bar["close"]),
            "volume": float(bar["volume"]),
        }
        self.bars.append(bar_norm)
        self._rsrs_update()
        self._update_fvgs(bar_norm)

        if len(self.bars) < max(self.fib_window, self.sfp_lookback + 1, self.rsi_period + 1):
            return None
        bars_list = list(self.bars)
        cur = bars_list[-1]
        close = cur["close"]

        # Bear SFP — mirror of v5 long
        window = bars_list[-self.sfp_lookback - 1:-1]
        key_high = max(b["high"] for b in window)
        if not (cur["high"] > key_high and cur["close"] < key_high):
            return None
        # Fib_0.382 (= low_w + 0.382 * diff) — the lower-mid level (mirror axis of 0.618)
        fwin = bars_list[-self.fib_window:]
        hi = max(b["high"] for b in fwin)
        lo = min(b["low"]  for b in fwin)
        if hi <= lo: return None
        fib_382 = compute_fib_levels(hi, lo)["fib_0.382"]
        if not (fib_382 * (1 - self.fib_tol_pct) <= close <= fib_382 * (1 + self.fib_tol_pct)):
            return None
        closes = [b["close"] for b in bars_list]
        rsi = rsi_wilder(closes, self.rsi_period)
        if rsi is None or rsi <= self.rsi_min: return None

        z = self._rsrs_z()
        if z is None or z < self.rsrs_threshold:
            return None

        if ts <= self._last_fire_ts: return None
        self._last_fire_ts = ts

        # Dynamic TP from nearest bull FVG BELOW
        fvg_tp = self._nearest_bull_fvg_below(close)
        if fvg_tp:
            tp_px = fvg_tp["high"]
            tp_type = "fvg_high"
            tp_dist_pct = (close - tp_px) / close
        else:
            tp_px = close * (1 - self.fallback_tp)
            tp_type = "fallback_fixed"
            tp_dist_pct = self.fallback_tp

        sl_px = close * (1 + self.fixed_sl)
        return {
            "direction": "short",
            "trigger":   "fib_sr_v5_short_mirror",
            "mid":       close,
            "tp":        tp_px,
            "sl":        sl_px,
            "meta": {
                "fib_382":  round(fib_382, 2),
                "rsi":      round(rsi, 2),
                "rsrs_z":   round(z, 3),
                "tp_type":  tp_type,
                "tp_dist_pct": round(tp_dist_pct, 4),
                "sl_dist_pct": round(self.fixed_sl, 4),
                "note":     "SHORT MIRROR — not backtested",
            },
        }
