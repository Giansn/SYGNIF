#!/usr/bin/env python3
"""Sygnif Futures Dashboard server on port 8889."""
import http.server
import os

PORT = 8889
DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(DIR)

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/dashboard':
            self.path = '/dashboard_futures_full.html'
        return super().do_GET()

    def log_message(self, format, *args):
        pass  # Suppress logs

print(f"Dashboard running on http://0.0.0.0:{PORT}")
http.server.HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
