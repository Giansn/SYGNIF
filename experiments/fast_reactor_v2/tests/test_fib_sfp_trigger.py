"""Unit tests for fib_sfp_trigger.

Test matrix:
    1. bootstrap bars below cold-start threshold → None
    2. bars present but no SFP                    → None
    3. Bull SFP + price near fib_0.618           → fire LONG
    4. Bear SFP + price near fib_0.382           → fire SHORT
    5. Bull SFP but price NOT near fib            → None
    6. Bull SFP + near fib_0.618, same bar twice → None on 2nd call (edge-trigger)
    7. .shift(1) lookahead-bias guard            — key_low excludes current bar
    8. Performance smoke — evaluate() < 1 ms on 240-bar buffer
"""
import sys, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fib_sfp_trigger import FibSfpState, compute_fibonacci_levels, detect_sfp_on_bar


def make_bar(o, h, l, c, ts_ms=0, vol=10.0, confirm=True):
    return {"ts_ms_open": ts_ms, "open": o, "high": h, "low": l,
            "close": c, "volume": vol, "confirm": confirm}


def flat_bars(n, price=80000.0, ts_start=1000):
    """Bars of trivial ±$5 noise around `price`."""
    out = []
    for i in range(n):
        out.append(make_bar(
            o=price - 2, h=price + 5, l=price - 5, c=price + 1,
            ts_ms=(ts_start + i) * 60_000,
        ))
    return out


# ---------------------------------------------------------------------------
# Test 1 — cold start: < 50 bars yields no signal
# ---------------------------------------------------------------------------
def test_cold_start_no_fire():
    s = FibSfpState(maxlen=240, lookback=50, min_bars_for_signal=50)
    for b in flat_bars(40):
        assert s.evaluate(b) is None
    assert s.warmup_progress() == (40, 50)
    print("  [1] cold-start (40 < 50 bars): no fire  ✓")


# ---------------------------------------------------------------------------
# Test 2 — bars present, no SFP pattern, no fire
# ---------------------------------------------------------------------------
def test_no_sfp_no_fire():
    s = FibSfpState(maxlen=240, lookback=50)
    for b in flat_bars(60):                 # all bars within tight range
        s.evaluate(b)
    # Last bar is also tight — no sweep of key_low or key_high
    assert s.evaluate(make_bar(79999, 80005, 79995, 80001, ts_ms=61*60_000)) is None
    print("  [2] 60 flat bars + 1 flat bar: no fire  ✓")


# ---------------------------------------------------------------------------
# Test 3 — Bull SFP at fib_0.618, fires LONG
# ---------------------------------------------------------------------------
def test_bull_sfp_at_fib_618_fires_long():
    s = FibSfpState(maxlen=240, lookback=50, fib_proximity=0.02)
    # Build a 240-bar history with range [78000, 82000] → fib_0.618 = 80472
    # Use bars that DO NOT make a new low (we don't want lookback to drift)
    # Range bars: oscillate within [78050, 81950]
    for i in range(240):
        s.evaluate(make_bar(
            o=80000, h=81950 if i % 2 else 79900,
            l=78050 if i % 2 else 80100, c=80000, ts_ms=i * 60_000,
        ))
    # Now build the SFP setup bar:
    # bar low must be BELOW key_low (78050), close must be ABOVE key_low
    # AND close must be within 2% of fib_0.618 (80472)
    # so close = ~80472, low = 78040 (sweep), high = 80500
    bar = make_bar(o=80000, h=80500, l=78040, c=80472, ts_ms=241 * 60_000)
    p = s.evaluate(bar)
    assert p is not None, f"expected fire, got None. buf={s.warmup_progress()}"
    assert p["direction"] == "long"
    assert p["trigger"] == "fib_sfp"
    assert p["meta"]["sfp_kind"] == "bull"
    assert abs(p["mid"] - 80472) < 1
    assert "fib_0.618" in str(p["meta"]["fib_0_618"]) or p["meta"]["fib_0_618"] > 78000
    print(f"  [3] bull SFP @ ~fib_0.618: fired LONG mid={p['mid']:.2f}  "
          f"fib_618={p['meta']['fib_0_618']}  key_low={p['meta']['key_low']}  ✓")


# ---------------------------------------------------------------------------
# Test 4 — Bear SFP at fib_0.382, fires SHORT
# ---------------------------------------------------------------------------
def test_bear_sfp_at_fib_382_fires_short():
    s = FibSfpState(maxlen=240, lookback=50, fib_proximity=0.02)
    # Same setup: range [78000, 82000] → fib_0.382 = 79528
    for i in range(240):
        s.evaluate(make_bar(
            o=80000, h=81950 if i % 2 else 79900,
            l=78050 if i % 2 else 80100, c=80000, ts_ms=i * 60_000,
        ))
    # Bear SFP: high above key_high (81950), close below key_high
    # AND close within 2% of fib_0.382 (79528)
    bar = make_bar(o=80000, h=82010, l=79500, c=79528, ts_ms=241 * 60_000)
    p = s.evaluate(bar)
    assert p is not None
    assert p["direction"] == "short"
    assert p["meta"]["sfp_kind"] == "bear"
    print(f"  [4] bear SFP @ ~fib_0.382: fired SHORT mid={p['mid']:.2f}  "
          f"fib_382={p['meta']['fib_0_382']}  key_high={p['meta']['key_high']}  ✓")


