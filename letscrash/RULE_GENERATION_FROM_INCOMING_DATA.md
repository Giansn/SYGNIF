# Trading rule generation from **incoming data** (`/ruleprediction-agent`)

**Purpose:** When data lands in the repo (Nautilus bundle, training channel JSON, runner output, briefing), use a **fixed** cycle to **propose** trading-relevant rules — **not** silent live changes.

**Align with:** **`sygnif-predict-workflow`** (Predict → Analyze → Proofread → Adjust) and **`letscrash/RULE_AND_DATA_FLOW_LOOP.md`** (prove → test → apply or remove).

---

## 1. Incoming data — read first (priority)

| Source | Path (host) | What to extract |
|--------|---------------|------------------|
| Nautilus OHLCV + bundle | `finance_agent/btc_specialist/data/btc_1h_ohlcv.json`, `btc_daily_90d.json`, `nautilus_spot_btc_market_bundle.json` | Regime (vol/trend), last ticker/trades/ob snapshot freshness, gaps |
| Training channel | `prediction_agent/training_channel_output.json` | `recognition.*`, `risk_assessment.*`, `inflow.channels` health |
| Runner | `prediction_agent/btc_prediction_output.json` | Model disagreement, last horizons |
| Briefing contract | `GET /briefing` on **8091** | Pipe semantics, char budget — do not duplicate long OHLCV inside briefing |
| Strategy truth | `user_data/strategies/SygnifStrategy.py`, `user_data/strategy_adaptation.json` | What is **already** gated; avoid duplicate rules |

---

## 2. Rule proposal shape (every cycle)

For each **finding** from §1, emit **one** row:

| Field | Content |
|-------|---------|
| **Observation** | Quote metric + timestamp / file mtime |
| **Hypothesis** | “If X then tighten/relax Y” (one sentence) |
| **Surface** | `.mdc` / `strategy_adaptation.json` / `SygnifStrategy.py` / compose — **one primary** |
| **Risk class** | L0 horizon-only / L1 runner / L2 adaptation / L3 entry logic (see `PREDICTION_PIPELINE_AND_SELF_LEARNING_PLAN.md`) |
| **Proof** | `pytest` path, `prediction_horizon_check.py check`, or `dry_run` observation window |
| **Rollback** | Revert file / key removal |

**Forbidden:** changing live **`exchange.key`** or slot caps from JSON alone without explicit user sign-off.

---

## 3. Apply vs hold

- **Hold (document only):** Pine references, bundle-only microstructure ideas — log in PR / `letscrash/` note.
- **Apply (code or clamped JSON):** only after proof + tests; **L2** adaptation keys must stay within documented clamps.
- **Remove:** if A/B test or horizon check falsifies the hypothesis — delete rule text or revert adaptation.

---

## 4. Automation boundary

- **Allowed unattended:** Nautilus feed, `channel_training.py`, `btc_predict_runner.py`, `prediction_horizon_check.py save`.
- **Not unattended by default:** edits to **`SygnifStrategy.py`** live entries/exits, or unbounded **`strategy_adaptation.json`** expansion — human or explicit CI gate.

---

## 5. Log — first rule line (from training channel inflow)

**Source snapshot:** `prediction_agent/training_channel_output.json` with `generated_utc` **2026-04-12T22:34:57Z** (read from disk when this row was written).

