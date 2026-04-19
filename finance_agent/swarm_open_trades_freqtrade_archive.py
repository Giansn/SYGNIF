"""
Archived **Freqtrade** open-trades sources (trade-overseer HTTP + SQLite).

``swarm_knowledge.build_open_trades_report()`` now scans **Bybit** ``position/list`` only.
Import this module explicitly if you still need overseer/SQLite (e.g. legacy tooling).

No secrets in JSON output; read-only.
"""
from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _overseer_url() -> str:
    return (os.environ.get("OVERSEER_URL") or "http://127.0.0.1:8090").rstrip("/")


def _fetch_overseer_trades_json() -> dict[str, Any] | None:
    """GET trade-overseer ``/trades`` (open list + profit aggregates)."""
    try:
        url = f"{_overseer_url()}/trades"
        req = urllib.request.Request(url, headers={"User-Agent": "SYGNIF-swarm-freqtrade-archive/1"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError, TypeError):
        return None


def _open_trades_scope_files() -> list[tuple[str, str]]:
    """
    (filename, label) under ``user_data/`` for SQLite open-trade scan.

    Default **btc_docker**: BTC-only Docker DBs (see ``docker-compose.yml`` BTC_Strategy_0_1 + btc-spot).
    **all**: legacy multi-pair archives (``archived-main-traders`` stack).
    """
    raw = (os.environ.get("SYGNIF_SWARM_OPEN_TRADES_SCOPE") or "btc_docker").strip().lower()
    if raw in ("all", "legacy", "archive"):
        return (
            ("tradesv3.sqlite", "spot_archive"),
            ("tradesv3-futures.sqlite", "futures_archive"),
        )
    custom = (os.environ.get("SYGNIF_SWARM_OPEN_TRADES_SQLITE_FILES") or "").strip()
    if custom:
        parts = [p.strip() for p in custom.split(",") if p.strip()]
        return [(p, "custom") for p in parts]
    return (
        ("tradesv3-futures-btc01-demo.sqlite", "futures_btc01_demo"),
        ("tradesv3-btc-spot.sqlite", "btc_spot"),
    )


def _open_trades_btc_sql_clause() -> str:
    """Restrict to BTC linear/spot pair prefix (not WBTC)."""
    btc_only = os.environ.get("SYGNIF_SWARM_OPEN_TRADES_BTC_ONLY", "1").strip().lower()
    if btc_only in ("0", "false", "no", "off", "all"):
        return ""
    return " AND pair LIKE 'BTC/%'"


def _sqlite_open_trades_brief() -> dict[str, Any]:
    """Read-only summary from selected Freqtrade DBs under ``user_data/``."""
    ud = _repo_root() / "user_data"
    out: dict[str, Any] = {"ok": False, "dbs": [], "scope": (os.environ.get("SYGNIF_SWARM_OPEN_TRADES_SCOPE") or "btc_docker")}
    if not ud.is_dir():
        return out
    extra = _open_trades_btc_sql_clause()
    for fname, label in _open_trades_scope_files():
        p = ud / fname
        if not p.is_file():
            continue
        try:
            con = sqlite3.connect(str(p))
            cur = con.execute(f"SELECT COUNT(*) FROM trades WHERE is_open = 1{extra}")
            n = int(cur.fetchone()[0] or 0)
            cur = con.execute(
                f"SELECT pair, COALESCE(enter_tag,''), is_short FROM trades "
                f"WHERE is_open = 1{extra} ORDER BY id LIMIT 40"
            )
            rows = [
                {"pair": r[0], "enter_tag": r[1] or "", "is_short": bool(r[2])}
                for r in cur.fetchall()
            ]
            con.close()
            out["dbs"].append({"file": fname, "label": label, "open_n": n, "trades": rows})
        except (OSError, sqlite3.Error) as e:
            out["dbs"].append({"file": fname, "error": str(e)[:160]})
    out["ok"] = bool(out["dbs"])
    return out


def build_open_trades_report_freqtrade_legacy() -> dict[str, Any]:
    """
    Legacy: Freqtrade open positions — overseer when up, else SQLite brief.

    Prefer ``swarm_knowledge.build_open_trades_report()`` (Bybit-only).
    """
    rep: dict[str, Any] = {"enabled": True}
    data = _fetch_overseer_trades_json()
    if data is not None and isinstance(data.get("trades"), list):
        trades = list(data["trades"])
        btc_only = os.environ.get("SYGNIF_SWARM_OPEN_TRADES_BTC_ONLY", "1").strip().lower()
        scope = (os.environ.get("SYGNIF_SWARM_OPEN_TRADES_SCOPE") or "btc_docker").strip().lower()
        if btc_only not in ("0", "false", "no", "off", "all") and scope not in ("all", "legacy", "archive"):
            trades = [t for t in trades if str(t.get("pair") or "").upper().startswith("BTC/")]
        rep["source"] = "overseer"
        rep["overseer_url"] = _overseer_url()
        rep["open_n"] = len(trades)
        rep["trades"] = trades[:60]
        rep["profits"] = data.get("profits") if isinstance(data.get("profits"), list) else []
        return rep
    sq = _sqlite_open_trades_brief()
    rep["source"] = "sqlite"
    rep["sqlite"] = sq
    rep["open_n"] = sum(int(d.get("open_n") or 0) for d in sq.get("dbs") or [])
    return rep
