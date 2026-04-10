---
name: finance-agent
description: "Sygnif unified domain: strategy code + live markets as one job. Market/TA/plays/overseer/bot AND SygnifStrategy/MarketStrategy2/config/refactors/GitNexus — same skill. Triggers: crypto, TA, signals, trades, /ta, NFI, Sygnif, entry tags, failure swing (sf_* via strategy_adaptation.json), Heavy91-style MTF RSI, backtest parity, bot.py."
allowed-tools:
  - Agent
  - Task
  - Bash
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - WebSearch
  - WebFetch
---

# Sygnif Finance Agent (unified)

**Code and finance are the same problem here.** Sygnif is a trading codebase: every market question touches **what the bot actually implements** (`user_data/strategies/SygnifStrategy.py`, `user_data/strategies/MarketStrategy2.py` when futures/sentiment MS2 is in use, `finance_agent/bot.py`, configs), and every code change touches **what happens in live markets** (tags, TA score, protections, overseer, backtest parity). This skill replaces the old **finance-agent** vs **finance-consultant** split — there is no separate “consultant” skill.

**Canonical copy (version control):** Sygnif repo — `.claude/skills/finance-agent/SKILL.md` (clone path is usually **`~/SYGNIF`**).  
**Claude Code global install:** mirror this file to `~/.claude/skills/finance-agent/SKILL.md` if you use `/finance-agent` from home skills.

## How to work (connected, not siloed)

| Situation | Do this |
|-----------|---------|
| User asks markets, TA, signals, plays | Ground claims in **live strategy + bot** when the context is Sygnif (read or cite `user_data/strategies/SygnifStrategy.py`, `user_data/strategies/MarketStrategy2.py`, `finance_agent/bot.py`; use GitNexus for “where is this defined?”). |
| User asks code: entries, exits, tags, adaptation JSON | Tie behaviour to **observable market rules** (TA bands, volume gates, slot caps) and to **`docs/backtest_live_parity.md`** when backtests matter. |
| User asks “why did / would the bot …?” | **Code path + data path together:** GitNexus query/context + Bybit (or logs/overseer) — not generic crypto commentary alone. |
| Refactors, tests, notifications, Docker | Same repo, same risk rails: strategy changes and ops are still **Sygnif finance** — use this skill plus project **`SYGNIF_CONTEXT.md`** / **`AGENTS.md`** (GitNexus) for edits. |

**Default stance:** Prefer one answer that merges **implementation truth** and **market snapshot**, instead of answering “as an analyst” and ignoring the repo (or the reverse).

## When to use

- Market conditions, prices, volume leaders, gainers/losers
- Technical analysis on a pair; Sygnif **TA score** and **entry-signal** interpretation
- Investment plays, opportunities, deep scan, news/sentiment
- NFI or Sygnif code: entries, exits, slots, sentiment zones
- **Telegram bot parity:** user asks in terms of `/market`, `/ta`, `/signals`, `/evaluate`, etc.
- Open trades, P/L context, HOLD/TRAIL/CUT-style evaluation (via Trade Overseer when available)
- Macro (Fed, rates, DXY, equities vs crypto)
- **Strategy engineering:** `SygnifStrategy.py`, `MarketStrategy2.py`, `strategy_adaptation.json`, configs, tests, parity docs — whenever the user is still in the Sygnif trading context (use GitNexus for blast radius per repo rules)
- **Failure swing / Heavy91 / `sf_*` tuning** (`strategy_adaptation.json` + `.cursor/rules/sygnif-swing-tuning.mdc`), MTF RSI, swing vs indicator exits

## Part A — Cursor / Claude execution modes

Use **subagents** (`Agent`) or **Task** where your client supports it; otherwise run the same steps inline.

### 1. Quick market check

Trigger: "market", "prices", "movers", "what's happening"

- `GET https://api.bybit.com/v5/market/tickers?category=spot`
- Top ~15 by volume: price, 24h change; top 5 gainers + losers
- Compact, mobile-friendly, UTC timestamp

### 2. Coin research

