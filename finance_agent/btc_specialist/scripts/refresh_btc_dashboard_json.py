#!/usr/bin/env python3
"""Regenerate `btc_specialist_dashboard.json` (finance-agent KB + Cursor Cloud LLM when configured).

Use after `run_crypto_market_data_daily.py` or a full `pull_btc_context.py` so
`crypto_market_data_daily_analysis.md` exists. Reads `CRYPTO_CONTEXT_LLM` and
`CURSOR_*` from the same `.env` chain as `pull_btc_context.py`.

  python3 finance_agent/btc_specialist/scripts/refresh_btc_dashboard_json.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_BTC_SPEC = _SCRIPTS.parent
OUT = _BTC_SPEC / "data"


def main() -> int:
    sys.path.insert(0, str(_SCRIPTS))
    import pull_btc_context as pbc  # noqa: PLC0415

    pbc._load_repo_env()
    sys.path.insert(0, str(_BTC_SPEC))
    from report import write_btc_specialist_dashboard_json  # noqa: PLC0415

    utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_btc_specialist_dashboard_json(OUT, utc)
    print(f"Wrote {OUT / 'btc_specialist_dashboard.json'} (UTC {utc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
