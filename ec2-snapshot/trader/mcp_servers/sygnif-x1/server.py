#!/usr/bin/env python3
"""sygnif-x1 — homegrown HTTP MCP server (Tailscale-only).

Wraps a curated subset of the sygnif_neurons.py registry. Exposes ONLY
read-mostly + bounded-mutation neurons. Order placement and IAM-mutation
neurons are NOT routed through this MCP.

Transport: HTTP POST /rpc with JSON-RPC 2.0 body (line per request).
Auth: bearer token from env SYGNIF_X1_MCP_TOKEN.
Bind: 100.71.122.115:9001 (Tailscale interface only — never 0.0.0.0).

Run:
    SYGNIF_X1_MCP_TOKEN=$(openssl rand -hex 32) \\
    SYGNIF_X1_MCP_BIND=100.71.122.115 \\
    python3 ~/sygnif-agent/mcp_servers/sygnif-x1/server.py

Or via the systemd-user unit at ~/.config/systemd/user/sygnif-x1-mcp.service.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# import the neuron registry
AGENT_DIR = Path(os.environ.get("SYGNIF_AGENT_DIR", str(Path.home() / "sygnif-agent")))
sys.path.insert(0, str(AGENT_DIR))
import sygnif_neurons as N  # type: ignore

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "sygnif-x1"
SERVER_VERSION = "0.1.0"
BIND_HOST = os.environ.get("SYGNIF_X1_MCP_BIND", "127.0.0.1")
BIND_PORT = int(os.environ.get("SYGNIF_X1_MCP_PORT", "9001"))
TOKEN = os.environ.get("SYGNIF_X1_MCP_TOKEN", "").strip()


# ---------------------------------------------------------------------------
# logging (stderr only)
# ---------------------------------------------------------------------------


def log(msg: str) -> None:
    sys.stderr.write(f"[{SERVER_NAME}] {msg}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# Curated whitelist — only THESE neurons are reachable via this MCP
# ---------------------------------------------------------------------------

ALLOWED_NEURONS = {
    # swarm.* read/write the X1 self-hosted swarm.db (the agent permanent memory)
    "swarm.recent":           "List newest N entries in a swarm partition (default partition + limit 25).",
    "swarm.search":           "Substring match across content/topic/agent_id; if swarm_id omitted searches all partitions.",
    "swarm.write":            "Append a new entry to a swarm partition. Returns the assigned uuid.",
    # discovery — market state cache
    "discovery.read":      "Latest Bybit-mainnet baseline summary (regime, BTC focus, options).",
    "discovery.refresh":   "Trigger a fresh Bybit-mainnet pass (60-120s, ingests to swarm).",
    # btc.* — read-only trading lab + ticker
    "btc.status":          "BTC trading lab summary (counts + latest forecast).",
    "btc.forecast":        "Last N BTC forecasts.",
    "btc.trades":          "Last N BTC opens/closes.",
    "btc.health":          "EC2 trading process / service health.",
    "btc.ticker":          "Symbol ticker (latest.json + live fallback). Any symbol.",
    "btc.tape.live":       "Fresh BTC tape direct from Bybit (no cache).",
    # bee
    "bee.health":          "Combined snapshot of X1 + EC2 Bee nodes.",
    "bee.fetch":           "Fetch swarm reference, return text payload.",
    # aws.eu1.* — read + bounded mutation
    "aws.eu1.status":      "Trading EC2 instance + system reachability.",
    "aws.eu1.endpoint":    "Current public IP / DNS / state.",
    "aws.eu1.metrics":     "CloudWatch CPU% over last N minutes.",
    "aws.eu1.ssm":         "Run a NAMED whitelisted SSM script (no raw shell).",
    # ec2 — whitelisted only
    "ec2.run":             "Run a NAMED whitelisted shell script over SSH.",
}

# Map neuron name → MCP tool name (replace dots with underscores)
def _tool_name(neuron: str) -> str:
    return neuron.replace(".", "_")


def _tools_list() -> list[dict]:
    out = []
    for nname, desc in ALLOWED_NEURONS.items():
        n = N.NEURONS.get(nname)
        if not n:
            continue
        # MCP-friendly schema: any object (we trust caller; neuron does its own validation)
        out.append({
            "name": _tool_name(nname),
            "description": desc + f"  [neuron={nname} cost={n.cost}]",
            "inputSchema": {
                "type": "object",
                "additionalProperties": True,
                "description": "see neuron description for params",
            },
        })
    return out


def _call_tool(name: str, args: dict) -> dict:
    # name is the MCP tool name (underscores) — map back
    neuron_name = None
    for nname in ALLOWED_NEURONS:
        if _tool_name(nname) == name:
            neuron_name = nname
            break
    if not neuron_name:
        raise KeyError(f"tool '{name}' not in whitelist")
    return N.run(neuron_name, args or {})


# ---------------------------------------------------------------------------
# JSON-RPC dispatch (shared with stdio MCPs)
# ---------------------------------------------------------------------------


def dispatch(req: dict) -> dict | None:
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }}
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": _tools_list()}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            out = _call_tool(name, args)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text",
                             "text": json.dumps(out, default=str, indent=2)}],
            }}
        except KeyError as e:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": str(e)}}
        except Exception as e:
            log(f"tool {name!r} crashed: {e}\n{traceback.format_exc()}")
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}}
    if method in ("resources/list", "prompts/list"):
        return {"jsonrpc": "2.0", "id": rid, "result": {method.split("/")[0]: []}}
    if rid is not None:
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = f"{SERVER_NAME}/{SERVER_VERSION}"

    def log_message(self, fmt, *args):
        # mute the default access log (we have our own)
        pass

    def _check_auth(self) -> bool:
        # Fail closed: empty TOKEN means main() refused to start, so this
        # branch should never run — but guard regardless so a future change
        # that loosens startup can't silently auth-bypass at request time.
        if not TOKEN:
            return False
        hdr = self.headers.get("Authorization", "")
        if not hdr.startswith("Bearer "):
            return False
        return secrets.compare_digest(hdr[len("Bearer "):].strip(), TOKEN)

    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"ok": True, "server": SERVER_NAME,
                              "version": SERVER_VERSION,
                              "auth_required": bool(TOKEN),
                              "tools": len(ALLOWED_NEURONS)})
            return
        self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path != "/rpc":
            self.send_response(404); self.end_headers(); return
        if not self._check_auth():
            self._json(401, {"error": "unauthorized — missing or wrong bearer token"})
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(n))
        except Exception as e:
            self._json(400, {"error": f"bad json: {e}"})
            return
        # support batch + single
        if isinstance(body, list):
            replies = [r for r in (dispatch(req) for req in body) if r is not None]
            self._json(200, replies)
        else:
            reply = dispatch(body)
            if reply is None:
                self.send_response(204); self.end_headers()
            else:
                self._json(200, reply)


def main() -> int:
    # Fail closed at startup if no bearer token is configured. Pre-2026-04-28
    # the server would silently auth-bypass with an empty TOKEN and run open;
    # config drift (missing env file, unset var) could expose control-plane
    # tools without auth. Set SYGNIF_X1_MCP_TOKEN before starting.
    if not TOKEN:
        log("FATAL: SYGNIF_X1_MCP_TOKEN is not set — refusing to start.")
        log("       generate one with:  openssl rand -hex 32")
        log(f"       and write it to:    ~/.sygnif/x1-mcp.env  (chmod 600)")
        return 2
    log(f"binding {BIND_HOST}:{BIND_PORT} · {len(ALLOWED_NEURONS)} tools whitelisted")
    srv = ThreadingHTTPServer((BIND_HOST, BIND_PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("shutdown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