| Field | Content |
|-------|---------|
| **Observation** | `inflow.channels`: `bybit_1h` **ok**, 200 rows; `bybit_daily` **ok**, 90 rows. `recognition.last_bar_probability_down_pct` **99.09** (up **0.91**); `btc_predict_runner_snapshot.predictions.consensus` **BEARISH**; `direction_logistic` **DOWN** confidence **94.5**; `holdout_brier_score` **0.2741**; `holdout_when_predicted_up_empirical_win_rate` **60.0**%. `risk_assessment.historical_1bar_return_var_95_pct` **≈ −0.56**%; `naive_long_if_model_up_max_drawdown_pct` **−1.14**. |
| **Hypothesis** | When next-bar direction model and runner are **both** strongly bearish on the same snapshot, treat **next-bar** signals as **noise / timing** only — do **not** use them alone to justify **widening** long risk or lowering long entry thresholds until a **later** snapshot (or Nautilus bundle freshness) **dissents** or a horizon check validates edge. |
| **Surface** | **Hold:** document in this log + optional **8091 briefing** appendix line (char budget); **not** `strategy_adaptation.json` or `SygnifStrategy.py` from this row alone. |
| **Risk class** | **L0** (human / briefing interpretation of training JSON; no automated trade clamp). |
| **Proof** | Re-run `training_pipeline/channel_training.py` after fresh OHLCV; if promoting: `python3 scripts/prediction_horizon_check.py …` per workflow + dry_run observation window before any L2 key. |
| **Rollback** | Delete this §5 row or replace with superseding snapshot row. |

---

## 6. Log — second rule line (Pine + algo + risk + market-data scan)

**Source:** Cross-read of `prediction_agent/btc_predict_5m.pine` (PAC/EMA trend vs sideways scalps; zones only in `trending`), `user_data/strategies/btc_trend_regime.py` (RSI 1h/4h, EMA200 1h, ADX gate), `research/nautilus_lab/btc_regime_assessment.py` (**risk_off** at **−1.15%** 1h candle + RSI thresholds), `prediction_agent/reference/README.md` (LuxAlgo **NC-SA**), `letscrash/BTC_DUMP_PROTECTION_DESIGN.md`, and market paths in `letscrash/BTC_Strategy_0.1.md` §4. **Runner vs training:** A full `channel_training` pass embeds the runner file as `recognition.btc_predict_runner_snapshot` and sets top-level `generated_utc` to the runner’s `generated_utc` when present (`predict_runner_alignment`). If files later diverge (manual edit / runner-only refresh), treat mismatch as a signal to **re-run** `channel_training`, not as an automatic trade trigger.

| Field | Content |
|-------|---------|
| **Observation** | **Pine:** `btc_predict_5m.pine` creates **demand/supply** zones only under **`trending`**; **scalp** signals fire under **`sideways`** (PAC break). **Algo:** `btc_trend_long_row` requires **RSI_14_1h & RSI_14_4h > 50**, **close > EMA_200_1h**, **ADX_14 > 25**. **Risk script:** `btc_regime_assessment.py` flags **`risk_off`** when the **current 1h** candle is **≤ −1.15%** and **RSI14_1h < 37** and **4h RSI proxy < 46**. **Reference SFP** Pine is **non-commercial** share-alike — not a production execution artifact. |
| **Hypothesis** | When **`btc_regime_assessment`** would label **`risk_off`** **or** **`btc_trend_long_row`** is **false** (chop / weak HTF structure), **do not** map **5m Pine scalps** or **LuxAlgo SFP** chart semantics into **new live long overlays** or adaptation wideners; require **either** validated **trend-long regime** **or** the **documented dump / risk-off playbook** (`BTC_DUMP_PROTECTION_DESIGN.md` + existing Sygnif exits) before treating lower-timeframe discretionary signals as **execution-grade**. |
| **Surface** | **Hold:** `letscrash/BTC_Strategy_0.1.md` registry **BTC-0.1-R02** + optional **8091 briefing** line; **not** `SygnifStrategy.py` / `strategy_adaptation.json` from this row alone. |
| **Risk class** | **L1** (cross-feed + Pine reference + regime script — research gating narrative). |
| **Proof** | After OHLCV refresh: run `python3 research/nautilus_lab/btc_regime_assessment.py` (or container equivalent) and record label; compare `btc_prediction_output.json` `generated_utc` with `training_channel_output.json`; if promoting: `pytest` for any new FT hook + `prediction_horizon_check.py` per workflow. |
| **Rollback** | Remove §6 row and delete **BTC-0.1-R02** from `BTC_Strategy_0.1.md` §5 table. |

---

## 7. Log — third rule line (PAC pullback + BullByte scalper + tagged proof capital)

