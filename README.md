# SYGNIF

Autonomous BTC trading system. Three processes across two hosts plus a
Bybit demo account. The repo hosts the canonical source for the agent,
the EC2 services snapshot, and the legacy Freqtrade execution layer.

> **AI coding agents:** read [`AGENTS.md`](./AGENTS.md) and
> [`SYGNIF.md`](./SYGNIF.md) before editing.

## Architecture at a glance

```
┌──────────────── X1 (Lenovo Yoga, Windows + WSL) ────────────────┐
│  sygnif-x1-mcp / sygnif-bybit-mcp / sygnif-commander-mcp        │
│  sygnif-trader (author, NO_EXECUTE — plans only)                │
│  master swarm.db, dashboards, sygnif-letscrash CLI              │
└─────────────────────────────────────────────────────────────────┘
                        ↕ Tailscale mesh
┌──────────────── EC2 eu-central-1 (m7i-flex.large) ──────────────┐
│  sygnif-trader (executor) → Bybit demo                          │
│  NeuroLinked brain :8889 (3,000 neurons, STDP)                  │
│  Brain insights :8890                                           │
│  17+ intel daemons (chain, evm, ecosystem, news, polymarket …)  │
│  intel-aggregator + fast-reactor (sub-ms perp opener)           │
│  Freqtrade containers (legacy spot + futures execution)         │
│  trade_overseer (Telegram commentary)                           │
└─────────────────────────────────────────────────────────────────┘
                        ↕ Bybit V5 (api-demo)
┌──────────────── Bybit demo (UTA) ───────────────────────────────┐
│  equity ≈ $1,500   perp + options                               │
└─────────────────────────────────────────────────────────────────┘
```

**X1 = brain / author. EC2 = executor.** Either can stop without
breaking the other.

## Top-level layout

| Path | Status | Purpose |
|---|---|---|
| `SygnifStrategy.py` | live | Freqtrade spot strategy |
| `user_data/` | live | Freqtrade configs + journal |
| `docker-compose.yml`, `docker/` | live | 4-container Freqtrade stack |
| `trade_overseer/` | live | Telegram commentary + NPU LLM hooks |
| `finance_agent/` | live | Briefing + strategy router (host:8091) |
| `notification_handler.py` | live | Webhook fan-out |
| `ec2-snapshot/services/` | snapshot | 46 daemons mirrored from `/opt/sygnif-services/` on EC2 |
| `ec2-snapshot/systemd/` | snapshot | 60 unit files + 7 drop-in dirs |
| `ec2-snapshot/neurolinked/` | snapshot | Brain code (no state) |
| `ec2-snapshot/trader/` | snapshot | EC2 agent code mirror |
| `archive/` | legacy | Old freqtrade-era dashboards, scripts. Do not edit. |
| `docs/` | live | Architecture + ops docs |
| `tests/` | live | Unit tests |
| `experiments/` | per-project | Sandboxed work (BTC sim, toolkits, …) |

## Docs

| File | Audience | Content |
|---|---|---|
| `SYGNIF.md` | everyone | Canonical system specification — start here |
| `AGENTS.md` | AI coding agents | Briefing for any LLM about to edit the repo |
| `CLAUDE.md` | Claude Code | Long-form instructions, mirrors `SYGNIF.md` |
| `SNAPSHOT.md` | operators | What the 2026-05-13 snapshot captured + restore steps |
| `SETUP.md` | operators | Bootstrap from scratch |
| `INSTANCE_SETUP.md` | operators | EC2-specific provisioning |

## Freqtrade execution layer (still live, runs alongside the agent)

| Container | Mode | Port | Config | Telegram | DB |
|---|---|---|---|---|---|
| `freqtrade` | Spot | 8080 | `user_data/config.json` | `@sygnif_bot` | `tradesv3.sqlite` |
| `freqtrade-futures` | Futures | 8081 | `user_data/config_futures.json` | `@sygnifuture_bot` | `tradesv3-futures.sqlite` |
| `trade-overseer` | Monitor | 8090 | `trade_overseer/` | `@Sygnif_hedge_bot` | `trade_overseer/data` |

`SygnifStrategy.py` (spot) and `user_data/strategies/MarketStrategy.py`
(futures) are loaded by the two Freqtrade containers and share the
`./user_data` volume. Restart containers after editing strategy files —
Freqtrade caches the compiled strategy at container start.

