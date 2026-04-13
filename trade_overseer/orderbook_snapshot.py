"""Read-only BTC spot book snapshot from Nautilus bundle + sidecar signal (host-mounted JSON)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def _bundle_path() -> Path:
    base = Path(os.environ.get("OVERSEER_BTC_DATA_DIR", "/ro/btc_specialist_data"))
    return base / "nautilus_spot_btc_market_bundle.json"


def _signal_path() -> Path:
    base = Path(os.environ.get("OVERSEER_BTC_DATA_DIR", "/ro/btc_specialist_data"))
    return base / "nautilus_strategy_signal.json"


def load_bundle() -> dict[str, Any] | None:
    p = _bundle_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def load_strategy_signal() -> dict[str, Any] | None:
    p = _signal_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def best_prices_from_deltas(deltas: list, tail: int = 150) -> tuple[float | None, float | None]:
    """Heuristic top-of-book from recent ADD deltas (not a full L2 replay)."""
    if not isinstance(deltas, list):
        return None, None
    chunk = deltas[-tail:] if len(deltas) > tail else deltas
    bids: list[float] = []
    asks: list[float] = []
    for d in chunk:
        if not isinstance(d, dict) or d.get("action") != "ADD":
            continue
        o = d.get("order") or {}
        try:
            p = float(o.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if p <= 0:
            continue
        side = str(o.get("side", "") or "").upper()
        if side == "BUY":
            bids.append(p)
        elif side == "SELL":
            asks.append(p)
    return (max(bids) if bids else None, min(asks) if asks else None)


def build_orderbook_overview() -> dict[str, Any]:
    bundle = load_bundle()
    sig = load_strategy_signal()
    out: dict[str, Any] = {
        "bundle_path": str(_bundle_path()),
        "signal_path": str(_signal_path()),
        "bundle_ok": bundle is not None,
        "signal_ok": sig is not None,
    }
    if not bundle:
        out["error"] = "nautilus_spot_btc_market_bundle.json missing or unreadable"
        return out

    try:
        mtime = _bundle_path().stat().st_mtime
        out["bundle_age_sec"] = round(time.time() - mtime, 1)
    except OSError:
        out["bundle_age_sec"] = None

    out["generated_utc"] = bundle.get("generated_utc")
    out["instrument_id"] = bundle.get("instrument_id")
    trades = bundle.get("recent_trades") or []
    if isinstance(trades, list) and trades:
        last = trades[-1]
        if isinstance(last, dict):
            out["last_trade"] = {
                "price": last.get("price"),
                "size": last.get("size"),
                "aggressor_side": last.get("aggressor_side"),
            }
    out["recent_trades_count"] = len(trades) if isinstance(trades, list) else 0

    deltas = bundle.get("orderbook_deltas") or []
    bb, ba = best_prices_from_deltas(deltas if isinstance(deltas, list) else [])
    out["orderbook_delta_count"] = len(deltas) if isinstance(deltas, list) else 0
    out["best_bid"] = bb
    out["best_ask"] = ba
    if bb is not None and ba is not None and ba > bb:
        out["spread"] = round(ba - bb, 2)
        out["mid"] = round((bb + ba) / 2.0, 2)
    else:
        out["spread"] = None
        out["mid"] = None

    if sig:
        out["nautilus_strategy"] = {
            "bias": sig.get("bias"),
            "rsi14": sig.get("rsi14"),
            "close": sig.get("close"),
            "generated_utc": sig.get("generated_utc"),
        }
    return out
