---
name: btc-specialist
description: >-
  Sygnif BTC-only toolkit: Bybit spot BTCUSDT, btc_specialist JSON bundle
  (pull_btc_context.py), Sygnif TA/tags (bot.py + strategies), NewHedge,
  correlation docs, btc_trend_regime + ML regime hooks. Not Telegram — use
  finance-agent for /btc parity and multi-asset work.
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
| **`finance_agent/crypto_market_data.py`** | All README `data/daily` JSONs (**`ALL_README_DAILY_PATHS`**); **`run_crypto_market_data_daily.py`** (cron) or **`pull_btc_context.py`** → `btc_crypto_market_data.json` + **`crypto_market_data_daily_analysis.md`**; **`/finance-agent crypto-daily`**; not Sygnif TA / not Bybit OHLC |
| **`docs/correlation_research_evidence.md`** | ORB / NewHedge / methodology references |
| **`scripts/market_open_context_report.py`** | UTC session + Bybit BTC/ETH snapshot (+ optional NewHedge probe) |
| **`scripts/train_btc_5m_direction.py`** | **Research-only:** next **5m bar** direction (Bybit 5m + `train_ml_ensemble` features). Optional **`--regime-filter`** = train only when **`btc_trend_regime`** is true. **Not** live Freqtrade tags. |
| **`scripts/train_ml_ensemble.py`** | XGBoost signal experiment; merges 1h/4h for **`btc_trend_regime`** column. **`--btc-trend-regime-only`** = ablation on trend bars. Requires `xgboost` + `sklearn` (see repo `.venv` or pip). |
| **`user_data/strategies/btc_trend_regime.py`** | Rule-based trend-long definition (1h/4h RSI, 1h EMA200, 5m ADX); used when **`SYGNIF_PROFILE=btc_trend`** — **not** the same as `/btc` Telegram output. |
| **`docs/btc_trend_backtest_checklist.md`** | How to backtest / validate the `btc_trend` profile. |

## Bybit v5 (spot `BTCUSDT`)

- Tickers: `GET https://api.bybit.com/v5/market/tickers?category=spot&symbol=BTCUSDT`
- Klines: `…/v5/market/kline?category=spot&symbol=BTCUSDT&interval=60|D&limit=…`

## Data contract

- **`manifest.json`**: last pull time (UTC); not a live quote.
- **`btc_sygnif_ta_snapshot.json`**: optional; built when `bot` imports during pull — re-run pull after TA/bot changes.
- **`btc_newhedge_altcoins_correlation.json`**: optional third-party; never label as Sygnif score or Bybit OHLC.
- **`btc_crypto_market_data.json`**: optional full README daily JSON bundle; **CC BY 4.0**; daily granularity only.
- **`crypto_market_data_daily_analysis.md`**: optional markdown pass over all README daily series; refresh via **`run_crypto_market_data_daily.py`** (cron) or **`pull_btc_context.py`**.

## TA / signal semantics (BTC)

Align narratives with **`detect_signals`** in `finance_agent/bot.py` (not generic TradingView defaults). Band gist: strong long zone ≈ TA ≥ 65 (+ volume gates in code); strong short ≈ ≤25; mid bands → sentiment / ambiguous paths — exact thresholds in code + live strategy.

## Sub-agent workflow

1. Read `manifest.json` (+ `btc_sygnif_ta_snapshot.json` if present). For on-chain/derivatives context: prefer **`crypto_market_data_daily_analysis.md`**, else **`btc_crypto_market_data.json`**.
2. If stale or user wants live: Bybit ticker/klines or run **`pull_btc_context.py`** from repo root.
3. For **ML / regime research** (5m noise — research only): **`scripts/train_btc_5m_direction.py`**, **`scripts/train_ml_ensemble.py`** with optional **`--btc-trend-regime-only`**; rule-based regime source: **`btc_trend_regime.py`** (distinct from Telegram **`/btc`**).
4. For “what would Telegram show?” or multi-coin → attach **finance-agent**, not this skill alone.

## Spot vs futures (naming)

- **Default narrative reference:** Bybit **spot** **`BTCUSDT`** (this skill, `pull_btc_context`, public tickers above).
- **Live Freqtrade:** may use **`BTC/USDT:USDT`** (perps) — strategy and `SYGNIF_PROFILE=btc_trend` still key off **BTC** pair; cite the pair the user’s config uses when it matters for execution.

## Deeper TA math

Long-form patterns / indicators: `finance_agent/AI Upload/technical-analyzer/SKILL.md` (Sygnif execution data remains **Bybit CEX**).
