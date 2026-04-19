#!/usr/bin/env python3
"""
**Close** a Bybit **USDT-linear** position with a **reduce-only market** order.

Uses ``trade_overseer/bybit_linear_hedge.py`` (same stack as ``scripts/bybit_demo_positions.py``):

- **Demo** (default): ``api-demo.bybit.com`` + ``BYBIT_DEMO_API_KEY`` / ``BYBIT_DEMO_API_SECRET``.
- **Mainnet** (``--mainnet``): ``api.bybit.com`` + ``BYBIT_API_KEY`` / ``BYBIT_API_SECRET``,
  with ``OVERSEER_BYBIT_HEDGE_MAINNET=1`` and ``OVERSEER_HEDGE_LIVE_OK=YES`` (the flag sets both).

**Safety:** default is **dry-run** (prints plan only). To send the order::

  SYGNIF_BYBIT_CLOSE_ACK=YES python3 scripts/bybit_close_linear_position.py --execute

Optional: ``--cancel-orders`` first calls ``cancel_all_open_orders_linear`` for that symbol
(working orders only; does not close the position by itself).

Examples::

  python3 scripts/bybit_close_linear_position.py
  python3 scripts/bybit_close_linear_position.py --symbol ETHUSDT
  python3 scripts/bybit_close_linear_position.py --mainnet --cancel-orders
  SYGNIF_BYBIT_CLOSE_ACK=YES python3 scripts/bybit_close_linear_position.py --execute
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def load_env(path: Path, *, override: bool = False) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[7:].strip()
        if "=" not in s:
            continue
        k, _, rest = s.partition("=")
        k = k.strip()
        if not k:
            continue
        v = rest.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if override or k not in os.environ:
            os.environ[k] = v


def _load_standard_env() -> None:
    repo = _repo()
    load_env(repo / ".env", override=False)
    xrp = Path.home() / "xrp_claude_bot" / ".env"
    load_env(xrp, override=True)
    extra = (os.environ.get("SYGNIF_SECRETS_ENV_FILE") or "").strip()
    if extra:
        load_env(Path(extra).expanduser(), override=True)


def _close_qty_str(raw_api: str, abs_float: float) -> str:
    s = (raw_api or "").strip()
    if s:
        return s
    step = 0.001
    q = math.floor(abs_float / step) * step
    return f"{q:.6f}".rstrip("0").rstrip(".") or str(step)


def _position_idx(row: dict) -> int:
    v = row.get("positionIdx", 0)
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _best_open_row(pr: dict, symbol: str) -> dict | None:
    """Largest non-zero linear row for ``symbol`` (same side rules as ``parse_linear_position``)."""
    sym = symbol.upper().strip()
    best_row: dict | None = None
    best_sz = 0.0
    for row in (pr.get("result") or {}).get("list") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("symbol", "")).upper() != sym:
            continue
        raw_sz = str(row.get("size") or "").strip()
        try:
            sz = abs(float(raw_sz))
        except (TypeError, ValueError):
            continue
        if sz < 1e-12:
            continue
        side_api = str(row.get("side") or "").strip().lower()
        if side_api not in ("buy", "sell"):
            continue
        if sz > best_sz:
            best_sz = sz
            best_row = row
    return best_row


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Close Bybit USDT-linear position (reduce-only market); dry-run unless --execute + ACK",
    )
    ap.add_argument("--symbol", default="BTCUSDT", help="Linear symbol (default BTCUSDT)")
    ap.add_argument(
        "--mainnet",
        action="store_true",
        help="Live api.bybit.com + BYBIT_API_* (sets OVERSEER_BYBIT_HEDGE_MAINNET=1 and OVERSEER_HEDGE_LIVE_OK=YES)",
    )
    ap.add_argument(
        "--execute",
        action="store_true",
        help="Submit reduce-only market order (requires SYGNIF_BYBIT_CLOSE_ACK=YES)",
    )
    ap.add_argument(
        "--cancel-orders",
        action="store_true",
        help="Before closing, cancel all open orders for the symbol (cancel-all)",
    )
    ap.add_argument("--json", action="store_true", help="Print plan/result as one JSON object")
    args = ap.parse_args()

    _load_standard_env()
    if args.mainnet:
        os.environ["OVERSEER_BYBIT_HEDGE_MAINNET"] = "1"
        os.environ["OVERSEER_HEDGE_LIVE_OK"] = "YES"

    sys.path.insert(0, str(_repo() / "trade_overseer"))
    import bybit_linear_hedge as blh  # noqa: PLC0415

    sym = (args.symbol or "").replace("/", "").upper().strip() or "BTCUSDT"
    host = "api.bybit.com" if blh._hedge_mainnet_enabled() else "api-demo.bybit.com"  # noqa: SLF001

    try:
        pr = blh.position_list(sym)
    except RuntimeError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print("bybit_close_linear_position:", exc, file=sys.stderr)
        return 2

    if pr.get("retCode") != 0:
        err = {"ok": False, "host": host, "symbol": sym, "position_list": pr}
        if args.json:
            print(json.dumps(err, default=str))
        else:
            print("position/list failed:", pr.get("retCode"), pr.get("retMsg"), file=sys.stderr)
        return 1

    row = _best_open_row(pr, sym)
    if row is None:
        out = {"ok": True, "host": host, "symbol": sym, "action": "none", "reason": "already_flat"}
        if args.json:
            print(json.dumps(out))
        else:
            print(f"{host} {sym}: no open position (flat).")
        return 0

    raw_sz = str(row.get("size") or "").strip()
    try:
        pos_sz = abs(float(raw_sz))
    except (TypeError, ValueError):
        pos_sz = 0.0
    side_api = str(row.get("side") or "").strip().lower()
    pos_side = "long" if side_api == "buy" else "short" if side_api == "sell" else None
    if pos_side is None or pos_sz < 1e-12:
        out = {"ok": True, "host": host, "symbol": sym, "action": "none", "reason": "no_parseable_side"}
        if args.json:
            print(json.dumps(out, default=str))
        else:
            print(f"{host} {sym}: could not parse side/size.", row)
        return 0

    close_venue_side = "Sell" if pos_side == "long" else "Buy"
    pidx = _position_idx(row)
    qclose = _close_qty_str(raw_sz, pos_sz)

    plan = {
        "ok": True,
        "host": host,
        "symbol": sym,
        "position_side": pos_side,
        "size": raw_sz,
        "close_side": close_venue_side,
        "positionIdx": pidx,
        "qty": qclose,
        "execute": bool(args.execute),
    }

    if not args.execute:
        if args.json:
            print(json.dumps({**plan, "action": "dry_run"}))
        else:
            print(f"Bybit {host} · {sym}")
            print(f"  Open: {pos_side} size={raw_sz} positionIdx={pidx}")
            print(f"  Would submit: Market {close_venue_side} qty={qclose} reduceOnly=true")
            print("  (omit --execute or set SYGNIF_BYBIT_CLOSE_ACK=YES to actually send)")
        return 0

    ack = (os.environ.get("SYGNIF_BYBIT_CLOSE_ACK") or "").strip().upper()
    if ack != "YES":
        if args.json:
            print(
                json.dumps(
                    {
                        **plan,
                        "ok": False,
                        "error": "Refusing --execute without SYGNIF_BYBIT_CLOSE_ACK=YES",
                    }
                )
            )
        else:
            print(
                "Refusing --execute: set SYGNIF_BYBIT_CLOSE_ACK=YES in the environment.",
                file=sys.stderr,
            )
        return 3

    results: dict = {"plan": plan, "cancel_all": None, "market_close": None}

    if args.cancel_orders:
        ca = blh.cancel_all_open_orders_linear(sym)
        results["cancel_all"] = ca
        if ca.get("retCode") not in (0,):
            if args.json:
                print(json.dumps({"ok": False, **results}, default=str))
            else:
                print("cancel-all failed:", json.dumps(ca, default=str), file=sys.stderr)
            return 1

    mo = blh.create_market_order(sym, close_venue_side, qclose, pidx, reduce_only=True)
    results["market_close"] = mo
    ok = mo.get("retCode") == 0
    if args.json:
        print(json.dumps({"ok": ok, **results}, default=str))
    else:
        print(json.dumps(results, indent=2, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
