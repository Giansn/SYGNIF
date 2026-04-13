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
| `user_data/config_futures.json` | Futures config (often **gitignored** on instances with real keys) | Tracked template: `config_futures` on `main` **without** secrets; **Bybit demo + `BTC_Strategy_0_1`:** copy from `user_data/config_btc_strategy_0_1_bybit_demo.example.json` (see **§4b** below) |

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

### 4b. BTC_Strategy_0_1 + Bybit demo bridge (EC2)

**Full reference:** [letscrash/BTC_STRATEGY_0_1_BYBIT_BRIDGE.md](letscrash/BTC_STRATEGY_0_1_BYBIT_BRIDGE.md) (CCXT options, **`bybit_ccxt_demo_patch.py`**, Docker bake vs `freqtrade-futures` entrypoint, retCode **10003** / **10032**).

**On this host you typically:**

1. Put **Bybit Demo Trading** API keys in **`.env`** as `BYBIT_DEMO_API_KEY` / `BYBIT_DEMO_API_SECRET` (see `.env.example`).
2. Build **`user_data/config_futures.json`** from **`user_data/config_btc_strategy_0_1_bybit_demo.example.json`**: set `exchange.key` / `exchange.secret` (or inject via your own merge script). Keep **`ccxt_config.options`**: `defaultType` **swap**, `defaultSettle` **USDT**, **`enableDemoTrading`: true**, **`hostname`: `bybit.com`** — do **not** point linear demo at legacy hard-coded `api-demo` URLs (see bridge doc).
3. **Rebuild** traders after changing the patch: `docker compose --profile archived-main-traders build freqtrade-futures` (or full `up -d --build`). `Dockerfile.custom` runs `bybit_ccxt_demo_patch.py` at **image** build; the **`freqtrade-futures`** service also runs it at **container start** before `freqtrade trade`.
4. Start futures: `docker compose --profile archived-main-traders up -d` (includes `freqtrade-futures` with `BTC_Strategy_0_1` per compose). **Paper-only BTC 0.1** without that stack: `docker compose --profile btc-0-1 up -d --build freqtrade-btc-0-1` → base **`user_data/config_btc_strategy_0_1_paper_market.json`** + `apply_bybit_demo_to_btc_0_1_config.py` (`dry_run: true` if `BYBIT_DEMO_*` missing; else live demo orders).
5. **Never commit** a `config_futures.json` that contains real Telegram tokens or exchange secrets — use examples + `.env` only.

6. **Optional — open order from BTC analysis:** `python3 scripts/btc_analysis_forceenter.py` (dry-run) posts a plan from `prediction_agent/btc_prediction_output.json` + training channel; `--execute` calls Freqtrade **`/forceenter`** (needs `force_entry_enable` + `FT_API_URL` / `FT_PASS` in env). See **`letscrash/BTC_STRATEGY_0_1_BYBIT_BRIDGE.md`** §7.

**Force-enter scripts (btc-0-1, default API `http://127.0.0.1:8185/api/v1`):**

| Purpose | Path |
|---------|------|
| Force enter on btc-0-1 | `scripts/ft_btc_0_1_forceenter.py` |
| Force enter from BTC analysis JSON | `scripts/btc_analysis_forceenter.py` |
| Force enter from 24h movement JSON | `scripts/ft_btc_0_1_from_24h_forecast.py` |

Paper config **`user_data/config_btc_strategy_0_1_paper_market.json`** uses **`max_open_trades`: 100** for headroom; **`position_adjustment_enable`: true** allows **`BTC_Strategy_0_1.adjust_trade_position`** scale-ins (DCA-style) on the same BTC trade — not simultaneous long+short on one symbol (Freqtrade+Bybit one-way). Strategy slot caps (R01–R03) still apply on new entries.

**Logs:** `docker logs freqtrade-futures --tail 80` — confirm exchange init and no Bybit **retCode** auth errors.

