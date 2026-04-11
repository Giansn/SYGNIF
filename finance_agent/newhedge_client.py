"""
Optional client for NewHedge metrics API (e.g. BTC vs altcoins correlation).

Official pattern (see https://docs.newhedge.io/api):

  GET /api/v2/metrics/:chart_slug/:metric_name?api_token=YOUR_TOKEN

Unauthenticated calls return **401**. Use ``NEWHEDGE_API_KEY`` (24-char token
from account settings) — passed as the ``api_token`` query parameter.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

ALTCOINS_CORRELATION_USD = (
    "https://newhedge.io/api/v2/metrics/altcoins-correlation/altcoins_price_usd"
)


def _request_json(url: str, timeout_sec: float) -> tuple[Any | None, str | None]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "SygnifFinanceAgent/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return None, f"HTTP {e.code}: {body[:500]}"
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        logger.warning("newhedge fetch failed: %s", e)
        return None, str(e)
    try:
        return json.loads(raw), None
    except json.JSONDecodeError:
        return None, "invalid JSON from NewHedge"


def fetch_altcoins_correlation_usd(
    *,
    api_key: str | None = None,
    timeout_sec: float = 20.0,
) -> tuple[Any | None, str | None]:
    """
    Return ``(payload, None)`` on success, or ``(None, error_message)``.

    ``payload`` is the decoded JSON body (typically a list of ``[timestamp_ms, value]``).
    """
    key = (api_key or os.environ.get("NEWHEDGE_API_KEY", "") or "").strip()
    if not key:
        return None, "NEWHEDGE_API_KEY not set"
    q = urllib.parse.urlencode({"api_token": key})
    url = f"{ALTCOINS_CORRELATION_USD}?{q}"
    return _request_json(url, timeout_sec)


def _last_point_series(payload: Any) -> tuple[int | None, float | None]:
    """Best-effort: last ``[ts_ms, v]`` from a list-of-pairs response."""
    if not isinstance(payload, list) or not payload:
        return None, None
    last = payload[-1]
    if isinstance(last, (list, tuple)) and len(last) >= 2:
        try:
            return int(last[0]), float(last[1])
        except (TypeError, ValueError):
            return None, None
    return None, None


def format_telegram_altcoins_correlation_block() -> str:
    """
    Compact Telegram / markdown snippet for **BTC vs alts** NewHedge metric.

    Returns empty string if ``NEWHEDGE_API_KEY`` is unset. On HTTP/parse errors,
    returns a one-line italic hint. Labels source as **not** Sygnif TA (parity
    with third-party separation in ``briefing.md``).
    """
    key = (os.environ.get("NEWHEDGE_API_KEY", "") or "").strip()
    if not key:
        return ""
    payload, err = fetch_altcoins_correlation_usd(api_key=key)
    if err:
        return f"_NewHedge (BTC–alts corr.): {err}_"
    ts_ms, val = _last_point_series(payload)
    if val is None:
        return (
            "_NewHedge (BTC–alts corr.): JSON received but not a simple time series — "
            "see `btc_newhedge_altcoins_correlation.json` after `pull_btc_context`._"
        )
    ts_s = "?"
    if ts_ms is not None:
        try:
            ts_s = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OSError, OverflowError, ValueError):
            pass
    return (
        f"_NewHedge BTC–alts (`altcoins_price_usd`): last value `{val:g}` @ {ts_s} UTC — "
        "third-party series, not Sygnif TA / Bybit._"
    )
