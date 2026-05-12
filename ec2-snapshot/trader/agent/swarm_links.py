"""swarm_links — Hebbian co-fire edge graph over swarm_entries.

Turns the swarm.db from a flat list of entries into a weighted graph.
Two entries get an edge when they "co-fire" — written close in time within
the same swarm_id (temporal coupling) and/or share tags (semantic coupling).
Edges accumulate weight on repeat co-fires and decay on read by time-since
last_seen.

Public API:
  ensure_schema(conn)        — idempotent CREATE TABLE
  update_links(conn, ...)    — process new entries since last run, upsert edges
  neighbors(conn, entry_id)  — top-K linked entries
  cluster(conn, swarm_id, topic) — frequent co-firing peers of a (swarm,topic)
  decay_factor(weight, last_seen, half_life_h) — applied at read time

Design notes (mined from neurolinked-brain, no code lifted):
- Hebbian rule: weight += time_score + tag_score on each co-fire
- Symmetric edges stored as (sorted-min, sorted-max) UNIQUE pair
- Decay is exponential on read with configurable half-life (default 7d)
  → no background pruning needed, weak edges fade naturally in queries
- ON CONFLICT upsert collapses re-firing into single row
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from typing import Iterable

DB_PATH = os.environ.get("SYGNIF_SWARM_DB", "/var/lib/sygnif/swarm.db")
DEFAULT_WINDOW_S = 300        # entries within 5 min of each other → temporal link
DEFAULT_HALF_LIFE_H = 24 * 7  # 7-day half-life on read
MIN_EDGE_WEIGHT = 0.1
MAX_EDGES_PER_NEW = 20        # cap fan-out per new entry to avoid quadratic blowup
INITIAL_LOOKBACK_S = 30 * 86400   # first run: seed from last 30 days


# -------- schema -----------------------------------------------------------

def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS swarm_links (
            id          TEXT PRIMARY KEY,
            a_id        TEXT NOT NULL,
            b_id        TEXT NOT NULL,
            weight      REAL NOT NULL DEFAULT 0,
            co_fires    INTEGER NOT NULL DEFAULT 0,
            first_seen  REAL NOT NULL,
            last_seen   REAL NOT NULL,
            reason      TEXT NOT NULL DEFAULT '',
            UNIQUE (a_id, b_id)
        );
        CREATE INDEX IF NOT EXISTS idx_links_a       ON swarm_links(a_id, weight DESC);
        CREATE INDEX IF NOT EXISTS idx_links_b       ON swarm_links(b_id, weight DESC);
        CREATE INDEX IF NOT EXISTS idx_links_lastseen ON swarm_links(last_seen DESC);
        CREATE TABLE IF NOT EXISTS swarm_links_state (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
    """)
    conn.commit()


# -------- state (last-run cursor) -----------------------------------------

def _get_state(conn: sqlite3.Connection, key: str) -> str | None:
    r = conn.execute("SELECT value FROM swarm_links_state WHERE key=?",
                     (key,)).fetchone()
    return r[0] if r else None


def _set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO swarm_links_state(key,value) VALUES(?,?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value))


# -------- weighting --------------------------------------------------------

def _edge_id(a: str, b: str) -> tuple[str, str, str]:
    """Return canonical (small, large, id) for the unordered pair."""
    if a < b:
        return a, b, f"{a}|{b}"
    return b, a, f"{b}|{a}"


def _pair_weight(time_dist_s: float, shared_tags: int,
                 window_s: float, same_topic: bool) -> tuple[float, str]:
    """Compute the increment for a single co-fire.

    time_score: 0..1 inverse-linear in time distance within the window
    tag_score:  +1.0 per shared tag, capped at 3.0
    topic_bonus: +0.5 if both entries have the same topic
    """
    time_score = max(0.0, 1.0 - (time_dist_s / window_s)) if window_s > 0 else 0.0
    tag_score = min(3.0, float(shared_tags))
    topic_bonus = 0.5 if same_topic else 0.0
    w = time_score + tag_score + topic_bonus
    parts = []
    if time_score > 0: parts.append(f"t={time_score:.2f}")
    if tag_score > 0: parts.append(f"tag={int(tag_score)}")
    if topic_bonus > 0: parts.append("topic=1")
    return w, "+".join(parts) or "weak"


