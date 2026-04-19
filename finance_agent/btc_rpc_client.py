"""
**Bitcoin Core JSON-RPC** (local or tunneled node).

Uses HTTP POST with Basic auth **or** a ``.cookie`` file (same format as Bitcoin Core: ``__cookie__:password``).

**Env**

- ``BITCOIN_RPC_URL`` — full base URL, e.g. ``http://127.0.0.1:8332`` (optional; overrides host/port/ssl)
- ``BITCOIN_RPC_HOST`` — default ``127.0.0.1``
- ``BITCOIN_RPC_PORT`` — default ``8332``
- ``BITCOIN_RPC_SSL`` — ``1``/``true`` for ``https://`` when not using ``BITCOIN_RPC_URL``
- ``BITCOIN_RPC_USER`` / ``BITCOIN_RPC_PASSWORD`` — Basic auth
- ``BITCOIN_RPC_COOKIE_FILE`` — path to cookie file (if set, used **instead of** user/password)
- ``BITCOIN_RPC_WALLET`` — optional multi-wallet segment (``/wallet/<name>``)

**Example**

.. code-block:: bash

   export BITCOIN_RPC_COOKIE_FILE=$HOME/.bitcoin/.cookie
   python3 -c "from btc_rpc_client import bitcoin_rpc_call; print(bitcoin_rpc_call('getblockchaininfo'))"

Or with user/password (e.g. ``bitcoin.conf`` ``rpcuser`` / ``rpcpassword``):

.. code-block:: bash

   export BITCOIN_RPC_USER=bitcoinrpc
   export BITCOIN_RPC_PASSWORD=secret
   python3 scripts/btc_node_rpc_probe.py
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class BitcoinRpcError(RuntimeError):
    """RPC transport or ``error`` field from bitcoind."""


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _base_url() -> str:
    raw = (os.environ.get("BITCOIN_RPC_URL") or "").strip().rstrip("/")
    if raw:
        return raw
    host = (os.environ.get("BITCOIN_RPC_HOST") or "127.0.0.1").strip()
    port = (os.environ.get("BITCOIN_RPC_PORT") or "8332").strip()
    scheme = "https" if _truthy("BITCOIN_RPC_SSL") else "http"
    return f"{scheme}://{host}:{port}"


def _wallet_path() -> str:
    w = (os.environ.get("BITCOIN_RPC_WALLET") or "").strip()
    if not w:
        return ""
    return "/wallet/" + urllib.parse.quote(w, safe="")


def _basic_auth_header() -> str:
    cookie_path = (os.environ.get("BITCOIN_RPC_COOKIE_FILE") or "").strip()
    if cookie_path:
        p = Path(cookie_path).expanduser()
        line = p.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        if ":" not in line:
            raise BitcoinRpcError(f"cookie file {p} missing user:password format")
        user, pw = line.split(":", 1)
        raw = f"{user}:{pw}".encode("utf-8")
    else:
        user = (os.environ.get("BITCOIN_RPC_USER") or "").strip()
        pw = (os.environ.get("BITCOIN_RPC_PASSWORD") or "").strip()
        if not user or not pw:
            raise BitcoinRpcError(
                "Set BITCOIN_RPC_COOKIE_FILE or both BITCOIN_RPC_USER and BITCOIN_RPC_PASSWORD"
            )
        raw = f"{user}:{pw}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def bitcoin_rpc_call(
    method: str,
    params: list[Any] | None = None,
    *,
    timeout_sec: float = 60.0,
) -> Any:
    """
    Call ``method`` with ``params`` (Bitcoin Core JSON-RPC).

    Returns the JSON ``result`` or raises ``BitcoinRpcError``.
    """
    base = _base_url()
    path = _wallet_path()
    url = base + path
    body = json.dumps(
        {"jsonrpc": "1.0", "id": "sygnif-btc-rpc", "method": method, "params": list(params or [])}
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": _basic_auth_header(),
            "Content-Type": "application/json",
            "User-Agent": "Sygnif-btc-rpc/1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw_txt = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        hint = ""
        if e.code == 401:
            hint = " (check cookie file or rpcuser/rpcpassword)"
        elif e.code == 404 and path:
            hint = " (wallet name wrong or node not in multi-wallet mode?)"
        raise BitcoinRpcError(f"HTTP {e.code}{hint}: {e.read().decode('utf-8', errors='replace')[:500]}") from e
    except urllib.error.URLError as e:
        raise BitcoinRpcError(f"connection failed: {e}") from e

    try:
        doc = json.loads(raw_txt)
    except json.JSONDecodeError as e:
        raise BitcoinRpcError(f"invalid JSON from node: {raw_txt[:200]!r}") from e

    if not isinstance(doc, dict):
        raise BitcoinRpcError(f"unexpected RPC envelope: {doc!r}")
    err = doc.get("error")
    if err is not None and err != {}:
        raise BitcoinRpcError(f"RPC error: {err}")
    return doc.get("result")


def fetch_chain_snapshot() -> dict[str, Any]:
    """Convenience bundle for dashboards (best-effort; omits failing calls)."""
    out: dict[str, Any] = {}
    for method, key in (
        ("getblockchaininfo", "blockchain"),
        ("getnetworkinfo", "network"),
        ("getmempoolinfo", "mempool"),
    ):
        try:
            out[key] = bitcoin_rpc_call(method, [])
        except BitcoinRpcError as e:
            out[key] = {"_error": str(e)}
    try:
        h = bitcoin_rpc_call("getbestblockhash", [])
        out["best_block_hash"] = h
        if isinstance(h, str) and h:
            hdr = bitcoin_rpc_call("getblockheader", [h])
            out["best_block_header"] = hdr
    except BitcoinRpcError as e:
        out["best_block"] = {"_error": str(e)}
    return out
