# Market Strategy 3 (MS3) — Frozen design spec

**Status:** design frozen (no implementation obligation from this document).  
**Owner intent:** regime-first layer that fills structural gaps vs default `SygnifStrategy` / `MarketStrategy2`.  
**Last updated:** 2026-04-11  

**Related code (today, not MS3):**

- `user_data/strategies/SygnifStrategy.py` — default stack; `SYGNIF_STRATEGY_BACKEND=ms2` → `MarketStrategy2.py`
- Future entrypoint (when built): e.g. `SYGNIF_STRATEGY_BACKEND=ms3` → `MarketStrategy3.py` (design only until implemented)

---

## 1. Positioning in the strategy family

| Layer | Role today | MS3 role (target) |
|--------|------------|-------------------|
| **SygnifStrategy (default)** | Long-biased spot + futures; strong long crash filters; futures shorts with hard BTC / anti-pump stack; spot long-only | Remains **stable core**; MS3 does not replace it on day one |
| **MarketStrategy2 (MS2)** | Parallel stack: live snapshot + LLM sentiment, `strategy_adaptation.json`, ORB, etc. | MS3 **reuses** adaptation / sentiment **hooks** but adds **regime engine + gap fills**, not a third duplicate of the same entry table |
| **MarketStrategy3 (MS3)** | *Not implemented* | **Regime-first overlay:** bull / chop / risk-off / bear impulse / crash-liquidity → **capital, slots, and templates** mapped explicitly |

**Composition (design):** Same pattern as MS2: config keeps `strategy: SygnifStrategy`; env selects backend class that inherits MS3. MS3 may inherit MS2 or a thin shared base; implementation choice deferred.

---

## 2. Problems MS3 is meant to address (gap → intent)

| ID | Gap (observed or inferred) | MS3 design response |
|----|-----------------------------|---------------------|
| G-001 | **Spot cannot short** — no negative beta on spot leg | **Split policy:** spot = de-risk, size caps, stricter entries in risk-off; never shorts |
| G-002 | **Futures shorts sparse in BTC-led bull** (e.g. structural `btc_RSI_14_4h` gate) | **Regime-conditioned** thresholds — stricter in R1 bull, **relaxed only in chop (R2)** with explicit risk budget and backtest gates |
| G-003 | **No systematic “profit from dump”** — shorts opportunistic, not a bear sleeve | **R3–R4 regimes:** dedicated **short templates** (breakdown continuation, failed bounce) + **notional cap** for short sleeve |
| G-004 | **Open long risk in cliff** — mostly per-trade exits, no cohort flatten | **Portfolio-level rules:** regime flip → tighten `custom_stoploss` / optional **partial exit** weakest names in correlated cluster (subject to Freqtrade callback constraints) |
| G-005 | **No hedge between spot and futures notionals** | **Optional module:** size futures short sleeve as function of **correlated spot gross** — off by default until validated |
| G-006 | **Limited first-class observability** for why shorts/long sizing fire or block | **Regime + intent logging** (required in MS3); interim: `SYGNIF_SHORT_DIAG` on SygnifStrategy for selected pairs |
| G-007 | **Leverage asymmetry** — shorts capped (e.g. 2×) limits dump capture | Keep **safety cap** in R5; optional **small** increase in R2 only if evidence supports — default unchanged |
| G-008 | **Regime flip noise** on 5m | **Hysteresis:** N candles or dual-signal confirmation before regime change drives capital |

---

## 3. Regime engine (single source of truth)

**Inputs (design):**

- BTC: multi-TF RSI, ROC, structure (4h/1d slope, loss of key levels).
- Breadth: share of whitelist below 20/50 EMA; median correlation to BTC impulse.
- Volatility: ATR%, realized vol bucket.
- **Optional soft input:** finance-agent / trade_overseer **risk score** — never sole trigger without tests.

**Discrete regimes (v1 taxonomy — tunable):**

