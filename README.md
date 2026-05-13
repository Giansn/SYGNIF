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

| Container | Mode | Port | Config | Telegram | DB |
|-----------|------|------|--------|----------|----|
| `freqtrade` | Spot | 8080 | `user_data/config.json` | `@sygnif_bot` | `tradesv3.sqlite` |
| `freqtrade-futures` | Futures | 8081 | `user_data/config_futures.json` | `@sygnifuture_bot` | `tradesv3-futures.sqlite` |
| `trade-overseer` | Monitor | 8090 | `trade_overseer/overseer.py` | `@Sygnif_hedge_bot` | `trade_overseer/data` |

Both mount the same `./user_data` volume and share the strategy file. The compact `/status` patch is auto-applied on container start via the entrypoint.

### Trade Overseer (agent endpoint first)

`trade-overseer` is dockerized and can run without direct Claude API usage.

- Preferred: set `OVERSEER_AGENT_URL` (plus optional `OVERSEER_AGENT_TOKEN`) to your agent endpoint.
- Fallback: if no endpoint, it can still use `ANTHROPIC_API_KEY` (legacy path).
- Final fallback: rules-only summary if no model backend is reachable.

`docker compose up` wires **`trade-overseer`** with **`FT_SPOT_URL` / `FT_FUTURES_URL`** pointing at the **`freqtrade`** and **`freqtrade-futures`** service names (not `127.0.0.1`), **`OVERSEER_HTTP_HOST=0.0.0.0`**, and **`FINANCE_AGENT_BRIEFING_URL`** defaulting to **`host.docker.internal`** so TA briefing can reach a finance agent running on the host.

Overseer Telegram token priority:
1. `SYGNIF_HEDGE_BOT_TOKEN` (recommended)
2. `FINANCE_BOT_TOKEN`
3. `TELEGRAM_BOT_TOKEN`

## Dashboards

| Dashboard | Port | Server |
|-----------|------|--------|
| Spot | `:8888` | `dashboard_server.py` |
| Futures | `:8889` | `dashboard_server_futures.py` |

Dark theme, mobile-responsive. Stats: balance, open/closed P/L, win rate, open positions table, pairlist, top movers, performance.

## Leverage Tiers (Futures)

| Pair type | Leverage | Examples |
|-----------|----------|----------|
| Majors | 5x | BTC, ETH, SOL, XRP |
| Default | 3x | All other pairs |
| Movers | 2x | mover_gainer, mover_loser |

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

| Type | Threshold | Trigger |
|------|-----------|---------|
| Doom (spot) | -20% of entry cost | Unconditional |
| Doom (futures) | -20% / leverage | e.g. -6.7% at 3x |
| Conditional (long) | -10% profit | Below EMA200 + negative CMF + RSI divergence |
| Conditional (short) | -10% profit | Above EMA200 + positive CMF + RSI divergence |
| `stoploss_on_exchange` | Config-level | Futures only, market order |

## Reboot Notifications

Systemd service `sygnif-notify` sends Telegram messages on system reboot:
- **Shutdown**: `"Spot/Futures down. X open trades. [DRY/LIVE]"`
- **Startup**: `"Spot/Futures up. X open trades. Pairs updated. [DRY/LIVE]"`

## Files

| File | Purpose |
|------|---------|
| `SygnifStrategy.py` | Main strategy |
| `docker-compose.yml` | Dual container setup with auto-patching entrypoint |
| `docker/Dockerfile.custom` | Base image + feedparser, pandas_ta |
| `notify.sh` | Telegram reboot notifier |
| `update_movers.py` | Fetches top Bybit movers, writes `movers_pairlist.json` |
| `dashboard.html` | Spot dashboard |
| `dashboard_futures_full.html` | Futures dashboard (adds Side/Leverage columns) |
| `dashboard_server.py` | Serves spot dashboard on :8888 |
| `dashboard_server_futures.py` | Serves futures dashboard on :8889 |
| `status_patch.py` / `status_patch_v2.py` | Compact `/status` Telegram command |
| `fill_patch.py` | Order fill notification patch |
| `config_claude_bot.example.json` | Example config |
| `telemetry.py` | Optional telemetry |
| `tf_controller.py` / `tf_switch.py` | Timeframe switching utilities |

## Quick Start

```bash
# Clone
git clone https://github.com/Giansn/SYGNIF.git
cd SYGNIF

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

Claude Haiku sentiment: ~$0.50-1.00/month (~20 calls/day).
