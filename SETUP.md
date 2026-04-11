# Sygnif -- Setup & Connection Guide

> Freqtrade trading bot on Bybit spot with NFI-inspired strategy + Claude sentiment layer.
> Runs on an Azure VM at `51.102.104.76`.

> **Naming:** The product/repo is **Sygnif**. Clone into **`~/SYGNIF`** (canonical). **`xrp_claude_bot`** was an old folder name only — if a host still uses it, set **`SYGNIF_REPO=/home/ubuntu/xrp_claude_bot`** in `.env` or symlink **`~/SYGNIF`** → that directory.

---

## Architecture

```
                         Azure VM (Ubuntu)
  +---------------------------------------------------------+
  |                                                         |
  |   +------------------+       +---------------------+   |
  |   |   Freqtrade      |       |   Claude Code CLI   |   |
  |   |   (Docker)       |       |   (SSH session)     |   |
  |   |                  |       |                     |   |
  |   |  SygnifStrategy  |       |  GitNexus MCP  <------------ Code intelligence
  |   |  - TA indicators |       |  Finance Agent      |   |
  |   |  - Claude Haiku  |       |  Skills & Hooks     |   |
  |   |  - Mover system  |       |                     |   |
  |   |                  |       +---------------------+   |
  |   |  API :8080  <-----------  Dashboard / FreqUI       |
  |   +------------------+                                  |
  |           |                                             |
  |     Bybit Spot API                                      |
  +---------------------------------------------------------+
```

---

## 1. Clone the Repo

```bash
git clone https://github.com/Giansn/sygnif.git ~/SYGNIF
cd ~/SYGNIF
```

---

## 2. Environment Variables

Copy and fill the `.env` file:

```bash
cp .env.example .env   # or create manually
```

Required keys:

