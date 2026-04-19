#!/usr/bin/env python3
"""
**letscrash** + predict-loop **resource guard** (Linux-friendly, no hard dependency on psutil).

Reads optional policy from ``letscrash/btc_strategy_0_1_rule_registry.json``:

.. code-block:: json

   "tuning": {
     "predict_loop_resource": {
       "enabled": true,
       "mem_available_min_mb": 512,
       "loadavg_max": 14.0,
       "cooldown_sec": 25,
       "log_every_n_skips": 1
     }
   }

Env (override file):

- ``SYGNIF_PREDICT_RESOURCE_GUARD`` — ``1`` / ``true`` force on; ``0`` / ``false`` force off; unset → use registry.
- ``SYGNIF_RESOURCE_MEM_MIN_MB`` — minimum **MemAvailable** from ``/proc/meminfo`` (MB); skip iteration if below.
- ``SYGNIF_RESOURCE_LOAD_MAX`` — max 1m loadavg; skip if above.
- ``SYGNIF_RESOURCE_COOLDOWN_SEC`` — sleep before returning from a skipped iteration (reduces CPU spin).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _linux_mem_available_kb() -> int | None:
    try:
        txt = Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    m = re.search(r"^MemAvailable:\s+(\d+)\s+kB", txt, flags=re.MULTILINE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _loadavg1() -> float | None:
    try:
        return float(os.getloadavg()[0])
    except OSError:
        return None


def _self_rss_kb() -> int | None:
    try:
        txt = Path("/proc/self/status").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    m = re.search(r"^VmRSS:\s+(\d+)\s+kB", txt, flags=re.MULTILINE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def load_registry(repo_root: Path) -> dict[str, Any] | None:
    p = repo_root / "letscrash" / "btc_strategy_0_1_rule_registry.json"
    if not p.is_file():
        return None
    try:
        o = json.loads(p.read_text(encoding="utf-8"))
        return o if isinstance(o, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def load_guard_config(repo_root: Path) -> dict[str, Any]:
    raw = (os.environ.get("SYGNIF_PREDICT_RESOURCE_GUARD") or "").strip().lower()
    reg = load_registry(repo_root) or {}
    tuning = reg.get("tuning") if isinstance(reg.get("tuning"), dict) else {}
    pr = tuning.get("predict_loop_resource")
    pr = pr if isinstance(pr, dict) else {}
    enabled = bool(pr.get("enabled", False))
    if raw in ("1", "true", "yes", "on"):
        enabled = True
    elif raw in ("0", "false", "no", "off"):
        enabled = False
    mem_mb = float(pr.get("mem_available_min_mb", 512) or 512)
    load_max = float(pr.get("loadavg_max", 14.0) or 14.0)
    cooldown = float(pr.get("cooldown_sec", 25.0) or 25.0)
    mem_mb = _env_float("SYGNIF_RESOURCE_MEM_MIN_MB", mem_mb)
    load_max = _env_float("SYGNIF_RESOURCE_LOAD_MAX", load_max)
    cooldown = max(1.0, _env_float("SYGNIF_RESOURCE_COOLDOWN_SEC", cooldown))
    return {
        "enabled": enabled,
        "mem_available_min_mb": mem_mb,
        "loadavg_max": load_max,
        "cooldown_sec": cooldown,
    }


def resource_snapshot() -> dict[str, Any]:
    mk = _linux_mem_available_kb()
    rss = _self_rss_kb()
    la = _loadavg1()
    out: dict[str, Any] = {"ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if mk is not None:
        out["mem_available_mb"] = round(mk / 1024.0, 2)
    if rss is not None:
        out["process_rss_mb"] = round(rss / 1024.0, 2)
    if la is not None:
        out["loadavg_1m"] = round(la, 3)
    return out


def should_skip_iteration(cfg: dict[str, Any]) -> tuple[bool, str, float]:
    """
    Return (skip, reason, sleep_sec).

    On non-Linux or when MemAvailable unknown, only loadavg gate applies if available.
    """
    if not cfg.get("enabled"):
        return False, "", 0.0
    mem_min_mb = float(cfg.get("mem_available_min_mb", 512) or 512)
    load_max = float(cfg.get("loadavg_max", 14.0) or 14.0)
    cool = float(cfg.get("cooldown_sec", 25.0) or 25.0)

    mk = _linux_mem_available_kb()
    if mk is not None:
        avail_mb = mk / 1024.0
        if avail_mb < mem_min_mb:
            return True, f"mem_available_{avail_mb:.0f}mb_lt_{mem_min_mb:.0f}mb", cool

    la = _loadavg1()
    if la is not None and la > load_max:
        return True, f"loadavg_{la:.2f}_gt_{load_max:.2f}", cool

    return False, "", 0.0
