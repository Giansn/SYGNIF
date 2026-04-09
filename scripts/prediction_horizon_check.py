#!/usr/bin/env python3
"""
Save a TA snapshot and compare later (e.g. +24h / +48h UTC) against Bybit spot.

Example:
  python3 prediction_horizon_check.py save --symbol DOGE
  python3 prediction_horizon_check.py check
  python3 prediction_horizon_check.py check --snapshot ~/.local/share/sygnif-agent/predictions/DOGE_latest.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BYBIT = "https://api.bybit.com/v5/market"
DATA_DIR = Path.home() / ".local/share/sygnif-agent/predictions"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _spot_price(symbol: str) -> float:
    sym = f"{symbol.upper()}USDT"
    r = requests.get(f"{BYBIT}/tickers", params={"category": "spot", "symbol": sym}, timeout=15)
    r.raise_for_status()
    lst = (r.json().get("result") or {}).get("list") or []
    if not lst:
        raise RuntimeError(f"No ticker for {sym}")
    return float(lst[0]["lastPrice"])


def _levels_from_bot(symbol: str) -> dict:
    """Use finance_agent indicators when available."""
    root = Path(__file__).resolve().parents[1]
    fa = root.parent / "finance_agent"
    sys.path.insert(0, str(fa))
    from bot import bybit_kline, calc_indicators  # type: ignore

    df = bybit_kline(f"{symbol.upper()}USDT", "60", 200)
    if df.empty or not calc_indicators(df):
        raise RuntimeError("No indicator data")
    ind = calc_indicators(df)
    return {
        "support": round(float(ind["support"]), 6),
        "resistance": round(float(ind["resistance"]), 6),
    }


def cmd_save(args: argparse.Namespace) -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    sym = args.symbol.upper()
    try:
        lv = _levels_from_bot(sym)
    except Exception as e:
        print(f"bot levels failed ({e}), using CLI overrides or abort")
        if args.support is None or args.resistance is None:
            print("Pass --support and --resistance or fix finance_agent import.")
            return 1
        lv = {"support": args.support, "resistance": args.resistance}

    price = _spot_price(sym)
    ts = _utc_now()
    t0 = ts.replace(microsecond=0)
    snap = {
        "symbol": sym,
        "created_utc": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "spot_usd": price,
        "levels": lv,
        "horizons": {
            "check_24h_utc": (t0 + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "check_48h_utc": (t0 + timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "thresholds": {
            "range_ok_low": lv["support"],
            "range_ok_high": lv["resistance"],
            "break_down": lv["support"],
            "break_up": lv["resistance"],
        },
        "note": args.note or "",
    }
    stem = ts.strftime("%Y%m%d_%H%M%SZ")
    path = DATA_DIR / f"{sym}_{stem}.json"
    path.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    latest = DATA_DIR / f"{sym}_latest.json"
    latest.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    print(f"Saved {path}")
    print(f"Latest -> {latest}")
    print(f"Suggested checks (UTC): 24h @ {snap['horizons']['check_24h_utc']}")
    print(f"                        48h @ {snap['horizons']['check_48h_utc']}")
    return 0


def _verdict(price: float, snap: dict) -> str:
    th = snap.get("thresholds") or {}
    lo = float(th.get("range_ok_low", snap["levels"]["support"]))
    hi = float(th.get("range_ok_high", snap["levels"]["resistance"]))
    bd = float(th.get("break_down", lo))
    bu = float(th.get("break_up", hi))
    if price <= bd:
        return "DOWN_BREAK — unter Support / Range unten (Thesis geschwächt)"
    if price >= bu:
        return "UP_BREAK — über Resistance (Range nach oben aufgelöst)"
    return "IN_RANGE — zwischen Support und Resistance (Range-Thesis ok)"


def cmd_check(args: argparse.Namespace) -> int:
    path = Path(args.snapshot or DATA_DIR / f"{args.symbol.upper()}_latest.json")
    if not path.exists():
        print(f"No snapshot: {path} — run `save` first.")
        return 1
    snap = json.loads(path.read_text(encoding="utf-8"))
    sym = snap["symbol"]
    old = float(snap["spot_usd"])
    now_p = _spot_price(sym)
    created = snap.get("created_utc", "?")
    v = _verdict(now_p, snap)
    ch = (now_p / old - 1.0) * 100.0
    print(f"Symbol: {sym}")
    print(f"Snapshot: {created}  spot_was=${old:.6g}")
    print(f"Now ({_utc_now().strftime('%Y-%m-%d %H:%M:%S')} UTC): spot=${now_p:.6g}  ({ch:+.2f}% vs snapshot)")
    print(f"Verdict: {v}")
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    if not DATA_DIR.exists():
        print("No predictions dir yet.")
        return 0
    for p in sorted(DATA_DIR.glob("*.json")):
        print(p)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Prediction horizon check (Bybit spot)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("save", help="Write snapshot (+ DOGE_latest.json)")
    s.add_argument("--symbol", default="DOGE")
    s.add_argument("--support", type=float, default=None)
    s.add_argument("--resistance", type=float, default=None)
    s.add_argument("--note", default="")
    s.set_defaults(func=cmd_save)

    c = sub.add_parser("check", help="Compare latest (or --snapshot) to spot now")
    c.add_argument("--symbol", default="DOGE")
    c.add_argument("--snapshot", default=None)
    c.set_defaults(func=cmd_check)

    l = sub.add_parser("list", help="List saved JSON files")
    l.set_defaults(func=cmd_list)

    args = p.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