| Key | Source | Purpose |
|-----|--------|---------|
| `BYBIT_API_KEY` | [Bybit API Management](https://www.bybit.com/app/user/api-management) | Exchange trading |
| `BYBIT_API_SECRET` | Same | Exchange auth |
| `ANTHROPIC_API_KEY` | [Anthropic Console](https://console.anthropic.com) | Claude Haiku sentiment calls |
| `TELEGRAM_BOT_TOKEN` | @BotFather on Telegram | Trade notifications |
| `TELEGRAM_CHAT_ID` | @userinfobot on Telegram | Your chat ID |
| `JWT_SECRET_KEY` | Generate: `openssl rand -hex 32` | Freqtrade API auth |
| `API_PASSWORD` | Your choice | Freqtrade API login |

---

## 3. Bot Setup

```bash
# Build and start (dry run by default)
docker compose up -d --build

# Check logs
docker compose logs -f --tail 50

# Verify API
curl -s http://localhost:8080/api/v1/ping
```

The bot runs on **5-minute candles** with up to **10 concurrent spot trades**.

### Config

Edit `user_data/config.json` for:
- `dry_run`: `true` (paper) / `false` (live)
- `dry_run_wallet`: simulated balance (default: 100 USDT)
- `max_open_trades`: concurrent positions (default: 10)
- `stake_amount`: `"unlimited"` = auto-size per trade

### Movers Pairlist

The bot reads top gainers/losers from `user_data/movers_pairlist.json`, updated by cron:

```bash
# Manual update
python3 update_movers.py

# Add to cron (every 4h)
(crontab -l; echo "0 */4 * * * /usr/bin/python3 ~/SYGNIF/update_movers.py >> ~/SYGNIF/movers_update.log 2>&1") | crontab -
```

---

## 4. Cursor & Claude Code

### Cursor (IDE & Cloud Agent)

Open this repo in **Cursor**. Persistent guidance lives in:

| Component | Source | What it does |
|-----------|--------|--------------|
| **`.cursor/rules/*.mdc`** | Project | Agent identity, workflows (always-on or glob-scoped) |
| **`SYGNIF_CONTEXT.md`** | Project root | Strategy, risk, deployment, tests, key files |
| **`AGENTS.md`** | Project root | GitNexus rules (inside `gitnexus:start` … `end`) |
| **MCP servers** | Cursor / global | GitNexus, AWS, etc. (configure per environment) |

Use **@SYGNIF_CONTEXT.md** or **@AGENTS.md** in chat when you want that context injected explicitly.

### Claude Code (CLI over SSH)

```bash
ssh ubuntu@51.102.104.76
cd ~/SYGNIF
claude   # launches Claude Code CLI
```

Or use **Claude Code Desktop/Web** with remote SSH connection.

### What Claude Code loads automatically

| Component | Source | What it does |
|-----------|--------|--------------|
| **`CLAUDE.md`** | Project root | Claude Code entry + GitNexus block (mirrors `AGENTS.md`; refreshed by `gitnexus analyze`) |
| **`SYGNIF_CONTEXT.md`** | Project root | Strategy / ops narrative |
| **`AGENTS.md`** | Project root | GitNexus impact / graph rules |
| GitNexus MCP | Global config | Code intelligence — query, context, impact, rename |
| Finance Agent (Cursor skill) | `.cursor/skills/finance-agent/SKILL.md` | Market research, TA, Telegram bot parity, strategy — attach in Cursor workspace |
| GitNexus Skills | `.claude/skills/gitnexus/` | Exploring, debugging, refactoring, impact analysis |
| Pre/PostToolUse Hooks | `~/.claude/settings.json` | Auto-enriches searches with graph context, detects stale index |
| Auto-memory | `~/.claude/projects/` | Persists user prefs and project context across sessions |

---

## 5. GitNexus MCP Server Setup

GitNexus provides a code knowledge graph over MCP (Model Context Protocol). It indexes the codebase into symbols, relationships, clusters, and execution flows.

### Install

```bash
npm install -g gitnexus
# or use npx (no install needed, auto-fetches latest)
```

### Register as MCP server (global)

Add to `~/.claude.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "gitnexus": {
      "command": "npx",
      "args": ["-y", "gitnexus@latest", "mcp"]
    }
  }
}
```

This makes GitNexus available to **every** Claude Code session on this machine.

### Index the repo

```bash
cd ~/SYGNIF
npx gitnexus analyze
```

This creates `.gitnexus/` with the knowledge graph. Re-run after significant code changes (a PostToolUse hook does this automatically after `git commit`).

### Verify

```bash
npx gitnexus status
```

Expected output: ~145 nodes, ~305 edges, 15 clusters, 9 execution flows.

### MCP Tools available in Claude Code

| Tool | Use |
|------|-----|
| `gitnexus_query({query: "..."})` | Find code by concept (e.g., "entry conditions") |
| `gitnexus_context({name: "..."})` | Full context: callers, callees, flows for a symbol |
| `gitnexus_impact({target: "...", direction: "upstream"})` | Blast radius before editing |
| `gitnexus_detect_changes()` | Pre-commit scope check |
| `gitnexus_rename({symbol_name: "old", new_name: "new"})` | Safe multi-file rename |
| `gitnexus_cypher({query: "MATCH ..."})` | Raw graph queries |

### MCP Resources

| Resource URI | Content |
|--------------|---------|
| `gitnexus://repos` | All indexed repositories |
| `gitnexus://repo/SYGNIF/context` | Codebase overview |
| `gitnexus://repo/SYGNIF/clusters` | Functional areas |
| `gitnexus://repo/SYGNIF/processes` | Execution flows |
| `gitnexus://repo/SYGNIF/process/{name}` | Step-by-step flow trace |

GitNexus resource paths use the **indexed directory name** from `npx gitnexus status`; it should be **`SYGNIF`** when you run `analyze` from **`~/SYGNIF`**.

---

## 6. AWS MCP Servers

Open-source MCP servers from [awslabs/mcp](https://github.com/awslabs/mcp) that give Claude Code direct access to AWS services, docs, and infrastructure tools.

### Install

AWS MCP servers use `uvx` (Python) — install it if not present:

```bash
pip install uv
```

### Add to `~/.claude.json`

Extend the `mcpServers` block alongside GitNexus:

```json
{
  "mcpServers": {
    "gitnexus": {
      "command": "npx",
      "args": ["-y", "gitnexus@latest", "mcp"]
    },
    "aws-docs": {
      "command": "uvx",
      "args": ["awslabs.aws-documentation-mcp-server@latest"],
      "env": {
        "FASTMCP_LOG_LEVEL": "ERROR"
      }
    },
    "aws-iac": {
      "command": "uvx",
      "args": ["awslabs.iac-mcp-server@latest"],
      "env": {
        "AWS_PROFILE": "default",
        "AWS_REGION": "eu-central-1"
      }
    },
    "aws-serverless": {
      "command": "uvx",
      "args": ["awslabs.aws-serverless-mcp-server@latest"],
      "env": {
        "AWS_PROFILE": "default",
        "AWS_REGION": "eu-central-1"
      }
    }
  }
}
```

### Available Servers

| Server | Package | Use |
|--------|---------|-----|
| **AWS Docs** | `awslabs.aws-documentation-mcp-server` | Search AWS docs, API references, What's New posts |
| **AWS IaC** | `awslabs.iac-mcp-server` | CloudFormation, CDK guidance, construct examples, security validation |
| **AWS Serverless** | `awslabs.aws-serverless-mcp-server` | SAM CLI, Lambda, API Gateway lifecycle |
| **AWS CloudFormation** | `awslabs.cfn-mcp-server` | Direct resource management via Cloud Control API |
| **Amazon ECS** | `awslabs.ecs-mcp-server` | Container orchestration, ECS deployment |
| **Amazon EKS** | `awslabs.eks-mcp-server` | Kubernetes cluster management |
| **Finch** | `awslabs.finch-mcp-server` | Local container builds + ECR push |
| **Lambda Tool** | `awslabs.lambda-tool-mcp-server` | Execute Lambda functions as AI tools |
| **AWS Support** | `awslabs.aws-support-mcp-server` | Create/manage support cases |

### Auth

AWS MCP servers use standard AWS credentials. On this instance (EC2 with IAM role `EC2-SSM-Role`), auth is automatic via instance profile. For other setups:

```bash
# Option 1: AWS CLI profile
aws configure --profile default

# Option 2: Environment variables
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION="eu-central-1"
```

### Verify

After adding servers, restart Claude Code. The new tools appear automatically:

```bash
claude
# Then in session: /mcp
```

---

## 7. GitNexus Hooks (Auto-enrichment)

Two hooks are configured globally in `~/.claude/settings.json`:

**PreToolUse** (on Grep/Glob/Bash):
- Intercepts searches and enriches results with related symbols from the knowledge graph
- Shows callers, callees, and affected flows before you even look at the code

**PostToolUse** (on Bash):
- Detects when `git commit`, `git merge`, or `git rebase` runs
- Warns if the GitNexus index is stale and needs re-analysis

These require no manual setup -- they activate automatically for any Claude Code session on this machine.

---

## 8. Finance Agent

Cursor skill at **`.cursor/skills/finance-agent/SKILL.md`** (in this repo): unified Sygnif domain + **`finance_agent/bot.py`** Telegram command parity. BTC-only analysis tooling lives under **`finance_agent/btc_specialist/`** with skill **`.cursor/skills/btc-specialist/SKILL.md`**.

### Trigger in Cursor

Attach the **finance-agent** skill (or invoke your client’s slash if configured). Legacy Claude Code **`/finance-agent`** referred to the same content when mirrored globally; canonical copy is the repo **`.cursor/skills/`** file.

### Modes

| Mode | Trigger | What it does |
|------|---------|--------------|
| Quick Market | "market", "prices" | Top movers, volume, 24h changes |
| Coin Research | "research BTC" | Parallel TA + news + synthesis |
| Investment Plays | "plays", "opportunities" | 3 actionable trade setups |
| Strategy Exploration | "NFI", "strategy" | Deep dive into Sygnif/NFI code via GitNexus |
| Comprehensive | "full report" | All of the above combined |
| Macro Correlation | "macro", "fed" | BTC vs S&P/DXY/Gold analysis |

### Data sources

- Bybit REST API (tickers, OHLCV)
- CoinTelegraph, CoinDesk, CryptoPanic RSS
- GDELT news API
- WebSearch for live macro data

---

## 9. Indexed Repos

Two repos are indexed by GitNexus and available for code exploration:

| Repo | Path | Nodes | Use |
|------|------|-------|-----|
| **Sygnif** | `~/SYGNIF` | (varies) | Our strategy — entries, exits, sentiment, movers |
| **NostalgiaForInfinity** (NFI) | `~/NostalgiaForInfinity` | 1,662 | Reference strategy -- patterns, grind modes, exit logic |

Query across both:
```
gitnexus_query({query: "grind mode entry"})
```

---

## 10. Dashboard

```bash
# Access FreqUI at:
http://51.102.104.76:8080

# Or the custom dashboard:
python3 dashboard_server.py
# -> http://51.102.104.76:8888
```

Login: `freqtrader` / (password from `.env`)

---

## 11. Project Structure

```
SYGNIF/   # or your clone directory; see naming note above
+-- docker-compose.yml          # Freqtrade container config
+-- .env                        # API keys (git-ignored)
+-- SygnifStrategy.py           # Strategy (root copy)
+-- update_movers.py            # Movers pairlist generator (cron every 4h)
+-- telemetry.py                # Telegram status reports
+-- dashboard.html              # Custom web dashboard
+-- dashboard_server.py         # Dashboard HTTP server
+-- tf_controller.py            # Timeframe controller
+-- SYGNIF_CONTEXT.md           # Strategy, risk, deploy (human + agent context)
+-- AGENTS.md                   # GitNexus rules (fenced; refreshed by analyze)
+-- CLAUDE.md                   # Claude entry + GitNexus block (same fenced text as AGENTS.md)
+-- .cursor/rules/              # Cursor IDE / Cloud Agent rules
+-- SETUP.md                    # This file
+-- user_data/
|   +-- config.json             # Freqtrade config (git-ignored)
|   +-- strategies/
|   |   +-- SygnifStrategy.py   # Strategy (active copy used by bot)
|   +-- movers_pairlist.json    # Top gainers/losers (auto-updated)
|   +-- tradesv3.sqlite         # Trade database
|   +-- logs/
+-- .claude/
|   +-- skills/                 # GitNexus + generated cluster skills
+-- .gitnexus/                  # Knowledge graph index (git-ignored)
```

---

## Quick Reference

```bash
# Start bot
docker compose up -d

# Stop bot
docker compose down

# View logs
docker compose logs -f --tail 50

# Check open trades
curl -s -X POST http://localhost:8080/api/v1/token/login \
  -H "Content-Type: application/json" \
  -d '{"username":"freqtrader","password":"YOUR_PW"}' | jq -r .access_token

# Update movers now
python3 update_movers.py

# Re-index codebase
npx gitnexus analyze

# Launch Claude Code
claude
```
