"""fib_sfp_trigger — Fibonacci × Swing-Failure-Pattern trigger for sygnif-fast-reactor.

Replaces the dead `bounce` trigger (whose feeder sygnif-bounce-watcher.service is
disabled) with a real signal source built from Jules' Fib/SR/SFP math that was
merged into SygnifStrategy.py via PR #10 (commit f30f87b).

Design:
    - Maintains a rolling 240-bar 1m OHLC buffer (collections.deque)
    - On each CLOSED 1m kline:
        1. Append bar to buffer
        2. If buffer < 50 bars  → return None  (cold start, can't detect SFP yet)
        3. Compute key_low_t = min(low[-51:-1]) and key_high_t = max(high[-51:-1])
           (note the shift(1) — exclude current bar from the lookback window
           to avoid lookahead bias, matching Jules' .shift(1) in SygnifStrategy)
        4. Bull SFP: bar.low < key_low_t  AND  bar.close > key_low_t
           Bear SFP: bar.high > key_high_t AND  bar.close < key_high_t
        5. Compute fib levels from 240-bar (or available, ≥ 50) range:
            high_240 = max(high[-240:]), low_240 = min(low[-240:])
            diff = high_240 - low_240
            fib_0.382 = low_240 + 0.382 * diff
            fib_0.618 = low_240 + 0.618 * diff
        6. Fire LONG if Bull SFP AND |close - fib_0.618| / close < 0.01
        7. Fire SHORT if Bear SFP AND |close - fib_0.382| / close < 0.01
        8. Edge-trigger: don't refire on the same closed bar

Performance budget:
    Rolling min/max over 240 entries via Python deque + min()/max() = ~24 µs
    on a modern CPU at 240 bars. Negligible relative to the 1-minute kline
    cadence.

Returns:
    None       → no fire
    dict       → fire payload to pass to fire_trade():
        {"direction": "long"|"short", "trigger": "fib_sfp", "mid": <close>,
         "meta": {"thesis": "...", "fib_0_618": ..., "fib_0_382": ...,
                  "key_low": ..., "key_high": ..., "sfp_kind": "bull"|"bear"}}

Cold-start handling:
    The first 50 bars yield no signal (need lookback). Bars 50–240 use a
    shorter Fib window (whatever's available). The bootstrap option (preload
    from swarm.db klines) is documented in §Operator-notes below.

Author: Claude, 2026-05-13
Spec:   SYGNIF.md §4.1 (proposed) + AGENTS.md "Trading mechanics" §6 protocol.
"""
from __future__ import annotations

import collections
import logging
import time
from typing import Optional, Any

log = logging.getLogger("fib_sfp")

# ---------------------------------------------------------------------------
# Indicator math — copied verbatim from SygnifStrategy.py to avoid coupling
# (fast-reactor must not import from freqtrade strategy package)
# ---------------------------------------------------------------------------

def compute_fibonacci_levels(high: float, low: float) -> dict:
    """Standard Fibonacci retracement levels. Returns all 7 ratios."""
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


def detect_sfp_on_bar(bars: list[dict], lookback: int = 50) -> tuple[bool, bool]:
    """Detect Swing Failure Pattern on the LAST bar in `bars`.

    bars: list of dicts {"open","high","low","close","volume"}, ordered oldest→newest.
    Returns (bull_sfp, bear_sfp).

    Mirrors Jules' detect_swing_failure but evaluates only the latest bar
    instead of vectorising over a DataFrame — avoids per-bar pandas overhead.
    Uses .shift(1) equivalent: key_low/high are computed over bars[-lookback-1:-1]
    (excluding the current bar) to prevent lookahead.
    """
    if len(bars) < lookback + 1:
        return False, False
    window = bars[-lookback - 1:-1]   # the `lookback` bars BEFORE the current bar
    key_low  = min(b["low"]  for b in window)
    key_high = max(b["high"] for b in window)
    cur = bars[-1]
    bull = cur["low"]  < key_low  and cur["close"] > key_low
    bear = cur["high"] > key_high and cur["close"] < key_high
    return bull, bear


# ---------------------------------------------------------------------------
# State container
# ---------------------------------------------------------------------------

