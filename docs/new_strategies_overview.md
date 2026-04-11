# New Strategy Components — Overview

Three additional features identified from ai-hedge-fund analysis that complement the existing plan.

**See also:** [Market Strategy 3 (MS3) — regime layer & gap register](market_strategy_3.md) for bear/risk-off design, living gap table, and evidence log (orthogonal to the items below unless explicitly merged later).

---

## 1. Volume Acceleration (Linear Regression Slope on Volume)

**Source:** ai-hedge-fund `growth_agent.py` — `_calculate_trend()` function

**What it does:**
Calculates the linear regression slope of rolling volume over N candles. A positive slope means volume is increasing candle-over-candle (accelerating). A negative slope means volume is drying up (decelerating). Unlike simple `volume / volume_sma_20` (which you already have), this captures the *direction* of volume change, not just the level.

**Formula:**
```
slope = (n * Σ(i * vol_i) - Σ(i) * Σ(vol_i)) / (n * Σ(i²) - (Σ(i))²)
normalized_slope = slope / volume_sma  (so it's comparable across pairs)
```

**Benefits:**
- Detects volume acceleration *before* it shows up in moving averages (leading signal)
- Distinguishes "high volume but fading" from "low volume but building" — the latter often precedes breakouts
- Pairs naturally with squeeze detection: squeeze + rising volume slope = imminent breakout
- Cheap to compute (single rolling linear regression)

**Drawbacks:**
- Noisy on 5m candles — needs smoothing (rolling window ≥ 21 candles)
- Volume spikes from single whale orders create false slope signals
- On low-cap altcoins, volume patterns are less reliable
- Adds marginal value over the existing `volume / volume_sma_20` ratio for simple cases

**Implementation approach:**
```python
# In populate_indicators(), after volume_sma_20 (line ~467)
def _vol_slope(vol_series):
    n = len(vol_series)
    x = np.arange(n)
    return (n * np.sum(x * vol_series) - np.sum(x) * np.sum(vol_series)) / \
           (n * np.sum(x**2) - np.sum(x)**2)

df["vol_slope_21"] = df["volume"].rolling(21).apply(_vol_slope, raw=True)
df["vol_slope_norm"] = (df["vol_slope_21"] / df["volume_sma_20"]).fillna(0)

# In _calculate_ta_score_vectorized(), after volume confirmation
# Accelerating volume into a bullish signal: +3
# Decelerating volume during entry: -2
vol_accel = df["vol_slope_norm"].fillna(0)
score += np.where((vol_accel > 0.05) & (score > 55), 3,
         np.where(vol_accel < -0.05, -2, 0))
```

**TA score impact:** ±3 points (minor). Best value is as a *confirmation* filter, not a primary signal.

---

## 2. Volatility-of-Volatility (Vol-of-Vol)

**Source:** ai-hedge-fund `nassim_taleb.py` — `analyze_volatility_regime()` lines 602-623

**What it does:**
Measures how unstable volatility itself is. High vol-of-vol means the volatility regime is changing — a precursor to major market moves. Low vol-of-vol means stable conditions (which Taleb warns is the "turkey problem" — calm before the storm).

**Formula:**
```
hist_vol = returns.rolling(21).std() * sqrt(252)      # annualized 21-day vol
vol_of_vol = hist_vol.rolling(21).std()                # std of the vol itself
vov_ratio = current_vol_of_vol / median_vol_of_vol     # normalized
```

**Benefits:**
- Detects regime transitions before they complete — vol-of-vol spikes 2-5 candles before price breakouts
- Complements squeeze detection: squeeze fires on BB/KC compression (slow), vol-of-vol fires on instability (fast)
- Taleb's key insight: when vol-of-vol is extremely low, the market is *fragile* — small shocks propagate. When vol-of-vol is high, the market is already repricing risk
- Combined with vol_regime: `vol_regime < 0.8 AND vov_ratio > 2.0` = "compressed vol about to explode" — the highest-conviction squeeze signal

