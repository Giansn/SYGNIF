#!/usr/bin/env python3
"""
Open a position on **freqtrade-btc-0-1** (Bybit demo when configured) via Freqtrade REST.

**Paths** (relative to API base, default ``http://127.0.0.1:8185/api/v1``):

1. ``POST …/token/login`` — HTTP Basic auth (username + password) → JWT ``access_token``
2. ``POST …/forceenter`` — header ``Authorization: Bearer <token>``, JSON body per
   ``ForceEnterPayload`` (``pair``, ``side``, ``ordertype``, ``stakeamount``, ``entry_tag``)

The bot must have ``force_entry_enable`` (set automatically in demo runtime when
``BYBIT_DEMO_*`` are present). Use an ``entry_tag`` starting with ``manual_`` so
``BTC_Strategy_0_1`` bypasses the Sygnif volume-regime gate on BTC-only whitelists.

Run from repo root (after ``docker compose --profile btc-0-1 up -d``)::

    python3 scripts/ft_btc_0_1_forceenter.py
    python3 scripts/ft_btc_0_1_forceenter.py --side short --stake 200 --ordertype limit

Password resolution (first hit wins): ``FT_BTC_0_1_PASS``, ``FT_PASS``,
``FT_FUTURES_PASS``, ``API_PASSWORD``, ``FREQTRADE_API_PASSWORD``, else
``api_server.password`` from ``SYGNIF_FT_BTC_0_1_CONFIG`` (default: paper-market JSON).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CONFIG = _REPO_ROOT / "user_data/config_btc_strategy_0_1_paper_market.json"
_DEFAULT_API_BASE = "http://127.0.0.1:8185/api/v1"


def _password_from_config(path: Path) -> str:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    return str((cfg.get("api_server") or {}).get("password") or "")


def _resolve_auth() -> tuple[str, str]:
    user = (
        os.environ.get("FT_BTC_0_1_USER")
        or os.environ.get("FT_USER")
        or os.environ.get("FREQTRADE_API_USERNAME")
        or "freqtrader"
    )
    pw = (
        os.environ.get("FT_BTC_0_1_PASS")
        or os.environ.get("FT_PASS")
        or os.environ.get("FT_FUTURES_PASS")
        or os.environ.get("API_PASSWORD")
        or os.environ.get("FREQTRADE_API_PASSWORD")
        or ""
    )
    if not pw:
        cfg_path = Path(os.environ.get("SYGNIF_FT_BTC_0_1_CONFIG", str(_DEFAULT_CONFIG)))
        if cfg_path.is_file():
            pw = _password_from_config(cfg_path)
    if not pw:
        print(
            "ft_btc_0_1_forceenter: set FT_BTC_0_1_PASS (or API_PASSWORD / FT_PASS), "
            "or api_server.password in user_data/config_btc_strategy_0_1_paper_market.json",
            file=sys.stderr,
        )
        sys.exit(2)
    return user, pw


def _login(api_base: str, user: str, password: str) -> str:
    url = api_base.rstrip("/") + "/token/login"
    req = Request(url, method="POST")
    req.add_header(
        "Authorization",
        "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode(),
    )
    with urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    tok = data.get("access_token")
    if not tok:
        raise RuntimeError(f"token/login: missing access_token in {data!r}")
    return str(tok)


def _forceenter(api_base: str, token: str, payload: dict) -> dict:
    url = api_base.rstrip("/") + "/forceenter"
    body = json.dumps(payload).encode("utf-8")
    req = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(req, timeout=120) as resp:
        return json.load(resp)


def main() -> int:
    p = argparse.ArgumentParser(
        description="POST /api/v1/forceenter on freqtrade-btc-0-1 (host port 8185 by default).",
    )
    p.add_argument(
        "--api-url",
        default=os.environ.get("FT_BTC_0_1_API_URL", _DEFAULT_API_BASE),
        help=f"API root including /api/v1 (default: {_DEFAULT_API_BASE})",
    )
    p.add_argument("--pair", default="BTC/USDT:USDT")
    p.add_argument("--side", default="long", choices=("long", "short"))
    p.add_argument("--ordertype", default="market", choices=("limit", "market"))
    p.add_argument("--stake", type=float, default=400.0, dest="stakeamount")
    p.add_argument(
        "--entry-tag",
        default="manual_demo_open",
        help="Prefix manual_ recommended (strategy bypass for BTC-only whitelist).",
    )
    p.add_argument(
        "--scalp-r03",
        action="store_true",
        help="Set entry_tag to BTC-0.1-R03 (scalping sleeve: R03 TP/RSI exits + SL floor in strategy).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print endpoints + payload only, no HTTP calls.",
    )
    args = p.parse_args()
    api_base = args.api_url.rstrip("/")

    entry_tag = "BTC-0.1-R03" if args.scalp_r03 else args.entry_tag
    payload = {
        "pair": args.pair,
        "side": args.side,
        "ordertype": args.ordertype,
        "stakeamount": args.stakeamount,
        "entry_tag": entry_tag,
    }

    if args.dry_run:
        print(
            json.dumps(
                {
                    "step_1_post": f"{api_base}/token/login",
                    "step_2_post": f"{api_base}/forceenter",
                    "forceenter_body": payload,
                },
                indent=2,
            )
        )
        return 0

    user, pw = _resolve_auth()
    try:
        token = _login(api_base, user, pw)
        result = _forceenter(api_base, token, payload)
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(json.dumps({"ok": False, "status": e.code, "body": body}, indent=2), file=sys.stderr)
        return 1
    except OSError as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        return 1

    print(json.dumps({"ok": True, "response": result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
