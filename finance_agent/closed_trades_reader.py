"""
Read closed trades from Freqtrade SQLite (tradesv3.sqlite, tradesv3-futures.sqlite).

Used by scripts/analyze_closed_trades.py and train_sentiment_mlp.py (--freqtrade-db).
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Any


def pair_to_token(pair: str) -> str:
    """BTC/USDT:USDT -> btc, ETH/USDT -> eth."""
    if not pair:
        return "btc"
    base = pair.split("/")[0].strip()
    return base.lower() if base else "btc"


def fetch_closed_trades(
    db_path: Path | str,
    *,
    days: int | None = None,
) -> list[dict[str, Any]]:
    """
    Return closed trades newest-first. `days` None or 0 = no time filter.

    Columns align with trade_overseer/trading_success.fetch_closed_trades.
    """
    path = Path(db_path)
    if not path.is_file():
        return []

    sql = """
        SELECT id, pair, enter_tag, exit_reason, is_short, leverage,
               open_rate, close_rate, close_profit, close_profit_abs,
               stake_amount, open_date, close_date, max_rate, min_rate
        FROM trades
        WHERE is_open = 0
    """
    params: list[Any] = []
    if days is not None and days > 0:
        sql += " AND close_date >= datetime('now', ?)"
        params.append(f"-{days} days")

    sql += " ORDER BY close_date DESC"

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
    return rows


def trade_to_training_sample(trade: dict[str, Any]) -> tuple[str, list[str], str, float]:
    """
    Map one closed trade to (token, headlines, live_tape_snippet, ta_proxy).

    Headlines use the same bullish/bearish lexicon as the expert so the teacher
    score is grounded in PnL + exit/entry metadata.
    """
    tok = pair_to_token(str(trade.get("pair") or ""))
    pr = float(trade.get("close_profit") or 0.0)
    ta = 50.0 + 35.0 * math.tanh(pr * 22.0)
    ta = max(15.0, min(85.0, ta))
    er = (trade.get("exit_reason") or "unknown").replace("_", " ")
    et = (trade.get("enter_tag") or "").replace("_", " ")
    short = bool(trade.get("is_short"))
    side = "short" if short else "long"
    if pr > 0.0001:
        h = (
            f"{tok.upper()} {side} gains profit rally surge; bullish momentum "
            f"exit {er} entry {et}"
        )
    elif pr < -0.0001:
        h = (
            f"{tok.upper()} {side} selloff bearish plunge crash warning "
            f"exit {er} stoploss risk {et}"
        )
    else:
        h = f"{tok.upper()} {side} flat range trade exit {er} {et}"
    lev = trade.get("leverage")
    live = (
        f"closed pnl {pr * 100:.2f}% leverage {lev or 1} "
        f"open {trade.get('open_rate')} close {trade.get('close_rate')}"
    )
    return tok, [h], live, ta
