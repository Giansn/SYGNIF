# Instance Setup Guide

Full steps to recreate the Sygnif setup on a fresh Ubuntu instance.

**Directory name:** clone into **`SYGNIF`** (or set **`SYGNIF_REPO`** if you keep a legacy path like **`xrp_claude_bot`**).

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
git clone https://github.com/Giansn/SYGNIF.git SYGNIF
cd SYGNIF
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
docker ps --format "table {{.Names}}\t{{.Ports}}\t{{.Status}}"
```

Expected containers:

| Container | Host bind | Role |
|-----------|-----------|------|
| `freqtrade` | `0.0.0.0:8080` | Spot Freqtrade API |
| `freqtrade-futures` | `0.0.0.0:8081` | Futures Freqtrade API |
| `notification-handler` | `127.0.0.1:8089` | Webhooks → Telegram routing |
| `trade-overseer` | `127.0.0.1:8090` | LLM trade monitor HTTP (`/overview`, `/plays`, …) |

**Trade overseer: avoid double bind on 8090.** Do **not** `systemctl enable --now trade-overseer` on the host while Docker runs `trade-overseer` — both use `127.0.0.1:8090` and the systemd unit will fail with `Address already in use`. **Production = Docker** (`docker-compose.yml`). Use the host unit only for a **host-only** overseer (stop the container first); comments in `/etc/systemd/system/trade-overseer.service` describe this.

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

### Cursor Agent worker (optional)

Sygnif Agent / Cursor Cloud worker — same repo, management port `8093`.

```bash
sudo cp systemd/cursor-agent-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cursor-agent-worker
```

Verify: `curl -fsS http://127.0.0.1:8093/healthz`

### Finance agent (optional)

Telegram research bot + briefing HTTP for overseer (`~/finance_agent`, separate clone). Expects Cursor worker healthy first (`After=` / `ExecStartPre` in unit).

```bash
sudo cp ~/finance_agent/systemd/finance-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now finance-agent
```

Default briefing URL: `http://127.0.0.1:8091` (see `FINANCE_AGENT_HTTP_*` in `finance_agent/bot.py`). Docker `trade-overseer` reaches the host via `extra_hosts: host.docker.internal:host-gateway` in `docker-compose.yml`.

## 7. Movers Pairlist (Optional Cron)

Updates top gainers/losers from Bybit every 4 hours:

```bash
crontab -e
# Add:
0 */4 * * * cd /home/ubuntu/SYGNIF && /usr/bin/python3 update_movers.py >> movers_update.log 2>&1
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

# Local-only services (from host)
curl -fsS http://127.0.0.1:8089/   # notification-handler GET → {"status":"healthy"}
curl -fsS http://127.0.0.1:8090/overview 2>/dev/null | head -c 200
curl -fsS http://127.0.0.1:8093/healthz   # Cursor worker
```

## Services Summary

| Service | Type | Port | Persists reboot |
|---------|------|------|-----------------|
| `freqtrade` | Docker (`unless-stopped`) | 8080 (all interfaces) | yes |
| `freqtrade-futures` | Docker (`unless-stopped`) | 8081 (all interfaces) | yes |
| `notification-handler` | Docker | 8089 (**localhost only**) | yes |
| `trade-overseer` | Docker | 8090 (**localhost only**) | yes |
| `sygnif-dashboard-spot` | systemd | 8888 | yes |
| `sygnif-dashboard-futures` | systemd | 8889 | yes |
| `sygnif-notify` | systemd | — | yes |
| `cursor-agent-worker` | systemd (optional) | 8093 (**localhost**, management) | yes |
| `finance-agent` | systemd (optional) | 8091 (default **localhost**, briefing HTTP) | yes |

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

| Port | Service | Notes |
|------|---------|--------|
| 8080 | Freqtrade API (spot) | Typically open for UI/API access |
| 8081 | Freqtrade API (futures) | Same |
| 8888 | Spot dashboard | |
| 8889 | Futures dashboard | |
| 8089 | Notification handler | **Bound to localhost** in compose — not exposed publicly by default |
| 8090 | Trade overseer HTTP | **Localhost** — use SSH tunnel if needed remotely |
| 8091 | Finance agent briefing | Default **127.0.0.1** — overseer container uses `host.docker.internal` |
| 8093 | Cursor worker management | **Localhost** |

## Optional: `network-dev-loop` (separate project)

If `network-dev-loop.service` / `network-dev-loop.timer` are installed (e.g. under `~/network-dev-agents`), a **Failed to start** in `journalctl` is often **expected** when the script skips work: `run-dev-loop.sh` exits non-zero if **load per CPU** \> `--max-load-per-cpu` (default `1.50`) or **available RAM** \< `--min-mem-available-mb` (default `512`). Another cause is a stale lock: `/tmp/network-dev-loop.lock` when a previous run did not release.

**Debug:** `sudo tail -100 /var/log/network-dev-loop.log` and `~/network-dev-agents/scripts/run-dev-loop.sh` (see `check_system_load`).

## AWS Session Manager (SSM)

Persistent shell without inbound SSH: see [docs/AWS_SSM_SESSION_MANAGER.md](docs/AWS_SSM_SESSION_MANAGER.md).

Quick verify after reboot: ~/SYGNIF/scripts/verify-ssm-agent.sh

