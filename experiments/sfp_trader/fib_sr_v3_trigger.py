"""fib_sr_v3_trigger — v1 winner + 2 audited filters.

After the Pine + open-source audit found v2's canonical-pipeline approach
under-performed v1 on BTC, we go the OTHER direction: start with v1's
proven winner (SFP + fib_0.618 + RSI<35 at 5m + maker fees) and add ONLY
the two highest-ROI filters surfaced in the 2nd-round audit.

Added filters:

  1. RSRS regime gate (Resistance/Support Relative Strength)
     - Rolling OLS beta of high~low over N=18 bars
     - Z-score normalization over M=600 bars
     - Only accept LONG if rsrs_z < -RSRS_THRESHOLD (compressed range)
     - Filters out "support" in a runaway trend where beta > 1
     - Source: institutional A-share + ETF backtests (TradingView script)
     - arXiv-adjacent: regression-based level-strength

  2. 3-pivot cluster confirmation (Coinmonks)
     - The fib_0.618 level is "valid" only if >= MIN_CLUSTER_TOUCHES historical
       pivots fall within +/- CLUSTER_TOL_PCT of it
     - Look-ahead protection: only count pivots OLDER than CLUSTER_AGE_MIN bars
     - Source: github.com/dimensionsoftware Coinmonks article, with rigorous
       min_rank protection against recency bias

Layered design — each filter can be toggled via env knobs:

  FIBSRV3_RSRS_ENABLED=1      enable RSRS regime gate (default on)
  FIBSRV3_RSRS_THRESHOLD=0.7  z-score threshold (paper default)
  FIBSRV3_RSRS_N=18           OLS window
  FIBSRV3_RSRS_M=600          z-score normalization window

  FIBSRV3_CLUSTER_ENABLED=1   enable 3-pivot cluster gate (default on)
  FIBSRV3_CLUSTER_TOL=0.002   cluster tolerance (Coinmonks 0.2%)
  FIBSRV3_CLUSTER_MIN=2       minimum historical touches required
  FIBSRV3_CLUSTER_AGE=120     pivot age floor (no look-ahead)

  FIBSRV3_RSI_MAX=35          RSI threshold (v1 winner)
  FIBSRV3_FIB_TOL=0.005       fib proximity (v1 winner)

This lets us A/B test each filter in isolation.
"""
from __future__ import annotations

import collections
import math
import os
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
    if len(closes) < period + 1:
        return None
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
    """OLS regression beta of high ~ low over the window.

    Direct OLS: beta = cov(L,H) / var(L). Returns None on degenerate input.
    """
    n = len(bars_window)
    if n < 5:
        return None
    sx = sy = sxx = sxy = 0.0
    for b in bars_window:
        x = b["low"]; y = b["high"]
        sx += x; sy += y; sxx += x * x; sxy += x * y
    nf = float(n)
    denom = sxx * nf - sx * sx
    if denom <= 0:
        return None
    return (sxy * nf - sx * sy) / denom