# ---------------------------------------------------------------------------
# Test 5 — Bull SFP but price NOT near fib — no fire
# ---------------------------------------------------------------------------
def test_sfp_far_from_fib_no_fire():
    s = FibSfpState(maxlen=240, lookback=50, fib_proximity=0.01)
    for i in range(240):
        s.evaluate(make_bar(o=80000, h=81950 if i % 2 else 79900,
                            l=78050 if i % 2 else 80100, c=80000, ts_ms=i * 60_000))
    # Bull SFP triggers (low < key_low, close > key_low) but close is
    # at 78100, which is FAR from fib_0.618 (~80472)
    bar = make_bar(o=80000, h=78200, l=78040, c=78100, ts_ms=241 * 60_000)
    p = s.evaluate(bar)
    assert p is None, f"expected None (far from fib), got {p}"
    print("  [5] SFP fires but close far from fib_0.618: no fire  ✓")


# ---------------------------------------------------------------------------
# Test 6 — Edge-trigger: same bar fires only once
# ---------------------------------------------------------------------------
def test_edge_trigger_no_refire_same_bar():
    s = FibSfpState(maxlen=240, lookback=50, fib_proximity=0.02)
    for i in range(240):
        s.evaluate(make_bar(o=80000, h=81950 if i % 2 else 79900,
                            l=78050 if i % 2 else 80100, c=80000, ts_ms=i * 60_000))
    bar = make_bar(o=80000, h=80500, l=78040, c=80472, ts_ms=241 * 60_000)
    p1 = s.evaluate(bar)
    p2 = s.evaluate(bar)   # same ts_ms_open → must not refire
    assert p1 is not None and p2 is None
    print("  [6] edge-trigger guard (same ts): 1st fires, 2nd None  ✓")


# ---------------------------------------------------------------------------
# Test 7 — .shift(1) equivalence: key_low excludes the current bar
# ---------------------------------------------------------------------------
def test_no_lookahead_bias():
    """If the current bar IS the new low, key_low should NOT include it.
    Otherwise SFP would never trigger (low can't be < key_low if it is key_low)."""
    bars = []
    for i in range(60):
        bars.append({"open":80000, "high":80100, "low":79900 + (i % 5),
                     "close":80000, "volume":10})
    # Now the LAST bar makes a brand-new low at 79000
    bars.append({"open":80000, "high":80050, "low":79000, "close":79950, "volume":10})

    bull, bear = detect_sfp_on_bar(bars, lookback=50)
    # key_low over bars[-51:-1] = min(low among 60 prior bars) = 79900
    # current bar low = 79000 < 79900 → sweep, close (79950) > 79900 → bull SFP
    assert bull is True, f"expected bull SFP, key_low excludes current bar"
    print("  [7] .shift(1) guard — key_low excludes current bar  ✓")


# ---------------------------------------------------------------------------
# Test 8 — Performance: < 1 ms p99 per evaluate() on full 240-bar buffer
# ---------------------------------------------------------------------------
def test_perf_under_1ms_p99():
    s = FibSfpState(maxlen=240, lookback=50)
    # Warmup
    for b in flat_bars(240):
        s.evaluate(b)
    # Measure 1000 evaluations
    samples = []
    for i in range(1000):
        b = make_bar(80000 - i % 7, 80100, 79900, 80000 + i % 5, ts_ms=(1000 + i) * 60_000)
        t0 = time.perf_counter_ns()
        s.evaluate(b)
        samples.append(time.perf_counter_ns() - t0)
    samples.sort()
    p50 = samples[500] / 1000  # µs
    p95 = samples[950] / 1000
    p99 = samples[990] / 1000
    print(f"  [8] evaluate() latency  p50={p50:>5.1f}µs  p95={p95:>5.1f}µs  p99={p99:>5.1f}µs  ", end="")
    assert p99 < 1000, f"p99 = {p99:.1f}µs exceeded 1 ms budget"
    print("✓ (< 1 ms p99)")


if __name__ == "__main__":
    print("Running fib_sfp_trigger tests...\n")
    test_cold_start_no_fire()
    test_no_sfp_no_fire()
    test_bull_sfp_at_fib_618_fires_long()
    test_bear_sfp_at_fib_382_fires_short()
    test_sfp_far_from_fib_no_fire()
    test_edge_trigger_no_refire_same_bar()
    test_no_lookahead_bias()
    test_perf_under_1ms_p99()
    print("\n  ALL 8 TESTS PASSED")
