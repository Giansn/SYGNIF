---
name: create-btc-agent
description: >-
  Scaffold or extend BTC-related Cursor artifacts in SYGNIF: new agent/skill
  files, registry rows, and worker inherit rules — following repo conventions.
  Does not replace btc-specialist for analysis; use that skill/agent for TA and
  JSON bundle work.
---

# Create BTC-related Cursor artifacts (SYGNIF)

## Purpose

| Use this skill (`create-btc-agent`) | Use **`btc-specialist`** instead |
|-------------------------------------|-----------------------------------|
| Checklists for **new** `.cursor/agents/*.md`, `.cursor/skills/*/SKILL.md`, and registry updates | **Bitcoin analysis**: pulls, `manifest.json`, Bybit, TA semantics, research scripts |

## Decision tree

1. **User wants BTC market / TA / data analysis** → Attach or delegate **`.cursor/agents/btc-specialist.md`** (or **`.cursor/skills/btc-specialist/SKILL.md`**).
2. **User wants a new Cursor persona or skill for BTC-adjacent workflows** → Follow this checklist; do **not** duplicate the full `btc-specialist` tool matrix unless the scope is genuinely new.

## Conventions (mirror existing)

- **Kebab-case** for skill folder and agent basename: `btc-specialist`, `finance-agent`, `prediction-agent`.
- **Twin pattern** (agent + skill): same `name` in frontmatter; skill links **Delegated agent:** → `.cursor/agents/<name>.md`.
- **Agent-only** (e.g. `prediction-agent`): no matching skill folder — keep agent doc self-contained.
- **Frontmatter:** `name:` matches folder/file stem; `description: >-` folded YAML; no secrets.
- **Registries:** extend **`AGENTS.md`** (table *Cursor — Sygnif finance / BTC skills & agents*) and **`.cursor/rules/sygnif-agent-inherit.mdc`** (Cursor SKILL files table) whenever you add a **canonical** skill the worker should know about.

## Checklist: new skill under `.cursor/skills/<name>/`

- [ ] Create **`SKILL.md`** with YAML frontmatter (`name`, `description: >-`).
- [ ] Scope table: what this skill covers vs **finance-agent** / **btc-specialist**.
- [ ] Link to delegated agent **if** you add `.cursor/agents/<name>.md`.
- [ ] Add row to **`AGENTS.md`** table (Kind: Skill).
- [ ] Add row to **`sygnif-agent-inherit.mdc`** skill table (if worker should inherit it).

## Checklist: new agent under `.cursor/agents/<name>.md`

- [ ] YAML frontmatter + sections: role, inputs, outputs, boundaries, escalation (see **`.cursor/agents/prediction-agent.md`** for a lean template).
- [ ] For **BTC analysis** agents: reference **`finance_agent/btc_specialist/`**, **`pull_btc_context.py`**, **`manifest.json`**, spot **`BTCUSDT`** vs perps naming — align with **`.cursor/agents/btc-specialist.md`**; do not invent a second Telegram surface (**`/btc`** stays in **`finance_agent/bot.py`**).
- [ ] If paired with a skill: add **Delegated agent** line in the skill pointing here; add **Agent** row in **`AGENTS.md`**.

## Anti-patterns

- Adding a **second** full BTC analyst parallel to **`btc-specialist`** without a clear scope split.
- Duplicating **Telegram** command logic outside **`finance_agent/bot.py`** for Sygnif parity.
- Documenting skills under removed **`~/.claude/skills/finance-agent/`** — canonical skills live under **`.cursor/skills/`** in this repo.

## Validation (before merge)

- Paths exist; cross-links **skill ↔ agent** match filenames.
- **`AGENTS.md`** and **`sygnif-agent-inherit.mdc`** stay in sync for any new canonical skill.
- No contradiction with **`finance_agent/btc_specialist/README.md`** and **`btc-specialist`** agent scope.

## Related (read-only reference)

| File | Role |
|------|------|
| `.cursor/rules/btc-prediction.mdc` | Cursor rule for **BTC ML runner / channel JSON / briefing appendix** (tight globs; aliases `/btc-prediction`, `/create-btc-prediction`) |
| `.cursor/skills/btc-specialist/SKILL.md` | Reference layout for project skills |
| `.cursor/agents/btc-specialist.md` | BTC-only delegated persona |
| `.cursor/skills/finance-agent/SKILL.md` | Router skill + Telegram parity pointer |
