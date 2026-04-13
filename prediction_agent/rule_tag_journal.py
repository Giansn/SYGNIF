#!/usr/bin/env python3
"""
Append-only CSV journal for /ruleprediction-agent (BTC-0.1-R* evidence).

Default path: prediction_agent/rule_tag_journal.csv
Override: RULE_TAG_JOURNAL_PATH

Events:
  - channel_training: each successful training_channel_output.json write
  - horizon_save: optional when prediction_horizon_check.py save + RULE_TAG_JOURNAL=1
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

_JOURNAL_COLUMNS = (
    "event_utc",
    "event",
    "rule_tag",
    "detail_json",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def journal_path() -> Path:
    env = (os.environ.get("RULE_TAG_JOURNAL_PATH") or "").strip()
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parent / "rule_tag_journal.csv"


def append_event(
    event: str,
    *,
    rule_tag: str = "",
    detail: dict | None = None,
) -> Path | None:
    """Append one CSV row. Returns path if written, None if disabled."""
    if os.environ.get("RULE_TAG_JOURNAL_DISABLE", "").lower() in ("1", "true", "yes"):
        return None
    p = journal_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    new_file = not p.exists()
    row = {
        "event_utc": _utc_now(),
        "event": event,
        "rule_tag": rule_tag or "",
        "detail_json": json.dumps(detail or {}, separators=(",", ":"), ensure_ascii=False)[:4000],
    }
    with p.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_JOURNAL_COLUMNS)
        if new_file:
            w.writeheader()
        w.writerow(row)
    return p


def append_channel_training_event(
    out_path: Path,
    payload: dict,
    *,
    alignment: dict | None = None,
) -> Path | None:
    detail = {
        "training_channel_out": str(out_path),
        "generated_utc": payload.get("generated_utc"),
        "alignment": alignment or payload.get("predict_runner_alignment"),
        "r01": (payload.get("ruleprediction_briefing") or {}).get("r01"),
        "r02": (payload.get("ruleprediction_briefing") or {}).get("r02"),
    }
    return append_event("channel_training", rule_tag="", detail=detail)


def append_horizon_save_event(
    *,
    symbol: str,
    snapshot_path: Path,
    note: str,
    rule_tag: str,
) -> Path | None:
    return append_event(
        "horizon_save",
        rule_tag=rule_tag,
        detail={
            "symbol": symbol,
            "snapshot_path": str(snapshot_path),
            "note": note or "",
        },
    )
