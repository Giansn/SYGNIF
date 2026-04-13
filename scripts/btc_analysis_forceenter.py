#!/usr/bin/env python3
"""
Place (or **dry-plan**) a Freqtrade **forceenter** from **BTC analysis** JSON.

Reads:
  ``prediction_agent/btc_prediction_output.json``
  ``prediction_agent/training_channel_output.json`` (for R01 governance)

Uses ``prediction_agent/btc_analysis_order_signal.decide_forceenter_intent``.

**Safety:** default is **dry-run** (print only). Use ``--execute`` to POST ``/api/v1/forceenter``.
Requires ``force_entry_enable: true`` in the bot config.

Auth (same as dashboards / notify):
  ``FT_API_URL`` — default ``http://127.0.0.1:8081/api/v1`` (btc-0-1 demo: ``http://127.0.0.1:8185/api/v1``)
  ``FT_USER`` / ``FT_PASS`` or ``FREQTRADE_API_USERNAME`` / ``FT_FUTURES_PASS`` / ``API_PASSWORD``
  Optional: ``FT_BTC_0_1_PASS``; or ``api_server.password`` from ``SYGNIF_FT_BTC_0_1_CONFIG`` (paper-market JSON).

Freqtrade **token/login** expects **HTTP Basic** auth (not JSON body).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_PA = _REPO / "prediction_agent"
_DEFAULT_BTC01_CFG = _REPO / "user_data/config_btc_strategy_0_1_paper_market.json"
sys.path.insert(0, str(_PA))

from btc_analysis_order_signal import (  # noqa: E402
    decide_forceenter_intent,
    r01_bearish_from_training,
)


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        out = json.loads(path.read_text(encoding="utf-8"))
        return out if isinstance(out, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _password_from_ft_config(path: Path) -> str:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    return str((cfg.get("api_server") or {}).get("password") or "")


def _ft_credentials() -> tuple[str, str]:
    user = (
        os.environ.get("FT_USER")
        or os.environ.get("FT_BTC_0_1_USER")
        or os.environ.get("FREQTRADE_API_USERNAME")
        or os.environ.get("FT_FUTURES_USER")
        or "freqtrader"
    )
    pw = (
        os.environ.get("FT_BTC_0_1_PASS")
        or os.environ.get("FT_PASS")
        or os.environ.get("FT_FUTURES_PASS")
        or os.environ.get("FREQTRADE_API_PASSWORD")
        or os.environ.get("API_PASSWORD")
        or ""
    )
    if not pw:
        cfg_path = Path(os.environ.get("SYGNIF_FT_BTC_0_1_CONFIG", str(_DEFAULT_BTC01_CFG)))
        if cfg_path.is_file():
            pw = _password_from_ft_config(cfg_path)
    return user, pw


def ft_login(base: str, user: str, password: str) -> str:
    url = base.rstrip("/") + "/token/login"
    req = urllib.request.Request(url, method="POST")
    req.add_header(
        "Authorization",
        "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode(),
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    tok = data.get("access_token")
    if not tok:
        raise RuntimeError(f"token/login: no access_token in {data!r}")
    return str(tok)


def ft_forceenter(
    base: str,
    token: str,
    *,
    pair: str,
    side: str,
    ordertype: str,
    enter_tag: str,
    stake_amount: float | None,
    leverage: float | None,
) -> dict:
    url = base.rstrip("/") + "/forceenter"
    payload: dict = {"pair": pair, "side": side, "ordertype": ordertype, "entry_tag": enter_tag}
    if stake_amount is not None:
        payload["stakeamount"] = stake_amount
    if leverage is not None:
        payload["leverage"] = leverage
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    p = argparse.ArgumentParser(description="BTC analysis → Freqtrade forceenter (dry-run by default)")
    p.add_argument("--execute", action="store_true", help="POST /forceenter (default: print plan only)")
    p.add_argument(
        "--api-url",
        default=os.environ.get("FT_API_URL") or os.environ.get("FT_FUTURES_URL") or "http://127.0.0.1:8081/api/v1",
        help="Freqtrade REST base including /api/v1",
    )
    p.add_argument("--pair", default="BTC/USDT:USDT", help="Futures pair")
    p.add_argument("--ordertype", default="limit", choices=("limit", "market"), help="Order type for forceenter")
    p.add_argument("--allow-short", action="store_true", help="Allow BEARISH → short")
    p.add_argument("--min-dir-conf", type=float, default=65.0, help="Min confidence for direction_logistic fallback")
    p.add_argument("--stake-amount", type=float, default=None, help="Optional stakeamount for forceenter")
    p.add_argument("--leverage", type=float, default=None, help="Optional leverage for forceenter")
    args = p.parse_args()

    pred = _load_json(_PA / "btc_prediction_output.json")
    train = _load_json(_PA / "training_channel_output.json")
    if pred is None:
        print("btc_analysis_forceenter: missing or invalid btc_prediction_output.json", file=sys.stderr)
        return 2

    intent = decide_forceenter_intent(
        train,
        pred,
        allow_short=args.allow_short,
        direction_min_confidence=args.min_dir_conf,
    )
    if intent is None:
        print(
            json.dumps(
                {
                    "action": "skip",
                    "reason": "no intent (consensus neutral / missing, R01 bearish blocks long, or BEARISH without --allow-short)",
                    "r01_bearish": r01_bearish_from_training(train or {}),
                    "prediction_generated": pred.get("generated_utc"),
                },
                indent=2,
            )
        )
        return 0

    plan = {
        "action": "forceenter" if args.execute else "dry_run",
        "pair": args.pair,
        "side": intent["side"],
        "ordertype": args.ordertype,
        "enter_tag": intent["enter_tag"],
        "reason": intent["reason"],
        "prediction_generated": pred.get("generated_utc"),
        "training_generated": (train or {}).get("generated_utc"),
    }
    print(json.dumps(plan, indent=2))

    if not args.execute:
        print("\n(No POST) Re-run with --execute after enabling force_entry_enable on the bot.", file=sys.stderr)
        return 0

    user, password = _ft_credentials()
    if not password:
        print("btc_analysis_forceenter: set FT_PASS, FT_FUTURES_PASS, or API_PASSWORD", file=sys.stderr)
        return 2
    try:
        token = ft_login(args.api_url, user, password)
        result = ft_forceenter(
            args.api_url,
            token,
            pair=args.pair,
            side=intent["side"],
            ordertype=args.ordertype,
            enter_tag=intent["enter_tag"],
            stake_amount=args.stake_amount,
            leverage=args.leverage,
        )
        print(json.dumps({"ok": True, "response": result}, indent=2))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(json.dumps({"ok": False, "status": e.code, "body": body}, indent=2), file=sys.stderr)
        return 1
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
