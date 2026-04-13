# BTC expertise — proven formulas (Sygnif)

Research and live stack use explicit numeric rules so behaviour is reproducible, testable, and auditable. This note captures the **implemented** formulas (code is source of truth). Not investment advice.

---

## 1. Sygnif TA score (0–100)

Used across `SygnifStrategy` entries (`strong_ta`, sentiment bands, swing gates). Neutral base **50**; result **clipped to [0, 100]**.

| Component | Rule (vectorized) | Range |
|-----------|-------------------|-------|
| RSI_14 | `<30` +15, `<40` +8, `>70` −15, `>60` −8 | ±15 |
| RSI_3 | `<10` +10, `<20` +5, `>90` −10, `>80` −5 | ±10 |
| EMA 9 vs 26 | Bull cross +10; bull +7; else −7 | ±10 |
| Bollinger 20,2 | Close ≤ lower +8; close ≥ upper −8 | ±8 |
| Aroon 14 | Up>80 & Down<30 +8; mirror bear −8 | ±8 |
| StochRSI 14,14,3,3 | `<20` +5; `>80` −5 | ±5 |
| CMF 20 | `>0.15` +5; `<−0.15` −5 | ±5 |
| MTF RSI | 1h<35 & 4h<40 +5; 1h>70 & 4h>65 −5 | ±5 |
| BTC 1h RSI (`btc_RSI_14_1h`) | `<30` −5; `>60` +3 | −5 … +3 |
| Volume vs SMA25 | ratio>1.5 & score>50 +3; ratio>1.5 & score<50 −3 | ±3 |
| ADX/CDL | `adx_ta_score_component` (optional) | ±5 |
| Nautilus overlay | `nautilus_signal_score` column if present | additive |

**Implementation:** `user_data/strategies/SygnifStrategy.py` → `_calculate_ta_score_vectorized`.

---

## 2. BTC trend regime (R02 governance input)

**`btc_trend_long_row`** (last bar) is true iff all hold:

- `RSI_14_1h` > **50** (`RSI_BULL_MIN`)
- `RSI_14_4h` > **50**
- `close` > `EMA_200_1h` (finite, >0)
- `ADX_14` (5m) > **25** (`ADX_MIN`)

**Implementation:** `user_data/strategies/btc_trend_regime.py`.

Freqtrade `BTC_Strategy_0_1` maps `btc_trend_long` → tag **`BTC-0.1-R02`** when `SYGNIF_PROFILE=btc_trend`; R02 slot cap and exits are in `BTC_Strategy_0_1.py`.

---

## 3. BTC-0.1 rule tags R01 / R02 / R03

Registry: `letscrash/btc_strategy_0_1_rule_registry.json`. Rule proof bucket: **⅓ reference equity** → default **3333.33 USDT** notional cap for tagged competition (`load_notional_cap_usdt`).

### R01 — governance (training + runner extremes)

**`r01_training_runner_bearish()`** is true iff:

1. `training_channel_output.json` → `recognition.last_bar_probability_down_pct` **≥ 90**
2. Same file → `recognition.btc_predict_runner_snapshot.predictions.consensus` (uppercased) **== `"BEARISH"`**

When true, the strategy **blocks aggressive long timing** (e.g. strips or blocks certain tags) per `BTC_Strategy_0_1` + engine.

**Implementation:** `user_data/strategies/btc_strategy_0_1_engine.py` → `r01_training_runner_bearish`, `_read_training_channel`.

### R03 — scalping sleeve (PAC-style proxy, last bar)

**`r03_pullback_long(df)`** requires `len(df) ≥ 6`, columns `RSI_14`, `close`; `ADX_14` optional (defaults **20** if missing).

| Step | Condition |
|------|-----------|
| RSI depth | `RSI_14[i−3] < 38` |
| RSI rebound | `RSI_14[i] > 42` and `RSI_14[i] > RSI_14[i−1]` |
| Trend compression | `ADX_14[i] < 34` |
| Price | `close[i] > close[i−1]` |

**Exit / risk constants** (same engine module):

| Constant | Value | Role |
|----------|-------|------|
| `R03_SCALP_TP_PROFIT_PCT` | **0.012** (× max(1, leverage)) | scalp take-profit |
| `R03_SCALP_RSI_OVERBOUGHT` | **62** | RSI exit |
| `R01_R03_STACK_GUARD_LOSS_PCT` | **0.008** (× max(1, leverage)) | stack guard vs bearish training |
| `R03_STOPLOSS_FLOOR_VS_PARENT` | **−0.025** | floor vs parent `custom_stoploss` |

**Implementation:** `user_data/strategies/btc_strategy_0_1_engine.py`; strategy wiring `user_data/strategies/BTC_Strategy_0_1.py`.

---

## 4. Training channel (next-bar recognition)

**`training_pipeline/channel_training.py`** (after optional `btc_predict_runner`):

1. Load Bybit OHLCV (`btc_1h_ohlcv.json` or daily).
2. `add_ta_features` + sliding window (`WINDOW` default **5**).
3. **Holdout** split (`TEST_RATIO` default **0.2**).
4. **StandardScaler** + **LogisticRegression** (`liblinear`, `max_iter=400`) on **direction** of next-bar target.
5. Outputs **last-bar** `last_bar_probability_up_pct` / `last_bar_probability_down_pct`, Brier score, naive long MDD on holdout, VaR on historical 1-bar returns, embedded `btc_predict_runner_snapshot`.

**Output:** `prediction_agent/training_channel_output.json` — feeds R01 and `/briefing` / ruleprediction copy in JSON.

---

## 5. Operational loop (monitor + develop)

1. Refresh OHLCV (Nautilus sink, `update_movers` / btc data jobs — see `letscrash/RULE_AND_DATA_FLOW_LOOP.md`).
2. Run **`./scripts/run_training_flow.sh`** — chains runner → channel → **R01–R03 monitor** (JSON default).
3. **`scripts/monitor_r01_r03_gate.py`** — read-only what-if: R01 stack, R03 pattern on 1h TA, R02 if MTF columns exist on the dataframe.
4. Optional audit trail: **`RULE_TAG_JOURNAL_MONITOR=YES`** appends `r01_r03_monitor` rows to `prediction_agent/rule_tag_journal.csv`.

**Stale training:** use `monitor_r01_r03_gate.py --strict-stale --max-age-hours 48` in CI/cron to fail if channel JSON is old.

---

## 6. Cross-links

| Topic | Path |
|-------|------|
| Rule ↔ data loop | `letscrash/RULE_AND_DATA_FLOW_LOOP.md` |
| Rule generation from data | `letscrash/RULE_GENERATION_FROM_INCOMING_DATA.md` |
| BTC 0.1 strategy L3 | `letscrash/BTC_Strategy_0.1.md` |
| Bridge / demo | `letscrash/BTC_STRATEGY_0_1_BYBIT_BRIDGE.md` |
| Horizon + journal | `prediction_horizon_check.py`, `prediction_agent/rule_tag_journal.py` |

---

*Last aligned with repo formulas (engine + SygnifStrategy). When you change thresholds in code, update this table in the same PR.*
