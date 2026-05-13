# AGENTS.md — briefing for AI coding agents

If you are an AI coding agent (Jules, Claude Code, Cursor, etc.) about to
make changes in this repo: read this file first, then `SYGNIF.md` for
the architecture spec, then `SNAPSHOT.md` for the EC2 restore reference.

**Last updated:** 2026-05-13. The "Drawbacks & known issues" section
near the bottom is the most current truth — read it.

---

## TL;DR — what this system is

SYGNIF is an autonomous BTC trading system, three processes across two
hosts plus a Bybit demo account. Real money flows in the demo (~$1.5 k
equity); the live mainnet wallet is essentially empty. Treat every
change as if it could move a position.

```
┌──────────────── X1 (Lenovo Yoga, Windows + WSL) ──────────────┐
│  sygnif-trader (author, SYGNIF_TRADER_NO_EXECUTE=1) — plans   │
│  sygnif-x1-mcp / sygnif-bybit-mcp / sygnif-commander-mcp      │
│  master swarm.db (/var/lib/sygnif/swarm.db, group sygnif-users)│
│  dashboards, sygnif-letscrash CLI, Bee/Swarm permanence tunnel│
└────────────────────────────────────────────────────────────────┘
                          ↕ Tailscale mesh (tailff64b5.ts.net)
┌──────────────── EC2 eu-central-1 (m7i-flex.large) ────────────┐
│  sygnif-trader (executor, SYGNIF_ORDERS_MODE=demo)             │
│  NeuroLinked brain :8889  3000 Izhikevich neurons, STDP        │
│  Brain insights :8890  read-only dashboard                     │
│  17+ intel daemons   chain / evm / tron / ecosystem / news ... │
│  intel-aggregator + fast-reactor (sub-ms perp opener)          │
│  Freqtrade containers (legacy spot + futures execution)        │
│  trade-overseer (Telegram commentary)                          │
│  + Tailscale node sygnif-ec2 @ 100.97.226.116                  │
└────────────────────────────────────────────────────────────────┘
                          ↕ Bybit V5 (api-demo.bybit.com)
┌──────────────── Bybit demo (UTA) ─────────────────────────────┐
│  equity ≈ $1,500 USDT                                          │
│  open structures: trades opened by fast-reactor + daemons     │
└────────────────────────────────────────────────────────────────┘
```

---

## Repo layout — what's live, what's read-only

```
.                                  ← X1's home of canonical agent code + execution layer
├── SygnifStrategy.py              live freqtrade spot strategy
├── user_data/                     live freqtrade configs + journal
├── docker-compose.yml             live 4-container stack
├── docker/                        Dockerfiles
├── trade_overseer/                live Telegram commentary
├── finance_agent/                 live briefing + strategy router
├── notification_handler.py        live webhook fan-out
│
├── ec2-snapshot/                  READ-ONLY mirror of /opt/sygnif-services/ on EC2
│   ├── services/   (46 daemons)
│   ├── systemd/    (60 unit files + 7 drop-in dirs)
│   ├── neurolinked/ (brain code — no state, 1.9 GB stripped)
│   └── trader/     (agent + 4 MCP servers)
│
├── archive/                       READ-ONLY legacy (old dashboards, sygnif_bot,
│                                   tf_controller, update_movers, mcp_rethink ...)
│
├── docs/                          architecture + ops + workflow docs
├── tests/                         unit tests
├── experiments/                   per-project sandboxed work — new agent code lands here
│
├── AGENTS.md  ← this file
├── CLAUDE.md  long-form Claude Code instructions (mirrors SYGNIF.md)
├── SYGNIF.md  canonical architecture spec — the source of truth
├── SNAPSHOT.md  what the 2026-05-13 snapshot commit captured + restore steps
├── README.md   human-facing overview
└── SETUP.md    bootstrap instructions
```

**Do not edit `archive/` or `ec2-snapshot/`.** They are git-tracked
checkpoints, not the live system. To change a daemon, edit it on EC2
and re-snapshot.

---

## Where new code should land

Default: `experiments/<project-name>/` — a fresh top-level directory per
piece of sandboxed work, isolated from production paths.

