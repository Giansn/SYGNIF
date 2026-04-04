#!/usr/bin/env python3
"""Sygnif Dashboard server on port 8888."""
import http.server
import os

PORT = 8888
DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(DIR)

handler = http.server.SimpleHTTPServer if hasattr(http.server, 'SimpleHTTPServer') else http.server.SimpleHTTPRequestHandler

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/dashboard':
            self.path = '/dashboard.html'
        return super().do_GET()

    def log_message(self, format, *args):
        pass  # Suppress logs

print(f"Dashboard running on http://0.0.0.0:{PORT}")
http.server.HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
