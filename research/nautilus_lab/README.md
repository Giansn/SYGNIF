# Nautilus research lab

**`docker-compose.yml` no longer ships** `nautilus-research`, bar node, or grid services. Run from a **host venv** (see `requirements-bybit-demo-live.txt`) or restore **`archive/freqtrade-btc-dock-2026-04-13/`**. Point `FINANCE_AGENT_BASE_URL` at `http://127.0.0.1:8091` (or `http://finance-agent:8091` if you attach a custom container to the compose network).

**Primary live Sygnif path for BTC demo + ML gate:** the **predict protocol** — [`scripts/start_nautilus_btc_predict_protocol.sh`](../../scripts/start_nautilus_btc_predict_protocol.sh) (see § *BTC predict protocol* below). Prefer **one** `TradingNode` per Bybit demo account (no parallel `run_sygnif_btc_trading_node` in Docker + host).

## BTC predict protocol (canonical)

| Piece | Role |
|--------|------|
| **[`scripts/start_nautilus_btc_predict_protocol.sh`](../../scripts/start_nautilus_btc_predict_protocol.sh)** | Loads demo keys safely from `~/xrp_claude_bot/.env` + `SYGNIF/.env`, sets `PYTHONPATH`, runs `run_sygnif_btc_trading_node.py --live-predict --exec-adaptive` with ACK + sidecar gate defaults. |
| **[`scripts/btc_predict_asap_order.py`](../../scripts/btc_predict_asap_order.py)** | **Low-latency** path: fresh `btc_predict_live` fit (~2–5s with default ASAP tree sizes) → **stdout flush** → optional **immediate** Bybit demo **market** long/short + **inverse-move leverage** (small predicted move % → higher leverage). Not bar-synced; use when signal decay matters. Requires Nautilus **venv** Python + `PYTHONPATH` to `prediction_agent`. |
| **[`scripts/btc_predict_protocol_loop.py`](../../scripts/btc_predict_protocol_loop.py)** | **Circular / delegated** demo REST loop: same live predict + `decide_side` on each tick → **auto exit** on **opposite** signal (reduce-only), **auto entry** when flat. **No-edge** (`decide_side` → `None`): by default **hold** open position (`PREDICT_LOOP_HOLD_ON_NO_EDGE=1`) to cut chop; use `--exit-on-no-edge` or env `0` to flatten every no-edge cycle. Each line logs `predict_ms` (~2–8s typical with default ASAP trees). Interval `PREDICT_LOOP_INTERVAL_SEC` (default **0** = no extra gap; seamless cycles until Ctrl+C). Core: `prediction_agent/btc_asap_predict_core.py`. |
| **`run_sygnif_btc_trading_node.py`** | Bybit **demo** `TradingNode`, 5m bars, optional exec. With `--live-predict`, gates post-only BUY from in-process `btc_predict_live` (RF/XGB/LogReg + `nautilus_enhanced_consensus`). |
| **`run_nautilus_bundled.sh`** | Feeds **`nautilus_strategy_signal.json`** (and OHLCV JSON) under `finance_agent/btc_specialist/data/` — run alongside when **`NAUTILUS_SYGNIF_NODE_SIDECAR_GATE=1`** (default in the start script). |
| **Logs / PID (optional)** | Append to `research/nautilus_lab/nautilus_btc_predict_protocol.log`; `echo $! > research/nautilus_lab/nautilus_btc_predict_protocol.pid` after `nohup`. |

### Order execution path (Sygnif BTC)

- **Canonical:** **Nautilus** `TradingNode` → `SygnifBtcBarNodeStrategy` → `submit_order` → Bybit **demo** exec adapter (`demo=True`). All venue orders for this protocol originate in Nautilus.
- **Not this path:** Freqtrade `POST /forceenter` (`scripts/btc_analysis_forceenter.py`) — optional **separate** stack when a Freqtrade futures bot is up.
- **Not this path:** Direct Bybit REST order scripts (e.g. `scripts/btc_analysis_bybit_demo_market.py`) — bypasses Nautilus `LiveRiskEngine`; use only for emergencies / debugging.
- **Latency-first:** `scripts/btc_predict_asap_order.py` — fresh live predict, **flush-print**, then **market** order on demo in the same process (still not Nautilus OMS; use when bar-wait would stale the signal).
- **Loop / delegated exits:** `scripts/btc_predict_protocol_loop.py` — repeated predict + **reduce-only** closes + opens on Bybit demo (not Nautilus OMS).

