#!/usr/bin/env python3
"""
Ethereum Swarm Bee storage upload/download for Sygnif prediction signals.

Uses the existing bee-light node (port 1633) and pre-funded stamp to upload
prediction outputs and swarm signals to the decentralized Swarm network.
The 139 connected peers propagate and cache the chunks.

Env:
- SYGNIF_BEE_API_URL       — Bee API base (default http://127.0.0.1:1633)
- SYGNIF_BEE_STAMP_ID      — override auto-detect of first usable stamp
- SYGNIF_BEE_STORAGE_TAGS  — comma list of data types to upload (default: swarm,predict,neurolinked)
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from finance_agent.bee_swarm_bridge import bee_api_base_url, _env_float


def _bee_base() -> str:
    return (bee_api_base_url() or "http://127.0.0.1:1633").rstrip("/")


def get_usable_stamp() -> str | None:
    """Return first usable stamp batchID, or env override."""
    override = (os.environ.get("SYGNIF_BEE_STAMP_ID") or "").strip()
    if override:
        return override
    timeout = max(2.0, _env_float("SYGNIF_BEE_API_TIMEOUT_SEC", 4.0))
    try:
        raw = urllib.request.urlopen(f"{_bee_base()}/stamps", timeout=timeout).read()
        stamps = json.loads(raw).get("stamps", [])
        for s in stamps:
            if s.get("usable"):
                return str(s["batchID"])
    except Exception:
        pass
    return None


def upload_bytes(data: bytes, stamp: str, content_type: str = "application/json",
                 tag: str | None = None) -> str | None:
    """
    Upload raw bytes to Swarm. Returns the Swarm hash reference (hex) or None on failure.
    """
    timeout = max(5.0, _env_float("SYGNIF_BEE_API_TIMEOUT_SEC", 4.0))
    headers = {
        "Content-Type": content_type,
        "Swarm-Postage-Batch-Id": stamp,
        "Swarm-Pin": "false",
        "Swarm-Encrypt": "false",
    }
    if tag:
        headers["Swarm-Tag"] = tag
    req = urllib.request.Request(
        f"{_bee_base()}/bytes",
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
        return str(body.get("reference") or "")
    except urllib.error.HTTPError as exc:
        return None
    except Exception:
        return None


def upload_json(payload: dict[str, Any], stamp: str) -> str | None:
    """Serialize dict to JSON and upload. Returns Swarm hash or None."""
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return upload_bytes(data, stamp)


def download_json(ref: str) -> dict[str, Any] | None:
    """Fetch a JSON payload by Swarm hash reference."""
    timeout = max(5.0, _env_float("SYGNIF_BEE_API_TIMEOUT_SEC", 4.0))
    try:
        raw = urllib.request.urlopen(f"{_bee_base()}/bytes/{ref}", timeout=timeout).read()
        return json.loads(raw)
    except Exception:
        return None


def upload_prediction_signal(
    payload: dict[str, Any],
    signal_type: str = "swarm_signal",
) -> dict[str, Any]:
    """
    Upload a prediction/swarm signal JSON to Bee storage.

    Returns dict with ``ref`` (Swarm hash), ``stamp``, ``ts_utc``, ``ok``.
    """
    stamp = get_usable_stamp()
    if not stamp:
        return {"ok": False, "detail": "no_usable_stamp"}
    wrapped = {
        "sygnif_signal_type": signal_type,
        "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "payload": payload,
    }
    ref = upload_json(wrapped, stamp)
    if not ref:
        return {"ok": False, "detail": "upload_failed", "stamp": stamp}
    return {
        "ok": True,
        "ref": ref,
        "stamp": stamp[:16] + "…",
        "signal_type": signal_type,
        "ts_utc": wrapped["ts_utc"],
    }
