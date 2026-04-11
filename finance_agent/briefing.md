# Finance agent & BTC specialist — briefing contract and neural evaluation nodes

**Purpose:** Single reference for the **pipe-delimited briefing** consumed by overseer, Cursor, and small models — plus **evaluation nodes**: discrete checks (logical “gates”) for any **neural** or LLM output that summarizes, extends, or trades on that briefing. Nodes are **rubric checkpoints**, not OpenVINO IR (see `network/docs/AGENT_NODE_NETWORK_DRAFT.md` for hardware split graphs).

---

## 1. Sources of truth

| Surface | Contract | Implementation |
|--------|-----------|----------------|
| HTTP | `GET /briefing` and `GET /briefing?symbols=BTC,ETH,...` | `finance_agent/bot.py` → `_briefing()`, `_BriefingHandler` |
| Telegram | `/finance-agent briefing` | `cmd_briefing()` — same **pipe body** as HTTP + optional **NewHedge** BTC–alts correlation line + footer |
| BTC shortcut | `/btc` | `cmd_btc()` = `cmd_ta("BTC")` + snapshot footer + optional **NewHedge** (`newhedge_client.py`, `NEWHEDGE_API_KEY`) |
| Live TA / score / tags | Always | `calc_indicators`, `calc_ta_score`, `detect_signals` in `finance_agent/bot.py` |
| Offline BTC bundle | Stale-safe context | `finance_agent/btc_specialist/data/` (`manifest.json`, `btc_sygnif_ta_snapshot.json`, OHLCV JSON) — refresh: `python3 finance_agent/btc_specialist/scripts/pull_btc_context.py` |

Sygnif TA **bands and tag names** must match `detect_signals`, not generic TradingView defaults — see `.cursor/skills/btc-specialist/SKILL.md` and `btc_specialist/PROMPT.md`.

---

## 2. Briefing line format (per symbol)

Each non-empty line is **one asset**, fields separated by **`|`** (pipe). Built from 1h Bybit klines (200 candles) for `BTCUSDT`, `ETHUSDT`, plus optional extra `SYMBOLUSDT` (capped in code).

**Segments (conceptual order):**

1. **Header:** `{SYMBOL} {PRICE} {TREND}` — trend text may shorten “Strong ” → `s-`.
2. **Oscillators:** `RSI:{n} WR:{willr} StRSI:{stochrsi_k}`
3. **Flow / momentum:** `MACD:{macd_signal_text} CMF:{cmf}`
4. **Levels:** `S:{support} R:{resistance}` (compact numeric formatting)
5. **Sygnif block:** `TA:{ta_score} {primary_entry_tag} {leverage}x` and optional ` EXIT:{exit_tag}`

**Example shape (illustrative, not a live quote):**

```text
BTC $97,000 s-uptrend|RSI:62 WR:-28 StRSI:48|MACD:bull CMF:+0.08|S:95000 R:98500|TA:58 claude_s0 5x
ETH $3,200 downtrend|RSI:48 WR:-45 StRSI:52|MACD:bear CMF:-0.05|S:3100 R:3300|TA:45 none 3x
```

**Parser rule:** Split on `|` first; then split the last segment on spaces for `TA:`, entry tag, leverage, optional `EXIT:`.

---

## 3. Neural evaluation nodes — how to use

- **When:** After an LLM (or human) produces text that **quotes, interprets, or trades on** briefing, `/btc`, or `btc_sygnif_ta_snapshot.json`.
- **How:** Run each applicable node; record **PASS** / **WARN** / **FAIL** and a one-line reason. **FAIL** on any **N1–N8** or **B1–B7** node when quoted facts contradict the source.
- **WARN:** Staleness, ambiguous wording, or missing caveats — not a numeric lie.

---

## 4. Finance-agent briefing nodes (`N1`–`N8`)

Multi-line `/briefing` or HTTP body (BTC + ETH + optional symbols). Parent input = **authoritative briefing text** from server.

