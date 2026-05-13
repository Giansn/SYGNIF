"""fib_sr_v4_trigger — v3 + FVG (Fair Value Gap) magnet filter.

After v3 (v1 core + RSRS regime gate) showed +0.064% net per trade on
90d BTC 5m, this adds FVG ("magnet gap") detection — the ICT/SMC
concept that liquidity voids act as price magnets.

FVG definition (ICT canonical, 3-candle pattern):
  - Bullish FVG (long magnet): bars[-3].high < bars[-1].low
    Gap region = [bars[-3].high, bars[-1].low]
    CE (Consequent Encroachment) = midpoint of gap
  - Bearish FVG: bars[-3].low > bars[-1].high

Quality filter (displacement requirement):
  - bars[-2] (the middle candle) must have body size >= DISPLACEMENT_ATR_MULT * ATR
  - Otherwise the "gap" is just micro-noise

Magnet behavior:
  - FVG is "unfilled" until price re-enters the gap region
  - When price re-enters, mark as filled and remove from active list
  - Active FVGs are the unfilled magnets that may pull price

Modes (composable filter on top of v3):
  - FVG_MODE=score   -> count FVG presence in the confluence score
  - FVG_MODE=strict  -> require close within unfilled bullish FVG for LONG
  - FVG_MODE=ce      -> require close near FVG midpoint (CE) — purest ICT entry
  - FVG_MODE=off     -> v3 unchanged

References:
  - ICT FVG canonical definition (innercircletrader.net)
  - thesimpleict.com — displacement candle requirement
  - 3-candle pattern, C2 displacement, CE midpoint as entry
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


class FibSrV4State:
    """v3 (v1 core + RSRS) + FVG magnet filter."""

    def __init__(
        self,
        # v1 core
        fib_window: int = 240,
        sfp_lookback: int = 50,
        rsi_period: int = 14,
        rsi_max: float = 35.0,
        fib_tol_pct: float = 0.005,
        # Pivot tracking
        pivot_lr: int = 5,
        # RSRS
        rsrs_enabled: bool = True,
        rsrs_threshold: float = 0.0,
        rsrs_n: int = 18,
        rsrs_m: int = 600,
        # FVG ── new in v4
        fvg_mode: str = "score",          # off | score | strict | ce
        fvg_displacement_atr: float = 1.0,  # C2 body >= this * ATR
        fvg_atr_period: int = 14,
        fvg_max_active: int = 50,         # cap on tracked FVGs
        fvg_max_age_bars: int = 500,      # purge old FVGs
        fvg_tol_pct: float = 0.005,       # how close to FVG/CE counts as "in"
    ):
        self.fib_window      = fib_window
        self.sfp_lookback    = sfp_lookback
        self.rsi_period      = rsi_period
        self.rsi_max         = rsi_max
        self.fib_tol_pct     = fib_tol_pct
        self.pivot_lr        = pivot_lr
        self.rsrs_enabled    = rsrs_enabled
        self.rsrs_threshold  = rsrs_threshold
        self.rsrs_n          = rsrs_n
        self.rsrs_m          = rsrs_m
        self.fvg_mode        = fvg_mode
        self.fvg_displacement_atr = fvg_displacement_atr
        self.fvg_atr_period  = fvg_atr_period
        self.fvg_max_active  = fvg_max_active
        self.fvg_max_age_bars = fvg_max_age_bars
        self.fvg_tol_pct     = fvg_tol_pct

        buf = max(fib_window, sfp_lookback + 1, rsi_period + 1,
                  rsrs_m + rsrs_n, 2 * pivot_lr + 1, fvg_atr_period + 3)
        self.bars: collections.deque[dict] = collections.deque(maxlen=buf)
        self.pivots: collections.deque[dict] = collections.deque(maxlen=2000)
        self.fvgs: list[dict] = []   # active (unfilled) FVGs
        self._bar_count = 0
        self._rsrs_betas: collections.deque[float] = collections.deque(maxlen=rsrs_m)
        self._last_fire_ts = 0
        self._stat_blocked_fvg = 0

    # ── Pivot detection (validated against Pine ta.pivothigh) ──
    def _confirm_pivot(self) -> Optional[dict]:
        needed = 2 * self.pivot_lr + 1
        if len(self.bars) < needed: return None
        bars = list(self.bars)
        idx = -(self.pivot_lr + 1)
        cand = bars[idx]
        left  = bars[idx - self.pivot_lr : idx]
        right = bars[idx + 1 :]
        is_high = all(cand["high"] > b["high"] for b in left + right)
        is_low  = all(cand["low"]  < b["low"]  for b in left + right)
        if is_high:
            return {"ts": cand["ts_ms_open"], "kind": "high",
                    "level": cand["high"], "bar_idx": self._bar_count - self.pivot_lr - 1}
        if is_low:
            return {"ts": cand["ts_ms_open"], "kind": "low",
                    "level": cand["low"],  "bar_idx": self._bar_count - self.pivot_lr - 1}
        return None

    # ── RSRS rolling update + z-score ──
    def _rsrs_update(self):
        if not self.rsrs_enabled or len(self.bars) < self.rsrs_n: return
        beta = rsrs_beta(list(self.bars)[-self.rsrs_n:])
        if beta is not None: self._rsrs_betas.append(beta)

    def _rsrs_z(self) -> Optional[float]:
        if not self.rsrs_enabled or len(self._rsrs_betas) < min(self.rsrs_m, 50):
            return None
        betas = list(self._rsrs_betas)
        cur = betas[-1]
        mu = sum(betas) / len(betas)
        sd = math.sqrt(sum((b - mu) ** 2 for b in betas) / len(betas))
        return (cur - mu) / sd if sd > 0 else None

    # ── FVG detection (ICT canonical 3-candle pattern) ──
    def _detect_fvg(self):
        """Check if the latest 3 bars form an FVG. Adds to self.fvgs if so."""
        if self.fvg_mode == "off" or len(self.bars) < 3:
            return
        bars = list(self.bars)
        c1, c2, c3 = bars[-3], bars[-2], bars[-1]
        atr = atr_simple(bars, self.fvg_atr_period)
        if atr is None or atr <= 0:
            return
        # Displacement requirement on C2
        c2_body = abs(c2["close"] - c2["open"])
        if c2_body < self.fvg_displacement_atr * atr:
            return
        # Bullish FVG: c1.high < c3.low
        if c1["high"] < c3["low"]:
            self.fvgs.append({
                "kind":     "bull",
                "low":      c1["high"],
                "high":     c3["low"],
                "ce":       (c1["high"] + c3["low"]) / 2,
                "ts":       c2["ts_ms_open"],
                "bar_idx":  self._bar_count - 1,
                "filled":   False,
            })
        # Bearish FVG: c1.low > c3.high
        elif c1["low"] > c3["high"]:
            self.fvgs.append({
                "kind":     "bear",
                "low":      c3["high"],
                "high":     c1["low"],
                "ce":       (c1["low"] + c3["high"]) / 2,
                "ts":       c2["ts_ms_open"],
                "bar_idx":  self._bar_count - 1,
                "filled":   False,
            })
        # Cap active FVGs
        if len(self.fvgs) > self.fvg_max_active:
            self.fvgs = [f for f in self.fvgs if not f["filled"]][-self.fvg_max_active:]

    def _update_fvg_status(self, bar: dict):
        """Mark FVGs as filled when price enters the gap region."""
        h = bar["high"]; l = bar["low"]
        for fvg in self.fvgs:
            if fvg["filled"]:
                continue
            # Filled when bar's range overlaps the gap interior
            if l <= fvg["high"] and h >= fvg["low"]:
                fvg["filled"] = True
        # Purge old/filled FVGs to keep memory bounded
        if len(self.fvgs) > self.fvg_max_active:
            kept = []
            for f in self.fvgs:
                age = self._bar_count - f["bar_idx"]
                if not f["filled"] and age <= self.fvg_max_age_bars:
                    kept.append(f)
            self.fvgs = kept[-self.fvg_max_active:]

    def _active_bull_fvgs_near(self, price: float) -> list[dict]:
        """Unfilled bullish FVGs where price is inside the gap OR near CE."""
        out = []
        for f in self.fvgs:
            if f["filled"] or f["kind"] != "bull":
                continue
            age = self._bar_count - f["bar_idx"]
            if age > self.fvg_max_age_bars:
                continue
            # "in gap" = price in [low, high]
            in_gap = f["low"] <= price <= f["high"]
            near_ce = abs(price - f["ce"]) / price <= self.fvg_tol_pct
            if in_gap or near_ce:
                out.append(f)
        return out

    # ── Main entry point ──
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

        # Maintain side state
        np_ = self._confirm_pivot()
        if np_ and (not self.pivots or self.pivots[-1]["ts"] != np_["ts"]):
            self.pivots.append(np_)
        self._rsrs_update()
        self._update_fvg_status(bar_norm)
        self._detect_fvg()

        if len(self.bars) < max(self.fib_window, self.sfp_lookback + 1, self.rsi_period + 1):
            return None
        bars_list = list(self.bars)
        cur = bars_list[-1]
        close = cur["close"]

        # v1 CORE
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
        if self.rsrs_enabled:
            z = self._rsrs_z()
            if z is None or z < self.rsrs_threshold:
                return None
        else:
            z = None

        # FVG filter (the new layer)
        active_fvgs = self._active_bull_fvgs_near(close)
        if self.fvg_mode in ("strict", "ce") and not active_fvgs:
            self._stat_blocked_fvg += 1
            return None
        if self.fvg_mode == "ce":
            # Require close near a CE specifically (not just inside gap)
            near_ce = [f for f in active_fvgs
                       if abs(close - f["ce"]) / close <= self.fvg_tol_pct]
            if not near_ce:
                self._stat_blocked_fvg += 1
                return None

        if ts <= self._last_fire_ts: return None
        self._last_fire_ts = ts

        return {
            "direction": "long",
            "trigger":   "fib_sr_v4",
            "mid":       close,
            "meta": {
                "fib_618":   round(fib_618, 2),
                "rsi":       round(rsi, 2),
                "rsrs_z":    round(z, 3) if z is not None else None,
                "key_low":   round(key_low, 2),
                "n_fvg":     len(active_fvgs),
                "fvg_mode":  self.fvg_mode,
                "thesis":    (f"v3 core + FVG[{self.fvg_mode}]: "
                              f"{len(active_fvgs)} unfilled bullish FVGs near price"),
            },
        }
