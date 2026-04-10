"""
Deterministic Sygnif finance-agent narratives (no LLM).
Used by Telegram commands instead of Haiku.
"""

from __future__ import annotations

from expert_sentiment import expert_sygnif_sentiment_score


def expert_tendency_insight(
    bull_count: int,
    bear_count: int,
    neutral_count: int,
    headlines: list[str],
    avg_ta_score: float,
) -> str:
    """Short narrative from scan counts + headline expert score."""
    sc, reason = expert_sygnif_sentiment_score("BTC", avg_ta_score, headlines, "")
    bias = "mixed"
    if bull_count > bear_count + 1 and avg_ta_score >= 52:
        bias = "constructive"
    elif bear_count > bull_count + 1 and avg_ta_score <= 48:
        bias = "cautious"
    parts = [
        f"Regime read: *{bias}* ({bull_count} bull / {bear_count} bear / {neutral_count} neutral on scan).",
        f"Headline tilt (expert): `{sc:+.1f}` — _{reason}_",
    ]
    return " ".join(parts)


def expert_research_markdown(
    ticker: str,
    ind: dict,
    sig: dict,
    price: float,
    change_24h: float,
    vol_24h: float,
    headlines: list[str],
) -> str:
    """Markdown report from TA + strategy + expert headline score."""
    score = sig.get("ta_score", 50)
    entries = ", ".join(sig.get("entries") or []) or "None"
    exits = ", ".join(sig.get("exits") or []) or "None"
    lev = sig.get("leverage", 1.0)
    atr = sig.get("atr_pct", 0.0)

    sent, s_reason = expert_sygnif_sentiment_score(ticker, float(score), headlines, "")

    if not ind:
        return (
            f"**Market status:** `{ticker}` at `${price:.4g}` ({change_24h:+.1f}% 24h), "
            f"vol ~${vol_24h/1e6:.1f}M — insufficient TA history.\n\n"
            f"**News & expert tilt:** `{sent:+.1f}` — {s_reason}"
        )

    news_bullets = "\n".join(f"- {h}" for h in (headlines or [])[:5]) or "- _No recent headlines._"

    verdict = "Neutral"
    if score >= 62 and sent >= 0:
        verdict = "Lean bullish (TA + headline tilt agree)"
    elif score <= 38 and sent <= 0:
        verdict = "Lean bearish"
    elif score >= 55 and sent < -3:
        verdict = "Mixed — strong TA vs negative headlines"
    elif score <= 45 and sent > 3:
        verdict = "Mixed — weak TA vs positive headlines"

    return f"""**1. Market status**
Price `${price:.4g}` ({change_24h:+.1f}% 24h), volume ~`${vol_24h/1e6:.1f}M` USDT. Trend: **{ind.get('trend', '?')}**.

**2. Technical outlook**
- RSI14 `{ind.get('rsi', 0):.1f}` ({ind.get('rsi_signal', '')}), Williams %R `{ind.get('willr', 0):.0f}`
- MACD: {ind.get('macd_signal_text', '?')}, CMF `{ind.get('cmf', 0):.3f}`
- Support `{ind.get('support', 0):.4g}` / Resistance `{ind.get('resistance', 0):.4g}`
- BB: {ind.get('bb_position', '?')}

**3. Strategy view**
- TA score **{score}/100**, entries: `{entries}`, exits: `{exits}`
- Leverage tier **{lev:.0f}x** (ATR ~{atr:.1f}%)

**4. News & sentiment (expert)**
- Expert adjustment score: **{sent:+.1f}** — _{s_reason}_
{news_bullets}

**5. Verdict**
{verdict}. Watch `{ind.get('resistance', 0):.4g}` / `{ind.get('support', 0):.4g}` for continuation or failure."""


def _play_line(n: int, sym: str, side: str, typ: str, p: dict, ind: dict, sig: dict) -> str:
    price = p.get("price", 0)
    ch = p.get("change", 0)
    atr = float(sig.get("atr_pct") or 1.0)
    slip = max(price * (atr / 100.0) * 1.5, price * 0.003)
    tp_pct = min(8.0, max(2.0, atr * 2))
    sl_pct = min(5.0, max(1.2, atr * 1.2))
    if side == "Long":
        entry = price - slip * 0.3
        tp = price * (1 + tp_pct / 100)
        sl = price * (1 - sl_pct / 100)
    else:
        entry = price + slip * 0.3
        tp = price * (1 - tp_pct / 100)
        sl = price * (1 + sl_pct / 100)
    risk = "Medium" if atr > 3 else "Low" if atr < 1.5 else "Medium"
    return (
        f"**Play #{n}: {sym}**\n"
        f"Type: `{typ}` | Side: **{side}** | Risk: {risk}\n"
        f"- *Thesis:* TA `{sig['ta_score']}` with `{sig['entries'][0] if sig.get('entries') else 'setup'}`; 24h `{ch:+.1f}%`.\n"
        f"- *Entry:* ~`${entry:.4g}`\n"
        f"- *TP:* ~`${tp:.4g}` (~{tp_pct:.1f}%)\n"
        f"- *SL:* ~`${sl:.4g}` (~{sl_pct:.1f}%)\n"
        f"- *Timeframe:* Days (1h structure)\n"
    )


