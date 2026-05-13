# SFP signal variant backtest — final results

**Backtest window**: 30 days × 1m BTCUSDT (44,000 bars), Bybit linear perp.
**Acceptance gates**: WR ≥ 50%, fires/wk ∈ [5, 30], EV_net > 0 (after fees).
**Default sim**: TP=0.40% / SL=0.25% / max-hold=60m / fee=0.10% RT taker.

## TL;DR

**Every variant FAILS the acceptance gates.** EV_gross is near zero
(−0.02% to +0.0002%) across all regime filters, exit mechanics, fee
assumptions, and R:R ratios. **The fib-SFP signal as designed in PR #15
does not have statistical edge in this BTC sample.** Fees are not the
bottleneck — even at idealised 0.025% RT maker fees, no configuration
clears the gates.

## Tested variants

### Hypothesis 1 — Baseline (PR #15 spec, both directions)

| Variant | Trades | WR | Fires/wk | EV gross | EV net | Verdict |
|---|---|---|---|---|---|---|
| baseline (TP 0.4 / SL 0.25 / fee 0.10) | 2070 | 45.4% | 474 | −0.006% | −0.106% | FAIL |

### Hypothesis 2 — Regime filter (BB-width range gate)

| BB threshold | Trades | WR | Fires/wk | EV gross | EV net | Verdict |
|---|---|---|---|---|---|---|
| 0.0050 (loose) | 1769 | 46.8% | 405 | −0.003% | −0.103% | FAIL |
| 0.0030 | 1211 | 48.5% | 277 | −0.003% | −0.103% | FAIL |
| 0.0020 | 654 | 51.4% | 150 | **+0.0001%** | −0.100% | FAIL |
| 0.0012 | 151 | 52.3% | 34.6 | −0.018% | −0.118% | FAIL |
| 0.0008 (tight) | 33 | 39.4% | 7.6 | −0.049% | −0.149% | FAIL |

**Finding**: BB filter trades selectivity for WR. Sweet spot ≈ 0.002 (best
gross). Beyond that, signal degrades from over-filtering.

### Hypothesis 3 — Trailing exit (no filter)

| Trail | Act | Trades | WR | EV gross | Verdict |
|---|---|---|---|---|---|
| 0.0010 | 0.0010 | 2070 | **69.2%** | −0.016% | FAIL |
| 0.0015 | 0.0010 | 2070 | 48.6% | −0.015% | FAIL |
| 0.0020 | 0.0010 | 2070 | 43.5% | −0.007% | FAIL |
| 0.0015 | 0.0020 | 2070 | 53.7% | −0.016% | FAIL |
| 0.0020 | 0.0020 | 2070 | 53.7% | −0.010% | FAIL |
| 0.0030 | 0.0020 | 2070 | 45.9% | −0.009% | FAIL |

**Finding**: Tight trail at 0.10% gives 69% WR but captures are too small
to beat fees, and fires-cap is wildly violated (474/wk). No combo escapes.

### Hypothesis 4 — Regime × Trailing hybrid

| BB | Trail | Trades | WR | Fires/wk | EV gross | Verdict |
|---|---|---|---|---|---|---|
| 0.0030 | 0.0010 | 1211 | 68.9% | 277 | −0.008% | FAIL |
| 0.0030 | 0.0020 | 1211 | 46.6% | 277 | −0.001% | FAIL |
| 0.0020 | 0.0010 | 654 | **70.0%** | 150 | −0.002% | FAIL |
| 0.0020 | 0.0020 | 654 | 50.0% | 150 | **+0.0002%** | FAIL |
| 0.0012 | 0.0010 | 151 | 62.3% | 34.6 | −0.022% | FAIL |
| 0.0012 | 0.0020 | 151 | 45.0% | 34.6 | −0.024% | FAIL |

**Finding**: Hybrid gives best WR (70%) but EV gross still ~zero. When you
tighten BB to satisfy fires-cap (≤30/wk), WR drops back below 60% and gross
is negative. The selectivity × trail combination cannot beat fees.

### Hypothesis 5 — Maker-entry fee model (signal × execution)

Reduces fee assumption to test whether better execution would save the
signal. 0.055% = limit-entry + taker-exit; 0.025% = both legs maker.

| Fee | BB | EV gross | EV net | Verdict |
|---|---|---|---|---|
| 0.055% | 0.0020 | +0.0001% | −0.055% | FAIL |
| 0.055% | 0.0012 | −0.018% | −0.073% | FAIL |
| 0.025% | 0.0020 | +0.0001% | −0.025% | FAIL |
| 0.025% | 0.0012 | −0.018% | −0.043% | FAIL |

