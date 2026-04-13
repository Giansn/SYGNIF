# Reference Pine (third-party)

Files here are **not** executed by Freqtrade. They document **TradingView** logic for alignment with Sygnif research (e.g. swing / SFP semantics in `SygnifStrategy`).

## LuxAlgo — Swing Failure Pattern

| File | License |
|------|---------|
| `luxalgo_swing_failure_pattern_cc_by_nc_sa_4.pine` | **CC BY-NC-SA 4.0** — [summary](https://creativecommons.org/licenses/by-nc-sa/4.0/) |

**NonCommercial (NC):** commercial deployment (live trading products, paid signals, etc.) may **not** be covered by this license. Obtain rights from **LuxAlgo** if you need commercial use. **ShareAlike (SA):** derivatives must use a compatible license.

Attribution: **© LuxAlgo** (as in source header).

**TradingView:** [Swing Failure Pattern (SFP) \[LuxAlgo\]](https://www.tradingview.com/script/YmWELClV-Swing-Failure-Pattern-SFP-LuxAlgo/) (open-source on chart; updates may diverge from this file — diff against TV export if needed).

## MPL 2.0 references (Mozilla)

| File | Attribution | Use in SYGNIF |
|------|-------------|---------------|
| `quantum_edge_manual_pro_mpl2.pine` | Header only in paste (indicator name **QuantumEdge**); **MPL 2.0** | Inspiration for **BTC dump protection** / staged TP–SL–trail — see `letscrash/BTC_DUMP_PROTECTION_DESIGN.md` |
| `chikaharu_state_aware_ma_cross_mpl2.pine` | **© chikaharu**, MPL 2.0 | Regime-aware MA cross; defensive `close_all` on crossunder — compare to risk-off / dump exit framing |
| `chikaharu_trend_volatility_index_tvi_mpl2.pine` | **© chikaharu**, MPL 2.0 | **TVI**: MA-band “scatter” / Gini-style mean diff → synthetic OHLC candles + ATR & HL-range reference; regime / vol expansion context (not Sygnif TA) |

If you redistribute **modified** Pine, include MPL notices per [MPL 2.0 FAQ](https://www.mozilla.org/MPL/2.0/).

## BullByte — Pro Scalper AI (Pine v6)

| File | Attribution | License / use |
|------|-------------|----------------|
| `bullbyte_pro_scalper_ai_mpl2.pine` | **© BullByte** (header in file) | **MPL 2.0** — archival copy for research; **not** executed by Freqtrade. |

**SYGNIF mapping:** **BTC-0.1-R03** sleeve research — composite oscillator + latching signals vs **JustUncleL** PAC / `btc_predict_5m.pine` scalping semantics. Optional **AI forecast** branch is **off** in saved defaults; enable only after understanding threshold shift behaviour.

## JustUncleL — Scalping PullBack Tool (Pine v4 study)

| File | Attribution | License / use |
|------|-------------|----------------|
| `justunclel_scalping_pullback_tool_r1_1_v4.pine` | **JustUncleL** (header in file; R1.1, 4-Feb-2020) | **Archival copy** from public Pine paste — **not** executed by Freqtrade. **Confirm** TradingView / author terms before **commercial** redistribution or paid signals. |

**SYGNIF mapping:** PAC pullback recovery (`TrendDirection`, `pacExitU` / `pacExitL`, optional HA via `security`) — see `letscrash/BTC_Strategy_0.1.md` and `letscrash/RULE_GENERATION_FROM_INCOMING_DATA.md` §7. Compare to `prediction_agent/btc_predict_5m.pine` (similar PAC/EMA stack; different entry packaging).
