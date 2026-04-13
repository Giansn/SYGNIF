# Swing failure pattern (SygnifStrategy) — code-aligned analysis

This describes **`swing_failure` / `swing_failure_short`** and the hybrid tags **`sygnif_swing` / `sygnif_swing_short`**, as implemented in `user_data/strategies/SygnifStrategy.py` (populate_indicators + populate_entry_trend + `_exit_swing_failure`).

## 1. Pattern definition (5m, per pair)

| Column | Meaning |
|--------|---------|
| `sf_support` / `sf_resistance` | Rolling min/max of **prior** highs/lows over `sf_lookback_bars` (min 8). |
| `sf_*_stable` | Level unchanged for **2** bars — avoids flickering levels. |
| `sf_volatility` | `abs(close - EMA_120) / EMA_120` — distance from slow trend. |
| `sf_vol_filter` | Volatility **>** `sf_vol_filter_min` — blocks entries when price sits on EMA120 (no “dead” failures). |

**Long (`sf_long`):** wick **sweeps** support (`low <= sf_support`) but **closes back above** (`close > sf_support`), stable support, volatility filter true → classic **stop-hunt / failure swing** long setup.

**Short (`sf_short`):** mirror — wick **above** resistance, close **below**, stable resistance, same vol filter.

## 2. Entry routing (last candle only)

- If `sf_long` and no higher-priority long yet:
  - `TA >= sf_ta_split` → **`sygnif_swing`** (pattern + TA agreement).
  - else → **`swing_failure`** (pattern **without** strong TA — higher discretion / mean-reversion character).
- Short side: `sf_short` + `TA <= sf_ta_split` → **`sygnif_swing_short`**, else **`swing_failure_short`**.

Global protections (`protections_long_global` / `protections_short_global`) and `empty_ok` still apply — BTC dump/pump rails can **block** the candle even if `sf_*` is true.

## 3. Exits (`_exit_swing_failure`)

- **TP:** price vs volatility-adjusted **`sf_tp_ema`** (EMA120 scaled by `sf_volatility` and `sf_tp_vol_scale`), requires **>0.5%** profit to avoid noise exits.
- **SL:** percent from entry = `sf_sl_base + sf_volatility * sf_sl_vol_scale` (volatility-scaled).

Hybrid tags run **swing exit first**, then can fall through to Williams/RSI stack; standalone `swing_failure*` uses **only** swing exit path for swing-specific TP/SL.

## 4. BTC correlation (spot book context)

- **Entries** are filtered by merged BTC columns (e.g. crash rails, pump guard for shorts).
- **Longs** additionally watch **`exit_btc_risk_off`**: sharp **1h BTC** weakness + low **1h/4h RSI** flattens small longs — relevant when spot wallet is small and alt-BTC beta hurts.

## 5. TradingView Pine

If you paste an indicator script, we can **map** its conditions to `sf_long` / `sf_short` / TP-SL semantics and check for **lookahead** (e.g. rolling max including current bar vs Sygnif’s `shift(1)` window).

## 6. Nautilus research container

Use `research/nautilus_lab/btc_regime_assessment.py` + `btc_dump_run_framework.py` for **regime labels** that echo the strategy’s BTC gates (not a performance guarantee).