The in-flight Jules toolkit task (BTC Golden Cross + edge-attribution
+ lead-lag) is expected to land under `experiments/sygnif_toolkit/`
with this structure:

```
experiments/sygnif_toolkit/
├── pyproject.toml              poetry-managed
├── README.md                   install + run + attribution guide
├── bitcoin_sim.py              Golden Cross BTC simulator
├── edge_attrib/                Phase 1 — PnL decomposition harness
│   ├── decompose.py            python -m edge_attrib decompose
│   └── report.py               python -m edge_attrib report
├── lead_lag/                   Phase 2 — cross-venue lead-lag signal
│   ├── indicators.py           Fibonacci, S/R, SFP — shared math
│   ├── record.py               python -m lead_lag record
│   ├── stream.py               python -m lead_lag stream
│   ├── logic.py                EWMA mid-velocity + cross-correlation
│   └── backtest.py             python -m lead_lag backtest
├── fixtures/
│   ├── fills.jsonl             24h synthetic with ground-truth components
│   └── book_l2/<venue>/*.jsonl
└── tests/
    ├── test_decompose.py       residual ≤ $0.01 gate
    ├── test_indicators.py
    └── test_lead_lag.py
```

**Do not** place experimental work next to `SygnifStrategy.py`, into
`user_data/`, or into `ec2-snapshot/`. Live or read-only.

---

## External resources you can use

### GitHub repos (Giansn)

| Repo | Purpose | Live? |
|---|---|---|
| `Giansn/SYGNIF` | This repo. Main monorepo. | yes |
| `Giansn/Sygnif-On-Chain-Intelligence` | Backup of on-chain intel daemons. Source of truth for what's deployed on EC2 under `/opt/sygnif-services/`. PR #2 (v3 architecture) is open for review. | yes |
| `Giansn/sygnif-bastion` | HTTPS→SSH bastion deployed to Render (see below). | yes |

### Open pull requests (as of writing)

