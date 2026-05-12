"""SYGNIF live-Bybit position adapter.

Fetches open option + linear positions from Bybit (demo or live) and
projects them into a dict shape compatible with the paper-portfolio
position dicts that agent.exit_logic.decide_exit consumes.

This is the bridge that makes review_positions() see EXCHANGE truth, not
just the paper journal — closing the gap that left 5 live positions
unmanaged for 8+ hours during the 2026-05-01 incident.

Output shape (each position dict):
  {
    "id":               stable hash, sha256(symbol|side)[:8] — survives updatedTime churn,
    "source":           "bybit_live",
    "symbol":           "BTC-2MAY26-78000-P-USDT" or "BTCUSDT",
    "side":             "Buy" | "Sell",
    "qty":              float (contracts),
    "entry":            float (avgPrice),
    "mark":             float (markPrice),
    "unrealized_pnl_usdc": float,
    "stop_loss_price":  str | None,
    "take_profit_price":str | None,
    "trailing_stop":    str | None,
    "updated_time_ms":  int,
    "instrument":       "option" | "perp",
    "structure":        "long_premium" | "short_premium" | "perp" | "unknown",
    "expiry_iso":       "2026-05-02" | None,
    "dte_h":            float | None,
    "label":            "" (empty — live positions have no label, structure is inferred),
    # back-compat for decide_exit's paper-shape readers:
    "marks":            [{"symbol", "side", "qty", "now_mark", "entry_mark", "now_iv"}],
    "legs":             [{"side", "symbol", "qty", "entry_price"}],
    "opened_ts_utc":    None  (Bybit doesn't expose first-fill ts; updatedTime is the latest mod),
  }
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ENV_FILE = Path.home() / ".sygnif" / "bybit-mcp.env"

BASE_DEMO = "https://api-demo.bybit.com"
BASE_LIVE = "https://api.bybit.com"
RECV_WINDOW = "5000"
TIMEOUT = 10

_BYBIT_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _load_env() -> dict:
    env: dict[str, str] = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k] = v.strip().strip('"').strip("'")
    return env


def _signed_get(base: str, path: str, params: dict, key: str, secret: str) -> dict:
    qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    ts = str(int(time.time() * 1000))
    pre = ts + key + RECV_WINDOW + qs
    sig = hmac.new(secret.encode(), pre.encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        f"{base}{path}?{qs}",
        headers={
            "X-BAPI-API-KEY": key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": RECV_WINDOW,
            "X-BAPI-SIGN": sig,
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def _stable_pid(symbol: str, side: str) -> str:
    return hashlib.sha256(f"{symbol}|{side}".encode()).hexdigest()[:8]


def _parse_option_symbol(sym: str) -> tuple[str | None, float | None]:
    """`BTC-2MAY26-78000-P-USDT` → (`2026-05-02`, dte_hours_at_expiry).
    Bybit options settle at 08:00 UTC.
    """
    try:
        parts = sym.split("-")
        if len(parts) < 3:
            return None, None
        expiry_token = parts[1]
        # day digits + 3 letter month + 2 digit yy
        i = 0
        while i < len(expiry_token) and expiry_token[i].isdigit():
            i += 1
        dd = int(expiry_token[:i])
        mmm = expiry_token[i:i + 3]
        yy = int(expiry_token[i + 3:])
        month = _BYBIT_MONTHS.get(mmm.upper())
        if not month:
            return None, None
        year = 2000 + yy
        expiry_iso = f"{year:04d}-{month:02d}-{dd:02d}"
        return expiry_iso, None  # dte_h computed against now in classify
    except Exception:
        return None, None


def _hours_until(expiry_iso: str | None, now_utc: datetime) -> float | None:
    if not expiry_iso:
        return None
    try:
        e = datetime.strptime(expiry_iso + "T08:00:00+0000",
                              "%Y-%m-%dT%H:%M:%S%z")
        return (e - now_utc).total_seconds() / 3600.0
    except Exception:
        return None


def _classify_option(side: str, sym: str) -> str:
    """Single-leg structure classifier. Aggregated multi-leg structures don't
    exist on Bybit's position list — each leg is its own position. So:
        SHORT (any) → short_premium
        LONG  (any) → long_premium
    """
    s = (side or "").lower()
    if s == "sell":
        return "short_premium"
    if s == "buy":
        return "long_premium"
    return "unknown"


def fetch_open_positions(mode: str = "demo",
                          *, key: str | None = None,
                          secret: str | None = None,
                          now_utc: datetime | None = None,
                          instrument: str | None = None) -> list[dict]:
    """Pull open option + linear positions and return paper-compatible dicts.

    mode='demo' uses api-demo.bybit.com; 'live' uses api.bybit.com.
    Credentials default to ENV_FILE (~/.sygnif/bybit-mcp.env), then env vars.

    instrument filter (added 2026-05-04 for per-instrument daemon split):
        None      → fetch BOTH option + linear (default; legacy behaviour)
        "option"  → fetch ONLY option positions
        "perp"    → fetch ONLY linear (perp) positions
        "linear"  → alias of "perp"
    """
    if mode not in ("demo", "live"):
        raise ValueError(f"unknown mode {mode!r}")
    fetch_option = instrument is None or instrument == "option"
    fetch_perp = instrument is None or instrument in ("perp", "linear")
    if not (fetch_option or fetch_perp):
        raise ValueError(f"unknown instrument filter {instrument!r}")
    base = BASE_DEMO if mode == "demo" else BASE_LIVE
    env = _load_env()
    KEY = key or env.get("BYBIT_API_KEY") or os.environ.get("BYBIT_API_KEY")
    SECRET = secret or env.get("BYBIT_API_SECRET") or os.environ.get("BYBIT_API_SECRET")
    if not KEY or not SECRET:
        return []
    now_utc = now_utc or datetime.now(timezone.utc)

    out: list[dict] = []

    # Option positions
    if fetch_option:
        try:
            cursor = ""
            for _ in range(5):
                params = {"category": "option", "settleCoin": "USDT", "limit": 200}
                if cursor:
                    params["cursor"] = cursor
                r = _signed_get(base, "/v5/position/list", params, KEY, SECRET)
                if r.get("retCode") != 0:
                    break
                res = r.get("result", {}) or {}
                rows = res.get("list", []) or []
                for p in rows:
                    qty = float(p.get("size", 0) or 0)
                    if qty <= 0:
                        continue
                    sym = p.get("symbol", "")
                    side = p.get("side", "")
                    entry = float(p.get("avgPrice") or 0)
                    mark = float(p.get("markPrice") or 0)
                    expiry_iso, _ = _parse_option_symbol(sym)
                    dte_h = _hours_until(expiry_iso, now_utc)
                    pid = _stable_pid(sym, side)
                    marks = [{
                        "symbol": sym, "side": side, "qty": qty,
                        "now_mark": mark, "entry_mark": entry,
                        "now_iv": float(p.get("ivIv") or p.get("iv") or 0) or 0.0,
                    }]
                    legs = [{"side": side, "symbol": sym, "qty": qty,
                             "entry_price": entry}]
                    out.append({
                        "id": pid,
                        "source": "bybit_live",
                        "symbol": sym,
                        "side": side,
                        "qty": qty,
                        "entry": entry,
                        "mark": mark,
                        "unrealized_pnl_usdc": float(p.get("unrealisedPnl") or 0),
                        "stop_loss_price": p.get("stopLoss") or None,
                        "take_profit_price": p.get("takeProfit") or None,
                        "trailing_stop": p.get("trailingStop") or None,
                        "updated_time_ms": int(p.get("updatedTime") or 0),
                        "instrument": "option",
                        "structure": _classify_option(side, sym),
                        "expiry_iso": expiry_iso,
                        "dte_h": dte_h,
                        "label": "",
                        "marks": marks,
                        "legs": legs,
                        "opened_ts_utc": None,
                    })
                cursor = res.get("nextPageCursor", "") or ""
                if not cursor:
                    break
        except Exception:
            pass

    # Linear (perp) positions
    if fetch_perp:
        try:
            params = {"category": "linear", "settleCoin": "USDT"}
            r = _signed_get(base, "/v5/position/list", params, KEY, SECRET)
            if r.get("retCode") == 0:
                for p in (r.get("result", {}) or {}).get("list", []) or []:
                    qty = float(p.get("size", 0) or 0)
                    if qty <= 0:
                        continue
                    sym = p.get("symbol", "")
                    side = p.get("side", "")
                    entry = float(p.get("avgPrice") or 0)
                    mark = float(p.get("markPrice") or 0)
                    pid = _stable_pid(sym, side)
                    legs = [{"side": side, "symbol": sym, "qty": qty,
                             "entry_price": entry}]
                    marks = [{"symbol": sym, "side": side, "qty": qty,
                              "now_mark": mark, "entry_mark": entry}]
                    out.append({
                        "id": pid,
                        "source": "bybit_live",
                        "symbol": sym,
                        "side": side,
                        "qty": qty,
                        "entry": entry,
                        "mark": mark,
                        "unrealized_pnl_usdc": float(p.get("unrealisedPnl") or 0),
                        "stop_loss_price": p.get("stopLoss") or None,
                        "take_profit_price": p.get("takeProfit") or None,
                        "trailing_stop": p.get("trailingStop") or None,
                        "updated_time_ms": int(p.get("updatedTime") or 0),
                        "instrument": "perp",
                        "structure": "perp",
                        "expiry_iso": None,
                        "dte_h": None,
                        "label": "",
                        "marks": marks,
                        "legs": legs,
                        "opened_ts_utc": None,
                    })
        except Exception:
            pass

    return out


def get_btc_funding_rate(*,
                          cache_path: Path = Path.home() / ".sygnif" / "funding-cache.json",
                          cache_ttl_s: int = 300) -> float | None:
    """Latest BTCUSDT-perp funding rate from public market data, cached 5min.

    Sign indicates pressure direction: positive → longs pay shorts (long-heavy
    book, MMs short bias); negative → shorts pay longs (short-heavy, MM long
    bias). Used by Q5 (funding-flip exit) in decide_exit.
    """
    try:
        if cache_path.exists():
            d = json.loads(cache_path.read_text())
            if (time.time() - d.get("ts", 0)) < cache_ttl_s and "rate" in d:
                return float(d["rate"])
    except Exception:
        pass
    try:
        url = ("https://api.bybit.com/v5/market/tickers?"
               "category=linear&symbol=BTCUSDT")
        with urllib.request.urlopen(url, timeout=8) as r:
            d = json.loads(r.read())
        rows = (d.get("result", {}) or {}).get("list", []) or []
        if not rows:
            return None
        rate = float(rows[0].get("fundingRate") or 0)
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"rate": rate, "ts": time.time()}))
        except Exception:
            pass
        return rate
    except Exception:
        return None


def get_btc_24h_swing(*,
                      cache_path: Path = Path.home() / ".sygnif" / "btc-swing-cache.json",
                      cache_ttl_s: int = 600) -> dict | None:
    """24-hour high/low for BTCUSDT-perp from public 1h klines, cached 10min.
    Returns {"high": float, "low": float, "ts": unix} or None.
    Used by Q4 (stop-cluster avoidance).
    """
    try:
        if cache_path.exists():
            d = json.loads(cache_path.read_text())
            if (time.time() - d.get("ts", 0)) < cache_ttl_s:
                if "high" in d and "low" in d:
                    return d
    except Exception:
        pass
    try:
        url = ("https://api.bybit.com/v5/market/kline?"
               "category=linear&symbol=BTCUSDT&interval=60&limit=24")
        with urllib.request.urlopen(url, timeout=8) as r:
            d = json.loads(r.read())
        rows = (d.get("result", {}) or {}).get("list", []) or []
        if not rows:
            return None
        highs = [float(row[2]) for row in rows]
        lows = [float(row[3]) for row in rows]
        out = {"high": max(highs), "low": min(lows), "ts": time.time()}
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(out))
        except Exception:
            pass
        return out
    except Exception:
        return None


def get_btc_atr_h1(period: int = 14, *,
                    cache_path: Path = Path.home() / ".sygnif" / "atr-cache.json",
                    cache_ttl_s: int = 300) -> float | None:
    """ATR(period) on 1h BTC bars from public market data, cached 5min.

    Public endpoint, no auth. Used by decide_exit's S3 trail-width formula.
    Returns None on any failure — caller falls back to a percentage of risk.
    """
    try:
        if cache_path.exists():
            d = json.loads(cache_path.read_text())
            if (time.time() - d.get("ts", 0)) < cache_ttl_s and d.get("atr"):
                return float(d["atr"])
    except Exception:
        pass

    try:
        url = ("https://api.bybit.com/v5/market/kline?"
               f"category=linear&symbol=BTCUSDT&interval=60&limit={period+5}")
        with urllib.request.urlopen(url, timeout=8) as r:
            d = json.loads(r.read())
        rows = (d.get("result", {}) or {}).get("list", []) or []
        rows = list(reversed(rows))  # newest-first → oldest-first
        if len(rows) < period + 1:
            return None
        trs: list[float] = []
        for i in range(1, len(rows)):
            h = float(rows[i][2])
            lw = float(rows[i][3])
            pc = float(rows[i - 1][4])
            trs.append(max(h - lw, abs(h - pc), abs(lw - pc)))
        atr = sum(trs[:period]) / period
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(
                {"atr": atr, "ts": time.time(), "period": period}))
        except Exception:
            pass
        return atr
    except Exception:
        return None
