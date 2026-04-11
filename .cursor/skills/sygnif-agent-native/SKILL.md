---
name: sygnif-agent-native
description: >-
  Applies agent-native product discipline to Sygnif — parity between human
  surfaces (Telegram, UI, Cursor) and agent capabilities, atomic tools vs
  workflow-shaped APIs, and emergent workflows via prompts plus bounded
  adaptation. Use when designing or reviewing finance_agent features, Cursor
  worker capabilities, overseer/bot integration, strategy adaptation contract,
  or when the user asks for agent parity, MCP/tool design, or
  "agent-native" behavior in this repo.
---

# Sygnif agent-native discipline

## Scope

Sygnif has multiple **human surfaces** (Telegram `finance_agent/bot.py`, Freqtrade/dashboards, Cursor worker) and **agent runtimes** (same bot stack, Cursor Cloud worker). This skill keeps **outcomes** reachable from every surface that should support them.

Canonical repo context (read when relevant, do not duplicate here):

- `SYGNIF_CONTEXT.md` — deployment, strategy, tests
- `.cursor/rules/sygnif-agent-inherit.mdc` — what the Sygnif Agent inherits
- `.cursor/rules/sygnif-linear-workflow.mdc` — coordinator and API order
- `user_data/strategy_adaptation.py` + `user_data/strategy_adaptation.json` — bounded runtime overrides

## Core rules (compressed)

1. **Parity** — If a user can achieve an outcome via Telegram or an operator workflow, the Cursor agent should be able to achieve the **same outcome** via files, shell, and documented HTTP/local APIs — unless explicitly out of scope (e.g. placing live orders without explicit user request).
2. **Granularity** — Prefer **primitives** (read state, call one overseer endpoint, edit one config file) over new **workflow-shaped** Python functions that embed policy the model should judge.
3. **Composability** — New “features” often land as **prompts + docs** plus thin glue; repeated patterns may graduate to a **domain tool** or one bot command — not the other way around by default.
4. **Bounded automation** — Strategy and risk limits stay behind **`strategy_adaptation.json`** rails (`DEFAULTS` / `BOUNDS`); do not bypass with ad-hoc strategy edits for the same intent.

## Capability map (maintain mentally; extend when you add features)

| Outcome | Typical human path | Agent path |
|--------|-------------------|------------|
| Market / TA context | `/ta`, `/market`, etc. | Read `finance_agent/bot.py` semantics; use Bybit/HTTP helpers documented there; optional `:8091` briefing |
| Open plays / overseer | `/plays`, `/overseer` | `GET` overseer HTTP (`OVERSEER_URL`, default `http://127.0.0.1:8090`) — `/plays`, `/overview`, `/trades` |
| Strategy behavior | Config + `SygnifStrategy.py` | Read `user_data/strategies/SygnifStrategy.py`; tune via `strategy_adaptation.json` within bounds |
| Post-trade / analysis | Bot commands, scripts | Same scripts/APIs the bot uses; avoid duplicating logic in a second place |
| **Cursor worker on this instance** (edit **`~/SYGNIF`**, inherit `.cursor/rules`) | Cursor **Agents** UI for the registered worker | **`cursor-agent-worker.service`** from `systemd/cursor-agent-worker.service`: **`--worker-dir /home/ubuntu/SYGNIF`**, **`EnvironmentFile=-/home/ubuntu/SYGNIF/.env`**, management **`http://127.0.0.1:8093/healthz`** — see `.cursor/cursor-agent-config.md` |

When adding a **new** user-facing capability, append a row: *what the user does*, *how the agent does it*.

## Checklist (before shipping a feature)

- [ ] **Parity**: Document how the agent achieves the same outcome as the Telegram command (or justify exclusion).
- [ ] **Single coordinator**: No second parallel LLM “brain” unless the user asked for comparison (see linear workflow rule).
- [ ] **No silent state**: File/config changes are **visible** (git-tracked paths, logged commands, or API-visible).
- [ ] **CRUD / lifecycle**: If you introduce a persistent entity, agents can **read and update** it; deletion/revert path is clear.
- [ ] **Completion**: Long agent tasks should have an explicit **done signal** (user message, written summary file, or checklist) — not only implicit stop.
- [ ] **Rails**: Risk/strategy tuning goes through **adaptation JSON** when a key already exists in the contract.

## Anti-patterns (Sygnif-specific)

- **Worker pointed at the wrong repo** — systemd **`cursor-agent-worker`** must use **`~/SYGNIF`** (not another clone) so rules, strategy, and `CURSOR_*` env stay aligned with Telegram/Docker; reinstall with `sudo cp systemd/cursor-agent-worker.service /etc/systemd/system/` then `daemon-reload` + `restart`.
- **Bot-only glue** — Logic exists only inside a Telegram handler with no HTTP/script path for the worker.
- **Fat “do_everything” tools** — One function that encodes multi-step trading policy; split into inspect → decide → act primitives.
- **Strategy edits for what belongs in adaptation** — Duplicates rails and confuses operators.
- **Inventing overseer/FT state** — Agent must **read** APIs/logs, not assume positions or PnL.

## When to add a reference file

If this skill grows past ~400 lines, add `reference.md` with long checklists or endpoint tables and link it **one level deep** from here.

## Related external pattern

For general agent-native theory (parity, primitives, emergent capability), see the Compound Engineering skill **agent-native-architecture** in the Cursor plugin cache under `compound-engineering/skills/agent-native-architecture/`.