**Grid + BTC 0.1 on the same host:** `nautilus-grid-btc01` (profile `btc-grid-mm`) and **`freqtrade-btc-0-1`** / **`freqtrade-futures`** both trade **BTCUSDT linear** on Bybit **demo** if they share **`BYBIT_DEMO_*`** — **one net position and one order book**; the grid is **not** an automatic hedge unless you use a **second demo API** (**`BYBIT_DEMO_GRID_API_KEY` / `BYBIT_DEMO_GRID_API_SECRET`**) so the MM runs on an isolated demo wallet (see `docker-compose.yml` header + `run_bybit_demo_grid_market_maker.py` startup warning).

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
# Unit loads ``~/xrp_claude_bot/.env`` then ``~/SYGNIF/.env`` (same as Docker compose) so ``BYBIT_DEMO_*`` in the secrets file are visible to BTC Terminal / ``/interface``.
sudo systemctl daemon-reload
sudo systemctl enable --now sygnif-dashboard-futures
# Use **either** spot **or** BTC Terminal on 8888 (not both):
sudo systemctl enable --now sygnif-dashboard-btc-terminal
# sudo systemctl enable --now sygnif-dashboard-spot
```

Verify:
```bash
curl -s http://localhost:8888 | head -1   # Spot **or** BTC Terminal (only one may use 8888)
curl -s http://localhost:8888/interface | head -1   # BTC Interface (when btc-terminal owns 8888)
curl -s http://localhost:8889 | head -1   # Futures dashboard
```

**Port 8888 — one listener only:** `sygnif-dashboard-btc-terminal`, `sygnif-dashboard-spot`, and **Docker** services that publish **8888** (e.g. **`nautilus-jupyter-lab`** `8888:8888`) **cannot** all run at once. Stop the conflicting unit/container or set `SYGNIF_BTC_TERMINAL_PORT` (e.g. `8891`) in `.env` for the terminal unit. Then `sudo systemctl restart sygnif-dashboard-btc-terminal`.

### Reverse SSH tunnel (optional — stable URL via your own gateway)

The instance opens **outbound** SSH and requests **remote port forward** so a VPS/home server you control exposes a port that maps to **this host’s** `127.0.0.1:8888` (Sygnif BTC Terminal by default, if it holds :8888). That gives a **fixed hostname** (your gateway) instead of opening `8888` on the EC2 security group.

1. On the **gateway**: create a Linux user, add **this instance’s** SSH public key to `~/.ssh/authorized_keys`. For a **public** listen address on the gateway, set in `sshd_config`: `GatewayPorts clientspecified` or `yes`, then `sudo systemctl reload ssh`.
2. In **`~/SYGNIF/.env`** set (see `.env.example` tail):

   - `SYGNIF_REVERSE_TUNNEL_ENABLE=1`
   - `SYGNIF_REVERSE_TUNNEL_GATEWAY=ubuntu@your-vps.example.com`
   - `SYGNIF_REVERSE_TUNNEL_IDENTITY_FILE=/home/ubuntu/.ssh/id_ed25519_sygnif_tunnel` (chmod `600`)
   - Optional: `SYGNIF_REVERSE_TUNNEL_REMOTE_BIND=0.0.0.0`, `SYGNIF_REVERSE_TUNNEL_REMOTE_PORT=19888`, `SYGNIF_REVERSE_TUNNEL_LOCAL_PORT=8888`

3. Install and start the unit:

```bash
sudo cp /home/ubuntu/SYGNIF/systemd/sygnif-reverse-tunnel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sygnif-reverse-tunnel
sudo systemctl start sygnif-reverse-tunnel
sudo systemctl status sygnif-reverse-tunnel
```

The unit is **disabled by default** until you set `SYGNIF_REVERSE_TUNNEL_ENABLE=1` in `.env`; otherwise `start` is skipped (`ConditionEnvironment`).

4. On the **gateway**, browse `http://127.0.0.1:19888/` (or your public IP + port if `REMOTE_BIND=0.0.0.0`). From your laptop: `ssh -L 8888:127.0.0.1:19888 ubuntu@your-vps` then open `http://127.0.0.1:8888/`.

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

### BTC 0.1 persistent finetune tick (optional, complements Cursor worker)

The **Cursor worker** (`cursor-agent-worker`) is for **Cloud-side** tasks (edits, reviews). It does **not** run a schedule by itself. For **continuous R01/R02/R03 evidence** on disk (report + monitor + optional `rule_tag_journal.csv` rows), enable this **systemd timer**:

- **Scripts:** `scripts/btc01_finetune_tick.py` → `btc01_r01_r02_report.py` + `monitor_r01_r03_gate.py`
- **Default cadence:** every **5 minutes** after boot (`OnUnitInactiveSec=5min` — edit the installed timer to slow down)
- **Log:** `~/.local/share/sygnif/btc01_finetune_tick.log`
- **Journal:** set `RULE_TAG_JOURNAL_MONITOR=YES` (already default in the unit) → `prediction_agent/rule_tag_journal.csv`

```bash
sudo cp systemd/sygnif-btc01-finetune.service systemd/sygnif-btc01-finetune.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sygnif-btc01-finetune.timer
systemctl list-timers sygnif-btc01-finetune.timer
```

One-shot test: `sudo systemctl start sygnif-btc01-finetune.service` then `tail -5 ~/.local/share/sygnif/btc01_finetune_tick.log`

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

