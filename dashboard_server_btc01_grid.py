#!/usr/bin/env python3
"""Sygnif BTC 0.1 + Nautilus Grid MM dashboard — chart/open trades via Freqtrade proxy; grid orders + logs."""
from __future__ import annotations

import base64
import json
import os
import http.server
import socketserver
import subprocess
import sys
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler
from pathlib import Path

DIR = Path(__file__).resolve().parent
if str(DIR) not in sys.path:
    sys.path.insert(0, str(DIR))

try:
    from dashboard_cryptoapis import ensure_cryptoapis_foundation_json
except ImportError:

    def ensure_cryptoapis_foundation_json(*_a, **_k):
        return None


_CLIENT_SOCK_TIMEOUT = 120
PORT = int(os.environ.get("SYGNIF_DASHBOARD_BTC01_GRID_PORT", "8892"))
BTC01_API = os.environ.get("FT_BTC01_URL", "http://127.0.0.1:8185").rstrip("/")
DOCKER_GRID = os.environ.get("SYGNIF_DOCKER_GRID_CONTAINER", "nautilus-grid-btc01")
DOCKER_BTC01 = os.environ.get("SYGNIF_DOCKER_BTC01_CONTAINER", "freqtrade-btc-0-1")
_LOG_TAIL = int(os.environ.get("SYGNIF_DASHBOARD_DOCKER_LOG_TAIL", "400"))
_PLACEHOLDER = b"__SYGNIF_FT_BASIC_B64__"

os.chdir(DIR)


def _btc01_password() -> str:
    p = (
        os.environ.get("FT_BTC01_PASS")
        or os.environ.get("FREQTRADE_API_PASSWORD")
        or os.environ.get("FT_FUTURES_PASS")
        or os.environ.get("API_PASSWORD")
        or ""
    ).strip()
    if p:
        return p.replace("$$", "$")
    cfg = DIR / "user_data" / "config_btc_strategy_0_1_paper_market.json"
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        p = str(data.get("api_server", {}).get("password", "") or "").strip()
        if p:
            return p.replace("$$", "$")
    except OSError:
        pass
    return "CHANGE_ME"


def _basic_b64() -> bytes:
    u = os.environ.get("FT_BTC01_USER") or os.environ.get("FREQTRADE_API_USERNAME") or "freqtrader"
    return base64.b64encode(f"{u}:{_btc01_password()}".encode())


def _inject_api_auth(html: bytes) -> bytes:
    return html.replace(_PLACEHOLDER, _basic_b64())


def _grid_bybit_creds() -> tuple[str, str]:
    gk = os.environ.get("BYBIT_DEMO_GRID_API_KEY", "").strip()
    gs = os.environ.get("BYBIT_DEMO_GRID_API_SECRET", "").strip()
    if gk and gs:
        return gk, gs
    dk = os.environ.get("BYBIT_DEMO_API_KEY", "").strip()
    ds = os.environ.get("BYBIT_DEMO_API_SECRET", "").strip()
    return dk, ds


def _grid_orders_json() -> dict:
    from trade_overseer.bybit_linear_hedge import get_open_orders_realtime_linear

    sym = (os.environ.get("SYGNIF_GRID_DASHBOARD_SYMBOL", "BTCUSDT") or "BTCUSDT").strip()
    gk, gs = _grid_bybit_creds()
    if not gk or not gs:
        return {
            "retCode": -1,
            "retMsg": "missing BYBIT_DEMO_* or BYBIT_DEMO_GRID_*",
            "result": {"list": []},
        }
    if os.environ.get("BYBIT_DEMO_GRID_API_KEY", "").strip():
        return get_open_orders_realtime_linear(sym, api_key=gk, api_secret=gs)
    return get_open_orders_realtime_linear(sym)


