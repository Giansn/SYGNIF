---
name: btc-specialist
description: >-
  Sygnif BTC-only analysis toolkit: Bybit spot BTCUSDT, offline JSON bundle
  (pull_btc_context.py), Sygnif TA/signal semantics via bot.py + strategy code,
  optional FDN/NewHedge/correlation evidence. Not the Telegram surface — use
  finance-agent skill for bot commands and multi-asset work.
---

> **Delegated agent (preferred for BTC-only runs):** **`.cursor/agents/btc-specialist.md`**. This **SKILL.md** is the attachable reference pack (same scope).

# BTC analysis toolkit

## Scope

| This skill (`btc-specialist`) | **finance-agent** skill |
|------------------------------|-------------------------|
| Bitcoin-only: pulls, JSON snapshots, Bybit patterns, reading TA/tag semantics for **BTCUSDT** | Multi-asset markets, strategy engineering, GitNexus, **Telegram** command parity (`/market`, `/btc`, `/finance-agent …`, overseer, plays) |

**Telegram** lives only in `finance_agent/bot.py` (deployed as the finance-agent bot). There is no separate “BTC Telegram bot”; `/btc` is documented under **finance-agent**.

## Analysis tools (use these)

| Tool | Purpose |
|------|---------|
| **`finance_agent/btc_specialist/scripts/pull_btc_context.py`** | Refresh offline bundle: ticker, 1h + daily OHLCV, `manifest.json`, optional `btc_sygnif_ta_snapshot.json` |
| **`finance_agent/btc_specialist/data/*.json`** | Stale-safe context for agents; check `manifest.json` UTC |
| **`finance_agent/bot.py`** | Ground truth for **Sygnif** indicators, `calc_ta_score`, `detect_signals` (import or read; same stack as pull when env is complete) |
| **`user_data/strategies/SygnifStrategy.py`** / **`MarketStrategy2.py`** | How BTC entries/exits behave live (tags, protections, ORB on BTC) |
| **`user_data/strategies/market_sessions_orb.py`** | Session ORB columns; **`orb_long`** is **BTC/ETH** — relevant when the question is BTC breakout context |
| **`finance_agent/newhedge_client.py`** | Optional BTC–alts correlation metric (`NEWHEDGE_API_KEY`); not Sygnif TA |
| **`finance_agent/fdn_fundamentals.py`** | Optional FDN metadata (`FINANCIALDATA_API_KEY`); not Bybit price |
| **`docs/correlation_research_evidence.md`** | ORB / NewHedge / methodology references |
| **`scripts/market_open_context_report.py`** | UTC session + Bybit BTC/ETH snapshot (+ optional NewHedge probe) |

## Bybit v5 (spot `BTCUSDT`)

- Tickers: `GET https://api.bybit.com/v5/market/tickers?category=spot&symbol=BTCUSDT`
- Klines: `…/v5/market/kline?category=spot&symbol=BTCUSDT&interval=60|D&limit=…`

## Data contract

- **`manifest.json`**: last pull time (UTC); not a live quote.
- **`btc_sygnif_ta_snapshot.json`**: optional; built when `bot` imports during pull — re-run pull after TA/bot changes.
- **`btc_fdn_fundamentals.json`** / **`btc_newhedge_altcoins_correlation.json`**: optional third-party; never label as Sygnif score or Bybit OHLC.

## TA / signal semantics (BTC)

Align narratives with **`detect_signals`** in `finance_agent/bot.py` (not generic TradingView defaults). Band gist: strong long zone ≈ TA ≥ 65 (+ volume gates in code); strong short ≈ ≤25; mid bands → sentiment / ambiguous paths — exact thresholds in code + live strategy.

## Sub-agent workflow

1. Read `manifest.json` (+ `btc_sygnif_ta_snapshot.json` if present).
2. If stale or user wants live: Bybit ticker/klines or run **`pull_btc_context.py`** from repo root.
3. For “what would Telegram show?” or multi-coin → attach **finance-agent**, not this skill alone.

## Deeper TA math

Long-form patterns / indicators: `finance_agent/AI Upload/technical-analyzer/SKILL.md` (Sygnif execution data remains **Bybit CEX**).
