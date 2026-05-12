#!/usr/bin/env python3
"""sygnif_whale_alignment_audit.py — daily compare SYGNIF vs whales.

For all attributed outcomes in the last 7 days, bucket by whale_alignment:
  ALIGNED   — SYGNIF traded same direction as whales (≥0.65 imbalance)
  DIVERGED  — SYGNIF traded opposite to whales
  NEUTRAL   — whales balanced (no clear direction)

For each bucket: n trades, win rate, avg P&L, avg R per trade.

The actionable question: is divergence profitable, or do we just lose
when we go against the smart money?

Possible findings:
  1. ALIGNED beats DIVERGED → trader should defer to whales when they speak.
     Action: enable whale_alignment_required gate.
  2. DIVERGED beats ALIGNED → SYGNIF has independent edge; whales are noise/lag.
     Action: ignore whale flow, current state is fine.
  3. ALIGNED ≈ DIVERGED → whale flow is uncorrelated with our edge.
     Action: leave it as observability-only feature.
  4. NEUTRAL bucket dominates → not enough whale activity to act on; need
     a second signal source.

Emits agent.review.whale_alignment swarm row daily.

Run:
  python3 /opt/sygnif-services/sygnif_whale_alignment_audit.py
Wired by sygnif-whale-alignment-audit.timer (daily 04:00 UTC).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid
from collections import defaultdict
from typing import Any

DB = "/var/lib/sygnif/swarm.db"
WINDOW_DAYS = 7


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def _bucket(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    pnls = [r.get("pnl", 0) for r in rows]
    rs   = [r.get("r", 0) for r in rows if r.get("r") is not None]
    wins = sum(1 for p in pnls if p > 0)
    return {
        "n":         len(rows),
        "win_rate":  round(wins / len(rows), 3),
        "total_pnl": round(sum(pnls), 4),
        "avg_pnl":   round(sum(pnls) / len(rows), 4),
        "avg_r":     round(sum(rs) / len(rs), 3) if rs else None,
        "n_with_r":  len(rs),
    }


def main() -> int:
    if not os.path.exists(DB):
        print(f"swarm.db not found at {DB}", file=sys.stderr)
        return 1
    since = int(time.time()) - WINDOW_DAYS * 86400
    c = _connect()

    # Pull all snapshots + outcomes in window
    snapshots = {}
    for (rid, content_meta) in c.execute(
        "SELECT id, meta FROM swarm_entries WHERE topic='decision.snapshot' "
        "AND created > ?", (since,)):
        try:
            m = json.loads(content_meta)
        except json.JSONDecodeError:
            continue
        cid = m.get("correlation_id")
        if cid:
            snapshots[cid] = m

    outcomes = {}
    for (rid, content_meta) in c.execute(
        "SELECT id, meta FROM swarm_entries WHERE topic='outcome.attributed' "
        "AND created > ?", (since,)):
        try:
            m = json.loads(content_meta)
        except json.JSONDecodeError:
            continue
        cid = m.get("correlation_id")
        if cid:
            outcomes.setdefault(cid, []).append(m)
    c.close()

    print(f"=== whale_alignment_audit @ "
          f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ===")
    print(f"  window:      {WINDOW_DAYS}d")
    print(f"  snapshots:   {len(snapshots)}")
    print(f"  outcomes:    {len(outcomes)}")

    # Bucket outcomes by snapshot's whale_alignment
    buckets: dict[str, list[dict]] = defaultdict(list)
    n_with_alignment = 0
    n_with_outcome = 0
    for cid, snap in snapshots.items():
        outs = outcomes.get(cid, [])
        if not outs:
            continue
        n_with_outcome += 1
        wa = snap.get("whale_alignment") or {}
        alignment = wa.get("alignment", "no_data")
        if alignment in ("aligned", "diverged", "neutral"):
            n_with_alignment += 1
        # Aggregate multi-leg outcomes
        sum_pnl = sum(float(o.get("closed_pnl") or 0) for o in outs)
        # Best-effort R
        risk = (snap.get("plan") or {}).get("max_loss_usd")
        r_val = sum_pnl / float(risk) if risk and float(risk) > 0 else None
        buckets[alignment].append({
            "cid":      cid,
            "pnl":      sum_pnl,
            "r":        r_val,
            "structure": (snap.get("plan") or {}).get("structure"),
            "score":    ((snap.get("tier_promotion") or {})
                          .get("candidates") or {}).get("score"),
            "whale_strength":  wa.get("strength"),
            "plan_direction":  wa.get("plan_direction"),
            "whale_direction": wa.get("whale_direction"),
        })

    print(f"  joined (snap+outcome): {n_with_outcome}")
    print(f"  with alignment data:   {n_with_alignment}")
    print()

    payload = {
        "window_days":          WINDOW_DAYS,
        "n_snapshots":          len(snapshots),
        "n_outcomes":           len(outcomes),
        "n_joined":             n_with_outcome,
        "n_with_alignment":     n_with_alignment,
        "buckets":              {},
        "recommendation":       None,
    }

    print(f"  --- buckets ---")
    for bk in ("aligned", "diverged", "neutral", "no_data"):
        agg = _bucket(buckets.get(bk, []))
        payload["buckets"][bk] = agg
        if agg["n"] == 0: continue
        print(f"  {bk:10s} n={agg['n']:3d}  "
              f"win={agg.get('win_rate', 0):.0%}  "
              f"total_pnl=${agg.get('total_pnl', 0):+.2f}  "
              f"avg_pnl=${agg.get('avg_pnl', 0):+.4f}  "
              f"avg_R={agg.get('avg_r')}")

    a = payload["buckets"].get("aligned",  {"avg_r": None, "n": 0})
    d = payload["buckets"].get("diverged", {"avg_r": None, "n": 0})

    # Recommendation logic
    if a["n"] >= 20 and d["n"] >= 20 and a["avg_r"] is not None and d["avg_r"] is not None:
        delta = a["avg_r"] - d["avg_r"]
        if delta >= 0.30:
            rec = ("ENABLE whale_alignment_required: aligned beats diverged "
                   f"by {delta:+.2f} R/trade — defer to whales")
        elif delta <= -0.30:
            rec = ("KEEP independent: diverged beats aligned by "
                   f"{-delta:+.2f} R/trade — SYGNIF has edge against the herd")
        else:
            rec = ("NEUTRAL: |delta R| < 0.30 — whale flow uncorrelated with our edge; "
                   "keep as observability-only feature")
        payload["recommendation"] = rec
        print(f"\n  RECOMMENDATION: {rec}")
    else:
        n_min = min(a["n"], d["n"])
        rec = (f"INSUFFICIENT DATA: need ≥20 trades per bucket; "
               f"aligned={a['n']} diverged={d['n']}")
        payload["recommendation"] = rec
        print(f"\n  {rec}")

    # Emit to swarm
    try:
        wc = sqlite3.connect(DB, timeout=10)
        rid = str(uuid.uuid4())
        head = (f"WHALE ALIGNMENT {WINDOW_DAYS}d "
                f"aligned={a['n']}({a.get('avg_r','?')}) "
                f"diverged={d['n']}({d.get('avg_r','?')}) "
                f"→ {payload['recommendation'][:80] if payload['recommendation'] else 'no_rec'}")
        wc.execute(
            "INSERT OR IGNORE INTO swarm_entries "
            "(id, created, swarm_id, agent_id, topic, content, meta, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, int(time.time()), "trading", "sygnif-whale-alignment-audit",
             "agent.review.whale_alignment", head,
             json.dumps(payload, default=str),
             json.dumps(["whale", "alignment", "audit"])))
        wc.commit()
        wc.close()
    except Exception as e:
        print(f"  swarm write failed: {type(e).__name__}: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
