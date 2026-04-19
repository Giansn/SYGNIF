"""
Optional HTTPS fetch to populate ``BYBIT_DEMO_API_KEY`` / ``BYBIT_DEMO_API_SECRET``
before Swarm ``bf`` (btc_future) vote — e.g. when keys are distributed via an internal webhook.

**Security:** URL must be ``https://``. Use ``SYGNIF_SWARM_DEMO_KEYS_WEBHOOK_TOKEN`` (Bearer) or
``SYGNIF_SWARM_DEMO_KEYS_WEBHOOK_HEADER`` as ``Name: value``. Never log response bodies.

Env:
  SYGNIF_SWARM_DEMO_KEYS_WEBHOOK_URL   — HTTPS GET endpoint returning JSON
  SYGNIF_SWARM_DEMO_KEYS_WEBHOOK_TOKEN — optional Bearer token
  SYGNIF_SWARM_DEMO_KEYS_WEBHOOK_HEADER — optional extra header ``Name: value``
  SYGNIF_SWARM_DEMO_KEYS_WEBHOOK_TTL_SEC — cache TTL (default 300)
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

_LAST_FETCH_TS = 0.0
_LAST_OK = False


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def ensure_demo_keys_from_webhook() -> None:
    """If demo keys are missing, try one HTTPS GET and set ``os.environ`` (process-local)."""
    if os.environ.get("BYBIT_DEMO_API_KEY", "").strip() and os.environ.get(
        "BYBIT_DEMO_API_SECRET", ""
    ).strip():
        return
    url = os.environ.get("SYGNIF_SWARM_DEMO_KEYS_WEBHOOK_URL", "").strip()
    if not url:
        return
    if not url.lower().startswith("https://"):
        return
    global _LAST_FETCH_TS, _LAST_OK
    ttl = max(30.0, _env_float("SYGNIF_SWARM_DEMO_KEYS_WEBHOOK_TTL_SEC", 300.0))
    now = time.time()
    if _LAST_OK and (now - _LAST_FETCH_TS) < ttl:
        return
    req = urllib.request.Request(url, method="GET")
    token = os.environ.get("SYGNIF_SWARM_DEMO_KEYS_WEBHOOK_TOKEN", "").strip()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    extra = os.environ.get("SYGNIF_SWARM_DEMO_KEYS_WEBHOOK_HEADER", "").strip()
    if ":" in extra:
        name, val = extra.split(":", 1)
        req.add_header(name.strip(), val.strip())
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        _LAST_FETCH_TS = now
        _LAST_OK = False
        return
    if not isinstance(data, dict):
        _LAST_FETCH_TS = now
        _LAST_OK = False
        return
    key = data.get("BYBIT_DEMO_API_KEY") or data.get("api_key") or data.get("key")
    sec = data.get("BYBIT_DEMO_API_SECRET") or data.get("api_secret") or data.get("secret")
    if isinstance(key, str) and isinstance(sec, str) and len(key) >= 8 and len(sec) >= 8:
        os.environ["BYBIT_DEMO_API_KEY"] = key.strip()
        os.environ["BYBIT_DEMO_API_SECRET"] = sec.strip()
        _LAST_OK = True
    else:
        _LAST_OK = False
    _LAST_FETCH_TS = now
