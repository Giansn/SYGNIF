# BTC_Strategy_0_1 — Bybit **demo / paper** bridge (Freqtrade + CCXT)

**Purpose:** One place to understand **how `BTC_Strategy_0_1` talks to Bybit**, why **`user_data/bybit_ccxt_demo_patch.py`** exists, which **configs** to copy, and how **Docker** wires the stack. **No live keys** in git — use `*.example.json` and `.env`.

**Canonical strategy spec:** [`BTC_Strategy_0.1.md`](./BTC_Strategy_0.1.md) · **Registry:** [`btc_strategy_0_1_rule_registry.json`](./btc_strategy_0_1_rule_registry.json) · **Paper config (tracked):** `user_data/config_btc_strategy_0_1_paper_market.json` · **Demo exchange template:** `user_data/config_btc_strategy_0_1_bybit_demo.example.json`

---

## 1. Components

| Piece | Path / service | Role |
|--------|----------------|------|
| Strategy class | `user_data/strategies/BTC_Strategy_0_1.py` | Extends `SygnifStrategy`; R01–R03 tags, bucket cap, `custom_stoploss` / `custom_exit` |
| Rule engine | `user_data/strategies/btc_strategy_0_1_engine.py` | Registry cap, `training_channel_output.json` read for R01 governance |
| CCXT demo patch | `user_data/bybit_ccxt_demo_patch.py` | Patches Freqtrade’s bundled `exchange.py` so **`enable_demo_trading(True)`** runs when `options.enableDemoTrading` is set |
| Docker bake | `docker/Dockerfile.custom` | Runs patch **at image build** against `/freqtrade/.../exchange.py` |
| Runtime re-patch | `docker-compose.yml` → `freqtrade-futures` `command` | **`python3 /freqtrade/user_data/bybit_ccxt_demo_patch.py && exec freqtrade ...`** so volume-mounted `user_data/` updates still apply after rebuild |

---

## 2. Why the patch exists

Bybit **demo trading** uses API keys from the **Bybit demo** UI. CCXT exposes `options.enableDemoTrading`, but Freqtrade’s exchange init **does not** call `enable_demo_trading()` for you. Without that call, private calls can still target the wrong environment and demo keys fail (e.g. **retCode 10003** during `additional_exchange_init` / `set_position_mode`).

The patch injects (once, idempotent marker `BYBIT_CCXT_DEMO_PATCH`):

- After `api = ccxt.bybit(ex_config)`, if `ex_config["options"].get("enableDemoTrading")` and `api.enable_demo_trading` exist → **`api.enable_demo_trading(True)`**.

**Rebuild** the trader image after editing the patch script: `docker compose ... up -d --build`.

---

## 3. Exchange block — **futures demo** (recommended shape)

Use **keys from the Bybit demo account** only. In **`ccxt_config` / `ccxt_async_config`**:

- `"defaultType": "swap"`, `"defaultSettle": "USDT"`
- `"enableDemoTrading": true`
- `"hostname": "bybit.com"` (stay on production hostnames; let CCXT route demo — avoids **retCode 10032** / `load_markets` issues seen when hard-coding legacy `api-demo` URLs for linear)

**Spot demo** (different stack, e.g. `config_btc_spot_dedicated.bybit_demo.example.json`) may still document explicit `api-demo.bybit.com` URLs — that is **spot**; **linear USDT perps** follow the futures pattern above.

Secrets belong in **`.env`** (e.g. `BYBIT_DEMO_API_KEY` / `BYBIT_DEMO_API_SECRET`) and in a **gitignored** `user_data/config_futures.json` — **never** commit filled keys (see repo history hygiene).

---

## 4. Config files (what to copy)

| File | Use |
|------|-----|
| `user_data/config_btc_strategy_0_1_paper_market.json` | **Paper / dry_run** futures, `BTC/USDT:USDT`, `BTC_Strategy_0_1` — safe defaults in git |
| `user_data/config_btc_trend_backtest.json` | Minimal **backtest** / CI shape |
| `user_data/config_btc_strategy_0_1_bybit_demo.example.json` | **Template** for Bybit **demo** futures (`dry_run: false`); merge keys from `.env`, then save as **gitignored** `config_futures.json` or a private filename |
| `user_data/config_btc_spot_dedicated.bybit_demo.example.json` | **Spot** demo (different `defaultType` / URLs) — not the same as perps |

`SYGNIF_PROFILE=btc_trend` (compose) aligns R02 trend mapping with `btc_trend_regime.py`.

---

## 5. Docker entrypoints

| Service | Profile | Notes |
|---------|---------|--------|
| `freqtrade-btc-0-1` | `btc-0-1` | Standard `freqtrade trade` entry; **paper** config; API e.g. host `8185` → container `8085` |
| `freqtrade-futures` | `main-traders` | **`bybit_ccxt_demo_patch.py` then `freqtrade trade`** with `config_futures.json` + `BTC_Strategy_0_1` — your **local** `config_futures.json` should match demo keys + options above |

Rebuild after changing **`Dockerfile.custom`**, the patch script, or **Freqtrade base image** tag.

---

## 6. Operational checklist

1. Copy example → gitignored config; set **demo** keys and **api_server** password / JWT.
2. `docker compose --profile btc-0-1 up -d --build freqtrade-btc-0-1` **or** main-traders futures stack.
3. On failure: `docker logs freqtrade-futures` / `freqtrade-btc-0-1` — search **retCode**, **10003**, **10032**, **enableDemoTrading**.
4. `python3 user_data/bybit_ccxt_demo_patch.py` on the host does nothing useful (path is `/freqtrade/...` inside the container) — always reason about **container** paths.

---

## 7. Cross-links

- Rule / training inflow: [`RULE_AND_DATA_FLOW_LOOP.md`](./RULE_AND_DATA_FLOW_LOOP.md)  
- R01 live gate vs Nautilus: same doc **Cross-link** + [`BTC_Strategy_0.1.md`](./BTC_Strategy_0.1.md) §7.1  
- Rebuild helper: `scripts/rebuild_freqtrade_btc_0_1.sh`
