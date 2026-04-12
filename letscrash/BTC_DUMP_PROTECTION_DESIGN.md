# BTC dump protection — design (inspiration: QuantumEdge + state-aware MA) + **`/ruleprediction-agent`** loop

**Goal:** **Dump recognition** → defensive or **short** bias with **trailing / staged exits**, aligned with **bounded** continuous improvement under **`.cursor/rules/ruleprediction-agent.mdc`** and **`letscrash/RULE_AND_DATA_FLOW_LOOP.md`** (never silent live wiring; prove → test → apply or remove).

**Reference Pine (MPL 2.0, not live in Freqtrade):**

| File | Ideas to port (conceptually) |
|------|-------------------------------|
| `prediction_agent/reference/quantum_edge_manual_pro_mpl2.pine` | Wallet % margin, leverage, **ATR** initial SL + **TP1/TP2/TP3** (50% / 30% / 20% notional), **trail** after TP3, **liq vs SL** warning, **bull/bear score** (VWAP, RSI, EMA cross, ADX/DMI), “trusted” filter |
| `prediction_agent/reference/chikaharu_state_aware_ma_cross_mpl2.pine` | **Regime** `00/01/10/11` from EMA20 slope + price vs MA; **state-dependent** MA pair; **crossunder** → close all (defensive exit — useful for **dump / risk-off** framing) |

---

## 1. Dump recognition (signals, not Pine in prod)

Translate TV-style metrics into **Sygnif** vocabulary (see **`finance_agent/bot.py`** / **`SygnifStrategy`**):

| Pine / concept | Possible FT-side analogue |
|----------------|---------------------------|
| `bullPct` / `bearPct` vs threshold | Extend or mirror **TA score** components + **BTC** regime columns (`btc_trend_regime`, global RSI protections) |
| `vocalSignal` (rel vol) | Existing **volume** gates (`vol_sma_25`, `strong_ta` vol mult) |
| `emaFast`/`emaSlow` cross + score | Already partially overlaps **EMA** + momentum stack — do **not** duplicate blindly; diff against live tags |
| ADX + DMI | Already in TA score — align thresholds in **`strategy_adaptation.json`** first |

**Shorts:** Freqtrade futures + `strong_ta_short` / `claude_short_*` / `swing_failure_short` — map “dump” to **existing** tags before adding new ones.

---

## 2. Risk + trailing (from QuantumEdge)

| Mechanism | Sygnif direction |
|-----------|------------------|
| Staged TP + move SL to BE / prior TP | Compare to **`sf_*`** / soft SL / `exit_profit_rsi_*` — may be **adaptation JSON** tunables before new code |
| Trail after “TP3” | Optional new profile or **custom_exit** hook — **requires tests** + GitNexus impact |
| Liq vs SL warning | Education / dashboard only unless exchange supports isolated margin math in strategy |

---

## 3. Continuous loop (**`/ruleprediction-agent`**)

1. **Ingest:** BTC 1h/4h returns, `manifest.json`, `btc_prediction_output.json` (if used), FT trade logs, `prediction_horizon_check` outcomes.  
2. **Propose:** Parameter shifts in **`strategy_adaptation.json`** (clamped) or doc-only hypothesis.  
3. **Prove:** `pytest`, dry-run on **btc_Trader_Docker**, horizon check.  
4. **Test false → rm:** revert adaptation keys / doc.  
5. **Test true → apply:** merge + reload; **compare** next cycle vs same feeds.

---

## 4. TradingView indicators you may add later

- **VPVR / fixed range volume** (export → CSV) for **support** under dump.  
- **Session killzones** (ICT-style) — document in Pine reference folder with license.  
- **LuxAlgo SFP** (`reference/luxalgo_…`) — already present; **NC-SA** restricts commercial path.

---

## 5. Out of scope (until explicitly approved)

- Auto **short** from “dump score” without slot caps and futures protections.  
- Wiring Pine **directly** into `SygnifStrategy` without Python parity tests.

---

*MPL 2.0: if you distribute modified versions of the reference Pine, follow [Mozilla MPL 2.0](https://www.mozilla.org/MPL/2.0/) notice requirements.*
