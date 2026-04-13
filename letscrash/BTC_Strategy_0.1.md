# BTC_Strategy_0.1 — rule collection (research)

**Version:** 0.1  
**Purpose:** Single place to **register** BTC-only rules proposed under **`/ruleprediction-agent`**, with a **traceable scan** of Pine references, Python regime/prediction code, risk notes, and market-data paths. **No live execution** is implied by this file.

**Canonical rule log (full §2 rows):** `letscrash/RULE_GENERATION_FROM_INCOMING_DATA.md` §5–§7.

**Machine tags + 10k / ⅓ bucket:** `letscrash/btc_strategy_0_1_rule_registry.json` (`rule_tag` on each experimental fill).

---

## 1. Scan — indicators (Pine)

| Asset | Path | Role |
|-------|------|------|
| SYGNIF 5m strategy | `prediction_agent/btc_predict_5m.pine` | **ATR(14)**, **RSI(14)**, **MACD(12,26,9)**, vol ratio vs SMA(20); **PAC** (`pacLen` default 34) + **EMA89 / EMA200** for `trendUp` / `trendDn`; **HTF** `request.security(..., "60", ta.ema(close,50))` for `htfUp`/`htfDn`; **S/R** lookback + Fib bands; **demand/supply zones** created only when `trending`; **scalp** entries only when `sideways` (PAC break with candle confirm); SL/TP as multiples of ATR (`slMul`, `scalpTP`/`scalpSL`). |
| BullByte Pro Scalper AI | `prediction_agent/reference/bullbyte_pro_scalper_ai_mpl2.pine` | **MPL 2.0** — composite oscillator (trend / momentum / vol / volume weights), dynamic thresholds, optional HTF + AI bias, **latching** state machine (Early→Strong Long/Short). **R03** research sibling to PAC pullback tools; **not** executed by Freqtrade. |
| JustUncleL SCALPTOOL R1.1 | `prediction_agent/reference/justunclel_scalping_pullback_tool_r1_1_v4.pine` | **Pine v4 study:** PAC **HiLoLen** default **34**, **fast/medium/slow** EMA **89/200/600** (slow unused in trend BG); **`TrendDirection`** from ribbon + PAC vs medium EMA; **pullback recovery** `pacExitU` / `pacExitL` + **`TradeDirection`** state; optional **HA** for algo; fractals + HH/LL helpers; TV **`alertcondition`** BUY/SELL. See `reference/README.md` (license: confirm with author/TV). |
| LuxAlgo SFP | `prediction_agent/reference/luxalgo_swing_failure_pattern_cc_by_nc_sa_4.pine` | **Swing failure** + optional volume validation — **reference only**; **CC BY-NC-SA** (see `prediction_agent/reference/README.md`). |
| QuantumEdge (MPL2) | `prediction_agent/reference/quantum_edge_manual_pro_mpl2.pine` | Staged TP/SL/trail + score ideas — mapped conceptually in `BTC_DUMP_PROTECTION_DESIGN.md`. |
| State-aware MA (MPL2) | `prediction_agent/reference/chikaharu_state_aware_ma_cross_mpl2.pine` | Regime + defensive close-all on crossunder — compare to risk-off framing. |
| TVI (MPL2) | `prediction_agent/reference/chikaharu_trend_volatility_index_tvi_mpl2.pine` | Vol expansion / synthetic range context — research only. |

---

## 2. Scan — algo (Python)

| Module | Path | Role |
|--------|------|------|
| Trend-long regime v1 | `user_data/strategies/btc_trend_regime.py` | `btc_trend_long_row`: **RSI_14_1h & RSI_14_4h > 50**, **close > EMA_200_1h**, **ADX_14 > 25** (5m frame with merged informatives). |
| 1h regime / dump read | `research/nautilus_lab/btc_regime_assessment.py` | Labels e.g. **risk_off** when 1h move ≤ **−1.15%** and **RSI14_1h < 37** and **RSI14_4h proxy < 46** (aligns with strategy “risk off” vocabulary); **pump_guard** when micro RSI hot vs 4h floor. |
| Prediction runner | `prediction_agent/btc_predict_runner.py` → `prediction_agent/btc_prediction_output.json` | 1h windowed means + **direction_logistic** + **consensus** string. |
| Training channel | `training_pipeline/channel_training.py` → `prediction_agent/training_channel_output.json` | Inflow health + sklearn direction + embedded runner snapshot + naive risk stats. |

---

## 3. Scan — risk management (design + JSON)