**Source:** `prediction_agent/reference/justunclel_scalping_pullback_tool_r1_1_v4.pine` (archived **R1.1** study) **+** `prediction_agent/reference/bullbyte_pro_scalper_ai_mpl2.pine` (**© BullByte**, **MPL 2.0** — composite oscillator, dynamic bands, optional HTF / AI bias, **latching** Early→Strong states). JustUncleL core: **`TrendDirection`** = +1 when **fast EMA > medium EMA** and **`pacL > mediumEMA`**; −1 when **fast < medium** and **`pacU < mediumEMA`**; else 0 (yellow transition). **Buy** = `TrendDirection == 1` and **`pacExitU`** … **`pacExitL`**. Optional **HA** via `security(heikinashi(...))`. **`TradeDirection`** + TV **`alertcondition`**. BullByte: **Strong Buy/Sell** when oscillator clears dynamic thresholds with **HMA trend** and **ADX > 20**; latched vs immediate modes per inputs.

| Field | Content |
|-------|---------|
| **Observation** | JustUncleL encodes **continuation scalps** after **PAC pullback recovery** (trend filter + HA default) — same *family* as `btc_predict_5m.pine` with **fractal / HH–LL** + **alert** semantics. **BullByte Pro Scalper AI** adds a **weighted composite oscillator** (trend/momentum/vol/volume) with **std-dev bands** and **state-machine latching** for Early vs Strong signals — complementary **R03** research view; neither script runs in Freqtrade. |
| **Hypothesis** | For **rule worthiness testing** on a **10 000 USDT** reference book: allocate **exactly one-third (~3333 USDT)** of **equity at risk / staged margin** to **tagged** experimental trades only (`rule_tag` = **BTC-0.1-R01**, **R02**, or **R03** per `letscrash/btc_strategy_0_1_rule_registry.json`). **Inside that bucket**, allow **any number of concurrent opens** the venue margin allows, provided **every** fill logs **`rule_tag` + entry_reason** and **sum(open stakes in bucket) ≤ bucket cap** (rebalance on closes). **Which rule “wins”** is decided only after **horizon_check / journal win-rate by tag** — not by peak PnL over a short window. |
| **Surface** | **Hold:** `letscrash/btc_strategy_0_1_rule_registry.json` + `BTC_Strategy_0.1.md` §6; journal or export (CSV/DB) keyed by `rule_tag`. **Not** live `SygnifStrategy.py` wiring from Pine alerts without tests + sign-off. |
| **Risk class** | **L1** (operational research capital split); promotion to **L2** only with explicit caps in `strategy_adaptation.json` / FT config after proof. |
| **Proof** | Paper or **Bybit demo** with fixed 10k reference; weekly rollup: PnL and trade count **grouped by `rule_tag`**; optional `prediction_horizon_check.py save` correlation. Retire tags that underperform vs control (remaining ~6667 USDT “untagged” baseline stays conservative per R01/R02). |
| **Rollback** | Remove §7 row; delete **BTC-0.1-R03** from `btc_strategy_0_1_rule_registry.json` + `BTC_Strategy_0.1.md` §5 table / §6 bucket text; revert bucket sizing to single-strategy rules. |

---

## 8. `/ruleprediction-agent` — analysis of **BTC-0.1-R01 … R03** (2026-04-13)

Cycle: **Predict → Analyze → Proofread → Adjust** (see **`.cursor/rules/ruleprediction-agent.mdc`**).

### §8.1 L3 — first tagged trade (entry / TP / SL)

**Cross-link:** **`letscrash/BTC_Strategy_0.1.md` §7.1** — canonical table (R02 vs R03 first probe, R03 TP/RSI exits, R03 SL floor vs Sygnif doom, stack-guard loss band). Implementation: `user_data/strategies/BTC_Strategy_0_1.py` + `btc_strategy_0_1_engine.py` constants.

### Predict (what each rule *claims*)

