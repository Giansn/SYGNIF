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

---

# Addendum — sfp_v2 (community-standard rewrite)

After the original 38-config sweep failed, we audited the math against
TradingView community Pine Scripts (LuxAlgo SFP, AGPro SFP Engine,
BullByte Structural Liquidity, cd_sfp_Cx) plus ICT/SMC and Wyckoff
literature. Findings:

- **Detection logic was textbook-correct** (`low<key_low AND close>key_low`) ✓
- **But the wrapper was non-standard**:
  - Used rolling 50-bar min/max instead of **confirmed pivots** (5/5 left/right)
  - Used **fib retracement** as confluence — no community script does this
  - Missing all 3 community filters: **wick ratio ≥ 0.55**, **reclaim ≥ 0.25 × ATR**, **volume ≥ 1.15 × SMA20**
  - Ran on **1m** — below the 5m floor recommended by every TV author
  - No CISD / MSS / FVG confirmation step

`sfp_v2_trigger.py` ships a faithful AGPro-spec implementation. The
harness was extended with `SFP_AGGREGATE_TF=N` (rolls up 1m → N-min bars)
so the same cached data drives every timeframe sweep.

## Timeframe sweep — sfp_v2 (TP=0.40% / SL=0.25% / fee=0.10% RT)

Pivot 5L/5R default; relaxed pivots tested separately at HTF.

| TF  | Pivot | Bars in 30d | Trades | Fires/wk | WR    | EV gross | EV net  | Verdict |
|-----|-------|-------------|--------|----------|-------|----------|---------|---------|
| 1m  | 5/5   | 44,000      | 1735   | 397      | 43.9% | −0.008%  | −0.108% | FAIL    |
| 5m  | 5/5   | 8,799       | 402    | 92       | 47.0% | **+0.009%** | −0.091% | FAIL |
| 15m | 5/5   | 2,933       | 111    | 25.4 ✓   | 42.3% | +0.007%  | −0.093% | FAIL    |
| 30m | 5/5   | 1,466       | 50     | 11.5 ✓   | 44.0% | **+0.021%** | −0.079% | FAIL |
| 60m | 5/5   | 732         | 21     | 4.8      | 47.6% | **+0.027%** | −0.073% | FAIL |
| 60m | 3/3   | 732         | 25     | 5.7 ✓    | 40.0% | +0.010%  | −0.090% | FAIL (looser pivot = worse) |
| 2h  | 5/5   | 365         | 9      | 2.1      | 44.4% | **+0.039%** | −0.061% | FAIL (under fires-floor) |
| 2h  | 3/3   | 365         | 11     | 2.5      | 36.4% | −0.014%  | −0.114% | FAIL |
| **4h** | **5/5** | **182** | **3** | **0.7** | **0%** | **−0.250%** | **−0.350%** | **N/A — sample too small** |
| 4h  | 3/3   | 182         | 4      | 0.9      | 25.0% | −0.088%  | −0.188% | N/A — sample too small |
| 4h  | 2/2   | 182         | 5      | 1.2      | 20.0% | −0.120%  | −0.220% | N/A — sample too small |

**Findings**:

1. **EV_gross monotonically improves with TF** through 2h: −0.008 → +0.009 → +0.007 → +0.021 → +0.027 → **+0.039**. Real signal.
2. **Relaxing pivot length HURTS edge** at HTF — 60m 3/3 (5.7/wk, 40% WR, +0.010%) is worse than 60m 5/5 (4.8/wk, 47.6% WR, +0.027%). The community-default 5/5 is correct.
3. **4h cannot be evaluated** on a 30-day sample. 3-5 trades is statistical noise; the 0% / 20% / 25% WR results are not a signal failure, they're an undersized sample. To properly test 4h we'd need ≥ 90 days of data (270 bars → ~30 trades).
4. **2h has the best per-trade edge** (+0.039% gross) but fires/wk = 2.1 is below the 5/wk floor — would need months more data to harvest enough trades.
5. **30m is the operating sweet spot** for the 30-day sample: 11.5/wk in-gate, +0.021% gross, the deepest TF where signal AND sample-size both work.

## R:R sweep at 30m TF — wider TP hurts

