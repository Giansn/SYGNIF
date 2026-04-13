#!/usr/bin/env python3
"""
Pull BTC/USDT spot 1h bars via **Nautilus Trader's Bybit adapter** (Rust HTTP client),
not CCXT — data inflow for `btc_regime_assessment` / btc_Trader_Docker stack.

Writes JSON compatible with `finance_agent/btc_specialist/data/btc_1h_ohlcv.json`:
list of {"t": ms, "o","h","l","c","v": float}.

Public market data works **without** API keys on mainnet. With **`NAUTILUS_BYBIT_DEMO=true`**, the client uses Bybit **demo** hosts; optional signed calls use **`BYBIT_DEMO_API_KEY`** / **`BYBIT_DEMO_API_SECRET`** in the container env (same names Nautilus documents for `demo=True`).
Private endpoints are not used for this OHLCV pull.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from nautilus_trader.adapters.bybit import get_cached_bybit_http_client
from nautilus_trader.core import nautilus_pyo3 as p3
from nautilus_trader.core.nautilus_pyo3 import BybitProductType


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def _bar_to_row(b: object) -> dict[str, float | int]:
    ts_ms = int(b.ts_event // 1_000_000)
    return {
        "t": ts_ms,
        "o": float(b.open),
        "h": float(b.high),
        "l": float(b.low),
        "c": float(b.close),
        "v": float(b.volume),
    }


async def _fetch_rows(
    *,
    demo: bool,
    testnet: bool,
    limit: int,
) -> list[dict[str, float | int]]:
    client = get_cached_bybit_http_client(
        api_key=None,
        api_secret=None,
        demo=demo,
        testnet=testnet,
    )
    await client.request_instruments(BybitProductType.SPOT)
    bar_type = p3.BarType.from_str("BTCUSDT-SPOT.BYBIT-1-HOUR-LAST-EXTERNAL")
    bars = await client.request_bars(
        BybitProductType.SPOT,
        bar_type,
        limit=limit,
        timestamp_on_close=True,
    )
    rows = [_bar_to_row(b) for b in bars]
    rows.sort(key=lambda r: int(r["t"]))
    return rows


def run_once(out_path: Path, *, demo: bool, testnet: bool, limit: int) -> int:
    rows = asyncio.run(_fetch_rows(demo=demo, testnet=testnet, limit=limit))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out_path)
    return len(rows)


def _emit_ok(*, demo: bool, testnet: bool, n: int, out_path: Path, dt: float) -> None:
    print(
        json.dumps(
            {
                "ok": True,
                "sink": "nautilus_bybit_http",
                "demo": demo,
                "testnet": testnet,
                "bars_written": n,
                "out": str(out_path),
                "seconds": round(dt, 3),
            }
        ),
        flush=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Nautilus Bybit → BTC 1h OHLCV JSON sink (one shot or --loop)",
    )
    ap.add_argument("--limit", type=int, default=None, help="override NAUTILUS_BTC_BAR_LIMIT")
    ap.add_argument(
        "--loop",
        action="store_true",
        help="repeat forever; sleep NAUTILUS_BYBIT_POLL_SEC (default 3600) between pulls",
    )
    args = ap.parse_args()

    demo = _env_bool("NAUTILUS_BYBIT_DEMO", False)
    testnet = _env_bool("NAUTILUS_BYBIT_TESTNET", False)
    limit = args.limit if args.limit is not None else int(os.environ.get("NAUTILUS_BTC_BAR_LIMIT", "900"))
    out_dir = Path(os.environ.get("NAUTILUS_BTC_OHLCV_DIR", "/lab/btc_specialist_data"))
    out_name = os.environ.get("NAUTILUS_BTC_OHLCV_NAME", "btc_1h_ohlcv_nautilus_bybit.json")
    out_path = out_dir / out_name
    interval = int(os.environ.get("NAUTILUS_BYBIT_POLL_SEC", "3600"))

    def pull_and_log() -> int:
        t0 = time.perf_counter()
        try:
            n = run_once(out_path, demo=demo, testnet=testnet, limit=limit)
            dt = time.perf_counter() - t0
            _emit_ok(demo=demo, testnet=testnet, n=n, out_path=out_path, dt=dt)
            return 0
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}), flush=True)
            return 1

    if args.loop:
        while True:
            pull_and_log()
            time.sleep(max(60, interval))
        return 0

    return pull_and_log()


if __name__ == "__main__":
    raise SystemExit(main())
