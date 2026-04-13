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
sudo cp systemd/sygnif-dashboard-btc-terminal.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sygnif-dashboard-spot sygnif-dashboard-futures sygnif-dashboard-btc-terminal
```

Verify:
```bash
curl -s http://localhost:8888 | head -1   # Spot dashboard
curl -s http://localhost:8889 | head -1   # Futures dashboard
curl -s http://localhost:8891 | head -1   # Sygnif BTC Terminal (prediction / training)
```

### Reverse SSH tunnel (optional — stable URL via your own gateway)

The instance opens **outbound** SSH and requests **remote port forward** so a VPS/home server you control exposes a port that maps to **this host’s** `127.0.0.1:8891` (Sygnif BTC Terminal by default). That gives a **fixed hostname** (your gateway) instead of opening `8891` on the EC2 security group.

1. On the **gateway**: create a Linux user, add **this instance’s** SSH public key to `~/.ssh/authorized_keys`. For a **public** listen address on the gateway, set in `sshd_config`: `GatewayPorts clientspecified` or `yes`, then `sudo systemctl reload ssh`.
2. In **`~/SYGNIF/.env`** set (see `.env.example` tail):

   - `SYGNIF_REVERSE_TUNNEL_ENABLE=1`
   - `SYGNIF_REVERSE_TUNNEL_GATEWAY=ubuntu@your-vps.example.com`
   - `SYGNIF_REVERSE_TUNNEL_IDENTITY_FILE=/home/ubuntu/.ssh/id_ed25519_sygnif_tunnel` (chmod `600`)
   - Optional: `SYGNIF_REVERSE_TUNNEL_REMOTE_BIND=0.0.0.0`, `SYGNIF_REVERSE_TUNNEL_REMOTE_PORT=19891`, `SYGNIF_REVERSE_TUNNEL_LOCAL_PORT=8891`

3. Install and start the unit:

```bash
sudo cp /home/ubuntu/SYGNIF/systemd/sygnif-reverse-tunnel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sygnif-reverse-tunnel
sudo systemctl start sygnif-reverse-tunnel
sudo systemctl status sygnif-reverse-tunnel
```

The unit is **disabled by default** until you set `SYGNIF_REVERSE_TUNNEL_ENABLE=1` in `.env`; otherwise `start` is skipped (`ConditionEnvironment`).

4. On the **gateway**, browse `http://127.0.0.1:19891/` (or your public IP + port if `REMOTE_BIND=0.0.0.0`). From your laptop: `ssh -L 8891:127.0.0.1:19891 ubuntu@your-vps` then open `http://127.0.0.1:8891/`.

`systemd` restarts the tunnel if SSH drops (`Restart=always`). Optional: `sudo apt install autossh` and swap `ExecStart` to `autossh` for extra watchdog behaviour (not required).

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

## 7b. Crypto market data + finance-agent / Cursor dashboard (optional daily cron)

