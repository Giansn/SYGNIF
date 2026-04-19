#!/usr/bin/env python3
"""
**Bybit v5** USDT-linear: open **positions** + **working orders** (read-only).

**Demo** (default): ``api-demo.bybit.com`` + ``BYBIT_DEMO_API_KEY`` / ``BYBIT_DEMO_API_SECRET``.

**Mainnet** (``--mainnet`` or env): ``https://api.bybit.com`` + ``BYBIT_API_KEY`` / ``BYBIT_API_SECRET``.
Requires the same safety gate as ``trade_overseer/bybit_linear_hedge.py``:
``OVERSEER_BYBIT_HEDGE_MAINNET=1`` and ``OVERSEER_HEDGE_LIVE_OK=YES`` (the CLI flag sets both for this process).

Loads env: ``~/SYGNIF/.env``, then ``~/xrp_claude_bot/.env`` (override), then ``SYGNIF_SECRETS_ENV_FILE``.

Other **trade observers** in this repo (not Bybit-native):
  - **Trade overseer** — ``trade_overseer/overseer.py``; polls **Freqtrade** APIs, HTTP ``:8090``.
  - **Advisor observer** — ``scripts/sygnif_advisor_observer.py``; ``advisor_state.json``.

Examples::

  python3 scripts/bybit_demo_positions.py
  python3 scripts/bybit_demo_positions.py --json
  python3 scripts/bybit_demo_positions.py --mainnet --json
  python3 scripts/bybit_demo_positions.py --settle-coin USDT --extra-order-symbols ETHUSDT
"""
from __future__ import annotations

import argparse
import json
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


def run(
    *,
    settle_coin: str,
    extra_order_symbols: list[str],
    as_json: bool,
) -> int:
    sys.path.insert(0, str(_repo() / "trade_overseer"))
    import bybit_linear_hedge as blh  # noqa: PLC0415

    mainnet = os.environ.get("OVERSEER_BYBIT_HEDGE_MAINNET", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    host = "api.bybit.com" if mainnet else "api-demo.bybit.com"

    try:
        pos = blh._get(  # noqa: SLF001
            "/v5/position/list",
            {"category": "linear", "settleCoin": settle_coin.upper().strip() or "USDT"},
        )
    except RuntimeError as exc:
        if as_json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print("bybit_demo_positions:", exc, file=sys.stderr)
        return 2

    if pos.get("retCode") != 0:
        if as_json:
            print(json.dumps({"ok": False, "response": pos}))
        else:
            print("bybit_demo_positions: API error", pos.get("retCode"), pos.get("retMsg"), file=sys.stderr)
            print(json.dumps(pos, indent=2)[:2000])
        return 1

    rows = (pos.get("result") or {}).get("list") or []
    open_pos = [
        r
        for r in rows
        if isinstance(r, dict) and abs(float(r.get("size") or 0)) > 1e-12
    ]

    sym_set = {str(r.get("symbol", "")).upper() for r in open_pos if r.get("symbol")}
    sym_set.add("BTCUSDT")
    for s in extra_order_symbols:
        t = (s or "").strip().upper()
        if t:
            sym_set.add(t)

    orders_by_symbol: dict[str, list[dict]] = {}
    for sym in sorted(sym_set):
        o = blh.get_open_orders_realtime_linear(sym)
        if o.get("retCode") != 0:
            orders_by_symbol[sym] = []
            continue
        ol = (o.get("result") or {}).get("list") or []
        orders_by_symbol[sym] = ol if isinstance(ol, list) else []

    out = {
        "ok": True,
        "host": host,
        "settleCoin": settle_coin.upper(),
        "positions_open": open_pos,
        "open_orders_by_symbol": orders_by_symbol,
    }

    if as_json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    print(f"Bybit {host} · USDT-linear · settleCoin={settle_coin.upper()}")
    print(f"Open positions (non-zero size): {len(open_pos)}")
    if not open_pos:
        print("  (flat)")
    for r in open_pos:
        print(
            f"  {r.get('symbol')}  side={r.get('side')}  size={r.get('size')}  "
            f"positionIdx={r.get('positionIdx')}  lev={r.get('leverage')}x  "
            f"uPnL={r.get('unrealisedPnl')}  entry={r.get('avgPrice')}"
        )
    print("--- working orders (order/realtime) ---")
    any_o = False
    for sym in sorted(orders_by_symbol.keys()):
        ol = orders_by_symbol[sym]
        if not ol:
            continue
        any_o = True
        print(f"{sym}: {len(ol)} order(s)")
        for x in ol[:25]:
            print(
                " ",
                x.get("orderId"),
                x.get("side"),
                x.get("orderType"),
                "qty",
                x.get("qty"),
                x.get("orderStatus"),
            )
    if not any_o:
        print("(none on scanned symbols)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="List Bybit USDT-linear positions + open orders (demo or mainnet, read-only)",
    )
    ap.add_argument(
        "--mainnet",
        action="store_true",
        help="Use api.bybit.com + BYBIT_API_* (sets OVERSEER_BYBIT_HEDGE_MAINNET=1 and OVERSEER_HEDGE_LIVE_OK=YES)",
    )
    ap.add_argument(
        "--settle-coin",
        default="USDT",
        help="settleCoin for position/list (default USDT)",
    )
    ap.add_argument(
        "--extra-order-symbols",
        default="",
        help="Comma-separated extra symbols to scan for open orders (e.g. ETHUSDT,SOLUSDT)",
    )
    ap.add_argument("--json", action="store_true", help="Print one JSON object to stdout")
    args = ap.parse_args()
    _load_standard_env()
    if args.mainnet:
        os.environ["OVERSEER_BYBIT_HEDGE_MAINNET"] = "1"
        os.environ["OVERSEER_HEDGE_LIVE_OK"] = "YES"
    extra = [x.strip() for x in (args.extra_order_symbols or "").split(",") if x.strip()]
    return run(settle_coin=args.settle_coin, extra_order_symbols=extra, as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
