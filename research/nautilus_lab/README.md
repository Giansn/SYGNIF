# Nautilus research lab (Docker)

## Run (with live `finance-agent` on same compose network)

```bash
cd ~/SYGNIF
export COMPOSE_FILE=docker-compose.yml:docker-compose.nautilus-research.yml
docker compose build nautilus-research
docker compose up -d finance-agent   # if not already up
docker compose up -d nautilus-research
```

## Exec examples

```bash
docker exec -it nautilus-research python3 /lab/workspace/nautilus_smoke.py
docker exec -it nautilus-research python3 /lab/workspace/btc_regime_assessment.py
docker exec -it nautilus-research python3 /lab/workspace/btc_dump_run_framework.py
```

`FINANCE_AGENT_BASE_URL` defaults to `http://finance-agent:8091` inside the stack.

### Bybit via Nautilus (not CCXT) — spot **BTC/USDT** training + regime feed

- **Canonical script:** **`bybit_nautilus_spot_btc_training_feed.py`** — Nautilus **`BybitHttpClient`**: `request_instruments`, `request_bars` (1h + 1d), `request_tickers`, `request_trades`, `request_orderbook_snapshot`, `request_instrument_statuses`; optional **`request_fee_rates`** if `BYBIT_*` / `BYBIT_DEMO_*` keys exist in the container env.
- **Outputs** (under `NAUTILUS_BTC_OHLCV_DIR`, default `/lab/btc_specialist_data`): **`btc_1h_ohlcv.json`**, **`btc_daily_90d.json`** (training + `btc_predict_runner`), **`btc_1h_ohlcv_nautilus_bybit.json`** (regime), **`nautilus_spot_btc_market_bundle.json`** (market snapshot JSON).
- **Loop (bundled):** Service **`nautilus-research`** in **`docker-compose.yml`** (profile **`btc-nautilus`**) runs **`run_nautilus_bundled.sh`** — **sink** + **sidecar** in one container. Intervals **`NAUTILUS_BYBIT_POLL_SEC`** / **`NAUTILUS_STRATEGY_POLL_SEC`** (default 300s).
- **Legacy one-liner:** `bybit_nautilus_btc_ohlcv_sink.py` (1h only) remains for manual use.
- **Env:** `NAUTILUS_BYBIT_DEMO` / `NAUTILUS_BYBIT_TESTNET`, `NAUTILUS_TRAINING_BAR_LIMIT_1H` (default 1600), `NAUTILUS_TRAINING_BAR_LIMIT_1D` (120), `NAUTILUS_TRAINING_TRADES_LIMIT`, `NAUTILUS_TRAINING_BOOK_LEVELS`, `NAUTILUS_TRAINING_BOOK_DELTA_CAP`.
- **Consumers:** `training_pipeline/channel_training.py`, `prediction_agent/btc_predict_runner.py`, `btc_regime_assessment.py`.

### Strategy sidecar (same container as sink)

- **Script:** `nautilus_sidecar_strategy.py` — reads sink `btc_1h_ohlcv.json`, writes **`nautilus_strategy_signal.json`**. Started by **`run_nautilus_bundled.sh`** alongside the Bybit sink (no second container).
- **Optional second container:** merge **`docker-compose.nautilus-strategy-sidecar.yml`** only if you intentionally split sink vs sidecar.

Upstream framework: [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader). Official **example strategies** (develop): [nautilus_trader/examples/strategies](https://github.com/nautechsystems/nautilus_trader/tree/develop/nautilus_trader/examples/strategies) — includes **`ema_cross_hedge_mode.py`** (venue hedge-style / dual-side patterns in Nautilus’ OMS; not the same as this repo’s read-only **spot** HTTP sink). Other useful references: `ema_cross.py`, `orderbook_imbalance.py`, `blank.py` (template).