Trigger: "research BTC", "analyze ETH"

Spawn parallel work:

- **TA:** Bybit klines `.../kline?category=spot&symbol={TICKER}USDT&interval=60&limit=200` — RSI_14, EMA 9/21/50/200, BB(20,2), MACD(12,26,9), VWAP, S/R
- **News:** RSS CoinTelegraph, CoinDesk, CryptoPanic token feed; WebSearch "{TICKER} crypto news today"
- **Synthesis:** Market status, technical outlook, sentiment, verdict; keep tight unless user wants depth
- If the user cares about **Sygnif entries**, map conclusions to **current tag thresholds** (TA score, sentiment band, volume gates) by checking the strategy file — don’t invent gates that aren’t in code.

### 3. Investment plays

Trigger: "plays", "opportunities", "what to buy"

- Bybit tickers + BTC 1h context
- Exactly **3** plays: type, entry, TP, SL, risk, timeframe, bull/bear case, kill criteria

Full prompt template: `finance_agent/AI Upload/crypto-research/agent-prompts/crypto_investment_plays_agent_prompt.md`

### 4. Strategy exploration (GitNexus)

Trigger: "NFI", "Sygnif", "entry tag", "exit", "strategy"

Use GitNexus MCP directly:

```
READ gitnexus://repos
gitnexus_query({query: "entry conditions"})
gitnexus_context({name: "populate_entry_trend"})
READ gitnexus://repo/{name}/process/{name}
```

**Repos (typical paths on Gianluca’s host):**

- NostalgiaForInfinity — `~/NostalgiaForInfinity`
- Sygnif — `~/SYGNIF` (or legacy `~/xrp_claude_bot`)

**NFI focus:** tags 1–13, 21–26, 41–53, 61–62, 120 grind; exit helpers; slots; `adjust_trade_position` / DCA.

**Sygnif focus:** `user_data/strategies/SygnifStrategy.py` (class **`SygnifStrategy`** = default **`_SygnifStrategyDefault`** or **`MarketStrategy2`** subclass when **`SYGNIF_STRATEGY_BACKEND=ms2`** — import-time); `user_data/strategies/MarketStrategy2.py`; movers; `SygnifSentiment` / `MarketStrategy2Sentiment`; failure swing **`sf_*`** (`strategy_adaptation.json`); tag-level SQL (`scripts/merge_backup_trade_analysis.sql`).

### 5. Comprehensive research

Trigger: "comprehensive", "full report", "deep analysis"

- Orchestrate market + TA + news + plays + macro (see `finance_agent/AI Upload/crypto-research/workflows/`)
- Optional file outputs under `outputs/YYYY-MM-DD_HH-MM-SS/` per `crypto-research/SKILL.md`

### 6. Macro correlation

Trigger: "macro", "fed", "correlation", "DXY"

Prompt: `finance_agent/AI Upload/crypto-research/agent-prompts/macro_crypto_correlation_scanner_agent_prompt.md`

---

## Part B — `finance_agent` Telegram bot parity

The live bot is `finance_agent/bot.py`. When the user’s ask matches a command, mirror that behavior (Bybit spot, USDT pairs, filters below).

| User intent | Bot command | Notes |
|-------------|-------------|--------|
| Market snapshot | `/market` | Top volume, prices, changes |
| Bull/bear read + AI | `/tendency` | Uses Haiku when configured |
| Full TA + strategy signals | `/ta <TICKER>` | Aligns with Sygnif TA stack |
| Active entry signals | `/signals` | Scans top universe |
| Signals + news + ranking | `/scan` | Heavier |
| Full AI research | `/research <TICKER>` | TA + news + sentiment |
| Three structured plays | `/plays` | Same family as Part A §3 |
| Top movers | `/movers` | Gainers/losers |
| Headlines | `/news` | RSS |
| Open trades + context | `/overview` | Needs Trade Overseer |
| HOLD/TRAIL/CUT | `/evaluate` | Needs overseer + AI |

### Sygnif TA score (0–100)

Mirrors `_calculate_ta_score_vectorized()` conceptually:

