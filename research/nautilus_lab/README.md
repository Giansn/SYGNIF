# Nautilus research lab (Docker)

## Run (with live `finance-agent` on same compose network)

```bash
cd ~/SYGNIF
docker compose --profile btc-nautilus up -d --build finance-agent nautilus-research
```

## Exec examples

```bash
docker exec -it nautilus-research python3 /lab/workspace/nautilus_smoke.py
docker exec -it nautilus-research python3 /lab/workspace/btc_regime_assessment.py
docker exec -it nautilus-research python3 /lab/workspace/btc_dump_run_framework.py
docker exec -it nautilus-research python3 -c "import grid_market_maker; print(grid_market_maker.GridMarketMaker.__name__)"
docker exec -w /lab/workspace nautilus-research python3 /lab/workspace/run_grid_market_maker_backtest.py
docker exec -w /lab/workspace -e PYTHONPATH=/lab/workspace nautilus-research python3 /lab/workspace/run_bybit_demo_trading_node.py --max-ticks 5
docker exec -w /lab/workspace -e PYTHONPATH=/lab/workspace -e NAUTILUS_EXEC_TESTER_DEMO_ACK=YES nautilus-research \
  python3 /lab/workspace/run_bybit_demo_exec_tester.py --instrument ETHUSDT-LINEAR.BYBIT
docker exec -w /lab/workspace -e PYTHONPATH=/lab/workspace -e NAUTILUS_GRID_MM_DEMO_ACK=YES nautilus-research \
  python3 /lab/workspace/run_bybit_demo_grid_market_maker.py
```

`FINANCE_AGENT_BASE_URL` defaults to `http://finance-agent:8091` inside the stack.

### Bybit demo **live** trading (Nautilus adapters — smoke only)

This path uses Nautilus’ **official Bybit integration** (`BybitDataClient` / `BybitExecutionClient` via `BybitLiveDataClientFactory` + `BybitLiveExecClientFactory`), **not** the read-only `BybitHttpClient` used by `bybit_nautilus_spot_btc_training_feed.py`.

