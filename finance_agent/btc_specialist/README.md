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
| `data/btc_cryptoapis_foundation.json` | Optional [Crypto APIs](https://cryptoapis.io): BTC mainnet last block + market-data asset + BTC/USD ref — *not* Sygnif TA (`cryptoapi_Token` in `.env`) |
| `data/btc_crypto_market_data.json` | Optional: all **README** daily JSONs from [crypto-market-data](https://github.com/ErcinDedeoglu/crypto-market-data) (**CC BY 4.0**) — *not* Sygnif TA |
| `data/crypto_market_data_daily_analysis.md` | Markdown summary of those series (same refresh path) |
| `scripts/pull_btc_context.py` | Refreshes Bybit bundle + optional NewHedge + **full** crypto-market-data pull + `.md` |
| `scripts/run_crypto_market_data_daily.py` | **Lightweight daily-only** pull (same JSON + `.md`); intended for **cron 1×/day** |
| `scripts/refresh_btc_dashboard_json.py` | Regenerates **`btc_specialist_dashboard.json`** (finance-agent KB + Cursor LLM when enabled) |
| `PROMPT.md` | System prompt stub for a dedicated sub-agent |
| **`../../scripts/train_btc_5m_direction.py`** (repo root) | **Research-only:** next **5m** bar **direction** model — Bybit spot OHLCV + same indicator features as **`scripts/train_ml_ensemble.py`**. Saves to `user_data/ml_models/`; does **not** replace `/btc` or live strategy. |

## Refresh data

From repo root (`SYGNIF`):

```bash
python3 finance_agent/btc_specialist/scripts/pull_btc_context.py
# or on-chain/derivatives only (README datasets, ~31 files):
python3 finance_agent/btc_specialist/scripts/run_crypto_market_data_daily.py
```

**Recommended (daily README pull + dashboard LLM):** `scripts/cron_finance_agent_btc_context.sh` — runs `run_crypto_market_data_daily.py` then `refresh_btc_dashboard_json.py`. Log: `user_data/logs/finance_agent_btc_context.log`. See `INSTANCE_SETUP.md` §7b.

**README-only (no dashboard JSON refresh):**  
`0 * * * * [ "$(TZ=Europe/Berlin date +\%H)" = "00" ] && CRYPTO_MARKET_DATA_RUN_SCRIPT=$HOME/finance_agent/btc_specialist/scripts/run_crypto_market_data_daily.py $HOME/SYGNIF/scripts/cron_crypto_market_data_daily.sh`  
(Adjust `TZ=` / paths; see `scripts/cron_crypto_market_data_daily.sh`.)

Requires `requests` + `pandas` + `numpy` (same stack as `finance_agent/bot.py`). No API keys for public Bybit market endpoints. Optional: `cryptoapi_Token` (Crypto APIs) for `btc_cryptoapis_foundation.json`; `NEWHEDGE_API_KEY` for `btc_newhedge_altcoins_correlation.json`.

## Telegram

- **`/btc`** — same output base as **`/ta BTC`**, plus manifest footer; optional NewHedge line when `NEWHEDGE_API_KEY` is set (`finance_agent/bot.py`).
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
