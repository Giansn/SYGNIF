# freqtrade-btc-dock — retained repo assets (nothing deleted)

These paths stay in the tree; this file is the “extract” checklist for operators.

## Compose (archived profile name)

- **Old profile:** `freqtrade-btc-dock`
- **New profile:** `archived-freqtrade-btc-dock` (same three services; opt-in restore only)

## Services & ports (unchanged definitions)

| Service | Host port | Notes |
|---------|-----------|--------|
| `freqtrade-btc-spot` | 127.0.0.1:8282 → 8080 | `Dockerfile.btc_trader`, `run_freqtrade_btc_spot_demo.sh` |
| `freqtrade-btc-0-1` | 127.0.0.1:8185 → 8085 | `BTC_Strategy_0_1`, `run_freqtrade_btc_0_1_demo.sh`; also profile **`btc-0-1`** |
| `trade-overseer` | 127.0.0.1:8090 | `FT_SPOT_URL` / `FT_FUTURES_URL` default to the two containers above |

## Runtime / config (still in `user_data/`)

- `run_freqtrade_btc_spot_demo.sh`, `run_freqtrade_btc_0_1_demo.sh`
- `apply_bybit_demo_to_btc_spot_config.py`, `apply_bybit_demo_to_btc_0_1_config.py`
- `config_btc_spot_dedicated*.json`, `config_btc_strategy_0_1_*.json`
- `strategies/BTC_Strategy_0_1.py`, `SygnifStrategy.py` (spot dedicated)
- SQLite: e.g. `tradesv3-btc01-bybit-demo.sqlite` (0.1 bot)

## Operator scripts

- `scripts/ft_btc_0_1_forceenter.py`, `scripts/ft_btc_0_1_from_24h_forecast.py`
- `scripts/rebuild_freqtrade_btc_0_1.sh`, `scripts/btc01_r01_r02_report.py`
- `scripts/deploy_health_check.sh` (optional pings :8282 / :8185)

## Docs / design

- `letscrash/BTC_STRATEGY_0_1_BYBIT_BRIDGE.md`, `BTC_TRADER_DOCKER.md`, `RULE_AND_DATA_FLOW_LOOP.md`, `BTC_TRADING_DOCKER_SYGNIF_INHERIT_DESIGN.md`
- `INSTANCE_SETUP.md` (paper 0.1, ports)
- `dashboard_server_btc01_grid.py`, `dashboard_btc01_grid.html` (container log name `freqtrade-btc-0-1`)

## Nautilus overlap

- `nautilus-research` + `finance_agent/btc_specialist/data/` remain the **canonical** training/prediction feed; Freqtrade bots only **read** that mount where configured.
