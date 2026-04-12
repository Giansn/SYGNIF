# letscrash

Design notes and **plans** that may land in main Sygnif code later. Contents here are **not** executed by default.

| Document | Purpose |
|----------|---------|
| [PREDICTION_PIPELINE_AND_SELF_LEARNING_PLAN.md](./PREDICTION_PIPELINE_AND_SELF_LEARNING_PLAN.md) | Prediction engine, briefing HTTP ports, pipeline, bounded self-learning, RAM |
| [BTC_TRADING_DOCKER_SYGNIF_INHERIT_DESIGN.md](./BTC_TRADING_DOCKER_SYGNIF_INHERIT_DESIGN.md) | Optional BTC-only Freqtrade Docker service + **ruleprediction-agent** + **sygnif-agent-inherit** mapping |
| `../user_data/config_btc_spot_dedicated.example.json` | Example Freqtrade config for that service (copy to `config_btc_spot_dedicated.json`) |
| [BTC_TRADER_DOCKER.md](./BTC_TRADER_DOCKER.md) | **btc_Trader_Docker**: Image mit `yfinance`, Build/Compose, kein Host-`--break-system-packages` |
| [RULE_AND_DATA_FLOW_LOOP.md](./RULE_AND_DATA_FLOW_LOOP.md) | Kontinuierlicher Rule-/Informations-Loop, **btc_Trader_Docker**-I/O, Agent-Querverweise, Indikator-/Feed-Wishlist (TV, Bybit, crypto-market-data) |
| [BTC_DUMP_PROTECTION_DESIGN.md](./BTC_DUMP_PROTECTION_DESIGN.md) | BTC **Dump-Schutz** / Short+Trail-Inspiration (QuantumEdge + chikaharu MA), Mapping zu Sygnif + **`ruleprediction-agent`**-Loop |