def _docker_logs(name: str) -> tuple[int, bytes]:
    allowed = {DOCKER_GRID, DOCKER_BTC01, "nautilus-grid-btc01", "freqtrade-btc-0-1"}
    if name not in allowed:
        return 400, b"invalid container"
    try:
        out = subprocess.run(
            ["docker", "logs", name, "--tail", str(_LOG_TAIL), "--timestamps"],
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        text = (out.stdout or "") + (out.stderr or "")
        return 200, text.encode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        return 502, str(e).encode()


_BTC_DATA_DIR = os.path.join(DIR, "finance_agent", "btc_specialist", "data")
_PREDICTION_BTC_SNAPSHOT = os.path.join(
    os.path.expanduser("~"), ".local/share/sygnif-agent/predictions/BTCUSDT_latest.json"
)
_BTC_JSON_ALLOWED = frozenset(
    {
        "btc_sygnif_ta_snapshot.json",
        "btc_1h_ohlcv.json",
        "bybit_btc_ticker.json",
        "manifest.json",
        "btc_daily_90d.json",
        "btc_cryptoapis_foundation.json",
        "btc_newhedge_altcoins_correlation.json",
        "btc_specialist_dashboard.json",
        "btc_crypto_market_data.json",
    }
)


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/grid/orders"):
            return self._send_json(_grid_orders_json())
        if path == "/api/grid/logs":
            code, body = _docker_logs(DOCKER_GRID)
            return self._send_raw(code, body, "text/plain; charset=utf-8")
        if path == "/api/btc01/logs":
            code, body = _docker_logs(DOCKER_BTC01)
            return self._send_raw(code, body, "text/plain; charset=utf-8")
        if path.startswith("/api/btc01/"):
            return self._proxy_btc01()
        if path == "/sygnif/btc/prediction_snapshot.json":
            return self._serve_json_file(_PREDICTION_BTC_SNAPSHOT)
        if path.startswith("/sygnif/btc/"):
            name = path[len("/sygnif/btc/") :]
            if ".." in name or "/" in name or not name:
                self.send_error(400)
                return
            if name in _BTC_JSON_ALLOWED:
                if name == "btc_cryptoapis_foundation.json":
                    ensure_cryptoapis_foundation_json(_BTC_DATA_DIR, str(DIR))
                return self._serve_json_file(os.path.join(_BTC_DATA_DIR, name))
            self.send_error(404)
            return
        if path == "/sygnif/btc_sygnif_ta_snapshot.json":
            return self._serve_json_file(os.path.join(_BTC_DATA_DIR, "btc_sygnif_ta_snapshot.json"))
        if path in ("/", "/dashboard"):
            self.path = "/dashboard_btc01_grid.html"
        if self.path == "/dashboard_btc01_grid.html":
            return self._serve_injected_html("dashboard_btc01_grid.html")
        return super().do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/btc01/"):
            return self._proxy_btc01()
        self.send_error(404)

    def do_OPTIONS(self):
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/btc01/"):
            return self._proxy_btc01()
        self.send_error(404)

    def _send_json(self, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send_raw(200, body, "application/json; charset=utf-8")

    def _send_raw(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def _serve_json_file(self, abs_path: str) -> None:
        try:
            with open(abs_path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def _serve_injected_html(self, name: str) -> None:
        path = os.path.join(DIR, name)
        try:
            with open(path, "rb") as f:
                body = _inject_api_auth(f.read())
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def _proxy_btc01(self):
        path = self.path.split("?", 1)[0]
        q = ""
        if "?" in self.path:
            path, q = self.path.split("?", 1)
            q = "?" + q
        if not path.startswith("/api/btc01/"):
            self.send_error(400)
            return
        suffix = path[len("/api/btc01") :]  # /v1/...
        url = BTC01_API + "/api" + suffix + q
        headers = {}
        for h in ("Authorization", "Content-Type"):
            v = self.headers.get(h)
            if v:
                headers[h] = v
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(url, data=body, headers=headers, method=self.command)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() not in ("transfer-encoding", "connection"):
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, format, *args):
        pass


class SygnifThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def process_request_thread(self, request, client_address):
        try:
            request.settimeout(_CLIENT_SOCK_TIMEOUT)
        except OSError:
            pass
        super().process_request_thread(request, client_address)


if __name__ == "__main__":
    print(f"Dashboard BTC01+Grid on http://0.0.0.0:{PORT} → FT {BTC01_API} (threaded)")
    SygnifThreadingServer(("0.0.0.0", PORT), Handler).serve_forever()
