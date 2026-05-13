"""fib_sr_trigger — Fibonacci × Support/Resistance confluence detector.

Two implementations side-by-side:
  - FibSrV1State:  port of SygnifStrategy.fib_bounce_long
                   (close near fib_0.618 + RSI<30 + bull SFP)
  - FibSrV2State:  research-redesigned (LuxAlgo/Babypips/ACY consensus):
                   pivot 5/5 S/R clustered with fibs of last major swing,
                   confluence score >= 2, bounce candle, volume spike, RSI<40

Sources synthesised:
  - Babypips "Combining Fibs with S/R" — confluence math
  - LuxAlgo Pro Toolkit, Strength Classifier — clustering pattern
  - ACY 2025 confluence backtests — 70-75% WR claim (fib + RSI + volume)
  - QuantInsti pivot strategy — pivot 5/5 + cluster tolerance
  - Trading Rush 100-test fib study — naive fib alone = 15% react
  - Williams Fractal — pivot detection primitive

Quantitative defaults from research:
  pivot_left/right       5
  rsi_period            14
  rsi_long_max          40   (community uses 30-40)
  vol_period            20
  vol_mult              1.3  (1.3-1.5 typical for "volume spike")
  fib_window            240  bars for major swing range
  confluence_tol_pct    0.005  (0.5% — standard "tight")
  confluence_min_score  2    (2-3 sources stacked = high-probability)
  round_number_step     1000 (BTC psychological barriers at $1k)
"""
from __future__ import annotations

import collections
import math
from typing import Optional


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

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
    """Wilder's RSI on a list of recent closes."""
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))
    # Wilder smoothing: SMA seed, then RMA
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr_simple(bars: list[dict], period: int = 14) -> Optional[float]:
    """Simple-mean ATR over the last `period` bars."""
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(len(bars) - period, len(bars)):
        h = bars[i]["high"]; l = bars[i]["low"]; pc = bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / period


# ---------------------------------------------------------------------------
# V1 — port of SygnifStrategy.fib_bounce_long  (verbatim, as already coded)
# ---------------------------------------------------------------------------

class FibSrV1State:
    """Reproduces SygnifStrategy.py lines 1233-1245 exactly.

    Fires LONG when:
      - close is within 0.5% of fib_0.618 (computed over last 240 bars)
      - RSI(14) < 30
      - bull SFP detected (low < 50-bar key_low AND close > key_low)
    """

    def __init__(
        self,
        fib_window: int = 240,
        sfp_lookback: int = 50,
        rsi_period: int = 14,
        rsi_max: float = 30.0,
        fib_tol_pct: float = 0.005,
    ):
        self.fib_window  = fib_window
        self.sfp_lookback = sfp_lookback
        self.rsi_period  = rsi_period
        self.rsi_max     = rsi_max
        self.fib_tol_pct = fib_tol_pct
        buf = max(fib_window, sfp_lookback + 1, rsi_period + 1)
        self.bars: collections.deque[dict] = collections.deque(maxlen=buf)
        self._last_fire_ts = 0

    def evaluate(self, bar: dict) -> Optional[dict]:
        if not bar.get("confirm"):
            return None
        ts = int(bar.get("ts_ms_open", 0))
        self.bars.append({
            "ts_ms_open": ts,
            "open":   float(bar["open"]),
            "high":   float(bar["high"]),
            "low":    float(bar["low"]),
            "close":  float(bar["close"]),
            "volume": float(bar["volume"]),
        })
        if len(self.bars) < max(self.fib_window, self.sfp_lookback + 1, self.rsi_period + 1):
            return None
        bars_list = list(self.bars)

        # SFP: 50-bar key_low excluding current bar (shift(1) equivalent)
        window = bars_list[-self.sfp_lookback - 1:-1]
        key_low = min(b["low"] for b in window)
        cur = bars_list[-1]
        sfp_long = cur["low"] < key_low and cur["close"] > key_low
        if not sfp_long:
            return None

        # Fib over 240-bar window (INCLUDES current bar to match strategy code)
        fwin = bars_list[-self.fib_window:]
        hi = max(b["high"] for b in fwin)
        lo = min(b["low"]  for b in fwin)
        if hi <= lo:
            return None
        fib_618 = compute_fib_levels(hi, lo)["fib_0.618"]
        if not (fib_618 * (1 - self.fib_tol_pct) <= cur["close"] <= fib_618 * (1 + self.fib_tol_pct)):
            return None

        # RSI(14) < 30
        closes = [b["close"] for b in bars_list]
        rsi = rsi_wilder(closes, self.rsi_period)
        if rsi is None or rsi >= self.rsi_max:
            return None

        if ts <= self._last_fire_ts:
            return None
        self._last_fire_ts = ts
        return {
            "direction": "long",
            "trigger":   "fib_sr_v1",
            "mid":       cur["close"],
            "meta": {
                "thesis":   (f"fib_0.618=${fib_618:,.2f}, close=${cur['close']:,.2f}, "
                             f"RSI={rsi:.1f}, bull SFP swept ${key_low:,.2f}"),
                "fib_618":  round(fib_618, 2),
                "rsi":      round(rsi, 2),
                "key_low":  round(key_low, 2),
            },
        }


