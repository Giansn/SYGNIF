#!/usr/bin/env python3
"""sygnif_gate_optimizer.py — Phase 3.2 daily gate-threshold optimizer.

Reads /var/lib/sygnif/training_pairs.ndjson (last 30 days), and for each
tunable gate listed below, sweeps the threshold across its bounds, picks
the value maximizing realized R/trade, applies a 10%/day max-change clamp,
and writes the proposal to gate_params_challenger.json.

Promotion to champion (gate_params.json) is operator-only:
  cp gate_params_challenger.json gate_params.json
  systemctl restart sygnif-trader sygnif-bybit-daemon

Safeties:
  * Max change per day = 10% of bounds range. Slow drift, no overcorrection.
  * Min sample size per candidate = 20 trades. Reject thin slices.
  * Demo-only data trains demo gates. Live data trains live gates.
    (Currently demo only — live wallet has $0.01.)
  * Hard bounds enforced — optimizer cannot push past gate_params.bounds.
  * Skips gracefully on insufficient data (logs and exits 0).

Run:
  python3 /opt/sygnif-services/sygnif_gate_optimizer.py            # daily
  python3 /opt/sygnif-services/sygnif_gate_optimizer.py --dry-run  # report only
Wired by sygnif-gate-optimizer.timer (daily 03:00 UTC).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import sqlite3
import sys
import time
import uuid
from typing import Any, Callable

# Hot-path import safety: gate_params lives in agent/. Resolve via absolute.
sys.path.insert(0, "/home/ubuntu/sygnif-agent-mirror")
try:
    from agent import gate_params as GP
except Exception as e:
    print(f"FATAL: cannot import agent.gate_params: {e}", file=sys.stderr)
    sys.exit(1)

DB = "/var/lib/sygnif/swarm.db"
TRAINING_PAIRS = pathlib.Path("/var/lib/sygnif/training_pairs.ndjson")
WINDOW_DAYS = 30
MIN_SAMPLES_PER_CANDIDATE = 20
MAX_DAILY_CHANGE_FRAC = 0.10        # 10% of bounds range per day
N_SWEEP_STEPS = 21                   # candidate thresholds per gate

# ---------------------------------------------------------------------------
# Gate definitions: each gate has a passes(row, threshold) function and a
# direction. The optimizer sweeps thresholds, computes the cohort R/trade
# for trades that "would have passed" at each candidate threshold, and
# picks the value with the best mean R (subject to min samples).
# ---------------------------------------------------------------------------

def _passes_iv_rv_min(row: dict, threshold: float) -> bool | None:
    """For theta_iv_rv_min: a trade passes if its iv_rv at decision time was
    >= threshold AND the trade was a theta-harvest structure (short_iron_condor).
    Returns None if not applicable (no iv_rv data, wrong structure)."""
    structure = row.get("structure") or ""
    if not structure.startswith("short_iron_condor"):
        return None
    # iv_rv lives in snapshot's discovery options
    iv_rv = None
    snap = row.get("_snapshot") or {}
    disc = snap.get("discovery") or {}
    options = (disc.get("options") or {}) if isinstance(disc, dict) else {}
    for k in ("iv_realized_ratio_1h", "iv_rv_ratio"):
        if k in options:
            iv_rv = options[k]; break
        if k in disc:
            iv_rv = disc[k]; break
    if iv_rv is None:
        return None
    try:
        return float(iv_rv) >= threshold
    except (ValueError, TypeError):
        return None


def _passes_perp_min_score(row: dict, threshold: float) -> bool | None:
    """perp_runner total score >= threshold. Skip non-perp rows."""
    if (row.get("instrument") or "") != "perp":
        return None
    score = (row.get("_snapshot", {}).get("plan", {}).get("score")
             or row.get("_snapshot", {}).get("plan", {}).get("total_score"))
    if score is None:
        return None
    try:
        return float(score) >= threshold
    except (ValueError, TypeError):
        return None


def _passes_bounce_move_threshold_pct(row: dict, threshold: float) -> bool | None:
    """For bounce_move_threshold_pct: a trade qualifies at threshold T if the
    bounce setup snapshot was active and its absolute magnitude (|move %|)
    was >= T. Returns None when the trade was not bounce-driven (no active
    bounce in the decision snapshot) so it is excluded from the cohort.

    The other 3 bounce tunables (bounce_ratio, bounce_horizon_min,
    bounce_cooldown_min) are NOT registered here — ratio is post-hoc target
    sizing (not a 'would have passed' gate), and horizon/cooldown shape the
    WS daemon's alert cadence rather than the entry decision. They live in
    gate_params for operator tuning but are not auto-swept.
    """
    snap = row.get("_snapshot") or {}
    bounce = snap.get("bounce") if isinstance(snap, dict) else None
    if not isinstance(bounce, dict):
        return None
    if not bounce.get("active"):
        return None
    mag = bounce.get("magnitude_abs_pct")
    if mag is None:
        return None
    try:
        return float(mag) >= float(threshold)
    except (ValueError, TypeError):
        return None


# Gate registry — name → (passes_fn, default_threshold, env_filter)
GATES: dict[str, dict] = {
    "theta_iv_rv_min": {
        "passes":  _passes_iv_rv_min,
        "filter":  lambda r: (r.get("structure") or "").startswith(
            "short_iron_condor"),
    },
    "perp_min_score": {
        "passes":  _passes_perp_min_score,
        "filter":  lambda r: (r.get("instrument") or "") == "perp",
    },
    # 2026-05-10 — bounce_move_threshold_pct: only sweep trades where the
    # decision snapshot had an active bounce setup. The other 3 bounce
    # tunables (bounce_ratio / bounce_horizon_min / bounce_cooldown_min)
    # are intentionally NOT registered — see _passes_bounce_move_threshold_pct
    # docstring. They remain operator-tunable in gate_params.
    "bounce_move_threshold_pct": {
        "passes":  _passes_bounce_move_threshold_pct,
        "filter":  lambda r: bool(
            ((r.get("_snapshot") or {}).get("bounce") or {}).get("active")),
    },
}


def _r_per_trade(row: dict) -> float | None:
    """R = pnl / risk. Best-effort risk from snapshot.plan.max_loss_usd or
    fallback to pnl alone (R = pnl / 1)."""
    pnl = row.get("outcome_pnl_usd")
    if pnl is None:
        return None
    try:
        pnl_f = float(pnl)
    except (ValueError, TypeError):
        return None
    risk = (row.get("_snapshot", {}).get("plan", {}).get("max_loss_usd")
            or row.get("max_loss_usd"))
    if risk:
        try:
            risk_f = float(risk)
            if risk_f > 0:
                return pnl_f / risk_f
        except (ValueError, TypeError):
            pass
    # Fallback: $1 per R (so R = pnl in dollars). Crude but consistent.
    return pnl_f


def load_training_pairs(env: str = "demo", days: int = WINDOW_DAYS) -> list[dict]:
    """Read training_pairs.ndjson, filter to env + window."""
    if not TRAINING_PAIRS.exists():
        return []
    cutoff = time.time() - days * 86400
    rows = []
    with TRAINING_PAIRS.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("env") != env:
                continue
            ts = r.get("ts_decision") or ""
            try:
                ts_iso = ts.replace("Z", "+00:00") if isinstance(ts, str) else ""
                from datetime import datetime as _dt
                if isinstance(ts, str):
                    when = _dt.fromisoformat(ts_iso).timestamp()
                else:
                    when = float(ts)
                if when < cutoff:
                    continue
            except (ValueError, TypeError):
                pass
            rows.append(r)
    return rows


def sweep_gate(rows: list[dict], gate_name: str, gate_def: dict,
                bounds: tuple) -> dict:
    """Sweep candidate thresholds, pick best R/trade subject to min samples."""
    lo, hi = bounds
    if isinstance(lo, int) and isinstance(hi, int):
        candidates = list(range(lo, hi + 1))
    else:
        if N_SWEEP_STEPS <= 1:
            candidates = [(lo + hi) / 2]
        else:
            step = (hi - lo) / (N_SWEEP_STEPS - 1)
            candidates = [lo + step * i for i in range(N_SWEEP_STEPS)]
    pre_filter = gate_def["filter"]
    relevant = [r for r in rows if pre_filter(r)]

    by_candidate = {}
    for v in candidates:
        passed = []
        for r in relevant:
            ok = gate_def["passes"](r, v)
            if ok is None:
                continue   # not applicable — exclude
            if not ok:
                continue   # filtered out
            r_pnl = _r_per_trade(r)
            if r_pnl is None:
                continue
            passed.append(r_pnl)
        if len(passed) < MIN_SAMPLES_PER_CANDIDATE:
            continue
        avg_r = sum(passed) / len(passed)
        wins = sum(1 for r in passed if r > 0)
        by_candidate[v] = {
            "n":         len(passed),
            "avg_r":     round(avg_r, 4),
            "win_rate":  round(wins / len(passed), 3),
        }
    if not by_candidate:
        return {
            "n_relevant":  len(relevant),
            "n_candidates_with_data": 0,
            "best_value":  None,
            "best_avg_r":  None,
            "by_candidate": {},
        }
    best_v = max(by_candidate, key=lambda v: by_candidate[v]["avg_r"])
    return {
        "n_relevant":             len(relevant),
        "n_candidates_with_data": len(by_candidate),
        "best_value":             best_v,
        "best_avg_r":             by_candidate[best_v]["avg_r"],
        "by_candidate":           by_candidate,
    }


def clamp_change(current: float, proposed: float, bounds: tuple) -> float:
    """Limit single-day change to MAX_DAILY_CHANGE_FRAC of bounds range."""
    lo, hi = bounds
    max_step = (hi - lo) * MAX_DAILY_CHANGE_FRAC
    if proposed > current + max_step:
        return current + max_step
    if proposed < current - max_step:
        return current - max_step
    return proposed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report proposals without writing challenger file")
    ap.add_argument("--env", default="demo",
                    help="filter training pairs by env (default demo)")
    args = ap.parse_args()

    rows = load_training_pairs(env=args.env)
    print(f"=== gate_optimizer @ "
          f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ===")
    print(f"  env: {args.env}  window: {WINDOW_DAYS}d")
    print(f"  rows loaded: {len(rows)}")

    if len(rows) < MIN_SAMPLES_PER_CANDIDATE * 2:
        print(f"  insufficient data (<{MIN_SAMPLES_PER_CANDIDATE*2} rows) — "
              "no proposals this run")
        _emit_swarm_summary({"insufficient_data": True,
                             "rows_loaded": len(rows)},
                            env=args.env)
        return 0

    proposals = {}
    for gate_name, gate_def in GATES.items():
        bounds = GP.get_bounds(gate_name)
        if bounds is None:
            continue
        current = GP.get(gate_name)
        try:
            current_f = float(current)
        except (ValueError, TypeError):
            continue
        result = sweep_gate(rows, gate_name, gate_def, bounds)
        print(f"\n  --- {gate_name} ---")
        print(f"    current:    {current_f}")
        print(f"    bounds:     {bounds}")
        print(f"    n_relevant: {result['n_relevant']}")
        if result["best_value"] is None:
            print(f"    no candidate had >= "
                  f"{MIN_SAMPLES_PER_CANDIDATE} samples; skip")
            continue
        proposed_raw = float(result["best_value"])
        clamped = clamp_change(current_f, proposed_raw, bounds)
        # Round to a sensible precision based on bounds range
        precision = max(2, int(-math.log10((bounds[1] - bounds[0]) / 100)))
        clamped = round(clamped, precision)
        print(f"    best_avg_r: {result['best_avg_r']}")
        print(f"    proposed:   {proposed_raw} → clamped: {clamped}")
        if clamped != current_f:
            proposals[gate_name] = {
                "current": current_f,
                "proposed": clamped,
                "best_avg_r": result["best_avg_r"],
                "n_relevant": result["n_relevant"],
            }

    print(f"\n  proposals: {len(proposals)} gate(s) would change")

    if proposals and not args.dry_run:
        # Apply each to challenger
        for gate_name, info in proposals.items():
            try:
                GP.set_param(gate_name, info["proposed"], file="challenger",
                              reason=(f"optimizer: best_avg_r={info['best_avg_r']} "
                                      f"on {info['n_relevant']} samples "
                                      f"({WINDOW_DAYS}d {args.env})"),
                              actor="sygnif-gate-optimizer")
                print(f"    challenger updated: {gate_name} → {info['proposed']}")
            except ValueError as e:
                print(f"    BOUNDS REJECT {gate_name}: {e}")

    _emit_swarm_summary({"rows_loaded": len(rows),
                         "proposals": proposals,
                         "dry_run": args.dry_run}, env=args.env)
    return 0


def _emit_swarm_summary(payload: dict, env: str) -> None:
    """Write summary swarm row to agent.review.gate_optimizer."""
    try:
        c = sqlite3.connect(DB, timeout=10)
        rid = str(uuid.uuid4())
        n_props = len(payload.get("proposals") or {})
        head = (f"GATE OPTIMIZER {env} {WINDOW_DAYS}d "
                f"rows={payload.get('rows_loaded',0)} "
                f"proposals={n_props}")
        if n_props:
            head += " — " + ", ".join(payload["proposals"].keys())
        c.execute(
            "INSERT OR IGNORE INTO swarm_entries "
            "(id, created, swarm_id, agent_id, topic, content, meta, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, int(time.time()), "trading", "sygnif-gate-optimizer",
             "agent.review.gate_optimizer", head,
             json.dumps(payload, default=str),
             json.dumps(["optimizer", "gate", env])))
        c.commit()
        c.close()
    except Exception as e:
        print(f"  swarm summary write failed: "
              f"{type(e).__name__}: {e}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
