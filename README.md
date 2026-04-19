# SYGNIF

NFI-enhanced Freqtrade bot with Claude AI sentiment layer, optional **Nautilus Trader** BTC dock (demo/testnet), and a local **prediction** pipeline. Docker is **profile-based**: the default compose stack is `finance-agent` + `notification-handler`; legacy **spot + futures** Freqtrade and **BTC Nautilus** services are started with explicit profiles (see **Containers**).

## Architecture

```
SygnifStrategy.py          Main strategy (shared by both containers)
  |-- Multi-TF analysis     5m base + 15m/1h/4h/1d informative
  |-- BTC correlation        BTC/USDT indicators merged into all pairs
  |-- NFI indicators         RSI_3/14, Aroon, StochRSI, CMF, CCI, ROC, BB, EMA, Williams %R
  |-- Global protections     Multi-TF cascade (long + short)
  |-- Claude sentiment       Haiku analyzes news for ambiguous signals
  |-- Profit-tiered exits    NFI-style RSI exits + doom stoploss
  |-- Short logic            NFI-derived: inverted entries, exits, protections
  |-- Mover system           Top gainers/losers from Bybit (4h refresh)
  +-- Leverage callback      5x majors / 3x default / 2x movers
```

## Containers

### Default stack (no profile)

| Service                 | Port   | Role |
| ----------------------- | ------ | ---- |
| `finance-agent`         | `8091` | Telegram research bot HTTP: `/briefing`, sentiment, hooks |
| `notification-handler`| `8089` | Freqtrade webhooks → Telegram routing |

```bash
docker compose up -d
```

### Legacy multi-pair Freqtrade (profile `archived-main-traders`)

| Container           | Mode    | Port (example) | Config                          | Telegram            | DB                        |
| ------------------- | ------- | -------------- | ------------------------------- | ------------------- | ------------------------- |
| `freqtrade`         | Spot    | 8181→8080      | `user_data/config.json`         | `@sygnif_bot`       | `tradesv3.sqlite`         |
| `freqtrade-futures` | Futures | 8081           | `user_data/config_futures.json` | `@sygnifuture_bot`  | `tradesv3-futures.sqlite` |
| `trade-overseer`    | Monitor | 8090           | `trade_overseer/overseer.py`    | hedge / agent hooks | `trade_overseer/data`     |

```bash
docker compose --profile archived-main-traders up -d
```

Both traders mount `./user_data` and share strategy files. The compact `/status` patch is applied when those images are built with `Dockerfile.custom`.

### BTC Nautilus / grid (host; not in Compose)

Dedicated **Compose** services for the BTC Nautilus dock (`nautilus-research`, bar node, grid MM, `freqtrade-btc-*`) were **removed** from `docker-compose.yml`. Run sinks / bar strategy / grid MM from **`research/nautilus_lab/`** with a local venv (`requirements-bybit-demo-live.txt`), or restore **`archive/freqtrade-btc-dock-2026-04-13/`** if you need the old stack. **`scripts/start_bybit_demo_grid_mm.sh`** runs the grid runner on the host when `nautilus_trader` is installed.

### Trade Overseer (agent endpoint first)

`trade-overseer` is dockerized and can run without direct Claude API usage.

- Preferred: set `OVERSEER_AGENT_URL` (plus optional `OVERSEER_AGENT_TOKEN`) to your agent endpoint.
- Fallback: if no endpoint, it can still use `ANTHROPIC_API_KEY` (legacy path).
- Final fallback: rules-only summary if no model backend is reachable.

With the **archived-main-traders** profile, compose wires `trade-overseer` with `FT_SPOT_URL` / `FT_FUTURES_URL` defaulting to `http://freqtrade:8080/api/v1` and `http://freqtrade-futures:8081/api/v1`, `OVERSEER_HTTP_HOST=0.0.0.0`, and `FINANCE_AGENT_BRIEFING_URL` defaulting to the `finance-agent` container on the compose network.

