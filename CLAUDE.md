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
2. **Soft SL fires at correct levels** — `exit_stoploss_conditional` exits should show `current_profit` near -12%, NOT at -3% to -5% (the old broken range at 3-5x leverage).
3. **Win/loss ratio improves on futures** — The old double-division bug was cutting winners short while letting losers run full doom distance. Futures P&L should trend toward symmetry.
4. **Fewer doom exits overall** — With breakeven guard + correct soft SL, fewer trades should reach the -20% hard stop.

### How to Check

```bash
# SSH into EC2 and check recent exits
ssh ubuntu@3.122.252.186 "cd ~/xrp_claude_bot && \
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
- **Repo path on instance**: `~/xrp_claude_bot`

### Deploy Commands

```bash
# Push SSH key (required before each SSH session)
aws ec2-instance-connect send-ssh-public-key \
  --instance-id i-0cd5389584d70a7fc \
  --instance-os-user ubuntu \
  --ssh-public-key file://~/.ssh/id_ed25519.pub \
  --region eu-central-1

# SSH and deploy
ssh ubuntu@3.122.252.186 "cd ~/xrp_claude_bot && git pull && docker compose restart freqtrade freqtrade-futures"
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

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **sygnif** (540 symbols, 1337 relationships, 42 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## When Debugging

1. `gitnexus_query({query: "<error or symptom>"})` — find execution flows related to the issue
2. `gitnexus_context({name: "<suspect function>"})` — see all callers, callees, and process participation
3. `READ gitnexus://repo/sygnif/process/{processName}` — trace the full execution flow step by step
4. For regressions: `gitnexus_detect_changes({scope: "compare", base_ref: "main"})` — see what your branch changed

## When Refactoring

- **Renaming**: MUST use `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` first. Review the preview — graph edits are safe, text_search edits need manual review. Then run with `dry_run: false`.
- **Extracting/Splitting**: MUST run `gitnexus_context({name: "target"})` to see all incoming/outgoing refs, then `gitnexus_impact({target: "target", direction: "upstream"})` to find all external callers before moving code.
- After any refactor: run `gitnexus_detect_changes({scope: "all"})` to verify only expected files changed.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Tools Quick Reference

| Tool | When to use | Command |
|------|-------------|---------|
| `query` | Find code by concept | `gitnexus_query({query: "auth validation"})` |
| `context` | 360-degree view of one symbol | `gitnexus_context({name: "validateUser"})` |
| `impact` | Blast radius before editing | `gitnexus_impact({target: "X", direction: "upstream"})` |
| `detect_changes` | Pre-commit scope check | `gitnexus_detect_changes({scope: "staged"})` |
| `rename` | Safe multi-file rename | `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` |
| `cypher` | Custom graph queries | `gitnexus_cypher({query: "MATCH ..."})` |

## Impact Risk Levels

| Depth | Meaning | Action |
|-------|---------|--------|
| d=1 | WILL BREAK — direct callers/importers | MUST update these |
| d=2 | LIKELY AFFECTED — indirect deps | Should test |
| d=3 | MAY NEED TESTING — transitive | Test if critical path |

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/sygnif/context` | Codebase overview, check index freshness |
| `gitnexus://repo/sygnif/clusters` | All functional areas |
| `gitnexus://repo/sygnif/processes` | All execution flows |
| `gitnexus://repo/sygnif/process/{name}` | Step-by-step execution trace |

## Self-Check Before Finishing

Before completing any code modification task, verify:
1. `gitnexus_impact` was run for all modified symbols
2. No HIGH/CRITICAL risk warnings were ignored
3. `gitnexus_detect_changes()` confirms changes match expected scope
4. All d=1 (WILL BREAK) dependents were updated

## Keeping the Index Fresh

After committing code changes, the GitNexus index becomes stale. Re-run analyze to update it:

```bash
npx gitnexus analyze
```

If the index previously included embeddings, preserve them by adding `--embeddings`:

```bash
npx gitnexus analyze --embeddings
```

To check whether embeddings exist, inspect `.gitnexus/meta.json` — the `stats.embeddings` field shows the count (0 means no embeddings). **Running analyze without `--embeddings` will delete any previously generated embeddings.**

> Claude Code users: A PostToolUse hook handles this automatically after `git commit` and `git merge`.

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
