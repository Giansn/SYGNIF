#!/usr/bin/env python3
"""sygnif_drift_monitor.py — Phase 3.3 hourly feature-distribution drift check.

Compares feature distributions between two windows from training_pairs.ndjson:
  RECENT      = last 7 days of decisions
  TRAINING    = decisions 7-30 days ago (the "training window")

For each numeric feature, computes:
  • Mean delta (recent - training)
  • Std ratio (recent / training)
  • KL divergence on histogram

Emits warning to swarm topic agent.review.drift_monitor when KL exceeds
gate_params.drift_kl_alert (default 0.20). Severe drift can be wired to
trigger circuit_breaker (Phase 3.5).

Best-effort — logs and exits cleanly when training_pairs has insufficient
data, or features have constant values.

Run:
  python3 /opt/sygnif-services/sygnif_drift_monitor.py
Wired by sygnif-drift-monitor.timer (hourly).
"""
from __future__ import annotations

import json
import math
import os
import pathlib
import sqlite3
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, "/home/ubuntu/sygnif-agent-mirror")
try:
    from agent import gate_params as GP
except Exception:
    GP = None

DB = "/var/lib/sygnif/swarm.db"
TRAINING_PAIRS = pathlib.Path("/var/lib/sygnif/training_pairs.ndjson")

RECENT_DAYS = 7
TRAINING_LOOKBACK_DAYS = 30   # bracket: 7-30d
N_BINS = 10                    # histogram bins for KL
MIN_SAMPLES_PER_WINDOW = 20

NUMERIC_FEATURES = [
    "predict_strong",          # 0/1
    "big_recent_move",         # 0/1
    "at_psych_barrier",        # 0/1
    "regime_is_trend",         # 0/1
    "recent_move_pct",
    "psych_distance_bps",
    "fc_last_price",
    "disc_iv_pct",
    "disc_atm_iv",
    "disc_atr_pct",
    "disc_funding_bps",
    "equity_usd",
    "open_count",
    "drawdown_pct",
    "recent_win_rate",
    "recent_avg_pnl",
    "outcome_pnl_usd",
    "outcome_win",             # 0/1
    "outcome_hold_s",
]


def _to_float(v) -> float | None:
    if v is None or v is False or v is True:
        return float(v) if isinstance(v, bool) else None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _bool_to_float(v) -> float | None:
    if v is None: return None
    if isinstance(v, bool): return 1.0 if v else 0.0
    if isinstance(v, (int, float)): return float(v)
    return None


def load_pairs() -> list[dict]:
    if not TRAINING_PAIRS.exists():
        return []
    out = []
    with TRAINING_PAIRS.open() as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            out.append(r)
    return out


def _ts_seconds(rec: dict) -> float:
    """Decode rec['ts_decision'] to unix seconds."""
    ts = rec.get("ts_decision")
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        try:
            iso = ts.replace("Z", "+00:00")
            return datetime.fromisoformat(iso).timestamp()
        except ValueError:
            return 0
    return 0