| Code | Name | Plain meaning | Default posture |
|------|------|----------------|-----------------|
| R1 | Bull trend | BTC + breadth supportive | Current-like **long bias**; shorts **minimal** |
| R2 | Chop | No clean trend | **Smaller size**; **easier shorts than R1**; fewer marginal longs |
| R3 | Risk-off | Breadth weak, BTC losing structure | **Aggressive longs off**; **tighten** open longs; **short sleeve on** |
| R4 | Bear impulse | Fast downside, vol spike | **Short priority**; longs only strict mean-reversion or off |
| R5 | Crash / liquidity | Extreme gaps, disorderly tape | **Survival:** min edge for entries, lower leverage, optional **flatten** |

**Hysteresis:** regime changes require **confirmation** (N bars and/or two independent signals) to avoid thrashing.

---

## 4. Policies per regime (summary)

**Longs:** R1 aligned with today’s strong TA / sentiment / swing stack; R2–R3 scale **stake** and trim **low-edge tags**; R4–R5 hard **slot** caps and optional tag whitelist.

**Shorts (futures):** R1 conservative; R2 **relaxed vs R1** on documented gates; R3–R4 **template-based** bear sleeve with **max notional**; R5 fewer trades, higher quality or stand-down if spreads blow out.

**Spot:** Never shorts; R3+ **max gross exposure** and **cooldown** after large BTC down sequences.

---

## 5. Portfolio-level actions (cohort)

- On transition to **R3+:** optional **tighten** stop floor globally; **partial exit** rules for worst R-multiple / oldest in **theme cluster**.
- **No martingale:** regime change does not increase size on underwater same-direction adds unless a separate **recovery** module exists and is explicitly enabled (default **off**).

---

## 6. Risk & config (knobs, not fixed numbers)

- **Regime risk budget:** e.g. max short notional as % of futures equity in R4 vs R3 — all in **`strategy_adaptation.json`** or **`market_strategy_3.json`** (filename TBD at implementation).
- **Drawdown governor:** account DD vs peak → force R3 or flat (ties to equity / overseer if available).
- **Kill switch:** `MS3_DISABLE=1` → regime engine **neutral** (no change vs parent behavior).

---

## 7. Rollout & validation (when implementing)

1. **Shadow mode:** log “MS3 would have done X” — no trades — until stable.
2. **Stake + slots only** by regime.
3. **New short templates** in R3–R4 only.
4. **Cohort exits** last (highest complexity).

**Success metrics:** time-in-regime stability; conditional PnL / MAE **by regime and side**; canned **dump windows** (historical BTC -X% in Y days) vs baseline on entries, shorts, and DD. **Inputs:** use the automated bundle in `user_data/market_strategy_3_metrics.json` (NT-style Sharpe/Sortino/drawdown, tag families, trading_success) plus `user_data/logs/market_strategy_3_metrics.jsonl` for trends.

---

## 8. Non-goals

- Perfect timing of tops/bottoms.
- Replacing **StoplossGuard**, **doom**, or **exchange SL** — MS3 sits **above** them.
- Mandatory cross-exchange hedging in v1.

---

## 9. Automated metrics feed (feeds MS3 design / evidence)

**Purpose:** Persist the evaluation metrics that were previously “implemented but not attended” into machine-readable artifacts MS3 and operators can consume.

| Artifact | Producer | Contents |
|----------|----------|----------|
| `user_data/market_strategy_3_metrics.json` | `scripts/collect_ms3_metrics.py` (daily) + compact writer | NT-style **`perf_nt`** (7d/30d per spot+futures: Sharpe, Sortino, max DD, win rate, …), **`entry_tag_families`**, **`closed_trades_analysis`** (entry/exit family breakdowns), **`trading_success_7d`** |
| `user_data/logs/market_strategy_3_metrics.jsonl` | same | One JSON line per run (trend / MS3 shadow) |
| `user_data/logs/entry_performance.jsonl` | daily collector (`append_entry_perf_log=True`) | Tag-family snapshots over time |
| `user_data/strategy_adaptation_weekly.json` | `scripts/weekly_strategy_analysis.py` | Adds embedded **`ms3_metrics`** bundle (no duplicate entry_perf append on Sunday) |

