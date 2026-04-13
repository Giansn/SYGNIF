# SYGNIF

NFI-enhanced Freqtrade bot with Claude AI sentiment layer. Runs spot + futures on Bybit via dual Docker containers.

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


| Container           | Mode    | Port | Config                          | Telegram            | DB                        |
| ------------------- | ------- | ---- | ------------------------------- | ------------------- | ------------------------- |
| `freqtrade`         | Spot    | 8080 | `user_data/config.json`         | `@sygnif_bot`       | `tradesv3.sqlite`         |
| `freqtrade-futures` | Futures | 8081 | `user_data/config_futures.json` | `@sygnifuture_bot`  | `tradesv3-futures.sqlite` |
| `trade-overseer`    | Monitor | 8090 | `trade_overseer/overseer.py`    | `@Sygnif_hedge_bot` | `trade_overseer/data`     |


Both mount the same `./user_data` volume and share the strategy file. The compact `/status` patch is auto-applied on container start via the entrypoint.

### Trade Overseer (agent endpoint first)

`trade-overseer` is dockerized and can run without direct Claude API usage.

- Preferred: set `OVERSEER_AGENT_URL` (plus optional `OVERSEER_AGENT_TOKEN`) to your agent endpoint.
- Fallback: if no endpoint, it can still use `ANTHROPIC_API_KEY` (legacy path).
- Final fallback: rules-only summary if no model backend is reachable.

`docker compose up` wires `**trade-overseer**` with `**FT_SPOT_URL` / `FT_FUTURES_URL**` pointing at the `**freqtrade**` and `**freqtrade-futures**` service names (not `127.0.0.1`), `**OVERSEER_HTTP_HOST=0.0.0.0**`, and `**FINANCE_AGENT_BRIEFING_URL**` defaulting to `**host.docker.internal**` so TA briefing can reach a finance agent running on the host.

Overseer Telegram token priority:

1. `SYGNIF_HEDGE_BOT_TOKEN` (recommended)
2. `FINANCE_BOT_TOKEN`
3. `TELEGRAM_BOT_TOKEN`

## Dashboards


| Dashboard | Port    | Server                        |
| --------- | ------- | ----------------------------- |
| Spot      | `:8888` | `dashboard_server.py`         |
| Futures   | `:8889` | `dashboard_server_futures.py` |
| BTC Terminal | `:8891` | `dashboard_server_btc_terminal.py` |


Dark theme, mobile-responsive. Spot/futures: balance, open/closed P/L, win rate, open positions, pairlist, movers, performance. **BTC Terminal:** `training_channel_output.json`, `btc_prediction_output.json`, rule registry, R01 gate — prediction / training only (no Freqtrade API proxy). Optional **reverse SSH tunnel** (`systemd/sygnif-reverse-tunnel.service` + `scripts/sygnif_reverse_tunnel.sh`) exposes that UI via a stable host you control — see **INSTANCE_SETUP.md**.

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
| `dashboard_btc_terminal.html` / `dashboard_server_btc_terminal.py` | Sygnif BTC Terminal (:8891) — prediction + training |
| `letscrash/BTC_STRATEGY_0_1_BYBIT_BRIDGE.md` | **BTC_Strategy_0_1** + Bybit **demo** CCXT bridge (`bybit_ccxt_demo_patch`, Docker, configs) |
| `user_data/config_btc_strategy_0_1_bybit_demo.example.json` | Example futures **demo** config for `BTC_Strategy_0_1` (no secrets in git) |
| `prediction_agent/btc_predict_runner.py`        | BTC prediction runner (RF + XGB + LogReg)               |
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

# Build and run
docker compose up -d

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