"""fib_sfp_trigger — confluence-gated swing-failure-pattern detector.

Designed to plug into sygnif_fast_reactor.py as a new fire trigger that
replaces the dead `bounce` trigger. Fires LONG when:

  1. The most recent closed 1m bar is a bull SFP per Jules' definition
     (low pierces key_low of prior 50 bars, close back above key_low)
  2. close is within 0.3% of fib_0.618 of the rolling 240-bar range
  3. intel_summary.json has boosts_long non-empty
  4. intel_summary.json has vetoes_long empty
  5. ≥ 5 bars since the last fib_sfp fire (cluster dedup)

Bear/short path intentionally omitted — wet 7d backtest shows bear SFP
at fib_0.382 is flat-EV (+0.004%, 45.3% WR) below the fee threshold.
Revisit when BTC drift flips.

Cold-start: this trigger is silent until at least FIB_RANGE bars have
been observed (~4 hours after service start). Documented in evaluate().

Performance: pure Python (no pandas). One closed-bar tick computes
O(LOOKBACK_SFP) min/max + O(FIB_RANGE) min/max ≈ a few hundred
arithmetic ops. Well under 1 ms p99 on EC2 hardware.

Module is import-safe (no side effects, no env reads, no I/O). The
live fast-reactor instantiates one FibSfpState in its `state` dict
and routes closed-bar ticks through eval_trigger_fib_sfp_confluence().
"""
from __future__ import annotations

from collections import deque
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Parameters — exposed at module scope for tests + future env override
# ---------------------------------------------------------------------------
LOOKBACK_SFP = 50          # bars looked at for key_low / key_high
FIB_RANGE = 240            # rolling window for fib 0/100 anchors
FIB_PROXIMITY_PCT = 0.003  # close must be within this fraction of fib_0.618
COOLDOWN_BARS = 5          # min bars between fib_sfp fires
COLDSTART_MIN_BARS = FIB_RANGE  # silent until this many bars observed


def compute_fibonacci_levels(high: float, low: float) -> dict:
    """Standard Fibonacci retracement levels from a high/low pair.

    Returns dict with keys 'fib_0.0' through 'fib_1.0'. Mirrors the
    function in SygnifStrategy.py for parity.
    """
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


def detect_bull_sfp(prior_bars: list, current_bar: dict) -> bool:
    """Single-point bull SFP detector.

    prior_bars: list of LOOKBACK_SFP bars STRICTLY BEFORE current_bar.
                Each must have float 'low' and 'close'.
    current_bar: the bar to test.

    Returns True iff current_bar.low < min(prior_bars.low)
                AND current_bar.close > min(prior_bars.low).

    The strict prior-only view encodes the .shift(1) semantic in Jules'
    pandas implementation — guarantees no lookahead bias.
    """
    if not prior_bars:
        return False
    key_low = min(b["low"] for b in prior_bars)
    return current_bar["low"] < key_low and current_bar["close"] > key_low


def detect_bear_sfp(prior_bars: list, current_bar: dict) -> bool:
    """Mirror of detect_bull_sfp. Kept for completeness; not used by
    the live trigger in this design (bears are flat-EV per 7d backtest)."""
    if not prior_bars:
        return False
    key_high = max(b["high"] for b in prior_bars)
    return current_bar["high"] > key_high and current_bar["close"] < key_high


