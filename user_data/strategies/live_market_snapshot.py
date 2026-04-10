"""
Live Bybit context for sentiment — mirrors finance_agent /finance-agent market checks.

Public market data only (no API keys). Used by MarketStrategy2Sentiment.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BYBIT = "https://api.bybit.com/v5"


def _ticker_line(sess: requests.Session, category: str, symbol: str, label: str) -> str:
    try:
        r = sess.get(
            f"{BYBIT}/market/tickers",
            params={"category": category, "symbol": symbol},
            timeout=8,
        )
        r.raise_for_status()
        lst = r.json().get("result", {}).get("list") or []
        if not lst:
            return f"{label}: (no ticker row)"
        t = lst[0]
        last = t.get("lastPrice", "?")
        p24 = float(t.get("price24hPcnt", 0) or 0) * 100.0
        turn = float(t.get("turnover24h", 0) or 0)
        return f"{label} [{category}] last={last} 24h%={p24:+.2f}% turnover24h≈${turn:,.0f}"
    except Exception as e:
        logger.warning("Bybit ticker %s %s: %s", category, symbol, e)
        return f"{label} [{category}]: error {e}"


def _kline_roll_summary(sess: requests.Session, symbol: str, label: str, bars: int = 24) -> str:
    try:
        r = sess.get(
            f"{BYBIT}/market/kline",
            params={
                "category": "spot",
                "symbol": symbol,
                "interval": "60",
                "limit": str(max(bars + 2, 30)),
            },
            timeout=8,
        )
        r.raise_for_status()
        rows = r.json().get("result", {}).get("list") or []
        if len(rows) < 2:
            return f"{label} kline: insufficient data"
        # Bybit: newest first
        def cl(i: int) -> float:
            return float(rows[i][4])

        last_c = cl(0)
        n = min(bars, len(rows) - 1)
        old_c = cl(n)
        roll_pct = ((last_c / old_c) - 1.0) * 100.0 if old_c else 0.0
        highs = [float(x[2]) for x in rows[: n + 1]]
        lows = [float(x[3]) for x in rows[: n + 1]]
        return (
            f"{label} spot 60m×{n + 1}: last={last_c:.6f} roll{n}h≈{roll_pct:+.2f}% "
            f"range_high={max(highs):.6f} range_low={min(lows):.6f}"
        )
    except Exception as e:
        logger.warning("Bybit kline %s: %s", symbol, e)
        return f"{label} kline: error {e}"


def fetch_finance_agent_market_context(
    token: str,
    session: Optional[requests.Session] = None,
) -> str:
    """
    Compact real-time snapshot: spot tape + optional linear perp + BTC context.
    Aligns with finance-agent quick market / coin research (Bybit v5 spot).
    """
    tok = (token or "").strip().upper()
    if not tok:
        return ""

    sess = session if session is not None else requests.Session()
    sym_spot = f"{tok}USDT"

    include_linear = os.environ.get("SENTIMENT_MS2_LINEAR_TICKER", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    lines = [
        f"as_of_utc={datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        _ticker_line(sess, "spot", sym_spot, tok),
        _kline_roll_summary(sess, sym_spot, tok, bars=24),
    ]
    if include_linear:
        lines.append(_ticker_line(sess, "linear", sym_spot, f"{tok}_perp"))

    lines.append(_ticker_line(sess, "spot", "BTCUSDT", "BTC"))
    lines.append(_kline_roll_summary(sess, "BTCUSDT", "BTC", bars=24))

    return "\n".join(lines)
