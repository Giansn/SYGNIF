# Finance Agent Ops (in-repo)

This folder centralizes the Cursor Cloud finance-agent operating assets directly in `finance_agent`.

## Included

- `briefing.md`: **Pipe briefing** contract (HTTP `/briefing`, Telegram) + **neural evaluation nodes** `N1–N8` / `B1–B7`
- `auto_improvement_workflow.md`: End-to-end **auto improvement** loop (agents, GitNexus nodes, approval gates, mermaid)
- `cloud-runbook.md`: Cloud system prompt + JSON output contract
- `futures-agent-prompt.md`: Futures analysis prompt with BTC dependency and strategy-tag comparison
- `spot-agent-prompt.md`: Spot analysis prompt with BTC dependency and strategy-tag comparison
- `strategy-comparison-module.md`: CUR-6 strategy tag comparison policy (`swing_failure`, `sygnif_swing`, baseline `sygnif_s0`)
- `futures-shorts-module.md`: Dedicated short-side futures decision and squeeze-risk framework
- `mode_router.py`: Task router for `futures_long`, `futures_short`, and `spot` modes

## Intended use

- Keep the runtime in analysis-only mode by default.
- Use strict JSON outputs for automation and auditability.
- Route overseer commentary via `OVERSEER_AGENT_URL` (configured in `.env`).
- Use `SYGNIF_HEDGE_BOT_TOKEN` for dedicated overseer Telegram delivery.
- Use labels (`futures-short`, `futures-long`, `spot`) for deterministic mode routing.

## Workflow loop (Telegram / Cursor / Overseer)

- **Single LLM entry:** Slash commands go through `agent_slash_dispatch` → Cursor Cloud (`llm_analyze`); exception: **`/sygnif state`**, **`/sygnif pending`**, **`/sygnif approve <id>`** are deterministic (no LLM).
- **Cycle bundle:** `/sygnif` or `/cursor` loads worker health, Overseer `/overview` + `/trades`, `strategy_adaptation.json` (via `SYGNIF_REPO`), then Signals / Tendency / Macro — context for the reply.
- **Background observer:** `scripts/sygnif_advisor_observer.py` writes `user_data/advisor_state.json` (+ optional heuristics → `advisor_pending.json`). The Telegram bot starts a thread when `ADVISOR_BG_INTERVAL_SEC` > 0 (default **3600**). Approval: `/sygnif approve <id>` merges validated keys into `strategy_adaptation.json`.
- **Env:** `SYGNIF_REPO` (default `$HOME/SYGNIF`; set explicitly if the repo still lives under a legacy path), `OVERSEER_URL`, `CURSOR_WORKER_HEALTH_URL`, optional `ADVISOR_BG_TELEGRAM=1` + `ADVISOR_TELEGRAM_EVERY_N`, `ADVISOR_HEURISTICS=0` to disable suggestions.
- See also `.cursor/cursor-agent-config.md`.