| Block | Role |
|-------|------|
| RSI 14 | ±15 |
| RSI 3 momentum | ±10 |
| EMA cross (9/26) | ±10 |
| Bollinger | ±8 |
| Aroon | ±8 |
| StochRSI | ±5 |
| CMF | ±5 |
| Volume ratio | ±3 |

**Bands:** ≥65 bullish (`strong_ta` long zone); 55–64 lean bull; 45–54 neutral; 35–44 lean bear; ≤25 bearish (`strong_ta_short` zone). **Ambiguous / Claude zones:** long-oriented ~40–70 TA, short-oriented ~30–60 + sentiment — see live strategy for exact gates.

### Signal names (high level)

| Signal | Side | Idea |
|--------|------|------|
| `strong_ta_long` | Long | Strong TA + volume confirmation |
| `strong_ta_short` | Short | Very weak TA |
| `ambiguous_long` / `ambiguous_short` | Either | Mid TA + sentiment path |
| `sf_long` / `sf_short` | Long / Short | Swing failure at S/R (see **Failure swing** below) |

Exits (examples): Williams %R extremes — confirm against `SygnifStrategy.py` / `MarketStrategy2.py` (same routing).

### Failure swing (5m) — Heavy91-style stack + `sf_*` tuning

**Ground truth:** `user_data/strategies/SygnifStrategy.py`, `user_data/strategies/MarketStrategy2.py` (parallel stack + **MarketStrategy2Sentiment**; often **Docker futures**), and root `SygnifStrategy.py` if the repo keeps a sync copy.

