#!/usr/bin/env python3
"""
Resolve **pending** BTC forecast rows against the **next unseen 5m bar** and print a summary report.

Each live ``fit_predict_live`` (via ``run_live_fit``) may append one line to the pending JSONL when
``SYGNIF_PREDICT_EVAL_LOG`` is truthy. This script fetches fresh Bybit 5m klines, labels the first bar strictly
after ``last_candle_utc``, and appends outcome rows — metrics are true **out-of-sample** relative to the fit time.

Examples:
  python3 scripts/evaluate_btc_forecast_outcomes.py
  python3 scripts/evaluate_btc_forecast_outcomes.py --symbol BTCUSDT --max 5000
  python3 scripts/evaluate_btc_forecast_outcomes.py --no-report
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_PA = _REPO / "prediction_agent"
sys.path.insert(0, str(_PA))

from btc_forecast_eval import aggregate_report  # noqa: E402
from btc_forecast_eval import process_pending_outcomes  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Resolve pending BTC forecast eval rows vs next 5m bar.")
    p.add_argument("--symbol", default="", help="Only process this symbol (e.g. BTCUSDT). Default: all.")
    p.add_argument("--max", type=int, default=2000, help="Max pending tail lines to scan (default 2000).")
    p.add_argument("--fetch-limit", type=int, default=400, help="Kline limit per symbol when fetching (default 400).")
    p.add_argument("--no-report", action="store_true", help="Skip printing aggregate_report JSON.")
    args = p.parse_args()

    sym = (args.symbol or "").strip() or None
    summary = process_pending_outcomes(symbol=sym, max_rows=max(1, args.max), fetch_limit=max(50, args.fetch_limit))
    print(json.dumps(summary, indent=2, default=str))
    if not args.no_report:
        rep = aggregate_report()
        print(json.dumps(rep, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