| Source | Path | Role |
|--------|------|------|
| Dump / trail design | `letscrash/BTC_DUMP_PROTECTION_DESIGN.md` | Maps Pine *ideas* to Sygnif vocabulary; staged exit / short bias **design-only** until proven. |
| Training-channel risk block | `prediction_agent/training_channel_output.json` → `risk_assessment` | e.g. **historical_1bar_return_var_95_pct**, **naive_long_if_model_up_max_drawdown_pct** — **research**, not FT execution. |
| Live adaptation clamps | `user_data/strategy_adaptation.json` | Bounded knobs (scores, ORB, vol mult) — **no silent** expansion per `RULE_GENERATION_FROM_INCOMING_DATA.md` §4. |

---

## 4. Scan — market data

| Data | Path (typical) | Notes |
|------|----------------|--------|
| 1h / daily OHLCV | `finance_agent/btc_specialist/data/btc_1h_ohlcv.json`, `btc_daily_90d.json` | Training inflow + charts. |
| Nautilus Bybit sink | `finance_agent/btc_specialist/data/btc_1h_ohlcv_nautilus_bybit.json`, `nautilus_spot_btc_market_bundle.json` | Preferred for `btc_regime_assessment` when present. |
| TA snapshot / market JSON | `finance_agent/btc_specialist/data/btc_sygnif_ta_snapshot.json`, `btc_crypto_market_data.json` | Briefing / dashboards. |

---

## 5. Collected rules (registry)

| ID | Summary | Full row |
|----|---------|----------|
| **BTC-0.1-R01** | Next-bar + runner bearish alignment → do not widen long risk from timing alone. | `RULE_GENERATION_FROM_INCOMING_DATA.md` §5 |
| **BTC-0.1-R02** | HTF structure / risk_off → suppress lower-TF Pine overlays until stack aligns. | `RULE_GENERATION_FROM_INCOMING_DATA.md` §6 |
| **BTC-0.1-R03** | JustUncleL PAC pullback + **BullByte Pro Scalper AI** (composite oscillator, latching, MPL 2.0) + **⅓-of-10k tagged bucket**; journal by `rule_tag` to see which rule proves worthy. | `RULE_GENERATION_FROM_INCOMING_DATA.md` §7; `prediction_agent/reference/bullbyte_pro_scalper_ai_mpl2.pine` |

---

## 6. Tagged proof capital (10k model)

- **Reference book:** **10 000 USDT** equity (paper or demo).
- **Rule-competition bucket:** **⅓ ≈ 3333 USDT** — only trades that set **`rule_tag`** to **BTC-0.1-R01**, **R02**, or **R03** (see `btc_strategy_0_1_rule_registry.json`).
- **Concurrent trades:** no fixed **max_open_trades** inside the bucket beyond **margin** and the **bucket notional cap** (sum of open stake/risk in tagged trades ≤ **3333 USDT**; scale entries accordingly).
- **Worthiness:** compare **closed-trade metrics by tag** over agreed windows; drop or freeze tags that fail vs the untagged **~6667 USDT** control style (still obey **R01/R02** narratives).

## 7. Freqtrade `order_types` + tag-routed entry/exit (`BTC_Strategy_0_1`)

### Tag-routed entry / exit

All **BTC-0.1-R\*** logic in Freqtrade is keyed on **`enter_tag`** (same string as registry `rule_tag`). **Only** `BTC_Strategy_0_1` performs these remaps and `custom_exit` branches; other pairs keep base Sygnif tags.

| `enter_tag` | Entry path | `custom_exit` (before Sygnif defaults) |
|-------------|------------|----------------------------------------|
| **BTC-0.1-R01** | BTC **`strong_ta`** candidate **after** R01 strip (training+runner not extreme bearish) → retag last bar to **R01**. | **`exit_btc01_r01_stack_guard`** when bearish stack returns while long. |
| **BTC-0.1-R02** | `SYGNIF_PROFILE=btc_trend` + `btc_trend_long_row` (was `btc_trend_long`). | **`exit_btc01_r02_regime_break`** when `btc_trend_long_row` fails. |
| **BTC-0.1-R03** | Last-bar **R03 sleeve** proxy (`r03_pullback_long`); blocked if R01 bearish stack. | **`exit_btc01_r03_scalp_overbought`** (RSI > 62) or **`exit_btc01_r03_scalp_take`**; plus **`exit_btc01_r01_stack_guard`** on bearish stack if profit \< ~0.8 %×lev. |

