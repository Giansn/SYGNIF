#!/usr/bin/env python3
"""
Extended BTC history (research / training):

1) **Bybit v5 public** spot klines — paginate backward (`end` cursor), up to **1000** bars per call.
   Writes separate JSON files so `pull_btc_context.py` (200×1h / 90×1d) is unchanged.

2) **CoinGecko** public `market_chart/range` (no pip package required — `requests` only).
   Chunked requests + sleeps to reduce 429 risk. Optional **Pro** header if `COINGECKO_API_KEY` is set.

Usage (repo root):

  python3 finance_agent/btc_specialist/scripts/pull_btc_extended_history.py
  python3 .../pull_btc_extended_history.py --1h-bars 8000 --daily-bars 1500 --no-coingecko
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[3]

BYBIT = "https://api.bybit.com/v5/market/kline"
OUT = Path(__file__).resolve().parents[1] / "data"
COINGECKO_FREE = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart/range"
COINGECKO_PRO = "https://pro-api.coingecko.com/api/v3/coins/bitcoin/market_chart/range"


def _load_dotenv_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if not k:
            continue
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def _load_repo_env() -> None:
    home = REPO_ROOT.parent
    for p in (home / "xrp_claude_bot" / ".env", home / "finance_agent" / ".env", REPO_ROOT / ".env"):
        _load_dotenv_file(p)


def _kline_page(category: str, symbol: str, interval: str, limit: int, end_ms: int | None) -> list[list]:
    params: dict = {"category": category, "symbol": symbol, "interval": interval, "limit": limit}
    if end_ms is not None:
        params["end"] = end_ms
    r = requests.get(BYBIT, params=params, timeout=45)
    r.raise_for_status()
    j = r.json()
    if j.get("retCode") != 0:
        raise SystemExit(j)
    rows = j.get("result", {}).get("list") or []
    if not rows:
        return []
    # Bybit returns newest first
    rows.sort(key=lambda x: int(x[0]))
    return rows


def _rows_to_ohlcv(rows: list[list]) -> list[dict]:
    out = []
    for row in rows:
        ts, o, h, low, c, v = row[:6]
        out.append(
            {
                "t": int(ts),
                "o": float(o),
                "h": float(h),
                "l": float(low),
                "c": float(c),
                "v": float(v),
            }
        )
    return out


def fetch_klines_paginated(
    category: str,
    symbol: str,
    interval: str,
    target_bars: int,
    limit_per_call: int = 1000,
) -> list[dict]:
    """Oldest → newest (ascending by t), de-duplicated."""
    limit_per_call = max(1, min(1000, limit_per_call))
    merged: list[dict] = []
    end_ms: int | None = None
    seen_ts: set[int] = set()

    while len(merged) < target_bars:
        raw = _kline_page(category, symbol, interval, limit_per_call, end_ms)
        if not raw:
            break
        batch = _rows_to_ohlcv(raw)
        oldest_t = batch[0]["t"]
        # prepend older batch
        new_part = [b for b in batch if b["t"] not in seen_ts]
        for b in new_part:
            seen_ts.add(b["t"])
        merged = new_part + merged
        if len(raw) < limit_per_call:
            break
        end_ms = oldest_t - 1
        if not new_part:
            break
        time.sleep(0.08)

    merged.sort(key=lambda x: x["t"])
    if len(merged) > target_bars:
        merged = merged[-target_bars:]
    return merged


def _coingecko_headers() -> dict:
    key = (os.environ.get("COINGECKO_API_KEY") or "").strip()
    if key:
        return {"x-cg-pro-api-key": key}
    return {}


def _coingecko_base_url() -> str:
    return COINGECKO_PRO if (os.environ.get("COINGECKO_API_KEY") or "").strip() else COINGECKO_FREE


def fetch_coingecko_market_chart_range(
    from_ts: int,
    to_ts: int,
    chunk_seconds: int = 80 * 24 * 3600,
    sleep_s: float = 2.0,
) -> dict:
    """
    CoinGecko returns prices as [[ms, price], ...]; long ranges are auto-granular (often daily).
    Free tier: stay slow between chunks to avoid 429.
    """
    headers = _coingecko_headers()
    base = _coingecko_base_url()
    all_prices: list[list] = []
    all_mcaps: list[list] = []
    all_vol: list[list] = []
    t = from_ts
    while t < to_ts:
        chunk_end = min(t + chunk_seconds, to_ts)
        params = {"vs_currency": "usd", "from": int(t), "to": int(chunk_end)}
        for attempt in range(5):
            r = requests.get(base, params=params, headers=headers, timeout=90)
            if r.status_code == 429:
                time.sleep(sleep_s * (attempt + 2))
                continue
            r.raise_for_status()
            break
        else:
            raise SystemExit("CoinGecko rate limited after retries")
        j = r.json()
        all_prices.extend(j.get("prices") or [])
        all_mcaps.extend(j.get("market_caps") or [])
        all_vol.extend(j.get("total_volumes") or [])
        t = chunk_end
        time.sleep(sleep_s)
    # de-dupe by ms
    def dedupe(pairs: list[list]) -> list[list]:
        d: dict[int, float] = {}
        for ms, v in pairs:
            d[int(ms)] = float(v)
        return [[k, d[k]] for k in sorted(d.keys())]

    return {
        "prices": dedupe(all_prices),
        "market_caps": dedupe(all_mcaps),
        "total_volumes": dedupe(all_vol),
    }


def main() -> int:
    _load_repo_env()
    ap = argparse.ArgumentParser(description="Bybit long klines + optional CoinGecko range.")
    ap.add_argument("--1h-bars", dest="h1", type=int, default=8000, help="Max 1h candles to keep (default 8000).")
    ap.add_argument("--daily-bars", dest="dd", type=int, default=1500, help="Max daily candles (default 1500).")
    ap.add_argument("--no-coingecko", action="store_true", help="Skip CoinGecko fetch.")
    ap.add_argument(
        "--coingecko-from",
        default="2013-04-28",
        help="ISO date start for CoinGecko range (default 2013-04-28).",
    )
    ap.add_argument(
        "--coingecko-days",
        type=int,
        default=None,
        help="If set, fetch only the last N days of CoinGecko (quick test / smaller pull).",
    )
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    h1 = fetch_klines_paginated("spot", "BTCUSDT", "60", args.h1)
    (OUT / "btc_1h_ohlcv_long.json").write_text(json.dumps(h1, indent=2) + "\n", encoding="utf-8")

    d1 = fetch_klines_paginated("spot", "BTCUSDT", "D", args.dd)
    (OUT / "btc_daily_ohlcv_long.json").write_text(json.dumps(d1, indent=2) + "\n", encoding="utf-8")

    meta = {
        "generated_utc": utc,
        "bybit": {
            "symbol": "BTCUSDT",
            "category": "spot",
            "files": {
                "btc_1h_ohlcv_long.json": len(h1),
                "btc_daily_ohlcv_long.json": len(d1),
            },
        },
        "coingecko": None,
    }

    if not args.no_coingecko:
        to_sec = int(datetime.now(timezone.utc).timestamp())
        if args.coingecko_days is not None:
            from_sec = to_sec - int(args.coingecko_days) * 86400
        else:
            from_dt = datetime.strptime(args.coingecko_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            from_sec = int(from_dt.timestamp())
        try:
            cg = fetch_coingecko_market_chart_range(from_sec, to_sec)
        except Exception as e:
            meta["coingecko"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        else:
            from_iso = (
                datetime.fromtimestamp(from_sec, tz=timezone.utc).strftime("%Y-%m-%d")
                if args.coingecko_days is not None
                else args.coingecko_from
            )
            meta["coingecko"] = {
                "ok": True,
                "from": from_iso,
                "prices_points": len(cg.get("prices") or []),
                "pro": bool((os.environ.get("COINGECKO_API_KEY") or "").strip()),
            }
            (OUT / "btc_coingecko_market_chart.json").write_text(
                json.dumps({"generated_utc": utc, "source": "CoinGecko market_chart/range", **cg}, indent=2)
                + "\n",
                encoding="utf-8",
            )

    (OUT / "btc_extended_history_manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    print(f"[extended] wrote {OUT} ({utc})")
    print(f"  1h bars: {len(h1)}  daily bars: {len(d1)}  coingecko: {meta['coingecko']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
