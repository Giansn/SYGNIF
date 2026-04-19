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


def signed_trading_rest_base() -> str:
    """
    Which signed-trading REST host ``_credentials()`` would use (no key load; no I/O).

    **API demo** is ``https://api-demo.bybit.com`` (UTA demo / ``BYBIT_DEMO_*``) — same **USDT linear**
    products as mainnet, not live ``api.bybit.com`` balances.
    """
    if _hedge_mainnet_enabled() and _hedge_live_ok():
        return "https://api.bybit.com"
    return "https://api-demo.bybit.com"


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


def _post_with_creds(
    path: str,
    body: Dict[str, Any],
    key: str,
    secret: str,
    base: str,
) -> Dict[str, Any]:
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


def _get_with_creds(
    path: str,
    params: Dict[str, str],
    key: str,
    secret: str,
    base: str,
) -> Dict[str, Any]:
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


def _get(path: str, params: Dict[str, str]) -> Dict[str, Any]:
    key, secret, base = _credentials()
    return _get_with_creds(path, params, key, secret, base)


def get_open_orders_realtime_linear(
    symbol: str = "BTCUSDT",
    *,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
) -> Dict[str, Any]:
    """
    GET /v5/order/realtime — open working orders for USDT linear symbol.

    If ``api_key`` / ``api_secret`` are set, uses **Bybit demo** host (``api-demo``) with those keys
    (e.g. ``BYBIT_DEMO_GRID_*`` for Nautilus grid isolation). Otherwise uses ``_credentials()``.
    """
    sym = (symbol or "").replace("/", "").upper().strip() or "BTCUSDT"
    if api_key and api_secret:
        return _get_with_creds(
            "/v5/order/realtime",
            {"category": "linear", "symbol": sym},
            api_key.strip(),
            api_secret.strip(),
            "https://api-demo.bybit.com",
        )
    return _get("/v5/order/realtime", {"category": "linear", "symbol": sym})


def set_linear_leverage(symbol: str, leverage: str) -> Dict[str, Any]:
    """
    POST /v5/position/set-leverage — USDT linear; same buy/sell leverage (one-way or symmetric).

    ``leverage`` string e.g. ``"20"`` (Bybit max depends on symbol / risk tier).
    """
    sym = (symbol or "").replace("/", "").upper().strip()
    lev = str(leverage).strip()
    if not sym or not lev:
        return {"retCode": -1, "retMsg": "symbol and leverage required"}
    body: Dict[str, Any] = {
        "category": "linear",
        "symbol": sym,
        "buyLeverage": lev,
        "sellLeverage": lev,
    }
    return _post("/v5/position/set-leverage", body)


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
    *,
    order_link_id: Optional[str] = None,
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
    if order_link_id:
        ol = str(order_link_id).strip()
        if ol:
            body["orderLinkId"] = ol
    return _post("/v5/order/create", body)


