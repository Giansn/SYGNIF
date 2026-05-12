#!/usr/bin/env python3
"""sygnif_challenger_report.py — Phase 3.4 daily report on what the gate
optimizer is proposing vs what's currently in production.

Emits a swarm row to agent.review.challenger_diff (and via the existing
sygnif-telegram-relay) so the operator sees pending optimizer proposals
in their daily morning brief without grepping JSON files.

Operator promotes a proposal by:
  1. Review the diff (this report or `cat /var/lib/sygnif/gate_params_challenger.json`)
  2. Promote: cp gate_params_challenger.json gate_params.json
  3. Restart trader: systemctl restart sygnif-trader sygnif-bybit-daemon
  4. Tier_audit + drift_monitor will catch any regressions

Run:
  python3 /opt/sygnif-services/sygnif_challenger_report.py
Wired by sygnif-challenger-report.timer (daily 09:00 UTC, after morning brief).
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
import uuid

sys.path.insert(0, "/home/ubuntu/sygnif-agent-mirror")
try:
    from agent import gate_params as GP
except Exception as e:
    print(f"FATAL: {e}", file=sys.stderr)
    sys.exit(1)

DB = "/var/lib/sygnif/swarm.db"


def main() -> int:
    diff = GP.champion_vs_challenger()
    print(f"=== challenger_report @ "
          f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ===")
    print(f"  challenger present:  {diff['challenger_present']}")
    print(f"  challenger version:  {diff['challenger_version']}")
    print(f"  challenger updated:  {diff['challenger_updated']}")

    diffs = diff.get("diffs") or {}
    if not diffs:
        print("  no pending changes")
        head = "CHALLENGER: no pending proposals"
        meta = diff
    else:
        print(f"  {len(diffs)} pending change(s):")
        for k, v in diffs.items():
            c = v.get("champion")
            ch = v.get("challenger")
            print(f"    {k}: {c} → {ch}")
        head_lines = [f"{k}: {v['champion']}→{v['challenger']}"
                      for k, v in diffs.items()]
        head = (f"CHALLENGER {len(diffs)} pending: "
                + "; ".join(head_lines)[:200])
        meta = diff

    try:
        c = sqlite3.connect(DB, timeout=10)
        rid = str(uuid.uuid4())
        c.execute(
            "INSERT OR IGNORE INTO swarm_entries "
            "(id, created, swarm_id, agent_id, topic, content, meta, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, int(time.time()), "trading", "sygnif-challenger-report",
             "agent.review.challenger_diff", head,
             json.dumps(meta, default=str),
             json.dumps(["challenger", "report"]
                        + (["pending"] if diffs else []))))
        c.commit()
        c.close()
        print(f"  swarm row written: {rid}")
    except Exception as e:
        print(f"  swarm write failed: {type(e).__name__}: {e}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
