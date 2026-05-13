# fib_sfp trigger — backtest results (DO NOT DEPLOY)

**Date:** 2026-05-13
**Data:** 43,200 × BTCUSDT 1m klines from Bybit V5 public API, 2026-04-13 12:52 → 2026-05-13 12:51 UTC (30 days).
**Verdict:** **FAILED** all three acceptance gates from `AGENTS.md` §6.
**Recommendation:** **Do not deploy.** The trigger as specified does not have positive expectancy on BTC 1m data in this window.

---

## Acceptance gates

| Gate | Threshold | Best observed | Verdict |
|---|---|---|---|
| Win rate | ≥ 40% | **26.4%** | ✗ FAIL |
| Fires/week | ≥ 5 | up to 474 (way over) | ✓ PASS (volume is fine) |
| EV net | > $0/trade | **−$2.80 (best case)** | ✗ FAIL |

## What was tested

**Baseline params** (from the original design prompt):
- `lookback=50`, `maxlen=240`, `fib_proximity=1.0%`, TP=0.4%, SL=0.25%
- Result: 2,032 fires (474/wk), 22.3% WR, EV_net = **−$4.71/trade**, total = **−$9,575** over 30d

**Parameter sweep — proximity, lookback, window size:**

| Config | Fires | Fires/wk | WR | EV_net | Total_net |
|---|---|---|---|---|---|
| baseline (1.0% prox) | 2032 | 474.1 | 22.3% | −$4.71 | −$9,575 |
| 1/2 prox (0.5%) | 1522 | 355.1 | 20.5% | −$4.67 | −$7,100 |
| 1/5 prox (0.2%) | 720 | 168.0 | 19.2% | −$4.52 | −$3,253 |
| 1/10 prox (0.1%) | 374 | 87.3 | 21.4% | −$4.32 | −$1,617 |
| lookback 100, prox 0.5% | 787 | 183.6 | 19.3% | −$4.61 | −$3,628 |
| lookback 100, prox 0.2% | 331 | 77.2 | 19.6% | −$4.35 | −$1,440 |
| lookback 100, prox 0.1% | 179 | 41.8 | 22.3% | −$4.34 | −$777 |
| wide 720, lb 150, prox 0.5% | 458 | 106.9 | 20.1% | −$4.45 | −$2,037 |
| wide 720, lb 150, prox 0.2% | 224 | 52.3 | 23.2% | −$3.71 | −$831 |
| **1-day window, lb 200, prox 0.2%** | **178** | **41.5** | **25.3%** | **−$3.82** | **−$679** |
| 1-day window, lb 200, prox 0.1% | 106 | 24.7 | 23.6% | −$4.11 | −$435 |

**Parameter sweep — TP/SL ratio** (on the best-WR config):

| TP/SL | R:R | Breakeven WR | Observed WR | EV_net |
|---|---|---|---|---|
| 0.40% / 0.25% | 1.60 | 60.0% | 25.3% | −$3.82 |
| 0.80% / 0.25% | 3.20 | 37.1% | 25.3% | −$3.64 |
| 1.00% / 0.25% | 4.00 | 31.2% | 25.3% | −$3.53 |
| 1.50% / 0.20% | 7.50 | 20.0% | 25.3% | −$3.89 |
| 1.00% / 0.30% | 3.33 | 33.8% | 26.4% | **−$2.85** (least bad) |
| 2.00% / 0.30% | 6.67 | 19.1% | 26.4% | −$2.80 |
| 1.00% / 0.10% | 10.00 | 21.8% | 20.2% | −$9.15 (tight SL hits too often) |

## Why it fails

The trigger fires the right **count** and the gates fail on **quality**. Two observations:

1. **WR plateaus at ~25% regardless of selectivity.** Tightening proximity from 1.0% → 0.1% cuts fires from 2,032 → 374 but only nudges WR from 22.3% to 21.4%. SFP at Fib retracement is NOT mean-reverting on BTC 1m in this window.

2. **Wider R:R doesn't unlock edge.** When SL stays fixed and TP widens, win count holds (same signals, same exits) but timeouts grow — most positions just don't reach the wider TP within 30 bars. Net EV moves slightly less negative but never crosses zero.

Hypothesis: **BTC 1m in the audit window was trending more than ranging** (BTC drifted from ~84k → ~80k over the period). SFP signals are mean-reversion entries; in a trending regime they're systematically wrong. A regime filter (only fire in low-volatility / range-bound conditions) might unlock edge, but that's a design change, not a parameter tweak.

## What would be needed before deploying

This trigger needs at least one of:

1. **Regime filter.** Only enable when realised volatility is below some band AND price is range-bound (e.g. Bollinger Band width < threshold). Test on the historical data with regime labels.

2. **Inverted directionality test.** Treat SFP at Fib as a **breakout confirmation** (price tested the level, rejected, momentum continues in the rejection direction) — but require this to confirm with the rest of fast-reactor's signals rather than fire standalone.

3. **Higher timeframe gating.** SFP detection on 1m + Fib levels on a 1h or 4h frame instead of 240m. May yield far fewer fires but with structural significance.

4. **Different exit model.** Trailing-stop exit (mirroring fast-reactor's trailing-daemon) rather than fixed TP. Use the existing 0.1%/0.1% trail to let winners run.

5. **Confluence with intel.** Currently the proposal lets intel-veto BLOCK trades but never makes signal generation conditional on intel. Inverting: only fire when intel `boosts_<direction>` has ≥ 2 entries → much tighter, more selective.

## Artifacts in this branch

| File | Purpose |
|---|---|
| `fib_sfp_trigger.py` | The trigger module (228 lines, no external deps beyond stdlib) |
| `tests/test_fib_sfp_trigger.py` | 8 unit tests (all pass; p99 latency 5.9 µs) |
| `backtest_fib_sfp.py` | Backtest harness (fetches Bybit V5 klines, simulates execution, checks gates) |
| `data/btc_1m_klines.jsonl` | Cached 30d × 43,200 1m klines (~10 MB) |
| `data/backtest_result.json` | Detailed per-trade outcomes for the baseline config |
| `BACKTEST_RESULTS.md` | This file |

## Decision

Per `AGENTS.md` §6 rule "If gates fail, DO NOT MERGE — report results and stop":
- This branch should **stay open as a PR for visibility** but **not be merged**.
- `eval_trigger_bounce` remains as-is in `/opt/sygnif-services/sygnif_fast_reactor.py` (still dead, but not replaced).
- Follow-up: pick one of the 5 design changes above, implement, re-backtest, re-PR.

The good news: the module + tests + harness are reusable. Iterating on (1)–(5) is a parameter+code change, not a from-scratch rebuild.
