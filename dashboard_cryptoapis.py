"""Refresh ``btc_cryptoapis_foundation.json`` (Crypto APIs / cryptoapis.io) on the dashboard host.

Uses the same env vars as ``finance_agent/cryptoapis_client.py``: ``cryptoapi_Token``,
``CRYPTOAPI_TOKEN``, or ``CRYPTOAPIS_API_KEY`` (HTTP ``X-API-Key`` to ``rest.cryptoapis.io``).
Server-side only — never sent to the browser. ``pull_btc_context.py`` can also write this file.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Do not hammer Crypto APIs on every dashboard poll.
_MIN_SECONDS_BETWEEN_FETCH = 45
_STALE_FILE_SECONDS = 300


def _load_dotenv_file(path: str) -> None:
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if not k:
            continue
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def load_dashboard_env(server_dir: str) -> None:
    """Match ``pull_btc_context._load_repo_env`` search order (host + repo)."""
    home = os.path.dirname(os.path.abspath(server_dir))
    for p in (
        os.path.join(server_dir, ".env"),
        os.path.join(home, "xrp_claude_bot", ".env"),
        os.path.join(home, "finance_agent", ".env"),
        os.path.join(home, "SYGNIF", ".env"),
    ):
        _load_dotenv_file(p)


def resolve_finance_agent_dir(server_dir: str) -> str | None:
    """Prefer local ``finance_agent``; fall back to ``~/SYGNIF/finance_agent`` (xrp deploy)."""
    cand = os.path.join(server_dir, "finance_agent")
    if os.path.isfile(os.path.join(cand, "cryptoapis_client.py")):
        return cand
    home = os.path.dirname(os.path.abspath(server_dir))
    alt = os.path.join(home, "SYGNIF", "finance_agent")
    if os.path.isfile(os.path.join(alt, "cryptoapis_client.py")):
        return alt
    return None


_last_fetch_mono: float = 0.0


def ensure_cryptoapis_foundation_json(btc_data_dir: str, server_dir: str) -> None:
    """If key is set, refresh foundation JSON when missing or older than ``_STALE_FILE_SECONDS``."""
    global _last_fetch_mono
    load_dashboard_env(server_dir)
    fa = resolve_finance_agent_dir(server_dir)
    if not fa:
        return
    out_path = os.path.join(btc_data_dir, "btc_cryptoapis_foundation.json")
    now = time.time()
    if os.path.isfile(out_path):
        if (now - os.path.getmtime(out_path)) < _STALE_FILE_SECONDS:
            return
    mono = time.monotonic()
    if (mono - _last_fetch_mono) < _MIN_SECONDS_BETWEEN_FETCH:
        return
    _last_fetch_mono = mono
    if fa not in sys.path:
        sys.path.insert(0, fa)
    try:
        from cryptoapis_client import api_key, write_btc_foundation_json
    except ImportError:
        return
    if not api_key():
        return
    utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        Path(btc_data_dir).mkdir(parents=True, exist_ok=True)
        write_btc_foundation_json(Path(btc_data_dir), utc)
    except Exception:
        pass
