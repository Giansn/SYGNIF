#!/usr/bin/env python3
"""Sygnif BTC Terminal — prediction / training JSON + **BTC Interface** (Bybit demo portfolio) on the same port."""
from __future__ import annotations

import http.server
import json
import os
import socket
import socketserver
import sys

_CLIENT_SOCK_TIMEOUT = 120

PORT = int(os.environ.get("SYGNIF_BTC_TERMINAL_PORT", "8888"))
DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(DIR)

if str(DIR) not in sys.path:
    sys.path.insert(0, DIR)

_PREDICTION_DIR = os.path.join(DIR, "prediction_agent")
_LETSCRASH_DIR = os.path.join(DIR, "letscrash")

_DATA_FILES: dict[str, str] = {
    "training_channel_output.json": os.path.join(_PREDICTION_DIR, "training_channel_output.json"),
    "btc_prediction_output.json": os.path.join(_PREDICTION_DIR, "btc_prediction_output.json"),
    "btc_strategy_0_1_rule_registry.json": os.path.join(
        _LETSCRASH_DIR, "btc_strategy_0_1_rule_registry.json"
    ),
}

try:
    from dashboard_server_btc_interface import build_snapshot as _btciface_snapshot
except ImportError:
    _btciface_snapshot = None  # type: ignore[misc, assignment]


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def process_request_thread(self, request, client_address):
        try:
            request.settimeout(_CLIENT_SOCK_TIMEOUT)
        except OSError:
            pass
        super().process_request_thread(request, client_address)


class ThreadingHTTPServerV6(ThreadingHTTPServer):
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
        if path == "/api/btciface/snapshot.json":
            return self._btciface_json()
        if path in ("/interface", "/btciface", "/portfolio"):
            return self._serve_html("dashboard_btc_interface.html")
        if path.startswith("/data/"):
            name = path[len("/data/") :]
            if ".." in name or "/" in name or name not in _DATA_FILES:
                self.send_error(404)
                return
            return self._serve_json(_DATA_FILES[name])
        if path in ("/", "/terminal", "/dashboard"):
            return self._serve_html("dashboard_btc_terminal.html")
        return super().do_GET()

    def _btciface_json(self) -> None:
        if _btciface_snapshot is None:
            body = json.dumps(
                {"ok": False, "error": "dashboard_server_btc_interface import failed"},
                ensure_ascii=False,
            ).encode("utf-8")
        else:
            body = json.dumps(_btciface_snapshot(), ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self, abs_path: str) -> None:
        try:
            with open(abs_path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self, name: str) -> None:
        path = os.path.join(DIR, name)
        try:
            with open(path, "rb") as f:
                body = f.read()
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
        bind_note = "::: (IPv4+IPv6)"
    except OSError as e:
        httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
        bind_note = f"0.0.0.0 (IPv4 only; :: failed: {e})"
    print(
        f"Sygnif BTC Terminal on http://{bind_note}:{PORT} — "
        f"prediction/training + /interface (Bybit demo view)"
    )
    httpd.serve_forever()


if __name__ == "__main__":
    _serve()