| Repo | PR | Title | Status |
|---|---|---|---|
| SYGNIF | [#8](https://github.com/Giansn/SYGNIF/pull/8) | feat: Add sygnif_toolkit with BTC sim, edge attribution, and lead-lag analysis | **OPEN, needs revisions** — wires indicators into live `SygnifStrategy.py` (out of scope); re-injects GitNexus boilerplate; missing `lead_lag/indicators.py` |
| SYGNIF | [#7](https://github.com/Giansn/SYGNIF/pull/7) | Add REPO_ANALYSIS.md | OPEN |
| On-Chain-Intel | [#2](https://github.com/Giansn/Sygnif-On-Chain-Intelligence/pull/2) | feat: v3 architecture — signal aggregator + centralized price fetching | OPEN |

### Render (Sygnif team `tea-d81s2b8sfn5c738u1sbg`)

| Service | URL | Status |
|---|---|---|
| `sygnif-bastion` | https://sygnif-bastion.onrender.com | **live, but SSH currently fails** (see Drawbacks below) |

API key in `~/Desktop/Sygnif Local/API.txt` as `JULES_RENDER_API` (prefix `rnd_`).

### Tailscale (tailnet `tailff64b5.ts.net`)

| Device | TS IP | Identity |
|---|---|---|
| `sygnif-ec2` | 100.97.226.116 | linux, Giansn@ |
| `thinkx1` | 100.71.122.115 | linux X1 |
| `gtaura` | 100.97.19.123 | Windows X1 |

Tailscale auth keys in `API.txt` as `AUTH_KEY`. **Note:** one was leaked
in Render logs on 2026-05-13 and rotated; the new key is what's there
now. Don't paste auth keys into chat or commits.

### Jules (Google Labs coding agent)

- API: `https://jules.googleapis.com/v1alpha/` with header `x-goog-api-key`
- Key in `API.txt` as `JULES_API` (prefix `AQ.`)
- Sources: GitHub repos must be linked from the Jules UI first
- `AGENTS.md` (this file) is read by Jules at session start
- Jules has Tailscale-SSH access to EC2 as `Giansn@github` — see
  Drawbacks/concurrent-agents below

### Bee / Swarm (mainnet permanence)

Mainnet Swarm Bee for permanent ref storage. Tunnel runs on X1 as
`sygnif-bee-tunnel`. Lessons / postmortems get a `bzz://...` ref when
material.

---

## Service inventory — what's running where (2026-05-13)

### Active on EC2 (verified `systemctl is-active` returns `active`)

| Service | What it does |
|---|---|
| `sygnif-trader.service` | EC2-side executor; demo mode; runs `agent.loop --daemon` |
| `sygnif-neurolinked.service` | 3000-neuron Izhikevich brain on :8889 |
| `sygnif-brain-insights.service` | Read-only dashboard + WS bypass on :8890 |
| `sygnif-bybit-mcp.service` | MCP vault for Bybit ops |
| `sygnif-fast-reactor.service` | Sub-ms WS reactor, opens perps with `sygFAST` prefix |
| `sygnif-trailing-daemon.service` | Real-time trailing stops |
| `sygnif-perp-runner.service` | scanner-driven perp executor (`perpRun` prefix) |
| `sygnif-funding-harvester.service` | funding-rate arbitrage scanner |
| `sygnif-intel-aggregator.service` | 30s digest of 17+ sources → `intel_summary.json` |
| `sygnif-chain-intel.service` | UTXO age, CIH clustering, peeling, OFAC |
| `sygnif-evm-signals.service` / `evm-extras.service` | stablecoin mints, exch reserves, DEX, bridges |
| `sygnif-tron-signals.service` | Tron-side USDT mint tracker (using public v1 — key disabled) |
| `sygnif-xchg-liquidations.service` | binance + okx + bitget liq WS |
| `sygnif-ecosystem.service` | DefiLlama + CoinGecko + Goldrush |
| `sygnif-market-premium.service` | Coinbase/Binance premium |
| `sygnif-microstructure-feed.service` | funding / basis / OI |
| `sygnif-news-feed.service` / `polymarket-feed.service` / `hivemind-feed.service` | macro + prediction-market + options |
| `sygnif-market-brain-feed.service` | market_synth → brain `/api/input/text` (hardened: 120 s POST timeout, 900 s cycle) |
| `sygnif-brain-context.service` / `trade-nl-publisher.service` / `bybit-nl-feed.service` | publishers feeding the brain (hardened to 120 s timeout) |
| `sygnif-whale-watcher.service` / `dlp.service` / `telegram-relay.service` | aux |
| `sygnif-cf-tunnel.service` / `read-api.service` | tunnels (read-api was added 2026-05-13 by another agent) |

### Inactive on EC2 (intentionally stopped)

| Service | Reason stopped |
|---|---|
| `btc-predict-runner.timer` + `.service` | **bleeding** — at 50× lev / $100k notional / 60 s ticks, lost $253 over 7 days at 14 % win rate. Stopped 2026-05-13 00:42 UTC. Disabled on boot. |
| `sygnif-standing-orders.service` | Stopped earlier; awaiting redesign |
| `sygnif-bounce-watcher.service` | Stopped earlier |
| `sygnif-training-scanner.service` | Stopped earlier |
| `sygnif-bybit-daemon.service` | Stopped earlier; attribution work depends on it being off |
| `sygnif-trailing-manager.service` | Replaced by `sygnif-trailing-daemon.service` |

To re-enable any of these, get explicit user confirmation in chat
first — they were stopped for a reason.

### Active on X1

| Service | Notes |
|---|---|
| `sygnif-trader.service` | Planner mode, `SYGNIF_TRADER_NO_EXECUTE=1`. Plans only. |
| `sygnif-bee-tunnel.service` | Swarm/Bee mainnet permanence |
| `sygnif-x1-mcp` / `sygnif-bybit-mcp` / `sygnif-commander-mcp` | HTTP MCP servers |
| `sygnif-letscrash` | Boot bringup + daily refresh + EC2 sync |
| `sygnif-dashboard*` | Read-only dashboards (legacy paths) |

---

## Rules — apply to every change

1. **Real data only.** Never fabricate prices, indicators, equity values
   or P&L. For tests, generate synthetic data with clearly labelled
   ground-truth fields.
2. **No secrets in commits.** `.env`, `.pem`, `id_*` keys, AWS creds —
   `.gitignore` covers the common ones; verify with
   `git diff --cached --name-only | grep -iE "\.env$|secret|key$|credential"`.
   The 2026-05-13 Tailscale-key-in-Render-logs incident is a reminder
   that even logs can leak credentials. Always pipe potentially-key-
   bearing stderr through a redactor.
3. **Read-only directories:** `archive/`, `ec2-snapshot/`. Period.
4. **No live execution.** Demo trading: explicit invocation only
   (`SYGNIF_ORDERS_MODE=demo`). Live mainnet: requires
   `SYGNIF_ORDERS_LIVE=clear-for-live` AND explicit user chat
   confirmation. Never via default, never as a side effect.
5. **PRs, not direct pushes to main.** Always feature branch + PR.
   Main is the deployed reference. The recent `force-push to main`
   for the 2026-05-13 snapshot was a one-off; don't make a habit.
6. **OrderLinkID prefix discipline.** Every order-placing daemon stamps
   a stable prefix so post-trade attribution works. Known prefixes:

   | Prefix | Source | Status |
   |---|---|---|
   | `sygFAST` | fast-reactor | active (current authorized perp opener) |
   | `sygSTND` | standing-orders | inactive |
   | `sygTRN` | training-scanner | inactive |
   | `sygOL` / `sygCS` | option.py — open / close-stop | active |
   | `perpRun` | perp-runner | active |
   | `sygRT` | bybit_daemon action_executor | inactive |
   | `sygPL` | **legacy bleeder** — old `btc_predict_protocol_loop.py` from `sygnif-swarm/BTC_Prediction`. **Stopped.** Do not reuse this prefix. |

   If you write a new order-placing daemon, pick a fresh prefix and
   document it here AND in `SYGNIF.md` §3.
7. **Tier flags are planner-only.** `leverage_tier` and `size_tier` on
   a plan are set by `agent.trade.plan`, never hand-injected by tools.
8. **WAIaaS triple-gate.** Mutating chain neurons require
   `confirm: True` AND `i_understand_real_money: 'yes'`. Do not bypass.
9. **Line endings.** Never check in `.py`, `.sh`, or systemd unit files
   with CR characters. Use LF. Windows agents: configure your editor
   or strip CRs before staging.
10. **Implementation-tax is real.** 2026-04-29 lesson: 8 min decision-
    to-action on a 2-leg orphan strangle bled −$29.40. Pre-arm close
    brackets at open, mid-cross limits not panic-cross, combo orders
    for multi-leg, track per-trade slip as a first-class KPI.

---

## File paths cheat sheet

```
# X1 paths
~/sygnif/                              this repo (execution layer + EC2 mirror)
~/sygnif/sygnif-agent/                 (does not exist — agent code is on EC2)
~/.ssh/sygnif-bastion(.pub)            keypair for the Render bastion (don't commit)
~/Desktop/Sygnif Local/API.txt         all external API keys — never paste to chat
                                         (Jules, Render, Tailscale, others)
~/.aws/credentials                     AWS CLI creds (EC2 SG, Instance Connect)

# EC2 paths
/opt/sygnif-services/                  46 daemons (mirrored in ec2-snapshot/)
/etc/systemd/system/sygnif-*.service   unit files
/etc/sygnif/trader.env                 Bybit demo + live keys (mode 640 root:ubuntu)
/etc/sygnif/bybit-mcp.env              MCP keys
/etc/sygnif/tron-keys.env              TronGrid key — currently DISABLED (bogus key)
/var/lib/sygnif/                       state files (chain, evm, tron, intel_summary,
                                         portfolio_demo, market_premium, etc.)
/var/lib/sygnif/swarm.db               master swarm SQLite (read via mode=ro from agents)
/var/log/sygnif/                       all daemon logs (StandardOutput=append:...)
/home/ubuntu/SYGNIF/                   legacy "SYGNIF" dir (scripts, third_party,
                                         brain state under third_party/neurolinked/)
/home/ubuntu/SYGNIF/third_party/neurolinked/brain_state/
                                       live brain state (1.9 GB, regions/synapses/
                                         knowledge.db/live.json — never commit)
/home/ubuntu/sygnif-agent-mirror/      EC2 mirror of agent code (sygnif-trader runs here)
/home/ubuntu/sygnif-swarm/             legacy BTC_Prediction system (mostly archived)
                                         — btc_predict_protocol_loop.py was the bleeder
```

---

## Common diagnostic queries

```bash
# Recent trader cycles (X1 master swarm)
ssh x1 'sqlite3 /var/lib/sygnif/swarm.db \
  "SELECT datetime(created,\"unixepoch\"), agent_id, substr(content,1,140) \
   FROM swarm_entries WHERE topic=\"trader.heartbeat\" \
   ORDER BY created DESC LIMIT 10"'

# EC2 service health
ssh ec2-eu1 'systemctl is-active sygnif-trader sygnif-neurolinked \
             sygnif-fast-reactor sygnif-intel-aggregator'

# Brain live state (step count, neuromodulators)
ssh ec2-eu1 'cat /home/ubuntu/SYGNIF/third_party/neurolinked/brain_state/live.json | jq .'

# Recent Bybit demo closed-PnL (via bybit MCP or direct)
ssh ec2-eu1 'sudo bash -c "set -a; source /etc/sygnif/trader.env; set +a; \
  /opt/sygnif/.venv/bin/python -c \"import os,urllib.request,hmac,hashlib,json,time; \
  ... see pnl_diag.py in C:/Users/giank/AppData/Local/Temp/ \" "'

# Currently-bleeding orderLinkID prefixes (last 100 closed trades)
# (use the diagnostic script in C:/Users/giank/AppData/Local/Temp/pnl_diag.py)
```

---

## Drawbacks & known issues — read this section

### 1. Bastion: live but SSH path broken (Tailscale on Render free tier)

`https://sygnif-bastion.onrender.com` is up. `GET /` returns 200,
`GET /health` returns 200 with `ec2_reachable: false`, `POST /exec`
returns 500. **Why:** Render's free tier blocks the UDP traffic
Tailscale needs for DERP relays. `tailscale up` hangs; my `start.sh`
times it out after 30 s and falls back to direct SSH, but
`EC2_HOST=sygnif-ec2` only resolves via Tailscale DNS so SSH fails
with `gaierror: name not known`.

**Three fixes available** (none applied as of writing — pending user
choice):

- **A1**: change `EC2_HOST` env var to public IP `3.64.28.14` and open
  EC2 SG port 22 to `0.0.0.0/0`. Bastion private key (ed25519) is the
  only way in; if it leaks, delete one line from `~/.ssh/authorized_keys`
  matching marker `bastion-render-rw`.
- **A2**: upgrade Render to Starter ($7/mo) for static egress IPs;
  allowlist only those in EC2 SG.
- **B**: try harder with Tailscale — bump timeout to 120 s, force
  DERP-over-HTTPS (TCP/443).

Until one of these lands, the bastion is "live but SSH-blocked".

### 2. SygnifStrategy.py has synchronous HTTP in `populate_entry_trend`

Flagged by Jules' v3 diagnosis (see `Sygnif-On-Chain-Intelligence` PR #2,
file `SYSTEM_DIAGNOSIS.md`):

> The strategy currently performs synchronous HTTP requests to RSS
> feeds and the Claude API within `populate_entry_trend`. If multiple
> pairs trigger a sentiment check simultaneously, or if the API is
> slow, the bot will lag — potentially missing entries or causing
> "out of sync" errors.

Planned fix: move sentiment to a background daemon
(`sygnif_sentiment_daemon.py`) writing to `swarm.db`; strategy reads
with a 10-min in-memory cache. **Not yet implemented.** Until then,
adding more indicator math directly to the strategy compounds the
latency risk — be cautious when extending entry/exit logic.

### 3. Concurrent agents on EC2 — coordinate

Multiple agent processes touch `/opt/sygnif-services/` and other paths.
When deploying, snapshot before/after and check who else has been
modifying files recently:

| Agent | Where | Behavior |
|---|---|---|
| **Cursor IDE worker** | `/home/ubuntu/.local/bin/agent worker` (since May 1) | Background; long-running; mostly read but can write under `~/SYGNIF/` |
| **Jules via Tailscale-SSH** | logs in as `Giansn@github` from `100.97.19.123` | Has full ubuntu privileges; can create services (added `sygnif-read-api.service` on 2026-05-13) and run `tailscale serve --bg --https=443 → 127.0.0.1:8765` to publicly expose ports |
| **You** (this agent) | via X1 with `~/.ssh/id_ed25519` or the bastion | Track edits with `git status` and EC2 `find /opt/sygnif-services -mmin -120` |

**Before writing to EC2:** check `find /opt/sygnif-services -mmin -60`
and `who`/`last -i -n 10`. If another agent's session is active,
either coordinate (chat with the user) or wait.

### 4. Brain ingest is slow (~72 s per text input)

NeuroLinked is single-threaded under the Python GIL. Posts to
`/api/input/text` block the brain for ~72 s. The brain-feed publishers
have been hardened to 120 s POST timeout + 90/180/900 s cycle intervals
(see `harden_brain_publishers.sh` for the pattern). **Don't reduce
those intervals** — at 60 s cadence the queue fills up faster than
the brain can drain it.

### 5. Bybit UTA position blending

Same-symbol+side positions merge into one blended position on Bybit
UTA. FIFO attribution can't unblend cleanly. Mitigation: strict
strategy-claim mutex (`agent/strategy_claim.py`) + orderLinkID prefix
discipline (see Rules §6). If you write a new order-placing daemon,
respect the mutex.

### 6. Tron API key currently disabled

`/etc/sygnif/tron-keys.env` has `SYGNIF_TRON_KEY` commented out — the
key value got `"ApiKey not exists"` from TronGrid. Daemon falls back
to public v1 (works for our scale). To restore: get a valid TronGrid
Pro key and uncomment.

### 7. Coordination friction with the Tron header fix

PR #1 on the on-chain repo fixed a real bug: `if False and TRON_API_KEY:`
mashed into one line meant the TRON-PRO-API-KEY header was never set.
After deploying the fix, the daemon started sending the bogus key and
got 401'd 1×/cycle. Mitigation in §6.

### 8. Render bastion auth key leaked once

On 2026-05-13 ~02:05 UTC, a Tailscale auth key was echoed into Render
logs when `tailscale up` failed with `--advertise-tags`. The key was
revoked + rotated. `start.sh` now pipes stderr through a sed
redactor and uses `timeout 30s` so retries don't print full original
commands. If you change `start.sh` in `sygnif-bastion`, **preserve the
redactor**.

### 9. Stale GitNexus index

The local `.gitnexus/` index is stale (last indexed `5f09acc` —
older than the snapshot commit). Most agents (Jules, generic LLMs)
can't reach GitNexus tools anyway, so this matters mainly for Claude
Code on X1. Run `npx gitnexus analyze` when needed; otherwise ignore.

### 10. The "swarm.db divergence" — two writers

Both X1 and EC2 maintain a `swarm.db` at `/var/lib/sygnif/swarm.db`.
EC2→X1 mirror runs every 2 min via `swarm_x1_mirror`. X1→EC2 reverse
push is via `sygnif-letscrash` step 8. If you query for "recent
events", choose your host:

- For predict / brain context / EC2 daemon emits → query EC2's `swarm.db`
- For plan_authored / agent.review / X1-side reasoning → query X1's

In a pinch, run the same query against both and reconcile.

---

## When you're done with a change

1. Tests pass for whatever you touched.
2. `git diff --cached --stat` — change set looks like what you intended.
3. No secrets snuck in:
   `git diff --cached --name-only | grep -iE "\.env$|secret|key$|credential"`
4. If you modified anything on EC2 directly, also re-snapshot it into
   `ec2-snapshot/` in a follow-up PR — otherwise the git tree drifts.
5. PR description: state the goal, what changed, how you tested, and
   anything the reviewer should look at carefully. Include any
   backtest/diagnostic outputs as evidence.

That's the brief. Now read `SYGNIF.md` for the full architecture spec
and trading doctrine.