| ID | Claim in one line |
|----|---------------------|
| **R01** | Extreme **next-bar** bearish alignment in **training JSON** is **timing noise** for capital decisions — do not **widen** long risk from that snapshot alone. |
| **R02** | **HTF / dump script** + **`btc_trend_long_row`** gate **execution-grade** mapping from **lower-TF Pine** (and NC-SA SFP reference) — need trend playbook or documented risk-off path first. |
| **R03** | **Operational experiment:** ~**⅓ of 10k** tagged capital; **journal by `rule_tag`**; “winner” = **evidence over time**, not short peak PnL; Pine (**JustUncleL** + **BullByte Pro Scalper AI**) are **conceptual siblings** to PAC work in `btc_predict_5m.pine`. |

### Analyze (coherence, overlap, tensions)

| Lens | Assessment |
|------|----------------|
| **Stacking** | **R01 + R02** stack cleanly: R01 cautions **model timing**; R02 cautions **structure + regime** before discretionary overlays. **No logical contradiction.** |
| **Overlap** | **R02** already covers much of “do not trust 5m scalps in bad structure.” **R03** (PAC pullback family) is **implementation detail** for *one* sleeve — keep it **subordinate** to R02 (no R03 live longs when `risk_off` / no trend regime, unless you explicitly scope R03 as **short-only** or **flat** tests). |
| **Tag semantics** | **R01** and **R02** are **governance / checklists**, not entry signals. Using the same `rule_tag` namespace for **“trades that obeyed R01”** vs **“JustUncleL-style sleeve R03”** mixes **policy** with **strategy** — OK for **demo journaling** if each trade logs **`entry_signal`** + **`governance_flags`**; poor for naïve PnL-by-tag unless definitions are frozen. |
| **Risk classes** | **L0 / L1 / L1** matches intent: none authorize **L3** `SygnifStrategy.py` edits. **R03** “promote to L2” is correct only with **clamped** `strategy_adaptation.json` + tests — still needs explicit owner sign-off per §4. |
| **Data freshness** | §5 snapshot is **point-in-time**; per §1, **re-ingest** `training_channel_output.json` each cycle before citing R01 metrics in briefings. |
| **Automation gap** | Freqtrade does **not** read `btc_strategy_0_1_rule_registry.json`. **Proof** for R03 requires an external **journal** (CSV/DB/Telegram) or future exporter — absent that, R03 stays **paper doctrine** only. |

### Proofread (duplication vs Sygnif + gaps)

| Check | Result |
|--------|--------|
| **SygnifStrategy** | Global **RSI / BTC** protections and exits already encode regime-ish behavior — **R02** should remain **documentation + briefing** until a diff proves a **non-duplicative** adaptation key. |
| **LuxAlgo SFP** | **NC-SA** — **R02** correctly keeps it **reference-only** for commercial Sygnif context. |
| **JustUncleL** | **License unverified** in-repo — **R03** must stay **research / demo** until cleared. |
| **Missing proof hooks** | (1) **Single timestamp** for runner + training embed. (2) **Minimum sample** per tag (e.g. ≥**N** closes or ≥**T** weeks) before “winner.” (3) **Control** sleeve (~**⅔**) must use a **frozen** rulebook so uplift is attributable. |

### Adjust (recommended next actions — policy-safe only)

1. **Clarify tags in `btc_strategy_0_1_rule_registry.json`:** each rule now has **`kind`**: **`governance`** (R01, R02) vs **`sleeve`** (R03) — use **`entry_signal`** in journals so PnL-by-tag stays interpretable.  
2. **Briefing (8091):** one **≤1 line** each for R01/R02 when JSON mtimes change — no OHLCV dump inside briefing body.  
3. **Pipeline:** ensure `channel_training` writes **`btc_predict_runner_snapshot`** at the **same** `generated_utc` boundary as `btc_prediction_output.json` when both exist (reduces false “disagreement”).  
4. **Promotion gate:** no **L2** keys from these rows until **`prediction_horizon_check.py`** + agreed **minimum sample** per tag are recorded in a dated `letscrash/` or PR note.

*End — extend this file when new data producers appear; keep tables short.*
