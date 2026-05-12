#!/usr/bin/env python3
"""SYGNIF journal daily-report → NeuroLinked.

Aggregates the previous 24h of trade-journal records into a compact
summary line and posts it to the brain. Run nightly at 00:00 UTC by
sygnif-journal-daily.timer.

Output schema:
  SYGNIF_JOURNAL_DAILY v1 date=YYYY-MM-DD
    entries=N exits=N blocks=N
    paths=[entry_path:n=N|w=N|l=N|$=±X.XX,...]
    exits=[exit_path:n=N|$=±X.XX,...]
    blocks=[gate:n=N,...]
    edges=[feature:lo=X%|hi=Y%|edge=±Zpp,...]
    summary=<one-line synopsis>

Run: python3 sygnif_journal_daily_report.py [--days 1]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO,
                     format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sygnif_journal_report")

NL_URL = (os.environ.get("SYGNIF_NEUROLINKED_HOST_URL")
          or "http://127.0.0.1:8889").rstrip("/")
JOURNAL_DIR = Path(os.environ.get("SYGNIF_JOURNAL_DIR",
                                    str(Path.home() / ".sygnif" / "journal")))
POST_TIMEOUT = int(os.environ.get("REPORT_POST_TIMEOUT_SEC", "30"))


def _iter_records(kind: str, since: datetime, until: datetime):
    for path in sorted(JOURNAL_DIR.glob(f"{kind}-*.ndjson")):
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = rec.get("ts_utc")
                    if not ts:
                        continue
                    try:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if since <= dt <= until:
                        yield rec
        except OSError:
            continue


def _post_nl(text: str) -> bool:
    body = json.dumps({"text": text, "skip_claude_bridge": True}).encode("utf-8")
    req = urllib.request.Request(f"{NL_URL}/api/input/text", data=body,
                                  headers={"Content-Type": "application/json"},
                                  method="POST")
    try:
        with urllib.request.urlopen(req, timeout=POST_TIMEOUT) as r:
            return r.status == 200
    except Exception as e:
        log.warning("NL post failed: %s", e)
        return False


def aggregate(entries, exits, blocks):
    """Produce the compact aggregation dicts used in the output line."""
    exit_by_entry = {ex.get("linked_entry_decision_id"): ex
                      for ex in exits if ex.get("linked_entry_decision_id")}
    paths = defaultdict(lambda: {"n": 0, "wins": 0, "losses": 0,
                                    "total_pnl": 0.0})
    for en in entries:
        p = en.get("entry_path", "?")
        d = paths[p]
        d["n"] += 1
        ex = exit_by_entry.get(en.get("decision_id"))
        if ex:
            d["total_pnl"] += float(ex.get("realized_pnl_usdt") or 0)
            o = ex.get("outcome")
            if o == "win":
                d["wins"] += 1
            elif o == "loss":
                d["losses"] += 1

    exit_paths = defaultdict(lambda: {"n": 0, "total_pnl": 0.0})
    for ex in exits:
        d = exit_paths[ex.get("exit_path", "?")]
        d["n"] += 1
        d["total_pnl"] += float(ex.get("realized_pnl_usdt") or 0)

    block_gates = defaultdict(int)
    for b in blocks:
        block_gates[b.get("blocking_gate", "?")] += 1

    # Co-occurrence: for each numeric ctx feature, win-rate above vs below
    # median across decided pairs.
    decided = [(en.get("ctx") or {},
                exit_by_entry[en.get("decision_id")].get("outcome") == "win")
                for en in entries
                if en.get("decision_id") in exit_by_entry
                and exit_by_entry[en.get("decision_id")].get("outcome")]
    edges = []
    if len(decided) >= 4:
        feat = defaultdict(list)
        for ctx, won in decided:
            for k, v in ctx.items():
                if isinstance(v, (int, float)):
                    feat[k].append((v, won))
        for k, vs in feat.items():
            if len(vs) < 4:
                continue
            sorted_vs = sorted(vs, key=lambda x: x[0])
            n = len(sorted_vs)
            lo = sorted_vs[:n // 2]
            hi = sorted_vs[n // 2:]
            lo_w = sum(1 for _, w in lo if w) / max(len(lo), 1)
            hi_w = sum(1 for _, w in hi if w) / max(len(hi), 1)
            e = (hi_w - lo_w) * 100
            edges.append((abs(e), k, lo_w, hi_w, e))
        edges.sort(reverse=True)
        edges = edges[:5]

    return paths, exit_paths, block_gates, edges, decided


def _format_line(date_iso, entries, exits, blocks, paths, exit_paths,
                  block_gates, edges, decided) -> str:
    paths_s = "|".join(
        f"{p}:n={d['n']}/w={d['wins']}/l={d['losses']}/${d['total_pnl']:+.2f}"
        for p, d in sorted(paths.items(), key=lambda kv: -kv[1]["total_pnl"])
    )
    exits_s = "|".join(
        f"{p}:n={d['n']}/${d['total_pnl']:+.2f}"
        for p, d in sorted(exit_paths.items(), key=lambda kv: -kv[1]["n"])
    )
    blocks_s = "|".join(f"{g}:n={n}"
                         for g, n in sorted(block_gates.items(),
                                              key=lambda kv: -kv[1]))
    edges_s = "|".join(
        f"{k}:lo={lo*100:.0f}%/hi={hi*100:.0f}%/edge={e:+.0f}pp"
        for _, k, lo, hi, e in edges
    )
    # synopsis
    if not entries:
        synopsis = "no entries"
    else:
        total_pnl = sum(d["total_pnl"] for d in paths.values())
        n_decided = sum(d["wins"] + d["losses"] for d in paths.values())
        win_rate = (sum(d["wins"] for d in paths.values()) / n_decided * 100) if n_decided else 0.0
        best = max(paths.items(), key=lambda kv: kv[1]["total_pnl"]) if paths else None
        synopsis = (f"net=${total_pnl:+.2f} win%={win_rate:.0f} "
                    f"top={best[0] if best else '-'}")

    parts = [
        f"SYGNIF_JOURNAL_DAILY v1 date={date_iso}",
        f"entries={len(entries)} exits={len(exits)} blocks={len(blocks)}",
    ]
    if paths_s:
        parts.append(f"paths=[{paths_s}]")
    if exits_s:
        parts.append(f"exits=[{exits_s}]")
    if blocks_s:
        parts.append(f"blocks=[{blocks_s}]")
    if edges_s:
        parts.append(f"edges=[{edges_s}]")
    parts.append(f"summary={synopsis}")
    return " ".join(parts)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=1)
    p.add_argument("--no-post", action="store_true",
                    help="just print, don't POST to NL brain")
    args = p.parse_args()

    until = datetime.now(timezone.utc)
    since = until - timedelta(days=args.days)

    entries = list(_iter_records("entry", since, until))
    exits = list(_iter_records("exit", since, until))
    blocks = list(_iter_records("block", since, until))

    paths, exit_paths, block_gates, edges, decided = aggregate(
        entries, exits, blocks)

    line = _format_line(until.strftime("%Y-%m-%d"), entries, exits, blocks,
                         paths, exit_paths, block_gates, edges, decided)
    print(line)

    if args.no_post:
        log.info("--no-post specified, skipping NL POST")
        return 0
    if not entries and not exits and not blocks:
        log.info("no journal records in window — skipping NL POST")
        return 0
    ok = _post_nl(line)
    log.info("posted to NL: %s (line len=%d)", ok, len(line))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
