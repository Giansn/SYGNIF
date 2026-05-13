"""Synthetic-fixture tests for fib_sfp_trigger.

Each test builds a controlled OHLC sequence and verifies:
  - bull SFP detection itself (the .shift(1) safety)
  - fib_0.618 distance gating (0.3% threshold)
  - intel-boost gating
  - cluster dedup (5-bar cooldown)

No external dependencies. Run with: python -m pytest -q test_fib_sfp_confluence.py
"""
import sys
import os
from pathlib import Path

# Make the trigger module importable
sys.path.insert(0, str(Path(__file__).parent))
from fib_sfp_trigger import (
    FibSfpState,
    detect_bull_sfp,
    detect_bear_sfp,
    compute_fibonacci_levels,
    eval_trigger_fib_sfp_confluence,
    LOOKBACK_SFP,
    FIB_RANGE,
    FIB_PROXIMITY_PCT,
    COOLDOWN_BARS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def bar(o, h, l, c, v=100.0, ts=0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v,
            "ts": ts, "confirm": True}

def flat_bars(n, price=80_000.0):
    return [bar(price, price + 5, price - 5, price, ts=i * 60) for i in range(n)]


# ---------------------------------------------------------------------------
# Unit-level: detect_bull_sfp
# ---------------------------------------------------------------------------
def test_detect_bull_sfp_positive():
    """Bull SFP: low pierces prior_min, close back above."""
    prior = [bar(100, 102, 99, 101) for _ in range(LOOKBACK_SFP)]
    # current bar: dips to 98 (below prior min 99), closes at 100 (above 99)
    current = bar(100, 100.5, 98, 100)
    assert detect_bull_sfp(prior, current) is True

def test_detect_bull_sfp_negative_no_pierce():
    """No pierce → no SFP."""
    prior = [bar(100, 102, 99, 101) for _ in range(LOOKBACK_SFP)]
    current = bar(100, 100.5, 99.5, 100)   # low 99.5 ≥ 99
    assert detect_bull_sfp(prior, current) is False

def test_detect_bull_sfp_negative_close_below():
    """Pierce but close also below → not a successful SFP (it's a breakdown)."""
    prior = [bar(100, 102, 99, 101) for _ in range(LOOKBACK_SFP)]
    current = bar(100, 100, 98, 98.5)   # low 98 < 99 but close 98.5 < 99
    assert detect_bull_sfp(prior, current) is False

def test_detect_bear_sfp_positive():
    """Mirror: bear SFP."""
    prior = [bar(100, 102, 99, 101) for _ in range(LOOKBACK_SFP)]
    current = bar(100, 103, 100, 101.5)  # high 103 > prior max 102, close 101.5 < 102
    assert detect_bear_sfp(prior, current) is True


# ---------------------------------------------------------------------------
# FibSfpState — cold start
# ---------------------------------------------------------------------------
def test_coldstart_blocks_eval():
    """Until FIB_RANGE bars are seen, evaluate() returns None even on a
    perfect SFP-at-fib setup. This prevents firing on unreliable fib levels."""
    s = FibSfpState()
    # Push (FIB_RANGE - 2) warmup bars then the SFP bar → total FIB_RANGE - 1,
    # which is 1 short of the COLDSTART_MIN_BARS threshold.
    for b in flat_bars(FIB_RANGE - 2, price=80_000):
        s.on_bar(b)
    s.on_bar(bar(80_000, 80_001, 79_800, 79_900))   # would otherwise trigger
    assert s.evaluate() is None
    assert not s.ready()


# ---------------------------------------------------------------------------
# FibSfpState — fib distance gate
# ---------------------------------------------------------------------------
def _build_range_with_sfp_at_fib(target_fib_618: float = None, n_warmup=FIB_RANGE):
    """Build a synthetic range that places a bull SFP at a specific fib_0.618.

    We arrange:
      - First (n_warmup-1) bars oscillating between high=100 and low=80
        → range diff = 20 → fib_0.618 = 100 - 0.618*20 = 87.64
      - Last bar: close exactly at fib_0.618, low piercing the prior min
        but close > prior min (perfect bull SFP)
    """
    bars = []
    for i in range(n_warmup - 1):
        # Alternate to maintain the full range
        if i % 2 == 0:
            bars.append(bar(90, 100, 85, 92, ts=i * 60))   # touches 100, lows 85
        else:
            bars.append(bar(92, 95, 80, 90, ts=i * 60))    # touches 80, highs 95
    # Now range is high=100, low=80; fib_0.618 = 87.64
    return bars

def test_fires_when_sfp_at_fib():
    """Plant a bull SFP with close ≈ fib_0.618 → evaluate() returns signal."""
    bars = _build_range_with_sfp_at_fib(n_warmup=FIB_RANGE)
    s = FibSfpState()
    for b in bars: s.on_bar(b)
    # Sanity: fib_0.618 should be ~87.64
    fib = s.fib_618()
    assert 87.0 < fib < 88.5, f"unexpected fib_0.618 = {fib}"

    # SFP requires piercing the prior LOOKBACK_SFP min (in this fixture, the min is 80)
    # Construct a bar that pierces 80 + closes above 80 + close near fib (~87.64)
    sfp = bar(90, 90.5, 79.5, 87.6, ts=FIB_RANGE * 60)   # low 79.5 < 80 ; close 87.6 ≈ fib
    s.on_bar(sfp)
    sig = s.evaluate()
    assert sig is not None, "expected a signal"
    assert sig["fib_distance_pct"] < FIB_PROXIMITY_PCT * 100
    assert sig["close"] == 87.6

def test_fires_blocked_by_fib_distance():
    """Same SFP but close is 0.5% off from fib_0.618 → no fire (above 0.3% gate)."""
    bars = _build_range_with_sfp_at_fib(n_warmup=FIB_RANGE)
    s = FibSfpState()
    for b in bars: s.on_bar(b)
    fib = s.fib_618()
    # Place close at fib * 1.005 — that's 0.5% above fib, well above 0.3% threshold
    far_close = fib * 1.005
    sfp = bar(90, far_close + 0.1, 79.5, far_close, ts=FIB_RANGE * 60)
    s.on_bar(sfp)
    assert s.evaluate() is None, "expected no signal when close is >0.3% from fib"


# ---------------------------------------------------------------------------
# Cooldown / cluster dedup
# ---------------------------------------------------------------------------
def test_cooldown_dedup():
    """Two SFP bars within COOLDOWN_BARS → only the first fires."""
    bars = _build_range_with_sfp_at_fib(n_warmup=FIB_RANGE)
    s = FibSfpState()
    for b in bars: s.on_bar(b)
    fib = s.fib_618()
    target = fib  # exactly at fib_0.618

    # First SFP bar — should fire
    s.on_bar(bar(90, target + 0.1, 79.5, target, ts=FIB_RANGE * 60))
    sig1 = s.evaluate()
    assert sig1 is not None, "first SFP should fire"
    s.mark_fired()

    # Three more SFP bars within cooldown window — should be silent
    for i in range(1, 4):
        s.on_bar(bar(target, target + 0.1, 79.5, target, ts=(FIB_RANGE + i) * 60))
        sig = s.evaluate()
        assert sig is None, f"SFP {i} bars after fire should be in cooldown"


# ---------------------------------------------------------------------------
# Top-level integration: intel gate
# ---------------------------------------------------------------------------
class _MockState(dict):
    """fast-reactor-shaped state dict."""

def test_intel_gate_no_boost_blocks_fire():
    """SFP + fib pass but intel is neutral → no fire."""
    bars = _build_range_with_sfp_at_fib(n_warmup=FIB_RANGE)
    state = _MockState()
    for b in bars:
        eval_trigger_fib_sfp_confluence(b, state, _intel_neutral, _capture_fire(state))

    # Now push the SFP-at-fib bar
    fib_state = state["fib_sfp_state"]
    fib = fib_state.fib_618()
    sfp = bar(90, fib + 0.1, 79.5, fib, ts=FIB_RANGE * 60)
    eval_trigger_fib_sfp_confluence(sfp, state, _intel_neutral, _capture_fire(state))
    assert state.get("fired") is None, "neutral intel should not fire"

def test_intel_gate_veto_blocks_fire():
    """SFP + fib pass but intel vetoes long → no fire."""
    bars = _build_range_with_sfp_at_fib(n_warmup=FIB_RANGE)
    state = _MockState()
    for b in bars:
        eval_trigger_fib_sfp_confluence(b, state, _intel_veto, _capture_fire(state))

    fib_state = state["fib_sfp_state"]
    fib = fib_state.fib_618()
    sfp = bar(90, fib + 0.1, 79.5, fib, ts=FIB_RANGE * 60)
    eval_trigger_fib_sfp_confluence(sfp, state, _intel_veto, _capture_fire(state))
    assert state.get("fired") is None, "vetoed direction should not fire"

def test_intel_gate_boost_fires():
    """SFP + fib + intel_boost present → fires."""
    bars = _build_range_with_sfp_at_fib(n_warmup=FIB_RANGE)
    state = _MockState()
    for b in bars:
        eval_trigger_fib_sfp_confluence(b, state, _intel_boost, _capture_fire(state))

    fib_state = state["fib_sfp_state"]
    fib = fib_state.fib_618()
    sfp = bar(90, fib + 0.1, 79.5, fib, ts=FIB_RANGE * 60)
    eval_trigger_fib_sfp_confluence(sfp, state, _intel_boost, _capture_fire(state))
    fired = state.get("fired")
    assert fired is not None, "boost + SFP + fib should fire"
    assert fired["direction"] == "long"
    assert fired["trigger"] == "fib_sfp_conf"


# ---------------------------------------------------------------------------
# Mock intel + fire ---------------------------------------------------------
def _intel_neutral(direction):    return (True,  "neutral",                1.0)
def _intel_veto(direction):       return (False, "intel_veto:cold_accum",  0.0)
def _intel_boost(direction):      return (True,  "intel_boost:cold_accum_4h:31",  1.3)

def _capture_fire(state):
    def _fire(direction, trigger, mid, meta):
        state["fired"] = {"direction": direction, "trigger": trigger,
                           "mid": mid, "meta": meta}
        return {"ok": True}
    return _fire


if __name__ == "__main__":
    # Standalone runner — run as: python test_fib_sfp_confluence.py
    import sys
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0; failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n  {passed}/{len(tests)} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