| Item | Purpose |
|------|---------|
| [`requirements-bybit-demo-live.txt`](./requirements-bybit-demo-live.txt) | Same Python deps as `docker/Dockerfile.nautilus_research` for a **host venv** (`pip install -r …`). |
| **Env** | **`BYBIT_DEMO_API_KEY`** / **`BYBIT_DEMO_API_SECRET`** — keys from [Bybit demo trading](https://www.bybit.com/en-US/demo-trade); Nautilus reads them when `demo=True` (see `get_cached_bybit_http_client` in upstream `adapters/bybit/factories.py`). |
| [`run_bybit_demo_trading_node.py`](./run_bybit_demo_trading_node.py) | Builds `TradingNodeConfig` with `demo=True`, registers both factories, loads **`BTCUSDT-LINEAR.BYBIT`** (override `--instrument`), runs [`bybit_demo_quote_smoke.py`](./bybit_demo_quote_smoke.py) — **logs quote ticks, does not send orders**. After `--max-ticks` (default 8) raises `KeyboardInterrupt` so the process exits and `dispose()` runs. |
| **`NAUTILUS_DEMO_SMOKE_AUTO_EXIT_SEC`** | Optional (Linux): `SIGALRM` → interrupt if the venue never delivers ticks (auth / firewall). Also set in [`.env.example`](../../.env.example). |
| **Host helper** | [`scripts/start_bybit_demo_nautilus.sh`](../../scripts/start_bybit_demo_nautilus.sh) — `docker exec` into `nautilus-research` with `PYTHONPATH=/lab/workspace`, or local `python3` if `nautilus_trader` is installed. |

```bash
# From repo root (container must see BYBIT_DEMO_* from compose env_file)
docker exec -w /lab/workspace -e PYTHONPATH=/lab/workspace nautilus-research \
  python3 /lab/workspace/run_bybit_demo_trading_node.py --max-ticks 5
```

**Orders / strategies:** For order flow tests, upstream ships [`examples/live/bybit/bybit_exec_tester.py`](https://github.com/nautechsystems/nautilus_trader/blob/develop/examples/live/bybit/bybit_exec_tester.py) (`ExecTester`). This repo adds a **demo-keyed** runner (aligned with installed NT `demo=True` configs, not the develop-only `BybitEnvironment` snippet):

| Script | Role |
|--------|------|
| [`run_bybit_demo_exec_tester.py`](./run_bybit_demo_exec_tester.py) | Registers Bybit live factories with **`demo=True`**, loads one instrument, runs **`ExecTester`** from `nautilus_trader.test_kit.strategies.tester_exec`. **Submits orders** on the demo venue. |
| **Gate** | Set **`NAUTILUS_EXEC_TESTER_DEMO_ACK=YES`** (also in [`.env.example`](../../.env.example)) plus **`BYBIT_DEMO_API_KEY`** / **`BYBIT_DEMO_API_SECRET`**. |
| **Defaults** | Instrument **`ETHUSDT-LINEAR.BYBIT`**, `order_qty=0.01`, **post-only** limits off touch; no market open unless ``--open-on-start 0.01``. **Hedge** (`BothSides`) for linear/inverse unless ``--merged-single``. |
| **Host** | [`scripts/start_bybit_demo_exec_tester.sh`](../../scripts/start_bybit_demo_exec_tester.sh) |

```bash
export NAUTILUS_EXEC_TESTER_DEMO_ACK=YES
docker exec -w /lab/workspace -e PYTHONPATH=/lab/workspace nautilus-research \
  python3 /lab/workspace/run_bybit_demo_exec_tester.py --instrument ETHUSDT-LINEAR.BYBIT
```

To attach **`GridMarketMaker`** live instead, reuse the same `TradingNodeConfig` / factory registration from `run_bybit_demo_trading_node.py` and swap the strategy. **Do not** point `ExecTester` at mainnet keys.

### Bybit via Nautilus (not CCXT) — spot **BTC/USDT** training + regime feed

- **Canonical script:** **`bybit_nautilus_spot_btc_training_feed.py`** — Nautilus **`BybitHttpClient`**: `request_instruments`, `request_bars` (1h + 1d), `request_tickers`, `request_trades`, `request_orderbook_snapshot`, `request_instrument_statuses`; optional **`request_fee_rates`** if `BYBIT_*` / `BYBIT_DEMO_*` keys exist in the container env.
- **Outputs** (under `NAUTILUS_BTC_OHLCV_DIR`, default `/lab/btc_specialist_data`): **`btc_1h_ohlcv.json`**, **`btc_daily_90d.json`** (training + `btc_predict_runner`), **`btc_1h_ohlcv_nautilus_bybit.json`** (regime), **`nautilus_spot_btc_market_bundle.json`** (market snapshot JSON).
- **Loop (bundled):** Service **`nautilus-research`** in **`docker-compose.yml`** (profile **`btc-nautilus`**) runs **`run_nautilus_bundled.sh`** — **sink** + **sidecar** in one container. Intervals **`NAUTILUS_BYBIT_POLL_SEC`** / **`NAUTILUS_STRATEGY_POLL_SEC`** (default 300s). Same profile also starts **`nautilus-sygnif-btc-node`** (`run_sygnif_btc_trading_node.py`) for BTC logic on a Nautilus **`TradingNode`** (Bybit **demo** keys).
- **Bybit testnet bar node:** profile **`btc-testnet`** → **`nautilus-btc-testnet`** runs the same script with **`--testnet`** and **`BYBIT_TESTNET_API_KEY`** / **`BYBIT_TESTNET_API_SECRET`**. Start: [`scripts/start_nautilus_btc_testnet.sh`](../../scripts/start_nautilus_btc_testnet.sh).
- **Legacy one-liner:** `bybit_nautilus_btc_ohlcv_sink.py` (1h only) remains for manual use.
- **Env:** `NAUTILUS_BYBIT_DEMO` / `NAUTILUS_BYBIT_TESTNET`, `NAUTILUS_TRAINING_BAR_LIMIT_1H` (default 1600), `NAUTILUS_TRAINING_BAR_LIMIT_1D` (120), `NAUTILUS_TRAINING_TRADES_LIMIT`, `NAUTILUS_TRAINING_BOOK_LEVELS`, `NAUTILUS_TRAINING_BOOK_DELTA_CAP`.
- **Consumers:** `training_pipeline/channel_training.py`, `prediction_agent/btc_predict_runner.py`, `btc_regime_assessment.py`.

### Strategy sidecar (same container as sink)

- **Script:** `nautilus_sidecar_strategy.py` — reads sink `btc_1h_ohlcv.json`, writes **`nautilus_strategy_signal.json`**. Started by **`run_nautilus_bundled.sh`** alongside the Bybit sink (no second container).
- **Split sink vs sidecar:** not supported as a separate compose file anymore; fork **`run_nautilus_bundled.sh`** if you need two processes in two containers.

Upstream framework: [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader). Official **example strategies** (develop): [nautilus_trader/examples/strategies](https://github.com/nautechsystems/nautilus_trader/tree/develop/nautilus_trader/examples/strategies) — includes **`ema_cross_hedge_mode.py`** (venue hedge-style / dual-side patterns in Nautilus’ OMS; not the same as this repo’s read-only **spot** HTTP sink). Other useful references: `ema_cross.py`, `orderbook_imbalance.py`, `blank.py` (template).

### Grid market maker (example strategy, vendored)

- **File:** [`grid_market_maker.py`](./grid_market_maker.py) — symmetric post-only grid around mid with optional inventory skew and requote threshold; upstream-style **test strategy** (see file header). **Not** started by the bundled sink/sidecar; register it in your own `TradingNode` / backtest script when you wire execution or sim.
- **Smoke:** from `/lab/workspace`, `python3 -c "import grid_market_maker"` (see exec examples above).
- **Ordered backtest start (synthetic quotes):** [`run_grid_market_maker_backtest.py`](./run_grid_market_maker_backtest.py) runs **(1) PREPARE** → writes a `ParquetDataCatalog`, **(2) RUN** → `BacktestNode` + `GridMarketMaker`, **(3) DONE** → JSON summary. Simulated venue is **BINANCE** with **MARGIN** / **USDT** (required for `CurrencyPair` BTCUSDT in NT).
  - All-in-one: `docker exec -w /lab/workspace nautilus-research python3 /lab/workspace/run_grid_market_maker_backtest.py`
  - Split: `--step prepare [--catalog DIR]` then `--step run --catalog DIR`
  - Optional env: `NAUTILUS_GRID_CATALOG` (default temp dir under `/tmp`).
- **Host helper:** [`scripts/start_grid_market_maker.sh`](../../scripts/start_grid_market_maker.sh) — uses `docker exec` into `nautilus-research` when that container is running, else local `python3` if `nautilus_trader` is importable.
- **Live demo (real orders on Bybit demo):** [`run_bybit_demo_grid_market_maker.py`](./run_bybit_demo_grid_market_maker.py) wires the same `TradingNode` + `BybitLive*Factory` pattern as the ExecTester demo runner, then runs **`GridMarketMaker`**. Requires **`NAUTILUS_GRID_MM_DEMO_ACK=YES`** and **`BYBIT_DEMO_*`** (see [`.env.example`](../../.env.example)). Host: [`scripts/start_bybit_demo_grid_mm.sh`](../../scripts/start_bybit_demo_grid_mm.sh). **Default hedge:** `BybitExecClientConfig.position_mode` is set to **`BothSides`** for the symbol (Nautilus calls Bybit `switch-mode`); use **`--merged-single`** for one-way.
- **BTCUSDT linear — dedicated compose service:** profile **`btc-grid-mm`** → **`nautilus-grid-btc01`** in [`docker-compose.yml`](../../docker-compose.yml) runs the same script with **`BTCUSDT-LINEAR.BYBIT`** and env-tunable sizes (`NAUTILUS_GRID_BTC01_*`). **`restart: unless-stopped`**; add **`COMPOSE_PROFILES=btc-grid-mm`** to `.env` so `docker compose up -d` brings the grid back after reboot. Start: [`scripts/start_btc01_nautilus_grid.sh`](../../scripts/start_btc01_nautilus_grid.sh). Cancel all demo opens for the symbol: [`scripts/bybit_demo_cancel_open_orders.py`](../../scripts/bybit_demo_cancel_open_orders.py) (stop `nautilus-grid-btc01` first if you do not want immediate re-quotes). Prefer **`BYBIT_DEMO_GRID_*`** if you run multiple demo linear bots on the same host.

```bash
export NAUTILUS_GRID_MM_DEMO_ACK=YES
docker exec -w /lab/workspace -e PYTHONPATH=/lab/workspace -e NAUTILUS_GRID_MM_DEMO_ACK=YES nautilus-research \
  python3 /lab/workspace/run_bybit_demo_grid_market_maker.py --instrument ETHUSDT-LINEAR.BYBIT
```
