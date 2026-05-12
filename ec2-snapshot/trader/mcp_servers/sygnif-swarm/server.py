#!/usr/bin/env python3
"""sygnif-swarm — homegrown stdio MCP server backed by ~/.sygnif/swarm.db.

Speaks MCP JSON-RPC 2.0 over stdio (line-delimited). No external SDK.

Tools exposed:
    swarm_recent  {swarm_id?, limit?}                 list newest N entries
    swarm_search  {query, swarm_id?, limit?}          substring match across content/topic/agent_id
    swarm_write   {content, swarm_id?, agent_id?,
                   topic?, tags?, meta?}              append a new entry, return uuid
    swarm_partitions {}                               list swarm_id partitions + counts
    swarm_get     {id}                                fetch single entry by uuid

Stdout = wire protocol. Logging goes to stderr.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import traceback
import uuid
from pathlib import Path

DB_PATH = Path(os.environ.get(
    "SYGNIF_SWARM_DB",
    str(Path.home() / ".sygnif" / "swarm.db"),
))

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "sygnif-swarm"
SERVER_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# logging (stderr only)
# ---------------------------------------------------------------------------


def log(msg: str) -> None:
    sys.stderr.write(f"[{SERVER_NAME}] {msg}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# db helpers
# ---------------------------------------------------------------------------


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def _row_to_dict(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "swarm_id": r["swarm_id"],
        "agent_id": r["agent_id"],
        "topic": r["topic"] or "",
        "content": r["content"],
        "tags": json.loads(r["tags"] or "[]"),
        "meta": json.loads(r["meta"] or "{}"),
        "created_unix": float(r["created"]),
    }


# ---------------------------------------------------------------------------
# tool implementations
# ---------------------------------------------------------------------------


def tool_swarm_recent(args: dict) -> dict:
    swarm_id = args.get("swarm_id", "default")
    limit = max(1, min(int(args.get("limit", 25)), 200))
    with _connect() as c:
        rows = c.execute(
            "SELECT id, swarm_id, agent_id, topic, content, tags, meta, created "
            "FROM swarm_entries WHERE swarm_id = ? ORDER BY created DESC LIMIT ?",
            (swarm_id, limit),
        ).fetchall()
    return {"swarm_id": swarm_id, "count": len(rows),
            "entries": [_row_to_dict(r) for r in rows]}


def tool_swarm_search(args: dict) -> dict:
    query = (args.get("query") or "").strip()
    if not query:
        raise ValueError("search 'query' required")
    swarm_id = args.get("swarm_id")  # None = all partitions
    limit = max(1, min(int(args.get("limit", 50)), 200))
    q = f"%{query}%"
    sql = (
        "SELECT id, swarm_id, agent_id, topic, content, tags, meta, created "
        "FROM swarm_entries WHERE (content LIKE ? OR topic LIKE ? OR agent_id LIKE ?)"
    )
    params: list = [q, q, q]
    if swarm_id:
        sql += " AND swarm_id = ?"
        params.append(swarm_id)
    sql += " ORDER BY created DESC LIMIT ?"
    params.append(limit)
    with _connect() as c:
        rows = c.execute(sql, params).fetchall()
    return {"query": query, "swarm_id": swarm_id, "count": len(rows),
            "entries": [_row_to_dict(r) for r in rows]}


def tool_swarm_write(args: dict) -> dict:
    content = args.get("content")
    if not content or not isinstance(content, str):
        raise ValueError("write 'content' (non-empty string) required")
    swarm_id = args.get("swarm_id", "default")
    agent_id = args.get("agent_id", "mcp-client")
    topic = args.get("topic", "")
    tags = args.get("tags", [])
    meta = args.get("meta", {})
    if not isinstance(tags, list):
        raise ValueError("tags must be a list")
    if not isinstance(meta, dict):
        raise ValueError("meta must be a dict")
    eid = str(uuid.uuid4())
    now = time.time()
    with _connect() as c:
        c.execute(
            "INSERT INTO swarm_entries (id, swarm_id, agent_id, topic, content, tags, meta, created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (eid, swarm_id, agent_id, topic, content,
             json.dumps(tags, ensure_ascii=False),
             json.dumps(meta, ensure_ascii=False), now),
        )
        c.commit()
    return {"id": eid, "swarm_id": swarm_id, "agent_id": agent_id,
            "topic": topic, "created_unix": now}


def tool_swarm_partitions(_args: dict) -> dict:
    with _connect() as c:
        rows = c.execute(
            "SELECT swarm_id, COUNT(*) AS n, MAX(created) AS last_ts "
            "FROM swarm_entries GROUP BY swarm_id ORDER BY n DESC"
        ).fetchall()
    return {"partitions": [
        {"swarm_id": r["swarm_id"], "count": int(r["n"]),
         "last_unix": float(r["last_ts"]) if r["last_ts"] else None}
        for r in rows
    ]}


def tool_swarm_get(args: dict) -> dict:
    eid = args.get("id")
    if not eid:
        raise ValueError("'id' required")
    with _connect() as c:
        row = c.execute(
            "SELECT id, swarm_id, agent_id, topic, content, tags, meta, created "
            "FROM swarm_entries WHERE id = ?",
            (eid,),
        ).fetchone()
    if not row:
        return {"id": eid, "found": False}
    return {"found": True, **_row_to_dict(row)}


TOOLS = [
    {
        "name": "swarm_recent",
        "description": "List the newest N entries in a swarm partition. "
                       "Cheap. Default partition='default', limit=25.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "swarm_id": {"type": "string", "description": "partition name",
                             "default": "default"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200,
                          "default": 25},
            },
        },
    },
    {
        "name": "swarm_search",
        "description": "Substring match across content/topic/agent_id. "
                       "If swarm_id omitted, searches ALL partitions.",
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "swarm_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200,
                          "default": 50},
            },
        },
    },
    {
        "name": "swarm_write",
        "description": "Append a new entry to a swarm partition. "
                       "Returns the assigned uuid.",
        "inputSchema": {
            "type": "object",
            "required": ["content"],
            "properties": {
                "content": {"type": "string", "minLength": 1},
                "swarm_id": {"type": "string", "default": "default"},
                "agent_id": {"type": "string", "default": "mcp-client"},
                "topic": {"type": "string", "default": ""},
                "tags": {"type": "array", "items": {"type": "string"}},
                "meta": {"type": "object"},
            },
        },
    },
    {
        "name": "swarm_partitions",
        "description": "List all swarm_id partitions with row counts and last write time.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "swarm_get",
        "description": "Fetch one entry by its uuid.",
        "inputSchema": {
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "string"}},
        },
    },
]

DISPATCH = {
    "swarm_recent": tool_swarm_recent,
    "swarm_search": tool_swarm_search,
    "swarm_write": tool_swarm_write,
    "swarm_partitions": tool_swarm_partitions,
    "swarm_get": tool_swarm_get,
}


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 dispatch
# ---------------------------------------------------------------------------


def reply_result(rid, result: dict) -> None:
    msg = {"jsonrpc": "2.0", "id": rid, "result": result}
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def reply_error(rid, code: int, message: str, data=None) -> None:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    msg = {"jsonrpc": "2.0", "id": rid, "error": err}
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def handle(req: dict) -> None:
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        reply_result(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
        return

    if method == "notifications/initialized":
        log("client initialized")
        return  # notifications carry no id, no reply

    if method == "ping":
        reply_result(rid, {})
        return

    if method == "tools/list":
        reply_result(rid, {"tools": TOOLS})
        return

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = DISPATCH.get(name)
        if not fn:
            reply_error(rid, -32601, f"unknown tool: {name}")
            return
        try:
            out = fn(args)
            reply_result(rid, {"content": [{"type": "text",
                                             "text": json.dumps(out, default=str, indent=2)}]})
        except ValueError as e:
            reply_error(rid, -32602, str(e))
        except Exception as e:
            log(f"tool {name!r} crashed: {e}\n{traceback.format_exc()}")
            reply_error(rid, -32603, f"{type(e).__name__}: {e}")
        return

    if method in ("resources/list", "prompts/list"):
        reply_result(rid, {method.split("/")[0]: []})
        return

    if rid is not None:
        reply_error(rid, -32601, f"method not found: {method}")


def main() -> int:
    if not DB_PATH.exists():
        log(f"FATAL: db not found at {DB_PATH}")
        return 2
    log(f"ready · db={DB_PATH} · pid={os.getpid()}")
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError as e:
            log(f"parse error: {e}")
            continue
        try:
            handle(req)
        except Exception as e:
            log(f"dispatch crash: {e}\n{traceback.format_exc()}")
    log("stdin closed; exiting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
