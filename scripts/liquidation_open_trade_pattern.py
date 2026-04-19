#!/usr/bin/env python3
"""
**Liquidation pattern** for **open** USDT-linear positions — uses venue ``liqPrice`` from
``GET /v5/position/list`` (same stack as ``trade_overseer/bybit_linear_hedge.py``).

Computes distance from **mark** to **liq** (%% of mark, USD per 1 BTC notional) and a coarse **band**:
``comfort`` (≥5%%), ``watch`` (2–5%%), ``stress`` (0.5–2%%), ``knife`` (<0.5%%).

Env: ``BYBIT_DEMO_*`` (demo) or mainnet keys when ``OVERSEER_BYBIT_HEDGE_MAINNET`` + ``OVERSEER_HEDGE_LIVE_OK``
(see hedge module). Loads ``swarm_operator.env`` / instance paths via ``apply_swarm_instance_env``.

Usage::

  cd ~/SYGNIF && python3 scripts/liquidation_open_trade_pattern.py
  python3 scripts/liquidation_open_trade_pattern.py --symbol BTCUSDT --json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def _num(x: Any) -> float:
    try:
        return float(str(x).strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _pct_room_mark(*, side: str, mark: float, liq: float) -> float | None:
    """Percent of mark price between mark and liq (positive = room before liq)."""
    if mark <= 0 or liq <= 0:
        return None
    s = str(side or "").lower()
    if s == "buy":  # long
        return (mark - liq) / mark * 100.0
    if s == "sell":  # short
        return (liq - mark) / mark * 100.0
    return None


def _band(pct: float | None) -> str:
    if pct is None or math.isnan(pct):
        return "unknown"
    if pct >= 5.0:
        return "comfort"
    if pct >= 2.0:
        return "watch"
    if pct >= 0.5:
        return "stress"
    return "knife"


def _pattern_row(row: dict[str, Any]) -> dict[str, Any]:
    sym = str(row.get("symbol") or "")
    side = str(row.get("side") or "")
    sz = abs(_num(row.get("size")))
    avg = _num(row.get("avgPrice"))
    mark = _num(row.get("markPrice"))
    liq = _num(row.get("liqPrice"))
    lev = _num(row.get("leverage"))
    upl = _num(row.get("unrealisedPnl"))
    pct = _pct_room_mark(side=side, mark=mark, liq=liq) if liq > 0 and mark > 0 else None
    usd_per_btc = abs(mark - liq) if liq > 0 and mark > 0 else None
    return {
        "symbol": sym,
        "side": side,
        "size": sz,
        "leverage": lev,
        "avg_price": avg,
        "mark_price": mark,
        "liq_price": liq if liq > 0 else None,
        "unrealised_pnl": upl,
        "pct_mark_to_liq": round(pct, 4) if pct is not None else None,
        "abs_mark_liq_usd_per_unit": round(usd_per_btc, 4) if usd_per_btc is not None else None,
        "notional_usdt_approx": round(sz * mark, 2) if sz and mark else None,
        "liquidation_band": _band(pct),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Liquidation distance pattern for open linear positions")
    ap.add_argument("--symbol", default="BTCUSDT", help="Linear symbol (default BTCUSDT)")
    ap.add_argument("--json", action="store_true", help="Print JSON array")
    args = ap.parse_args()
    root = _repo()
    sys.path.insert(0, str(root / "trade_overseer"))
    sys.path.insert(0, str(root / "finance_agent"))
    from swarm_instance_paths import apply_swarm_instance_env  # noqa: PLC0415

    apply_swarm_instance_env(root)
    import bybit_linear_hedge as blh  # noqa: PLC0415

    sym = str(args.symbol or "BTCUSDT").replace("/", "").upper().strip() or "BTCUSDT"
    try:
        pr = blh.position_list(sym)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    if pr.get("retCode") != 0:
        print(json.dumps({"ok": False, "error": pr.get("retMsg"), "raw": pr}), file=sys.stderr)
        return 3

    rows_out: list[dict[str, Any]] = []
    for row in (pr.get("result") or {}).get("list") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("symbol", "")).upper() != sym:
            continue
        if abs(_num(row.get("size"))) < 1e-12:
            continue
        rows_out.append(_pattern_row(row))

    doc = {
        "ok": True,
        "symbol": sym,
        "rest_base": getattr(blh, "signed_trading_rest_base", lambda: "")(),
        "positions": rows_out,
        "flat": len(rows_out) == 0,
    }
    if args.json:
        print(json.dumps(doc, indent=2))
        return 0
    base = doc["rest_base"]
    print(f"SYGNIF_LIQ_PATTERN symbol={sym} rest={base}", flush=True)
    if not rows_out:
        print("  (no open position for symbol)", flush=True)
        return 0
    for p in rows_out:
        print(
            f"  side={p['side']!r} sz={p['size']} lev={p['leverage']} mark={p['mark_price']} "
            f"liq={p['liq_price']} room%={p['pct_mark_to_liq']} band={p['liquidation_band']} "
            f"upl={p['unrealised_pnl']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
