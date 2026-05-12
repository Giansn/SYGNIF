#!/usr/bin/env python3
"""sygnif-bybit — homegrown HTTP MCP server (Tailscale-only).

The "vault MCP". All Bybit + order-placement neurons go through here.
API keys NEVER leave X1 — clients only ever see signed responses.

Whitelist:
    public option (no auth):  option.health, option.chain, option.build
    auth option (env keys):   option.wallet
    sizing (no auth):         order.size
    demo trading (env+conf):  order.demo.{perp,option}
    live trading (3-gate):    order.live.{perp,option}

Transport: HTTP POST /rpc with JSON-RPC 2.0.
Auth: bearer token from env SYGNIF_BYBIT_MCP_TOKEN.
Bind: 100.71.122.115:9002 (Tailscale interface only — never 0.0.0.0).
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AGENT_DIR = Path(os.environ.get("SYGNIF_AGENT_DIR", str(Path.home() / "sygnif-agent")))
sys.path.insert(0, str(AGENT_DIR))
import sygnif_neurons as N  # type: ignore

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "sygnif-bybit"
SERVER_VERSION = "0.1.0"
BIND_HOST = os.environ.get("SYGNIF_BYBIT_MCP_BIND", "127.0.0.1")
BIND_PORT = int(os.environ.get("SYGNIF_BYBIT_MCP_PORT", "9002"))
TOKEN = os.environ.get("SYGNIF_BYBIT_MCP_TOKEN", "").strip()


def log(msg: str) -> None:
    sys.stderr.write(f"[{SERVER_NAME}] {msg}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# whitelist — every tool here is gated by either the neuron itself or by
# SYGNIF's modes.resolve(). The MCP just routes the call.
# ---------------------------------------------------------------------------

ALLOWED_NEURONS = {
    # agent.* deterministic trade planner + reviewer
    "agent.trade.plan":       "Plan a single trade from regime/IV/portfolio. No execution.",
    "agent.trade.review":     "Review every open demo position, suggest HOLD/CLOSE/ROLL.",
    "agent.trade.execute":    "Plan + execute a demo trade in one call. Documents to swarm.",
    # public option (no auth)
    "option.health":          "Liveness probe — chain size + sample contract.",
    "option.chain":           "Live option ticker chain (bid/ask/mark/IV/Δ/Γ/V/Θ/OI).",
    "option.build":           "Templated multi-leg position → greeks + BEs + max P&L + payoff.",
    # auth option (uses BYBIT_OPTION_API_KEY env from X1 vault)
    "option.wallet":          "UTA wallet balance (USDC). Needs API key on X1.",
    # dashboard read-only: per-mode wallet + perp positions
    "wallet.live":            "Mainnet UTA wallet (read-only).",
    "wallet.demo":            "Demo UTA wallet (read-only, api-demo.bybit.com).",
    "wallet.demo.deposit":    "Top up demo UTA wallet via demo-apply-money (add only).",
    "perp.positions.live":    "Mainnet perp positions (read-only).",
    "perp.positions.demo":    "Demo perp positions (read-only).",
    "option.positions.live":  "Mainnet option positions (read-only).",
    "option.positions.demo":  "Demo option positions (read-only).",
    # sizing + balance
    "order.size":             "Position sizer (equity * risk_pct / stop). Capped.",
    "portfolio.demo":         "Bybit DEMO aggregated portfolio (canonical JSON shape) - single source of truth.",
    # demo trading — env + confirm gate enforced by order/modes.py
    "order.demo.perp":        "Demo perp via api-demo.bybit.com. Needs env + confirm.",
    "order.demo.option":      "Demo option strategy. Needs env + confirm.",
    "order.demo.option_leg":  "Demo single-leg option order (ad-hoc). Needs env + confirm.",
    # live trading — triple gate enforced by order/modes.py
    "order.live.perp":        "LIVE perp. Triple gate (env+TOKEN+confirm+i_understand).",
    "order.live.option":      "LIVE option strategy. Triple gate.",
}


def _tool_name(neuron: str) -> str:
    return neuron.replace(".", "_")


def _tools_list() -> list[dict]:
    out = []
    for nname, desc in ALLOWED_NEURONS.items():
        n = N.NEURONS.get(nname)
        if not n:
            continue
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
    neuron_name = None
    for nname in ALLOWED_NEURONS:
        if _tool_name(nname) == name:
            neuron_name = nname
            break
    if not neuron_name:
        raise KeyError(f"tool '{name}' not in whitelist")
    return N.run(neuron_name, args or {})


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


class Handler(BaseHTTPRequestHandler):
    server_version = f"{SERVER_NAME}/{SERVER_VERSION}"

    def log_message(self, fmt, *args):
        pass

    def _check_auth(self) -> bool:
        if not TOKEN:
            return True
        hdr = self.headers.get("Authorization", "")
        if not hdr.startswith("Bearer "):
            return False
        return secrets.compare_digest(hdr[len("Bearer "):].strip(), TOKEN)

    def _json(self, code, body):
        data = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            # report whether order-mode env vars are set (without leaking them).
            # legacy mode='paper' aliased to demo by modes.resolve() (back-compat).
            mode_env = (os.environ.get("SYGNIF_ORDERS_MODE", "demo") or "demo").lower()
            if mode_env == "paper":
                mode_env = "demo"
            self._json(200, {
                "ok": True, "server": SERVER_NAME, "version": SERVER_VERSION,
                "auth_required": bool(TOKEN), "tools": len(ALLOWED_NEURONS),
                "orders_mode_env": mode_env,
                "live_token_set": os.environ.get("SYGNIF_ORDERS_LIVE", "") == "clear-for-live",
                "demo_perp_key_present":   bool(os.environ.get("BYBIT_API_KEY", "")),
                "live_perp_key_present":   bool(os.environ.get("BYBIT_LIVE_API_KEY", "")),
                "demo_option_key_present": bool(os.environ.get("BYBIT_DEMO_OPTION_API_KEY", "")
                                                or os.environ.get("BYBIT_OPTION_API_KEY", "")),
                "live_option_key_present": bool(os.environ.get("BYBIT_LIVE_OPTION_API_KEY", "")),
            })
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
    if not TOKEN:
        log("WARNING: SYGNIF_BYBIT_MCP_TOKEN not set — auth disabled. Localhost only.")
    log(f"binding {BIND_HOST}:{BIND_PORT} · {len(ALLOWED_NEURONS)} tools whitelisted")
    log(f"orders_mode_env={os.environ.get('SYGNIF_ORDERS_MODE', 'demo')} "
        f"live_token={'set' if os.environ.get('SYGNIF_ORDERS_LIVE') == 'clear-for-live' else 'unset'}")
    srv = ThreadingHTTPServer((BIND_HOST, BIND_PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("shutdown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
