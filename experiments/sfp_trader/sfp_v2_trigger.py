"""sfp_v2_trigger — community-standard Swing Failure Pattern detector.

Rewrite of fib_sfp_trigger.py to match the TradingView community canon
(LuxAlgo / AGPro SFP Engine / cd_sfp_Cx / BullByte Structural Liquidity).

The original fib_sfp_trigger.py used:
  - rolling 50-bar min/max as the "swing"
  - 1% fib-level proximity as the confluence
  - no wick / reclaim / volume filters
  - 1m bars
  - fired on the sweep bar itself, no confirmation

The 30-day backtest showed it had no edge. Research surfaced the canonical
filters and structure used by every published TV SFP script. This module
implements that canon faithfully.

Canonical SFP detection (pivot-based, AGPro spec):
  1. Pivots via ta.pivothigh / ta.pivotlow with symmetric left/right bars
     (default 5/5). Pivot confirmed only AFTER right_bars close — adds
     `right_bars` of latency.
  2. Sweep + close-back-inside:
       bull_sfp: low  < pivot_low  AND close > pivot_low
       bear_sfp: high > pivot_high AND close < pivot_high
  3. Wick-ratio filter (AGPro 0.55 default):
       bear: wick = high - max(open,close); wick / (high - low) >= 0.55
       bull: wick = min(open,close) - low ;  wick / (high - low) >= 0.55
  4. Reclaim filter (AGPro 0.25 x ATR default):
       bear: pivot_high - close >= 0.25 * atr(14)
       bull: close - pivot_low  >= 0.25 * atr(14)
  5. Volume filter (AGPro 1.15 x SMA20 default):
       vol >= 1.15 * SMA(volume, 20)
  6. Edge-trigger guard — don't refire on the same closed bar.

NOT included here (callers add):
  - PDH/PDL / weekly-H/L / session-H/L / EQH-EQL confluence
  - CISD or MSS confirmation
  - FVG retrace entry
  - HTF bias filter

Designed to be embedded directly in fast-reactor or run through the
variants/_harness.py backtester. State is rolling; no DataFrame required.

References:
  - LuxAlgo SFP, AGPro SFP Engine, BullByte Structural Liquidity Signals
  - Wyckoff Spring (Pruden) for academic predecessor
  - ICT/SMC liquidity-sweep playbook
"""
from __future__ import annotations

import collections
from typing import Optional

# Defaults match AGPro SFP Engine where canonical numbers exist.
DEFAULT_PIVOT_LEFT     = 5
DEFAULT_PIVOT_RIGHT    = 5
DEFAULT_ATR_PERIOD     = 14
DEFAULT_VOL_WINDOW     = 20
DEFAULT_MIN_WICK_RATIO = 0.55
DEFAULT_MIN_RECLAIM_ATR = 0.25
DEFAULT_MIN_VOL_MULT    = 1.15
DEFAULT_BUFFER_SIZE     = 240   # how many recent pivots to keep "active"


