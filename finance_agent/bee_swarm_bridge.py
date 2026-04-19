#!/usr/bin/env python3
"""
**Ethereum Swarm Bee** HTTP probe — optional hook for Sygnif ``compute_swarm``.

This is **not** Truthcoin / Bitcoin Hivemind (see ``truthcoin_hivemind_swarm_core``). Bee is the
`ethersphere/bee` storage node; its API defaults to port **1633**.

Env:

- ``SYGNIF_BEE_API_URL`` — e.g. ``http://127.0.0.1:1633`` (recommended).
- ``BEE_API_ADDR`` — if ``SYGNIF_BEE_API_URL`` is unset, values like ``:1633`` or ``127.0.0.1:1633``
  are turned into a ``http://`` base URL (Docker-style ``:1633`` → ``http://127.0.0.1:1633``).
- ``SYGNIF_BEE_API_TIMEOUT_SEC`` — HTTP timeout (default ``4``).
- ``SYGNIF_BEE_TOPOLOGY`` — set to ``1`` to include peer topology (connected/population/depth) in
  ``fetch_bee_health()`` output. Enables peer-weighted vote in ``compute_swarm``.
- ``SYGNIF_SWARM_BEE_PEER_WEIGHT`` — peer tier thresholds for vote scaling in ``compute_swarm``:
  peers ≥ 100 → vote counts twice; peers < 25 → vote = 0 (isolated). Default: ``1``.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


def bee_api_base_url() -> str | None:
    raw = (os.environ.get("SYGNIF_BEE_API_URL_DOCKER") or
           os.environ.get("SYGNIF_BEE_API_URL") or "").strip()
    if raw:
        return raw.rstrip("/")
    addr = (os.environ.get("BEE_API_ADDR") or "").strip()
    if not addr:
        return None
    if addr.startswith(":"):
        return f"http://127.0.0.1{addr}"
    if "://" in addr:
        return addr.rstrip("/")
    return f"http://{addr}"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _fetch_json(url: str, timeout: float) -> dict[str, Any] | None:
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        body = json.loads(raw) if raw else {}
        return body if isinstance(body, dict) else None
    except Exception:
        return None


def fetch_bee_topology(base_url: str, timeout: float) -> dict[str, Any]:
    """GET ``/topology`` — returns connected/population/depth or empty dict on failure."""
    body = _fetch_json(f"{base_url}/topology", timeout)
    if not body:
        return {}
    try:
        return {
            "connected": int(body.get("connected") or 0),
            "population": int(body.get("population") or 0),
            "depth": int(body.get("depth") or 0),
        }
    except (TypeError, ValueError):
        return {}


def fetch_bee_node_mode(base_url: str, timeout: float) -> str:
    """GET ``/node`` — returns beeMode string (``full``, ``light``, ``ultra-light``)."""
    body = _fetch_json(f"{base_url}/node", timeout)
    if not body:
        return "unknown"
    return str(body.get("beeMode") or "unknown").lower()


def fetch_bee_health() -> dict[str, Any]:
    """
    GET ``/health`` (and optionally ``/topology``) on the Bee API.

    Returns a dict safe to embed in Swarm JSON. When ``SYGNIF_BEE_TOPOLOGY=1``,
    adds ``peers_connected``, ``peers_population``, and ``peers_depth`` fields.
    """
    base = bee_api_base_url()
    if not base:
        return {"enabled": False, "ok": False, "detail": "no_bee_api_url"}
    timeout = max(0.5, _env_float("SYGNIF_BEE_API_TIMEOUT_SEC", 4.0))
    url = f"{base}/health"
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return {
            "enabled": True,
            "ok": False,
            "base_url": base,
            "detail": f"http_{exc.code}",
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "enabled": True,
            "ok": False,
            "base_url": base,
            "detail": str(exc)[:200],
        }
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {
            "enabled": True,
            "ok": False,
            "base_url": base,
            "detail": "invalid_json",
        }
    if not isinstance(body, dict):
        return {"enabled": True, "ok": False, "base_url": base, "detail": "not_a_json_object"}
    st = str(body.get("status") or "").lower()
    ok = st == "ok"
    # Fetch node mode and topology (lightweight — parallel-ish via sequential calls)
    topo_env = os.environ.get("SYGNIF_BEE_TOPOLOGY", "1").strip()
    bee_mode = "unknown"
    topo: dict[str, Any] = {}
    if ok and topo_env not in ("0", "false", "no", "off"):
        bee_mode = fetch_bee_node_mode(base, min(timeout, 2.0))
        topo = fetch_bee_topology(base, min(timeout, 2.0))

    # A light/ultra-light node knows peers but doesn't serve chunks or join Kademlia routing.
    # Mark full participation separately so vote logic can distinguish.
    is_full_node = ok and bee_mode == "full"
    is_light = ok and bee_mode in ("light", "ultra-light")

    detail = "ok" if ok else f"status_{st or 'missing'}"
    if ok and bee_mode != "unknown":
        detail = f"{bee_mode}_mode"
    if is_light:
        detail = f"light_mode_observer_only"

    result: dict[str, Any] = {
        "enabled": True,
        "ok": ok,
        "full_node": is_full_node,
        "light_node": is_light,
        "bee_mode": bee_mode,
        "base_url": base,
        "status": body.get("status"),
        "version": body.get("version"),
        "apiVersion": body.get("apiVersion"),
        "detail": detail,
    }
    if topo:
        result["peers_connected"] = topo["connected"]
        result["peers_population"] = topo["population"]
        result["peers_depth"] = topo["depth"]
    return result
