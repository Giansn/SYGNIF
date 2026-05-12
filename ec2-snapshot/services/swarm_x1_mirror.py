#!/usr/bin/env python3
"""sygnif-swarm-x1-mirror — ship new EC2 swarm.db rows to X1 swarm-master.

Implements network_internal.swarm_master: x1_only_ec2_writes_via_mcp from
instruct.file. Each cycle, reads rows newer than the cursor and POSTs them
via tools/call JSON-RPC to X1's MCP. Idempotent: X1's swarm has UUID PKs
that reject duplicates.

Run by sygnif-swarm-x1-mirror.timer every 2 minutes.

Env (from /etc/sygnif/x1-mcp.env):
  SYGNIF_X1_MCP_URL    e.g. http://100.71.122.115:9001/rpc
  SYGNIF_X1_MCP_TOKEN  bearer token

Files:
  /var/lib/sygnif/swarm.db          source (read-only)
  /var/lib/sygnif/x1-mirror.cursor  last-shipped epoch
  /var/log/sygnif/x1-mirror.log     log
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.request

CURSOR_FILE = "/var/lib/sygnif/x1-mirror.cursor"
SWARM_DB = "/var/lib/sygnif/swarm.db"
DEFAULT_X1_URL = "http://100.71.122.115:9001/rpc"
SHIP_BATCH_LIMIT = 50       # max rows per cycle
HTTP_TIMEOUT_S = 15
PER_ROW_TIMEOUT_S = 8


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def cursor_read() -> float:
    try:
        with open(CURSOR_FILE) as f:
            return float(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 0.0


def cursor_write(ts: float) -> None:
    tmp = CURSOR_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(str(ts))
    os.replace(tmp, CURSOR_FILE)


def post_swarm_write(url: str, token: str, row: dict) -> tuple[bool, str]:
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "swarm_write",
            "arguments": row,
        },
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    })
    try:
        with urllib.request.urlopen(req, timeout=PER_ROW_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            err = data.get("error")
            if err:
                return False, f"rpc-error: {err}"
            return True, "ok"
    except (urllib.error.URLError, socket.timeout) as e:
        return False, f"net: {type(e).__name__}: {e}"
    except Exception as e:
        return False, f"err: {type(e).__name__}: {e}"


def main() -> int:
    url = os.environ.get("SYGNIF_X1_MCP_URL") or DEFAULT_X1_URL
    token = os.environ.get("SYGNIF_X1_MCP_TOKEN")
    if not token:
        log("FATAL: SYGNIF_X1_MCP_TOKEN missing")
        return 2

    cursor = cursor_read()
    log(f"start url={url} cursor={cursor:.1f}")

    con = sqlite3.connect(f"file:{SWARM_DB}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    cur = con.execute(
        "SELECT id, swarm_id, agent_id, topic, content, tags, meta, created "
        "FROM swarm_entries WHERE created > ? ORDER BY created LIMIT ?",
        (cursor, SHIP_BATCH_LIMIT),
    )
    rows = cur.fetchall()
    log(f"queued: {len(rows)} rows newer than cursor")

    if not rows:
        log("nothing to ship")
        return 0

    shipped = 0
    failed = 0
    last_ok_ts = cursor
    for r in rows:
        row = {
            "id": r["id"],
            "swarm_id": r["swarm_id"],
            "agent_id": r["agent_id"],
            "topic": r["topic"],
            "content": r["content"],
            "tags": r["tags"] or "[]",
            "meta": r["meta"] or "{}",
            "created": r["created"],
            "host": "ec2",
        }
        ok, msg = post_swarm_write(url, token, row)
        if ok:
            shipped += 1
            last_ok_ts = r["created"]
        else:
            failed += 1
            # If X1 is unreachable, stop the batch — preserves order for next cycle
            if "net:" in msg:
                log(f"network error after {shipped} shipped: {msg}")
                break
            # Other errors (rpc rejection, dedup, etc): continue (idempotent)
            log(f"row {r['id'][:8]} skipped: {msg}")

    if last_ok_ts > cursor:
        cursor_write(last_ok_ts)
        log(f"cursor advanced {cursor:.1f} -> {last_ok_ts:.1f}")
    log(f"done: shipped={shipped} failed={failed}")
    return 0 if shipped > 0 or failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
