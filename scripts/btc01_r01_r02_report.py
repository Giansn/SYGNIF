#!/usr/bin/env python3
"""
Join recent BTC_Strategy_0.1 trades (R01/R02/R03) with live R01 governance inputs.

- Trades: ``user_data/tradesv3-btc01-bybit-demo.sqlite`` (freqtrade-btc-0-1)
- R01 gate inputs: ``prediction_agent/training_channel_output.json``
- Tunables: ``letscrash/btc_strategy_0_1_rule_registry.json`` → ``tuning``

No secrets; read-only. Run on host:

  cd ~/SYGNIF && python3 scripts/btc01_r01_r02_report.py
  python3 scripts/btc01_r01_r02_report.py --trades 25
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description="BTC 0.1 R01/R02 report")
    ap.add_argument("--trades", type=int, default=20, help="max recent rule-tagged trades")
    args = ap.parse_args()

    root = _root()
    db = root / "user_data" / "tradesv3-btc01-bybit-demo.sqlite"
    tc = root / "prediction_agent" / "training_channel_output.json"
    reg = root / "letscrash" / "btc_strategy_0_1_rule_registry.json"

    if not db.is_file():
        print(f"Missing DB: {db}", file=sys.stderr)
        return 1

    tuning: dict = {}
    try:
        tuning = (json.loads(reg.read_text(encoding="utf-8")).get("tuning") or {})
    except OSError:
        tuning = {}
    r01g = tuning.get("r01_governance") or {}
    r02g = tuning.get("r02_regime") or {}

    print("=== Registry tuning (R01 / R02) ===")
    print(f"  R01 p_down_min_pct     = {r01g.get('p_down_min_pct', 90.0)}")
    print(f"  R01 consensus equals   = {r01g.get('runner_consensus_equals', 'BEARISH')!r}")
    print(f"  R02 rsi_bull_min       = {r02g.get('rsi_bull_min', 50.0)}")
    print(f"  R02 adx_min            = {r02g.get('adx_min', 25.0)}")
    print()

    if tc.is_file():
        try:
            doc = json.loads(tc.read_text(encoding="utf-8"))
            rec = doc.get("recognition") or {}
            p_down = rec.get("last_bar_probability_down_pct")
            snap = doc.get("btc_predict_runner_snapshot") or {}
            pred = snap.get("predictions") or {}
            cons = pred.get("consensus", "--")
            print("=== training_channel_output.json (R01 inputs) ===")
            print(f"  last_bar_probability_down_pct = {p_down}")
            print(f"  runner consensus            = {cons}")
            print()
        except (OSError, json.JSONDecodeError) as e:
            print(f"(could not read training_channel: {e})\n")
    else:
        print(f"(no {tc.name} — R01 governance uses empty doc)\n")

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    q = """
    SELECT id, is_open, pair, enter_tag, exit_reason,
           open_date, close_date,
           close_profit, stake_amount, leverage
    FROM trades
    WHERE enter_tag LIKE 'BTC-0.1-R%'
    ORDER BY id DESC
    LIMIT ?
    """
    rows = conn.execute(q, (args.trades,)).fetchall()
    conn.close()

    print(f"=== Last {len(rows)} trades (tags BTC-0.1-R*) ===")
    if not rows:
        print("  (none)")
        return 0
    for r in rows:
        op = "OPEN " if r["is_open"] else "closed"
        cr = r["close_profit"]
        crs = f"{100 * float(cr):+.2f}%" if cr is not None and not r["is_open"] else "--"
        ex = (r["exit_reason"] or "")[:40]
        print(
            f"  id={r['id']:<3} {op:<6} tag={r['enter_tag']!s:<14} "
            f"P/L%={crs:<8} lev={r['leverage'] or '--'} "
            f"exit={ex!r}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
