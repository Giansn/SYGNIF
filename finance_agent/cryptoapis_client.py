"""Optional [Crypto APIs](https://cryptoapis.io) REST: BTC mainnet block + market-data (not Sygnif TA).

Auth: header ``X-API-Key`` per https://developers.cryptoapis.io — base URL ``https://rest.cryptoapis.io``.
Env: ``cryptoapi_Token`` (preferred; matches host .env), or ``CRYPTOAPI_TOKEN`` / ``CRYPTOAPIS_API_KEY``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE = "https://rest.cryptoapis.io"
USER_AGENT = "Sygnif-finance-agent/1"


def api_key() -> str:
    for k in ("cryptoapi_Token", "CRYPTOAPI_TOKEN", "CRYPTOAPIS_API_KEY"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return ""


def _headers(key: str) -> dict[str, str]:
    return {
        "X-API-Key": key,
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }


def _get_json(key: str, path: str, *, timeout: float = 25.0) -> Any | None:
    url = f"{BASE}{path}"
    try:
        r = requests.get(url, headers=_headers(key), timeout=timeout)
        if r.status_code != 200:
            logger.info("Crypto APIs HTTP %s for %s", r.status_code, path.split("?")[0])
            return None
        return r.json()
    except requests.RequestException as e:
        logger.info("Crypto APIs request failed: %s", e)
        return None


def _item(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    item = data.get("item")
    return item if isinstance(item, dict) else None


def fetch_btc_foundation_raw(key: str) -> dict[str, Any]:
    """Fetch last BTC mainnet block, asset profile, and BTC/USD reference rate (best-effort)."""
    out: dict[str, Any] = {
        "last_block_mainnet": _get_json(key, "/blocks/utxo/bitcoin/mainnet/latest/details"),
        "asset_btc": _get_json(key, "/market-data/assets/by-symbol/BTC"),
        "exchange_rate_btc_usd": _get_json(key, "/market-data/exchange-rates/by-symbol/btc/usd"),
    }
    return out


def build_foundation_summary(raw: dict[str, Any]) -> dict[str, Any]:
    """Compact fields for dashboard + Telegram (no secrets)."""
    s: dict[str, Any] = {}
    block = raw.get("last_block_mainnet")
    item = _item(block)
    if item:
        s["block_height"] = item.get("height")
        s["transactions_count"] = item.get("transactionsCount")
        h = item.get("hash")
        if isinstance(h, str) and len(h) >= 12:
            s["block_hash_prefix"] = f"{h[:10]}…"
        ts = item.get("timestamp")
        if ts is not None:
            s["block_timestamp_unix"] = ts

    asset = raw.get("asset_btc")
    aitem = _item(asset)
    if aitem:
        if aitem.get("symbol"):
            s["asset_symbol"] = aitem.get("symbol")
        # common optional fields — presence varies by plan
        for k in ("name", "assetType", "marketCap", "circulatingSupply", "maxSupply"):
            if aitem.get(k) is not None:
                s[k] = aitem.get(k)

    rate = raw.get("exchange_rate_btc_usd")
    rit = _item(rate)
    if rit:
        if rit.get("rate") is not None:
            s["btc_usd_rate"] = rit.get("rate")
        if rit.get("calculationTimestamp") is not None:
            s["rate_calculation_timestamp"] = rit.get("calculationTimestamp")
        if rit.get("source") is not None:
            s["rate_source"] = rit.get("source")

    return s


def write_btc_foundation_json(out_dir: Path, utc_iso: str) -> bool:
    """Write ``btc_cryptoapis_foundation.json`` if key is set and at least one endpoint succeeds."""
    key = api_key()
    if not key:
        return False
    raw = fetch_btc_foundation_raw(key)
    if not any(raw.values()):
        return False
    summary = build_foundation_summary(raw)
    doc = {
        "generated_utc": utc_iso,
        "source": "Crypto APIs — Bitcoin mainnet + market-data (not Sygnif TA / not Bybit)",
        "summary": summary,
        "responses": raw,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "btc_cryptoapis_foundation.json").write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True


def _bundle_path() -> Path:
    return Path(__file__).resolve().parent / "btc_specialist" / "data" / "btc_cryptoapis_foundation.json"


def format_telegram_foundation_block() -> str:
    """Optional multi-line block for ``/btc`` from offline bundle (no live API call)."""
    p = _bundle_path()
    if not p.is_file():
        return ""
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    summary = doc.get("summary")
    if not isinstance(summary, dict) or not summary:
        return ""
    lines = ["*Foundation (Crypto APIs)* — _on-chain / market ref, not Sygnif TA_"]
    if summary.get("block_height") is not None:
        tc = summary.get("transactions_count")
        extra = f", txs `{tc}`" if tc is not None else ""
        lines.append(f"• Last BTC mainnet block: `{summary['block_height']}`{extra}")
    hp = summary.get("block_hash_prefix")
    if hp:
        lines.append(f"• Hash: `{hp}`")
    if summary.get("btc_usd_rate") is not None:
        lines.append(f"• CA BTC/USD ref rate: `{summary['btc_usd_rate']}`")
    if summary.get("marketCap") is not None:
        lines.append(f"• Market cap (metadata): `{summary['marketCap']}`")
    if len(lines) <= 1:
        return ""
    return "\n".join(lines)


def format_telegram_foundation_one_line() -> str:
    """Single italic line for Telegram briefing appendix."""
    blk = format_telegram_foundation_block()
    if not blk:
        return ""
    # first data line only
    for line in blk.split("\n")[1:3]:
        if line.strip().startswith("•"):
            return "_Crypto APIs:_ " + line.replace("•", "").strip()
    return ""
