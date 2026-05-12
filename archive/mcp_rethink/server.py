#!/usr/bin/env python3
"""
Sygnif rethink MCP — stdio server exposing Bybit-backed TA scan (mechanical only).

Requires: pip install mcp requests pandas pandas_ta numpy

Cursor mcp.json (add):
  "sygnif-rethink": {
    "command": "python",
    "args": ["C:\\\\Users\\\\giank\\\\sygnif\\\\mcp_rethink\\\\server.py"]
  }
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "user_data" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import rethink_sim as rs  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP(
    "sygnif-rethink",
    instructions=(
        "Mechanical Sygnif/NFI-style scan: Bybit LINEAR 5m OHLCV, TA score aligned with SygnifStrategy, "
        "swing_failure flags, NY counterfactuals, and optional predictability study vs forward returns. "
        "Network required; no API keys."
    ),
)


@mcp.tool()
def rethink_scan(
    limit: int = 2000,
    symbols: str = "BTCUSDT,ETHUSDT,SOLUSDT",
) -> str:
    """
    Pull fresh data from Bybit (network) and return JSON: per-bar counts,
    NY-window overlap for strong_long/strong_short/sf_*, and last-bar snapshot.

    Args:
        limit: 5m candles per symbol (200-5000).
        symbols: Comma-separated linear symbols (e.g. BTCUSDT,ETHUSDT).
    """
    syms = tuple(s.strip().upper() for s in symbols.split(",") if s.strip())
    data = rs.json_safe(rs.run_network_scan(limit=limit, symbols=syms))
    return json.dumps(data, indent=2)


# Smaller than default 2000 but >= 300: build_symbol_frame requires merge length >= 300 for alts (rethink_sim).
_LAST_BAR_LIMIT = 300


@mcp.tool()
def rethink_last_bar(
    symbols: str = "BTCUSDT,ETHUSDT,SOLUSDT",
) -> str:
    """Uses 300 bars (vs 2000 full scan): less fetch/CPU; JSON is last-bar snapshot only."""
    syms = tuple(s.strip().upper() for s in symbols.split(",") if s.strip())
    data = rs.json_safe(rs.run_network_scan(limit=_LAST_BAR_LIMIT, symbols=syms))
    slim = {
        "generated_utc": data["generated_utc"],
        "source": data["source"],
        "last_bar": data["last_bar"],
        "notes": data["notes"],
    }
    return json.dumps(slim, indent=2)


@mcp.tool()
def rethink_predictability(
    top_n: int = 15,
    target_bars: int = 6000,
    horizons: str = "12,48,288",
    symbols: str = "",
) -> str:
    """
    Paginated history, then per-pair forward % returns after strong_long/short and sf_* vs all-bar baseline.
    If `symbols` is non-empty CSV, uses that list (BTC first). If empty, uses top `top_n` USDT pairs by turnover.

    Args:
        top_n: Liquidity-ranked pair count when symbols omitted.
        target_bars: 5m candles to load per symbol (many Bybit requests; 500-20000).
        horizons: Forward steps in 5m bars, e.g. 12=~1h, 48=~4h, 288=~24h.
        symbols: Optional CSV e.g. BTCUSDT,ETHUSDT. Empty = turnover list.
    """
    hs = tuple(int(x.strip()) for x in horizons.split(",") if x.strip())
    sym_list = None
    if symbols.strip():
        sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    data = rs.json_safe(
        rs.run_predictability_study(
            top_n=top_n,
            target_bars=target_bars,
            horizons=hs,
            symbols=sym_list,
        )
    )
    compact = {
        "generated_utc": data["generated_utc"],
        "how_many_make_sense": data["how_many_make_sense"],
        "how_many_pass_tight": data.get("how_many_pass_tight"),
        "pass_counts": data["pass_counts"],
        "pass_counts_tight": data.get("pass_counts_tight"),
        "tight_methodology": data.get("tight_methodology"),
        "pairs_ok": data["pairs_ok"],
        "btc_bars_loaded": data["btc_bars_loaded"],
        "methodology": data["methodology"],
        "compact": rs.compact_predictability_report(data),
        "errors": data.get("errors", []),
    }
    return json.dumps(compact, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
