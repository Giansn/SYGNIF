#!/usr/bin/env python3
"""
Fetches top 3 gainers and top 3 losers from Bybit spot USDT pairs.
Writes them to configs/movers_pairlist.json for Freqtrade to include.
Run via cron every 4h or via docker healthcheck.
"""

import json
import requests
import sys
from pathlib import Path

BYBIT_TICKERS_URL = "https://api.bybit.com/v5/market/tickers?category=spot"

EXCLUDE_STABLES = {
    "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDD", "USDT",
    "USDP", "USDS", "XUSD", "USD1", "RLUSD", "AUSD", "EURI",
}
EXCLUDE_PATTERNS = {"2L", "3L", "5L", "2S", "3S", "5S"}

OUTPUT_PATH = Path(__file__).parent / "user_data" / "movers_pairlist.json"


def fetch_tickers():
    resp = requests.get(BYBIT_TICKERS_URL, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("retCode") != 0:
        raise RuntimeError(f"Bybit API error: {data.get('retMsg')}")
    return data["result"]["list"]


def filter_usdt_pairs(tickers):
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
        if turnover < 100_000:  # Skip very low volume
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
    tickers = fetch_tickers()
    pairs = filter_usdt_pairs(tickers)

    gainers, losers = get_movers(pairs, n=3)
    movers = list(dict.fromkeys(gainers + losers))  # dedupe, keep order

    output = {
        "exchange": {
            "pair_whitelist": movers
        },
        "_meta": {
            "description": "Top 3 gainers + top 3 losers by 24h change (auto-updated every 4h)",
            "gainers": gainers,
            "losers": losers,
        }
    }

    OUTPUT_PATH.write_text(json.dumps(output, indent=2) + "\n")
    print(f"Updated {OUTPUT_PATH}: {len(movers)} movers")
    print(f"  Gainers: {gainers}")
    print(f"  Losers:  {losers}")


if __name__ == "__main__":
    main()