# ---------------------------------------------------------------------------
# V2 — research-redesigned: pivot S/R × fib confluence + bounce + volume
# ---------------------------------------------------------------------------

class FibSrV2State:
    """Pivot-based S/R clustered with fibs, scored by source-count.

    Detection pipeline:
      1. Detect pivot highs/lows (5/5 left/right, Williams Fractal).
         Pivot confirmed 5 bars after the candidate.
      2. Maintain rolling pivots (last 240 bars).
      3. Compute fib levels from the most recent major swing
         (last confirmed pivot-high paired with last confirmed pivot-low,
         range >= 3 * ATR(14) to filter noise).
      4. For each new bar, find confluence zones where a pivot S/R level
         coincides with a fib level (|p - f| / price <= 0.005).
         Score = number of overlapping sources (fib + pivot + round number).
      5. Fire LONG when:
           - close in a support-confluence zone (score >= 2)
           - bull bounce candle: close > open AND lower-wick > 33% of range
           - volume >= 1.3 * SMA(volume, 20)
           - RSI(14) < 40 (community uses 30-40 for the long bias)
      6. Mirror for SHORT at resistance-confluence.
    """

    def __init__(
        self,
        pivot_lr: int   = 5,
        atr_period: int = 14,
        rsi_period: int = 14,
        rsi_long_max: float = 40.0,
        rsi_short_min: float = 60.0,
        vol_period: int = 20,
        vol_mult: float = 1.3,
        fib_window: int = 240,
        confluence_tol_pct: float = 0.005,
        confluence_min_score: int = 2,
        round_number_step: float = 1000.0,
        min_swing_atr_mult: float = 3.0,
        bounce_wick_ratio: float = 0.33,
    ):
        self.pivot_lr          = pivot_lr
        self.atr_period        = atr_period
        self.rsi_period        = rsi_period
        self.rsi_long_max      = rsi_long_max
        self.rsi_short_min     = rsi_short_min
        self.vol_period        = vol_period
        self.vol_mult          = vol_mult
        self.fib_window        = fib_window
        self.confluence_tol_pct = confluence_tol_pct
        self.confluence_min_score = confluence_min_score
        self.round_number_step = round_number_step
        self.min_swing_atr_mult = min_swing_atr_mult
        self.bounce_wick_ratio = bounce_wick_ratio

        buf = max(fib_window, atr_period + 1, vol_period, rsi_period + 1, pivot_lr * 2 + 1)
        self.bars: collections.deque[dict] = collections.deque(maxlen=buf)
        # Pivots: (ts, kind:'high'|'low', level)
        self.pivots: collections.deque[dict] = collections.deque(maxlen=64)
        self._last_fire_ts_long = 0
        self._last_fire_ts_short = 0

    # ── pivot detection (5/5 Williams Fractal) ───────────────────────────
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
            return {"ts": cand["ts_ms_open"], "kind": "high", "level": cand["high"]}
        if is_low:
            return {"ts": cand["ts_ms_open"], "kind": "low",  "level": cand["low"]}
        return None

    # ── fib levels from most-recent major swing ──────────────────────────
    def _major_fib_levels(self, atr_val: float) -> Optional[dict]:
        """Pick the last confirmed pivot-high + pivot-low whose absolute
        gap >= min_swing_atr_mult * ATR. Returns dict of fib levels or None."""
        recent_high = None; recent_low = None
        # Walk newest-first
        for p in reversed(self.pivots):
            if recent_high is None and p["kind"] == "high":
                recent_high = p
            elif recent_low is None and p["kind"] == "low":
                recent_low = p
            if recent_high is not None and recent_low is not None:
                break
        if recent_high is None or recent_low is None:
            return None
        h = recent_high["level"]; l = recent_low["level"]
        if h - l < self.min_swing_atr_mult * atr_val:
            return None
        return compute_fib_levels(h, l)

    # ── confluence zones ─────────────────────────────────────────────────
    def _zone_score(self, price: float, fibs: dict) -> tuple[int, list, str]:
        """For a candidate price, count overlapping S/R sources.
        Returns (score, source_labels, dominant_kind 'support'|'resistance'|None)."""
        sources = []
        # fib sources — any fib within tol
        for name, level in fibs.items():
            if level == 0:
                continue
            if abs(price - level) / price <= self.confluence_tol_pct:
                sources.append(name)
        # pivot sources
        for piv in self.pivots:
            if abs(price - piv["level"]) / price <= self.confluence_tol_pct:
                sources.append(f"pivot_{piv['kind']}")
        # round-number bonus
        nearest_round = round(price / self.round_number_step) * self.round_number_step
        if nearest_round > 0 and abs(price - nearest_round) / price <= self.confluence_tol_pct:
            sources.append("round_number")
        # determine support vs resistance — majority of pivot kinds
        kinds = [s for s in sources if s.startswith("pivot_")]
        if not kinds:
            kind = None
        else:
            n_low = sum(1 for k in kinds if "low" in k)
            n_high = sum(1 for k in kinds if "high" in k)
            kind = "support" if n_low >= n_high else "resistance"
        return (len(sources), sources, kind)

    # ── main entry point ─────────────────────────────────────────────────
    def evaluate(self, bar: dict) -> Optional[dict]:
        if not bar.get("confirm"):
            return None
        ts = int(bar.get("ts_ms_open", 0))
        self.bars.append({
            "ts_ms_open": ts,
            "open":   float(bar["open"]),
            "high":   float(bar["high"]),
            "low":    float(bar["low"]),
            "close":  float(bar["close"]),
            "volume": float(bar["volume"]),
        })
        # Track new pivots (lagged by pivot_lr bars)
        np_ = self._confirm_pivot()
        if np_ and (not self.pivots or self.pivots[-1]["ts"] != np_["ts"]):
            self.pivots.append(np_)

        # Cold-start
        bars_list = list(self.bars)
        if len(bars_list) < max(self.fib_window, self.rsi_period + 1, self.vol_period, self.atr_period + 1):
            return None

        atr_val = atr_simple(bars_list, self.atr_period)
        if atr_val is None or atr_val <= 0:
            return None

        # Need at least 1 pivot-high + 1 pivot-low to build fibs
        fibs = self._major_fib_levels(atr_val)
        if fibs is None:
            return None

        cur = bars_list[-1]
        close = cur["close"]
        score, sources, kind = self._zone_score(close, fibs)
        if score < self.confluence_min_score or kind is None:
            return None

        # Volume filter
        recent_vol = [b["volume"] for b in bars_list[-self.vol_period:]]
        vol_sma = sum(recent_vol) / len(recent_vol)
        if cur["volume"] < self.vol_mult * vol_sma:
            return None

        # Bounce-candle filter
        body_top = max(cur["open"], cur["close"])
        body_bot = min(cur["open"], cur["close"])
        rng = cur["high"] - cur["low"]
        if rng <= 0:
            return None
        lower_wick = body_bot - cur["low"]
        upper_wick = cur["high"] - body_top

        # RSI
        closes = [b["close"] for b in bars_list]
        rsi = rsi_wilder(closes, self.rsi_period)
        if rsi is None:
            return None

        # LONG branch — bull bounce at confluence-support
        if kind == "support":
            bull_body  = cur["close"] > cur["open"]
            long_lower_wick = lower_wick / rng >= self.bounce_wick_ratio
            if bull_body and long_lower_wick and rsi < self.rsi_long_max and ts > self._last_fire_ts_long:
                self._last_fire_ts_long = ts
                return {
                    "direction": "long",
                    "trigger":   "fib_sr_v2",
                    "mid":       close,
                    "meta": {
                        "score":      score,
                        "sources":    sources,
                        "rsi":        round(rsi, 1),
                        "vol_mult":   round(cur["volume"] / vol_sma, 2) if vol_sma else None,
                        "atr":        round(atr_val, 2),
                        "wick_ratio": round(lower_wick / rng, 2),
                    },
                }

        # SHORT branch — bear fade at confluence-resistance
        if kind == "resistance":
            bear_body = cur["close"] < cur["open"]
            long_upper_wick = upper_wick / rng >= self.bounce_wick_ratio
            if bear_body and long_upper_wick and rsi > self.rsi_short_min and ts > self._last_fire_ts_short:
                self._last_fire_ts_short = ts
                return {
                    "direction": "short",
                    "trigger":   "fib_sr_v2",
                    "mid":       close,
                    "meta": {
                        "score":      score,
                        "sources":    sources,
                        "rsi":        round(rsi, 1),
                        "vol_mult":   round(cur["volume"] / vol_sma, 2) if vol_sma else None,
                        "atr":        round(atr_val, 2),
                        "wick_ratio": round(upper_wick / rng, 2),
                    },
                }
        return None