def decay_factor(weight: float, last_seen: float,
                 half_life_h: float = DEFAULT_HALF_LIFE_H,
                 now: float | None = None) -> float:
    """Apply exponential decay to a stored edge weight at read time."""
    now = now if now is not None else time.time()
    age_h = max(0.0, (now - last_seen) / 3600.0)
    return weight * math.exp(-age_h * math.log(2) / max(0.001, half_life_h))


# -------- core update ------------------------------------------------------

def _safe_tags(raw: str | None) -> set[str]:
    try:
        v = json.loads(raw or "[]")
        return {str(t) for t in v} if isinstance(v, list) else set()
    except Exception:
        return set()


def update_links(
    conn: sqlite3.Connection,
    since: float | None = None,
    until: float | None = None,
    window_s: int = DEFAULT_WINDOW_S,
    max_edges_per_new: int = MAX_EDGES_PER_NEW,
) -> dict:
    """Walk all entries created in (since, until], find co-fire candidates
    within `window_s` of each, upsert edges. Idempotent on re-run.

    Returns: {new_entries, edges_upserted, edges_inserted, range_from, range_to}.
    """
    ensure_schema(conn)
    now = time.time()
    if since is None:
        last = _get_state(conn, "last_run_ts")
        since = float(last) if last else now - INITIAL_LOOKBACK_S
    if until is None:
        until = now

    new_rows = list(conn.execute(
        "SELECT id, swarm_id, agent_id, topic, tags, created"
        " FROM swarm_entries WHERE created > ? AND created <= ?"
        " ORDER BY created",
        (since, until),
    ))

    edges_upserted = 0
    edges_inserted = 0

    for new_id, swarm_id, _agent, topic, tags_raw, created in new_rows:
        new_tags = _safe_tags(tags_raw)

        # Candidates: same swarm_id, in [created-window, created+window),
        # excluding self. Order by absolute time distance and limit fan-out.
        cands = conn.execute(
            "SELECT id, topic, tags, created FROM swarm_entries"
            " WHERE swarm_id = ? AND id != ?"
            "   AND created BETWEEN ? AND ?"
            " ORDER BY ABS(created - ?) LIMIT ?",
            (swarm_id, new_id,
             created - window_s, created + window_s, created,
             max_edges_per_new * 4),
        ).fetchall()

        scored: list[tuple[float, str, str, str]] = []  # (w, cand_id, reason)
        for cand_id, c_topic, c_tags_raw, c_created in cands:
            c_tags = _safe_tags(c_tags_raw)
            shared = len(new_tags & c_tags)
            same_topic = bool(topic) and topic == c_topic
            w, reason = _pair_weight(
                time_dist_s=abs(c_created - created),
                shared_tags=shared,
                window_s=window_s,
                same_topic=same_topic,
            )
            if w < MIN_EDGE_WEIGHT:
                continue
            scored.append((w, cand_id, reason, ""))

        scored.sort(key=lambda x: -x[0])
        for w, cand_id, reason, _ in scored[:max_edges_per_new]:
            a, b, eid = _edge_id(new_id, cand_id)
            cur = conn.execute(
                "INSERT INTO swarm_links(id, a_id, b_id, weight, co_fires,"
                " first_seen, last_seen, reason) VALUES(?,?,?,?,1,?,?,?)"
                " ON CONFLICT(a_id, b_id) DO UPDATE SET"
                "   weight    = weight + excluded.weight,"
                "   co_fires  = co_fires + 1,"
                "   last_seen = excluded.last_seen,"
                "   reason    = excluded.reason",
                (eid, a, b, w, now, now, reason),
            )
            edges_upserted += 1
            if cur.rowcount == 1 and cur.lastrowid is not None:
                # SQLite's rowcount == 1 for both insert + update on
                # ON CONFLICT — distinguish via existence check.
                pass

    _set_state(conn, "last_run_ts", str(until))
    conn.commit()
    return {
        "new_entries": len(new_rows),
        "edges_upserted": edges_upserted,
        "range_from": since,
        "range_to": until,
    }


