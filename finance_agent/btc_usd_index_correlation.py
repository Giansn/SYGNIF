"""
BTC vs **broad USD strength** (FRED ``DTWEXBGS`` — trade-weighted dollar index).

**Narrative (often, not a law):** USD index **up** ↔ many USD-priced risk assets, including BTC, **under
pressure** on medium horizons → **negative return correlation** is common but **regime-dependent** (Fed
surprises, crypto-native catalysts, stablecoin flows). **USDT** is not the DXY; this module compares BTC/USD
moves to a macro USD basket index.

Requires env ``FRED_API_KEY`` (free at https://fred.stlouisfed.org/docs/api/api_key.html).
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
import requests

FRED_OBS_URL = "https://api.stlouisfed.org/fred/series/observations"
DEFAULT_SERIES = "DTWEXBGS"  # Trade Weighted U.S. Dollar Index: Broad, Goods and Services


def fred_api_key() -> str:
    return (os.environ.get("FRED_API_KEY") or os.environ.get("SYGNIF_FRED_API_KEY") or "").strip()


def fetch_fred_levels(
    *,
    series_id: str = DEFAULT_SERIES,
    limit: int = 200,
    timeout: float = 30.0,
) -> tuple[list[tuple[date, float]] | None, str | None]:
    key = fred_api_key()
    if not key:
        return None, "missing FRED_API_KEY (or SYGNIF_FRED_API_KEY)"
    r = requests.get(
        FRED_OBS_URL,
        params={
            "series_id": series_id,
            "api_key": key,
            "file_type": "json",
            "sort_order": "asc",
            "limit": limit,
        },
        timeout=timeout,
    )
    if r.status_code != 200:
        return None, f"fred_http_{r.status_code}"
    data = r.json()
    obs = data.get("observations")
    if not isinstance(obs, list):
        return None, "fred_bad_payload"
    out: list[tuple[date, float]] = []
    for row in obs:
        ds = str(row.get("date") or "")
        raw_v = row.get("value")
        if raw_v in (None, ".", ""):
            continue
        try:
            d = date.fromisoformat(ds)
            v = float(raw_v)
        except (TypeError, ValueError):
            continue
        out.append((d, v))
    if len(out) < 10:
        return None, "fred_too_few_points"
    return out, None


def btc_daily_to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    dates: list[date] = []
    closes: list[float] = []
    for r in rows:
        try:
            ts_ms = int(r.get("t") or 0)
            c = float(r.get("c") or 0)
        except (TypeError, ValueError):
            continue
        if ts_ms <= 0 or c <= 0:
            continue
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).date()
        dates.append(dt)
        closes.append(c)
    df = pd.DataFrame({"date": dates, "btc_close": closes})
    return df.sort_values("date").drop_duplicates("date", keep="last")


def compute_btc_usd_index_correlation(
    btc_daily_rows: list[dict[str, Any]],
    *,
    series_id: str = DEFAULT_SERIES,
    windows: tuple[int, ...] = (20, 60),
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Align BTC daily (Bybit ``D`` candles) with FRED USD index; Pearson corr on **simple daily returns**.
    """
    levels, err = fetch_fred_levels(series_id=series_id, limit=260)
    if err or not levels:
        return None, err or "fred_fetch_failed"

    usd = pd.DataFrame(levels, columns=["date", "usd_index"])
    btc = btc_daily_to_frame(btc_daily_rows)
    if btc.empty or len(btc) < max(windows) + 2:
        return None, "btc_daily_too_short"

    m = btc.merge(usd, on="date", how="inner")
    if len(m) < max(windows) + 2:
        return None, "overlap_too_short"

    m = m.sort_values("date")
    m["btc_ret"] = m["btc_close"].pct_change()
    m["usd_ret"] = m["usd_index"].pct_change()
    clean = m.dropna(subset=["btc_ret", "usd_ret"])
    if len(clean) < max(windows) + 1:
        return None, "returns_overlap_too_short"

    corrs: dict[str, float | None] = {}
    for w in windows:
        tail = clean.tail(w)
        if len(tail) < max(5, w // 2):
            corrs[f"pearson_last_{w}d"] = None
            continue
        corrs[f"pearson_last_{w}d"] = float(tail["btc_ret"].corr(tail["usd_ret"]))

    last = clean.iloc[-1]
    doc: dict[str, Any] = {
        "fred_series_id": series_id,
        "fred_description": (
            "Trade Weighted U.S. Dollar Index: Broad, Goods and Services (higher = stronger USD vs basket)"
        ),
        "last_common_date": str(last["date"]),
        "last_btc_daily_return": round(float(last["btc_ret"]), 6),
        "last_usd_index_return": round(float(last["usd_ret"]), 6),
        "pearson_correlation_daily_returns": corrs,
        "interpretation": (
            "Negative values mean BTC and the broad USD index tended to move opposite ways over the window — "
            "consistent with 'USD up → BTC often down' on that sample. Correlation is not stable; use as context."
        ),
    }
    return doc, None


def write_btc_usd_index_correlation_json(
    btc_daily_path: str | os.PathLike[str],
    out_path: str | os.PathLike[str],
    *,
    series_id: str = DEFAULT_SERIES,
) -> tuple[bool, str | None]:
    p = os.fspath(btc_daily_path)
    try:
        raw = json.loads(open(p, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError) as exc:
        return False, str(exc)
    if not isinstance(raw, list):
        return False, "btc_daily_not_a_list"

    doc, err = compute_btc_usd_index_correlation(raw, series_id=series_id)
    if err or doc is None:
        return False, err

    utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "generated_utc": utc,
        "source": "FRED API + Bybit BTCUSDT daily closes (this repo)",
        "metric": "Pearson correlation of same-calendar-day simple returns (BTC vs USD broad index)",
        **doc,
    }
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except OSError as exc:
        return False, str(exc)
    return True, None