class FibSfpState:
    """Stateful bull-SFP-at-fib_0.618 detector with cluster dedup.

    One instance lives in fast-reactor's shared state for its lifetime.
    .on_bar() pushes closed bars in. .evaluate() returns a signal dict
    or None on each call. .mark_fired() is called by the caller after
    actually firing the trade so we can enforce the cooldown.
    """

    def __init__(self) -> None:
        self.bars: deque = deque(maxlen=FIB_RANGE)
        self.bar_count: int = 0          # monotonic — counts all bars ever seen
        self.last_fire_bar_count: int = -10**9   # so first fire isn't blocked

    # --- ingestion ---------------------------------------------------------
    def on_bar(self, bar: dict) -> None:
        """Push a closed bar. Caller must pre-filter for bar.get('confirm')."""
        self.bars.append(bar)
        self.bar_count += 1

    # --- introspection -----------------------------------------------------
    def ready(self) -> bool:
        """True after enough warm-up bars to compute reliable fib levels."""
        return self.bar_count >= COLDSTART_MIN_BARS

    def fib_618(self) -> float:
        """Current rolling fib_0.618 level over PRIOR bars only.

        Uses bars[:-1] (everything except the most recently pushed bar)
        for the same reason detect_bull_sfp uses .shift(1) — the level
        we test the current bar against must be defined by the context
        BEFORE that bar, otherwise the very bar piercing the level also
        redefines where the level sits.
        """
        prior = list(self.bars)[:-1]
        if not prior:
            return 0.0
        h = max(b["high"] for b in prior)
        l = min(b["low"]  for b in prior)
        return h - 0.618 * (h - l)

    # --- core evaluation ---------------------------------------------------
    def evaluate(self) -> Optional[dict]:
        """Return a signal dict if all confluence pre-conditions pass.

        Pre-conditions (in order, cheap → expensive):
          1. coldstart complete (≥ FIB_RANGE bars seen)
          2. ≥ COOLDOWN_BARS since last fire
          3. current bar is a bull SFP relative to prior LOOKBACK_SFP bars
          4. close is within FIB_PROXIMITY_PCT of fib_0.618

        Note: this evaluator does NOT check intel — that's the caller's
        job, because intel reads are mtime-cached at a higher level and
        we don't want to couple cache layers.

        The caller still has gates beyond this evaluator: circuit
        breaker, hourly cap, open-position cap, and M5 momentum veto
        inside fire_trade(). Confluence here is purely
        signal-quality, not risk.
        """
        if not self.ready():
            return None
        if self.bar_count - self.last_fire_bar_count < COOLDOWN_BARS:
            return None
        if len(self.bars) < LOOKBACK_SFP + 1:
            return None
        prior = list(self.bars)[-LOOKBACK_SFP - 1:-1]
        current = self.bars[-1]
        if not detect_bull_sfp(prior, current):
            return None
        fib = self.fib_618()
        close = current["close"]
        if close <= 0:
            return None
        dist_pct = abs(close - fib) / close
        if dist_pct > FIB_PROXIMITY_PCT:
            return None
        return {
            "close":            close,
            "fib_0.618":        fib,
            "fib_distance_pct": dist_pct * 100,
            "key_low":          min(b["low"] for b in prior),
            "sfp_bar_ts":       current.get("ts"),
            "n_bars_seen":      self.bar_count,
            "thesis":           (f"bull SFP swept key_low; close ${close:.0f} "
                                  f"within {dist_pct*100:.2f}% of fib_0.618 ${fib:.0f}"),
        }

    def mark_fired(self) -> None:
        """Caller calls this after a successful fire_trade()."""
        self.last_fire_bar_count = self.bar_count


# ---------------------------------------------------------------------------
# Integration glue
# ---------------------------------------------------------------------------
def eval_trigger_fib_sfp_confluence(
    bar: dict,
    state: dict,
    check_intel_for_direction: Callable[[str], tuple],
    fire_trade: Callable[[str, str, float, dict], Any],
) -> None:
    """Drop-in trigger for sygnif_fast_reactor.on_message().

    Call signature mirrors eval_trigger_momentum / eval_trigger_whale.
    Caller must guarantee bar.get('confirm') is True (closed bar).

    state is fast-reactor's shared state dict. We use:
      state['fib_sfp_state']    — FibSfpState instance (created lazily)
    """
    if not bar.get("confirm"):
        return

    fib_state = state.get("fib_sfp_state")
    if fib_state is None:
        fib_state = FibSfpState()
        state["fib_sfp_state"] = fib_state

    fib_state.on_bar(bar)

    sig = fib_state.evaluate()
    if sig is None:
        return

    # Intel gate: must have a long boost AND no veto.
    # The existing check_intel_for_direction returns (allow, reason, modifier).
    # allow=False means veto present. reason starts with "intel_boost:" when
    # boosts_long is non-empty.
    allow, reason, conf_modifier = check_intel_for_direction("long")
    if not allow:
        return
    if not reason.startswith("intel_boost"):
        # Neutral intel: don't fire on SFP alone (fee-EV negative per backtest).
        return

    # Mark fired BEFORE calling fire_trade so cooldown works even if
    # fire_trade is async / re-entrant.
    fib_state.mark_fired()

    fire_trade("long", "fib_sfp_conf", sig["close"], {
        **sig,
        "intel_reason":    reason,
        "intel_conf_mod":  conf_modifier,
        "trigger_design":  "fib_sfp_confluence_v1",
    })
