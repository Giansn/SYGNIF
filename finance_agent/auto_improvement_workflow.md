# Auto improvement workflow (Sygnif + agents + GitNexus)

**UTC context:** use timestamps in ISO UTC for any `strategy_adaptation.json` or advisor metadata.

## Goal

A **repeatable loop** that improves *analysis quality* and *bounded strategy tuning* without autonomous exchange orders. Automation may **propose** changes; **humans** (or `/sygnif approve`) apply validated overrides.

## Available “agents” (runtime)

| Agent / service | Role in improvement |
|-----------------|---------------------|
| **Cursor Cloud Agent** + **`cursor-agent-worker.service`** | Code/strategy work in-repo; inherits `.cursor/rules` and finance-agent prompts. |
| **Finance Agent** (`finance_agent/bot.py`, Telegram) | Market/TA/research, `/sygnif` cycle bundle, LLM synthesis; **analysis-only** for trades. |
| **Trade Overseer** (`trade-overseer`, :8090) | Open-trade monitoring, LLM commentary, `/overview` + `/trades` for feedback. |
| **Advisor observer** (`scripts/sygnif_advisor_observer.py`) | Scheduled state + optional **heuristic** pending rows in `advisor_pending.json`. |
| **Weekly cron** (`scripts/weekly_strategy_analysis.py`) | Horizon snapshot + 7d trade stats → `strategy_adaptation_weekly.json` (sidecar) + embedded **`ms3_metrics`** (NT perf + tag families + trading success bundle). |
| **Daily cron** (`scripts/collect_ms3_metrics.py`) | Writes `user_data/market_strategy_3_metrics.json` + JSONL + appends `entry_performance.jsonl` — see `docs/market_strategy_3.md` §9. |
| **6h cron** (`scripts/cron_trading_success.sh`) | Telegram trading success (1d) + strategy path tracker; 7d variants log-only. |

## GitNexus “nodes” (when to use)

Indexed repos (see skill **finance-agent**):

| Repo | Typical queries |
|------|-----------------|
| **Sygnif** (this repo) | `populate_entry_trend`, `confirm_trade_entry`, `custom_exit`, `strategy_adaptation`, slot caps. |
| **NostalgiaForInfinity** | Reference patterns only — read **targeted** sections, not whole file. |

**Practice:** before editing symbols, run **`gitnexus_impact`** / **`gitnexus_detect_changes`** per project rules; use **`gitnexus_query`** for “who calls X?” and **`gitnexus_context`** on hot symbols.

## Core analysis loop (Sygnif Agent)

Aligned with `.cursor/rules/sygnif-predict-workflow.mdc`:

1. **Predict** — scenarios, levels, falsifiers, horizon.
2. **Analyze** — Bybit data, TA score / signals from `finance_agent` where relevant.
3. **Proofread** — numbers, contradictions, language.
4. **Adjust** — bounded changes only:
   - **Horizon:** `scripts/prediction_horizon_check.py` (`save` / `check`).
   - **Strategy:** `user_data/strategy_adaptation.json` overrides validated by `user_data/strategy_adaptation.py` (`BOUNDS`).

## Auto-improvement pipeline (orchestration)

```mermaid
flowchart LR
  subgraph observe [Observe]
    FT[Freqtrade]
    OV[Overseer]
    ADV[Advisor observer]
    WK[Weekly cron]
  end
  subgraph analyze [Analyze]
    FA[Finance Agent /sygnif]
    GN[GitNexus query]
    CUR[Cursor Agent + worker]
  end
  subgraph act [Act bounded]
    PEND[advisor_pending.json]
    APP["/sygnif approve"]
    SA[strategy_adaptation.json]
  end
  FT --> OV
  FT --> ADV
  FT --> WK
  OV --> FA
  ADV --> PEND
  FA --> CUR
  GN --> CUR
  PEND --> APP
  APP --> SA
  CUR --> SA
```

### Triggers

| Trigger | What runs | Output |
|---------|-----------|--------|
| **On a schedule** | `ADVISOR_BG_INTERVAL_SEC` → observer | `advisor_state.json`, optional `advisor_pending.json` |
| **Weekly (Sun 06:00 UTC)** | `weekly_strategy_analysis.py` | `strategy_adaptation_weekly.json` (+ `ms3_metrics`) + Telegram/log |
| **Daily (06:15 UTC)** | `collect_ms3_metrics.py` | `market_strategy_3_metrics.json` + JSONL + entry_perf log |
| **Every 6h (:30 UTC)** | `cron_trading_success.sh` | `trading_success` Telegram + `strategy_paths` + logs |
| **Every 20m** | `scripts/sentiment_health_watch.py` | Log always; **@sygnif_agent_bot** if sentiment/HTTP urgent **or** any `enter_tag` has **5 consecutive losing** closes (`close_profit < 0`) in spot/futures DB (see `.cursor/rules/sygnif-sentiment-layer.mdc`) |
| **On demand** | `/sygnif`, `/finance-agent cycle`, Cursor task | LLM + raw bundle |
| **Before code edits** | GitNexus impact / query | Safer refactors |

### Approval gates (mandatory for “live” strategy changes)

1. **Proposals** land in `advisor_pending.json` or Cursor PR — not directly on the exchange.
2. **Apply** validated overrides via **`/sygnif approve <id>`** or manual edit of `strategy_adaptation.json` (same schema).
3. **Never** grant the finance-agent **autonomous** `force_enter` / `force_exit` — out of scope for this workflow.

## JSON / strict outputs

For Linear / Cloud tasks that must be machine-readable, follow **`cloud-runbook.md`** keys and **`mode_router.py`** modes (`futures_long`, `futures_short`, `spot`).

## Quick checklist (operator)

- [ ] Worker health: `CURSOR_WORKER_HEALTH_URL` (default `:8093/healthz`).
- [ ] Overseer: `OVERSEER_URL` `/overview` responds.
- [ ] `SYGNIF_REPO` points at the repo with `user_data/strategy_adaptation.json`.
- [ ] `.env` for Docker: escape **`$`** in passwords as **`$$`** (Compose interpolation).
- [ ] After bounded tuning: run **`pytest`** for strategy tests when Python changes.

## Related files

- `.cursor/cursor-agent-config.md` — worker + Telegram alignment.
- `AGENT_OPS_README.md` — ops summary.
- `scripts/prediction_horizon_check.py` — horizon discipline.
- `user_data/strategy_adaptation.py` — override rails.