Optional **automated leverage (venue prep):** `NAUTILUS_SYGNIF_NODE_INITIAL_LEVERAGE` or `--initial-leverage` runs a one-shot Bybit `set-leverage` (demo keys, linear only) **before** the node starts. **Order size** on the canonical path is also automated: `--exec-adaptive` (default in `start_nautilus_btc_predict_protocol.sh`) sizes each post-only BUY from free USDT × `NAUTILUS_SYGNIF_NODE_EXEC_STAKE_FRAC` ÷ mid — both are deterministic **calls**, not manual picks at order time. **Fills still flow through Nautilus**.

**Env knobs (optional):** `NAUTILUS_SYGNIF_NODE_EXEC_STAKE_FRAC`, `NAUTILUS_SYGNIF_EXEC_OFFSET_BPS`, `NAUTILUS_SYGNIF_EXEC_MAX_ORDERS`, `NAUTILUS_SYGNIF_BAR_MINUTES`, `NAUTILUS_SYGNIF_MAX_BARS`, `NAUTILUS_SYGNIF_NODE_SIDECAR_GATE`, `NAUTILUS_SYGNIF_NODE_INITIAL_LEVERAGE`, `NAUTILUS_SYGNIF_NODE_LIVE_*` — see docstring in `run_sygnif_btc_trading_node.py`.

```bash
# 1) Data + sidecar (separate terminal or systemd)
cd ~/SYGNIF/research/nautilus_lab && bash run_nautilus_bundled.sh

# 2) Predict protocol (demo orders only when live signal is bullish; post-only limits)
nohup ~/SYGNIF/scripts/start_nautilus_btc_predict_protocol.sh \
  >> ~/SYGNIF/research/nautilus_lab/nautilus_btc_predict_protocol.log 2>&1 &
echo $! > ~/SYGNIF/research/nautilus_lab/nautilus_btc_predict_protocol.pid
```

**ASAP market entry (demo REST, not Nautilus OMS):** after `cd ~/SYGNIF/research/nautilus_lab && . .venv/bin/activate`:

```bash
export PYTHONPATH="$HOME/SYGNIF/prediction_agent"
# dry-run: predict + printed plan
python3 "$HOME/SYGNIF/scripts/btc_predict_asap_order.py"
# live: same, then set-leverage + market order immediately after prints
export SYGNIF_PREDICTION_ASAP_ORDER_ACK=YES
python3 "$HOME/SYGNIF/scripts/btc_predict_asap_order.py" --execute
```

**Closed-loop REST (entries + exits):**

```bash
export PYTHONPATH="$HOME/SYGNIF/prediction_agent"
# dry-run forever (Ctrl+C to stop); one-shot test: --max-iterations 1
python3 "$HOME/SYGNIF/scripts/btc_predict_protocol_loop.py"
export SYGNIF_PREDICT_PROTOCOL_LOOP_ACK=YES
python3 "$HOME/SYGNIF/scripts/btc_predict_protocol_loop.py" --execute
# optional: --interval-sec 300 to add 5m pacing between cycles
# background + demo keys (same merge as Nautilus protocol):
# bash scripts/start_btc_predict_protocol_loop.sh
```

**Behaviour:** no new demo BUY when live consensus is not `BULLISH` / `STRONG_BULLISH` (or, if consensus is `MIXED`, when `direction_logistic` is not `UP` with confidence ≥ 65 — same idea as `btc_analysis_order_signal`), or when sidecar bias is `short`. Fills print a `SYGNIF_OPENED_TRADE` line.

## Run (host)

```bash
cd ~/SYGNIF/research/nautilus_lab
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-bybit-demo-live.txt
export PYTHONPATH="$(pwd)"
export NAUTILUS_BTC_OHLCV_DIR="${NAUTILUS_BTC_OHLCV_DIR:-$HOME/SYGNIF/finance_agent/btc_specialist/data}"
bash run_nautilus_bundled.sh
```

## Exec examples (host; same paths)