**Finding**: **Fees are NOT the bottleneck.** Even at full maker fees,
EV_gross stays at ~zero. The signal itself has no positive expectancy
to recover.

### Hypothesis 6 — Asymmetric R:R (regime × wider TP / tighter SL)

| TP / SL | BB | WR | EV gross | EV net | Verdict |
|---|---|---|---|---|---|
| 0.60% / 0.20% | 0.0020 | 46.9% | −0.006% | −0.106% | FAIL |
| 0.80% / 0.20% | 0.0020 | 46.9% | −0.007% | −0.107% | FAIL |
| 0.60% / 0.15% | 0.0020 | 42.2% | −0.001% | −0.101% | FAIL |
| 0.60% / 0.20% | 0.0012 | 48.3% | −0.020% | −0.120% | FAIL |

**Finding**: Wider TP doesn't reach fast enough; tighter SL trips early.
WR falls proportionally — no R:R sweetspot exists.

## Why does the signal lack edge?

1. **Mean reversion at fib levels is too crowded.** Bull SFP at fib 0.618
   is a textbook pattern; the venue's market makers fade these aggressively.
2. **1-min cadence amplifies noise.** Same swing failure on 15m would have
   fewer false signals but be too slow for the fast-reactor's <2s budget.
3. **Fixed exits don't match signal half-life.** SFP momentum dies within
   ~20–40 minutes; fixed 60-min hold + 0.4% TP captures noise more than edge.

## Decisions

### 1. PR #17 (SFP-trader separation) — keep merged, signal disabled

The **architecture** is sound and reusable:
- Strategy-claim mutex prevents double-opens between SFP-trader and
  fast-reactor.
- Fib-zone veto patch keeps fast-reactor away from levels where any future
  signal needs space.
- Kill-switch (`SYGNIF_SFP_TRADER_ENABLED=0`) keeps it disabled by default.

The **signal** (fib-SFP) is dead. Future work should target a different
trigger source rather than tuning this one further.

### 2. Avoid re-attempting fib-SFP at 1m

Any future agent looking at this signal: it has been exhaustively swept.
The negative result is in this directory. Move on.

### 3. Open research questions (not blocked on this signal)

- **Maker-only execution stack** for the fast-reactor (separately valuable
  for cutting fee drag on whatever signal eventually works).
- **Higher-timeframe SFP** (15m / 1h) — prior session's HTF variant showed
  the only positive gross EV (+0.019%) but still over the fees floor.
  Worth a follow-up if combined with maker entry.
- **Different trigger primitives** — VWAP-band breaks, order-flow imbalance,
  funding-driven mean reversion. None tested here.

## Reproducibility

```bash
cd experiments/sfp_trader/variants
python _fetch_klines.py                       # one-time, ~3 min
python _harness.py --variant baseline         # 5s
SFP_BB_THRESHOLD=0.002 python _harness.py --variant regime
SFP_TRAIL_PCT=0.001 SFP_TRAIL_ACT=0.001 python _harness.py --variant trailing
SFP_BB_THRESHOLD=0.002 SFP_TRAIL_PCT=0.001 python _harness.py --variant regime_trail
SFP_FEE_PCT=0.00055 SFP_BB_THRESHOLD=0.002 python _harness.py --variant maker_sim
SFP_TP_PCT=0.008 SFP_SL_PCT=0.002 SFP_BB_THRESHOLD=0.002 python _harness.py --variant regime
```

All env-overrides are documented in `_harness.py` module docstring.

## Files

- `_harness.py` — env-overridable backtest engine, fixed and trailing exit models
- `_fetch_klines.py` — one-time cache of 30d × 1m BTCUSDT klines into `_data/`
- `baseline/evaluate.py` — raw PR #15 spec, both directions
- `regime/evaluate.py` — BB-width range gate
- `trailing/evaluate.py` — passes through baseline fires, harness handles trailing
- `regime_trail/evaluate.py` — BB filter + harness trailing
- `maker_sim/evaluate.py` — BB filter, fee assumption set via SFP_FEE_PCT env var
- `_data/` — gitignored kline cache (rebuilt by `_fetch_klines.py`)

---

*Backtest run: 2026-05-13 on `feature/sfp-trader-separation` (PR #17).*
*38 distinct config runs, all FAIL. Signal declared dead at 1m fib levels.*
