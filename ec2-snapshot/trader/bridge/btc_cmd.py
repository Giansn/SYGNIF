"""sygnif btc — read views over the swarm DB filtered to the BTC trading lab.

Used as a subcommand from the main sygnif CLI. Pure-stdlib. Read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

DB_DEFAULT = Path(os.environ.get("SWARM_KNOWLEDGE_DB", "/var/lib/sygnif/swarm.db"))
DEFAULT_SWARM_ID = "btc_demo"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _fmt(rows, cols=("created", "topic", "agent_id", "content")):
    for r in rows:
        ts = time.strftime("%m-%d %H:%M", time.localtime(r["created"]))
        line = f"{ts}  {r['topic']:<14}  {r['agent_id']:<14}  {r['content'][:120]}"
        print(line)


def _topic_filter(args, default_topics: list[str]) -> tuple[str, list]:
    topics = default_topics
    if getattr(args, "topic", None):
        topics = [args.topic]
    placeholders = ",".join("?" for _ in topics)
    return f"AND topic IN ({placeholders})", topics


def cmd_status(args) -> int:
    conn = _connect(args.db)
    cur = conn.execute(
        "SELECT COUNT(*) AS n, MAX(created) AS latest FROM swarm_entries WHERE swarm_id = ?",
        (args.swarm_id,),
    )
    r = cur.fetchone()
    n = r["n"]
    latest = r["latest"]
    age = "(empty)" if not latest else f"{int((time.time() - latest) // 60)} min ago"
    print(f"swarm_id={args.swarm_id}  entries={n}  latest={age}")
    cur = conn.execute(
        "SELECT topic, COUNT(*) AS n FROM swarm_entries WHERE swarm_id=? GROUP BY topic ORDER BY n DESC",
        (args.swarm_id,),
    )
    print()
    print(f"{'topic':<20} {'count':>6}")
    print("-" * 28)
    for row in cur:
        print(f"{row['topic']:<20} {row['n']:>6}")
    return 0


def cmd_recent(args) -> int:
    conn = _connect(args.db)
    where, params = _topic_filter(args, ["forecast", "trade.open", "trade.close", "resolved", "health"])
    sql = (
        "SELECT created, topic, agent_id, content FROM swarm_entries "
        f"WHERE swarm_id = ? {where} ORDER BY created DESC LIMIT ?"
    )
    rows = list(conn.execute(sql, (args.swarm_id, *params, args.limit)))
    if not rows:
        print("(no entries)")
        return 0
    _fmt(rows)
    return 0


def cmd_forecast(args) -> int:
    args.topic = "forecast"
    return cmd_recent(args)


def cmd_trades(args) -> int:
    conn = _connect(args.db)
    sql = (
        "SELECT created, topic, agent_id, content FROM swarm_entries "
        "WHERE swarm_id=? AND topic IN ('trade.open','trade.close') ORDER BY created DESC LIMIT ?"
    )
    rows = list(conn.execute(sql, (args.swarm_id, args.limit)))
    if not rows:
        print("(no trades)")
        return 0
    _fmt(rows)
    return 0


def cmd_pnl(args) -> int:
    conn = _connect(args.db)
    sql = (
        "SELECT created, agent_id, topic, content, meta FROM swarm_entries "
        "WHERE swarm_id=? AND topic='trade.close' ORDER BY created DESC LIMIT ?"
    )
    rows = list(conn.execute(sql, (args.swarm_id, args.limit)))
    n = len(rows)
    pnls = []
    for r in rows:
        try:
            m = json.loads(r["meta"])
            raw = m.get("raw") or m
            for k in ("closed_pnl", "pnl", "realized_pnl"):
                if k in raw and raw[k] is not None:
                    pnls.append(float(raw[k]))
                    break
        except Exception:
            pass
    if not pnls:
        print(f"(no closed-pnl figures in last {n} closes \u2014 trade-tag schema may not include pnl)")
        return 0
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total = sum(pnls)
    print(f"closes={len(pnls)} wins={len(wins)} losses={len(losses)} winrate={len(wins)/len(pnls):.1%} sum={total:.2f}")
    return 0


def cmd_health(args) -> int:
    conn = _connect(args.db)
    sql = "SELECT created, content, meta FROM swarm_entries WHERE swarm_id=? AND topic='health' ORDER BY created DESC LIMIT ?"
    rows = list(conn.execute(sql, (args.swarm_id, args.limit)))
    if not rows:
        print("(no health snapshots yet \u2014 wait for the next bridge tick)")
        return 0
    for r in rows:
        ts = time.strftime("%m-%d %H:%M", time.localtime(r["created"]))
        print(f"{ts}  {r['content']}")
    return 0


def cmd_pump(args) -> int:
    """Trigger the bridge ingest now (one-shot)."""
    import subprocess
    bridge = Path.home() / "sygnif-agent" / "bridge" / "btc_bridge.py"
    if not bridge.exists():
        print(f"bridge not found: {bridge}", file=sys.stderr)
        return 2
    env = os.environ.copy()
    env.setdefault("SWARM_KNOWLEDGE_DB", str(args.db))
    proc = subprocess.run([sys.executable, str(bridge)], env=env, capture_output=True, timeout=120)
    if proc.stdout:
        sys.stdout.write(proc.stdout.decode())
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode())
    return proc.returncode


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sygnif btc", description="BTC trading lab views over the swarm DB")
    p.add_argument("--db", type=Path, default=DB_DEFAULT)
    p.add_argument("--swarm-id", default=DEFAULT_SWARM_ID)
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("status", help="overall lab summary")
    ps.set_defaults(func=cmd_status)

    pr = sub.add_parser("recent", help="last N events across forecast / open / close / health")
    pr.add_argument("--limit", type=int, default=15)
    pr.add_argument("--topic", default=None)
    pr.set_defaults(func=cmd_recent)

    pf = sub.add_parser("forecast", help="last N forecasts")
    pf.add_argument("--limit", type=int, default=10)
    pf.set_defaults(func=cmd_forecast)

    pt = sub.add_parser("trades", help="last N opens/closes")
    pt.add_argument("--limit", type=int, default=10)
    pt.set_defaults(func=cmd_trades)

    pp = sub.add_parser("pnl", help="aggregate closed-pnl from trade tags")
    pp.add_argument("--limit", type=int, default=200)
    pp.set_defaults(func=cmd_pnl)

    ph = sub.add_parser("health", help="EC2 trading lab process/service health")
    ph.add_argument("--limit", type=int, default=5)
    ph.set_defaults(func=cmd_health)

    pu = sub.add_parser("pump", help="trigger the bridge ingest now")
    pu.set_defaults(func=cmd_pump)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