| ID | Node name | Check |
|----|-----------|--------|
| **N1** | **Pipe schema** | Every substantive line has the expected number of `\|` segments and a final `TA:` field with numeric score. |
| **N2** | **Symbol integrity** | Only symbols present in the briefing (or explicitly requested via `symbols=`) are discussed as “in briefing”; no invented tickers. |
| **N3** | **Numeric fidelity** | Price, RSI, WR, StRSI, S, R, `TA:` score, and `Nx` leverage match the source line **exactly** when quoted; rounding policy matches server (integers where server emits integers). |
| **N4** | **Tag fidelity** | Entry tag (`strong_ta_long`, `claude_s0`, `none`, …) and optional `EXIT:` match the source line; model does not swap BTC vs ETH tags across lines. |
| **N5** | **Level logic** | If the model infers “near support/resistance,” it must not invert S/R vs last price relative to the quoted numbers. |
| **N6** | **Horizon honesty** | Model states briefing is **snapshot / 1h-based** where relevant; does not claim sub-minute precision. |
| **N7** | **Query contract** | If `symbols=` was used, evaluation references the same symbol set (max symbols per server logic). |
| **N8** | **Cross-asset hygiene** | ETH conclusions are not silently attributed to BTC (or vice versa) unless the user asked for a pair read. |

---

## 5. BTC specialist nodes (`B1`–`B7`)

Parent input = **`/btc` or `cmd_ta("BTC")` block** and/or **`btc_specialist/data/*.json`**.

| ID | Node name | Check |
|----|-----------|--------|
| **B1** | **Scope** | Analysis is **BTC spot (`BTCUSDT`)** on Bybit unless user broadened scope. |
| **B2** | **Live vs offline** | If using JSON files, `manifest.json` `generated_utc` is cited; model does not present offline OHLC as “live tick.” |
| **B3** | **Parity with `/btc`** | When comparing to Telegram, acknowledges **`/btc` = `/ta BTC` + manifest footer** (see `cmd_btc`). |
| **B4** | **Sygnif semantics** | Score bands and candidate tags align with `detect_signals` / strategy docs — not TradingView defaults. |
| **B5** | **Snapshot consistency** | If `btc_sygnif_ta_snapshot.json` exists, quoted score/signals match file; if file missing/outdated, model says so. |
| **B6** | **No orders** | Output does not imply order placement or `dry_run` changes unless user explicitly requested ops. |
| **B7** | **OHLC discipline** | Structural claims (higher highs, range) are grounded in `btc_1h_ohlcv.json` / `btc_daily_90d.json` when not using live API. |

---

## 6. Combined run (Telegram `cmd_briefing`)

Telegram wraps the same `_briefing()` body plus optional **NewHedge** line, then **HTTP hint** and **`_btc_specialist_snapshot_footer()`**. Evaluators should:

1. Run **N1–N8** on the fenced briefing lines.
2. Run **B2, B6** (and **B5** if snapshot files are in context) on the footer / any BTC-only narrative.

---

## 7. Machine-readable node index (optional parsers)

```json
{
  "briefing_contract_version": "1",
  "nodes": [
    {"id": "N1", "scope": "finance_briefing", "severity": "fail", "name": "pipe_schema"},
    {"id": "N2", "scope": "finance_briefing", "severity": "fail", "name": "symbol_integrity"},
    {"id": "N3", "scope": "finance_briefing", "severity": "fail", "name": "numeric_fidelity"},
    {"id": "N4", "scope": "finance_briefing", "severity": "fail", "name": "tag_fidelity"},
    {"id": "N5", "scope": "finance_briefing", "severity": "warn", "name": "level_logic"},
    {"id": "N6", "scope": "finance_briefing", "severity": "warn", "name": "horizon_honesty"},
    {"id": "N7", "scope": "finance_briefing", "severity": "fail", "name": "query_contract"},
    {"id": "N8", "scope": "finance_briefing", "severity": "fail", "name": "cross_asset_hygiene"},
    {"id": "B1", "scope": "btc_specialist", "severity": "fail", "name": "scope_btcusdt"},
    {"id": "B2", "scope": "btc_specialist", "severity": "warn", "name": "live_vs_offline"},
    {"id": "B3", "scope": "btc_specialist", "severity": "warn", "name": "telegram_parity"},
    {"id": "B4", "scope": "btc_specialist", "severity": "fail", "name": "sygnif_semantics"},
    {"id": "B5", "scope": "btc_specialist", "severity": "fail", "name": "snapshot_consistency"},
    {"id": "B6", "scope": "btc_specialist", "severity": "fail", "name": "no_orders"},
    {"id": "B7", "scope": "btc_specialist", "severity": "warn", "name": "ohlc_discipline"}
  ]
}
```

---

## 8. Related files

- `finance_agent/bot.py` — `_briefing`, `cmd_briefing`, `cmd_btc`, `_btc_specialist_snapshot_footer`
- `finance_agent/btc_specialist/README.md` — data layout
- `finance_agent/btc_specialist/PROMPT.md` — BTC sub-agent stub
- `.cursor/skills/btc-specialist/SKILL.md` — Cursor skill