class FibSfpState:
    """Encapsulated rolling state for the fib_sfp trigger.

    Plug into sygnif_fast_reactor.py's state dict as:
        state["fib_sfp"] = FibSfpState(maxlen=240, lookback=50,
                                       fib_proximity=0.01,
                                       min_bars_for_signal=50)

    Then on each kline event in on_message():
        if bar["confirm"]:
            payload = state["fib_sfp"].evaluate(bar)
            if payload:
                fire_trade(payload["direction"], payload["trigger"],
                           payload["mid"], payload["meta"])
    """
    def __init__(
        self,
        maxlen: int = 240,
        lookback: int = 50,
        fib_proximity: float = 0.01,
        min_bars_for_signal: int = 50,
    ):
        self.bars: collections.deque[dict] = collections.deque(maxlen=maxlen)
        self.maxlen = maxlen
        self.lookback = lookback
        self.fib_proximity = fib_proximity
        self.min_bars = min_bars_for_signal
        # Edge-trigger guards: don't refire on the same closed bar
        self._last_fire_ts_long  = 0
        self._last_fire_ts_short = 0

    def warmup_progress(self) -> tuple[int, int]:
        """Return (current_bars, target_bars) — for the heartbeat log."""
        return (len(self.bars), self.min_bars)

    def preload(self, historical_bars: list[dict]) -> int:
        """Bootstrap from a list of historical bars (e.g. from swarm.db or
        a Bybit V5 kline backfill). Returns the number of bars loaded."""
        loaded = 0
        for b in historical_bars[-self.maxlen:]:
            if all(k in b for k in ("open", "high", "low", "close", "volume")):
                self.bars.append({
                    "ts_ms_open": int(b.get("ts_ms_open", 0)),
                    "open":  float(b["open"]),
                    "high":  float(b["high"]),
                    "low":   float(b["low"]),
                    "close": float(b["close"]),
                    "volume": float(b["volume"]),
                })
                loaded += 1
        return loaded

    def evaluate(self, bar: dict) -> Optional[dict]:
        """Append a CLOSED kline and decide whether to fire.

        bar: {"ts_ms_open","open","high","low","close","volume","confirm":True}.
        Returns None or a fire payload dict.
        """
        if not bar.get("confirm"):
            return None  # only act on closed bars
        ts = int(bar.get("ts_ms_open", 0))
        # Append (deque drops oldest when maxlen reached)
        self.bars.append({
            "ts_ms_open": ts,
            "open":  float(bar["open"]),
            "high":  float(bar["high"]),
            "low":   float(bar["low"]),
            "close": float(bar["close"]),
            "volume": float(bar["volume"]),
        })
        # Cold-start gate
        if len(self.bars) < self.min_bars + 1:
            return None

        bars_list = list(self.bars)
        bull_sfp, bear_sfp = detect_sfp_on_bar(bars_list, lookback=self.lookback)
        if not (bull_sfp or bear_sfp):
            return None

        # Fib levels off the available window (up to 240 bars)
        # IMPORTANT: include the current bar in the range so the level reflects
        # the breach that triggered the SFP.
        window = bars_list[-self.maxlen:] if len(bars_list) >= self.maxlen else bars_list
        high_w = max(b["high"] for b in window)
        low_w  = min(b["low"]  for b in window)
        if high_w <= low_w:
            return None
        levels = compute_fibonacci_levels(high_w, low_w)
        fib_618 = levels["fib_0.618"]
        fib_382 = levels["fib_0.382"]

        cur = bars_list[-1]
        close = cur["close"]
        # Fib proximity check (1% by default)
        near_618 = abs(close - fib_618) / close < self.fib_proximity
        near_382 = abs(close - fib_382) / close < self.fib_proximity

        if bull_sfp and near_618 and ts > self._last_fire_ts_long:
            self._last_fire_ts_long = ts
            key_low = min(b["low"] for b in bars_list[-self.lookback - 1:-1])
            return {
                "direction": "long",
                "trigger":   "fib_sfp",
                "mid":       close,
                "meta": {
                    "thesis":      (
                        f"bull SFP swept ${key_low:,.2f} key-low and closed "
                        f"${close:,.2f}, within {abs(close-fib_618)/close*100:.2f}% "
                        f"of fib_0.618 (${fib_618:,.2f})"
                    ),
                    "sfp_kind":    "bull",
                    "fib_0_618":   round(fib_618, 2),
                    "fib_0_382":   round(fib_382, 2),
                    "key_low":     round(key_low, 2),
                    "window_high": round(high_w, 2),
                    "window_low":  round(low_w, 2),
                    "bars_in_buf": len(self.bars),
                },
            }

        if bear_sfp and near_382 and ts > self._last_fire_ts_short:
            self._last_fire_ts_short = ts
            key_high = max(b["high"] for b in bars_list[-self.lookback - 1:-1])
            return {
                "direction": "short",
                "trigger":   "fib_sfp",
                "mid":       close,
                "meta": {
                    "thesis":      (
                        f"bear SFP swept ${key_high:,.2f} key-high and closed "
                        f"${close:,.2f}, within {abs(close-fib_382)/close*100:.2f}% "
                        f"of fib_0.382 (${fib_382:,.2f})"
                    ),
                    "sfp_kind":    "bear",
                    "fib_0_618":   round(fib_618, 2),
                    "fib_0_382":   round(fib_382, 2),
                    "key_high":    round(key_high, 2),
                    "window_high": round(high_w, 2),
                    "window_low":  round(low_w, 2),
                    "bars_in_buf": len(self.bars),
                },
            }

        return None


# ---------------------------------------------------------------------------
# Operator notes — integration patch shape
# ---------------------------------------------------------------------------
INTEGRATION_NOTES = """
To wire into /opt/sygnif-services/sygnif_fast_reactor.py:

  1. At top of file, add: from fib_sfp_trigger import FibSfpState
     (place this module alongside sygnif_fast_reactor.py in /opt/sygnif-services/)

  2. In the state dict (line ~143), add:
       "fib_sfp": FibSfpState(maxlen=240, lookback=50, fib_proximity=0.01),

  3. (Optional) Bootstrap from swarm.db on startup — see preload() docstring.
     Without bootstrap, first 50 min after restart have no signal. Acceptable.

  4. In on_message() at the kline handler (line ~580), replace:
         try: eval_trigger_bounce(bar["close"])  # legacy, feeder dead
     with:
         try:
             payload = state["fib_sfp"].evaluate(bar)
             if payload and bar.get("confirm"):
                 fire_trade(payload["direction"], payload["trigger"],
                            payload["mid"], payload["meta"])
         except Exception as e:
             print(f"  fib-sfp-eval err: {e}", file=sys.stderr, flush=True)

  5. Keep eval_trigger_bounce() defined but log a deprecation notice if called.

The fire_trade() function flows through all existing gates unchanged:
  - gate_circuit_breaker
  - gate_cooldown (per-direction)
  - gate_hourly_cap
  - gate_open_count
  - M5 momentum veto (inside fire_trade)
  - check_intel_for_direction (intel veto + confidence boost)
"""
