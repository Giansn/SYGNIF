# Prediction engine, briefing network, bounded self-learning, RAM

**Status:** design / backlog (folder `letscrash/`).  
**Audience:** implementers touching `prediction_agent/`, `finance_agent` HTTP, overseer, and optional retrain loops.

---

## 1. Goals

1. Keep **research-grade** prediction (`prediction_agent/`, `btc_predict_runner.py`) **separate** from live Freqtrade execution unless explicitly integrated.
2. Expose **stable briefing surfaces** on a **single HTTP port** (default **8091**) for overseer, LLM consumers, and optional prediction metadata.
3. Add **bounded self-learning**: horizon checks + optional periodic retrains + adaptation JSON — without unbounded RAM growth or silent live risk changes.

---

## 2. Network and ports (briefing “pipeline”)

| Hop | Component | Bind | Consumers |
|-----|-----------|------|-----------|
| A | **finance-agent** HTTP (`http_main.py` → `bot.start_finance_agent_http_server`) | `FINANCE_AGENT_HTTP_HOST`:`FINANCE_AGENT_HTTP_PORT` (default **127.0.0.1:8091** host; **0.0.0.0:8091** in Docker per compose) | Local overseer, trade-overseer env `FINANCE_AGENT_BRIEFING_URL`, Cursor agent probes |
| B | **`GET /briefing`** | Same listener | Compact pipe text (`_briefing` → `briefing_lines_plain`, char budget ~900); Telegram `/finance-agent briefing` mirrors contract with optional appendices |
| C | **`/sygnif/sentiment`** | Same | Freqtrade containers (`SYGNIF_SENTIMENT_HTTP_URL`) |
| D | **Overseer commentary** | `POST`/`GET` paths on **8091** (see `bot.py`) | trade-overseer `OVERSEER_AGENT_URL` |
| E | **Optional `/training`** | Same (orthogonal / Jupyter discovery) | Not prediction-core; keep isolated to avoid RAM spikes alongside ML fit |

**Design rule:** treat **8091** as one **multi-route** service; avoid spawning a second Python HTTP server for the same logical “briefing + sentiment” surface unless load isolation is required.

**Docker:** `docker-compose.yml` maps `127.0.0.1:8091:8091` for `finance-agent`. In-container DNS name **`finance-agent:8091`** for other services on `sygnif_backend`.

---

## 3. Prediction data plane (offline today)

| Artifact | Producer | Consumer (today) |
|----------|----------|-------------------|
| `finance_agent/btc_specialist/data/btc_1h_ohlcv.json`, `btc_daily_90d.json` | **`research/nautilus_lab/bybit_nautilus_spot_btc_training_feed.py`** (Docker **`nautilus-research`**, merge **`docker-compose.btc-nautilus-research.yml`**) — Nautilus **Bybit** adapter; **also** `pull_btc_context.py` / cron if you still run it | `btc_predict_runner.py`, **`training_pipeline/channel_training.py`** |
| `finance_agent/btc_specialist/data/nautilus_spot_btc_market_bundle.json` | same Nautilus feed | `channel_training.py` inflow report, humans / future features |
| `prediction_agent/btc_prediction_output.json` | `btc_predict_runner.py` | Dashboards, manual review, optional future briefing line |
| `~/.local/share/sygnif-agent/predictions/BTCUSDT_latest.json` | `prediction_horizon_check.py save` | `prediction_horizon_check.py check`, dashboard snapshot path in `dashboard_server.py` |

**Gap (optional future work):** add a **single optional line** (or JSON block) in `/briefing` sourced from `btc_prediction_output.json` **only if** file is fresh (e.g. `manifest`-compatible timestamp) and under a **strict char budget** — so overseer LLM context stays small.

---

## 4. “Self-learning” — safe layers (stack from low to high risk)

| Layer | Mechanism | Blast radius | Automation |
|-------|-----------|--------------|------------|
| L0 | `scripts/prediction_horizon_check.py` **save/check** | None on trading | Manual or cron |
| L1 | Retrain **`btc_predict_runner.py`** on schedule (systemd timer already sketched in repo) | Disk + CPU + **RAM spike during fit** | Timer; log metrics only |
| L2 | `user_data/strategy_adaptation.json` overrides (clamped) | Live behaviour | Human-in-loop or strict policy bot |
| L3 | Wire model output into **entries** in `SygnifStrategy.py` | **High** — needs tests + slot caps | Explicit product decision only |

**Never** imply L1–L2 are “alpha guaranteed”; document regime shift and leakage in any user-facing text.

---

## 5. RAM and CPU guardrails

1. **One heavy fit at a time:** do not run `btc_predict_runner`, Hydra `cryptopredictions/train.py`, and `ann_text_project` training concurrently on the same instance tier.
2. **Bound arrays:** keep rolling windows in runner CLI (`--window`, `--timeframe`) aligned with JSON size; reject absurd limits in wrapper scripts if added.
3. **Process model:** prefer **subprocess** or single worker for scheduled predict — avoids duplicate sklearn/xgboost copies in the Telegram bot process.
4. **HTTP server:** `ThreadingHTTPServer` patterns (dashboard) and bot HTTP handlers — cap body sizes and avoid loading full OHLC history into briefing responses (already pipe-oriented).
5. **Docker:** set **memory limits** in compose for experimental services before enabling continuous learning loops.

---

## 6. Suggested implementation phases

**Phase A — Observability (no behaviour change)**  
- Log lines: last predict run time, row counts, model versions → structured log or sidecar JSON next to `btc_prediction_output.json`.

**Phase B — Briefing hook (read-only)**  
- If `btc_prediction_output.json` mtime < N hours, append ≤1 line to briefing plain text; feature-flag env `SYGNIF_BRIEFING_INCLUDE_BTC_PREDICT=1`.

**Phase C — Closed-loop learning (still no live trades)**  
- Cron: `prediction_horizon_check.py check` → append CSV/JSON outcomes; weekly retrain script with fixed seeds and report-only diff.

**Phase D — Strategy integration (optional, high scrutiny)**  
- New tag or gate in strategy behind `dry_run` + explicit env; full `pytest tests/` and GitNexus impact on `SygnifStrategy` symbols.

---

## 7. Files to touch when implementing

- `prediction_agent/btc_predict_runner.py` — metrics, CLI, memory-friendly data load.
- `finance_agent/bot.py` — `_briefing`, HTTP routes, env flags.
- `docker-compose.yml` — env pass-through, depends_on, resource limits.
- `trade_overseer/` — only if overseer should parse new JSON fields.
- `.cursor/agents/prediction-agent.md` + `.cursor/rules/ruleprediction-agent.mdc` (canonical; former `ruler-prediction-agent.mdc`) — keep docs aligned with actual wiring.

---

## 8. Open questions

1. Should prediction snippets in `/briefing` be **Markdown** or **plain** to match overseer token budget?
2. Single **canonical** path for horizon snapshots under Docker (volume mount vs `~/.local/share/...` in container)?
3. Rate limit for retrain (max 1× per day?) to protect spot trader CPU colocation.

---

*End of plan — iterate in PRs; keep `letscrash/` as the scratchpad until phases land in tree.*
