#!/usr/bin/env python3
"""
**Spot BTC/USDT only** — market data via **Nautilus ``BybitHttpClient``** (not CCXT),
written for **training channel** + **ruleprediction-agent** data flow.

Writes (default dir ``NAUTILUS_BTC_OHLCV_DIR`` = ``/lab/btc_specialist_data``):

- ``btc_1h_ohlcv.json`` — canonical OHLCV list for ``training_pipeline/channel_training.py`` / ``btc_predict_runner``
- ``btc_daily_90d.json`` — daily OHLCV (same schema as Bybit JSON elsewhere)
- ``btc_1h_ohlcv_nautilus_bybit.json`` — duplicate 1h for ``btc_regime_assessment`` preference chain
- ``nautilus_spot_btc_market_bundle.json`` — snapshot: instrument, ticker, trades, order book deltas, statuses, optional fees

Endpoints used (HTTP adapter): ``request_instruments``, ``request_bars`` (1h + 1d),
``request_tickers``, ``request_trades``, ``request_orderbook_snapshot``,
``request_instrument_statuses``; optional ``request_fee_rates`` if API keys exist in env.

**Swarm hook:** ``NAUTILUS_SWARM_HOOK=1`` or legacy ``NAUTILUS_FUSION_SIDECAR_SYNC=1`` → after each
successful sink pass, ``prediction_agent/nautilus_swarm_hook.py`` (fusion; optional
``NAUTILUS_SWARM_HOOK_KNOWLEDGE=1`` for ``swarm_knowledge_output.json``).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nautilus_trader.adapters.bybit import get_cached_bybit_http_client
from nautilus_trader.core import nautilus_pyo3 as p3
from nautilus_trader.core.nautilus_pyo3 import BybitProductType, InstrumentId


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def _bar_to_ohlcv_row(b: object) -> dict[str, float | int]:
    ts_ms = int(b.ts_event // 1_000_000)
    return {
        "t": ts_ms,
        "o": float(b.open),
        "h": float(b.high),
        "l": float(b.low),
        "c": float(b.close),
        "v": float(b.volume),
    }


def _maybe_nautilus_swarm_hook_after_feed() -> None:
    sw = os.environ.get("NAUTILUS_SWARM_HOOK", "").strip().lower() in ("1", "true", "yes", "on")
    fu = os.environ.get("NAUTILUS_FUSION_SIDECAR_SYNC", "").strip().lower() in ("1", "true", "yes", "on")
    ex = os.environ.get("SYGNIF_BYBIT_DEMO_PREDICTED_MOVE_EXPORT", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not (sw or fu or ex):
        return
    try:
        root = Path(__file__).resolve().parents[2]
        pa = root / "prediction_agent"
        if not pa.is_dir():
            return
        if str(pa) not in sys.path:
            sys.path.insert(0, str(pa))
        from nautilus_swarm_hook import run_nautilus_swarm_hook  # noqa: PLC0415

        run_nautilus_swarm_hook(phase="training_feed", repo_root=root)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"nautilus_swarm_hook_feed_error": str(exc)}), flush=True)


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _has_fee_creds(demo: bool) -> bool:
    if demo:
        return bool(os.environ.get("BYBIT_DEMO_API_KEY", "").strip()) and bool(
            os.environ.get("BYBIT_DEMO_API_SECRET", "").strip()
        )
    return bool(os.environ.get("BYBIT_API_KEY", "").strip()) and bool(
        os.environ.get("BYBIT_API_SECRET", "").strip()
    )


async def _gather(
    *,
    demo: bool,
    testnet: bool,
    limit_1h: int,
    limit_1d: int,
    trades_limit: int,
    book_limit: int,
    book_delta_cap: int,
) -> dict[str, Any]:
    client = get_cached_bybit_http_client(
        api_key=None,
        api_secret=None,
        demo=demo,
        testnet=testnet,
    )
    await client.request_instruments(BybitProductType.SPOT)
    iid = InstrumentId.from_str("BTCUSDT-SPOT.BYBIT")

    instruments = await client.request_instruments(BybitProductType.SPOT)
    inst_dict: dict[str, Any] | None = None
    for ins in instruments:
        if str(ins.id) == "BTCUSDT-SPOT.BYBIT" and hasattr(ins, "to_dict"):
            inst_dict = ins.to_dict()
            break

    bt_1h = p3.BarType.from_str("BTCUSDT-SPOT.BYBIT-1-HOUR-LAST-EXTERNAL")
    bars_1h = await client.request_bars(
        BybitProductType.SPOT, bt_1h, limit=limit_1h, timestamp_on_close=True
    )
    rows_1h = [_bar_to_ohlcv_row(b) for b in bars_1h]
    rows_1h.sort(key=lambda r: int(r["t"]))

    bt_1d = p3.BarType.from_str("BTCUSDT-SPOT.BYBIT-1-DAY-LAST-EXTERNAL")
    bars_1d = await client.request_bars(
        BybitProductType.SPOT, bt_1d, limit=limit_1d, timestamp_on_close=True
    )
    rows_1d = [_bar_to_ohlcv_row(b) for b in bars_1d]
    rows_1d.sort(key=lambda r: int(r["t"]))

    tparams = p3.BybitTickersParams(BybitProductType.SPOT, "BTCUSDT")
    tickers = await client.request_tickers(tparams)
    ticker_payload: list[dict[str, Any]] = []
    for t in tickers:
        if hasattr(t, "to_dict"):
            ticker_payload.append(t.to_dict())

    trades = await client.request_trades(
        BybitProductType.SPOT, iid, limit=trades_limit
    )
    trade_payload = [x.to_dict() for x in trades if hasattr(x, "to_dict")]

    ob = await client.request_orderbook_snapshot(
        BybitProductType.SPOT, iid, limit=book_limit
    )
    deltas = ob.deltas if hasattr(ob, "deltas") else []
    delta_dicts: list[dict[str, Any]] = []
    for d in deltas[-book_delta_cap:]:
        if hasattr(d, "to_dict"):
            delta_dicts.append(d.to_dict())

    statuses = await client.request_instrument_statuses(BybitProductType.SPOT)
    btc_status: Any = None
    if isinstance(statuses, dict):
        btc_status = statuses.get(iid, statuses.get(str(iid)))

    fee_block: dict[str, Any] | None = None
    if _has_fee_creds(demo):
        try:
            fr = await client.request_fee_rates(BybitProductType.SPOT, symbol="BTCUSDT")
            if hasattr(fr, "to_dict"):
                fee_block = fr.to_dict()
            else:
                fee_block = {"raw": str(fr)}
        except Exception as e:
            fee_block = {"error": f"{type(e).__name__}: {e!s}"[:500]}

    bundle = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "nautilus_trader.adapters.bybit.BybitHttpClient",
        "instrument_id": "BTCUSDT-SPOT.BYBIT",
        "demo": demo,
        "testnet": testnet,
        "instrument": inst_dict,
        "bars_1h": {"count": len(rows_1h), "first_t": rows_1h[0]["t"] if rows_1h else None, "last_t": rows_1h[-1]["t"] if rows_1h else None},
        "bars_1d": {"count": len(rows_1d), "first_t": rows_1d[0]["t"] if rows_1d else None, "last_t": rows_1d[-1]["t"] if rows_1d else None},
        "tickers": ticker_payload,
        "recent_trades": trade_payload,
        "orderbook_delta_cap": book_delta_cap,
        "orderbook_deltas": delta_dicts,
        "instrument_status_BTCUSDT_SPOT": str(btc_status) if btc_status is not None else None,
        "fee_rates_BTCUSDT": fee_block,
    }
    return {
        "rows_1h": rows_1h,
        "rows_1d": rows_1d,
        "bundle": bundle,
    }


def run_once(out_dir: Path) -> dict[str, Any]:
    demo = _env_bool("NAUTILUS_BYBIT_DEMO", False)
    testnet = _env_bool("NAUTILUS_BYBIT_TESTNET", False)
    limit_1h = int(os.environ.get("NAUTILUS_TRAINING_BAR_LIMIT_1H", "1600"))
    limit_1d = int(os.environ.get("NAUTILUS_TRAINING_BAR_LIMIT_1D", "120"))
    trades_limit = int(os.environ.get("NAUTILUS_TRAINING_TRADES_LIMIT", "120"))
    book_limit = int(os.environ.get("NAUTILUS_TRAINING_BOOK_LEVELS", "50"))
    book_delta_cap = int(os.environ.get("NAUTILUS_TRAINING_BOOK_DELTA_CAP", "120"))

    data = asyncio.run(
        _gather(
            demo=demo,
            testnet=testnet,
            limit_1h=limit_1h,
            limit_1d=limit_1d,
            trades_limit=trades_limit,
            book_limit=book_limit,
            book_delta_cap=book_delta_cap,
        )
    )

    _atomic_write_json(out_dir / "btc_1h_ohlcv.json", data["rows_1h"])
    _atomic_write_json(out_dir / "btc_daily_90d.json", data["rows_1d"])
    _atomic_write_json(out_dir / "btc_1h_ohlcv_nautilus_bybit.json", data["rows_1h"])
    _atomic_write_json(out_dir / "nautilus_spot_btc_market_bundle.json", data["bundle"])

    return {
        "ok": True,
        "out_dir": str(out_dir),
        "bars_1h": len(data["rows_1h"]),
        "bars_1d": len(data["rows_1d"]),
        "demo": demo,
        "testnet": testnet,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Nautilus Bybit spot BTC/USDT → training OHLCV + market bundle",
    )
    ap.add_argument(
        "--loop",
        action="store_true",
        help="repeat every NAUTILUS_BYBIT_POLL_SEC (default 300s)",
    )
    args = ap.parse_args()

    out_dir = Path(os.environ.get("NAUTILUS_BTC_OHLCV_DIR", "/lab/btc_specialist_data"))
    interval = int(os.environ.get("NAUTILUS_BYBIT_POLL_SEC", "300"))

    def once() -> int:
        t0 = time.perf_counter()
        try:
            meta = run_once(out_dir)
            meta["seconds"] = round(time.perf_counter() - t0, 3)
            _maybe_nautilus_swarm_hook_after_feed()
            print(json.dumps(meta), flush=True)
            return 0
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}), flush=True)
            return 1

    if args.loop:
        while True:
            once()
            time.sleep(max(120, interval))
        return 0
    return once()


if __name__ == "__main__":
    raise SystemExit(main())
