# BTC & Sygnif — TP / exit system review (finance-agent)

**Role:** Structured reference for **implementation truth** (Freqtrade), aligned with **`.cursor/agents/finance-agent.md`** — code + risk rails, not generic TA advice.

**Related:** [`BTC_Strategy_0.1.md`](./BTC_Strategy_0.1.md) (research scan), [`btc_strategy_0_1_rule_registry.json`](./btc_strategy_0_1_rule_registry.json) (live tuning), `user_data/strategies/SygnifStrategy.py` (parent exits).

---

## 1. Scope

| System | TP style | Where |
|--------|-----------|--------|
| **BTC 0.1** (`BTC_Strategy_0_1`) | **Registry % TP** (+ R03 RSI scalp, governance exits) | `user_data/strategies/BTC_Strategy_0_1.py` + `btc_strategy_0_1_engine.py` |
| **Sygnif parent** | **No fixed % TP** — RSI tiers, Williams %R, swing EMA TP, soft SL | `SygnifStrategy.custom_exit` |
| **Bybit predict loop** | Signal flip / flat only (no % TP) | `scripts/btc_predict_protocol_loop.py` |

Freqtrade: **`minimal_roi = {"0": 100}`** + **`ignore_roi_if_entry_signal`** → **ROI table is not primary**; **`custom_exit` + `custom_stoploss` + exchange SL** define behaviour.

---

## 2. Callback order (BTC long, R-tag)

1. **`BTC_Strategy_0_1.custom_exit`** — if returns a string, **stops** (parent not run for that reason).
2. **`super().custom_exit`** → **SygnifStrategy** RSI / swing / WillR / soft SL / BTC risk-off.

**Implication:** R01–R03 get **tag TP first**; other BTC longs use **only** parent stack unless they fall through.

---

## 3. BTC 0.1 — exits by tag

| Tag | Entry (summary) | TP / exit triggers (long BTC) |
|-----|------------------|-------------------------------|
| **R01** | `strong_ta` → renamed; governance may strip longs when training+runner bearish | `exit_btc01_r01_stack_guard` (bearish stack); **`tp_profit_pct` × max(1,lev)** → `exit_btc01_r01_tp`; SL cap via `sl_doom` in `custom_stoploss` |
| **R02** | `btc_trend_long` → R02 + `btc01_r02_trend_long_row` (registry `r02_regime`) | `exit_btc01_r02_regime_break`; **`tp_profit_pct` × lev** → `exit_btc01_r02_tp`; stack guard if bearish + loss < threshold |
| **R03** | Pullback sleeve `r03_pullback_long` | **RSI > `r03_scalp.rsi_overbought`** → `exit_btc01_r03_scalp_overbought`; then **`tp_profit_pct`** (registry `tp_sl.R03` or `r03_scalp`) × lev → `exit_btc01_r03_scalp_take`; R03 **SL floor** vs parent; stack guard |

**DCA:** scale-in via `adjust_trade_position`; **TP is on whole-trade `current_profit`**, not per fill.

**Shorts:** R01–R03 branch is **long-only** in `custom_exit`; shorts defer to **parent** unless extended later.

---

## 4. Registry tuning map (`tuning` in JSON)

| Block | Keys | Effect |
|-------|------|--------|
| **`tp_sl.BTC-0.1-R01`** | `tp_profit_pct`, `sl_doom` | % TP; doom cap for `tag_sl_return_cap` (futures ÷ leverage) |
| **`tp_sl.BTC-0.1-R02`** | same | same |
| **`tp_sl.BTC-0.1-R03`** | `tp_profit_pct` **null** → use `r03_scalp` | Scalp TP source |
| **`r03_scalp`** | `tp_profit_pct`, `rsi_overbought`, `stack_guard_loss_pct` | R03 behaviour |
| **`r01_governance`** | `enabled`, `p_down_min_pct`, `runner_consensus_equals` | Blocks entries + drives stack guard context |
| **`r02_regime`** | `rsi_bull_min`, `adx_min` | R02 hold vs `exit_btc01_r02_regime_break` |
| **`entry_prediction`** | `enabled`, `also_gate_tags` | Extra long blocks under bearish stack |

**Single source of truth for % TP:** registry; engine constants (e.g. `R03_SCALP_TP_PROFIT_PCT`) are **fallbacks** when JSON missing.

---

## 5. SygnifStrategy — “TP-like” stack (parent)

| Mechanism | Long (idea) | Exit reason prefix |
|-----------|-------------|---------------------|
| BTC 1h risk-off | BTC change + RSI gates, profit cap scales with lev | `exit_btc_risk_off` |
| Swing / hybrid tags | Vol-adjusted EMA TP + vol SL | `exit_sf_ema_tp`, `exit_sf_vol_sl` (short: `exit_sf_short_*`) |
| Profit-tiered RSI | Min profit 2% (× lev futures); threshold vs **adj_profit/leverage** + EMA200 | `exit_profit_rsi_*` |
| Williams %R | Reversal after min profit | `exit_willr_reversal` / short variant |
| Soft SL | Before hard doom + RSI/EMA conditions | `exit_stoploss_conditional` |

**Leverage note:** Parent RSI tiers use **`adj_profit = profit / leverage`**; BTC 0.1 TP uses **`threshold × max(1, leverage)`** — **different conventions**; compare carefully when tuning both.

---

## 6. Design tensions (for iteration)

1. **Dual exit philosophy on BTC:** explicit % TP (R-tags) vs NFI-style RSI TP (parent) when R path returns `None`.
2. **R03 order:** RSI exit **before** % TP — can exit on RSI without reaching scalp %.
3. **Observability:** Prefer **exit_reason** in logs / overseer / journals aligned with strings above for post-trade review.

---

## 7. Operations & stability (light touch)

Recent **crashes / heavy usage** are often **ops stack**, not TP math. Keep load predictable:

| Practice | Why |
|----------|-----|
| **One `:8091` finance-agent** listener | Per `ruleprediction-agent` / inherit rules — avoid duplicate HTTP + LLM fan-out. |
| **Stagger crons** | Don’t align channel_training, movers, GitNexus analyze, and heavy Python jobs on the same minute. |
| **Freqtrade:** `reload_config` vs full **container restart** | Prefer reload for strategy JSON/registry edits when safe; full restart only when patches/Dockerfile change. |
| **Bounded retrains** | Keep prediction / self-learning jobs within documented caps — avoid tight loops + full bar history every few seconds on the same host. |
| **Targeted reads** | For TP questions: **registry + two strategy files + engine** — avoid whole-repo grep in automation. |
| **Memory** | Large `kline_limit` + many pairs increases worker RSS; BTC-only bot reduces surface. |

This section is **guidance**; correlate with **host logs** (`docker logs`, `journalctl`) for actual crash signatures.

---

## 8. Tests

| File | Covers |
|------|--------|
| `tests/test_btc_strategy_0_1_engine_tp_sl.py` | `tag_sl_return_cap`, `tag_takeprofit_profit_pct`, entry_prediction gates |

Integration tests for **full `custom_exit` precedence** are a gap if you want CI safety on ordering.

---

## 9. Revision

| Date | Note |
|------|------|
| 2026-04-13 | Initial structured review (finance-agent + letscrash); consolidates TP analysis into ops-friendly layout. |
