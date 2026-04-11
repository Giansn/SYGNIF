#!/usr/bin/env python3
"""Sygnif Dashboard server on port 8888 — proxies API requests to Freqtrade."""
import base64
import http.server
import os
import socketserver
import urllib.request

try:
    from dashboard_cryptoapis import ensure_cryptoapis_foundation_json
except ImportError:
    def ensure_cryptoapis_foundation_json(*_a, **_k):
        return None

_CLIENT_SOCK_TIMEOUT = 120  # seconds; frees a worker if the client never completes a request

PORT = 8888
API = "http://localhost:8080"
DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(DIR)

# Injected into dashboard HTML so Basic auth matches api_server in config (not committed).
_PLACEHOLDER = b"__SYGNIF_FT_BASIC_B64__"


def _api_password() -> str:
    # Docker Compose .env uses $$ for a literal $; systemd reads the file raw (keeps $$).
    # EC2 .env often uses FT_SPOT_PASS / API_PASSWORD instead of FREQTRADE_API_PASSWORD.
    p = (
        os.environ.get("FREQTRADE_API_PASSWORD")
        or os.environ.get("FT_SPOT_PASS")
        or os.environ.get("API_PASSWORD")
        or "CHANGE_ME"
    )
    return p.replace("$$", "$")


def _basic_b64() -> bytes:
    u = (
        os.environ.get("FREQTRADE_API_USERNAME")
        or os.environ.get("FT_SPOT_USER")
        or "freqtrader"
    )
    p = _api_password()
    return base64.b64encode(f"{u}:{p}".encode())


def _inject_api_auth(html: bytes) -> bytes:
    return html.replace(_PLACEHOLDER, _basic_b64())


_BTC_DATA_DIR = os.path.join(DIR, "finance_agent", "btc_specialist", "data")
# prediction_horizon_check.py save → ~/.local/share/sygnif-agent/predictions/BTCUSDT_latest.json
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


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """One thread per client so one slow /api proxy cannot block the whole dashboard."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def process_request_thread(self, request, client_address):
        try:
            request.settimeout(_CLIENT_SOCK_TIMEOUT)
        except OSError:
            pass
        super().process_request_thread(request, client_address)


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # Disable browser caching for the dashboard HTML/JS so deploys are
        # picked up on a normal refresh instead of requiring Ctrl+Shift+R.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/"):
            return self._proxy()
        if path == "/sygnif/btc/prediction_snapshot.json":
            return self._serve_json_file(_PREDICTION_BTC_SNAPSHOT)
        if path.startswith("/sygnif/btc/"):
            name = path[len("/sygnif/btc/") :]
            if ".." in name or "/" in name or not name:
                self.send_error(400)
                return
            if name in _BTC_JSON_ALLOWED:
                if name == "btc_cryptoapis_foundation.json":
                    ensure_cryptoapis_foundation_json(_BTC_DATA_DIR, DIR)
                return self._serve_json_file(os.path.join(_BTC_DATA_DIR, name))
            self.send_error(404)
            return
        if path == "/sygnif/btc_sygnif_ta_snapshot.json":
            return self._serve_json_file(os.path.join(_BTC_DATA_DIR, "btc_sygnif_ta_snapshot.json"))
        if path in ("/", "/dashboard"):
            self.path = "/dashboard.html"
        if self.path == "/dashboard.html":
            return self._serve_injected_html("dashboard.html")
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self._proxy()
        self.send_error(404)

    def do_OPTIONS(self):
        if self.path.startswith("/api/"):
            return self._proxy()
        self.send_error(404)

    def _serve_json_file(self, abs_path: str) -> None:
        try:
            with open(abs_path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
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
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self):
        url = API + self.path
        headers = {}
        for h in ("Authorization", "Content-Type"):
            v = self.headers.get(h)
            if v:
                headers[h] = v
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(url, data=body, headers=headers, method=self.command)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
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
            self.wfile.write(f'{{"error":"{e}"}}'.encode())

    def log_message(self, format, *args):
        pass


print(f"Dashboard running on http://0.0.0.0:{PORT} → API {API} (threaded)")
ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
