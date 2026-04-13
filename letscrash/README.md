# letscrash

Design notes and **plans** that may land in main Sygnif code later. Most of this folder stays **design-only**; exceptions are **documented** when compose + scripts in the repo already run (e.g. **Nautilus → training inflow** below).

| Document | Purpose |
|----------|---------|
| [PREDICTION_PIPELINE_AND_SELF_LEARNING_PLAN.md](./PREDICTION_PIPELINE_AND_SELF_LEARNING_PLAN.md) | Prediction engine, briefing HTTP ports, pipeline, bounded self-learning, RAM |
| [BTC_TRADING_DOCKER_SYGNIF_INHERIT_DESIGN.md](./BTC_TRADING_DOCKER_SYGNIF_INHERIT_DESIGN.md) | Optional BTC-only Freqtrade Docker service + **ruleprediction-agent** + **sygnif-agent-inherit** mapping |
| `../user_data/config_btc_spot_dedicated.example.json` | Example Freqtrade config for that service (copy to `config_btc_spot_dedicated.json`) |
| [BTC_TRADER_DOCKER.md](./BTC_TRADER_DOCKER.md) | **btc_Trader_Docker**: Image mit `yfinance`, Build/Compose, kein Host-`--break-system-packages`; **§3b** merge mit **Nautilus**-Feed |
| [RULE_AND_DATA_FLOW_LOOP.md](./RULE_AND_DATA_FLOW_LOOP.md) | Kontinuierlicher Rule-/Informations-Loop, **btc_Trader_Docker**-I/O, Agent-Querverweise, Indikator-/Feed-Wishlist (TV, Bybit, crypto-market-data) |
| [RULE_GENERATION_FROM_INCOMING_DATA.md](./RULE_GENERATION_FROM_INCOMING_DATA.md) | **`/ruleprediction-agent`**: datengetriebene **Trading-Rule-Vorschläge** (Nautilus, training JSON, Runner) → Prove/Test/Apply |
| [BTC_Strategy_0.1.md](./BTC_Strategy_0.1.md) | **BTC_Strategy_0.1**: Pine-/Algo-/Risk-/Market-**Scan** + **Regelregister** (R01–R03) + **10k/⅓**-Bucket; `btc_strategy_0_1_rule_registry.json` (**`rule_tag`**) |
| [BTC_STRATEGY_0_1_BYBIT_BRIDGE.md](./BTC_STRATEGY_0_1_BYBIT_BRIDGE.md) | **Bybit demo + CCXT**: `bybit_ccxt_demo_patch.py`, Docker bake/entrypoint, futures `exchange` block (`enableDemoTrading` / `hostname`), config matrix |
| `../user_data/config_btc_strategy_0_1_bybit_demo.example.json` | **Futures demo** template for `BTC_Strategy_0_1` — copy to gitignored `config_futures.json`; keys from Bybit demo UI |
| [BTC_DUMP_PROTECTION_DESIGN.md](./BTC_DUMP_PROTECTION_DESIGN.md) | BTC **Dump-Schutz** / Short+Trail-Inspiration (QuantumEdge + chikaharu MA), Mapping zu Sygnif + **`ruleprediction-agent`**-Loop |
| `../user_data/config_btc_spot_dedicated.bybit_demo.example.json` | **Bybit demo** CCXT URLs (`api-demo.bybit.com`) + `defaultType: spot` — copy/merge for demo keys |
| [../research/nautilus_lab/README.md](../research/nautilus_lab/README.md) | **Implementiert:** Nautilus **`BybitHttpClient`** (spot **BTC/USDT** only) → `btc_1h_ohlcv.json`, `btc_daily_90d.json`, `nautilus_spot_btc_market_bundle.json`; Compose **`docker-compose.yml`** profile **`btc-nautilus`** |

### Nautilus ↔ training (kurz, Apr 2026)

- **Script:** `research/nautilus_lab/bybit_nautilus_spot_btc_training_feed.py` — Market-Endpoints (Bars 1h/1d, Tickers, Trades, Orderbuch-Deltas, Status, optional Fees) **ohne CCXT**.
- **Out:** `finance_agent/btc_specialist/data/` — füttert **`training_pipeline/channel_training.py`** + **`btc_predict_runner`**; **`btc_regime_assessment`** bleibt über `btc_1h_ohlcv_nautilus_bybit.json` / Pfade in `_load_ohlcv_local` bedient.
- **Rule / Agent:** `.cursor/rules/ruleprediction-agent.mdc` (neue globs), `.cursor/agents/prediction-agent.md` (Ground truth), `letscrash/RULE_AND_DATA_FLOW_LOOP.md` §3 Tabelle.