`trade-overseer` LLM backend is pluggable:

- `OVERSEER_AGENT_URL` (preferred) — once the language plan ships, this
  points at `http://ec2-eu1:8889/api/commentary`.
- `ANTHROPIC_API_KEY` (legacy fallback).
- Rules-only summary if neither is reachable.

Telegram token priority: `SYGNIF_HEDGE_BOT_TOKEN` > `FINANCE_BOT_TOKEN` >
`TELEGRAM_BOT_TOKEN`.

## Agent layer (X1 + EC2)

- **X1** runs the planner (`sygnif-trader.service`,
  `SYGNIF_TRADER_NO_EXECUTE=1`) — plans only, no orders.
- **EC2** runs the executor (`sygnif-trader.service`,
  `SYGNIF_ORDERS_MODE=demo`) — places demo orders on Bybit.
- **Brain** (`sygnif-neurolinked.service` on EC2) — 3,000-neuron
  Izhikevich SNN, STDP plasticity, model2vec text encoding.
- **Intel daemons** (`/opt/sygnif-services/`) — 17 daemons mirrored into
  `ec2-snapshot/services/`. See `SYGNIF.md` §3 and §6 for the full list.

See `SYGNIF.md` for trading doctrine (sizing, leverage tiers, pre-flight
gates, regime classification) and the canonical service inventory.

## Deploy

### Freqtrade stack (this repo's containers)

```bash
aws ec2-instance-connect send-ssh-public-key \
  --instance-id i-0cd5389584d70a7fc --instance-os-user ubuntu \
  --ssh-public-key file://~/.ssh/id_ed25519.pub --region eu-central-1

ssh ec2-eu1 "cd ~/sygnif && git pull && \
  docker compose restart freqtrade freqtrade-futures trade-overseer"
```

### Agent + intel daemons (separate flow)

See `SYGNIF.md` §7 — `bin/sync-docs.sh --push-windows`, EC2 deploy via
`sygnif-letscrash refresh`. Edits to brain code go to
`~/SYGNIF/third_party/neurolinked/`; edits to trader code go to
`/home/ubuntu/sygnif-agent-mirror/`.

## Health + inspection

```bash
# Recent trader cycles (master swarm on X1)
ssh x1 'sqlite3 /var/lib/sygnif/swarm.db \
  "SELECT datetime(created,\"unixepoch\"), agent_id, substr(content,1,140) \
   FROM swarm_entries WHERE topic=\"trader.heartbeat\" \
   ORDER BY created DESC LIMIT 10"'

# EC2 service health + live brain state
ssh ec2-eu1 'systemctl is-active sygnif-trader sygnif-neurolinked sygnif-brain-insights'
ssh ec2-eu1 'cat /home/ubuntu/SYGNIF/third_party/neurolinked/brain_state/live.json | jq .'

# Recent Freqtrade futures exits
ssh ec2-eu1 "cd ~/sygnif && \
  sqlite3 user_data/tradesv3-futures.sqlite \
  \"SELECT pair, enter_tag, exit_reason, close_profit, leverage, close_date \
    FROM trades WHERE is_open=0 ORDER BY close_date DESC LIMIT 20;\""
```

## Environment

Required env (per service — files live on EC2, not in the repo):

```
# /etc/sygnif/trader.env       — Bybit demo + live keys (mode 640)
# /etc/sygnif/bybit-mcp.env    — MCP server keys
# /home/ubuntu/SYGNIF/.env     — brain + swarm
# /home/ubuntu/sygnif-swarm/BTC_Prediction/.env
# /home/ubuntu/sygnif-swarm/BTC_Prediction/swarm_operator.env
```

None of these are checked into git. See `SNAPSHOT.md` for restore-from-
scratch steps.

## Quick links

- Canonical spec: [`SYGNIF.md`](./SYGNIF.md)
- AI agent briefing: [`AGENTS.md`](./AGENTS.md)
- Restore checklist: [`SNAPSHOT.md`](./SNAPSHOT.md)
- EC2 services snapshot: [`ec2-snapshot/`](./ec2-snapshot/)
- Legacy code (do not edit): [`archive/`](./archive/)

## License

Private. Do not redistribute.
