"""
Bybit USDT linear v5: signed REST for hedge mode (switch-mode) and optional orders.

Uses BYBIT_DEMO_* on api-demo.bybit.com unless OVERSEER_BYBIT_HEDGE_MAINNET=YES
(and OVERSEER_HEDGE_LIVE_OK=YES), then BYBIT_API_KEY / BYBIT_API_SECRET on api.bybit.com.

Not used by Freqtrade; intended for Nautilus / manual hedge workflows.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
from typing import Any, Dict, Optional, Tuple

import requests

# Bybit v5: mode 0 = merged single (one-way), 3 = hedge (both sides).
MODE_ONE_WAY = 0
MODE_HEDGE = 3


def _hedge_mainnet_enabled() -> bool:
    v = os.environ.get("OVERSEER_BYBIT_HEDGE_MAINNET", "").strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def _hedge_live_ok() -> bool:
    v = os.environ.get("OVERSEER_HEDGE_LIVE_OK", "").strip().upper()
    return v == "YES"


def _credentials() -> Tuple[str, str, str]:
    if _hedge_mainnet_enabled():
        if not _hedge_live_ok():
            raise RuntimeError(
                "OVERSEER_BYBIT_HEDGE_MAINNET is set but OVERSEER_HEDGE_LIVE_OK is not YES; refusing live keys."
            )
        key = os.environ.get("BYBIT_API_KEY", "").strip()
        secret = os.environ.get("BYBIT_API_SECRET", "").strip()
        base = "https://api.bybit.com"
    else:
        key = os.environ.get("BYBIT_DEMO_API_KEY", "").strip()
        secret = os.environ.get("BYBIT_DEMO_API_SECRET", "").strip()
        base = "https://api-demo.bybit.com"
    if not key or not secret:
        raise RuntimeError(
            "Missing Bybit API credentials for hedge client "
            "(BYBIT_DEMO_API_KEY/BYBIT_DEMO_API_SECRET for demo, or "
            "BYBIT_API_KEY/BYBIT_API_SECRET + OVERSEER_BYBIT_HEDGE_MAINNET=YES + OVERSEER_HEDGE_LIVE_OK=YES)."
        )
    return key, secret, base


def _recv_window() -> str:
    return os.environ.get("BYBIT_RECV_WINDOW", "5000").strip() or "5000"


def _sign_post(secret: str, ts: str, api_key: str, recv: str, body_str: str) -> str:
    pre = ts + api_key + recv + body_str
    return hmac.new(secret.encode("utf-8"), pre.encode("utf-8"), hashlib.sha256).hexdigest()


def _sign_get(secret: str, ts: str, api_key: str, recv: str, query_string: str) -> str:
    pre = ts + api_key + recv + query_string
    return hmac.new(secret.encode("utf-8"), pre.encode("utf-8"), hashlib.sha256).hexdigest()


def _post(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    key, secret, base = _credentials()
    recv = _recv_window()
    ts = str(int(time.time() * 1000))
    body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    sign = _sign_post(secret, ts, key, recv, body_str)
    headers = {
        "Content-Type": "application/json",
        "X-BAPI-API-KEY": key,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
    }
    url = base.rstrip("/") + path
    r = requests.post(url, data=body_str.encode("utf-8"), headers=headers, timeout=30)
    try:
        return r.json()
    except Exception:
        return {"retCode": -1, "retMsg": r.text[:500], "httpStatus": r.status_code}


def _get(path: str, params: Dict[str, str]) -> Dict[str, Any]:
    key, secret, base = _credentials()
    recv = _recv_window()
    ts = str(int(time.time() * 1000))
    query_string = urllib.parse.urlencode(sorted(params.items()))
    sign = _sign_get(secret, ts, key, recv, query_string)
    headers = {
        "X-BAPI-API-KEY": key,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
    }
    url = base.rstrip("/") + path + "?" + query_string
    r = requests.get(url, headers=headers, timeout=30)
    try:
        return r.json()
    except Exception:
        return {"retCode": -1, "retMsg": r.text[:500], "httpStatus": r.status_code}


def switch_position_mode(symbol: str, mode: int) -> Dict[str, Any]:
    """
    POST /v5/position/switch-mode — symbol e.g. BTCUSDT; mode 0 one-way, 3 hedge.
    """
    sym = (symbol or "").replace("/", "").upper().strip()
    if not sym:
        return {"retCode": -1, "retMsg": "symbol required"}
    if mode not in (MODE_ONE_WAY, MODE_HEDGE):
        return {"retCode": -1, "retMsg": f"invalid mode {mode}; use {MODE_ONE_WAY} or {MODE_HEDGE}"}
    body = {"category": "linear", "symbol": sym, "mode": mode}
    return _post("/v5/position/switch-mode", body)


def create_market_order(
    symbol: str,
    side: str,
    qty: str,
    position_idx: int,
    reduce_only: bool = False,
) -> Dict[str, Any]:
    """
    POST /v5/order/create — Market order on linear USDT.
    positionIdx: 1 = long leg (Buy), 2 = short leg (Sell) in hedge mode.
    """
    sym = (symbol or "").replace("/", "").upper().strip()
    s = (side or "").strip().capitalize()
    if not sym or s not in ("Buy", "Sell"):
        return {"retCode": -1, "retMsg": "symbol and side Buy|Sell required"}
    if position_idx not in (0, 1, 2):
        return {"retCode": -1, "retMsg": "positionIdx must be 0, 1, or 2"}
    body: Dict[str, Any] = {
        "category": "linear",
        "symbol": sym,
        "side": s,
        "orderType": "Market",
        "qty": str(qty).strip(),
        "positionIdx": position_idx,
    }
    if reduce_only:
        body["reduceOnly"] = True
    return _post("/v5/order/create", body)


def position_list(symbol: str) -> Dict[str, Any]:
    """GET /v5/position/list for linear symbol."""
    sym = (symbol or "").replace("/", "").upper().strip()
    if not sym:
        return {"retCode": -1, "retMsg": "symbol required"}
    return _get("/v5/position/list", {"category": "linear", "symbol": sym})


def cancel_all_open_orders_linear(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    """
    POST /v5/order/cancel-all — cancels **all** open orders for the USDT-linear symbol.

    Uses demo (``BYBIT_DEMO_*`` + ``api-demo``) or mainnet credentials per ``_credentials()``.
    """
    sym = (symbol or "").replace("/", "").upper().strip()
    if not sym:
        return {"retCode": -1, "retMsg": "symbol required"}
    return _post("/v5/order/cancel-all", {"category": "linear", "symbol": sym})
