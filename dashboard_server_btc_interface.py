#!/usr/bin/env python3
"""
Sygnif **BTC Interface** — read-only dashboard for **Bybit linear demo** (USDT).

Portfolio stats, cumulative realized P/L chart, open orders, positions, and closed P/L rows.
Uses ``BYBIT_DEMO_*`` or optional ``BYBIT_DEMO_GRID_*`` (see ``SYGNIF_BTC_IFACE_USE_GRID_KEYS``).

**Primary URL:** same port as BTC Terminal (default **8888**) — ``/interface``, ``/api/btciface/snapshot.json``.
Run this file standalone only for debugging: ``SYGNIF_DASHBOARD_BTC_INTERFACE_PORT`` (default **8894**).
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import socketserver
import sys
import time
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

DIR = Path(__file__).resolve().parent
if str(DIR) not in sys.path:
    sys.path.insert(0, str(DIR))

_CLIENT_SOCK_TIMEOUT = 120
PORT = int(os.environ.get("SYGNIF_DASHBOARD_BTC_INTERFACE_PORT", "8894"))
os.chdir(DIR)


def _iface_creds() -> tuple[str, str]:
    gk = os.environ.get("BYBIT_DEMO_GRID_API_KEY", "").strip()
    gs = os.environ.get("BYBIT_DEMO_GRID_API_SECRET", "").strip()
    use_grid = os.environ.get("SYGNIF_BTC_IFACE_USE_GRID_KEYS", "").strip().lower() in (
        "1",
        "yes",
        "true",
        "on",
    )
    if use_grid and gk and gs:
        return gk, gs
    dk = os.environ.get("BYBIT_DEMO_API_KEY", "").strip()
    ds = os.environ.get("BYBIT_DEMO_API_SECRET", "").strip()
    return dk, ds


def _symbol() -> str:
    return (os.environ.get("SYGNIF_BTC_IFACE_SYMBOL", "BTCUSDT") or "BTCUSDT").strip().replace("/", "").upper()


def _num(x: Any) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse_wallet_usdt(resp: dict[str, Any]) -> dict[str, Any] | None:
    if resp.get("retCode") != 0:
        return None
    lst = (resp.get("result") or {}).get("list") or []
    if not lst:
        return None
    coins = lst[0].get("coin") or []
    for c in coins:
        if str(c.get("coin", "")).upper() != "USDT":
            continue
        wb = _num(c.get("walletBalance"))
        upl = _num(c.get("unrealisedPnl"))
        avail = _num(
            c.get("availableToWithdraw")
            or c.get("availableBalance")
            or c.get("transferBalance")
        )
        cum_r = _num(c.get("cumRealisedPnl"))
        return {
            "wallet_balance": wb,
            "unrealised_pnl": upl,
            "available": avail,
            "cum_realised_pnl": cum_r,
            "equity": wb + upl,
        }
    return None


def _parse_positions(resp: dict[str, Any], symbol: str) -> list[dict[str, Any]]:
    if resp.get("retCode") != 0:
        return []
    out: list[dict[str, Any]] = []
    for row in (resp.get("result") or {}).get("list") or []:
        if str(row.get("symbol", "")).upper() != symbol.upper():
            continue
        if abs(_num(row.get("size"))) < 1e-12:
            continue
        liq = _num(row.get("liqPrice"))
        out.append(
            {
                "symbol": row.get("symbol"),
                "side": row.get("side"),
                "size": _num(row.get("size")),
                "avg_price": _num(row.get("avgPrice")),
                "mark_price": _num(row.get("markPrice")),
                "unrealised_pnl": _num(row.get("unrealisedPnl")),
                "leverage": _num(row.get("leverage")),
                "liq_price": liq if liq > 0 else None,
                "position_value": _num(row.get("positionValue")),
            }
        )
    return out


def _parse_orders(resp: dict[str, Any]) -> list[dict[str, Any]]:
    if resp.get("retCode") != 0:
        return []
    out: list[dict[str, Any]] = []
    for row in (resp.get("result") or {}).get("list") or []:
        out.append(
            {
                "order_id": row.get("orderId"),
                "side": row.get("side"),
                "price": _num(row.get("price")),
                "qty": _num(row.get("qty")),
                "leaves_qty": _num(row.get("leavesQty")),
                "order_type": row.get("orderType"),
                "status": row.get("orderStatus"),
                "created_time": row.get("createdTime"),
            }
        )
    return out


def _fetch_closed_pnl_rows(symbol: str, key: str, secret: str, max_rows: int = 500) -> list[dict[str, Any]]:
    from trade_overseer.bybit_linear_hedge import closed_pnl_linear

    rows: list[dict[str, Any]] = []
    cursor = ""
    while len(rows) < max_rows:
        r = closed_pnl_linear(symbol, limit="100", cursor=cursor, api_key=key, api_secret=secret)
        if r.get("retCode") != 0:
            break
        res = r.get("result") or {}
        batch = res.get("list") or []
        rows.extend(batch)
        cursor = (res.get("nextPageCursor") or "").strip()
        if not cursor or not batch:
            break
    return rows


def _parse_closed_rows(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for row in raw:
        ts = int(_num(row.get("createdTime")) or _num(row.get("updatedTime")))
        pnl = _num(row.get("closedPnl"))
        parsed.append(
            {
                "created_ms": ts,
                "closed_pnl": pnl,
                "side": row.get("side"),
                "qty": _num(row.get("closedSize") or row.get("qty")),
                "avg_entry": _num(row.get("avgEntryPrice")),
                "avg_exit": _num(row.get("avgExitPrice")),
                "order_id": row.get("orderId"),
            }
        )
    parsed.sort(key=lambda x: x["created_ms"])
    return parsed


def _zurich_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Europe/Zurich"))
    except ImportError:
        return datetime.now(timezone.utc)


def _today_realized_zurich(parsed: list[dict[str, Any]]) -> tuple[float, int]:
    now = _zurich_now()
    key = (now.year, now.month, now.day)
    total = 0.0
    n = 0
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Europe/Zurich")
    except ImportError:
        tz = timezone.utc
    for r in parsed:
        if not r["created_ms"]:
            continue
        dt = datetime.fromtimestamp(r["created_ms"] / 1000.0, tz=timezone.utc)
        if tz is not timezone.utc:
            dt = dt.astimezone(tz)
        if (dt.year, dt.month, dt.day) == key:
            total += r["closed_pnl"]
            n += 1
    return total, n


def _chart_cumulative(parsed_closes: list[dict[str, Any]], days: float) -> tuple[list[float], list[str]]:
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - int(days * 86400000)
    in_window = [r for r in parsed_closes if r["created_ms"] >= start_ms]
    if not in_window:
        return [], []

    full_pts: list[tuple[int, float]] = []
    cum = 0.0
    for r in in_window:
        cum += r["closed_pnl"]
        full_pts.append((r["created_ms"], cum))

    if len(full_pts) > 96:
        step = max(1, len(full_pts) // 96)
        idxs = list(range(0, len(full_pts), step))
        if idxs[-1] != len(full_pts) - 1:
            idxs.append(len(full_pts) - 1)
        pts = [full_pts[i] for i in idxs]
    else:
        pts = full_pts

    values = [p[1] for p in pts]
    try:
        from zoneinfo import ZoneInfo

        loc_tz = ZoneInfo("Europe/Zurich")
    except ImportError:
        loc_tz = None

    labels: list[str] = []
    for t_ms, _ in pts:
        if loc_tz:
            dt = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc).astimezone(loc_tz)
            labels.append(dt.strftime("%d.%m %H:%M"))
        else:
            dt = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc)
            labels.append(dt.strftime("%m-%d %H:%M"))
    return values, labels


def build_snapshot() -> dict[str, Any]:
    sym = _symbol()
    ts = int(time.time() * 1000)
    key, secret = _iface_creds()
    if not key or not secret:
        return {
            "ok": False,
            "error": (
                "missing BYBIT_DEMO_API_KEY/SECRET (or set BYBIT_DEMO_GRID_* and "
                "SYGNIF_BTC_IFACE_USE_GRID_KEYS=1)"
            ),
            "symbol": sym,
            "generated_ms": ts,
        }

    from trade_overseer.bybit_linear_hedge import get_open_orders_realtime_linear
    from trade_overseer.bybit_linear_hedge import position_list
    from trade_overseer.bybit_linear_hedge import wallet_balance_unified_coin

    w_raw = wallet_balance_unified_coin("USDT", api_key=key, api_secret=secret)
    p_raw = position_list(sym, api_key=key, api_secret=secret)
    o_raw = get_open_orders_realtime_linear(sym, api_key=key, api_secret=secret)
    closed_raw = _fetch_closed_pnl_rows(sym, key, secret)
    parsed_closes = _parse_closed_rows(closed_raw)
    wallet = _parse_wallet_usdt(w_raw)
    positions = _parse_positions(p_raw, sym)
    orders = _parse_orders(o_raw)

    wins = sum(1 for r in parsed_closes if r["closed_pnl"] > 1e-9)
    losses = sum(1 for r in parsed_closes if r["closed_pnl"] < -1e-9)
    today_abs, today_n = _today_realized_zurich(parsed_closes)

    charts = {}
    for d in (1, 7, 30):
        v, lb = _chart_cumulative(parsed_closes, float(d))
        charts[str(d)] = {"values": v, "labels": lb}

    recent_closed = list(reversed(parsed_closes))[:80]

    return {
        "ok": True,
        "error": None,
        "symbol": sym,
        "generated_ms": ts,
        "wallet": wallet,
        "wallet_raw_ret": w_raw.get("retCode"),
        "positions": positions,
        "position_raw_ret": p_raw.get("retCode"),
        "orders": orders,
        "order_raw_ret": o_raw.get("retCode"),
        "closed_pnl_recent": recent_closed,
        "summary": {
            "open_order_count": len(orders),
            "open_position_count": len(positions),
            "closed_row_count": len(parsed_closes),
            "winning_trades": wins,
            "losing_trades": losses,
            "today_realized_pnl": today_abs,
            "today_close_count": today_n,
        },
        "charts": charts,
    }


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def process_request_thread(self, request, client_address):
        try:
            request.settimeout(_CLIENT_SOCK_TIMEOUT)
        except OSError:
            pass
        super().process_request_thread(request, client_address)


class ThreadingHTTPServerV6(ThreadingHTTPServer):
    """Dual-stack: ``::`` + ``IPV6_V6ONLY=0`` accepts IPv4-mapped clients (e.g. some ``localhost`` setups)."""

    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


class Handler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/health":
            return self._text(200, "ok\n", "text/plain; charset=utf-8")
        if path == "/api/btciface/snapshot.json":
            return self._snapshot()
        if path in ("/", "/dashboard", "/btc"):
            self.path = "/dashboard_btc_interface.html"
        if self.path == "/dashboard_btc_interface.html":
            return self._serve_html("dashboard_btc_interface.html")
        return super().do_GET()

    def do_HEAD(self):
        path = self.path.split("?", 1)[0]
        if path == "/health":
            return self._text(200, "ok\n", "text/plain; charset=utf-8", send_body=False)
        if path == "/api/btciface/snapshot.json":
            body = json.dumps(build_snapshot(), ensure_ascii=False).encode("utf-8")
            return self._raw_head(200, body, "application/json; charset=utf-8")
        if path in ("/", "/dashboard", "/btc"):
            self.path = "/dashboard_btc_interface.html"
        if self.path == "/dashboard_btc_interface.html":
            p = DIR / "dashboard_btc_interface.html"
            try:
                body = p.read_bytes()
            except OSError:
                self.send_error(404)
                return
            return self._raw_head(200, body, "text/html; charset=utf-8")
        return super().do_HEAD()

    def _text(self, code: int, text: str, ctype: str, *, send_body: bool = True) -> None:
        raw = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if send_body:
            self.wfile.write(raw)

    def _raw_head(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

    def _snapshot(self) -> None:
        body = json.dumps(build_snapshot(), ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self, name: str) -> None:
        p = DIR / name
        try:
            body = p.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def _serve() -> None:
    try:
        httpd = ThreadingHTTPServerV6(("::", PORT), Handler)
        bind_msg = f":::{PORT} (IPv4+IPv6)"
    except OSError as e:
        httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
        bind_msg = f"0.0.0.0:{PORT} (IPv4 only — :: bind failed: {e})"
    print(f"Sygnif BTC Interface on http://{bind_msg} — read-only Bybit demo")
    httpd.serve_forever()


if __name__ == "__main__":
    _serve()