```bash
cd ~/SYGNIF/research/nautilus_lab && export PYTHONPATH="$(pwd)"
python3 nautilus_smoke.py
python3 btc_regime_assessment.py
python3 btc_dump_run_framework.py
python3 -c "import grid_market_maker; print(grid_market_maker.GridMarketMaker.__name__)"
python3 run_grid_market_maker_backtest.py
python3 run_bybit_demo_trading_node.py --max-ticks 5
NAUTILUS_EXEC_TESTER_DEMO_ACK=YES python3 run_bybit_demo_exec_tester.py --instrument ETHUSDT-LINEAR.BYBIT
NAUTILUS_GRID_MM_DEMO_ACK=YES python3 run_bybit_demo_grid_market_maker.py
```

If you still run a **custom** container named `nautilus-research`, replace the above with `docker exec -w /lab/workspace -e PYTHONPATH=/lab/workspace nautilus-research python3 /lab/workspace/<script>.py …`.

### Bybit demo **live** trading (Nautilus adapters — smoke only)

This path uses Nautilus’ **official Bybit integration** (`BybitDataClient` / `BybitExecutionClient` via `BybitLiveDataClientFactory` + `BybitLiveExecClientFactory`), **not** the read-only `BybitHttpClient` used by `bybit_nautilus_spot_btc_training_feed.py`.

