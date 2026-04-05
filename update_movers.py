#!/usr/bin/env python3
"""
Fetches top 3 gainers and top 3 losers from Bybit USDT pairs.
Writes them to movers_pairlist.json for Freqtrade to include.
Run via cron every 4h or via docker healthcheck.

Usage:
    python update_movers.py                  # spot (default)
    python update_movers.py --category linear  # futures
"""

import argparse
import json
import requests
import sys
from pathlib import Path

BYBIT_TICKERS_URL = "https://api.bybit.com/v5/market/tickers"

EXCLUDE_STABLES = {
    "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDD", "USDT",
    "USDP", "USDS", "XUSD", "USD1", "RLUSD", "AUSD", "EURI",
}
EXCLUDE_PATTERNS = {"2L", "3L", "5L", "2S", "3S", "5S"}


def fetch_tickers(category: str):
    resp = requests.get(BYBIT_TICKERS_URL, params={"category": category}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("retCode") != 0:
        raise RuntimeError(f"Bybit API error: {data.get('retMsg')}")
    return data["result"]["list"]


def filter_usdt_pairs(tickers, min_turnover: float = 100_000):
    filtered = []
    for t in tickers:
        symbol = t.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        base = symbol.replace("USDT", "")
        if base in EXCLUDE_STABLES:
            continue
        if any(p in base for p in EXCLUDE_PATTERNS):
            continue
        try:
            change = float(t.get("price24hPcnt", 0)) * 100
            turnover = float(t.get("turnover24h", 0))
        except (ValueError, TypeError):
            continue
        if turnover < min_turnover:
            continue
        filtered.append({
            "pair": f"{base}/USDT",
            "change_pct": change,
            "turnover": turnover,
        })
    return filtered


def get_movers(pairs, n=3):
    sorted_by_change = sorted(pairs, key=lambda x: x["change_pct"], reverse=True)
    gainers = [p["pair"] for p in sorted_by_change[:n]]
    losers = [p["pair"] for p in sorted_by_change[-n:]]
    return gainers, losers


def main():
    parser = argparse.ArgumentParser(description="Update movers pairlist")
    parser.add_argument("--category", default="spot", choices=["spot", "linear"],
                        help="Bybit market category (spot or linear for futures)")
    args = parser.parse_args()

    suffix = "_futures" if args.category == "linear" else ""
    output_path = Path(__file__).parent / "user_data" / f"movers_pairlist{suffix}.json"
    # Futures need higher min turnover to avoid thin books
    min_turnover = 2_000_000 if args.category == "linear" else 100_000

    tickers = fetch_tickers(args.category)
    pairs = filter_usdt_pairs(tickers, min_turnover=min_turnover)

    gainers, losers = get_movers(pairs, n=3)
    movers = list(dict.fromkeys(gainers + losers))  # dedupe, keep order

    output = {
        "exchange": {
            "pair_whitelist": movers
        },
        "_meta": {
            "description": f"Top 3 gainers + top 3 losers by 24h change ({args.category}, auto-updated every 4h)",
            "category": args.category,
            "gainers": gainers,
            "losers": losers,
        }
    }

    output_path.write_text(json.dumps(output, indent=2) + "\n")
    print(f"Updated {output_path}: {len(movers)} movers ({args.category})")
    print(f"  Gainers: {gainers}")
    print(f"  Losers:  {losers}")


if __name__ == "__main__":
    main()