| TP / SL                  | Trades | WR    | EV gross | Notes        |
|--------------------------|--------|-------|----------|--------------|
| 0.40% / 0.25% (default)  | 50     | 44.0% | +0.021%  | best         |
| 0.60% / 0.30%            | 50     | 38.0% | +0.018%  | wider TP misses |
| 0.80% / 0.40%            | 50     | 38.0% | −0.005%  | losses dominate |
| 1.00% / 0.40%            | 50     | 32.0% | −0.001%  | too wide     |

**Finding**: signal reversals are short-lived (~30-60 min). Wider TP turns
winners into losers. Default symmetric-ish R:R is correct.

## Fee model sweep at 30m TF — maker entry crosses break-even

| Fee model                       | Fee % RT | EV gross | EV net      | Verdict     |
|---------------------------------|----------|----------|-------------|-------------|
| Taker entry + taker exit        | 0.100%   | +0.028%  | −0.073%     | FAIL        |
| Limit entry + taker exit        | 0.055%   | +0.028%  | −0.027%     | FAIL        |
| Full maker (entry + exit limit) | 0.025%   | +0.028%  | **+0.003%** | FAIL (WR<50%) |

**Finding**: at full-maker fees, EV_net just barely clears positive
(+0.003% / trade). The strict WR≥50% gate still blocks since SFP at
asymmetric R:R settles near 44% WR — but EV_net > 0 IS achievable with
better execution.

## What this means for SYGNIF

The signal has measurable edge at 30m TF on BTC, ~ +0.028% gross per trade
(50 trades over 30 days). The economics:

```
Edge per trade (30m, full-maker):  +0.003% net
Trades per week:                    11.5
Equity at stake:                    $2,000 (current demo)
Weekly P&L (at 0.5% risk-per-trade): +$0.35  ← essentially noise
```

**Edge exists but is too small to matter at $2k equity.** Per-trade
expectancy is below the noise floor of bybit-demo execution slippage.
To make this useful we need either:
- **10x larger equity** so absolute P&L becomes meaningful, OR
- **Additional orthogonal filter** to boost WR above 50% (HTF EMA bias,
  FVG context, RSI divergence — none tested here), OR
- **Verified maker-only execution stack** with measured slippage

## Updated recommendation

1. **Keep PR #17 architecture** (mutex, kill-switch, fast-reactor patch) — unchanged
2. **Replace `fib_sfp_trigger.py` with `sfp_v2_trigger.py`** as canonical detector
3. **Set the daemon's default TF to 30m** (not 1m) — but stay disabled until #4
4. **Pursue maker-only execution** for the fast-reactor stack independently —
   without it, no candidate signal beats fee drag at retail equity sizes
5. **Add HTF bias filter as Phase-2 enhancement** — quickest path to
   WR > 50% per community wisdom; testable via existing harness

## Files added in this addendum

- `experiments/sfp_trader/sfp_v2_trigger.py` — AGPro-spec community SFP detector
- `experiments/sfp_trader/variants/sfp_v2_1m/evaluate.py` — sanity check at 1m
- `experiments/sfp_trader/variants/sfp_v2_5m/evaluate.py` — TF-parametric runner

---

*Addendum: 2026-05-13. Total backtests across both rounds: ~50.*
*Conclusion: signal has marginal edge at 30m + maker execution.*
*Edge magnitude is below noise floor at current demo equity.*

---

# Second addendum — 90-day sample reverses the 30-day conclusion

After observing positive EV_gross at HTF on the 30-day sample, we
extended the cache to 90 days (130k bars, 2026-02-12 → 2026-05-13) to
properly evaluate 4h SFP where 30d had only 3 trades. The result
**invalidates the prior addendum's positive 30m / 60m / 2h findings**.

## Direct 30d vs 90d comparison — TF sweep, identical filters

| TF  | 30d EV gross | 30d EV net | 90d EV gross | 90d EV net | Delta gross |
|-----|--------------|------------|--------------|------------|-------------|
| 30m | **+0.021%**  | −0.079%    | +0.008%      | −0.092%    | −0.013pp (halved) |
| 60m | **+0.027%**  | −0.073%    | **−0.002%**  | −0.102%    | flipped negative |
| 2h  | **+0.039%**  | −0.061%    | **−0.040%**  | −0.140%    | massive deterioration |
| 4h  | N/A (n=3)    | N/A        | **−0.250%**  | −0.350%    | n=18, **0/18 WR** |
| 6h  | N/A (n=0)    | N/A        | −0.250%      | −0.350%    | n=5, 0/5 WR |

