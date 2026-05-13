# fast_reactor_v2 — design study, NOT FOR DEPLOY

This directory contains the proposed `fib_sfp_confluence` trigger and the
momentum-threshold tightening described in the spawned task. **Both
proposals FAILED their acceptance gates on a 7-day BTCUSDT backtest.**
This README documents the negative result and proposes the next moves.

## What was tested

| Design | Signals/week | Win rate | EV gross | EV net (0.10% fees) | Verdict |
|---|---|---|---|---|---|
| Raw bull SFP, no filter | 256 | 48.7 % | +0.017 % | −0.083 % | fail (baseline) |
| **fib_sfp_confluence v1** (SFP + 0.3 % fib + 5-bar dedup + 12 h intel surrogate) | **30.2** | **45.5 %** | **−0.041 %** | **−0.141 %** | **FAIL** |
| Lowered momentum (0.2 % / vol×1.2) | 27 | 27.6 % | −0.091 % | −0.191 % | **FAIL** |

Gate (per task spec):
- ≥ 50 % win rate
- ≥ 5 fires/week AND ≤ 30 fires/week
- EV net (after 0.10 % round-trip taker) > 0

**Best single combo in the TP/SL sensitivity sweep**: 0.30 % TP / 0.20 %
SL / 30 min hold on fib_sfp_confluence → 57.6 % WR but still
−0.107 % EV net.

## Root cause

Two reinforcing problems:

1. **Fee structure is the dominant headwind.** Round-trip taker fees of
   0.10 % swamp the raw +0.017 % SFP edge by 6×. The signal IS positive
   pre-fees on long side (Bull SFP raw: +0.017 % EV), but no parameter
   combo recovers that into net-positive territory.

2. **SFP + intel boost is anti-correlated by construction.**
   - Bull SFP fires at the END of a pullback (low pierces support, close
     comes back). At that moment, short-term momentum is bearish.
   - The 60-bar trend filter we initially tried REJECTED most SFPs.
   - The 12-hour trend filter we settled on accepts SFPs but doesn't
     filter out the bad ones — WR went from 48.7 % raw → 45.5 % filtered
     (worse, not better).

The wet-data lesson: confluence between a mean-reversion signal (SFP)
and a trend filter (intel boost) needs careful design. Adding any trend
filter to a counter-trend entry trigger either rejects all signals or
keeps the bad ones.

## What still ships from this work

| Artifact | Status | Why keep |
|---|---|---|
| `fib_sfp_trigger.py` | ✓ tested, 11/11 passing | Clean implementation of SFP + fib detection. Useful even if not wired live. Can be invoked from a future analysis or as a confluence INPUT (not a primary trigger). |
| `test_fib_sfp_confluence.py` | ✓ green | 11 unit tests covering detector, fib gate, cooldown, intel gates. Reusable. |
| `backtest_confluence.py` | ✓ runs | Repeatable acceptance-gate evidence. Useful for future re-evaluation if regime changes. |
| `sensitivity_sweep.py` | ✓ runs | TP/SL grid search — useful template for future signals. |
| `momentum_lowered_backtest.py` | ✓ runs | Confirms the supposedly-easy momentum-tightening also fails. |
| `momentum-tighten.conf` (systemd drop-in) | **DO NOT APPLY** | Would lower thresholds but the lowered design itself fails the gate. Keeping the file as evidence; do not copy to `/etc/systemd/system/`. |
| `patch.diff` (operator patch) | **DO NOT APPLY** | References the un-deployable confluence trigger. |

## Alternatives — what to try instead (sorted by promise)

### 1. Limit-order entry (most likely to fix it)

Switch fast-reactor from market entries to maker limits at ±2 ticks.
Round-trip fee drops from 0.10 % → 0.02 % (or even rebate). At
−0.041 % EV gross + 0.02 % fees = −0.061 % net — still negative but
much closer. Combined with a slight TP widening and slow fills could
flip the sign.

Trade-off: fill probability < 100 %. Need a fill model and adverse-
selection adjustment (the orders that DO fill tend to fill on the
wrong side).

### 2. Use SFP as a CONFIRMATION inside whale or intel signals

Don't fire on SFP alone. Instead: when `eval_trigger_whale` fires
(whale + flow imbalance), check if the most-recent bar also has SFP +
fib confluence — if yes, BOOST the confidence; if no, fire normally.
This way SFP doesn't add trades, it only adjusts sizing on existing
trades.

This needs a different test: instead of "signal frequency", we ask
"does whale + SFP confluence have higher EV than whale alone?"

### 3. Different timeframe

The 1m bar may be too noisy for SFP to be reliable. Test 5m bars (with
12-bar lookback ≈ 1h, fib over 48 bars ≈ 4h). 5m gives the pattern
more time to confirm but fast-reactor would need a `kline.5` WS
subscription.

### 4. Acknowledge the regime and reduce activity

The 7d window had BTC in $79.5k-$82.5k range — very tight. SFP
patterns in a non-trending range tend to fail because there's no
follow-through. **In a trending regime, the same signal might pass.**
Re-run this backtest weekly and revisit if regime changes.

### 5. Drop fast-reactor's autonomy entirely

Keep fast-reactor as a WS data collector + execution hand for the
slower agent.loop. Don't have it fire its own trades. Let
agent.loop's `plan` decide entries (where the planner has access to
the full intel + brain context), then route through fast-reactor's
already-optimised sub-second order placement. Two separate problems.

## What this means for the live system

**Status as of 2026-05-13:** `sygnif-fast-reactor` is running but has
fired ZERO trades in 14+ hours despite +95 net-directional bullish
intel. The two proposed fixes (fib_sfp_confluence + momentum-tightening)
DO NOT change that, per the backtest above. **Fast-reactor will stay
silent until either (a) the regime changes enough for momentum to
catch on its own thresholds, or (b) we ship one of the alternatives
above.**

This silence is **safe** — the system is bleeding less than it would
be firing losing trades. The strategy_claim mutex + the disabled
bleeders (sygPL, sygSTND, perpRun, etc.) ensure no orphan paths.

## Next decision

Pick one of the alternatives above (most likely #1 or #2) and spawn a
fresh task with the empirical context from this run baked in. Do NOT
re-attempt the bare `fib_sfp_confluence` design.