def split_windows(pairs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (recent, training) splits."""
    now = time.time()
    recent_cut    = now - RECENT_DAYS * 86400
    training_lo   = now - TRAINING_LOOKBACK_DAYS * 86400
    training_hi   = recent_cut
    recent, training = [], []
    for r in pairs:
        when = _ts_seconds(r)
        if when >= recent_cut:
            recent.append(r)
        elif training_lo <= when < training_hi:
            training.append(r)
    return recent, training


def extract_feature(rows: list[dict], feature: str) -> list[float]:
    """Pull non-null floats for one feature."""
    out = []
    for r in rows:
        v = r.get(feature)
        if isinstance(v, bool):
            out.append(1.0 if v else 0.0)
        else:
            f = _to_float(v)
            if f is not None and not math.isnan(f) and not math.isinf(f):
                out.append(f)
    return out


def basic_stats(values: list[float]) -> dict:
    n = len(values)
    if n == 0:
        return {"n": 0}
    mean = sum(values) / n
    if n > 1:
        var = sum((v - mean) ** 2 for v in values) / (n - 1)
        std = math.sqrt(var)
    else:
        std = 0.0
    return {"n": n, "mean": mean, "std": std,
            "min": min(values), "max": max(values)}


def kl_divergence(a: list[float], b: list[float], n_bins: int = N_BINS) -> float:
    """KL(P_a || P_b) on equal-width histograms over the union range.
    Uses Laplace smoothing so empty bins don't blow up to infinity."""
    if not a or not b:
        return 0.0
    lo = min(min(a), min(b))
    hi = max(max(a), max(b))
    if hi - lo < 1e-9:
        return 0.0
    edges = [lo + (hi - lo) * i / n_bins for i in range(n_bins + 1)]

    def hist(xs):
        h = [0] * n_bins
        for x in xs:
            i = int((x - lo) / (hi - lo) * n_bins)
            if i >= n_bins: i = n_bins - 1
            if i < 0: i = 0
            h[i] += 1
        # Laplace smoothing
        h = [c + 1 for c in h]
        s = sum(h)
        return [c / s for c in h]

    pa, pb = hist(a), hist(b)
    kl = 0.0
    for x, y in zip(pa, pb):
        if x > 0 and y > 0:
            kl += x * math.log(x / y)
    return kl


def main() -> int:
    pairs = load_pairs()
    print(f"=== drift_monitor @ "
          f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ===")
    print(f"  total pairs in store: {len(pairs)}")

    if len(pairs) < MIN_SAMPLES_PER_WINDOW * 2:
        print(f"  insufficient data (<{MIN_SAMPLES_PER_WINDOW*2} pairs); skip")
        _emit_summary({"insufficient_data": True, "n_pairs": len(pairs)})
        return 0

    recent, training = split_windows(pairs)
    print(f"  recent (last {RECENT_DAYS}d): {len(recent)}")
    print(f"  training ({RECENT_DAYS}-{TRAINING_LOOKBACK_DAYS}d ago): "
          f"{len(training)}")

    if (len(recent) < MIN_SAMPLES_PER_WINDOW
        or len(training) < MIN_SAMPLES_PER_WINDOW):
        print(f"  one window is too small (need ≥{MIN_SAMPLES_PER_WINDOW} each); skip")
        _emit_summary({"window_too_small": True,
                        "n_recent": len(recent),
                        "n_training": len(training)})
        return 0

    threshold = float(GP.get("drift_kl_alert", 0.20)) if GP else 0.20
    drifts = {}
    alerts = []
    for feat in NUMERIC_FEATURES:
        a = extract_feature(recent, feat)
        b = extract_feature(training, feat)
        if not a or not b:
            continue
        sa = basic_stats(a)
        sb = basic_stats(b)
        kl = kl_divergence(a, b)
        mean_delta = sa["mean"] - sb["mean"]
        std_ratio = (sa["std"] / sb["std"]) if sb["std"] > 1e-9 else None
        record = {
            "n_recent":    sa["n"],
            "n_training":  sb["n"],
            "mean_recent":  round(sa["mean"], 4),
            "mean_training": round(sb["mean"], 4),
            "mean_delta":  round(mean_delta, 4),
            "std_recent":  round(sa["std"], 4),
            "std_training": round(sb["std"], 4),
            "std_ratio":   round(std_ratio, 3) if std_ratio else None,
            "kl":          round(kl, 4),
            "alert":       kl >= threshold,
        }
        drifts[feat] = record
        if record["alert"]:
            alerts.append(feat)

    n_alerts = len(alerts)
    print(f"\n  features analysed: {len(drifts)}")
    print(f"  alerts (KL ≥ {threshold}): {n_alerts}")
    for feat in alerts:
        d = drifts[feat]
        print(f"    {feat}: KL={d['kl']:.3f} "
              f"mean {d['mean_training']:.3f} → {d['mean_recent']:.3f} "
              f"(Δ {d['mean_delta']:+.3f})")

    payload = {
        "threshold_kl":      threshold,
        "n_recent":          len(recent),
        "n_training":        len(training),
        "features_analyzed": len(drifts),
        "n_alerts":          n_alerts,
        "alerts":            alerts,
        "drifts":            drifts,
    }
    _emit_summary(payload)
    return 0


def _emit_summary(payload: dict) -> None:
    try:
        c = sqlite3.connect(DB, timeout=10)
        rid = str(uuid.uuid4())
        n_alerts = payload.get("n_alerts", 0)
        head = (f"DRIFT MONITOR features={payload.get('features_analyzed','?')} "
                f"alerts={n_alerts}")
        if n_alerts:
            head += " — " + ",".join(payload.get("alerts") or [])[:120]
        elif payload.get("insufficient_data"):
            head = (f"DRIFT MONITOR insufficient data "
                    f"(n_pairs={payload.get('n_pairs')})")
        elif payload.get("window_too_small"):
            head = (f"DRIFT MONITOR window too small "
                    f"(recent={payload.get('n_recent')}, "
                    f"training={payload.get('n_training')})")
        c.execute(
            "INSERT OR IGNORE INTO swarm_entries "
            "(id, created, swarm_id, agent_id, topic, content, meta, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, int(time.time()), "trading", "sygnif-drift-monitor",
             "agent.review.drift_monitor", head,
             json.dumps(payload, default=str),
             json.dumps(["drift", "monitor"]
                        + (["alert"] if n_alerts else []))))
        c.commit()
        c.close()
    except Exception as e:
        print(f"  swarm summary write failed: "
              f"{type(e).__name__}: {e}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
