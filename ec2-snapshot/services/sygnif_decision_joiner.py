#!/usr/bin/env python3
"""sygnif_decision_joiner.py — Phase 2.3 of autonomous-trader plan.

Joins the (X, y) training pairs the model needs:

  decision.snapshot     X  (full feature snapshot at decision time)
       ↓ via correlation_id
  decision.executed     ← bridges correlation_id to order_link_ids
       ↓ via order_link_id
  outcome.attributed    y  (realized P&L, win/loss, hold, etc.)

Output: /var/lib/sygnif/training_pairs.ndjson  (one row per join, append)
        + per-day rotated /var/lib/sygnif/training_pairs_YYYY-MM-DD.ndjson

Idempotent: skips already-joined correlation_ids (read existing ndjson +
remember seen cids). Coverage report emitted to swarm topic
agent.review.joiner_coverage every run.

Run:
  python3 /opt/sygnif-services/sygnif_decision_joiner.py
Wired by sygnif-decision-joiner.timer (every 60 min).
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import sys
import time
import uuid
from collections import defaultdict
from typing import Any

DB_PATH = "/var/lib/sygnif/swarm.db"
OUT_DIR = pathlib.Path("/var/lib/sygnif")
WINDOW_S = 7 * 86400         # join 7 days at a time (long enough for slow closes)
MAIN_OUT = OUT_DIR / "training_pairs.ndjson"


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def _load(c, topic: str, since: int) -> list[dict]:
    rows = []
    for rid, created, content, meta_s in c.execute(
        "SELECT id, created, content, meta FROM swarm_entries "
        "WHERE topic = ? AND created > ? ORDER BY created", (topic, since)):
        try:
            meta = json.loads(meta_s) if meta_s else {}
        except json.JSONDecodeError:
            meta = {}
        rows.append({"id": rid, "created": created,
                     "content": content or "", "meta": meta})
    return rows


def _seen_cids() -> set:
    """Read existing training_pairs.ndjson, return set of correlation_ids
    already joined. Idempotency."""
    out: set = set()
    if not MAIN_OUT.exists():
        return out
    try:
        with MAIN_OUT.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cid = rec.get("correlation_id")
                if cid:
                    out.add(cid)
    except Exception as e:
        print(f"  warn: could not read {MAIN_OUT}: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
    return out


def _append(rec: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, default=str)
    with MAIN_OUT.open("a") as f:
        f.write(line + "\n")
    # also per-day file (so it's easy to load a recent slice)
    ts = rec.get("ts_decision")
    if isinstance(ts, str):
        # ISO format from snapshot
        day = ts[:10]
    elif isinstance(ts, (int, float)):
        day = time.strftime("%Y-%m-%d", time.gmtime(ts))
    else:
        day = time.strftime("%Y-%m-%d", time.gmtime())
    day_path = OUT_DIR / f"training_pairs_{day}.ndjson"
    with day_path.open("a") as f:
        f.write(line + "\n")


def _flatten_for_training(snapshot_meta: dict, executed_meta: dict,
                            outcome_meta: dict) -> dict:
    """Pick the fields most useful for an LR/XGBoost trainer. Keeps full
    snapshot+executed+outcome under nested keys for richer extracts later."""
    p = snapshot_meta.get("plan") or {}
    cand = (snapshot_meta.get("tier_promotion") or {}).get("candidates") or {}
    fc = snapshot_meta.get("forecast") or {}
    disc = snapshot_meta.get("discovery") or {}
    port = snapshot_meta.get("portfolio") or {}
    wallet = snapshot_meta.get("wallet") or {}
    recent = snapshot_meta.get("recent_outcomes") or {}

    # discovery options block (IV, GEX, etc.) — best-effort
    disc_options = (disc.get("options") or {}) if isinstance(disc, dict) else {}

    flat = {
        "correlation_id":     snapshot_meta.get("correlation_id"),
        "ts_decision":        snapshot_meta.get("ts_utc"),
        "env":                snapshot_meta.get("env"),

        # Plan side
        "action":             p.get("action"),
        "structure":          p.get("structure"),
        "strategy":           p.get("strategy"),
        "instrument":         p.get("instrument"),
        "leverage":           p.get("leverage"),
        "leverage_tier":      p.get("leverage_tier") or "default",
        "size_tier":          p.get("size_tier") or "default",
        "qty":                p.get("qty"),
        "risk_pct":           p.get("risk_pct"),
        "stop_pct":           p.get("stop_pct"),
        "max_loss_usd":       p.get("max_loss_usd"),
        "F":                  p.get("F"),
        "skip_rule":          p.get("rule"),
        "skip_reason":        p.get("reason"),

        # Tier-candidate features
        "predict_strong":     cand.get("predict_strong"),
        "big_recent_move":    cand.get("big_recent_move"),
        "at_psych_barrier":   cand.get("at_psych_barrier"),
        "regime_is_trend":    cand.get("regime_is_trend"),
        "predict_signal":     cand.get("predict_signal"),
        "recent_move_pct":    cand.get("recent_move_pct"),
        "psych_distance_bps": cand.get("psych_distance_bps"),

        # Forecast features
        "fc_signal":          fc.get("signal"),
        "fc_regime":          (fc.get("regime") or {}).get("label") if isinstance(fc.get("regime"), dict) else None,
        "fc_last_price":      fc.get("last_price"),
        "fc_action":          fc.get("action"),

        # Discovery features
        "disc_regime":        disc.get("regime") or disc.get("label"),
        "disc_iv_pct":        disc.get("iv_pct"),
        "disc_atm_iv":        disc.get("atm_iv_nearest")
                              if disc.get("atm_iv_nearest") is not None
                              else disc_options.get("atm_iv_nearest"),
        "disc_atr_pct":       disc.get("atr_pct"),
        "disc_funding_bps":   disc.get("funding_bps"),

        # Portfolio + wallet (multi-asset aware)
        "equity_usd":         wallet.get("total_equity_usd")
                              or port.get("equity_usdc"),
        "available_usd":      wallet.get("available_usd"),
        "open_count":         port.get("open_count"),
        "drawdown_pct":       port.get("drawdown_pct"),
        "n_coins":            len(wallet.get("coins") or []),
        "wallet_coins":       [c.get("coin") for c in (wallet.get("coins") or [])],

        # Recent rolling outcomes
        "recent_n":           recent.get("n_with_pnl"),
        "recent_win_rate":    recent.get("win_rate"),
        "recent_avg_pnl":     recent.get("avg_pnl"),

        # Execution side
        "executed":           executed_meta.get("executed"),
        "exec_mode":          executed_meta.get("mode"),
        "exec_n_legs":        len(executed_meta.get("order_link_ids") or []),
        "exchange_error":     executed_meta.get("exchange_error"),
        "paper_blocked":      executed_meta.get("paper_blocked"),

        # OUTCOME (the y)
        "outcome_pnl_usd":    outcome_meta.get("closed_pnl"),
        "outcome_win":        outcome_meta.get("win"),
        "outcome_settle":     outcome_meta.get("settle_currency"),
        "outcome_hold_s":     outcome_meta.get("hold_seconds"),
        "outcome_symbol":     outcome_meta.get("symbol"),
        "outcome_side":       outcome_meta.get("side"),
        "outcome_pnl_source": outcome_meta.get("closed_pnl_source"),

        # Raw nested for later analysis
        "_snapshot":          snapshot_meta,
        "_executed":          executed_meta,
        "_outcome":           outcome_meta,
    }
    return flat


def main() -> int:
    if not os.path.exists(DB_PATH):
        print(f"swarm.db not found at {DB_PATH}", file=sys.stderr)
        return 1
    since = int(time.time()) - WINDOW_S
    c = _connect()

    snapshots = _load(c, "decision.snapshot", since)
    executed  = _load(c, "decision.executed", since)
    outcomes  = _load(c, "outcome.attributed", since)

    print(f"=== decision_joiner @ "
          f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ===")
    print(f"  window: {WINDOW_S//86400}d")
    print(f"  snapshots: {len(snapshots)}")
    print(f"  executed:  {len(executed)}")
    print(f"  outcomes:  {len(outcomes)}")

    # Index by correlation_id
    snap_by_cid: dict[str, dict] = {}
    for s in snapshots:
        cid = s["meta"].get("correlation_id")
        if cid:
            snap_by_cid[cid] = s["meta"]

    exec_by_cid: dict[str, dict] = {}
    for e in executed:
        cid = e["meta"].get("correlation_id")
        if cid:
            exec_by_cid[cid] = e["meta"]

    # outcomes can be multiple per cid (multi-leg); aggregate
    outcomes_by_cid: dict[str, list[dict]] = defaultdict(list)
    for o in outcomes:
        cid = o["meta"].get("correlation_id")
        if cid:
            outcomes_by_cid[cid].append(o["meta"])

    seen = _seen_cids()
    new_records = 0
    skipped_seen = 0
    skipped_no_outcome = 0

    for cid, snap_meta in snap_by_cid.items():
        if cid in seen:
            skipped_seen += 1
            continue
        exec_meta = exec_by_cid.get(cid, {})
        outs = outcomes_by_cid.get(cid, [])
        if not outs:
            # No outcome yet — leave for next run (might still be open)
            skipped_no_outcome += 1
            continue
        # Aggregate outcomes (sum pnl, take min open_ts, max close_ts)
        if len(outs) == 1:
            agg_outcome = outs[0]
        else:
            sum_pnl = sum(float(o.get("closed_pnl") or 0) for o in outs)
            min_open = min((o.get("open_ts_ms") or 0) for o in outs
                           if o.get("open_ts_ms"))
            max_close = max((o.get("close_ts_ms") or 0) for o in outs
                            if o.get("close_ts_ms"))
            hold_s = round((max_close - min_open) / 1000.0, 1) if (
                min_open and max_close) else None
            agg_outcome = {
                "correlation_id":     cid,
                "env":                outs[0].get("env"),
                "closed_pnl":         round(sum_pnl, 4),
                "win":                sum_pnl > 0,
                "n_legs_closed":      len(outs),
                "hold_seconds":       hold_s,
                "settle_currency":    outs[0].get("settle_currency"),
                "structure":          outs[0].get("structure"),
                "symbol":             outs[0].get("symbol"),
                "_legs":              outs,
            }

        rec = _flatten_for_training(snap_meta, exec_meta, agg_outcome)
        _append(rec)
        new_records += 1

    print(f"  new joined records: {new_records}")
    print(f"  skipped (already joined): {skipped_seen}")
    print(f"  skipped (no outcome yet): {skipped_no_outcome}")
    print(f"  output: {MAIN_OUT}")

    # Coverage report — emit to swarm
    try:
        wc = sqlite3.connect(DB_PATH, timeout=10)
        rid = str(uuid.uuid4())
        coverage = (new_records / len(snap_by_cid)
                    if snap_by_cid else 0)
        head = (f"JOINER {WINDOW_S//86400}d snapshots={len(snap_by_cid)} "
                f"executed={len(exec_by_cid)} outcomes={len(outcomes_by_cid)} "
                f"new_joined={new_records} pending={skipped_no_outcome}")
        wc.execute(
            "INSERT OR IGNORE INTO swarm_entries "
            "(id, created, swarm_id, agent_id, topic, content, meta, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, int(time.time()), "trading", "sygnif-decision-joiner",
             "agent.review.joiner_coverage", head,
             json.dumps({
                 "window_days":   WINDOW_S // 86400,
                 "snapshots":     len(snap_by_cid),
                 "executed":      len(exec_by_cid),
                 "outcomes":      len(outcomes_by_cid),
                 "new_joined":    new_records,
                 "pending_outcome": skipped_no_outcome,
                 "already_seen":  skipped_seen,
                 "coverage_pct":  round(coverage * 100, 1),
                 "main_path":     str(MAIN_OUT),
             }, default=str),
             json.dumps(["joiner", "coverage"])))
        wc.commit()
        wc.close()
    except Exception as e:
        print(f"  swarm coverage write failed: "
              f"{type(e).__name__}: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
