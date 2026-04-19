#!/usr/bin/env python3
"""
Derive ``direction_logistic`` confidence scaling from **recent closed** Freqtrade futures P/L.

Writes ``prediction_agent/live_trading_calibration.json`` for ``run_live_fit`` →
``live_trading_calibration.apply_direction_logistic_calibration``.

Requires Freqtrade REST (same env as ``sygnif_cli``: ``FREQTRADE_API_PASSWORD`` / ``API_PASSWORD``).
Uses **futures** ``sygnif_cli.FT_FUT`` (default ``http://127.0.0.1:8081``). Set ``SYGNIF_CALIB_FT_SPOT=1`` to
calibrate from **spot** instead.

Heuristic (conservative when win rate is poor):

- ``recent_closed_wr`` = wins / N on last **20** closed trades (futures).
- ``direction_logistic_confidence_multiplier`` ≈ clamp( recent_wr / 0.45, 0.25, 1.0 ).
- ``direction_logistic_confidence_cap`` ≈ clamp( 35 + 55 * recent_wr, 35, 85 ).

Cron example (hourly): ``0 * * * * cd ~/SYGNIF && . .venv/bin/activate && python3 scripts/sygnif_update_predict_calibration_from_ft.py``
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import sygnif_cli as sc  # noqa: E402


def _trade_pnl_ratio(t: dict) -> float:
    for k in ("profit_ratio", "close_profit"):
        v = t.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


def main() -> int:
    sc._load_env()
    spot = (os.environ.get("SYGNIF_CALIB_FT_SPOT") or "").strip().lower() in ("1", "true", "yes", "on")
    recent = sc.get_ft_recent_closed(spot=spot, max_closed=20)
    if not recent:
        print("No closed trades from Freqtrade /trades — write skipped.", file=sys.stderr)
        return 1
    n = len(recent)
    wins = sum(1 for t in recent if _trade_pnl_ratio(t) > 0)
    wr = wins / n if n else 0.0
    mult = max(0.25, min(1.0, wr / 0.45)) if wr > 0 else 0.25
    cap = max(35.0, min(85.0, 35.0 + 55.0 * wr))
    out = {
        "schema_version": 1,
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "freqtrade_recent_closed",
        "ft_base": sc.FT_SPOT if spot else sc.FT_FUT,
        "spot_mode": spot,
        "recent_closed_n": n,
        "recent_closed_wr": round(wr, 4),
        "direction_logistic_confidence_multiplier": round(mult, 4),
        "direction_logistic_confidence_cap": round(cap, 2),
    }
    dest = REPO / "prediction_agent" / "live_trading_calibration.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2))
    print(f"Wrote {dest}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
