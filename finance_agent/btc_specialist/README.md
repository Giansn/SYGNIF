# BTC specialist agent

Bitcoin-only persona for Sygnif: same TA stack as `finance_agent/bot.py`, Bybit spot `BTCUSDT`, JSON snapshots for **offline** prompts and Cursor sub-agents.

## Layout

| Path | Purpose |
|------|---------|
| `data/manifest.json` | UTC timestamp + list of pulled files |
| `data/bybit_btc_ticker.json` | Latest 24h ticker fields for `BTCUSDT` |
| `data/btc_1h_ohlcv.json` | Last 200 × 1h OHLCV |
| `data/btc_daily_90d.json` | Last 90 × 1d OHLCV |
| `data/btc_sygnif_ta_snapshot.json` | Sygnif **TA score**, signal names, key indicators (written when `pull_btc_context.py` can import `bot`) |
| `data/btc_fdn_fundamentals.json` | Optional **FinancialData.net** `crypto-information` slim fields (market cap, supplies) when `FINANCIALDATA_API_KEY` is set — *not* Sygnif TA |
| `data/btc_crypto_market_data.json` | Optional: all **README** daily JSONs from [crypto-market-data](https://github.com/ErcinDedeoglu/crypto-market-data) (**CC BY 4.0**) — *not* Sygnif TA |
| `data/crypto_market_data_daily_analysis.md` | Markdown summary of those series (same refresh path) |
| `scripts/pull_btc_context.py` | Refreshes Bybit bundle + optional FDN/NewHedge + **full** crypto-market-data pull + `.md` |
| `scripts/run_crypto_market_data_daily.py` | **Lightweight daily-only** pull (same JSON + `.md`); intended for **cron 1×/day** |
| `PROMPT.md` | System prompt stub for a dedicated sub-agent |
| **`../../scripts/train_btc_5m_direction.py`** (repo root) | **Research-only:** next **5m** bar **direction** model — Bybit spot OHLCV + same indicator features as **`scripts/train_ml_ensemble.py`**. Saves to `user_data/ml_models/`; does **not** replace `/btc` or live strategy. |

## Refresh data

From repo root (`SYGNIF`):

```bash
python3 finance_agent/btc_specialist/scripts/pull_btc_context.py
# or on-chain/derivatives only (README datasets, ~31 files):
python3 finance_agent/btc_specialist/scripts/run_crypto_market_data_daily.py
```

Cron example (UTC 06:00): `0 6 * * * cd $HOME && python3 finance_agent/btc_specialist/scripts/run_crypto_market_data_daily.py`

Requires `requests` + `pandas` + `numpy` (same stack as `finance_agent/bot.py`). No API keys for public Bybit market endpoints. Optional: `FINANCIALDATA_API_KEY` in `.env` for `btc_fdn_fundamentals.json` (see `finance_agent/fdn_fundamentals.py`).

## Telegram

- **`/btc`** — same output base as **`/ta BTC`**, plus manifest footer; Telegram also appends an optional **FDN** BTC profile block when `FINANCIALDATA_API_KEY` is set (`finance_agent/bot.py`).
- Slash commands still go through the usual agent path when the LLM is enabled; server context includes the full `/ta`-equivalent block.

## Briefing & evaluation nodes

`finance_agent/briefing.md` — shared **briefing line format**, HTTP/Telegram contract, and **neural evaluation nodes** (`B1`–`B7` for BTC, `N1`–`N8` for multi-symbol briefing).

## Cursor skill

`.cursor/skills/btc-specialist/SKILL.md` — attach for **BTC analysis toolkit** (pulls, JSON, Bybit patterns). Telegram commands stay in **finance-agent** (`bot.py`); see `.cursor/skills/finance-agent/SKILL.md`.

## Evaluation notes (design)

| Strength | Limit |
|----------|--------|
| Small JSONs, git-friendly, no LFS | Snapshots lag live price by definition |
| `btc_sygnif_ta_snapshot.json` ties offline files to **Sygnif** scoring | Pull must run in an env where `finance_agent/bot.py` imports (expert modules on `PYTHONPATH`) |
| `/btc` improves **human + agent parity** vs remembering `/ta BTC` | LLM may still rephrase; deterministic users can read JSON directly |