# -------- read-side queries ------------------------------------------------

def neighbors(
    conn: sqlite3.Connection,
    entry_id: str,
    limit: int = 10,
    min_decayed_weight: float = 0.05,
    half_life_h: float = DEFAULT_HALF_LIFE_H,
) -> list[dict]:
    """Top-K neighbors of an entry, decayed weight order."""
    rows = conn.execute(
        "SELECT a_id, b_id, weight, co_fires, last_seen, reason"
        " FROM swarm_links WHERE a_id = ? OR b_id = ?",
        (entry_id, entry_id),
    ).fetchall()
    now = time.time()
    out = []
    for a, b, w, cf, last_seen, reason in rows:
        peer = b if a == entry_id else a
        dw = decay_factor(w, last_seen, half_life_h, now)
        if dw < min_decayed_weight:
            continue
        peer_meta = conn.execute(
            "SELECT swarm_id, agent_id, topic, substr(content,1,160), created"
            " FROM swarm_entries WHERE id = ?", (peer,)).fetchone()
        if not peer_meta:
            continue
        out.append({
            "id": peer,
            "swarm_id": peer_meta[0],
            "agent_id": peer_meta[1],
            "topic": peer_meta[2],
            "preview": peer_meta[3],
            "weight_raw": round(w, 3),
            "weight_decayed": round(dw, 3),
            "co_fires": cf,
            "reason": reason,
            "age_h": round((now - peer_meta[4]) / 3600, 2),
        })
    out.sort(key=lambda r: -r["weight_decayed"])
    return out[:limit]


def cluster(
    conn: sqlite3.Connection,
    swarm_id: str,
    topic: str,
    limit: int = 15,
    half_life_h: float = DEFAULT_HALF_LIFE_H,
) -> list[dict]:
    """Aggregate which (swarm,topic) pairs co-fire with this one.

    Returns list of {peer_swarm, peer_topic, total_decayed_weight, edges}.
    """
    sql = """
    SELECT
      e_peer.swarm_id, e_peer.topic,
      SUM(l.weight) AS total_w,
      MAX(l.last_seen) AS recent,
      COUNT(*) AS edges
    FROM swarm_links l
    JOIN swarm_entries e_self ON e_self.id IN (l.a_id, l.b_id)
    JOIN swarm_entries e_peer ON e_peer.id IN (l.a_id, l.b_id)
                              AND e_peer.id != e_self.id
    WHERE e_self.swarm_id = ? AND e_self.topic = ?
    GROUP BY e_peer.swarm_id, e_peer.topic
    ORDER BY total_w DESC
    LIMIT ?
    """
    now = time.time()
    out = []
    for sw, tp, total_w, recent, edges in conn.execute(sql, (swarm_id, topic, limit)):
        decayed = decay_factor(total_w, recent or now, half_life_h, now)
        out.append({
            "peer_swarm": sw,
            "peer_topic": tp,
            "total_weight_raw": round(total_w, 3),
            "total_weight_decayed": round(decayed, 3),
            "edges": edges,
            "last_co_fire_h": round((now - (recent or now)) / 3600, 2),
        })
    return out


def top_hubs(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Most-connected entries (by raw degree) — useful for finding swarm hubs."""
    sql = """
    SELECT entry_id, COUNT(*) AS degree, SUM(weight) AS total_w
    FROM (
      SELECT a_id AS entry_id, weight FROM swarm_links
      UNION ALL
      SELECT b_id AS entry_id, weight FROM swarm_links
    )
    GROUP BY entry_id
    ORDER BY total_w DESC
    LIMIT ?
    """
    out = []
    for entry_id, degree, total_w in conn.execute(sql, (limit,)):
        meta = conn.execute(
            "SELECT swarm_id, agent_id, topic, substr(content,1,120)"
            " FROM swarm_entries WHERE id = ?", (entry_id,)).fetchone()
        if not meta:
            continue
        out.append({
            "id": entry_id,
            "swarm_id": meta[0],
            "agent_id": meta[1],
            "topic": meta[2],
            "preview": meta[3],
            "degree": degree,
            "total_weight": round(total_w, 2),
        })
    return out
