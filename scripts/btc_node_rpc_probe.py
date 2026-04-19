#!/usr/bin/env python3
"""
Probe **Bitcoin Core** JSON-RPC (read-only helpers).

Requires env: ``BITCOIN_RPC_COOKIE_FILE`` **or** ``BITCOIN_RPC_USER`` + ``BITCOIN_RPC_PASSWORD``
(optional ``BITCOIN_RPC_URL`` / host / port / ``BITCOIN_RPC_WALLET``).

Examples::

  BITCOIN_RPC_COOKIE_FILE=$HOME/.bitcoin/.cookie python3 scripts/btc_node_rpc_probe.py
  BITCOIN_RPC_USER=rpcuser BITCOIN_RPC_PASSWORD=rpcpass python3 scripts/btc_node_rpc_probe.py --method getblockcount
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_FA = _REPO / "finance_agent"
if str(_FA) not in sys.path:
    sys.path.insert(0, str(_FA))

from btc_rpc_client import BitcoinRpcError  # noqa: E402
from btc_rpc_client import bitcoin_rpc_call  # noqa: E402
from btc_rpc_client import fetch_chain_snapshot  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Bitcoin Core RPC probe")
    ap.add_argument(
        "--method",
        type=str,
        default="",
        help="Single RPC method (default: print chain snapshot bundle)",
    )
    ap.add_argument(
        "--params-json",
        type=str,
        default="[]",
        help="JSON array of params for --method (default [])",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write JSON to this path (default: stdout only)",
    )
    args = ap.parse_args()

    try:
        if args.method:
            params = json.loads(args.params_json)
            if not isinstance(params, list):
                raise ValueError("--params-json must be a JSON array")
            result = bitcoin_rpc_call(args.method, params)
            doc = {"method": args.method, "params": params, "result": result}
        else:
            doc = {"snapshot": fetch_chain_snapshot()}
    except (json.JSONDecodeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except BitcoinRpcError as e:
        print(f"RPC: {e}", file=sys.stderr)
        return 1

    text = json.dumps(doc, indent=2, default=str) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
