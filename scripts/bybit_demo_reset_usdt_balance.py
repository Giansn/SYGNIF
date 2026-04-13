#!/usr/bin/env python3
"""
Reset Bybit **demo trading** (api-demo.bybit.com) UNIFIED wallet toward a target **USDT** balance.

1) ``adjustType=1``: reduce listed coins by current ``walletBalance`` (from wallet query).
2) ``adjustType=0``: add ``USDT`` via ``amountStr`` (Bybit demo-apply-money).

Requires: ``pybit``, ``BYBIT_DEMO_API_KEY`` / ``BYBIT_DEMO_API_SECRET`` in the environment
or in ``--env-file`` (KEY=value lines; only those two keys are read).

Demo endpoint rate limit: **1 req/min** on ``demo-apply-money`` — do not spam.

Usage:
  BYBIT_DEMO_API_KEY=... BYBIT_DEMO_API_SECRET=... python3 scripts/bybit_demo_reset_usdt_balance.py --target 10000
  python3 scripts/bybit_demo_reset_usdt_balance.py --env-file ~/xrp_claude_bot/.env --target 10000
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from pybit.misc import Misc
from pybit.unified_trading import HTTP


def _parse_demo_keys(path: Path) -> tuple[str, str]:
    names = frozenset({"BYBIT_DEMO_API_KEY", "BYBIT_DEMO_API_SECRET"})
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k in names:
            out[k] = v
    return out.get("BYBIT_DEMO_API_KEY", ""), out.get("BYBIT_DEMO_API_SECRET", "")


def main() -> int:
    ap = argparse.ArgumentParser(description="Bybit demo: reset UNIFIED wallet to target USDT")
    ap.add_argument("--target", type=int, default=10_000, help="USDT to add after clearing positives")
    ap.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="optional .env path; reads BYBIT_DEMO_API_KEY / BYBIT_DEMO_API_SECRET only",
    )
    args = ap.parse_args()

    dk = os.environ.get("BYBIT_DEMO_API_KEY", "").strip()
    ds = os.environ.get("BYBIT_DEMO_API_SECRET", "").strip()
    if args.env_file is not None:
        dk2, ds2 = _parse_demo_keys(args.env_file)
        dk, ds = dk or dk2, ds or ds2
    if not dk or not ds:
        print("Missing BYBIT_DEMO_API_KEY / BYBIT_DEMO_API_SECRET", file=sys.stderr)
        return 1

    session = HTTP(testnet=False, demo=True, api_key=dk, api_secret=ds)
    path = session.endpoint + Misc.REQUEST_DEMO_TRADING_FUNDS

    r = session.get_wallet_balance(accountType="UNIFIED")
    if r.get("retCode") != 0:
        print("wallet-balance failed:", r, file=sys.stderr)
        return 1

    coins: list[dict[str, str]] = []
    for w in r.get("result", {}).get("list") or []:
        for c in w.get("coin") or []:
            wb = c.get("walletBalance")
            if not wb:
                continue
            try:
                if float(wb) <= 0:
                    continue
            except ValueError:
                continue
            coins.append({"coin": c["coin"], "amountStr": str(wb)})

    if coins:
        body = {"adjustType": 1, "utaDemoApplyMoney": coins}
        out = session._submit_request(method="POST", path=path, query=body, auth=True)
        if out.get("retCode") != 0:
            print("reduce failed:", out, file=sys.stderr)
            return 1
        time.sleep(2.1)

    tgt = str(int(args.target))
    body2 = {"adjustType": 0, "utaDemoApplyMoney": [{"coin": "USDT", "amountStr": tgt}]}
    out2 = session._submit_request(method="POST", path=path, query=body2, auth=True)
    if out2.get("retCode") != 0:
        print("add USDT failed:", out2, file=sys.stderr)
        return 1

    r3 = session.get_wallet_balance(accountType="UNIFIED")
    eq = (r3.get("result") or {}).get("list", [{}])[0].get("totalEquity")
    print("ok totalEquity=", eq, "ret=", out2.get("retMsg"))
    for c in (r3.get("result") or {}).get("list", [{}])[0].get("coin") or []:
        wb = c.get("walletBalance")
        if wb and float(wb) > 0:
            print(" ", c.get("coin"), wb)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