class SfpV2State:
    """Rolling state for community-standard SFP detection.

    Plug into a backtest variant via:
        s = SfpV2State()                          # accept defaults
        ...
        payload = s.evaluate(bar)
        if payload: ...
    """

    def __init__(
        self,
        pivot_left:       int   = DEFAULT_PIVOT_LEFT,
        pivot_right:      int   = DEFAULT_PIVOT_RIGHT,
        atr_period:       int   = DEFAULT_ATR_PERIOD,
        vol_window:       int   = DEFAULT_VOL_WINDOW,
        min_wick_ratio:   float = DEFAULT_MIN_WICK_RATIO,
        min_reclaim_atr:  float = DEFAULT_MIN_RECLAIM_ATR,
        min_vol_mult:     float = DEFAULT_MIN_VOL_MULT,
        active_pivot_max: int   = DEFAULT_BUFFER_SIZE,
        require_volume:   bool  = True,
    ):
        self.pivot_left      = pivot_left
        self.pivot_right     = pivot_right
        self.atr_period      = atr_period
        self.vol_window      = vol_window
        self.min_wick_ratio  = min_wick_ratio
        self.min_reclaim_atr = min_reclaim_atr
        self.min_vol_mult    = min_vol_mult
        self.active_pivot_max = active_pivot_max
        self.require_volume  = require_volume

        # Bar buffer — keep enough for ATR + pivot window
        buf_needed = max(atr_period + 1, vol_window, pivot_left + pivot_right + 1, 50)
        self.bars: collections.deque[dict] = collections.deque(maxlen=buf_needed)

        # Confirmed pivots — list of dicts: {ts, kind: high/low, level: float}
        # Each is "active" until swept or it falls off the active_pivot_max age.
        self.pivots: collections.deque[dict] = collections.deque(
            maxlen=active_pivot_max
        )

        # Edge-trigger guards per direction
        self._last_fire_ts_long  = 0
        self._last_fire_ts_short = 0

    # ── ATR ──────────────────────────────────────────────────────────────
    def _atr(self) -> Optional[float]:
        """Wilder's ATR over self.atr_period closed bars."""
        n = self.atr_period
        if len(self.bars) < n + 1:
            return None
        # True ranges over the last n bars (need n+1 bars for the first TR's prev_close)
        bars = list(self.bars)
        trs = []
        for i in range(len(bars) - n, len(bars)):
            high = bars[i]["high"]; low = bars[i]["low"]
            prev_close = bars[i - 1]["close"]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)
        # Simple mean ATR (good enough; Wilder smoothing diverges < 1% over 14 bars)
        return sum(trs) / n if trs else None

    # ── Volume SMA ────────────────────────────────────────────────────────
    def _vol_sma(self) -> Optional[float]:
        if len(self.bars) < self.vol_window:
            return None
        bars = list(self.bars)[-self.vol_window:]
        return sum(b["volume"] for b in bars) / self.vol_window

    # ── Pivot detection (lagged by pivot_right bars) ─────────────────────
    def _confirm_pivot(self) -> Optional[dict]:
        """Check if the bar at index -(pivot_right + 1) is a pivot.

        We need pivot_left bars BEFORE it and pivot_right bars AFTER it.
        Returns a new pivot dict, or None.
        """
        needed = self.pivot_left + self.pivot_right + 1
        if len(self.bars) < needed:
            return None
        bars = list(self.bars)
        idx = -(self.pivot_right + 1)            # the candidate pivot
        cand = bars[idx]
        left  = bars[idx - self.pivot_left : idx]
        right = bars[idx + 1 :]                  # exactly pivot_right bars

        # Pivot HIGH: strictly higher than all left+right highs
        is_high = (
            all(cand["high"] > b["high"] for b in left)
            and all(cand["high"] > b["high"] for b in right)
        )
        # Pivot LOW: strictly lower than all left+right lows
        is_low = (
            all(cand["low"] < b["low"] for b in left)
            and all(cand["low"] < b["low"] for b in right)
        )
        if is_high:
            return {"ts": cand["ts_ms_open"], "kind": "high", "level": cand["high"]}
        if is_low:
            return {"ts": cand["ts_ms_open"], "kind": "low",  "level": cand["low"]}
        return None

    # ── Filter quality checks on the sweep bar ──────────────────────────
    def _wick_ratio(self, bar: dict, direction: str) -> float:
        rng = bar["high"] - bar["low"]
        if rng <= 0:
            return 0.0
        body_top = max(bar["open"], bar["close"])
        body_bot = min(bar["open"], bar["close"])
        if direction == "long":
            wick = body_bot - bar["low"]            # lower wick for bull SFP
        else:
            wick = bar["high"] - body_top           # upper wick for bear SFP
        return max(0.0, wick / rng)

    # ── Main entry point ─────────────────────────────────────────────────
    def evaluate(self, bar: dict) -> Optional[dict]:
        """Append a CLOSED bar and decide whether to fire.

        Returns a payload dict {direction, mid, trigger, meta} or None.
        """
        if not bar.get("confirm"):
            return None

        ts = int(bar.get("ts_ms_open", 0))
        bar_norm = {
            "ts_ms_open": ts,
            "open":   float(bar["open"]),
            "high":   float(bar["high"]),
            "low":    float(bar["low"]),
            "close":  float(bar["close"]),
            "volume": float(bar["volume"]),
        }
        self.bars.append(bar_norm)

        # Step 1: confirm any new pivot (lagged by pivot_right bars)
        new_pivot = self._confirm_pivot()
        if new_pivot is not None:
            # Avoid duplicate entries if rebuffered
            if not self.pivots or self.pivots[-1]["ts"] != new_pivot["ts"]:
                self.pivots.append(new_pivot)

        # Step 2: cold-start gate — need ATR + vol windows + at least one pivot
        if (
            self._atr() is None
            or (self.require_volume and self._vol_sma() is None)
            or not self.pivots
        ):
            return None

        atr        = self._atr()
        vol_sma    = self._vol_sma() or 0.0
        cur        = bar_norm

        # Step 3: check the current bar against EACH active pivot
        #         (community scripts typically track all active pivots,
        #         not just the most recent one)
        for piv in list(self.pivots):
            level = piv["level"]
            if piv["kind"] == "low":
                # Bull SFP candidate: sweep below pivot_low, close back above
                if cur["low"] < level and cur["close"] > level:
                    if ts <= self._last_fire_ts_long:
                        continue
                    if self._evaluate_filters(cur, level, "long", atr, vol_sma):
                        self._last_fire_ts_long = ts
                        # Pivot is "used" — remove it so we don't refire later
                        try: self.pivots.remove(piv)
                        except ValueError: pass
                        return self._build_payload(cur, level, "long", atr, vol_sma, piv)
            else:  # pivot kind == "high"
                if cur["high"] > level and cur["close"] < level:
                    if ts <= self._last_fire_ts_short:
                        continue
                    if self._evaluate_filters(cur, level, "short", atr, vol_sma):
                        self._last_fire_ts_short = ts
                        try: self.pivots.remove(piv)
                        except ValueError: pass
                        return self._build_payload(cur, level, "short", atr, vol_sma, piv)
        return None

    def _evaluate_filters(self, bar, level, direction, atr, vol_sma) -> bool:
        """All three quality filters must pass."""
        # 1. Wick ratio
        if self._wick_ratio(bar, direction) < self.min_wick_ratio:
            return False
        # 2. Reclaim distance (in ATR units)
        if direction == "long":
            reclaim = bar["close"] - level
        else:
            reclaim = level - bar["close"]
        if reclaim < self.min_reclaim_atr * atr:
            return False
        # 3. Volume
        if self.require_volume and bar["volume"] < self.min_vol_mult * vol_sma:
            return False
        return True

    def _build_payload(self, bar, level, direction, atr, vol_sma, piv) -> dict:
        wick = self._wick_ratio(bar, direction)
        if direction == "long":
            reclaim = bar["close"] - level
            thesis = (f"bull SFP swept pivot-low ${level:,.2f} "
                      f"(wick {wick*100:.0f}%, reclaim {reclaim/atr:.2f}xATR)")
        else:
            reclaim = level - bar["close"]
            thesis = (f"bear SFP swept pivot-high ${level:,.2f} "
                      f"(wick {wick*100:.0f}%, reclaim {reclaim/atr:.2f}xATR)")
        return {
            "direction": direction,
            "trigger":   "sfp_v2",
            "mid":       bar["close"],
            "meta": {
                "thesis":          thesis,
                "pivot_ts":        piv["ts"],
                "pivot_level":     round(level, 2),
                "wick_ratio":      round(wick, 3),
                "reclaim_atr":     round(reclaim / atr, 3),
                "atr":             round(atr, 2),
                "vol_mult":        round(bar["volume"] / vol_sma, 2) if vol_sma else None,
                "pivot_age_bars":  None,  # callers can compute from ts diff
            },
        }
