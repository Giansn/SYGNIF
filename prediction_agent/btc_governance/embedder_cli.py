#!/usr/bin/env python3
"""
Optional hook for the **Embedder** terminal product (embedded-systems agent), **not** vector DBs.

Upstream: https://github.com/embedder-dev/embedder-cli — MCU / firmware / datasheet workflows.

When ``SYGNIF_EMBEDDER_CLI=1``, we probe ``embedder`` on ``PATH`` (``--help`` or ``--version``).
This does **not** index JSON for semantic search and does **not** reclaim disk by itself.
Use ``btc_governance.archive`` for gzip rotation.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def embedder_binary() -> str | None:
    return shutil.which("embedder")


def probe_embedder_cli(*, timeout_sec: float = 20.0) -> dict[str, Any]:
    """
    Non-interactive probe. Returns ``{"ok", "reason", "stdout", "embedder_path"}``.
    """
    if not _env_truthy("SYGNIF_EMBEDDER_CLI"):
        return {"ok": False, "reason": "SYGNIF_EMBEDDER_CLI off", "embedder_path": None}
    exe = embedder_binary()
    if not exe:
        return {"ok": False, "reason": "embedder not on PATH", "embedder_path": None}
    for args in (["--version"], ["--help"], ["-h"]):
        try:
            r = subprocess.run(
                [exe, *args],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            return {
                "ok": r.returncode in (0, 1),
                "reason": f"ran {' '.join(args)} rc={r.returncode}",
                "stdout": (r.stdout or "")[:2000],
                "stderr": (r.stderr or "")[:1000],
                "embedder_path": exe,
            }
        except (OSError, subprocess.TimeoutExpired) as exc:
            continue
    return {"ok": False, "reason": "probe failed", "embedder_path": exe}
