#!/usr/bin/env python3
"""
Analyze closed trades from Freqtrade SQLite (same schema as trade_overseer tools).

Usage:
  cd /path/to/SYGNIF/finance_agent
  python3 scripts/analyze_closed_trades.py --db ../user_data/tradesv3-futures.sqlite
  python3 scripts/analyze_closed_trades.py --db ../user_data/tradesv3.sqlite --days 30 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from closed_trades_analysis import analyze_closed_trades, format_analysis_text
from closed_trades_reader import fetch_closed_trades


def main() -> None:
    ap = argparse.ArgumentParser(description="Analyze Freqtrade closed trades from SQLite.")
    ap.add_argument(
        "--db",
        action="append",
        dest="dbs",
        default=[],
        help="Path to tradesv3*.sqlite (repeatable for spot + futures)",
    )
    ap.add_argument("--days", type=int, default=0, help="Last N days only (0 = all closed)")
    ap.add_argument("--json", action="store_true", help="Print JSON summary to stdout")
    args = ap.parse_args()

    if not args.dbs:
        print("ERROR: pass at least one --db path", file=sys.stderr)
        sys.exit(2)

    days = args.days if args.days > 0 else None
    all_trades: list = []
    for db in args.dbs:
        p = Path(db)
        if not p.is_file():
            print(f"ERROR: database not found: {p}", file=sys.stderr)
            sys.exit(1)
        rows = fetch_closed_trades(p, days=days)
        all_trades.extend(rows)
        print(f"# {p}: {len(rows)} closed trades", file=sys.stderr)

    summary = analyze_closed_trades(all_trades)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(format_analysis_text(summary))


if __name__ == "__main__":
    main()
