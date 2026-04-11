

# Project Memory — SYGNIF
> 1949 notes | Score threshold: >40

## Safety — Never Run Destructive Commands

> Dangerous commands are actively monitored.
> Critical/high risk commands trigger error notifications in real-time.

- **NEVER** run `rm -rf`, `del /s`, `rmdir`, `format`, or any command that deletes files/directories without EXPLICIT user approval.
- **NEVER** run `DROP TABLE`, `DELETE FROM`, `TRUNCATE`, or any destructive database operation.
- **NEVER** run `git push --force`, `git reset --hard`, or any command that rewrites history.
- **NEVER** run `npm publish`, `docker rm`, `terraform destroy`, or any irreversible deployment/infrastructure command.
- **NEVER** pipe remote scripts to shell (`curl | bash`, `wget | sh`).
- **ALWAYS** ask the user before running commands that modify system state, install packages, or make network requests.
- When in doubt, **show the command first** and wait for approval.

**Stack:** Python

## 📝 NOTE: 1 uncommitted file(s) in working tree.\n\n## Important Warnings

- **⚠️ GOTCHA: what-changed in tradesv3-futures.sqlite-wal** — - �	�=f�"�\��IUs
- **⚠️ GOTCHA: what-changed in tradesv3-futures.sqlite-wal** — - �	�=f�"�\��IUs
- **⚠️ GOTCHA: what-changed in tradesv3-futures.sqlite-wal** — - �	�=f�"�\��IUs
- **⚠️ GOTCHA: what-changed in tradesv3-futures.sqlite-wal** — - �	�=f�"�\��IUs
- **⚠️ GOTCHA: what-changed in tradesv3-futures.sqlite-wal** — - �	�=f�"�\��IUs
- **⚠️ GOTCHA: what-changed in tradesv3-futures.sqlite-wal** — - �	�=f�"�\��IUs

## Project Standards

- Strengthened types INFO
- Strengthened types INFO
- Strengthened types INFO
- convention in agent-rules.md
- Strengthened types HTTPError
- Strengthened types Scripts
- Use .dockerignore to exclude unnecessary files
- Use multi-stage builds to reduce image size

## Known Fixes

- ❌ - ?�e����trailing_stop_lossclosedMarketStrategy2strong_taFUTURES?�������>�����h�?�i/n���?f��� → ✅ problem-fix in tradesv3-futures.sqlite-wal

## Recent Decisions

- decision in freqtrade.log
- decision in freqtrade-futures.log
- decision in freqtrade.log
- decision in freqtrade-futures.log

## Verified Best Practices

- Agent generates new migration for every change (squash related changes)
- Agent installs packages without checking if already installed

### 📚 Core Framework Rules: [tinybirdco/tinybird-python-sdk-guidelines]
# Tinybird Python SDK Guidelines

Guidance for using the `tinybird-sdk` package to define Tinybird resources in Python.

## When to Apply

- Installing or configuring tinybird-sdk
- Defining datasources, pipes, or endpoints in Python
- Creating Tinybird clients in Python
- Using data ingestion or queries in Python
- Running tinybird dev/build/deploy commands for Python projects
- Migrating from legacy .datasource/.pipe files to Python
- Defining connections (Kafka, S3, GCS)
- Creating materialized views, copy pipes, or sink pipes

## Rule Files

- `rules/getting-started.md`
- `rules/configuration.md`
- `rules/defining-datasources.md`
- `rules/defining-endpoints.md`
- `rules/client.md`
- `rules/low-level-api.md`
- `rules/cli-commands.md`
- `rules/connections.md`
- `rules/materialized-views.md`
- `rules/copy-sink-pipes.md`
- `rules/tokens.md`

## Quick Reference

- Install: `pip install tinybird-sdk`
- Initialize: `tinybird init`
- Dev mode: `tinybird dev` (uses configured `dev_mode`, typically branch)
- Build: `tinybird build` (builds against configured dev target)
- Deploy: `tinybird deploy` (deploys to main/production)
- Preview in CI: `tinybird preview`
- Migrate: `tinybird migrate` (convert .datasource/.pipe files to Python)
- Server-side only; never expose tokens in browsers


### 📚 Core Framework Rules: [czlonkowski/n8n-code-python]
# Python Code Node (Beta)

Expert guidance for writing Python code in n8n Code nodes.

---

## ⚠️ Important: JavaScript First

**Recommendation**: Use **JavaScript for 95% of use cases**. Only use Python when:
- You need specific Python standard library functions
- You're significantly more comfortable with Python syntax
- You're doing data transformations better suited to Python

**Why JavaScript is preferred:**
- Full n8n helper functions ($helpers.httpRequest, etc.)
- Luxon DateTime library for advanced date/time operations
- No external library limitations
- Better n8n documentation and community support

---

## Quick Start



### Essential Rules

1. **Consider JavaScript first** - Use Python only when necessary
2. **Access data**: `_input.all()`, `_input.first()`, or `_input.item`
3. **CRITICAL**: Must return `[{"json": {...}}]` format
4. **CRITICAL**: Webhook data is under `_json["body"]` (not `_json` directly)
5. **CRITICAL LIMITATION**: **No external libraries** (no requests, pandas, numpy)
6. **Standard library only**: json, datetime, re, base64, hashlib, urllib.parse, math, random, statistics

---

## Mode Selection Guide

Same as JavaScript - choose based on your use case:

### Run Once for All Items (Recommended - Default)

**Use this mode for:** 95% of use cases

- **How it works**: Code executes **once** regardless of input count
- **Data access**: `_input.all()` or `_items` array (Native mode)
- **Best for**: Aggregation, filtering, batch processing, transformations
- **Performance**: Faster for multiple items (single execution)



### Run Once for Each Item

**Use this mode for:** Specialized cases only

- **How it works**: Code executes **separately** for each input item
- **Data access**: `_input.item` or `_item` (Native mode)
- **Best for**: Item-specific logic, independent operations, per-item validation
- **Performance**: Slower for large datasets (multiple executions)



---

## Python Modes: Beta vs Native

n8n offers two Python execution modes:

### Python (Beta) - Recommended
- **Use**: `_input`, `_json`, `_node` helper syntax
- **Best for**: Most Python use cases
- **Helpers available**: `_now`, `_today`, `_jmespath()`
- **Import**: `from datetime import datetime`



### Python (Native) (Beta)
- **Use**: `_items`, `_item` variables only
- **No helpers**: No `_input`, `_now`, etc.
- **More limited**: Standard Python only
- **Use when**: Need pure Python without n8n helpers



**Recommendation**: Use **Python (Beta)** for better n8...
(truncated)

- [Python] Use type hints for function arguments and return types
- [Python] Don't use mutable default arguments (def f(items=[]) is a bug)

## Available Tools (ON-DEMAND only)
- `sys_core_01(q)` — Deep search when stuck
- `sys_core_05(query)` — Full-text lookup
> Context above IS your context. Do NOT call sys_core_14() at startup.
