#!/usr/bin/env python3
"""
Framework hooks for BTC dump / run handling — research only, not live orders.

Maps Sygnif-style regime labels to **suggested** stance for alt strategies on **spot**
(small notional, e.g. dry_run_wallet 100 USDT): prioritize capital preservation on
`risk_off`, avoid new shorts on `bull` / `pump_guard` (futures mental model).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Import sibling module
sys.path.insert(0, str(Path(__file__).resolve().parent))
from btc_regime_assessment import (  # noqa: E402
    BtcRegime,
    _load_ohlcv_local,
    assess_from_candles,
)


@dataclass
class Stance:
    new_alt_longs: str  # full | reduced | avoid
    hold_existing_longs: str  # monitor | tighten | consider_flat
    notes: str


def stance_for_regime(r: BtcRegime) -> Stance:
    if r.label == "risk_off":
        return Stance(
            new_alt_longs="avoid",
            hold_existing_longs="consider_flat",
            notes="Matches custom_exit exit_btc_risk_off zone — prioritize BTC spill control.",
        )
    if r.label == "bull":
        return Stance(
            new_alt_longs="reduced",
            hold_existing_longs="monitor",
            notes="BTC 4h strong — shorts structurally blocked in Sygnif; longs compete with BTC beta.",
        )
    if r.label == "pump_guard":
        return Stance(
            new_alt_longs="reduced",
            hold_existing_longs="monitor",
            notes="Hot 1h micro-RSI — mean-reversion longs on alts risk chop.",
        )
    return Stance(
        new_alt_longs="full",
        hold_existing_longs="monitor",
        notes="Neutral tape — use pair-level TA + slot caps; no extra BTC overlay.",
    )


def main() -> int:
    lab = Path(os.environ.get("LAB_ROOT", "/lab"))
    if not lab.is_dir():
        lab = Path(__file__).resolve().parents[2]
    candles = _load_ohlcv_local(lab)
    if not candles:
        print(json.dumps({"ok": False, "error": "no ohlcv"}))
        return 1
    notional = float(os.environ.get("SYGNIF_SPOT_NOTIONAL_USDT", "100"))
    r = assess_from_candles(candles, notional)
    s = stance_for_regime(r)
    print(
        json.dumps(
            {
                "ok": True,
                "regime": r.label,
                "stance": s.__dict__,
                "max_risk_per_trade_pct_wallet": min(2.5, 15.0 / max(notional, 1e-6)),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
