# SYGNIF Agent (X1)

Local crypto research + trading-analysis agent. Runs on top of the `sygnif` CLI (Gemma-channeler via Ollama on localhost:11434). Adapted from the upstream `finance-agent` skill — all Claude/Cursor language rewritten for SYGNIF.

## Layout

| Path | Purpose |
|------|---------|
| `AGENT.md` | Main spec — signals, regime gating, capabilities, commands |
| `reference/` | Dense implementation-oriented references |
| `reference/mathematical-foundations.md` | Return math, GARCH, Kelly, OHLC vol estimators, microstructure, VaR/CVaR |
| `reference/market-openings.md` | Sessions, ACD/Crabel/Raschke ORB, Dalton day types, CME gaps, Bybit funding, Deribit expiry |
| `reference/swing-failure.md` | SFP taxonomy (Williams, Raschke Turtle, 2B, Wyckoff, ICT, Heavy91), confluence scoring |
| `reference/trading-structure.md` | Swing pivots, BOS/CHOCH, BSL/SSL, OB/FVG/breaker, Market Profile, Wyckoff phases, regime classifier |
| `reference/ta-indicators.md` | Indicator formulas (EMA/RSI/BB/MACD/Aroon/StochRSI/CMF/Williams%R/CCI/ROC/ATR) |
| `agent-prompts/` | One sub-agent prompt per task (market, movers, coin-analyzer, technical-analyzer, etc.) |
| `workflows/comprehensive.md` | Orchestration plan for full research runs |
| `scripts/sygnif-agent` | Runner — pipes a prompt through the local sygnif CLI |
| `config/agent.env` | Runtime config (model, Ollama host, output dir) |
| `outputs/` | Timestamped run artifacts (input.md + response.md per run) |

## Quick start

```bash
source ~/sygnif-agent/config/agent.env
sygnif-agent list                 # list available agent prompts
sygnif-agent market               # top 15 Bybit spot pairs
sygnif-agent technical-analyzer "BTC on 4H"
sygnif-agent coin-analyzer "ETH, focus on macro correlation"
```

Every run writes to `outputs/<UTC-timestamp>-<prompt>/` with `input.md` (full prompt sent) and `response.md` (model output).

## Models

- Default: `gemma-channeler:latest` via Ollama on `127.0.0.1:11434`
- Override: `SYGNIF_AGENT_MODEL=obliteratus-gemma4:latest sygnif-agent market`
- The Ollama model list is `curl -s http://127.0.0.1:11434/api/tags`

## Naming notes

Strategy tag renames (from the upstream skill):
- `claude_s0` → `sygnif_s0` (baseline comparison tag)
- `claude_swing` → `sygnif_swing` (strategy variant)
- "Claude sentiment zone" → "SYGNIF sentiment zone" (TA-score 55-64 / 26-44 range label)

Env variables:
- `CLAUDE_SKILL_DIR` → `SYGNIF_AGENT_DIR`

## Related on X1

- `~/.local/bin/sygnif` — raw chat CLI (upstream of this agent)
- `~/sygnif-swarm/` — BTC prediction + swarm bundle (separate live-trading project)

## Upstream live instance

- SYGNIF EC2 (eu-central-1, `ubuntu@3.122.252.186`) — production trading stack. Reach via `ssh ubuntu@3.122.252.186` through Tailscale + added pubkey.
