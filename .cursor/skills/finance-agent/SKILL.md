---
name: finance-agent
description: >-
  Sygnif unified domain (router): strategy + live markets + Telegram bot. Full
  knowledge base lives in `.cursor/agents/finance-agent.md` — same content as
  Cursor subagent `finance-agent` and Telegram `/finance-agent` LLM KB.
  Triggers: crypto, TA, signals, trades, /ta, /btc, /finance-agent, NFI, entry tags,
  sf_*, orb_*, GitNexus. For BTC-only offline bundle use btc-specialist skill.
allowed-tools:
  - Agent
  - Task
  - Bash
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - WebSearch
  - WebFetch
---

# Finance-agent (router)

**Single source of truth (edit this for behavior + docs):** [`.cursor/agents/finance-agent.md`](../agents/finance-agent.md)

That file is the **fused** skill + Cursor **subagent** prompt and is loaded by **`finance_agent/bot.py`** (`load_finance_agent_kb`) for Telegram **`/finance-agent`** LLM replies so Cursor and Telegram stay aligned.

**Telegram implementation:** `finance_agent/bot.py` only. **BTC-only tools:** `finance_agent/btc_specialist/` + **btc-specialist** skill. **Daily BTC on-chain/derivatives (CC BY 4.0):** `finance_agent/crypto_market_data.py` — all README daily JSONs; `run_crypto_market_data_daily.py` (1×/day cron) or `pull_btc_context.py` → `crypto_market_data_daily_analysis.md` + bundle; `/finance-agent crypto-daily`; compact pipe in `GET /briefing`.