| TF  | 30d WR    | 90d WR    | 30d trades | 90d trades |
|-----|-----------|-----------|------------|------------|
| 30m | 44.0%     | 41.4%     | 50         | 174        |
| 60m | 47.6%     | 38.6%     | 21         | 70         |
| 2h  | 44.4%     | 32.4%     | 9          | 34         |
| 4h  | (n=3) 0%  | **0.0%**  | 3          | **18**     |
| 6h  | (n=0)     | 0.0%      | 0          | 5          |

## What the 90-day sample shows

1. **4h SFP on BTC is anti-edge.** 18 trades, **all** SL hits. Under a 40% WR null, P(0/18 wins) ≈ 1.5e-5. The signal is *worse* than random at this TF, consistent with the hypothesis that BTC 4h is a trend-dominated regime where wicks below swing lows are typically *continuations*, not reversals.

2. **30d positive results were sample artifacts.** The favorable 2026-04-13 → 2026-05-13 window appears to have been a ranging period that flattered mean-reversion entries. The prior 60 days (2026-02-12 → 2026-04-13) were unfavorable enough to flip every HTF EV_gross negative.

3. **At 1m the signal is essentially flat** across both windows: baseline 30d −0.006%, 90d −0.0001%. WR ≈ 44% — consistent with random under our 1.6:1 R:R.

4. **30 days is too short** to validate a 5-15 trades/week strategy. Variance of EV across 30-day BTC windows is wider than any plausible edge. Lesson for future SYGNIF research: **minimum 90 days for validation, ideally 180 days**.

## Revised final conclusion

The fib_sfp_trigger signal and the community-standard sfp_v2 detector
**both have no statistical edge on BTC** across the tested 90-day
sample, at any timeframe from 1m through 6h. The "marginal edge at 30m"
claim in the first addendum was an artifact of sample-period luck.

### What still stands

- **PR #17 architecture** (mutex, kill-switch, fast-reactor coordination
  patch) — entirely correct, reusable for whatever signal succeeds it.
- **Math audit** — the original `fib_sfp_trigger.py` detection logic was
  correct; the wrapper (fib confluence at 1m) was the problem; the
  community-standard rewrite was also correct but the underlying signal
  thesis doesn't hold on BTC.

### What was overturned

- ❌ "30m TF has +0.021% gross edge" — sample-window artifact
- ❌ "60m TF has +0.027% gross edge" — sample-window artifact
- ❌ "EV_gross monotonically improves with TF" — only on 30d, reverses on 90d
- ❌ "Maker execution makes 30m profitable" — based on artifact
- ❌ "Apply sfp_v2_trigger.py as canonical detector" — replaces broken
     code with different-but-still-broken code

### What this means for SYGNIF going forward

1. **Don't ship any SFP-based signal** without first running ≥ 90-day
   backtest with WR confidence intervals.
2. **PR #17 daemon scaffold ships with sfp_v2** as a reference
   implementation of community SFP, but with the kill-switch armed
   permanently. Signal goes live only when a successor demonstrates
   sustained edge on rolling 90d sample.
3. **Need a regime gate at a minimum** — the 2026-02 to 2026-04
   trending period killed any directional fib-SFP variant. Future SFP
   work must include a Hurst / ADX / range-fraction filter.
4. **Or pivot away from SFP entirely** — try VWAP-stretch reversals,
   funding-rate mean reversion, or pure order-flow imbalance, none of
   which depend on this swing-failure thesis.

## Reproducibility — 90d data

```bash
cd experiments/sfp_trader/variants
python _fetch_klines.py --days 90              # ~3 min, 130k bars
SFP_DATA_DAYS=90 SFP_AGGREGATE_TF=240 python _harness.py --variant sfp_v2_5m
```

---

*Second addendum: 2026-05-13. Total backtests: ~60.*
*Final verdict: no edge on BTC at any TF, regardless of filter set.*
*The prior "30m edge" finding does not survive sample-size extension.*