Overseer Telegram token priority:

1. `SYGNIF_HEDGE_BOT_TOKEN` (recommended)
2. `FINANCE_BOT_TOKEN`
3. `TELEGRAM_BOT_TOKEN`

## Dashboards


| Dashboard | Port    | Server                        |
| --------- | ------- | ----------------------------- |
| Spot      | `:8888` | `dashboard_server.py`         |
| Futures   | `:8889` | `dashboard_server_futures.py` |
| BTC Terminal + Interface | `:8888` | `dashboard_server_btc_terminal.py` (`/interface` = Bybit demo; **exclusive** with spot dashboard on 8888) |


Dark theme, mobile-responsive. Spot/futures: balance, open/closed P/L, win rate, open positions, pairlist, movers, performance. **BTC Terminal** (`/`): `training_channel_output.json`, `btc_prediction_output.json`, rule registry, R01 gate — prediction / training (read-only JSON). **BTC Interface** (`/interface`): read-only **Bybit linear demo** — wallet, open orders, positions, closed P/L (`BYBIT_DEMO_*` or grid keys). Optional **reverse SSH tunnel** (`systemd/sygnif-reverse-tunnel.service` + `scripts/sygnif_reverse_tunnel.sh`) exposes that UI via a stable host you control — see **INSTANCE_SETUP.md**.

## Leverage Tiers (Futures)


| Pair type | Leverage | Examples                  |
| --------- | -------- | ------------------------- |
| Majors    | 5x       | BTC, ETH, SOL, XRP        |
| Default   | 3x       | All other pairs           |
| Movers    | 2x       | mover_gainer, mover_loser |


## Entry Logic

### Long

- **Strong TA** (score >= 75): RSI, EMA, BB, Aroon, StochRSI, CMF, multi-TF, BTC correlation
- **Claude sentiment**: Ambiguous zone (40-70), Haiku analyzes news headlines, enters if combined score >= 55
- **Mover gainer**: Momentum pullback on top gainers (RSI_3 dip + 1h trend intact)
- **Mover loser**: Mean-reversion on top losers (deep oversold + bounce signal)

### Short (Futures only)

- **Strong TA short** (score <= 25): Inverted TA score with short-specific global protections
- **Claude sentiment short**: Ambiguous zone (30-60), enters short if combined score <= 40

## Exit Logic

### Long exits

- Overbought BB + RSI, extreme RSI, multi-TF overbought, 1h BB stretch
- Profit-tiered RSI thresholds (NFI `long_exit_main` pattern)
- Williams %R overbought
- Doom stoploss (leverage-aware) + conditional u_e stoploss

### Short exits

- Oversold BB + RSI, extreme RSI < 12, multi-TF oversold, 1h BB stretch
- Profit-tiered RSI thresholds (NFI `short_exit_main` pattern, inverted)
- Williams %R oversold
- Doom stoploss (leverage-aware) + conditional stoploss

## Stoploss


| Type                   | Threshold          | Trigger                                      |
| ---------------------- | ------------------ | -------------------------------------------- |
| Doom (spot)            | -20% of entry cost | Unconditional                                |
| Doom (futures)         | -20% / leverage    | e.g. -6.7% at 3x                             |
| Conditional (long)     | -10% profit        | Below EMA200 + negative CMF + RSI divergence |
| Conditional (short)    | -10% profit        | Above EMA200 + positive CMF + RSI divergence |
| `stoploss_on_exchange` | Config-level       | Futures only, market order                   |


## Reboot Notifications

Systemd service `sygnif-notify` sends Telegram messages on system reboot:

- **Shutdown**: `"Spot/Futures down. X open trades. [DRY/LIVE]"`
- **Startup**: `"Spot/Futures up. X open trades. Pairs updated. [DRY/LIVE]"`

## Files


