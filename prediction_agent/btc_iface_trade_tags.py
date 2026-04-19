"""
Sidecar store for **BTC Interface** dashboard: strategy-style tags on Bybit demo linear fills.

The predict protocol loop appends JSONL rows and maintains a small **position meta** JSON so the
read-only dashboard can show **open** and **close** tags. Bybit ``closed-pnl`` REST does not return
``orderLinkId``; we key **closed** rows on the closing **orderId** when the loop records it.

Env:
- ``SYGNIF_BTC_IFACE_TRADE_TAGS_JSONL`` — journal path (default ``prediction_agent/btc_iface_trade_tags.jsonl``).
- ``SYGNIF_BTC_IFACE_POSITION_TAGS_JSON`` — open-position tag cache (default ``prediction_agent/btc_iface_position_tags.json``).
- ``SYGNIF_BTC_IFACE_TRADE_TAGS`` — set ``0`` to disable loop writes (no journal / position file updates).
"""
from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]


def _tags_enabled() -> bool:
    v = os.environ.get("SYGNIF_BTC_IFACE_TRADE_TAGS", "1").strip().lower()
    return v not in ("0", "false", "no", "off", "")


def journal_path() -> Path:
    raw = os.environ.get("SYGNIF_BTC_IFACE_TRADE_TAGS_JSONL", "").strip()
    if raw:
        return Path(raw).expanduser()
    return _REPO / "prediction_agent" / "btc_iface_trade_tags.jsonl"


def position_meta_path() -> Path:
    raw = os.environ.get("SYGNIF_BTC_IFACE_POSITION_TAGS_JSON", "").strip()
    if raw:
        return Path(raw).expanduser()
    return _REPO / "prediction_agent" / "btc_iface_position_tags.json"


def _order_link_entropy_suffix() -> str:
    """Short suffix so repeated ``iter_n`` (e.g. one-shot gate always ``1``) does not reuse Bybit ``orderLinkId``."""
    return f"{int(time.time() * 1000) & 0xFFFF:04x}{random.randint(0, 0xFFF):03x}"


def order_link_open(iter_n: int, side_long: bool) -> str:
    """Bybit ``orderLinkId`` (≤36 chars; unique per call — Bybit rejects duplicate link ids)."""
    n = int(iter_n) % 1_000_000
    suf = _order_link_entropy_suffix()
    return f"sygPL{n:06d}{suf}{'L' if side_long else 'S'}"


def order_link_close(iter_n: int) -> str:
    n = int(iter_n) % 1_000_000
    suf = _order_link_entropy_suffix()
    return f"sygPL{n:06d}{suf}CX"


def order_link_verify_open() -> str:
    """Unique ``orderLinkId`` for one-shot tag verification (Bybit max length 36)."""
    suf = f"{int(time.time() * 1000) & 0xFFFFFF:06x}{random.randint(0, 0xFFFF):04x}"
    return f"sygTV{suf}O"


def order_link_verify_close() -> str:
    suf = f"{int(time.time() * 1000) & 0xFFFFFF:06x}{random.randint(0, 0xFFFF):04x}"
    return f"sygTV{suf}C"


def order_id_from_create_response(mo: dict[str, Any]) -> str | None:
    if mo.get("retCode") != 0:
        return None
    res = mo.get("result") or {}
    oid = res.get("orderId") or res.get("order_id")
    if oid is None:
        return None
    s = str(oid).strip()
    return s or None


def append_journal(entry: dict[str, Any]) -> None:
    if not _tags_enabled():
        return
    path = journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(entry)
    row.setdefault("ts_ms", int(time.time() * 1000))
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_position_meta() -> dict[str, Any]:
    p = position_meta_path()
    if not p.is_file():
        return {}
    try:
        o = json.loads(p.read_text(encoding="utf-8"))
        return o if isinstance(o, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_position_meta(data: dict[str, Any]) -> None:
    if not _tags_enabled():
        return
    p = position_meta_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def set_position_open(
    symbol: str,
    *,
    open_tag: str,
    open_detail: str,
    open_order_id: str,
    open_order_link_id: str | None,
    pos_side: str,
    opened_ms: int | None = None,
) -> None:
    if not _tags_enabled():
        return
    sym = (symbol or "").replace("/", "").upper().strip()
    if not sym:
        return
    all_meta = load_position_meta()
    all_meta[sym] = {
        "open_tag": open_tag,
        "open_detail": (open_detail or "")[:2000],
        "open_order_id": open_order_id,
        "open_order_link_id": open_order_link_id or "",
        "pos_side": pos_side,
        "opened_ms": int(opened_ms if opened_ms is not None else time.time() * 1000),
    }
    save_position_meta(all_meta)


def clear_position_symbol(symbol: str) -> None:
    if not _tags_enabled():
        return
    sym = (symbol or "").replace("/", "").upper().strip()
    if not sym:
        return
    all_meta = load_position_meta()
    if sym in all_meta:
        del all_meta[sym]
        save_position_meta(all_meta)


def load_close_order_index(*, max_bytes: int = 2_000_000) -> dict[str, dict[str, Any]]:
    """
    Map **closing** ``order_id`` (string) → ``open_tag``, ``close_tag``, optional detail.

    Later journal lines win if the same order id appears twice.
    """
    path = journal_path()
    if not path.is_file():
        return {}
    try:
        sz = path.stat().st_size
        with path.open("rb") as f:
            if sz <= max_bytes:
                raw = f.read()
            else:
                f.seek(-max_bytes, os.SEEK_END)
                raw = f.read()
    except OSError:
        return {}
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if sz > max_bytes and lines:
        lines = lines[1:]
    out: dict[str, dict[str, Any]] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("action") != "close":
            continue
        oid = str(o.get("order_id") or "").strip()
        if not oid:
            continue
        out[oid] = {
            "open_tag": o.get("open_tag") or "",
            "close_tag": o.get("close_tag") or "",
            "open_detail": o.get("open_detail") or "",
            "exit_kind": o.get("exit_kind") or "",
        }
    return out
