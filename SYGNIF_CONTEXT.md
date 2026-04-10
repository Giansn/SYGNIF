# Sygnif — Freqtrade Trading Bot

## Overview

Dual-mode (spot + futures) crypto trading bot on Freqtrade with AI sentiment analysis. Runs on Bybit via Docker on AWS EC2 (eu-central-1).

## Architecture

| Component | Description |
|---|---|
| `SygnifStrategy.py` | Main strategy — NFI-derived, multi-TF analysis, Claude sentiment layer |
| `user_data/config.json` | Spot config (port 8080) |
| `user_data/config_futures.json` | Futures config (port 8081, isolated margin, 2-5x leverage) |
| `trade_overseer/` | Trade management and analysis system |
| `notification_handler.py` | Webhook notifications |
| `docker-compose.yml` | 3 containers: freqtrade, freqtrade-futures, notification-handler |

## Strategy Design

### Entry Types

| Tag | Side | Trigger |
|---|---|---|
| `strong_ta` | Long | TA score >= 65 + volume > 1.2x SMA25 |
| `strong_ta_short` | Short | TA score <= 25 (vectorized) |
| `claude_s{N}` | Long | TA 40-70 + Claude sentiment, combined >= 55 |
| `claude_short_s{N}` | Short | TA 30-60 + Claude sentiment, combined <= 40 |
| `claude_swing` | Long | Failure swing + TA >= 50 |
| `claude_swing_short` | Short | Failure swing + TA <= 50 |
| `swing_failure` | Long | Failure swing standalone |
| `swing_failure_short` | Short | Failure swing standalone |

### Exit Logic

- **Profit-tiered RSI exits**: leverage-aware (profit/leverage normalization)
- **Williams %R**: overbought/oversold exits at > 2% profit
- **Ratcheting trailing stop**: -3% at 2%+, -2% at 5%+, -1.5% at 10%+ profit
- **Soft stoploss**: 0.8x doom threshold, requires 3-bar RSI slope confirmation
- **Failure swing exits**: EMA-TP target or volatility-adjusted SL

### Risk Management

- **Doom stoploss**: -20% (divided by leverage for futures), placed on exchange
- **Doom cooldown**: 4h lockout per pair after stoploss hit
- **Consecutive loss lockout**: 2+ SL hits on same pair in 24h → 24h block
- **Slot caps**: max 6 strong_ta, max 4 swing trades open simultaneously
- **Futures volume gate**: vol_sma_25 > 50k required (except swings)
- **Global protections**: multi-TF RSI cascade blocks entries during crashes (long) / pumps (short)

### Failure Swing Parameters

- S/R window: 48 bars (4h on 5m TF)
- Volatility filter: > 3% distance from EMA_120
- Stability: S/R unchanged for 2 bars
- Dynamic SL/TP: volatility-adjusted coefficients

### Leverage Tiers

- Majors (BTC, ETH, SOL, XRP): 5x
- Default: 3x
- ATR > 3%: capped at 2x
- ATR > 2%: capped at 3x

## SL Architecture (updated 2026-04-06)

### Ratcheting Trail (on-exchange, price-based)

| P&L Threshold | Trail Distance | Effect at 5x |
|---|---|---|
| >= +10% | -1.5% price | Locks in ~+7.5% P&L |
| >= +5% | -2% price | Locks in ~+3% P&L |
| >= +2% | -3% price | Prevents doom from +2% |
| >= +1% | -1% price | Breakeven guard, worst ~-4% P&L |

### Soft & Doom SL

| Layer | Spot | Futures | Notes |
|---|---|---|---|
| Soft SL | -12% P&L | -12% P&L | Requires RSI slope confirmation. Configurable via `soft_sl_ratio_spot` / `soft_sl_ratio_futures` (default 0.60) |
| Doom SL | -20% P&L | -20% P&L / leverage (price) | Hard stop on exchange. Non-negotiable. |

### Validation Criteria

These fixes were deployed 2026-04-06. The strategy proves itself when:

1. **No more +profit-to-doom reversals** — Trades that reached +1% P&L should NOT appear in logs with `exit_reason: stoploss_on_exchange`. The breakeven guard should catch them as ratcheted trail exits instead.
2. **Soft SL fires at correct levels** — `exit_stoploss_conditional` exits should show `current_profit` near -12%, NOT at -3% to -5% (the old broken range at 3-5x leverage). **Known limitation 2026-04-07:** the entire `custom_exit` indicator-exit path (`exit_profit_rsi_*`, `exit_willr_reversal`, `exit_stoploss_conditional`, `exit_sf_*`) fires very rarely because the ratchet trail's tier thresholds (1%/2%/5%/10% P&L) usually trigger before custom_exit's leverage-multiplied profit gates (`0.02 × leverage`). First proven fire post-fix: AVAX `exit_short_willr_reversal` +4.04% on 2026-04-07. When indicator exits do fire, they capture 3-5x more upside than ratchet exits.
3. **Win/loss ratio improves on futures** — The old double-division bug was cutting winners short while letting losers run full doom distance. Futures P&L should trend toward symmetry.
4. **Fewer doom exits overall** — With breakeven guard + correct soft SL, fewer trades should reach the -20% hard stop.

### Tuning State (2026-04-07)

| Parameter | Value | Notes |
|---|---|---|
| `CooldownPeriod` (line 372) | **2 candles (10 min)** | 5→1 caused SIREN whipsaw (50s re-entry); 1→2 blocks same-pair churn while preserving cross-pair rotation |
| `max_open_trades` (futures) | **12** | 10 normal + 2 reserved for premium tags |
| `dry_run_wallet` (futures) | **$240** | $192 tradable / 12 slots = **$16/trade** (was $8) |
| `PREMIUM_TAGS` (line 416) | `{claude_s-5, claude_swing_short}` | Slots 11-12 reserved; non-premium hard-capped at 10 via `confirm_trade_entry` |
| `premium_nonreserved_max` | 10 | Non-premium cap inside the 12-slot book |

### Touch-Rate Tracker

`trade_overseer/touch_rate_tracker.py` reports per-entry-family hit-rate of the +1% breakeven-arming threshold, plus avg peak vs realized (slippage). Collapses `claude_s{N}` / `claude_short_s{N}` to families and lists never-fired strategy paths so dead code is visible. Ghosts `force_exit`/`emergency_exit`/`liquidation` from entry stats. Logs JSONL to `user_data/logs/touch_rate_tracker.jsonl` for trend tracking.

```bash
ssh ubuntu@3.122.252.186 "cd ~/SYGNIF && python3 trade_overseer/touch_rate_tracker.py"
# variants: --days 7  --side long  --threshold 0.02  --no-print
```

### How to Check

```bash
# SSH into EC2 and check recent exits
ssh ubuntu@3.122.252.186 "cd ~/SYGNIF && \
  sqlite3 user_data/tradesv3-futures.sqlite \
  \"SELECT pair, enter_tag, exit_reason, close_profit, leverage, close_date \
    FROM trades WHERE is_open=0 ORDER BY close_date DESC LIMIT 20;\""
```

Key columns to watch:
- `exit_reason` containing `stoploss` → should decrease
- `close_profit` on SL exits → should cluster near -0.12, not -0.03 to -0.05
- `close_profit` on trail exits → should show more +1% to +3% captures

## Deployment

### Instance

- **EC2**: `i-0cd5389584d70a7fc` at `3.122.252.186` (eu-central-1)
- **SSH**: EC2 Instance Connect (push key first, 60s window)
- **Repo path on instance**: `~/SYGNIF`

### Deploy Commands

```bash
# Push SSH key (required before each SSH session)
aws ec2-instance-connect send-ssh-public-key \
  --instance-id i-0cd5389584d70a7fc \
  --instance-os-user ubuntu \
  --ssh-public-key file://~/.ssh/id_ed25519.pub \
  --region eu-central-1

# SSH and deploy
ssh ubuntu@3.122.252.186 "cd ~/SYGNIF && git pull && docker compose restart freqtrade freqtrade-futures"
```

### Important

- `SygnifStrategy.py` exists in TWO places: root and `user_data/strategies/`. Always sync both after edits.
- Strategy is loaded at container startup — **must restart** after code changes (volume mount updates files but Freqtrade caches the loaded strategy).
- Both configs are `dry_run: true` — change to `false` for live trading.

## Development

### Tests

```bash
python -m pytest tests/ -v
```

### Key Files

| File | Purpose |
|---|---|
| `SygnifStrategy.py` | Strategy source (root copy) |
| `user_data/strategies/SygnifStrategy.py` | Strategy copy (loaded by Freqtrade) |
| `user_data/config.json` | Spot config |
| `user_data/config_futures.json` | Futures config |
| `tests/test_strategy.py` | Unit tests |
| `docker-compose.yml` | Container orchestration |
| `.env` | API keys (git-ignored) |