| File                                            | Purpose                                                 |
| ----------------------------------------------- | ------------------------------------------------------- |
| `SygnifStrategy.py`                             | Main strategy                                           |
| `docker-compose.yml`                            | Dual container setup with auto-patching entrypoint      |
| `docker/Dockerfile.custom`                      | Base image + feedparser, pandas_ta                      |
| `notify.sh`                                     | Telegram reboot notifier                                |
| `update_movers.py`                              | Fetches top Bybit movers, writes `movers_pairlist.json` |
| `dashboard.html`                                | Spot dashboard                                          |
| `dashboard_futures_full.html`                   | Futures dashboard (adds Side/Leverage columns)          |
| `dashboard_server.py`                           | Serves spot dashboard on :8888                          |
| `dashboard_server_futures.py`                   | Serves futures dashboard on :8889                       |
| `dashboard_btc_terminal.html` / `dashboard_server_btc_terminal.py` | Sygnif BTC Terminal (:8888) — prediction + training + serves `/interface` |
| `dashboard_btc_interface.html` / `dashboard_server_btc_interface.py` | Bybit demo UI (served under `/interface` on :8888; standalone script optional) |
| `letscrash/BTC_STRATEGY_0_1_BYBIT_BRIDGE.md` | **BTC_Strategy_0_1** + Bybit **demo** CCXT bridge (`bybit_ccxt_demo_patch`, Docker, configs) |
| `user_data/config_btc_strategy_0_1_bybit_demo.example.json` | Example futures **demo** config for `BTC_Strategy_0_1` (no secrets in git) |
| `prediction_agent/btc_predict_runner.py`        | BTC batch prediction runner (RF + XGB + LogReg) → `btc_prediction_output.json` |
| `prediction_agent/btc_predict_live.py`          | Rolling 5m OHLCV fit for Nautilus bar node (lighter estimators) |
| `research/nautilus_lab/run_sygnif_btc_trading_node.py` | Nautilus `TradingNode` entrypoint (demo / testnet) |
| `research/nautilus_lab/sygnif_btc_bar_node_strategy.py` | Bar strategy: quotes, gates, optional live ML + orders |
| `scripts/btc_analysis_forceenter.py` / `prediction_agent/btc_analysis_order_signal.py` | Optional **forceenter** from prediction JSON (dry-run default; see `BTC_STRATEGY_0_1_BYBIT_BRIDGE.md` §7) |
| `prediction_agent/prediction_code_extracted.py` | Consolidated upstream prediction library                |
| `status_patch.py` / `status_patch_v2.py`        | Compact `/status` Telegram command                      |
| `fill_patch.py`                                 | Order fill notification patch                           |
| `config_claude_bot.example.json`                | Example config                                          |
| `telemetry.py`                                  | Optional telemetry                                      |
| `tf_controller.py` / `tf_switch.py`             | Timeframe switching utilities                           |


## Prediction Agent (`prediction_agent/`)

Local ML-based BTC price and direction forecasting — **no API keys, no cloud, all free**.

