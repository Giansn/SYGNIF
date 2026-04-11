#!/usr/bin/env python3
"""
Collect MS3 metrics bundle (NT perf + entry families + trading success + closed analysis).

Writes:
  - user_data/market_strategy_3_metrics.json
  - user_data/logs/market_strategy_3_metrics.jsonl

Env:
  MS3_METRICS_WINDOWS  optional, comma-separated ints (default: 7,30)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

from ms3_metrics_feed import (  # noqa: E402
    build_ms3_metrics_bundle,
    summarize_for_console,
    write_ms3_metrics,
)


def main() -> int:
    raw = os.environ.get("MS3_METRICS_WINDOWS", "7,30").strip()
    windows: tuple[int, ...] = tuple(
        int(x.strip()) for x in raw.split(",") if x.strip().isdigit()
    )
    if not windows:
        windows = (7, 30)

    bundle = build_ms3_metrics_bundle(REPO, windows=windows, append_entry_perf_log=True)
    write_ms3_metrics(REPO, bundle)
    print(summarize_for_console(bundle))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