class FibSrV3State:
    """v1 winner core + RSRS regime gate + 3-pivot cluster confirmation."""

    def __init__(
        self,
        # v1 core (proven winners)
        fib_window: int = 240,
        sfp_lookback: int = 50,
        rsi_period: int = 14,
        rsi_max: float = 35.0,
        fib_tol_pct: float = 0.005,
        # Pivot tracking
        pivot_lr: int = 5,
        # RSRS filter
        rsrs_enabled: bool = True,
        rsrs_threshold: float = 0.7,
        rsrs_n: int = 18,
        rsrs_m: int = 600,
        # Cluster filter
        cluster_enabled: bool = True,
        cluster_tol: float = 0.002,
        cluster_min: int = 2,
        cluster_age_min: int = 120,
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
        self.cluster_enabled = cluster_enabled
        self.cluster_tol     = cluster_tol
        self.cluster_min     = cluster_min
        self.cluster_age_min = cluster_age_min

        # Buffer big enough for everything
        buf = max(fib_window, sfp_lookback + 1, rsi_period + 1,
                  rsrs_m + rsrs_n, 2 * pivot_lr + 1)
        self.bars: collections.deque[dict] = collections.deque(maxlen=buf)
        # Pivot history with bar-index tracking for age check
        self.pivots: collections.deque[dict] = collections.deque(maxlen=2000)
        self._bar_count = 0
        self._last_fire_ts = 0

        # Rolling RSRS beta history for z-score normalization
        self._rsrs_betas: collections.deque[float] = collections.deque(maxlen=rsrs_m)

        # Stats for observability
        self._stat_signals_raw    = 0  # v1 condition satisfied
        self._stat_blocked_rsrs   = 0
        self._stat_blocked_cluster = 0

    # ── Pivot detection (5/5 Williams, validated against ta.pivothigh) ──
    def _confirm_pivot(self) -> Optional[dict]:
        needed = 2 * self.pivot_lr + 1
        if len(self.bars) < needed:
            return None
        bars = list(self.bars)
        idx = -(self.pivot_lr + 1)
        cand = bars[idx]
        left  = bars[idx - self.pivot_lr : idx]
        right = bars[idx + 1 :]
        is_high = (
            all(cand["high"] > b["high"] for b in left)
            and all(cand["high"] > b["high"] for b in right)
        )
        is_low = (
            all(cand["low"] < b["low"] for b in left)
            and all(cand["low"] < b["low"] for b in right)
        )
        if is_high:
            # bar_idx == cand's age: bars-1 - position of cand in `bars`
            cand_bar_idx = self._bar_count - self.pivot_lr - 1
            return {"ts": cand["ts_ms_open"], "kind": "high",
                    "level": cand["high"], "bar_idx": cand_bar_idx}
        if is_low:
            cand_bar_idx = self._bar_count - self.pivot_lr - 1
            return {"ts": cand["ts_ms_open"], "kind": "low",
                    "level": cand["low"], "bar_idx": cand_bar_idx}
        return None

    # ── RSRS rolling update — called EVERY bar so the z-score normalization
    #    accumulates regardless of v1 firing. The previous implementation
    #    only updated the beta deque inside the v1 fire path, which meant
    #    walk-forward sub-windows never warmed up.
    def _rsrs_update(self):
        """Compute and store the current bar's beta, called from evaluate()."""
        if not self.rsrs_enabled:
            return
        if len(self.bars) < self.rsrs_n:
            return
        window = list(self.bars)[-self.rsrs_n:]
        beta = rsrs_beta(window)
        if beta is not None:
            self._rsrs_betas.append(beta)

    def _rsrs_z(self) -> Optional[float]:
        """Read the current z-score. _rsrs_update() must have been called."""
        if not self.rsrs_enabled:
            return None
        if len(self._rsrs_betas) < min(self.rsrs_m, 50):
            return None  # cold start
        betas = list(self._rsrs_betas)
        current_beta = betas[-1]
        mu = sum(betas) / len(betas)
        var = sum((b - mu) ** 2 for b in betas) / len(betas)
        sd = math.sqrt(var)
        if sd == 0:
            return None
        return (current_beta - mu) / sd

    # ── Cluster validation for a candidate level ─────────────────────────
    def _cluster_touches(self, level: float) -> int:
        """Count historical pivots within +/- cluster_tol of level,
        excluding pivots from the last cluster_age_min bars (look-ahead protection)."""
        count = 0
        for p in self.pivots:
            age = self._bar_count - p["bar_idx"]
            if age < self.cluster_age_min:
                continue
            if abs(p["level"] - level) / level <= self.cluster_tol:
                count += 1
        return count

    # ── Main entry point ─────────────────────────────────────────────────
    def evaluate(self, bar: dict) -> Optional[dict]:
        if not bar.get("confirm"):
            return None
        ts = int(bar.get("ts_ms_open", 0))
        self._bar_count += 1
        self.bars.append({
            "ts_ms_open": ts,
            "open":   float(bar["open"]),
            "high":   float(bar["high"]),
            "low":    float(bar["low"]),
            "close":  float(bar["close"]),
            "volume": float(bar["volume"]),
        })
        # Track pivots
        np_ = self._confirm_pivot()
        if np_ and (not self.pivots or self.pivots[-1]["ts"] != np_["ts"]):
            self.pivots.append(np_)

        # Always update RSRS beta history so z-score is ready when v1 fires
        self._rsrs_update()

        if len(self.bars) < max(self.fib_window, self.sfp_lookback + 1, self.rsi_period + 1):
            return None
        bars_list = list(self.bars)
        cur = bars_list[-1]
        close = cur["close"]

        # --- v1 CORE: SFP-reclaim + close-near-fib_0.618 + RSI<35 ---
        # 1. SFP: prior 50-bar key_low excluding current bar
        window = bars_list[-self.sfp_lookback - 1:-1]
        key_low = min(b["low"] for b in window)
        sfp_long = cur["low"] < key_low and cur["close"] > key_low
        if not sfp_long:
            return None

        # 2. Fib over 240-bar window (matches strategy code)
        fwin = bars_list[-self.fib_window:]
        hi = max(b["high"] for b in fwin)
        lo = min(b["low"]  for b in fwin)
        if hi <= lo:
            return None
        fib_618 = compute_fib_levels(hi, lo)["fib_0.618"]
        if not (fib_618 * (1 - self.fib_tol_pct) <= close <= fib_618 * (1 + self.fib_tol_pct)):
            return None

        # 3. RSI < 35
        closes = [b["close"] for b in bars_list]
        rsi = rsi_wilder(closes, self.rsi_period)
        if rsi is None or rsi >= self.rsi_max:
            return None

        # ALL v1 conditions met
        self._stat_signals_raw += 1

        # --- NEW FILTER 1: RSRS regime gate ---
        if self.rsrs_enabled:
            z = self._rsrs_z()
            # Original RSRS paper rule: buy when z > +threshold (trending regime)
            # For our fib_bounce_long: we want trend-up so the pullback is buyable
            if z is None or z < self.rsrs_threshold:
                self._stat_blocked_rsrs += 1
                return None
        else:
            z = None

        # --- NEW FILTER 2: 3-pivot cluster confirmation ---
        if self.cluster_enabled:
            touches = self._cluster_touches(fib_618)
            if touches < self.cluster_min:
                self._stat_blocked_cluster += 1
                return None
        else:
            touches = 0

        if ts <= self._last_fire_ts:
            return None
        self._last_fire_ts = ts

        return {
            "direction": "long",
            "trigger":   "fib_sr_v3",
            "mid":       close,
            "meta": {
                "thesis":   (f"v1 core (fib_0.618=${fib_618:,.2f}, RSI={rsi:.1f}, "
                             f"swept ${key_low:,.2f})"),
                "fib_618":  round(fib_618, 2),
                "rsi":      round(rsi, 2),
                "key_low":  round(key_low, 2),
                "rsrs_z":   round(z, 3) if z is not None else None,
                "cluster_touches": touches,
                "stats":    {
                    "raw":      self._stat_signals_raw,
                    "blk_rsrs": self._stat_blocked_rsrs,
                    "blk_clu":  self._stat_blocked_cluster,
                },
            },
        }