def expert_plays_from_scan(
    top_by_vol: list[dict],
    btc_ind: dict,
    btc_sig: dict,
    btc_df_ok: bool,
) -> str:
    """Pick up to 3 actionable plays from volume leaders + TA."""
    candidates = []
    for p in top_by_vol:
        sym = p["sym"]
        # Indicators must be precomputed by caller — we only receive enriched list
        ta_score = p.get("_ta_score")
        entries = p.get("_entries") or []
        ind = p.get("_ind") or {}
        sig = p.get("_sig") or {}
        if ta_score is None:
            continue
        pri = 0
        typ = "mean_reversion"
        side = "Long"
        if "strong_ta_long" in entries or "sf_long" in entries:
            pri = ta_score + 30
            typ = "strong_ta" if "strong_ta_long" in entries else "swing_failure"
            side = "Long"
        elif "strong_ta_short" in entries or "sf_short" in entries:
            pri = (100 - ta_score) + 30
            typ = "strong_ta_short" if "strong_ta_short" in entries else "swing_failure"
            side = "Short"
        elif "ambiguous_long" in entries:
            pri = ta_score + 5
            typ = "ambiguous_long"
            side = "Long"
        elif "ambiguous_short" in entries:
            pri = (100 - ta_score) + 5
            typ = "ambiguous_short"
            side = "Short"
        else:
            continue
        candidates.append((pri, sym, side, typ, p, ind, sig))

    candidates.sort(key=lambda x: -x[0])
    picks = candidates[:3]
    if not picks:
        return "_No strategy-aligned plays in top volume list right now._"

    lines = []
    if btc_df_ok and btc_ind:
        lines.append(
            f"_BTC_: `${btc_ind.get('price', 0):,.0f}` {btc_ind.get('trend', '')} "
            f"RSI `{btc_ind.get('rsi', 0):.0f}` TA `{btc_sig.get('ta_score', 0)}`"
        )
        lines.append("")
    for i, (_, sym, side, typ, p, ind, sig) in enumerate(picks, 1):
        lines.append(_play_line(i, sym, side, typ, p, ind, sig))
        lines.append("")
    return "\n".join(lines).rstrip()


def expert_scan_ranking_rows(scan_rows: list[dict]) -> str:
    """
    scan_rows: list of dicts with keys sym, price, trend, ta_score, entry, rsi, willr, lev, news_str
    """
    if not scan_rows:
        return ""

    def key(r):
        s = r.get("ta_score", 50)
        e = (r.get("entry") or "").lower()
        boost = 0
        if "strong_ta" in e:
            boost = 20
        elif "sf_" in e:
            boost = 15
        elif "ambiguous" in e:
            boost = 5
        return -(s + boost)

    ranked = sorted(scan_rows, key=key)
    out = []
    for i, r in enumerate(ranked[:6], 1):
        side = "Short" if "short" in (r.get("entry") or "").lower() else "Long"
        out.append(
            f"#{i} `{r['sym']}` {side} — TA:{r.get('ta_score')} {r.get('entry')} "
            f"RSI:{r.get('rsi', 0):.0f}"
        )
    return "\n".join(out)


def expert_trade_action(
    profit_pct: float,
    ind: dict | None,
    sig: dict | None,
) -> tuple[str, str]:
    """HOLD | TRAIL | CUT with short reason from TA + P/L."""
    if ind is None or sig is None:
        if profit_pct <= -2:
            return "CUT", "loss beyond tolerance"
        if profit_pct >= 3:
            return "TRAIL", "lock gains"
        return "HOLD", "no TA context"

    rsi = float(ind.get("rsi") or 50)
    wr = float(ind.get("willr") or -50)
    ta = int(sig.get("ta_score") or 50)

    if profit_pct <= -2.5:
        return "CUT", "deep loss vs plan"
    if profit_pct >= 4.0:
        return "TRAIL", "sizeable gain protect"
    if profit_pct >= 2.0 and (rsi > 72 or wr > -8):
        return "TRAIL", "overbooked take profit"
    if profit_pct < 0 and ta < 38 and rsi < 42:
        return "CUT", "weak TA pressure"
    if profit_pct < -1.0 and rsi < 35:
        return "CUT", "momentum failing"
    if ta >= 60 and profit_pct >= 0.5:
        return "HOLD", "TA supports hold"
    if ta <= 35 and profit_pct <= 0.5:
        return "HOLD", "manage short risk"
    return "HOLD", "no decisive trigger"


def expert_evaluate_lines(
    trades: list[dict],
    ta_map: dict[str, dict],
) -> str:
    """Same line format previously parsed from Claude: PAIR ACTION reason"""
    lines = []
    for t in sorted(trades, key=lambda x: x.get("profit_pct", 0)):
        pair = t["pair"].replace("/USDT:USDT", "").replace("/USDT", "")
        pct = float(t.get("profit_pct", 0))
        m = ta_map.get(pair)
        ind = m.get("ind") if m else None
        sig = m.get("sig") if m else None
        act, reason = expert_trade_action(pct, ind, sig)
        lines.append(f"{pair} {act} {reason}")
    return "\n".join(lines)
