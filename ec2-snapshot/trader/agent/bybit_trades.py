"""SYGNIF Bybit trade-history fetchers — canonical option + futures streams.

The Bybit web UI at bybit.com/user/assets/order/derivatives/options merges
data from THREE separate API endpoints. Pulling only one (e.g.
/v5/execution/list) misses option expirations/settlements that appear in
the UI as "Delivery" entries. This module gives us the same merged view
through a single call.

Two stream entry points:
  fetch_recent_options(hours=72)  → trades + deliveries on option contracts
  fetch_recent_futures(hours=72)  → trades + closed_pnl on linear (perp) contracts

Each returns a list of uniform dicts sorted newest-first:
  {
    "ts_utc":     ISO8601 string,
    "ts_unix":    float,
    "symbol":     "BTCUSDT-2MAY26-77000-P" (option) | "BTCUSDT" (perp),
    "category":   "option" | "linear",
    "action":     "Open" | "Close" | "Delivery",
    "side":       "Buy" | "Sell",
    "qty":        float,
    "price":      float,           # exec or delivery price
    "value_usd":  float,           # qty × price (signed by side)
    "fee_usd":    float,
    "change_usd": float,           # net wallet effect (negative on debit)
    "realized_pnl_usd": float | None,
    "source":     "execution" | "delivery" | "closed_pnl",
    "raw_id":     str (Bybit execId / orderId / settlement key),
  }

Usage:
    from agent import bybit_trades as T
    rows = T.fetch_recent_options(hours=24)
    for r in rows: print(r["ts_utc"], r["symbol"], r["action"], r["change_usd"])

CLI:
    python3 -m agent.bybit_trades options [hours]
    python3 -m agent.bybit_trades futures [hours]
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ENV_FILE = Path.home() / ".sygnif" / "bybit-mcp.env"
BASE_DEMO = "https://api-demo.bybit.com"
BASE_LIVE = "https://api.bybit.com"
RECV_WINDOW = "5000"
TIMEOUT = 12


def _load_env() -> dict:
    env: dict[str, str] = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k] = v.strip().strip('"').strip("'")
    return env


def _signed_get(base: str, path: str, params: dict, key: str, secret: str) -> dict:
    qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    ts = str(int(time.time() * 1000))
    pre = ts + key + RECV_WINDOW + qs
    sig = hmac.new(secret.encode(), pre.encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        f"{base}{path}?{qs}",
        headers={
            "X-BAPI-API-KEY": key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": RECV_WINDOW,
            "X-BAPI-SIGN": sig,
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def _resolve_creds(*, mode: str = "demo") -> tuple[str, str, str]:
    base = BASE_DEMO if mode == "demo" else BASE_LIVE
    env = _load_env()
    if mode == "demo":
        key_names = ("BYBIT_API_KEY",)
        secret_names = ("BYBIT_API_SECRET",)
    else:
        key_names = ("BYBIT_LIVE_API_KEY", "BYBIT_API_KEY")
        secret_names = ("BYBIT_LIVE_API_SECRET", "BYBIT_API_SECRET")

    def _lookup(names: tuple[str, ...]) -> str:
        for n in names:
            v = env.get(n) or os.environ.get(n)
            if v:
                return v
        return ""

    key = _lookup(key_names)
    secret = _lookup(secret_names)
    if not key or not secret:
        raise RuntimeError(f"Bybit {mode} keys not set; looked for {key_names} "
                           f"in env file {ENV_FILE} and process env")
    return base, key, secret


def _iso(ts_unix: float) -> str:
    return datetime.fromtimestamp(ts_unix, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# OPTIONS — execution + delivery
# ---------------------------------------------------------------------------
def fetch_recent_options(hours: int = 72, *, mode: str = "demo",
                          limit_per_endpoint: int = 50) -> list[dict]:
    """Recent option trade history. Combines:
      • /v5/execution/list  (Trade type — manual fills, "Open" / "Close")
      • /v5/position/closed-pnl  (realized P&L per close — joined to fills
                                   via orderId)  [P4b 2026-05-02]
      • /v5/asset/delivery-record  (auto-expirations, "Delivery")

    Returned newest-first, deduped by raw_id.
    """
    base, key, secret = _resolve_creds(mode=mode)
    cutoff = time.time() - hours * 3600
    out: list[dict] = []
    seen_ids: set[str] = set()

    # P4b: pre-fetch closed_pnl indexed by orderId, so the closing fill rows
    # below can attach realized_pnl. Same join pattern as the futures path.
    closed_by_order: dict[str, dict] = {}
    try:
        resp = _signed_get(base, "/v5/position/closed-pnl",
                           {"category": "option", "limit": limit_per_endpoint},
                           key, secret)
        for c in (resp.get("result", {}) or {}).get("list", []) or []:
            oid = c.get("orderId")
            if oid:
                closed_by_order[oid] = c
    except Exception as e:
        sys.stderr.write(f"option closed-pnl error: {e}\n")

    # 1. Manual fills via execution list
    try:
        resp = _signed_get(base, "/v5/execution/list",
                           {"category": "option", "limit": limit_per_endpoint},
                           key, secret)
        for r in (resp.get("result", {}) or {}).get("list", []) or []:
            ts_unix = int(r.get("execTime", 0)) / 1000
            if ts_unix < cutoff:
                continue
            exec_id = str(r.get("execId", ""))
            if exec_id in seen_ids:
                continue
            seen_ids.add(exec_id)
            side = str(r.get("side", ""))
            qty = float(r.get("execQty", 0) or 0)
            price = float(r.get("execPrice", 0) or 0)
            value = float(r.get("execValue", 0) or 0)
            fee = float(r.get("execFee", 0) or 0)
            # Sign convention: SELL credits wallet (+), BUY debits (−)
            sign = +1 if side == "Sell" else -1
            change_usd = sign * value - fee
            # closedSize > 0 means this is a closing fill (reduceOnly result)
            closed_size = float(r.get("closedSize", 0) or 0)
            action = "Close" if closed_size > 0 else "Open"
            # P4b: attach realized P&L from closed-pnl join (Close rows only)
            order_id = r.get("orderId", "")
            cp = closed_by_order.get(order_id) if action == "Close" else None
            realized = float(cp.get("closedPnl")) if cp and cp.get("closedPnl") else None
            out.append({
                "ts_utc": _iso(ts_unix), "ts_unix": ts_unix,
                "symbol": r.get("symbol", ""), "category": "option",
                "action": action, "side": side,
                "qty": qty, "price": price,
                "value_usd": value, "fee_usd": fee,
                "change_usd": (change_usd if realized is None
                                else realized),  # prefer truth when joined
                "realized_pnl_usd": realized,
                "source": ("execution+closed_pnl" if cp else "execution"),
                "raw_id": exec_id,
                "order_id": order_id,
                "order_link_id": r.get("orderLinkId"),
                "avg_entry_price": (float(cp.get("avgEntryPrice", 0))
                                     if cp and cp.get("avgEntryPrice") else None),
                "avg_exit_price": (float(cp.get("avgExitPrice", 0))
                                    if cp and cp.get("avgExitPrice") else None),
            })
    except Exception as e:
        sys.stderr.write(f"execution.list error: {e}\n")

    # 2. Auto-deliveries (expirations)
    try:
        resp = _signed_get(base, "/v5/asset/delivery-record",
                           {"category": "option", "limit": limit_per_endpoint},
                           key, secret)
        for d in (resp.get("result", {}) or {}).get("list", []) or []:
            ts_unix = int(d.get("deliveryTime", 0)) / 1000
            if ts_unix < cutoff:
                continue
            symbol = d.get("symbol", "")
            side = d.get("side", "")
            position_qty = float(d.get("position", 0) or 0)
            delivery_price = float(d.get("deliveryPrice", 0) or 0)
            realized = float(d.get("realisedPnl", 0) or 0)
            fee_total = float(d.get("fee", 0) or 0)
            delivery_rpnl = float(d.get("deliveryRpl", 0) or realized or 0)
            raw_id = f"deliv-{symbol}-{int(ts_unix)}"
            if raw_id in seen_ids:
                continue
            seen_ids.add(raw_id)
            out.append({
                "ts_utc": _iso(ts_unix), "ts_unix": ts_unix,
                "symbol": symbol, "category": "option",
                "action": "Delivery", "side": side,
                "qty": position_qty, "price": delivery_price,
                "value_usd": position_qty * delivery_price,
                "fee_usd": fee_total,
                "change_usd": delivery_rpnl,
                "realized_pnl_usd": delivery_rpnl,
                "source": "delivery",
                "raw_id": raw_id,
            })
    except Exception as e:
        sys.stderr.write(f"delivery-record error: {e}\n")

    out.sort(key=lambda r: r["ts_unix"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# FUTURES (linear perps) — execution + closed_pnl join
# ---------------------------------------------------------------------------
def fetch_recent_futures(hours: int = 72, *, mode: str = "demo",
                          limit_per_endpoint: int = 50) -> list[dict]:
    """Recent futures (linear perp) trade history. Combines:
      • /v5/execution/list  (every fill, "Open" / "Close")
      • /v5/position/closed-pnl  (realized P&L per close — joined to fills
                                   via orderId)

    Returned newest-first.
    """
    base, key, secret = _resolve_creds(mode=mode)
    cutoff = time.time() - hours * 3600
    out: list[dict] = []
    seen_ids: set[str] = set()

    # closed_pnl indexed by orderId so we can attach realized to the closing fill
    closed_by_order: dict[str, dict] = {}
    try:
        resp = _signed_get(base, "/v5/position/closed-pnl",
                           {"category": "linear", "limit": limit_per_endpoint},
                           key, secret)
        for c in (resp.get("result", {}) or {}).get("list", []) or []:
            oid = c.get("orderId")
            if oid:
                closed_by_order[oid] = c
    except Exception as e:
        sys.stderr.write(f"closed-pnl error: {e}\n")

    try:
        resp = _signed_get(base, "/v5/execution/list",
                           {"category": "linear", "limit": limit_per_endpoint},
                           key, secret)
        for r in (resp.get("result", {}) or {}).get("list", []) or []:
            ts_unix = int(r.get("execTime", 0)) / 1000
            if ts_unix < cutoff:
                continue
            exec_id = str(r.get("execId", ""))
            if exec_id in seen_ids:
                continue
            seen_ids.add(exec_id)
            side = str(r.get("side", ""))
            qty = float(r.get("execQty", 0) or 0)
            price = float(r.get("execPrice", 0) or 0)
            value = float(r.get("execValue", 0) or 0)
            fee = float(r.get("execFee", 0) or 0)
            closed_size = float(r.get("closedSize", 0) or 0)
            action = "Close" if closed_size > 0 else "Open"
            sign = +1 if side == "Sell" else -1
            order_id = r.get("orderId", "")
            cp = closed_by_order.get(order_id)
            realized = float(cp.get("closedPnl", 0)) if cp else None
            out.append({
                "ts_utc": _iso(ts_unix), "ts_unix": ts_unix,
                "symbol": r.get("symbol", ""), "category": "linear",
                "action": action, "side": side,
                "qty": qty, "price": price,
                "value_usd": value, "fee_usd": fee,
                "change_usd": (sign * value - fee) if action == "Open" else (realized or (sign * value - fee)),
                "realized_pnl_usd": realized,
                "source": "execution+closed_pnl" if cp else "execution",
                "raw_id": exec_id,
                "order_id": order_id,
                "order_link_id": r.get("orderLinkId"),
            })
    except Exception as e:
        sys.stderr.write(f"execution.list (linear) error: {e}\n")

    out.sort(key=lambda r: r["ts_unix"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("(no rows)")
        return
    print(f"{'time':18s}  {'symbol':40s}  {'action':9s}  {'side':5s}  "
          f"{'qty':>6s}  {'price':>10s}  {'change':>9s}  {'fee':>6s}  source")
    print("-" * 130)
    for r in rows:
        when = r["ts_utc"][:19].replace("T", " ")
        print(f"{when}  {r['symbol']:40s}  {r['action']:9s}  {r['side']:5s}  "
              f"{r['qty']:>6.4f}  ${r['price']:>9.2f}  ${r['change_usd']:>+8.2f}  ${r['fee_usd']:>5.2f}  {r['source']}")
    print()
    total_change = sum(r["change_usd"] for r in rows)
    total_fee = sum(r["fee_usd"] for r in rows)
    realized = sum(r["realized_pnl_usd"] or 0 for r in rows if r.get("realized_pnl_usd") is not None)
    print(f"{len(rows)} rows; net wallet change=${total_change:+,.2f}  "
          f"fees=${total_fee:.2f}  realized P&L=${realized:+,.2f}")


def _cli() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("options", "futures"):
        print("usage: python3 -m agent.bybit_trades [options|futures] [hours=72]",
              file=sys.stderr)
        return 2
    kind = sys.argv[1]
    hours = int(sys.argv[2]) if len(sys.argv) > 2 else 72
    mode = os.environ.get("SYGNIF_TRADES_MODE", "demo")
    if kind == "options":
        rows = fetch_recent_options(hours=hours, mode=mode)
    else:
        rows = fetch_recent_futures(hours=hours, mode=mode)
    _print_table(rows)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
