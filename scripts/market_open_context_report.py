#!/usr/bin/env python3
"""
Print UTC **crypto-liquidity proxy** sessions (aligned with ``market_sessions_orb``)
and optional Bybit **spot vs linear** last prices for BTC/ETH.

If ``NEWHEDGE_API_KEY`` is set, probes NewHedge via ``api_token`` query (see
``finance_agent/newhedge_client.py``); prints a short status (full JSON can be large).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _bybit_last(category: str, symbol: str) -> float | None:
    url = (
        "https://api.bybit.com/v5/market/tickers?"
        f"category={category}&symbol={symbol}"
    )
    try:
        with urllib.request.urlopen(url, timeout=12) as r:
            data = json.loads(r.read().decode())
    except (OSError, json.JSONDecodeError):
        return None
    lst = (data.get("result") or {}).get("list") or []
    if not lst:
        return None
    try:
        return float(lst[0].get("lastPrice") or 0)
    except (TypeError, ValueError):
        return None


def main() -> None:
    now = datetime.now(timezone.utc)
    h = now.hour
    if h < 8:
        sess = "asia"
    elif h < 13:
        sess = "eu_london"
    elif h < 22:
        sess = "us"
    else:
        sess = "pacific"
    print(f"UTC {now:%Y-%m-%d %H:%M:%SZ}  session={sess}")
    print("Bands (ORB module): asia 00–08, eu_london 08–13, us 13–22, pacific 22–24 UTC")
    spot_btc = _bybit_last("spot", "BTCUSDT")
    spot_eth = _bybit_last("spot", "ETHUSDT")
    lin_btc = _bybit_last("linear", "BTCUSDT")
    lin_eth = _bybit_last("linear", "ETHUSDT")
    print(
        "Bybit last: "
        f"BTC spot={spot_btc} linear={lin_btc} | "
        f"ETH spot={spot_eth} linear={lin_eth}"
    )
    if os.environ.get("NEWHEDGE_API_KEY", "").strip():
        from finance_agent.newhedge_client import fetch_altcoins_correlation_usd

        payload, err = fetch_altcoins_correlation_usd()
        if err:
            print(f"NewHedge: error {err}")
        else:
            kind = type(payload).__name__
            n = len(payload) if isinstance(payload, (list, dict)) else "n/a"
            print(f"NewHedge altcoins-correlation: ok type={kind} len={n}")
    else:
        print("NewHedge: skip (set NEWHEDGE_API_KEY for API probe)")


if __name__ == "__main__":
    main()
