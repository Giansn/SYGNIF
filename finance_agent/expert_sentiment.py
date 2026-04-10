"""
Deterministic Sygnif sentiment adjustment (-20..+20) for /sygnif/sentiment.
Uses headline + live-tape keyword signals (finance-agent rules) — no LLM.
"""

from __future__ import annotations

import math

# Longer phrases first so substring checks favor specific events.
_POSITIVE = sorted(
    (
        "all-time high",
        "record high",
        "etf approval",
        "etf approved",
        "bullish breakout",
        "institutional buying",
        "institutional demand",
        "partnership",
        "integration",
        "listing",
        "airdrop",
        "upgrade",
        "mainnet",
        "breakout",
        "bullish",
        "rally",
        "surge",
        "soar",
        "gains",
        "uptrend",
        "accumulation",
        "inflows",
        "inflow",
        "adoption",
        "milestone",
        "recovery",
        "rebound",
    ),
    key=len,
    reverse=True,
)

_NEGATIVE = sorted(
    (
        "security breach",
        "smart contract exploit",
        "rug pull",
        "rugpull",
        "sec charges",
        "subpoena",
        "investigation",
        "delisting",
        "delist",
        "bankruptcy",
        "bankrupt",
        "liquidation cascade",
        "liquidations",
        "liquidation",
        "outflows",
        "outflow",
        "exploit",
        "breach",
        "hacked",
        "hack",
        "stolen",
        "lawsuit",
        "lawsuits",
        "indictment",
        "fraud",
        "scam",
        "ban",
        "banned",
        "bearish",
        "selloff",
        "sell-off",
        "plunge",
        "crash",
        "dump",
        "warning",
        "halt",
        "paused withdrawals",
        "withdrawals paused",
    ),
    key=len,
    reverse=True,
)


def _score_text_block(low: str, weight: float) -> tuple[float, float, int, int]:
    """Return (bull_points, bear_points, bull_hits, bear_hits) for one block."""
    bull = bear = 0.0
    bh = eh = 0
    for phrase in _POSITIVE:
        if phrase in low:
            bull += weight
            bh += 1
    for phrase in _NEGATIVE:
        if phrase in low:
            bear += weight
            eh += 1
    return bull, bear, bh, eh


def expert_sygnif_sentiment_score(
    token: str,
    ta_score: float,
    headlines: list[str],
    live_text: str = "",
) -> tuple[float, str]:
    """
    Map RSS/headlines + optional live snapshot text to a single adjustment score.
    TA is used only as a light moderator when news and TA strongly disagree.
    """
    token_lc = (token or "").strip().lower()
    bull_t = bear_t = 0.0
    total_bh = total_eh = 0
    n_lines = 0

    for h in headlines:
        if not isinstance(h, str) or not h.strip():
            continue
        n_lines += 1
        line = h.strip().lower()
        w = 1.85 if token_lc and token_lc in line else 1.0
        b, e, bh, eh = _score_text_block(line, w)
        bull_t += b
        bear_t += e
        total_bh += bh
        total_eh += eh

    if live_text and live_text.strip():
        lw = live_text.strip().lower()
        w = 1.25
        b, e, bh, eh = _score_text_block(lw, w)
        bull_t += b
        bear_t += e
        total_bh += bh
        total_eh += eh

    net = bull_t - bear_t
    if net == 0.0 and n_lines == 0 and not (live_text or "").strip():
        return 0.0, "Finance-agent expert: no headlines or live context; neutral."

    # Scale net signal into -20..+20 (sublinear so many weak hits don't explode).
    raw = 6.0 * math.tanh(net / 4.5)
    score = max(-20.0, min(20.0, raw))

    # Moderate: strong TA vs contrary news — dampen the adjustment slightly.
    if ta_score >= 62 and score < 0:
        score *= 0.72
    elif ta_score <= 38 and score > 0:
        score *= 0.72

    score = max(-20.0, min(20.0, score))
    reason = (
        f"Finance-agent expert: net headline/live signal "
        f"(~{total_bh} bull / ~{total_eh} bear cues, {n_lines} headlines); "
        f"TA context {ta_score:.0f}/100."
    )
    return round(score, 2), reason