# API health (host ports match docker-compose: spot API → host 8181 by default, not 8080)
curl -fsS "http://127.0.0.1:8181/api/v1/ping"
curl -fsS "http://127.0.0.1:8081/api/v1/ping"

# One-shot sweep (core stack + optional profiles / worker)
./scripts/deploy_health_check.sh

# Local-only services (from host)
curl -fsS http://127.0.0.1:8089/   # notification-handler GET → {"status":"healthy"}
curl -fsS http://127.0.0.1:8090/health
curl -fsS http://127.0.0.1:8090/overview 2>/dev/null | head -c 200
curl -fsS http://127.0.0.1:8093/healthz   # Cursor worker
```

Compose **healthchecks** (Docker `HEALTHY` status): `finance-agent` → `GET /health`; `notification-handler` → `GET /`; Freqtrade containers (when started) → `GET /api/v1/ping`; `trade-overseer` (when started) → `GET /health`; `nautilus-research` → `python3 /lab/workspace/nautilus_smoke.py`. Freqtrade-based services **wait on** `finance-agent` + `notification-handler` **healthy** where `depends_on` is set — rebuild/recreate may take longer on first boot until `start_period` elapses.

## Services Summary

| Service | Type | Port | Persists reboot |
|---------|------|------|-----------------|
| `freqtrade` | Docker (`unless-stopped`) | API **8181→8080** in compose (all interfaces on 8181) | yes |
| `freqtrade-futures` | Docker (`unless-stopped`) | **8081** host/container | yes |
| `notification-handler` | Docker | 8089 (**localhost only**) | yes |
| `trade-overseer` | Docker | 8090 (**localhost only**) | yes |
| `sygnif-dashboard-spot` | systemd | 8888 | yes (**exclusive** with btc-terminal on same port) |
| `sygnif-dashboard-futures` | systemd | 8889 | yes |
| `sygnif-dashboard-btc-terminal` | systemd | 8888 (`/interface` = Bybit demo) | yes (**exclusive** with spot on same port) |
| `sygnif-reverse-tunnel` | systemd (optional) | — (outbound SSH) | yes |
| `sygnif-notify` | systemd | — | yes |
| `cursor-agent-worker` | systemd (optional) | 8093 (**localhost**, management) | yes |
| `sygnif-btc01-finetune.timer` | systemd (optional) | — (runs `btc01_finetune_tick.py` on interval) | yes |
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
| BTC 0.1 strategy | `user_data/strategies/BTC_Strategy_0_1.py`, `btc_strategy_0_1_engine.py` |
| Bybit demo bridge doc | `letscrash/BTC_STRATEGY_0_1_BYBIT_BRIDGE.md` |
| Spot config | `user_data/config.json` (gitignored) |
| Futures config | `user_data/config_futures.json` |
| BTC 0.1 futures demo template | `user_data/config_btc_strategy_0_1_bybit_demo.example.json` |
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
| 8888 | Spot dashboard **or** BTC Terminal + `/interface` | **One** service only on this port |
| 8889 | Futures dashboard | |
| 8089 | Notification handler | **Bound to localhost** in compose — not exposed publicly by default |
| 8090 | Trade overseer HTTP | **Localhost** — use SSH tunnel if needed remotely |
| 8091 | Finance agent briefing | Default **127.0.0.1** — overseer container uses `host.docker.internal` |
| 8093 | Cursor worker management | **Localhost** |

## BTC dock: pure Nautilus (`btc-nautilus`)

Profile **`btc-nautilus`** starts **`nautilus-research`** (Bybit HTTP sink → `finance_agent/btc_specialist/data/` for **`btc_predict_runner`** / training) + **`nautilus-sygnif-btc-node`** (Nautilus **`TradingNode`** bar strategy — extend for BTC execution logic). **No Freqtrade** in this profile. Optional archived Freqtrade BTC dock + **`trade-overseer`**: profile **`archived-freqtrade-btc-dock`** (see `archive/freqtrade-btc-dock-2026-04-13/RESTORE.txt`).

```bash
cd ~/SYGNIF
docker compose --profile btc-nautilus up -d --build
```

## Optional: `nautilus-research` only (same image as BTC dock)

Profile **`btc-nautilus`** groups **`nautilus-research`** and **`nautilus-sygnif-btc-node`**. Start **only** the sink container by naming the service. Legacy merge files **`docker-compose.nautilus-research.yml`** / **`docker-compose.nautilus-strategy-sidecar.yml`** / **`docker-compose.btc-nautilus-research.yml`** were removed — use **`docker-compose.yml`** only.

```bash
cd ~/SYGNIF
docker compose --profile btc-nautilus up -d --build finance-agent nautilus-research
# full BTC Nautilus dock (research + bar node): omit the service name
# docker compose --profile btc-nautilus up -d --build
docker exec -it nautilus-research python3 /lab/workspace/btc_regime_assessment.py
```

See `research/nautilus_lab/README.md` and `SWING_FAILURE_ANALYSIS.md`.

## Optional: `nautilus-grid-btc01` (Nautilus **GridMarketMaker** on Bybit demo, BTCUSDT linear)

Profile **`btc-grid-mm`**: places live **demo** orders via Nautilus `GridMarketMaker`. Set **`NAUTILUS_GRID_MM_DEMO_ACK=YES`** in `.env` plus **`BYBIT_DEMO_*`**. Prefer **`BYBIT_DEMO_GRID_*`** (or a separate demo key) if you also run other Bybit demo linear bots.

**Sizing (defaults in `docker-compose.yml`):** **`NAUTILUS_GRID_BTC01_NUM_LEVELS=8`** → up to **16** resting post-only limits (8 bid + 8 ask rungs) when flat and **`NAUTILUS_GRID_BTC01_MAX_POSITION`** allows (`≥ num_levels × trade_size` per side). Wider **`NAUTILUS_GRID_BTC01_GRID_STEP_BPS`** spreads rungs for larger BTC moves. **`NAUTILUS_GRID_BTC01_REQUOTE_BPS`** (wider = fewer cancel/replace cycles) plus **`NAUTILUS_GRID_BTC01_REQUOTE_MIN_SEC`** debounce reduce orders “flashing” on the Bybit UI when quote ticks outpace cancel acks; override in `.env` if needed.

**Persistent after reboot:** service uses **`restart: unless-stopped`**. Add **`COMPOSE_PROFILES=btc-grid-mm`** (alone or comma-appended) to `.env` so a normal **`docker compose up -d`** from `~/SYGNIF` recreates the grid after a host restart (still needs `finance-agent` healthy).

**Cancel all open BTCUSDT linear orders on demo** (stop the grid container first if you do not want immediate re-quotes):

```bash
cd ~/SYGNIF
docker stop nautilus-grid-btc01 2>/dev/null || true
PYTHONPATH=. python3 scripts/bybit_demo_cancel_open_orders.py
```

```bash
cd ~/SYGNIF
./scripts/start_btc01_nautilus_grid.sh
# or: docker compose --profile btc-grid-mm up -d nautilus-grid-btc01
docker logs nautilus-grid-btc01 -f
```

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

## Optional: BTC training channel + R01–R03 monitor

End-to-end **training flow** (runner → `channel_training` → what-if monitor for **BTC-0.1-R01–R03** gates):

```bash
cd ~/SYGNIF
chmod +x scripts/run_training_flow.sh
./scripts/run_training_flow.sh
```

Monitor only (read-only JSON): `PYTHONPATH=. python3 scripts/monitor_r01_r03_gate.py --json`

- **Formulas / thresholds:** [docs/btc_expertise_proven_formulas.md](docs/btc_expertise_proven_formulas.md)
- **Journal one line per monitor run:** `RULE_TAG_JOURNAL_MONITOR=YES ./scripts/run_training_flow.sh`
- **Fail if channel JSON stale:** `… monitor_r01_r03_gate.py --strict-stale --max-age-hours 48` (exit 2)

Example cron (hourly, adjust user/path):

`17 * * * * cd /home/ubuntu/SYGNIF && ./scripts/run_training_flow.sh >>/tmp/btc_training_flow.log 2>&1`

## Optional: `network-dev-loop` (separate project)

If `network-dev-loop.service` / `network-dev-loop.timer` are installed (e.g. under `~/network-dev-agents`), a **Failed to start** in `journalctl` is often **expected** when the script skips work: `run-dev-loop.sh` exits non-zero if **load per CPU** \> `--max-load-per-cpu` (default `1.50`) or **available RAM** \< `--min-mem-available-mb` (default `512`). Another cause is a stale lock: `/tmp/network-dev-loop.lock` when a previous run did not release.

**Debug:** `sudo tail -100 /var/log/network-dev-loop.log` and `~/network-dev-agents/scripts/run-dev-loop.sh` (see `check_system_load`).

## AWS Session Manager (SSM)

Persistent shell without inbound SSH: see [docs/AWS_SSM_SESSION_MANAGER.md](docs/AWS_SSM_SESSION_MANAGER.md).

Quick verify after reboot: ~/SYGNIF/scripts/verify-ssm-agent.sh

