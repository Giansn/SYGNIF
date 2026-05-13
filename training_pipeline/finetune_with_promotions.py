#!/usr/bin/env python3
"""
Consume challenger_promotions.jsonl (produced by workflows/gate_loop) and emit
a finetune signal that channel_training.py can pick up.

Outputs
-------
prediction_agent/promotion_signal.json
    Aggregate stats over the recent window:
      - recent_promote_rate    (last 7d unless --window-days)
      - recent_avg_delta_pnl_pct
      - recent_reject_rate
      - last_promote_utc
      - confidence_modifier    (∈ [0.5, 1.5], 1.0 = neutral)
      - n_promote / n_reject / n_abstain
      - source ledger path + appended_utc range

prediction_agent/promotion_consumed_offset.json
    Idempotency marker: byte offset into the ledger that was last consumed.
    Lets us skip already-processed rows on subsequent ticks (the ledger is
    append-only, so byte offset is stable).

Usage
-----
    python3 training_pipeline/finetune_with_promotions.py [--window-days 7]
    python3 training_pipeline/finetune_with_promotions.py --json    # dump signal to stdout

Hook in sygnif_finetune_automation.sh: invoke this BEFORE channel_training.py
so the signal file is fresh when channel_training reads it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PA = REPO_ROOT / "prediction_agent"
LEDGER = PA / "challenger_promotions.jsonl"
SIGNAL = PA / "promotion_signal.json"
OFFSET = PA / "promotion_consumed_offset.json"


def _parse_iso(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _read_ledger() -> list[dict[str, Any]]:
    if not LEDGER.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with LEDGER.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _confidence_modifier(promote_rate: float, n_total: int) -> float:
    """Map recent promote-rate to a [0.5, 1.5] multiplier.

    More recent promotions (signal is improving) → higher modifier.
    More recent rejects (challenger keeps failing) → lower modifier.
    Until we have enough samples (n_total ≥ 5), stay neutral (1.0).
    """
    if n_total < 5:
        return 1.0
    # Map promote_rate ∈ [0, 1] → multiplier ∈ [0.5, 1.5] linearly around 0.5.
    return round(max(0.5, min(1.5, 0.5 + promote_rate)), 3)


def compute_signal(window_days: int = 7) -> dict[str, Any]:
    rows = _read_ledger()
    if not rows:
        return {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "window_days": window_days,
            "n_promote": 0,
            "n_reject": 0,
            "n_abstain": 0,
            "recent_promote_rate": None,
            "recent_avg_delta_pnl_pct": None,
            "recent_reject_rate": None,
            "last_promote_utc": None,
            "confidence_modifier": 1.0,
            "ledger_path": str(LEDGER),
            "ledger_exists": False,
        }

    now_ts = datetime.now(timezone.utc).timestamp()
    cutoff = now_ts - window_days * 86400

    recent = [
        r for r in rows
        if (_parse_iso(r.get("appended_utc")) or 0) >= cutoff
    ]

    n_promote = sum(1 for r in recent if r.get("verdict") == "promote")
    n_reject = sum(1 for r in recent if r.get("verdict") == "reject")
    n_abstain = sum(1 for r in recent if r.get("verdict") == "abstain")
    n_total = n_promote + n_reject + n_abstain

    promote_pnl = [
        float(r.get("delta_pnl_pct") or 0.0)
        for r in recent
        if r.get("verdict") == "promote"
    ]
    avg_delta_pnl = (sum(promote_pnl) / len(promote_pnl)) if promote_pnl else None

    promote_rate = (n_promote / n_total) if n_total else 0.0
    reject_rate = (n_reject / n_total) if n_total else 0.0

    last_promote_ts = None
    for r in reversed(rows):
        if r.get("verdict") == "promote":
            last_promote_ts = r.get("appended_utc")
            break

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "window_days": window_days,
        "n_promote": n_promote,
        "n_reject": n_reject,
        "n_abstain": n_abstain,
        "recent_promote_rate": round(promote_rate, 3),
        "recent_reject_rate": round(reject_rate, 3),
        "recent_avg_delta_pnl_pct": (
            round(avg_delta_pnl, 3) if avg_delta_pnl is not None else None
        ),
        "last_promote_utc": last_promote_ts,
        "confidence_modifier": _confidence_modifier(promote_rate, n_total),
        "ledger_path": str(LEDGER),
        "ledger_exists": True,
        "ledger_rows_total": len(rows),
        "ledger_rows_in_window": len(recent),
    }


def write_signal(signal: dict[str, Any]) -> None:
    SIGNAL.parent.mkdir(parents=True, exist_ok=True)
    SIGNAL.write_text(json.dumps(signal, indent=2, sort_keys=True), encoding="utf-8")

    offset = {
        "consumed_utc": datetime.now(timezone.utc).isoformat(),
        "ledger_bytes": LEDGER.stat().st_size if LEDGER.is_file() else 0,
        "ledger_rows": signal.get("ledger_rows_total", 0),
    }
    OFFSET.write_text(json.dumps(offset, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1] if __doc__ else None)
    parser.add_argument("--window-days", type=int, default=7,
                        help="Promotion lookback window in days (default 7)")
    parser.add_argument("--json", action="store_true",
                        help="Also dump the signal to stdout")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute but don't write signal/offset files")
    args = parser.parse_args()

    signal = compute_signal(window_days=args.window_days)

    if not args.dry_run:
        write_signal(signal)
        print(
            f"[finetune_with_promotions] wrote {SIGNAL.relative_to(REPO_ROOT)} "
            f"(n_promote={signal['n_promote']}, n_reject={signal['n_reject']}, "
            f"confidence_modifier={signal['confidence_modifier']})",
            file=sys.stderr,
        )

    if args.json:
        print(json.dumps(signal, sort_keys=True))

    return 0


if __name__ == "__main__":
    sys.exit(main())