| Item | Purpose |
|------|---------|
| [`requirements-bybit-demo-live.txt`](./requirements-bybit-demo-live.txt) | Python deps for a **host venv** (`pip install -r …`); the old `Dockerfile.nautilus_research` was removed from the repo. |
| **Env** | **`BYBIT_DEMO_API_KEY`** / **`BYBIT_DEMO_API_SECRET`** — keys from [Bybit demo trading](https://www.bybit.com/en-US/demo-trade); Nautilus reads them when `demo=True` (see `get_cached_bybit_http_client` in upstream `adapters/bybit/factories.py`). |
| [`run_bybit_demo_trading_node.py`](./run_bybit_demo_trading_node.py) | Builds `TradingNodeConfig` with `demo=True`, registers both factories, loads **`BTCUSDT-LINEAR.BYBIT`** (override `--instrument`), runs [`bybit_demo_quote_smoke.py`](./bybit_demo_quote_smoke.py) — **logs quote ticks, does not send orders**. After `--max-ticks` (default 8) raises `KeyboardInterrupt` so the process exits and `dispose()` runs. |
| **`NAUTILUS_DEMO_SMOKE_AUTO_EXIT_SEC`** | Optional (Linux): `SIGALRM` → interrupt if the venue never delivers ticks (auth / firewall). Also set in [`.env.example`](../../.env.example). |
| **Host helper** | [`scripts/start_bybit_demo_nautilus.sh`](../../scripts/start_bybit_demo_nautilus.sh) — local `python3` if `nautilus_trader` is installed, else optional `docker exec` into a running `nautilus-research` container. |

```bash
cd ~/SYGNIF/research/nautilus_lab && export PYTHONPATH="$(pwd)"
python3 run_bybit_demo_trading_node.py --max-ticks 5
```

**Orders / strategies:** For order flow tests, upstream ships [`examples/live/bybit/bybit_exec_tester.py`](https://github.com/nautechsystems/nautilus_trader/blob/develop/examples/live/bybit/bybit_exec_tester.py) (`ExecTester`). This repo adds a **demo-keyed** runner (aligned with installed NT `demo=True` configs, not the develop-only `BybitEnvironment` snippet):

| Script | Role |
|--------|------|
| [`run_bybit_demo_exec_tester.py`](./run_bybit_demo_exec_tester.py) | Registers Bybit live factories with **`demo=True`**, loads one instrument, runs **`ExecTester`** from `nautilus_trader.test_kit.strategies.tester_exec`. **Submits orders** on the demo venue. |
| **Gate** | Set **`NAUTILUS_EXEC_TESTER_DEMO_ACK=YES`** (also in [`.env.example`](../../.env.example)) plus **`BYBIT_DEMO_API_KEY`** / **`BYBIT_DEMO_API_SECRET`**. |
| **Defaults** | Instrument **`ETHUSDT-LINEAR.BYBIT`**, `order_qty=0.01`, **post-only** limits off touch; no market open unless ``--open-on-start 0.01``. **Hedge** (`BothSides`) for linear/inverse unless ``--merged-single``. |
| **Host** | [`scripts/start_bybit_demo_exec_tester.sh`](../../scripts/start_bybit_demo_exec_tester.sh) |

```bash
cd ~/SYGNIF/research/nautilus_lab && export PYTHONPATH="$(pwd)"
export NAUTILUS_EXEC_TESTER_DEMO_ACK=YES
python3 run_bybit_demo_exec_tester.py --instrument ETHUSDT-LINEAR.BYBIT
```

To attach **`GridMarketMaker`** live instead, reuse the same `TradingNodeConfig` / factory registration from `run_bybit_demo_trading_node.py` and swap the strategy. **Do not** point `ExecTester` at mainnet keys.

### Bybit via Nautilus (not CCXT) — spot **BTC/USDT** training + regime feed

- **Canonical script:** **`bybit_nautilus_spot_btc_training_feed.py`** — Nautilus **`BybitHttpClient`**: `request_instruments`, `request_bars` (1h + 1d), `request_tickers`, `request_trades`, `request_orderbook_snapshot`, `request_instrument_statuses`; optional **`request_fee_rates`** if `BYBIT_*` / `BYBIT_DEMO_*` keys exist in the container env.
- **Outputs** (under `NAUTILUS_BTC_OHLCV_DIR`, default `/lab/btc_specialist_data`): **`btc_1h_ohlcv.json`**, **`btc_daily_90d.json`** (training + `btc_predict_runner`), **`btc_1h_ohlcv_nautilus_bybit.json`** (regime), **`nautilus_spot_btc_market_bundle.json`** (market snapshot JSON).
- **Loop (bundled):** On the host, **`bash run_nautilus_bundled.sh`** runs **sink** + **sidecar** (same script the old `nautilus-research` container used). Intervals **`NAUTILUS_BYBIT_POLL_SEC`** / **`NAUTILUS_STRATEGY_POLL_SEC`** (default 300s). Bar node / testnet: run **`python3 run_sygnif_btc_trading_node.py`** locally with **`BYBIT_DEMO_*`** or **`--testnet`** + **`BYBIT_TESTNET_*`** (no compose service).
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
  - All-in-one: `cd research/nautilus_lab && PYTHONPATH=. python3 run_grid_market_maker_backtest.py`
  - Split: `--step prepare [--catalog DIR]` then `--step run --catalog DIR`
  - Optional env: `NAUTILUS_GRID_CATALOG` (default temp dir under `/tmp`).
- **Host helper:** [`scripts/start_grid_market_maker.sh`](../../scripts/start_grid_market_maker.sh) — local `python3` if `nautilus_trader` is importable, else optional `docker exec` into `nautilus-research`.
- **Live demo (real orders on Bybit demo):** [`run_bybit_demo_grid_market_maker.py`](./run_bybit_demo_grid_market_maker.py) wires the same `TradingNode` + `BybitLive*Factory` pattern as the ExecTester demo runner, then runs **`GridMarketMaker`**. Requires **`NAUTILUS_GRID_MM_DEMO_ACK=YES`** and **`BYBIT_DEMO_*`** (see [`.env.example`](../../.env.example)). Host: [`scripts/start_bybit_demo_grid_mm.sh`](../../scripts/start_bybit_demo_grid_mm.sh). **Default hedge:** `BybitExecClientConfig.position_mode` is set to **`BothSides`** for the symbol (Nautilus calls Bybit `switch-mode`); use **`--merged-single`** for one-way.
- **BTCUSDT linear — host:** [`scripts/start_bybit_demo_grid_mm.sh`](../../scripts/start_bybit_demo_grid_mm.sh) runs **`run_bybit_demo_grid_market_maker.py`** when `nautilus_trader` is installed. Cancel demo opens: [`scripts/bybit_demo_cancel_open_orders.py`](../../scripts/bybit_demo_cancel_open_orders.py). Prefer **`BYBIT_DEMO_GRID_*`** if you run multiple demo linear bots on the same host.

```bash
cd ~/SYGNIF/research/nautilus_lab && export PYTHONPATH="$(pwd)"
export NAUTILUS_GRID_MM_DEMO_ACK=YES
python3 run_bybit_demo_grid_market_maker.py --instrument ETHUSDT-LINEAR.BYBIT
```
