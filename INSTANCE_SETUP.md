# Instance Setup Guide

Full steps to recreate the Sygnif setup on a fresh Ubuntu instance.

## 1. Prerequisites

```bash
# Docker
sudo apt update && sudo apt install -y docker.io docker-compose-v2 python3 python3-pip
sudo usermod -aG docker $USER
# Log out and back in for group to take effect
```

## 2. Clone

```bash
cd ~
git clone https://github.com/Giansn/SYGNIF.git xrp_claude_bot
cd xrp_claude_bot
```

## 3. Environment File

```bash
cp .env.example .env   # or create manually:
```

```
BYBIT_API_KEY=<your key>
BYBIT_API_SECRET=<your secret>
ANTHROPIC_API_KEY=<your key>
TELEGRAM_CHAT_ID=1134139785
```

## 4. Config Files

Two config files must exist in `user_data/`:

| File | Purpose | Template |
|------|---------|----------|
| `user_data/config.json` | Spot config | `config_claude_bot.example.json` |
| `user_data/config_futures.json` | Futures config | Already in repo |

```bash
cp config_claude_bot.example.json user_data/config.json
```

Edit both configs and set:
- `telegram.token` — spot: `@sygnif_bot` token, futures: `@sygnifuture_bot` token
- `telegram.chat_id` — `1134139785`
- `exchange.key` / `exchange.secret` — Bybit API keys
- `dry_run` — `true` for paper trading, `false` for live

### Telegram Bot Tokens

| Bot | Username | Token prefix |
|-----|----------|-------------|
| Spot | `@sygnif_bot` | `8753646984:...` |
| Futures | `@sygnifuture_bot` | `8016276540:...` |

## 5. Build and Start Containers

```bash
docker compose up -d --build
```

Verify:
```bash
docker ps
# Should show: freqtrade (port 8080) + freqtrade-futures (port 8081)
```

The entrypoint auto-applies the compact `/status` patch on every container start.

## 6. Systemd Services

### Dashboard servers (survive reboots)

```bash
sudo cp systemd/sygnif-dashboard-spot.service /etc/systemd/system/
sudo cp systemd/sygnif-dashboard-futures.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sygnif-dashboard-spot sygnif-dashboard-futures
```

Verify:
```bash
curl -s http://localhost:8888 | head -1   # Spot dashboard
curl -s http://localhost:8889 | head -1   # Futures dashboard
```

### Reboot notifier (Telegram alerts on up/down)

```bash
sudo cp systemd/sygnif-notify.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sygnif-notify
```

## 7. Movers Pairlist (Optional Cron)

Updates top gainers/losers from Bybit every 4 hours:

```bash
crontab -e
# Add:
0 */4 * * * cd /home/ubuntu/xrp_claude_bot && /usr/bin/python3 update_movers.py >> movers_update.log 2>&1
```

## 8. Verify Everything

```bash
# Containers running
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# Dashboards
sudo systemctl status sygnif-dashboard-spot sygnif-dashboard-futures

# Reboot notifier
sudo systemctl status sygnif-notify

# Test Telegram notifications
./notify.sh up

# API health
curl -s http://localhost:8080/api/v1/ping
curl -s http://localhost:8081/api/v1/ping
```

## Services Summary

| Service | Type | Port | Persists reboot |
|---------|------|------|-----------------|
| `freqtrade` | Docker (`unless-stopped`) | 8080 | yes |
| `freqtrade-futures` | Docker (`unless-stopped`) | 8081 | yes |
| `sygnif-dashboard-spot` | systemd | 8888 | yes |
| `sygnif-dashboard-futures` | systemd | 8889 | yes |
| `sygnif-notify` | systemd | — | yes |

## File Locations

| What | Path |
|------|------|
| Strategy | `user_data/strategies/SygnifStrategy.py` |
| Spot config | `user_data/config.json` (gitignored) |
| Futures config | `user_data/config_futures.json` |
| Spot DB | `user_data/tradesv3.sqlite` (gitignored) |
| Futures DB | `user_data/tradesv3-futures.sqlite` (gitignored) |
| Logs | `user_data/logs/` (gitignored) |
| Movers data | `movers_pairlist.json` (gitignored) |
| Env secrets | `.env` (gitignored) |
| Systemd units | `systemd/` (repo copies) |

## Ports to Open (Security Group / Firewall)

| Port | Service |
|------|---------|
| 8080 | Freqtrade API (spot) |
| 8081 | Freqtrade API (futures) |
| 8888 | Spot dashboard |
| 8889 | Futures dashboard |
