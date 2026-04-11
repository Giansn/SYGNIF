# Correlation research — evidence log (GitNexus + external)

**Purpose:** Reproducible, **proof-oriented** references for BTC–market / BTC–alts **correlation** work in Sygnif: what the **repo implements**, what **GitNexus** shows about call graph and blast radius, and **third-party** algorithms/repos to borrow from (verify licenses before reuse).

**Last GitNexus re-index (local):** `npx gitnexus analyze . --force --skip-agents-md`  
**Index snapshot (stdout):** 2,169 nodes · 4,935 edges · 133 clusters · 182 flows  
**Indexed git commit:** `38b2351a85be6518e9f78dc978b31fc69828b670` (same as `HEAD` at index time)

Re-run after large code changes:

```bash
cd /path/to/SYGNIF && npx gitnexus analyze . --force --skip-agents-md
```

Multi-repo machines must pass **`-r SYGNIF`** to CLI tools (`query`, `context`, `impact`, …).

---

## 1. GitNexus — internal symbols (correlation-adjacent)

### 1.1 `attach_orb_columns` (session ORB on 5m, BTC/ETH)

| Field | Value |
|--------|--------|
| **UID** | `Function:user_data/strategies/market_sessions_orb.py:attach_orb_columns` |
| **File** | `user_data/strategies/market_sessions_orb.py` (lines 34–111) |
| **Incoming calls** | `_populate_indicators_inner` in `SygnifStrategy.py`, `user_data/strategies/SygnifStrategy.py`, `user_data/strategies/MarketStrategy2.py`; test `test_attach_orb_columns_btc_sets_session_and_breakout` |
| **Outgoing** | `is_orb_pair` (same module) |

**`npx gitnexus impact -r SYGNIF attach_orb_columns` (upstream, excerpt):** risk **LOW**; direct callers include both strategy copies + root `SygnifStrategy.py` + tests.

### 1.2 `fetch_altcoins_correlation_usd` (NewHedge HTTP)

| Field | Value |
|--------|--------|
| **UID** | `Function:finance_agent/newhedge_client.py:fetch_altcoins_correlation_usd` |
| **Incoming** | `format_telegram_altcoins_correlation_block` |
| **Outgoing** | `_request_json` |

**`npx gitnexus impact -r SYGNIF fetch_altcoins_correlation_usd` (upstream, excerpt):** risk **LOW**; primary dependent `format_telegram_altcoins_correlation_block`. Graph also associates **process** names such as `cmd_briefing` (heuristic flow grouping — treat as navigation hint, not runtime proof).

### 1.3 `format_telegram_altcoins_correlation_block`

| Field | Value |
|--------|--------|
| **UID** | `Function:finance_agent/newhedge_client.py:format_telegram_altcoins_correlation_block` |
| **Incoming** | `finance_agent/bot.py:_newhedge_telegram_altcoins_correlation_block`; `tests/test_newhedge_client.py` (two tests) |
| **Outgoing** | `fetch_altcoins_correlation_usd`, `_last_point_series` |

### 1.4 BTC informative merge (ambiguous symbol name)

GitNexus reports **multiple** `btc_informative_indicators` definitions (root vs `user_data` vs `MarketStrategy2` vs legacy `MarketStrategy1`). For **live Sygnif**, treat **`user_data/strategies/SygnifStrategy.py`** (and **`MarketStrategy2.py`** when MS2) as canonical; keep root `SygnifStrategy.py` synced for tests per project rules.

**Disambiguation in CLI:** use full **uid**, e.g.  
`npx gitnexus context -r SYGNIF "Function:user_data/strategies/SygnifStrategy.py:btc_informative_indicators"`

---

## 2. External — vendor API (third-party series)

| Source | URL | Role |
|--------|-----|------|
| NewHedge API reference | https://docs.newhedge.io/api | `GET /api/v2/metrics/:chart_slug/:metric_name?api_token=…` |
| Sygnif client | `finance_agent/newhedge_client.py` | Implements `altcoins-correlation/altcoins_price_usd` + Telegram summary |

**Proof contract:** responses are **not** Sygnif TA and **not** Bybit; label them in any model rubric (see `finance_agent/briefing.md` third-party separation).

---

## 3. External — GitHub reference implementations (algorithms / patterns)

These are **independent** repositories (not vendored in SYGNIF). Cite for methodology; verify **license** and **data rights** before copying code.

| Repository | URL | Pattern |
|------------|-----|---------|
| Bitcoin-Rolling-Correlation | https://github.com/0xd3lbow/Bitcoin-Rolling-Correlation | Rolling correlation time series |
| btc-correlation | https://github.com/v0di/btc-correlation | Pearson BTC vs alts, exchange OHLCV + pandas |
| QuantCryptoProject | https://github.com/andrewting19/QuantCryptoProject | Cross-correlation / lead–lag style analysis |
| CryptoCorrelationPairTrading | https://github.com/JerryPan2718/CryptoCorrelationPairTrading | Cross-correlation, pair-trading notebooks |
| crypto-risk-toolkit | https://github.com/Ryan-Clinton/crypto-risk-toolkit | Correlation matrices, Pearson/Spearman/Kendall, rolling windows |
| crypto_prices_ML | https://github.com/ArtTucker/crypto_prices_ML | Heatmaps / multi-asset correlation from tabular history |
| ccxt-pandas | https://github.com/sigma-quantiphi/ccxt-pandas | CCXT + pandas OHLCV plumbing (optional if multi-venue) |

**Sygnif-native data path for DIY correlation:** align closes from **Bybit** (same endpoints as `finance_agent/bot.py` / `pull_btc_context.py`) and compute `pct_change` → rolling `.corr()` or `DataFrame.corr(method=...)`.

---

## 4. Optional evaluation nodes

When an LLM cites **NewHedge** or **rolling corr** outputs next to **Sygnif TA**, treat third-party correlation series like other non-briefing context: they must not be labeled as `calc_ta_score` / `detect_signals` / Bybit live unless the source line says so.

---

## 5. Changelog

| Date (UTC) | Change |
|------------|--------|
| 2026-04-11 | Initial doc: GitNexus force-analyze stats, UIDs/impacts for ORB + NewHedge, external GitHub table, NewHedge docs URL. |
