#!/usr/bin/env python3
"""Sygnif Futures Dashboard server on port 8889 — proxies API requests to Freqtrade."""
import http.server
import os
import urllib.request

PORT = 8889
API = "http://localhost:8081"
DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(DIR)


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # Disable browser caching for the dashboard HTML/JS so deploys are
        # picked up on a normal refresh instead of requiring Ctrl+Shift+R.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._proxy()
        if self.path in ("/", "/dashboard"):
            self.path = "/dashboard_futures_full.html"
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self._proxy()
        self.send_error(404)

    def do_OPTIONS(self):
        if self.path.startswith("/api/"):
            return self._proxy()
        self.send_error(404)

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


print(f"Dashboard running on http://0.0.0.0:{PORT} → API {API}")
http.server.HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
