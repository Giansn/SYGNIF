"""Optional FinancialData.net fundamentals (supplementary to Bybit + Sygnif TA).

Docs: https://financialdata.net/documentation
Auth: FINANCIALDATA_API_KEY — query param key= on each request.

Data is labeled in Telegram as *FDN* / not Sygnif. Many endpoints are Standard/Premium;
Free tier may return HTTP 403 — callers treat empty as "unavailable".
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

FDN_BASE = "https://financialdata.net/api/v1"
_CACHE: dict[str, tuple[float, Any]] = {}
_DEFAULT_TTL_SEC = 3600.0


def _api_key() -> str:
    return os.environ.get("FINANCIALDATA_API_KEY", "").strip()


def _fetch_json(path_after_v1: str, *, timeout: float = 25.0) -> Any | None:
    """path_after_v1 e.g. 'crypto-information?identifier=BTC' (no leading slash)."""
    key = _api_key()
    if not key:
        return None
    url = f"{FDN_BASE}/{path_after_v1}"
    url += "&" if "?" in url else "?"
    url += f"key={key}"
    try:
        r = requests.get(
            url,
            headers={"User-Agent": "Sygnif-finance-agent/1"},
            timeout=timeout,
        )
        if r.status_code != 200:
            logger.info("FDN HTTP %s for %s", r.status_code, path_after_v1.split("?")[0])
            return None
        return r.json()
    except requests.RequestException as e:
        logger.info("FDN request failed: %s", e)
        return None


def _cached_fetch(cache_key: str, path: str, ttl_sec: float = _DEFAULT_TTL_SEC) -> Any | None:
    now = time.monotonic()
    hit = _CACHE.get(cache_key)
    if hit is not None and (now - hit[0]) < ttl_sec:
        return hit[1]
    data = _fetch_json(path)
    if data is not None:
        _CACHE[cache_key] = (now, data)
    return data


def fetch_crypto_information_btc() -> dict[str, Any] | None:
    raw = _cached_fetch("crypto_info_BTC", "crypto-information?identifier=BTC")
    if not raw or not isinstance(raw, list) or len(raw) == 0:
        return None
    row = raw[0]
    return row if isinstance(row, dict) else None


def fetch_key_metrics_latest(identifier: str) -> dict[str, Any] | None:
    ident = identifier.strip().upper()
    if not ident:
        return None
    raw = _cached_fetch(f"key_metrics_{ident}", f"key-metrics?identifier={ident}")
    if not raw or not isinstance(raw, list) or len(raw) == 0:
        return None
    row = raw[0]
    return row if isinstance(row, dict) else None


def format_telegram_btc_fundamentals_one_line() -> str:
    """Single-line BTC profile for multi-asset briefing (cache shared with block)."""
    d = fetch_crypto_information_btc()
    if not d:
        return ""
    cap = d.get("market_cap")
    if not isinstance(cap, (int, float)) or cap <= 0:
        return ""
    return f"_FDN BTC profile (USD ref, not Bybit TA):_ `market_cap` ≈ *${cap / 1e12:.2f}T*"


def format_telegram_btc_fundamentals_block() -> str:
    """Short italic block for /btc — empty if no key, error, or empty payload."""
    d = fetch_crypto_information_btc()
    if not d:
        return ""
    lines = [
        "_Fundamentals (FinancialData.net, BTC/USD profile — *not* Bybit USDT TA):_",
    ]
    name = d.get("crypto_name") or "Bitcoin"
    cap = d.get("market_cap")
    circ = d.get("circulating_supply")
    mcap = d.get("fully_diluted_valuation")
    if isinstance(cap, (int, float)) and cap > 0:
        lines.append(f"• {name}: `market_cap` ≈ *${cap / 1e12:.2f}T*")
    if isinstance(circ, (int, float)) and circ > 0:
        lines.append(f"• `circulating_supply` ≈ *{circ / 1e6:.2f}M* BTC")
    if isinstance(mcap, (int, float)) and mcap > 0 and mcap != cap:
        lines.append(f"• `fully_diluted_valuation` ≈ *${mcap / 1e12:.2f}T*")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def format_telegram_equity_proxy_line(identifier: str) -> str:
    """One-line risk-on proxy for briefing (e.g. MSFT). Empty if unavailable."""
    ident = identifier.strip().upper()
    if not ident:
        return ""
    d = fetch_key_metrics_latest(ident)
    if not d:
        return ""
    pe = d.get("price_to_earnings_ratio")
    beta = d.get("one_year_beta")
    bits: list[str] = []
    if isinstance(pe, (int, float)):
        bits.append(f"P/E≈*{pe:.1f}*")
    if isinstance(beta, (int, float)):
        bits.append(f"1Y β≈*{beta:.2f}*")
    if not bits:
        return ""
    return f"_FDN equity proxy `{ident}` (not crypto):_ " + " · ".join(bits)


def write_btc_fundamentals_json(out_dir: Path, utc_iso: str) -> bool:
    """Fetch BTC crypto-information and write slim JSON; False if skipped/failed."""
    if not _api_key():
        return False
    raw = _fetch_json("crypto-information?identifier=BTC", timeout=30.0)
    if not raw or not isinstance(raw, list) or len(raw) == 0:
        return False
    d = raw[0]
    if not isinstance(d, dict):
        return False
    slim = {
        "generated_utc": utc_iso,
        "source": "FinancialData.net api/v1/crypto-information (supplementary)",
        "identifier": "BTC",
        "crypto_name": d.get("crypto_name"),
        "market_cap": d.get("market_cap"),
        "circulating_supply": d.get("circulating_supply"),
        "max_supply": d.get("max_supply"),
        "fully_diluted_valuation": d.get("fully_diluted_valuation"),
        "highest_price": d.get("highest_price"),
        "highest_price_date": d.get("highest_price_date"),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "btc_fdn_fundamentals.json").write_text(
        json.dumps(slim, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _CACHE["crypto_info_BTC"] = (time.monotonic(), raw)
    return True
