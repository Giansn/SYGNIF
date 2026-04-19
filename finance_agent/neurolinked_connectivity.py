"""
NeuroLinked HTTP reachability for **Swarm / operator** diagnostics.

``sygnif-neurolinked`` binds **8889** by default (see ``systemd/sygnif-neurolinked.service``).
Feeds that default to **8888** hit the spot/BTC-terminal dashboard and never reach the brain.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

# Canonical default: matches ``run.py --port 8889`` in sygnif-neurolinked unit.
DEFAULT_NEUROLINKED_HTTP_URL = "http://127.0.0.1:8889"

_LOOPBACK_HTTP_RE = re.compile(r"^http://127\.0\.0\.1:(\d{1,5})/?$")


def sanitize_loopback_neurolinked_url(val: str | None) -> str | None:
    """Allow only ``http://127.0.0.1:<port>`` (no path, no query)."""
    if not val or not isinstance(val, str):
        return None
    s = val.strip().rstrip("/")
    m = _LOOPBACK_HTTP_RE.match(s)
    if not m:
        return None
    port = int(m.group(1))
    if port < 1 or port > 65535:
        return None
    return f"http://127.0.0.1:{port}"


def configured_neurolinked_base_urls() -> tuple[str, str | None]:
    """
    Returns (primary, secondary) bases for probes.

    ``HOST`` is preferred for market feed scripts; ``HTTP`` for predict-loop hook.
    """
    host = (os.environ.get("SYGNIF_NEUROLINKED_HOST_URL") or "").strip().rstrip("/")
    http = (os.environ.get("SYGNIF_NEUROLINKED_HTTP_URL") or "").strip().rstrip("/")
    primary = host or http or DEFAULT_NEUROLINKED_HTTP_URL
    secondary = http if http and http != primary else None
    return primary, secondary


def probe_sygnif_summary(base: str, *, timeout: float = 2.0) -> dict[str, Any]:
    """GET ``/api/sygnif/summary`` — NeuroLinked brain JSON."""
    base = base.rstrip("/")
    url = f"{base}/api/sygnif/summary"
    out: dict[str, Any] = {"ok": False, "url": base, "http_code": None, "has_step": False, "error": None}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "sygnif-neurolinked-probe/1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out["http_code"] = resp.getcode()
            body = resp.read()
        data = json.loads(body.decode("utf-8"))
        out["has_step"] = isinstance(data, dict) and "step" in data
        out["ok"] = out["has_step"]
        if not out["ok"]:
            out["error"] = "json_missing_step"
    except urllib.error.HTTPError as exc:
        out["http_code"] = exc.code
        out["error"] = f"http_{exc.code}"
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:200]
    return out


def diagnose_neurolinked_swarm_feed(*, timeout: float = 2.0) -> dict[str, Any]:
    """
    Detect misconfigured NeuroLinked URL (common: **8888** vs **8889**).

    Safe to call from ``build_swarm_weak_points_bundle`` (short timeout, no secrets).
    """
    primary, secondary = configured_neurolinked_base_urls()
    candidates = [primary]
    if secondary:
        candidates.append(secondary)
    for extra in (DEFAULT_NEUROLINKED_HTTP_URL, "http://127.0.0.1:8888"):
        if extra not in candidates:
            candidates.append(extra)

    probes = [probe_sygnif_summary(u, timeout=max(0.5, float(timeout))) for u in candidates]
    working = next((p for p in probes if p.get("ok")), None)
    primary_probe = probes[0] if probes else None

    mismatch = bool(primary_probe and not primary_probe.get("ok") and working)
    suggest = working.get("url") if working and mismatch else None

    return {
        "schema": "sygnif.neurolinked_connectivity/v1",
        "configured_primary": primary,
        "configured_secondary": secondary,
        "primary_ok": bool(primary_probe and primary_probe.get("ok")),
        "working_url": working.get("url") if working else None,
        "mismatch_suggest_url": suggest,
        "probes": probes,
    }