def linear_mark_and_last_price(symbol: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Public GET /v5/market/tickers — USDT linear ``markPrice`` and ``lastPrice``.

    Uses the same REST host as ``signed_trading_rest_base()`` (demo vs mainnet).
    """
    sym = (symbol or "").replace("/", "").upper().strip()
    if not sym:
        return None, None
    base = signed_trading_rest_base().rstrip("/")
    params: Dict[str, str] = {"category": "linear", "symbol": sym}
    query_string = urllib.parse.urlencode(sorted(params.items()))
    url = f"{base}/v5/market/tickers?{query_string}"
    try:
        r = requests.get(url, timeout=15)
        j = r.json()
    except Exception:
        return None, None
    if j.get("retCode") != 0:
        return None, None
    lst = (j.get("result") or {}).get("list") or []
    if not lst or not isinstance(lst[0], dict):
        return None, None
    row = lst[0]

    def _px(key: str) -> Optional[float]:
        raw = row.get(key)
        if raw is None or str(raw).strip() == "":
            return None
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None

    return _px("markPrice"), _px("lastPrice")


def create_limit_order(
    symbol: str,
    side: str,
    qty: str,
    position_idx: int,
    price: str,
    *,
    time_in_force: str = "PostOnly",
    reduce_only: bool = False,
    order_link_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    POST /v5/order/create — Limit order on linear USDT (e.g. ``PostOnly`` maker rest below/above mark).
    """
    sym = (symbol or "").replace("/", "").upper().strip()
    s = (side or "").strip().capitalize()
    if not sym or s not in ("Buy", "Sell"):
        return {"retCode": -1, "retMsg": "symbol and side Buy|Sell required"}
    if position_idx not in (0, 1, 2):
        return {"retCode": -1, "retMsg": "positionIdx must be 0, 1, or 2"}
    px = str(price).strip()
    if not px:
        return {"retCode": -1, "retMsg": "price required"}
    tif = (time_in_force or "PostOnly").strip()
    body: Dict[str, Any] = {
        "category": "linear",
        "symbol": sym,
        "side": s,
        "orderType": "Limit",
        "qty": str(qty).strip(),
        "price": px,
        "timeInForce": tif,
        "positionIdx": position_idx,
    }
    if reduce_only:
        body["reduceOnly"] = True
    if order_link_id:
        ol = str(order_link_id).strip()
        if ol:
            body["orderLinkId"] = ol
    return _post("/v5/order/create", body)


def position_list(
    symbol: str,
    *,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
) -> Dict[str, Any]:
    """GET /v5/position/list for linear symbol."""
    sym = (symbol or "").replace("/", "").upper().strip()
    if not sym:
        return {"retCode": -1, "retMsg": "symbol required"}
    params: Dict[str, str] = {"category": "linear", "symbol": sym}
    if api_key and api_secret:
        return _get_with_creds(
            "/v5/position/list",
            params,
            api_key.strip(),
            api_secret.strip(),
            "https://api-demo.bybit.com",
        )
    return _get("/v5/position/list", params)


def wallet_balance_unified_coin(
    coin: str = "USDT",
    *,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
) -> Dict[str, Any]:
    """
    GET /v5/account/wallet-balance — UNIFIED account, single ``coin`` row.

    Uses demo (``api-demo``) with ``BYBIT_DEMO_*`` unless ``api_key`` / ``api_secret``
    are passed (same host), e.g. grid-isolated keys.
    """
    c = (coin or "USDT").upper().strip() or "USDT"
    params: Dict[str, str] = {"accountType": "UNIFIED", "coin": c}
    if api_key and api_secret:
        return _get_with_creds(
            "/v5/account/wallet-balance",
            params,
            api_key.strip(),
            api_secret.strip(),
            "https://api-demo.bybit.com",
        )
    return _get("/v5/account/wallet-balance", params)


def closed_pnl_linear(
    symbol: str,
    limit: str = "100",
    cursor: str = "",
    *,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
) -> Dict[str, Any]:
    """GET /v5/position/closed-pnl for USDT linear symbol (paginated via ``cursor``)."""
    sym = (symbol or "").replace("/", "").upper().strip()
    if not sym:
        return {"retCode": -1, "retMsg": "symbol required"}
    params: Dict[str, str] = {"category": "linear", "symbol": sym, "limit": str(limit)}
    cur = (cursor or "").strip()
    if cur:
        params["cursor"] = cur
    if api_key and api_secret:
        return _get_with_creds(
            "/v5/position/closed-pnl",
            params,
            api_key.strip(),
            api_secret.strip(),
            "https://api-demo.bybit.com",
        )
    return _get("/v5/position/closed-pnl", params)


def cancel_all_open_orders_linear(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    """
    POST /v5/order/cancel-all — cancels **all** open orders for the USDT-linear symbol.

    Uses demo (``BYBIT_DEMO_*`` + ``api-demo``) or mainnet credentials per ``_credentials()``.
    """
    sym = (symbol or "").replace("/", "").upper().strip()
    if not sym:
        return {"retCode": -1, "retMsg": "symbol required"}
    return _post("/v5/order/cancel-all", {"category": "linear", "symbol": sym})


def set_trading_stop_linear(
    symbol: str,
    *,
    position_idx: int,
    tpsl_mode: str = "Full",
    take_profit: Optional[str] = None,
    stop_loss: Optional[str] = None,
    trailing_stop: Optional[str] = None,
    active_price: Optional[str] = None,
    tp_trigger_by: str = "MarkPrice",
    sl_trigger_by: str = "MarkPrice",
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
) -> Dict[str, Any]:
    """
    POST /v5/position/trading-stop — TP/SL and/or trailing stop on an open USDT-linear position.

    Pass ``take_profit="0"``, ``stop_loss="0"``, or ``trailing_stop="0"`` to cancel that side (Bybit).

    ``positionIdx``: 0 one-way; 1 long (hedge); 2 short (hedge).

    If ``api_key`` / ``api_secret`` are set, uses **api-demo** (same pattern as ``position_list``).
    """
    sym = (symbol or "").replace("/", "").upper().strip()
    if not sym:
        return {"retCode": -1, "retMsg": "symbol required"}
    if position_idx not in (0, 1, 2):
        return {"retCode": -1, "retMsg": "positionIdx must be 0, 1, or 2"}
    mode = (tpsl_mode or "Full").strip()
    if mode not in ("Full", "Partial"):
        return {"retCode": -1, "retMsg": "tpslMode must be Full or Partial"}
    tp_set = take_profit is not None
    sl_set = stop_loss is not None
    ts_set = trailing_stop is not None
    if not tp_set and not sl_set and not ts_set:
        return {"retCode": -1, "retMsg": "at least one of take_profit, stop_loss, trailing_stop required"}
    body: Dict[str, Any] = {
        "category": "linear",
        "symbol": sym,
        "positionIdx": int(position_idx),
        "tpslMode": mode,
    }
    if tp_set:
        body["takeProfit"] = str(take_profit).strip()
        body["tpTriggerBy"] = tp_trigger_by
    if sl_set:
        body["stopLoss"] = str(stop_loss).strip()
        body["slTriggerBy"] = sl_trigger_by
    if ts_set:
        body["trailingStop"] = str(trailing_stop).strip()
    if active_price is not None:
        body["activePrice"] = str(active_price).strip()
    if api_key and api_secret:
        return _post_with_creds(
            "/v5/position/trading-stop",
            body,
            api_key.strip(),
            api_secret.strip(),
            "https://api-demo.bybit.com",
        )
    return _post("/v5/position/trading-stop", body)