**Bucket / slots:** `confirm_trade_entry` enforces **registry notional cap** across all `BTC-0.1-R*` opens; **R02** uses `max_slots_btc_trend`, **R03** uses `max_slots_btc_0_1_r03`, **R01** uses `max_slots_strong` (same cadence as former `strong_ta` on BTC). **Futures:** `BTC_Strategy_0_1` sets `_tags_bypass_volume_regime` so **R01–R03** are not silenced by the global **“≥3 active volume pairs”** gate on a **BTC-only** whitelist (base logic in `SygnifStrategy` / `MarketStrategy2`).

### §7.1 First tagged trade (ruleprediction **L3** — entry / TP / SL)

**Cross-link:** **`letscrash/RULE_GENERATION_FROM_INCOMING_DATA.md`** §8.1; constants in `user_data/strategies/btc_strategy_0_1_engine.py`.

| Leg | Mechanism | Default (BTC long, futures) |
|-----|-----------|------------------------------|
| **Entry point** | Freqtrade signal on **5m bar close**; tags **R01** (retagged `strong_ta` after R01 governance), **R02** (`SYGNIF_PROFILE=btc_trend` remap from `btc_trend_long`), **R03** (`r03_pullback_long` last bar). No `custom_entry_price` — use **`order_types.entry`** (`limit` on paper config) + `entry_pricing`. | Prefer **R02** as the first **structural** probe when `btc_trend` profile is on; **R03** for first **sleeve** pullback test. |
| **TP** | `BTC_Strategy_0_1.custom_exit` | **R03:** take-profit at **`R03_SCALP_TP_PROFIT_PCT` × max(1, leverage)** (~**1.2% × lev** ROI); overbought exit if **RSI_14 > `R03_SCALP_RSI_OVERBOUGHT`** (62). R01/R02: inherited Sygnif / NFI-style exits unless `exit_btc01_*` fires first. |
| **SL** | `custom_stoploss` | **R03:** floor **`R03_STOPLOSS_FLOOR_VS_PARENT`** (**−2.5%** in parent ratio units) vs Sygnif doom/ratchet (`max(parent, floor)`). **R01/R02:** parent `SygnifStrategy.custom_stoploss` only (doom ÷ leverage on futures + ratchets). |
| **Stack guard** | `custom_exit` | **R02/R03** + R01 bearish stack: exit if profit **< `R01_R03_STACK_GUARD_LOSS_PCT` × lev** (~**0.8% × lev**). |

### `order_types` ↔ registry rules (backtest / demo)

`user_data/config_btc_trend_backtest.json` aligns **execution shape** with the three **BTC-0.1-R\*** rows (governance + sleeve — not automatic FT tags):

| Rule | Role in config |
|------|-----------------|
| **BTC-0.1-R01** (next-bar / timing) | **`entry` + `exit` = `limit`** — default fills are **not** reactive market orders; reduces “widen risk from one noisy bar” behaviour at the exchange layer. |
| **BTC-0.1-R02** (HTF structure / risk-off) | **`stoploss` = `market`** + **`stoploss_on_exchange`: true** — when invalidation fires, exit is **hard and immediate** (maps to “cut / don’t lean on soft discretionary” at order level). Strategy logic still must enforce R02; this is only order routing. |
| **BTC-0.1-R03** (tagged sleeve / PAC family) | **`force_entry` / `force_exit` / `emergency_exit` = `market`** — operational / manual or tagged-sleeve paths can flat or flip quickly; **`can_short`: true** when testing short sleeve on futures. |

Duplicate string for operators: **`_btc_strategy_0_1_order_types`** in that JSON mirrors this table.

**Changelog**

| Date | Change |
|------|--------|
| 2026-04-13 | Initial **0.1**: scan matrix + R01/R02 registry. |
| 2026-04-13 | **R03** + JustUncleL Pine archive; **§6** capital model + `btc_strategy_0_1_rule_registry.json`. |
| 2026-04-13 | **§7** — `order_types` ↔ R01–R03 mapping for `config_btc_trend_backtest.json`. |
| 2026-04-13 | **§7** — tag-routed entry/exit table for `BTC_Strategy_0_1` (`enter_tag` = R01–R03). |
| 2026-04-13 | **R03** — BullByte **Pro Scalper AI** (`bullbyte_pro_scalper_ai_mpl2.pine`, MPL 2.0) linked in registry + §1 scan. |
| 2026-04-13 | **§7.1** — ruleprediction L3 first-trade entry / TP / SL table + engine constants (`btc_strategy_0_1_engine.py`); R03 `custom_stoploss` floor. |