**Concept:** Stop-hunt / false-break on **5m**: rolling **S/R** over **`sf_lookback_bars`** (default 48×5m ≈ 4h), shifted prior highs/lows, stable level, wick through + close back, **`sf_vol_filter_min`** vs distance from **EMA_120**. **`sf_sl_pct`** / **`sf_tp_ema`** drive exits (`_exit_swing_failure`, `exit_sf_*`). Inspired by [Heavy91 “Failure Swing”](https://github.com/Heavy91/TradingView_Indicators) — **not** a Pine port.

**Entry tags (last candle only in `populate_entry_trend`):**

| Tag | Meaning |
|-----|---------|
| `swing_failure` / `swing_failure_short` | Pattern; TA on the weak side of **`sf_ta_split`**; **`custom_exit`** = swing TP/SL only. |
| `claude_swing` / `claude_swing_short` | Pattern + TA on confirming side of **`sf_ta_split`**; swing exit first, then **may** use Williams/RSI paths. |

**Hot-reload (`user_data/strategy_adaptation.json` → `overrides`):** Clamped in `user_data/strategy_adaptation.py` (`DEFAULTS`, `BOUNDS`). Swing-related keys include **`max_slots_swing`**, **`sf_lookback_bars`**, **`sf_vol_filter_min`**, **`sf_sl_base`**, **`sf_sl_vol_scale`**, **`sf_tp_vol_scale`**, **`sf_ta_split`**, plus existing TA/sentiment keys. Reload ~60s in `bot_loop_start`; no restart. **Cursor workflow:** `.cursor/rules/sygnif-swing-tuning.mdc`.

**Freqtrade `strategy_parameters`:** Optional override of the same class attribute names in config.

**Not in code:** Earlier doc **`sf_enhance_enabled` / `sf_rsi_mtf_*` / `sf_vol_zone_*` / `sf_mtf_ma_*`** blocks were **design-only**; SF uses normal merged **MTF RSI** columns and global protections, not separate SF-only enhancement switches.

**MTF RSI vs TradingView “weekly”:** `info_timeframes` = **15m / 1h / 4h / 1d** merged into 5m. Weekly is not merged unless explicitly added.

**Tag-level SQL:** `scripts/merge_backup_trade_analysis.sql`; touch rates: `user_data/logs/touch_rate_tracker.jsonl`.

When the user asks **swing vs indicator interruption**, cite **`custom_exit`** for `swing_failure` vs `claude_swing` and **tag-level stats**.

### Leverage tiers (reference)

Majors (BTC, ETH, SOL, XRP): up to 5×; others often 3×; reduce on elevated ATR (e.g. cap 3× if ATR > 2%, 2× if > 3%) — verify in code.

### Pair filters (bot scans)

USDT only; exclude stables and leveraged tokens; turnover floors vary by command ($500K–$2M).

---

## Integrations

| Service | Default | Role |
|---------|---------|------|
| Trade Overseer | `http://127.0.0.1:8090` | `/overview`, `/evaluate`, `/plays` POST |
| Finance briefing HTTP | `http://127.0.0.1:8091` | `GET /briefing?symbols=BTC,ETH`, `GET /health` for overseer LLM |

Dockerized overseer often uses `host.docker.internal` to reach a host-run finance agent (see repo `docker-compose.yml`).

### Environment (bot + AI features)

| Variable | Purpose |
|----------|---------|
| `FINANCE_BOT_TOKEN` | Telegram |
| `TELEGRAM_CHAT_ID` | Allowed chat |
| `ANTHROPIC_API_KEY` | Haiku features in bot |
| `CURSOR_*` | Cursor Cloud API (when bot uses same stack as Cursor worker) |

---

## Data sources

| Source | URL / tool | Use |
|--------|------------|-----|
| Bybit tickers | `/v5/market/tickers?category=spot` | Prices, 24h stats |
| Bybit klines | `/v5/market/kline` | OHLCV |
| CoinTelegraph / CoinDesk / CryptoPanic | RSS | News |
| WebSearch | Built-in | Live macro/news |
| GitNexus MCP | `gitnexus_query`, `gitnexus_context`, resources | Code |

---

## In-repo references (progressive disclosure)

| Path | Content |
|------|---------|
| `finance_agent/AI Upload/crypto-research/SKILL.md` | Multi-agent research modes, output dirs |
| `finance_agent/AI Upload/crypto-research/agent-prompts/` | Per-agent prompts |
| `finance_agent/AI Upload/technical-analyzer/SKILL.md` | Long-form TA math/patterns (includes DEX/pool examples; **Sygnif live data is Bybit CEX**) |
| `finance_agent/AI Upload/market-movers-scanner/SKILL.md` | Movers / scanning methodology |
| `finance_agent/bot.py` | Ground truth for commands, filters, and indicator code |
| `user_data/strategy_adaptation.py` | Bounded overrides loader (`sf_*`, slots, sentiment bands) |
| `user_data/strategies/MarketStrategy2.py` | MS2 strategy (sentiment + same SF stack as SygnifStrategy) |
| `.cursor/rules/sygnif-swing-tuning.mdc` | Agent workflow for swing JSON tuning |
| `scripts/merge_backup_trade_analysis.sql` | Merged spot+futures backup SQLite: tag stats, `claude_swing` by `exit_reason`, median hold |

---

## Output rules

- Compact, mobile-friendly when possible
- **UTC timestamp** on snapshots
- Bold headers; inline code for numbers/tickers
- Prefer **probability framing**, not personalized investment advice unless the user explicitly asks for that style
- When code and this skill disagree, **trust the repository** and cite the file you checked

---

## Version note

**v2 (unified):** Merges former **finance-consultant** (strategy/bot alignment) into **finance-agent** (GitNexus + Cursor research). Older docs may still say "finance-consultant"; treat this skill as the single source of truth.

**v3 (connected domain):** Explicit policy — **code + finance are one workflow** for Sygnif; market answers and code edits should cross-reference each other instead of living in separate “modes.”

**v4 (failure swing doc):** Documents **5m SF + `sf_*`**, Heavy91 alignment, tag/exit routing, backup SQL.

**v5 (adaptation + MS2):** **`sf_*` in `strategy_adaptation.json`**, **`MarketStrategy2.py`** as ground truth alongside **`SygnifStrategy.py`**; removed obsolete **`sf_enhance_*`** implementation claims; added **`.cursor/rules/sygnif-swing-tuning.mdc`**.
