#!/usr/bin/env python3
"""Fetch all crypto-market-data README daily JSONs + write analysis markdown (CC BY 4.0).

Intended for **once-per-day** cron (e.g. `0 6 * * *`) beside or instead of full
`pull_btc_context.py`. Consumed by `/btc`, `/btc-specialist`, `/finance-agent crypto-daily`,
and `build_btc_specialist_report` when files exist.

  python3 finance_agent/btc_specialist/scripts/run_crypto_market_data_daily.py
"""
from __future__ import annotations

import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data"
FINANCE_AGENT_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    sys.path.insert(0, str(FINANCE_AGENT_DIR))
    try:
        from crypto_market_data import (
            ALL_README_DAILY_PATHS,
            build_daily_analysis_markdown,
            fetch_remote_bundle,
            write_bundle_json,
        )
    except Exception as e:
        print("import crypto_market_data:", e, file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    bundle = fetch_remote_bundle(paths=ALL_README_DAILY_PATHS, timeout_per=15.0)
    ds = bundle.get("datasets")
    if not isinstance(ds, dict) or not any(ds.values()):
        print("No datasets fetched", file=sys.stderr)
        return 2
    write_bundle_json(OUT, bundle)
    (OUT / "crypto_market_data_daily_analysis.md").write_text(
        build_daily_analysis_markdown(bundle),
        encoding="utf-8",
    )
    print(f"Wrote {OUT / 'btc_crypto_market_data.json'} + crypto_market_data_daily_analysis.md")
    print(f"Paths: {len(ALL_README_DAILY_PATHS)} (README daily list)")
    _ = REPO_ROOT  # reserved for future env checks
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
