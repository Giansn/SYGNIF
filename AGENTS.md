# AGENTS.md — briefing for AI coding agents

If you are an AI coding agent (Jules, Claude Code, Cursor, etc.) about to
make changes in this repo: read this file first, then read `SYGNIF.md`
(canonical architecture spec) for depth.

## What this repo is

SYGNIF is a three-process autonomous BTC trading system spread across two
hosts plus a Bybit demo account:

| Tier | Host | What |
|---|---|---|
| **Author** | X1 (Windows, this repo's home) | sygnif-trader (NO_EXECUTE), MCP servers, master `swarm.db`, dashboards |
| **Executor** | EC2 eu-central-1 | sygnif-trader (demo orders), NeuroLinked brain (3,000 Izhikevich neurons, STDP), 17+ intel daemons, freqtrade containers |
| **Venue** | Bybit demo (UTA) | ≈ $1.5 k equity, perp + options |

This is a **production-shaped repo**, not a playground. Real money flows
on the executor side. Treat every change as if it could move a position.

## Repository layout

```
.
├── SygnifStrategy.py           Freqtrade spot strategy (live)
├── user_data/                  Freqtrade configs + journal dir
├── docker-compose.yml          4-container stack (spot, futures, overseer, notify)
├── docker/                     Dockerfiles
├── trade_overseer/             Telegram commentary + NPU LLM hooks (live)
├── finance_agent/              Briefing + strategy router (live)
├── notification_handler.py     Webhook fan-out
│
├── ec2-snapshot/               READ-ONLY — exact copy of EC2 services
│   ├── services/               46 daemons from /opt/sygnif-services/
│   ├── systemd/                60 unit files + 7 drop-in dirs
│   ├── neurolinked/            Brain code (no state — that's 1.9 GB and lives on EC2)
│   └── trader/                 EC2 agent code mirror
│
├── archive/                    READ-ONLY — legacy files kept for blame history
│
├── docs/                       Architecture + ops docs
├── tests/                      Unit tests
│
├── AGENTS.md                   This file
├── CLAUDE.md                   Long-form Claude Code instructions (mirrors SYGNIF.md)
├── SYGNIF.md                   Canonical system specification — the source of truth
├── SNAPSHOT.md                 What the 2026-05-13 snapshot commit captured
├── README.md                   Human-facing overview
└── SETUP.md                    Bootstrap instructions
```

## Where new code should land

**Default for any experimental / sandboxed work:** create a new top-level
`experiments/<name>/` directory and put everything there. This keeps
sandboxed work clearly separated from production code paths.

For the current in-flight Jules task (BTC Golden Cross simulator + SYGNIF
edge-attribution / lead-lag toolkit): land everything under
`experiments/sygnif_toolkit/` with this structure:

```
experiments/sygnif_toolkit/
├── pyproject.toml              poetry-managed, isolated from root
├── README.md                   how to install and run, attribution-report guide
├── bitcoin_sim.py              Golden Cross BTC simulator (Phase 1 deliverable)
├── edge_attrib/                Phase 1 — PnL decomposition harness
│   ├── __init__.py
│   ├── decompose.py            CLI: python -m edge_attrib decompose
│   └── report.py               CLI: python -m edge_attrib report
├── lead_lag/                   Phase 2 — cross-venue lead-lag signal
│   ├── __init__.py
│   ├── indicators.py           Fibonacci, S/R, SFP — shared math library
│   ├── record.py               python -m lead_lag record
│   ├── stream.py               python -m lead_lag stream
│   ├── logic.py                EWMA mid-velocity + cross-correlation
│   └── backtest.py             python -m lead_lag backtest
├── fixtures/
│   ├── fills.jsonl             24h synthetic fills with ground-truth components
│   └── book_l2/<venue>/*.jsonl
└── tests/
    ├── test_decompose.py       per-component recovery to ±$0.01
    ├── test_indicators.py
    └── test_lead_lag.py
```

Do NOT add this work next to `SygnifStrategy.py`, into `user_data/`, or
into `ec2-snapshot/`. Those are live or read-only.

## Rules (apply to every change)

1. **Real data only.** Never fabricate prices, indicators, equity values,
   or P&L. If you need test data, generate synthetic with clearly-marked
   ground truth (see `fixtures/` convention above).
2. **No secrets in commits.** API keys, `.env` files, SSH keys, AWS
   credentials — none of these go in git. `.gitignore` already covers
   the common ones. Verify with `git status` before committing.
3. **Read-only directories:**
   - `archive/` — legacy code, kept for blame history. Do not edit.
   - `ec2-snapshot/` — verbatim mirror of EC2 state. Do not edit; if you
     need to change a daemon, do it on EC2 and re-snapshot.
4. **No live execution.** Demo trading is OK if explicitly invoked
   (`SYGNIF_ORDERS_MODE=demo`). Live trading requires
   `SYGNIF_ORDERS_LIVE=clear-for-live` AND explicit user confirmation in
   a chat message — never as a default, never as a side effect.
5. **PRs, not direct pushes to main.** Always work on a feature branch
   and open a PR. Main is the deployed reference.
6. **OrderLinkID prefix discipline.** Every order-placing daemon stamps
   a stable prefix so post-trade attribution works:
   - `sygFAST` — fast-reactor (the current authorized perp opener)
   - `sygSTND` — standing-orders (inactive)
   - `sygTRN` — training-scanner (inactive)
   - `sygOL` / `sygCS` — options open / close-stop
   - `perpRun` — perp-runner (inactive)
   - **Never reuse a known prefix for a different daemon.**
   - If you write a new order-placing daemon, pick a fresh prefix and
     document it in `SYGNIF.md`.
7. **Tier flags are planner-only.** `leverage_tier` and `size_tier` on a
   plan are set by `agent.trade.plan`, never hand-injected by tools or
   helper functions. Default tier if no justification.
8. **WAIaaS triple-gate.** Mutating chain neurons require
   `confirm: True` AND `i_understand_real_money: 'yes'`. Do not bypass.
9. **Line endings.** Never check in `.py`, `.sh`, or systemd unit files
   with CR characters. Use LF. (Windows agents: configure your editor or
   strip CRs before staging.)

## What to ignore

- This file used to reference a GitNexus MCP server. Most coding agents
  (Jules included) can't reach that. If your environment has GitNexus,
  treat its output as one input among many. Otherwise skip it and read
  `SYGNIF.md` for canonical structure.

## Quick orientation

To understand a specific area before editing:

```
# read the canonical spec
read SYGNIF.md

# what's in ec2-snapshot — the EC2 daemons
ls ec2-snapshot/services/ ec2-snapshot/systemd/

# what's actively deployed at the repo root (vs archived)
ls -d archive/         # do not edit
ls *.py *.md           # the live root-level files
```

## Before opening a PR

1. Tests pass for whatever you touched.
2. `git diff --cached --stat` — change set looks like what you intended;
   no surprise files.
3. No secrets snuck in:
   `git diff --cached --name-only | grep -iE "\.env$|secret|password|credential|\.key$"`
4. PR description: state the goal, what changed, how you tested, and
   anything the reviewer should look at carefully.

That's the brief. Now read `SYGNIF.md` for the full architecture.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **SYGNIF** (10379 symbols, 15370 relationships, 297 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/SYGNIF/context` | Codebase overview, check index freshness |
| `gitnexus://repo/SYGNIF/clusters` | All functional areas |
| `gitnexus://repo/SYGNIF/processes` | All execution flows |
| `gitnexus://repo/SYGNIF/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
