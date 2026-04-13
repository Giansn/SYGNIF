#!/usr/bin/env python3
"""Sygnif BTC Terminal — static dashboard + read-only JSON for prediction / training channel."""
from __future__ import annotations

import http.server
import os
import socketserver

_CLIENT_SOCK_TIMEOUT = 120

PORT = int(os.environ.get("SYGNIF_BTC_TERMINAL_PORT", "8891"))
DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(DIR)

_PREDICTION_DIR = os.path.join(DIR, "prediction_agent")
_LETSCRASH_DIR = os.path.join(DIR, "letscrash")

_DATA_FILES: dict[str, str] = {
    "training_channel_output.json": os.path.join(_PREDICTION_DIR, "training_channel_output.json"),
    "btc_prediction_output.json": os.path.join(_PREDICTION_DIR, "btc_prediction_output.json"),
    "btc_strategy_0_1_rule_registry.json": os.path.join(
        _LETSCRASH_DIR, "btc_strategy_0_1_rule_registry.json"
    ),
}


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


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path.startswith("/data/"):
            name = path[len("/data/") :]
            if ".." in name or "/" in name or name not in _DATA_FILES:
                self.send_error(404)
                return
            return self._serve_json(_DATA_FILES[name])
        if path in ("/", "/terminal", "/dashboard"):
            return self._serve_html("dashboard_btc_terminal.html")
        return super().do_GET()

    def _serve_json(self, abs_path: str) -> None:
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
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


print(f"Sygnif BTC Terminal on http://0.0.0.0:{PORT} (prediction + training JSON)")
ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