**Cron (install from `cron/crontab.txt`):**

- **Daily ~06:15 UTC** — `collect_ms3_metrics.py` (Telegram via `scripts/cron_wrap_tg.sh`).
- **Weekly Sun 06:00 UTC** — `weekly_strategy_analysis.py` (horizon + `ms3_metrics` embedded).
- **Every 6h at :30** — `scripts/cron_trading_success.sh` (Telegram 1d + paths; 7d log-only).

**Env:** `MS3_METRICS_WINDOWS` — optional comma-separated windows (default `7,30`). `SYGNIF_REPO` for wrap scripts.

---

## 10. Gap register & status (living table)

**Instructions:** Add a row per new gap discovered; set **Status** to `open` → `design` → `spec` → `implemented` → `verified` or `wontfix`. Link evidence log IDs.

| ID | Summary | MS3 § / response | Status | Owner notes |
|----|---------|------------------|--------|-------------|
| G-001 | Spot no shorts | §2, §4 | design | — |
| G-002 | Short gate in bull | §2, §3 R1/R2 | design | Thresholds TBD backtest |
| G-003 | Dump profit sleeve | §2, §3 R3–R4 | design | — |
| G-004 | Open long cliff | §5 | design | FT callback limits |
| G-005 | Cross-leg hedge | §2, §5 optional | design | Default off |
| G-006 | Observability | §2, §7 shadow logs | partial | `SYGNIF_SHORT_DIAG` + **§9 metrics JSON/JSONL** |
| G-007 | Short leverage cap | §2, §6 | design | Safety first |
| G-008 | Regime noise | §3 hysteresis | design | — |
| G-009 | Evaluation metrics not scheduled | §9 | implemented | `collect_ms3_metrics.py` + cron; weekly embed |

---

## 11. Evidence & data collection log

**Instructions:** Append **newest first** under this section. Each entry: **date (UTC)**, **source**, **finding**, **gap IDs touched**, **follow-up**.

### Log

- **2026-04-11** — *Automation (`scripts/ms3_metrics_feed.py`, `collect_ms3_metrics.py`)* — Daily bundle writes `market_strategy_3_metrics.json` + JSONL; weekly sidecar gains `ms3_metrics`; 6h trading success via `cron_trading_success.sh`. **Gaps:** G-009 closed; G-006 partial. **Follow-up:** wire regime labels into bundle when MS3 shadow exists.

- **2026-04-11** — *DB snapshot (`user_data/tradesv3*.sqlite`)* — Open and recent closed trades on spot + futures: all `is_short=0`; **lifetime** `COUNT(is_short=1)` = **0** on both DBs. **Gaps:** G-002, G-003 (market + strategy gates; no proof of bug). **Follow-up:** enable `SYGNIF_SHORT_DIAG=1` on futures during next review window; archive CSV of regime inputs if MS3 shadow exists.

- **2026-04-11** — *Code review (`SygnifStrategy._calc_global_protections*`) — Long side: multi-TF crash cascade + BTC line (`btc_RSI_3_1h` vs `btc_RSI_14_4h`). Short side: `btc_RSI_14_4h <= 60` blocks **all** shorts when BTC 4h RSI &gt; 60. **Gaps:** G-002, G-004. **Follow-up:** log `prot_short` / `btc_RSI_14_4h` in production when diagnosing.

---

## 12. Changelog (document only)

| Date | Change |
|------|--------|
| 2026-04-11 | Initial freeze: full MS3 spec + gap register + evidence log seeded from design session. |
| 2026-04-11 | §9 automated metrics feed: `ms3_metrics_feed`, daily collector, weekly embed, cron templates, G-009. |