Extracted and consolidated from [BitVision](https://github.com/shobrook/BitVision) (MIT) and [CryptoPredictions](https://github.com/alimohammadiamirhossein/CryptoPredictions), then wired to live Bybit OHLCV data from `finance_agent/btc_specialist/data/`.

### Models


| Model               | Type           | Library      | Output                           |
| ------------------- | -------------- | ------------ | -------------------------------- |
| RandomForest        | Regression     | scikit-learn | Next-bar mean price              |
| XGBoost             | Regression     | xgboost      | Next-bar mean price              |
| Logistic Regression | Classification | scikit-learn | Direction (UP/DOWN) + confidence |


All three vote on a **consensus** signal (BULLISH / BEARISH / MIXED).

### Usage

```bash
# 1h candles (default)
python3 prediction_agent/btc_predict_runner.py

# Daily candles
python3 prediction_agent/btc_predict_runner.py --timeframe daily

# Custom look-back window
python3 prediction_agent/btc_predict_runner.py --window 10 --timeframe 1h
```

Output: console summary + `prediction_agent/btc_prediction_output.json`.

**Live loop (Nautilus):** `btc_predict_live.py` reuses the same feature engineering; it seeds from public Bybit **linear 5m** klines, refits smaller RF/XGB/LogReg on each closed bar (background thread), applies `nautilus_enhanced_consensus` from sidecar JSON, and can rewrite `btc_prediction_output.json` for dashboards. Host jobs such as `scripts/sygnif_finetune_automation.sh` remain a heavier offline pass.

### Backtest Metrics (sample)


| Model            | Timeframe | MAE    | MAPE  | Direction Acc    |
| ---------------- | --------- | ------ | ----- | ---------------- |
| RandomForest     | 1h        | $733   | 1.00% | 51.4%            |
| XGBoost          | 1h        | $706   | 0.97% | 54.3%            |
| LogReg direction | 1h        | —      | —     | 65.7% (F1 68.4%) |
| RandomForest     | daily     | $639   | 0.92% | 92.3%            |
| XGBoost          | daily     | $1,515 | 2.18% | 84.6%            |
| LogReg direction | daily     | —      | —     | 84.6% (F1 90.0%) |


### Reference library

`prediction_agent/prediction_code_extracted.py` contains the full consolidated prediction code from both upstream repos (30+ indicators, 9 model backends, metrics, backtest strategies). See `prediction_agent/SOURCES.md` for attribution and upstream commits.

### Dependencies

```bash
pip3 install scikit-learn xgboost statsmodels  # only these three needed for the runner
```

## Network monorepo (`network/`)

SYGNIF vendors **[Giansn/Network](https://github.com/Giansn/Network)** as a **git submodule** at `[network/](https://github.com/Giansn/SYGNIF/tree/main/network)` (VPC / Client VPN / SSM helpers, OpenVINO edge smoke + split placement + MCP). It is **not** Freqtrade strategy code — keep strategy changes under `user_data/strategies/`.


| In SYGNIF                                | Upstream                                                       |
| ---------------------------------------- | -------------------------------------------------------------- |
| `network/` submodule                     | [github.com/Giansn/Network](https://github.com/Giansn/Network) |
| Update script                            | `./scripts/update_network_submodule.sh`                        |
| Linear workflow env (8093 → 8091 → 8090) | `scripts/linear_workflow.env.example`                          |


```bash
git submodule update --init --remote network   # or use scripts/update_network_submodule.sh
```

## Quick Start

```bash
# Clone (includes Network monorepo under network/ via submodule)
git clone --recurse-submodules https://github.com/Giansn/SYGNIF.git
cd SYGNIF
# If you already cloned without submodules: git submodule update --init --recursive

# Editor (optional): open the folder in Cursor/VS Code — `.vscode/extensions.json` recommends
# Python, Docker, GitLens, AWS Toolkit, YAML, and cfn-lint for `network/aws-node-network/` templates.

# Configure
cp config_claude_bot.example.json user_data/config.json
# Edit user_data/config.json and user_data/config_futures.json with API keys
# Edit .env with BYBIT keys, ANTHROPIC_API_KEY, TELEGRAM tokens

# Build and run — default: finance-agent + notification-handler
docker compose up -d

# Optional: archived spot + futures Freqtrade + overseer
# docker compose --profile archived-main-traders up -d --build

# Dashboards
python3 dashboard_server.py &
python3 dashboard_server_futures.py &

# Enable reboot notifications
sudo cp /etc/systemd/system/sygnif-notify.service  # (see SETUP.md)
sudo systemctl enable --now sygnif-notify
```

## Environment Variables (.env)

```
BYBIT_API_KEY=
BYBIT_API_SECRET=
ANTHROPIC_API_KEY=
TELEGRAM_CHAT_ID=
```

## Cost

Claude Haiku sentiment: ~~$0.50-1.00/month (~~20 calls/day).