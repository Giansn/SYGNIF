# Finance Agent Ops (in-repo)

This folder centralizes the Cursor Cloud finance-agent operating assets directly in `finance_agent`.

## Included

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

- **Single LLM entry:** Alle Slash-Befehle laufen über `agent_slash_dispatch` → Cursor Cloud (`llm_analyze`); Freitext nutzt denselben LLM mit Session-Verlauf.
- **Cycle bundle:** `/sygnif` oder `/cursor` lädt Worker-Health, Overseer `/overview` + `/trades`, `user_data/strategy_adaptation.json` (Analytics), dann Signals / Tendency / Macro — ein Kontextstring für die Antwort.
- **Env:** `OVERSEER_URL` (default `http://127.0.0.1:8090`), `CURSOR_WORKER_HEALTH_URL` (default `http://127.0.0.1:8093/healthz`).
- Siehe auch `.cursor/cursor-agent-config.md`.