**Drawbacks:**
- Requires 63+ candles of history (21 for hist_vol + 21 more for rolling std + buffer) — but you already have startup_candle_count=200
- Can produce false signals during low-activity periods (weekends, holidays) where vol mechanically drops
- Interpreting "high vol-of-vol" requires context — it means instability, not direction. You need other indicators for directionality
- Adds complexity to an already large indicator set

**Implementation approach:**
```python
# In populate_indicators(), after vol_regime and vol_z_score (Phase 1B)
returns = df["close"].pct_change()
hist_vol = returns.rolling(21).std() * np.sqrt(252)
df["vol_of_vol"] = hist_vol.rolling(21).std()
vov_median = df["vol_of_vol"].rolling(63).median()
df["vov_ratio"] = (df["vol_of_vol"] / vov_median).fillna(1.0)

# In _calc_global_protections() — turkey problem warning
# Extremely low vol-of-vol = fragile market, be cautious
if "vov_ratio" in df.columns:
    prot &= (df["vov_ratio"] > 0.3) | (df["RSI_3"] > 20)

# In _calculate_ta_score_vectorized() — enhance squeeze signal
# squeeze_on AND vol_regime < 0.8 AND vov_ratio > 2.0 = +10 (max conviction)
# squeeze_on AND vol_regime < 0.8 = +8 (existing)
# squeeze_on alone = +5 (existing)
squeeze = df.get("squeeze_on", False)
vol_r = df.get("vol_regime", 1.0)
vov_r = df.get("vov_ratio", 1.0)
score += np.where(squeeze & (vol_r < 0.8) & (vov_r > 2.0), 10,
         np.where(squeeze & (vol_r < 0.8), 8,
         np.where(squeeze, 5, 0)))
```

**TA score impact:** Upgrades the squeeze signal from +5/+8 to +5/+8/+10. The +10 fires rarely (squeeze + low vol + unstable vol = triple confirmation) but when it does, it's the highest-confidence entry signal.

---

## 3. Asymmetric Weight Grouping (Downside-First Scoring Architecture)

**Source:** ai-hedge-fund `mohnish_pabrai.py` — 45% downside / 35% valuation / 20% upside weighting

**What it does:**
Restructures `_calculate_ta_score_vectorized()` from a flat list of additive components into three weighted groups:

| Group | Weight | Components | Purpose |
|---|---|---|---|
| **Downside Protection** | 45% | Global protections, tail_ratio, vol_regime, vol_z_score, BTC correlation, volume spike | "Is it safe to enter?" |
| **Signal Quality** | 35% | RSI_14 (Hurst-modulated), EMA cross, BB, Aroon, StochRSI, CMF, multi-TF RSI | "Is the TA signal genuine?" |
| **Upside Potential** | 20% | Squeeze, momentum_score, pressure_ratio, RSI_3, vol_acceleration, skewness | "How much upside is there?" |

Each group scores independently (0-100), then the final score is the weighted combination.

**Benefits:**
- Encodes Pabrai's core principle: "avoid the losers first, then find the winners"
- Current scoring treats a +8 squeeze boost and a -5 BTC crash penalty as equal magnitude — they shouldn't be. Downside protection should dominate
- Makes the scoring system more modular and tunable — you can adjust group weights without touching individual indicator math
- Easier to debug: "this trade scored 80 overall = 90 downside protection + 75 signal quality + 65 upside" tells you exactly why it entered
- Aligns with how professional risk managers think: first check risk limits, then evaluate alpha

**Drawbacks:**
- **Biggest change in the plan** — restructures the entire scoring function, not just adding indicators
- Risk of regression: the current flat scoring works and has been tested. Restructuring could shift entry/exit behavior unpredictably
- Requires careful calibration of group weights and per-group normalization
- Each group needs its own 0-100 scale, which means rethinking all the individual ±N point assignments
- More code, more potential for bugs in the normalization math

