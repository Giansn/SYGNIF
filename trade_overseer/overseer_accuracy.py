"""
Overseer recommendation accuracy analyzer.

Reads the JSONL log written by overseer.log_recommendations and joins each
recommendation against the actual trade outcome (queried from the Freqtrade
sqlite databases). Reports per-recommendation-type accuracy and the cumulative
"would-have-been-useful" delta.

Scoring model
-------------
For each recommendation made on a now-closed trade we compute:
    delta = realized_close_profit_pct - profit_at_eval_pct

CUT recommendation:
    - delta < 0  → CORRECT  (cutting at eval time would have saved -delta pts)
    - delta >= 0 → WRONG    (you would have made +delta by holding)
HOLD recommendation:
    - delta >= 0 → CORRECT  (holding gained delta or was flat)
    - delta < 0  → WRONG    (you would have saved -delta by cutting)
TRAIL recommendation:
    - Treated as HOLD-like for scoring (let-it-run with tighter stop).
    - Considered CORRECT if delta >= 0.

The "saved/lost percentage points" metric is the sum of |delta| only over
recommendations that would have changed the outcome (i.e. CORRECT cuts and
WRONG holds — those are the ones where applying the recommendation matters).

Usage
-----
    python trade_overseer/overseer_accuracy.py
    python trade_overseer/overseer_accuracy.py --days 7
    python trade_overseer/overseer_accuracy.py --by-tag
    python trade_overseer/overseer_accuracy.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

DEFAULT_LOG = Path(__file__).parent / "data" / "overseer_recommendations.jsonl"
DEFAULT_DBS = {
    "spot": Path("user_data/tradesv3.sqlite"),
    "futures": Path("user_data/tradesv3-futures.sqlite"),
}


def load_recommendations(log_path: Path, days: int) -> list[dict]:
    if not log_path.exists():
        return []
    cutoff = None
    if days > 0:
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    out: list[dict] = []
    with log_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if cutoff is not None:
                ts = rec.get("ts", "")
                try:
                    if datetime.fromisoformat(ts).timestamp() < cutoff:
                        continue
                except Exception:
                    pass
            out.append(rec)
    return out


def fetch_closed_trades(db_paths: dict[str, Path]) -> dict[tuple[str, int], dict]:
    """Return {(instance, trade_id): trade_row} for all closed trades found."""
    result: dict[tuple[str, int], dict] = {}
    for instance, path in db_paths.items():
        if not path.exists():
            continue
        try:
            conn = sqlite3.connect(str(path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, pair, enter_tag, exit_reason, close_profit, "
                "close_profit_abs, close_date, open_date, leverage, is_short "
                "FROM trades WHERE is_open=0"
            ).fetchall()
            conn.close()
            for r in rows:
                d = dict(r)
                d["instance"] = instance
                result[(instance, int(d["id"]))] = d
        except Exception as e:
            print(f"warn: failed to read {path}: {e}", file=sys.stderr)
    return result


def score_recommendation(rec: dict, trade: dict) -> dict:
    """Compute correctness + delta for one recommendation against a closed trade."""
    realized_pct = (trade.get("close_profit") or 0.0) * 100
    eval_pct = float(rec.get("profit_at_eval_pct") or 0.0)
    delta = realized_pct - eval_pct
    rtype = (rec.get("recommendation") or "").upper()

    if rtype == "CUT":
        # Correct if outcome was worse than eval-time profit (cutting would have helped)
        correct = delta < 0
        useful_delta = -delta if correct else 0.0  # pts saved
        wasted_delta = delta if not correct else 0.0  # pts foregone
    elif rtype in ("HOLD", "TRAIL"):
        # Correct if outcome held up or improved
        correct = delta >= 0
        useful_delta = delta if correct else 0.0  # pts gained by holding
        wasted_delta = -delta if not correct else 0.0  # pts lost by holding
    else:
        correct = False
        useful_delta = 0.0
        wasted_delta = 0.0

    return {
        "correct": correct,
        "delta_pct": delta,
        "useful_delta_pct": useful_delta,
        "wasted_delta_pct": wasted_delta,
        "realized_pct": realized_pct,
        "eval_pct": eval_pct,
        "rtype": rtype,
        "exit_reason": trade.get("exit_reason"),
    }


def aggregate(scored: Iterable[dict]) -> dict:
    """Group by recommendation type and compute totals."""
    by_type: dict[str, dict] = defaultdict(lambda: {
        "n": 0, "correct": 0, "useful_pts": 0.0, "wasted_pts": 0.0,
    })
    for s in scored:
        b = by_type[s["rtype"]]
        b["n"] += 1
        if s["correct"]:
            b["correct"] += 1
        b["useful_pts"] += s["useful_delta_pct"]
        b["wasted_pts"] += s["wasted_delta_pct"]
    return dict(by_type)


def aggregate_by_tag(joined: list[tuple[dict, dict, dict]]) -> dict:
    """Group by enter_tag → recommendation → counts."""
    by_tag: dict[str, dict[str, dict]] = defaultdict(
        lambda: defaultdict(lambda: {"n": 0, "correct": 0, "useful_pts": 0.0, "wasted_pts": 0.0})
    )
    for rec, trade, score in joined:
        tag = trade.get("enter_tag") or "<none>"
        b = by_tag[tag][score["rtype"]]
        b["n"] += 1
        if score["correct"]:
            b["correct"] += 1
        b["useful_pts"] += score["useful_delta_pct"]
        b["wasted_pts"] += score["wasted_delta_pct"]
    return by_tag


def report_text(args, recs, joined, totals, pending):
    n_total = len(recs)
    n_resolved = len(joined)
    print(f"\nOverseer Accuracy — {DEFAULT_LOG.name}")
    print(f"Window: {'last ' + str(args.days) + 'd' if args.days else 'all-time'}")
    print(f"Recommendations logged: {n_total}  |  resolved (closed trades): {n_resolved}  |  pending (still open): {pending}")
    print()

    if not joined:
        print("No closed-trade recommendations to score yet.\n")
        return

    print(f"{'rec':<8} {'n':>4} {'correct':>9} {'acc%':>7} {'useful_pts':>12} {'wasted_pts':>12} {'net_pts':>10}")
    print("-" * 68)
    for rtype in ("CUT", "HOLD", "TRAIL"):
        b = totals.get(rtype)
        if not b:
            print(f"{rtype:<8} {0:>4} {'-':>9} {'-':>7} {'-':>12} {'-':>12} {'-':>10}")
            continue
        acc = 100.0 * b["correct"] / b["n"] if b["n"] else 0
        net = b["useful_pts"] - b["wasted_pts"]
        print(
            f"{rtype:<8} {b['n']:>4} {b['correct']:>9} {acc:>6.1f}% "
            f"{b['useful_pts']:>+11.2f}% {b['wasted_pts']:>+11.2f}% {net:>+9.2f}%"
        )

    # Notable misses + hits
    by_useful = sorted(joined, key=lambda x: -x[2]["useful_delta_pct"])
    print("\nTop 5 most-useful recommendations (had they been applied):")
    for rec, trade, s in by_useful[:5]:
        if s["useful_delta_pct"] <= 0:
            break
        print(
            f"  {rec['ts'][:16]}  {trade['pair']:<22} {s['rtype']:<6} "
            f"@ {s['eval_pct']:+.2f}% → realized {s['realized_pct']:+.2f}% "
            f"({s['useful_delta_pct']:+.2f}pts saved/gained)"
        )

    by_wasted = sorted(joined, key=lambda x: -x[2]["wasted_delta_pct"])
    print("\nTop 5 worst calls:")
    for rec, trade, s in by_wasted[:5]:
        if s["wasted_delta_pct"] <= 0:
            break
        print(
            f"  {rec['ts'][:16]}  {trade['pair']:<22} {s['rtype']:<6} "
            f"@ {s['eval_pct']:+.2f}% → realized {s['realized_pct']:+.2f}% "
            f"({s['wasted_delta_pct']:+.2f}pts foregone/lost)"
        )

    if args.by_tag:
        print("\nBy entry tag:")
        by_tag = aggregate_by_tag(joined)
        for tag, types in sorted(by_tag.items()):
            for rtype, b in types.items():
                acc = 100.0 * b["correct"] / b["n"] if b["n"] else 0
                net = b["useful_pts"] - b["wasted_pts"]
                print(
                    f"  {tag:<22} {rtype:<6} n={b['n']:<3} acc={acc:>5.1f}% "
                    f"net={net:+.2f}pts"
                )


def report_json(joined, totals, pending):
    out = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "resolved": len(joined),
        "pending": pending,
        "by_type": {
            rtype: {
                "n": b["n"],
                "correct": b["correct"],
                "accuracy_pct": round(100.0 * b["correct"] / b["n"], 2) if b["n"] else 0,
                "useful_pts": round(b["useful_pts"], 3),
                "wasted_pts": round(b["wasted_pts"], 3),
                "net_pts": round(b["useful_pts"] - b["wasted_pts"], 3),
            }
            for rtype, b in totals.items()
        },
    }
    print(json.dumps(out, indent=2, default=str))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--log", default=str(DEFAULT_LOG))
    p.add_argument("--days", type=int, default=0, help="0 = all time")
    p.add_argument("--by-tag", action="store_true", help="break down by enter_tag")
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = p.parse_args()

    log_path = Path(args.log)
    recs = load_recommendations(log_path, args.days)
    if not recs:
        print(f"No recommendations found at {log_path}", file=sys.stderr)
        sys.exit(0 if log_path.exists() else 1)

    closed = fetch_closed_trades(DEFAULT_DBS)

    joined: list[tuple[dict, dict, dict]] = []
    pending = 0
    for rec in recs:
        instance = (rec.get("instance") or "")[:1].lower()
        # The DB stores instance as "spot"/"futures"; the rec has the full word too
        full_inst = "futures" if instance == "f" else "spot"
        tid = rec.get("trade_id")
        if tid is None:
            continue
        trade = closed.get((full_inst, int(tid)))
        if trade is None:
            pending += 1
            continue
        joined.append((rec, trade, score_recommendation(rec, trade)))

    totals = aggregate(s for _, _, s in joined)

    if args.json:
        report_json(joined, totals, pending)
    else:
        report_text(args, recs, joined, totals, pending)


if __name__ == "__main__":
    main()
