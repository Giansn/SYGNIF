"""
Single translator: print the BTC swarm **synth** card (human-readable).

Input must use keys from ``swarm_btc_flow_constants`` / ``synthesize_swarm_btc_card`` output.
"""

from __future__ import annotations

import sys
from typing import Any

from swarm_btc_flow_constants import K_AMOUNT_BTC
from swarm_btc_flow_constants import K_BTC_DUMP_RISK_PCT
from swarm_btc_flow_constants import K_BTC_USD_PRICE
from swarm_btc_flow_constants import K_BULL_BEAR
from swarm_btc_flow_constants import K_LEVERAGE
from swarm_btc_flow_constants import K_ORDER_SIGNAL
from swarm_btc_flow_constants import K_PRICE_SYMBOL
from swarm_btc_flow_constants import K_SIDE


def _fmt_price(v: Any) -> str:
    if v is None:
        return "N/A"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "N/A"
    if x <= 0:
        return "N/A"
    return f"{x:,.2f}"


def _fmt_dump(v: Any) -> str:
    if v is None:
        return "N/A"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "N/A"
    return f"{x:.1f}"


def format_swarm_btc_card_lines(synth: dict[str, Any]) -> list[str]:
    """Return lines (no trailing newlines) for the trading card."""
    sym = synth.get(K_PRICE_SYMBOL) or "BTCUSDT"
    px = _fmt_price(synth.get(K_BTC_USD_PRICE))
    lines = [
        f"BTC/USD price: {px} ({sym} ~ USD peg)",
        f"Order: {synth.get(K_ORDER_SIGNAL) or 'N/A'}",
        f"Amount: {synth.get(K_AMOUNT_BTC) or 'N/A'}",
        f"Leverage: {synth.get(K_LEVERAGE) if synth.get(K_LEVERAGE) is not None else 'N/A'}x",
        f"Long/Short: {synth.get(K_SIDE) or 'N/A'}",
        f"BTC Dump risk %: {_fmt_dump(synth.get(K_BTC_DUMP_RISK_PCT))}",
        f"Bull/Bear: {synth.get(K_BULL_BEAR) or 'N/A'}",
    ]
    return lines


def print_swarm_btc_card(synth: dict[str, Any], *, file: Any = None) -> None:
    """Print the one standard card to ``file`` (default stdout)."""
    out = file if file is not None else sys.stdout
    for line in format_swarm_btc_card_lines(synth):
        print(line, file=out, flush=True)