**Implementation approach:**
```python
def _calculate_ta_score_vectorized(self, df: DataFrame) -> pd.Series:
    # --- Group 1: Downside Protection (0-100, higher = safer) ---
    downside = pd.Series(80.0, index=df.index)  # start optimistic

    # BTC crash
    if "btc_RSI_14_1h" in df.columns:
        btc_rsi = df["btc_RSI_14_1h"].fillna(50)
        downside += np.where(btc_rsi < 30, -25, np.where(btc_rsi > 60, 5, 0))

    # Tail ratio
    if "tail_ratio" in df.columns:
        downside += np.where(df["tail_ratio"] < 0.7, -20, np.where(df["tail_ratio"] > 1.2, 5, 0))

    # Vol regime
    if "vol_regime" in df.columns:
        downside += np.where(df["vol_regime"] > 1.5, -15, np.where(df["vol_regime"] < 0.8, 5, 0))

    # Volume spike
    if "volume_spike" in df.columns:
        downside += np.where(df["volume_spike"] > 2.5, -20, 0)

    downside = downside.clip(0, 100)

    # --- Group 2: Signal Quality (0-100, higher = stronger signal) ---
    signal = pd.Series(50.0, index=df.index)

    # RSI_14 (Hurst-modulated)
    rsi = df["RSI_14"].fillna(50)
    hurst_mod = np.where(df.get("hurst", 0.5) < 0.4, 1.3,
                np.where(df.get("hurst", 0.5) > 0.6, 0.7, 1.0))
    signal += np.where(rsi < 30, 15, np.where(rsi < 40, 8,
              np.where(rsi > 70, -15, np.where(rsi > 60, -8, 0)))) * hurst_mod

    # EMA, BB, Aroon, StochRSI, CMF, Multi-TF RSI
    # ... (existing logic, same point values)

    signal = signal.clip(0, 100)

    # --- Group 3: Upside Potential (0-100, higher = more upside) ---
    upside = pd.Series(40.0, index=df.index)

    # Squeeze + vol regime + vol-of-vol
    upside += np.where(squeeze & (vol_r < 0.8) & (vov_r > 2.0), 25,
              np.where(squeeze & (vol_r < 0.8), 20,
              np.where(squeeze, 12, 0)))

    # Momentum, pressure, RSI_3, skewness
    # ... (existing logic, scaled to this group)

    upside = upside.clip(0, 100)

    # --- Weighted Combination ---
    final = (downside * 0.45) + (signal * 0.35) + (upside * 0.20)
    return final.clip(0, 100)
```

**TA score impact:** Changes the *distribution* of scores, not just the range. Downside-heavy weighting means borderline entries in risky conditions get blocked more often, while clear setups in safe conditions score higher. Net effect: fewer trades, higher win rate (in theory — needs backtesting to confirm).

---

## Comparison Matrix

| Dimension | Vol Acceleration | Vol-of-Vol | Asymmetric Weighting |
|---|---|---|---|
| **Complexity** | Low (5 lines) | Medium (8 lines) | High (restructure entire function) |
| **Risk of regression** | None | Low | Medium-High |
| **Independent value** | Low (confirmation only) | Medium (enhances squeeze) | High (architectural improvement) |
| **Backtesting needed** | Quick sanity check | Compare squeeze hit rate | Full A/B comparison required |
| **Implementation time** | 15 min | 20 min | 1-2 hours |
| **Dependencies** | volume_sma_20 (exists) | hist_vol from Phase 1B | All Phase 1-3 indicators |

## Recommendation

**Implement #1 (Vol Acceleration) and #2 (Vol-of-Vol) now** — they're small, independent additions that slot into the existing plan without risk.

**Defer #3 (Asymmetric Weighting) to a separate iteration** — it's a scoring architecture change that should be done after the indicator additions are validated by backtesting. Once you confirm the new indicators improve performance, *then* restructure the scoring to weight them properly.
