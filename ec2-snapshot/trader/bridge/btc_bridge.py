#!/usr/bin/env python3
"""
sygnif-btc-bridge — pull EC2 trading state into the local swarm DB.

Reads (read-only) over SSH from EC2 ubuntu@3.64.28.14 (via sygnif-ssh ec2):
  - prediction_agent/btc_iface_trade_tags.jsonl    (opens / closes)
  - prediction_agent/btc_eval_forecasts_pending.jsonl  (5m forecasts)
  - SYGNIF/scripts/swarm_demo_pnl_report.py        (summary, optional)

Writes to /var/lib/sygnif/swarm.db with swarm_id="btc_demo", agent_id derived
from event source. Idempotent — uses deterministic SHA256-derived IDs so
re-runs only insert new entries.

Tail-only: keeps a per-source cursor so we don't re-read the entire journal
each tick. Cursor file: ~/.local/state/sygnif/btc_bridge.cursor.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

DB_PATH = Path(os.environ.get("SWARM_KNOWLEDGE_DB", "/var/lib/sygnif/swarm.db"))
SSH_WRAPPER = os.environ.get("SYGNIF_SSH", str(Path.home() / ".local/bin/sygnif-ssh"))
EC2_ALIAS = os.environ.get("SYGNIF_BTC_EC2_ALIAS", "ec2")
SWARM_ID = os.environ.get("SYGNIF_BTC_SWARM_ID", "btc_demo")
CURSOR_PATH = Path(os.environ.get("SYGNIF_BTC_CURSOR", str(Path.home() / ".local/state/sygnif/btc_bridge.cursor.json")))
TRADE_TAGS_PATH = "/home/ubuntu/sygnif-swarm/BTC_Prediction/prediction_agent/btc_iface_trade_tags.jsonl"
FORECASTS_PATH = "/home/ubuntu/sygnif-swarm/BTC_Prediction/prediction_agent/btc_eval_forecasts_pending.jsonl"
RESOLVED_PATH = "/home/ubuntu/sygnif-swarm/BTC_Prediction/prediction_agent/btc_nauti_prediction_journal.jsonl"

# Cap how many lines we tail per source per tick.
MAX_LINES_PER_TICK = 500


# ---------------------------------------------------------------------------
# Cursor (where we stopped last tick)
# ---------------------------------------------------------------------------


def _load_cursor() -> dict:
    if CURSOR_PATH.exists():
        try:
            return json.loads(CURSOR_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def _save_cursor(cur: dict) -> None:
    CURSOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_PATH.write_text(json.dumps(cur, indent=2))


# ---------------------------------------------------------------------------
# Remote tail
# ---------------------------------------------------------------------------


def _ec2_tail(path: str, n: int = MAX_LINES_PER_TICK) -> list[str]:
    """Pull last N lines of a remote file via sygnif-ssh."""
    cmd = [SSH_WRAPPER, EC2_ALIAS, f"tail -n {n} {path} 2>/dev/null || true"]
    proc = subprocess.run(cmd, capture_output=True, timeout=30)
    if proc.returncode != 0:
        return []
    return proc.stdout.decode("utf-8", errors="replace").splitlines()


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def _det_id(prefix: str, payload: str) -> str:
    h = hashlib.sha256(f"{prefix}|{payload}".encode("utf-8")).hexdigest()
    # Format as a UUID-like string for the schema
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _insert(conn, *, eid: str, agent_id: str, topic: str, content: str, tags: list[str], meta: dict, ts: float) -> bool:
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO swarm_entries (id, swarm_id, agent_id, topic, content, tags, meta, created)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                eid,
                SWARM_ID,
                agent_id,
                topic,
                content[:4000],
                json.dumps(tags, ensure_ascii=False),
                json.dumps(meta, ensure_ascii=False, default=str),
                ts,
            ),
        )
        return conn.total_changes > 0
    except Exception as e:
        print(f"[btc-bridge] insert failed: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Per-source ingest
# ---------------------------------------------------------------------------


def ingest_trade_tags(conn, cursor: dict) -> int:
    last_ts = float(cursor.get("trade_tags_ts_ms", 0))
    inserted = 0
    new_max_ts = last_ts
    for line in _ec2_tail(TRADE_TAGS_PATH):
        line = line.strip()
        if not line:
            continue
        try:
            tag = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts_ms = float(tag.get("ts_ms") or 0)
        if ts_ms <= last_ts:
            continue
        new_max_ts = max(new_max_ts, ts_ms)
        action = tag.get("action", "?")
        sym = tag.get("symbol", "BTCUSDT")
        side = tag.get("side", "?")
        eid = _det_id("trade_tag", f"{tag.get('order_link_id','')}:{tag.get('order_id','')}:{action}:{ts_ms}")
        if action == "open":
            content = (
                f"{action.upper()} {sym} side={side} "
                f"detail={tag.get('open_detail','')} "
                f"order_link={tag.get('order_link_id','')}"
            )
            tags = ["open", side.lower(), "predict_loop"]
        elif action == "close":
            content = (
                f"CLOSE {sym} kind={tag.get('exit_kind','?')} "
                f"open_link={tag.get('open_order_id','')[:12]} "
                f"close_link={tag.get('order_link_id','')}"
            )
            tags = ["close", tag.get("exit_kind", "?"), "predict_loop"]
        else:
            content = f"{action} {sym} {tag}"[:400]
            tags = [action]
        if _insert(
            conn,
            eid=eid,
            agent_id="predict_loop",
            topic=f"trade.{action}",
            content=content,
            tags=tags,
            meta=tag,
            ts=ts_ms / 1000.0,
        ):
            inserted += 1
    conn.commit()
    cursor["trade_tags_ts_ms"] = new_max_ts
    return inserted


def ingest_forecasts(conn, cursor: dict) -> int:
    last_ts = float(cursor.get("forecasts_ts", 0))
    inserted = 0
    new_max_ts = last_ts
    for line in _ec2_tail(FORECASTS_PATH):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Schema gives predicted_at_utc as ISO string
        pa = row.get("predicted_at_utc", "")
        try:
            ts = time.mktime(time.strptime(pa, "%Y-%m-%dT%H:%M:%SZ"))
        except Exception:
            ts = time.time()
        if ts <= last_ts:
            continue
        new_max_ts = max(new_max_ts, ts)
        fc = row.get("forecast", {})
        sym = row.get("symbol", "BTCUSDT")
        bm = row.get("bar_minutes", "?")
        cons = fc.get("consensus", "?")
        rf = fc.get("rf_delta", "?")
        xgb = fc.get("xgb_delta", "?")
        lr_label = fc.get("logreg_label", "?")
        lr_conf = fc.get("logreg_confidence", 0)
        close = row.get("current_close", "?")
        content = (
            f"{sym} {bm}m forecast at {pa}: {cons} (close={close} "
            f"RF={rf} XGB={xgb} logreg={lr_label}/{lr_conf}%)"
        )
        eid = _det_id("forecast", f"{sym}:{row.get('eval_id','')}:{pa}")
        tags = [f"{bm}m", str(cons).lower(), str(lr_label).lower()]
        if _insert(
            conn,
            eid=eid,
            agent_id="predict_loop",
            topic="forecast",
            content=content,
            tags=tags,
            meta={"eval_id": row.get("eval_id"), "forecast": fc, "current_close": close},
            ts=ts,
        ):
            inserted += 1
    conn.commit()
    cursor["forecasts_ts"] = new_max_ts
    return inserted


def ingest_resolved(conn, cursor: dict) -> int:
    last_ts = float(cursor.get("resolved_ts", 0))
    inserted = 0
    new_max_ts = last_ts
    for line in _ec2_tail(RESOLVED_PATH, n=200):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        rec_utc = row.get("resolved_utc") or row.get("recorded_utc") or ""
        try:
            ts = time.mktime(time.strptime(rec_utc, "%Y-%m-%dT%H:%M:%SZ"))
        except Exception:
            continue
        if ts <= last_ts:
            continue
        new_max_ts = max(new_max_ts, ts)
        cons = (row.get("predictions") or {}).get("consensus", "?")
        actual_up = row.get("actual_up")
        cons_correct = row.get("consensus_correct")
        content = (
            f"resolved {row.get('id', '?')[:16]}: consensus={cons} "
            f"actual_up={actual_up} cons_correct={cons_correct}"
        )
        eid = _det_id("resolved", f"{row.get('id','')}:{rec_utc}")
        tags = ["resolved", "win" if cons_correct is True else ("loss" if cons_correct is False else "open")]
        if _insert(
            conn,
            eid=eid,
            agent_id="predict_loop",
            topic="resolved",
            content=content,
            tags=tags,
            meta=row,
            ts=ts,
        ):
            inserted += 1
    conn.commit()
    cursor["resolved_ts"] = new_max_ts
    return inserted


# ---------------------------------------------------------------------------
# Health snapshot — record one entry per tick summarising EC2 trading state
# ---------------------------------------------------------------------------


def emit_health_snapshot(conn) -> None:
    """Capture which trading processes / services are alive on EC2."""
    pattern = r"predict_loop|swarm_auto|btc_predict|nautilus_|nautilus_sidecar|bybit_nautilus|bybit_nl|bee_signal|sygnif_swarm|run_nautilus|btc_predict_runner|btc_iface"
    cmd_procs = [
        SSH_WRAPPER, EC2_ALIAS,
        f"pgrep -fa \"{pattern}\" 2>/dev/null | grep -v grep | wc -l",
    ]
    cmd_services = [
        SSH_WRAPPER, EC2_ALIAS,
        "systemctl --no-pager list-units --type=service --state=running 2>/dev/null | grep -cE 'sygnif-|bee-|bybit-|cursor-agent'",
    ]
    procs_n = -1
    svcs_n = -1
    try:
        procs_n = int(subprocess.run(cmd_procs, capture_output=True, timeout=15).stdout.decode().strip() or 0)
    except Exception:
        pass
    try:
        svcs_n = int(subprocess.run(cmd_services, capture_output=True, timeout=15).stdout.decode().strip() or 0)
    except Exception:
        pass
    ts = time.time()
    eid = _det_id("health", f"{int(ts // 60)}:p={procs_n}:s={svcs_n}")
    _insert(
        conn,
        eid=eid,
        agent_id="btc_bridge",
        topic="health",
        content=f"EC2 lab: {procs_n} trading procs, {svcs_n} sygnif/bee/bybit services running",
        tags=["health", "snapshot"],
        meta={"procs": procs_n, "services": svcs_n, "ts": ts},
        ts=ts,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    if not DB_PATH.exists():
        print(f"[btc-bridge] DB missing: {DB_PATH}", file=sys.stderr)
        return 2
    cursor = _load_cursor()
    conn = _db()
    try:
        n_tags = ingest_trade_tags(conn, cursor)
        n_fcs = ingest_forecasts(conn, cursor)
        n_resolved = ingest_resolved(conn, cursor)
        emit_health_snapshot(conn)
    finally:
        conn.close()
    _save_cursor(cursor)
    print(f"[btc-bridge] inserted: trade_tags={n_tags} forecasts={n_fcs} resolved={n_resolved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
