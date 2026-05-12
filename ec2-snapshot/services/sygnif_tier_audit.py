#!/usr/bin/env python3
"""sygnif_tier_audit.py — daily champion/challenger audit for tier promotion.

For the rolling 14-day window, joins:
  agent.tier_promoted    (promoted plans + which tier flags fired + env)
  trade.open             (what was actually executed)
  trade.close            (closedPnl per leg)

Computes:
  promoted cohort   — trades opened with leverage_tier or size_tier set
  default cohort    — trades opened without those flags (baseline)

Reports per env (demo / live):
  count, win_rate, avg_R, total_pnl
  Δ R/trade vs baseline

Emits one summary row to swarm topic agent.review.tier_audit.

Decision rule (operator-facing):
  * promote tier rollout to FULL (SYGNIF_TIER_FULL=1) when, in last 14d,
    promoted-cohort avg_R ≥ default-cohort avg_R + 0.30 R AND n_promoted ≥ 50
  * REVERT (SYGNIF_TIER_PROMOTION=0) when, in last 14d,
    promoted-cohort avg_R < default-cohort avg_R - 0.30 R AND n_promoted ≥ 30

Run:
  python3 /opt/sygnif-services/sygnif_tier_audit.py

Wired by sygnif-tier-audit.timer (daily at 02:30 UTC).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from typing import Any

DB_PATH = "/var/lib/sygnif/swarm.db"
WINDOW_S = 14 * 86400          # 14-day rolling window
PROMOTE_TO_FULL_R = 0.30       # Δ R/trade for SYGNIF_TIER_FULL recommendation
PROMOTE_TO_FULL_N = 50
REVERT_R = -0.30
REVERT_N = 30


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def _load_rows(c: sqlite3.Connection, topic: str, since: int) -> list[dict]:
    rows = []
    cur = c.execute(
        "SELECT id, created, content, meta, tags, agent_id "
        "FROM swarm_entries WHERE topic = ? AND created > ? "
        "ORDER BY created", (topic, since))
    for rid, created, content, meta_s, tags_s, agent in cur:
        try:
            meta = json.loads(meta_s) if meta_s else {}
        except json.JSONDecodeError:
            meta = {}
        try:
            tags = json.loads(tags_s) if tags_s else []
        except json.JSONDecodeError:
            tags = []
        rows.append({
            "id": rid, "created": created, "content": content or "",
            "meta": meta, "tags": tags, "agent_id": agent,
        })
    return rows


def _classify_env(meta: dict) -> str:
    env = (meta.get("env") or "").lower()
    if env in ("demo", "live", "paper"):
        return env
    # fallback: if mode was captured on the open
    mode = (meta.get("mode") or "").lower()
    if mode in ("demo", "live", "paper"):
        return mode
    return "unknown"


def _was_promoted(meta: dict) -> tuple[bool, list[str]]:
    """A trade is "promoted" if leverage_tier or size_tier was non-default."""
    promotions = []
    lt = (meta.get("leverage_tier") or "").lower()
    st = (meta.get("size_tier") or "").lower()
    if lt and lt != "default":
        promotions.append(f"leverage_tier={lt}")
    if st and st != "default":
        promotions.append(f"size_tier={st}")
    return bool(promotions), promotions


def _r_per_trade(pnl_usd: float, risk_usd: float) -> float | None:
    if risk_usd is None or risk_usd <= 0:
        return None
    return pnl_usd / risk_usd


def _aggregate(closes: list[dict]) -> dict:
    """Aggregate a list of trade close meta dicts into cohort stats."""
    n = len(closes)
    if n == 0:
        return {"n": 0}
    pnls = []
    rs = []
    wins = 0
    for c in closes:
        meta = c.get("meta", {})
        pnl = meta.get("closed_pnl")
        if pnl is None:
            continue
        try:
            pnl_f = float(pnl)
        except (ValueError, TypeError):
            continue
        pnls.append(pnl_f)
        if pnl_f > 0:
            wins += 1
        # risk — best-effort from meta. Phase 2 decision_snapshot will give
        # us the actual risk_budget; for now use exec_qty * exec_price as
        # proxy notional and assume 1× sizing.
        try:
            risk = float(meta.get("risk_usd") or 0)
            if risk == 0:
                qty = float(meta.get("exec_qty") or 0)
                px = float(meta.get("exec_price") or 0)
                risk = qty * px * 0.01  # 1% stop assumption
            r = _r_per_trade(pnl_f, risk)
            if r is not None:
                rs.append(r)
        except (ValueError, TypeError):
            pass
    return {
        "n":          n,
        "n_pnl":      len(pnls),
        "win_rate":   wins / max(len(pnls), 1),
        "total_pnl":  round(sum(pnls), 4),
        "avg_pnl":    round(sum(pnls) / max(len(pnls), 1), 4),
        "avg_r":      round(sum(rs) / max(len(rs), 1), 3) if rs else None,
        "n_with_r":   len(rs),
    }


def main() -> int:
    if not os.path.exists(DB_PATH):
        print(f"swarm.db not found at {DB_PATH}", file=sys.stderr)
        return 1
    since = int(time.time()) - WINDOW_S
    c = _connect()

    # Load tier_promoted, trade.open, trade.close in window
    promoted = _load_rows(c, "agent.tier_promoted", since)
    opens    = _load_rows(c, "trade.open",          since)
    closes   = _load_rows(c, "trade.close",         since)

    # Bucket closes by env + promoted-flag.
    # A close is "promoted" if its meta.leverage_tier or meta.size_tier set.
    by_env_cohort: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for ev in closes:
        meta = ev.get("meta", {})
        env = _classify_env(meta)
        was_p, _ = _was_promoted(meta)
        cohort = "promoted" if was_p else "default"
        by_env_cohort[(env, cohort)].append(ev)

    summary = {
        "window_days": WINDOW_S // 86400,
        "promoted_events_n": len(promoted),
        "opens_n": len(opens),
        "closes_n": len(closes),
        "cohorts": {},
        "recommendations": [],
    }

    for env in ("demo", "live", "paper", "unknown"):
        prom_closes = by_env_cohort.get((env, "promoted"), [])
        def_closes  = by_env_cohort.get((env, "default"),   [])
        if not prom_closes and not def_closes:
            continue
        prom_agg = _aggregate(prom_closes)
        def_agg  = _aggregate(def_closes)
        delta_r = None
        if (prom_agg.get("avg_r") is not None
            and def_agg.get("avg_r") is not None):
            delta_r = round(prom_agg["avg_r"] - def_agg["avg_r"], 3)
        summary["cohorts"][env] = {
            "promoted": prom_agg,
            "default":  def_agg,
            "delta_r":  delta_r,
        }

        # Recommendations (demo only — never auto-recommend live promotion)
        if env == "demo" and delta_r is not None:
            n_prom = prom_agg.get("n_pnl") or 0
            if delta_r >= PROMOTE_TO_FULL_R and n_prom >= PROMOTE_TO_FULL_N:
                summary["recommendations"].append(
                    f"PROMOTE: SYGNIF_TIER_FULL=1 — demo Δ R/trade "
                    f"{delta_r:+.2f} (≥{PROMOTE_TO_FULL_R:+.2f}) on "
                    f"{n_prom} promoted trades")
            elif delta_r <= REVERT_R and n_prom >= REVERT_N:
                summary["recommendations"].append(
                    f"REVERT: SYGNIF_TIER_PROMOTION=0 — demo Δ R/trade "
                    f"{delta_r:+.2f} (≤{REVERT_R:+.2f}) on "
                    f"{n_prom} promoted trades — promotion is hurting")

    # Print human-readable + emit to swarm
    print(f"=== tier_audit @ {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ===")
    print(f"  window: {summary['window_days']}d")
    print(f"  promoted_events: {summary['promoted_events_n']}")
    print(f"  opens: {summary['opens_n']} closes: {summary['closes_n']}")
    for env, data in summary["cohorts"].items():
        print(f"\n  --- {env} ---")
        for k in ("promoted", "default"):
            a = data[k]
            print(f"    {k}: n={a['n']} (n_pnl={a.get('n_pnl',0)}) "
                  f"win={a.get('win_rate',0):.0%} pnl=${a.get('total_pnl',0):+.2f} "
                  f"avg_R={a.get('avg_r')} (n_with_r={a.get('n_with_r',0)})")
        if data.get("delta_r") is not None:
            print(f"    Δ R/trade (promoted - default): {data['delta_r']:+.3f}")
    if summary["recommendations"]:
        print("\n  >>> RECOMMENDATIONS:")
        for r in summary["recommendations"]:
            print(f"      • {r}")

    # Emit to swarm via direct sqlite (this script may run before sygnif_neurons
    # is on PYTHONPATH; sqlite write is idempotent and atomic).
    try:
        import uuid as _uuid
        rid = str(_uuid.uuid4())
        head = (f"TIER AUDIT {summary['window_days']}d "
                f"promoted_events={summary['promoted_events_n']} "
                f"closes={summary['closes_n']}")
        if summary["recommendations"]:
            head += f" — {summary['recommendations'][0]}"
        wc = sqlite3.connect(DB_PATH, timeout=10)
        wc.execute(
            "INSERT OR IGNORE INTO swarm_entries "
            "(id, created, swarm_id, agent_id, topic, content, meta, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, int(time.time()), "trading", "sygnif-tier-audit",
             "agent.review.tier_audit", head,
             json.dumps(summary, default=str),
             json.dumps(["audit", "tier", "champion-challenger"])))
        wc.commit()
        wc.close()
        print(f"\n  swarm row written: {rid}")
    except Exception as e:
        print(f"\n  swarm write failed: {type(e).__name__}: {e}",
              file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
