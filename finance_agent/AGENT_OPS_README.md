# Finance Agent Ops (in-repo)

This folder centralizes the Cursor Cloud finance-agent operating assets directly in `finance_agent`.

## Included

- `auto_improvement_workflow.md`: End-to-end **auto improvement** loop (agents, GitNexus nodes, approval gates, mermaid)
- `cloud-runbook.md`: Cloud system prompt + JSON output contract
- `futures-agent-prompt.md`: Futures analysis prompt with BTC dependency and strategy-tag comparison
- `spot-agent-prompt.md`: Spot analysis prompt with BTC dependency and strategy-tag comparison
- `strategy-comparison-module.md`: CUR-6 strategy tag comparison policy (`swing_failure`, `claude_swing`, baseline `claude_s0`)
- `futures-shorts-module.md`: Dedicated short-side futures decision and squeeze-risk framework
- `mode_router.py`: Task router for `futures_long`, `futures_short`, and `spot` modes

## Intended use

- Keep the runtime in analysis-only mode by default.
- Use strict JSON outputs for automation and auditability.
- Route overseer commentary via `OVERSEER_AGENT_URL` (configured in `.env`).
- Use `SYGNIF_HEDGE_BOT_TOKEN` for dedicated overseer Telegram delivery.
- Use labels (`futures-short`, `futures-long`, `spot`) for deterministic mode routing.

## Workflow loop (Telegram / Cursor / Overseer)

- **Single LLM entry:** Slash-Befehle laufen über `agent_slash_dispatch` → Cursor Cloud (`llm_analyze`); Ausnahme: **`/sygnif state`**, **`/sygnif pending`**, **`/sygnif approve <id>`** sind deterministisch (kein LLM).
- **Cycle bundle:** `/sygnif` oder `/cursor` lädt Worker-Health, Overseer `/overview` + `/trades`, `strategy_adaptation.json` (über `SYGNIF_REPO`), dann Signals / Tendency / Macro — Kontext für die Antwort.
- **Background observer:** `scripts/sygnif_advisor_observer.py` schreibt `user_data/advisor_state.json` (+ optional Heuristiken → `advisor_pending.json`). Der Telegram-Bot startet dazu einen Thread, wenn `ADVISOR_BG_INTERVAL_SEC` > 0 (Default **3600**). Freigabe: `/sygnif approve <id>` merged validierte Keys in `strategy_adaptation.json`.
- **Env:** `SYGNIF_REPO` (default `/home/ubuntu/xrp_claude_bot`), `OVERSEER_URL`, `CURSOR_WORKER_HEALTH_URL`, optional `ADVISOR_BG_TELEGRAM=1` + `ADVISOR_TELEGRAM_EVERY_N`, `ADVISOR_HEURISTICS=0` zum Abschalten der Vorschläge.
- Siehe auch `.cursor/cursor-agent-config.md`.
