# Finance Agent Ops (in-repo)

This folder centralizes the Cursor Cloud finance-agent operating assets directly in `finance_agent`.

## Included

- `cloud-runbook.md`: Cloud system prompt + JSON output contract
- `futures-agent-prompt.md`: Futures analysis prompt with BTC dependency and strategy-tag comparison
- `spot-agent-prompt.md`: Spot analysis prompt with BTC dependency and strategy-tag comparison
- `strategy-comparison-module.md`: CUR-6 strategy tag comparison policy (`swing_failure`, `claude_swing`, baseline `claude_s0`)

## Intended use

- Keep the runtime in analysis-only mode by default.
- Use strict JSON outputs for automation and auditability.
- Route overseer commentary via `OVERSEER_AGENT_URL` (configured in `.env`).
- Use `SYGNIF_HEDGE_BOT_TOKEN` for dedicated overseer Telegram delivery.