Runs once per day: (1) fetch all [crypto-market-data](https://github.com/ErcinDedeoglu/crypto-market-data) README `data/daily/*.json` + `crypto_market_data_daily_analysis.md`, (2) regenerate `finance_agent/btc_specialist/data/btc_specialist_dashboard.json` using the same **`llm_analyze` + finance-agent KB** path as Telegram `/finance-agent` when `CRYPTO_CONTEXT_LLM` is not disabled.

- **Script:** `scripts/cron_finance_agent_btc_context.sh` (log: `user_data/logs/finance_agent_btc_context.log`).
- **Secrets:** `CURSOR_API_KEY` (and related `CURSOR_*`) in `~/SYGNIF/.env`, `~/finance_agent/.env`, or `~/xrp_claude_bot/.env` — same chain as `pull_btc_context.py`.
- **Skip LLM** (heuristic only): set `CRYPTO_CONTEXT_LLM=0` in `.env`.
- **Align with `cursor-agent-worker`**: `llm_analyze` uses the same Cursor Cloud repo as the worker (`CURSOR_AGENT_REPOSITORY`). Optional `CRYPTO_CONTEXT_REQUIRE_WORKER=1` skips LLM when `http://127.0.0.1:8093/healthz` is not OK (cron then uses heuristics).
- **Legacy deploy tree:** if the dashboard reads JSON from another clone, set `BTC_CONTEXT_SYNC_TARGET=/home/ubuntu/xrp_claude_bot/finance_agent/btc_specialist/data` so the script copies the refreshed files after success.

Schedule (00:00 **Europe/Berlin**, DST-safe on a UTC host — same pattern as `scripts/cron_crypto_market_data_daily.sh`):

```bash
crontab -e
# Add:
0 * * * * [ "$(TZ=Europe/Berlin date +\%H)" = "00" ] && /home/ubuntu/SYGNIF/scripts/cron_finance_agent_btc_context.sh
```

## 8. Verify Everything

```bash
# Containers running
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# Dashboards
sudo systemctl status sygnif-dashboard-spot sygnif-dashboard-futures sygnif-dashboard-btc-terminal

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
| `sygnif-dashboard-btc-terminal` | systemd | 8891 | yes |
| `sygnif-reverse-tunnel` | systemd (optional) | — (outbound SSH) | yes |
| `sygnif-notify` | systemd | — | yes |
| `cursor-agent-worker` | systemd (optional) | 8093 (**localhost**, management) | yes |
| `finance-agent` | systemd (optional) | 8091 (default **localhost**, briefing HTTP) | yes |

## Automation as an instance-wide network (stable ops)

Treat the **EC2 host** as one **control plane**: processes are **nodes** that talk over **loopback TCP** and (optionally) a **Docker user-defined bridge**. That is more stable than one-off shell wrappers because **supervision** (systemd + Docker restart policies) and **connectivity** stay explicit.

| Layer | What it gives you | Sygnif pieces |
|-------|-------------------|---------------|
| **Whole-instance access** | Admin shell and automation **without** exposing SSH to the internet | [docs/AWS_SSM_SESSION_MANAGER.md](docs/AWS_SSM_SESSION_MANAGER.md), `scripts/verify-ssm-agent.sh` |
| **Host “bus”** | Fixed **127.0.0.1** ports = predictable edges between services | 8089 notification-handler, 8090 trade-overseer, 8091 finance-agent HTTP, 8093 cursor worker |
| **Docker bridge** | Other stacks or sidecars attach to the **same** L2 network as Sygnif containers | `docker network create sygnif_network` then `COMPOSE_FILE=docker-compose.yml:docker-compose.sygnif-network.yml` — see [docker-compose.sygnif-network.yml](docker-compose.sygnif-network.yml) |
| **VPC / VPN / edge** | When automation must span **laptop → EC2 → IR infer**, not only localhost | Submodule `network/` → `aws-node-network/`, `docs/AGENT_NODE_NETWORK_DRAFT.md`, `docs/NEURAL_NETWORK_SETUP.md` |
| **ANN artifacts on big disk** | Training/export layout aligned with Network “neural node” bundles | Submodule `ann_text_project/` → `docs/ARTIFACT_LAYOUT.md` |

**Principle:** keep **one** bind per port (e.g. do not run host `trade-overseer.service` while the Docker `trade-overseer` container holds `8090`). Expanding the “network” means adding **documented** edges (new port, new bridge, or SSM document), not duplicate listeners.

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
| 8891 | Sygnif BTC Terminal (prediction / training JSON) | Optional public UI — or SSH tunnel |
| 8089 | Notification handler | **Bound to localhost** in compose — not exposed publicly by default |
| 8090 | Trade overseer HTTP | **Localhost** — use SSH tunnel if needed remotely |
| 8091 | Finance agent briefing | Default **127.0.0.1** — overseer container uses `host.docker.internal` |
| 8093 | Cursor worker management | **Localhost** |

## Optional: `nautilus-research` (Docker — Nautilus + Sygnif mounts)

Research container with **`nautilus_trader`** plus read-only mounts: `finance_agent/` (incl. **btc_specialist** data), `prediction_agent/`, `user_data/`. Uses **`SYGNIF_SPOT_NOTIONAL_USDT`** (default **100**, matches spot `dry_run_wallet`) for regime notes. Merge with base compose so **`finance-agent`** resolves on `sygnif_backend`.

```bash
cd ~/SYGNIF
export COMPOSE_FILE=docker-compose.yml:docker-compose.nautilus-research.yml
docker compose up -d finance-agent   # once
docker compose build nautilus-research && docker compose up -d nautilus-research
docker exec -it nautilus-research python3 /lab/workspace/btc_regime_assessment.py
```

See `research/nautilus_lab/README.md` and `SWING_FAILURE_ANALYSIS.md`.

## Optional: `btc-predict-runner` (ML bot on host, not Docker)

Hourly **oneshot** that runs `prediction_agent/btc_predict_runner.py` (RandomForest + XGBoost + direction logreg on Bybit OHLCV JSON under `finance_agent/btc_specialist/data/`). Writes `prediction_agent/btc_prediction_output.json`. **Stale data** if you never refresh the JSON — align with your BTC data cron or call `pull_btc_context.py` separately.

```bash
sudo cp ~/SYGNIF/systemd/btc-predict-runner.service /etc/systemd/system/
sudo cp ~/SYGNIF/systemd/btc-predict-runner.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now btc-predict-runner.timer
# manual run:
sudo systemctl start btc-predict-runner.service
journalctl -u btc-predict-runner.service -n 40 --no-pager
```

## Optional: `network-dev-loop` (separate project)

If `network-dev-loop.service` / `network-dev-loop.timer` are installed (e.g. under `~/network-dev-agents`), a **Failed to start** in `journalctl` is often **expected** when the script skips work: `run-dev-loop.sh` exits non-zero if **load per CPU** \> `--max-load-per-cpu` (default `1.50`) or **available RAM** \< `--min-mem-available-mb` (default `512`). Another cause is a stale lock: `/tmp/network-dev-loop.lock` when a previous run did not release.

**Debug:** `sudo tail -100 /var/log/network-dev-loop.log` and `~/network-dev-agents/scripts/run-dev-loop.sh` (see `check_system_load`).

## AWS Session Manager (SSM)

Persistent shell without inbound SSH: see [docs/AWS_SSM_SESSION_MANAGER.md](docs/AWS_SSM_SESSION_MANAGER.md).

Quick verify after reboot: ~/SYGNIF/scripts/verify-ssm-agent.sh

