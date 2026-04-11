#!/usr/bin/env python3
"""
Save a TA snapshot and compare later (e.g. +24h / +48h UTC) against Bybit spot.

Levels + optional movement-probability metrics from historical forward returns
on the same kline interval as the snapshot (1h or 4h).

Examples:
  python3 prediction_horizon_check.py save --symbol BTC
  python3 prediction_horizon_check.py save --symbol BTC --interval 240 --limit 1000
  python3 prediction_horizon_check.py check
  python3 prediction_horizon_check.py check --snapshot ~/.local/share/sygnif-agent/predictions/BTCUSDT_latest.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

BYBIT = "https://api.bybit.com/v5/market"
DATA_DIR = Path.home() / ".local/share/sygnif-agent/predictions"

# Kline interval (Bybit) -> bar length in minutes
INTERVAL_MINUTES: dict[str, int] = {
    "1": 1,
    "3": 3,
    "5": 5,
    "15": 15,
    "30": 30,
    "60": 60,
    "120": 120,
    "240": 240,
    "360": 360,
    "720": 720,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _finance_agent_dir() -> Path:
    return _repo_root() / "finance_agent"


def _spot_price(symbol: str) -> float:
    sym = f"{symbol.upper()}USDT" if not symbol.upper().endswith("USDT") else symbol.upper()
    r = requests.get(f"{BYBIT}/tickers", params={"category": "spot", "symbol": sym}, timeout=15)
    r.raise_for_status()
    lst = (r.json().get("result") or {}).get("list") or []
    if not lst:
        raise RuntimeError(f"No ticker for {sym}")
    return float(lst[0]["lastPrice"])


def bars_for_horizon(horizon_hours: int, interval: str) -> int:
    m = INTERVAL_MINUTES.get(interval)
    if not m:
        raise ValueError(f"unsupported interval {interval}")
    return max(1, int(round(horizon_hours * 60 / m)))


def compute_movement_probability_metrics(
    closes: np.ndarray,
    bars_forward: int,
    *,
    atr_pct_bar: float | None,
) -> dict[str, float | int]:
    """
    Empirical distribution of single-path forward returns over `bars_forward` bars.

    p_neutral uses a data-driven band: 20th percentile of |fwd_return| on this series.
    p_gt_sigma_hat: fraction with |fwd| > atr_pct_bar * sqrt(bars_forward) (rough ATR scaling).
    """
    if bars_forward < 1 or len(closes) <= bars_forward + 20:
        return {}
    a = closes[:-bars_forward].astype(np.float64)
    b = closes[bars_forward:].astype(np.float64)
    fwd_pct = (b - a) / np.maximum(a, 1e-12) * 100.0
    n = int(fwd_pct.size)
    if n < 30:
        return {}

    p_up = float((fwd_pct > 0).sum() / n)
    p_down = float((fwd_pct < 0).sum() / n)
    abs_fwd = np.abs(fwd_pct)
    neutral_pct = float(np.percentile(abs_fwd, 20))
    neutral_pct = max(neutral_pct, 0.015)  # floor ~1.5 bps in % space
    p_neutral = float((abs_fwd < neutral_pct).sum() / n)

    sigma_hat = (float(atr_pct_bar) if atr_pct_bar and atr_pct_bar > 0 else 0.5) * math.sqrt(
        float(bars_forward)
    )
    p_gt_sigma = float((abs_fwd > sigma_hat).sum() / n)

    return {
        "bars_forward": int(bars_forward),
        "n_samples": n,
        "p_up": round(p_up, 4),
        "p_down": round(p_down, 4),
        "p_neutral_abs": round(p_neutral, 4),
        "median_fwd_return_pct": round(float(np.median(fwd_pct)), 4),
        "p90_abs_fwd_pct": round(float(np.percentile(abs_fwd, 90)), 4),
        "neutral_band_abs_pct": round(neutral_pct, 4),
        "sigma_hat_abs_pct": round(sigma_hat, 4),
        "p_abs_gt_sigma_hat": round(p_gt_sigma, 4),
    }


def _levels_and_context_from_bot(
    symbol: str,
    *,
    interval: str,
    limit: int,
) -> tuple[dict, dict, np.ndarray]:
    """Return (levels dict, ta_context dict, close numpy array)."""
    fa = _finance_agent_dir()
    if not fa.is_dir():
        raise RuntimeError(f"finance_agent not found at {fa}")
    sys.path.insert(0, str(fa))
    from bot import bybit_kline, calc_indicators  # type: ignore

    sym = symbol.upper()
    if not sym.endswith("USDT"):
        sym = f"{sym}USDT"

    df = bybit_kline(sym, interval=interval, limit=limit)
    if df.empty or len(df) < 50:
        raise RuntimeError("No / insufficient kline data")
    ind = calc_indicators(df)
    if not ind:
        raise RuntimeError("calc_indicators empty")

    levels = {
        "support": round(float(ind["support"]), 6),
        "resistance": round(float(ind["resistance"]), 6),
    }
    closes = df["close"].to_numpy(dtype=np.float64)
    ta_context = {
        "interval": interval,
        "kline_limit": int(limit),
        "n_bars": int(len(df)),
        "rsi14": round(float(ind.get("rsi") or 0), 4),
        "trend": str(ind.get("trend", "")),
        "atr_pct": round(float(ind.get("atr_pct") or 0), 6),
        "price_ref": round(float(ind.get("price") or closes[-1]), 6),
    }
    return levels, ta_context, closes


def _movement_block_for_interval(
    closes: np.ndarray,
    interval: str,
    atr_pct_bar: float,
    horizons_hours: tuple[int, ...] = (24, 48, 72),
) -> dict:
    out: dict = {
        "method": "empirical_forward_returns_same_interval",
        "interval": interval,
        "horizons_hours": list(horizons_hours),
        "by_horizon": {},
    }
    for h in horizons_hours:
        bf = bars_for_horizon(h, interval)
        m = compute_movement_probability_metrics(closes, bf, atr_pct_bar=atr_pct_bar)
        if m:
            out["by_horizon"][str(h)] = m
    return out


def cmd_save(args: argparse.Namespace) -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    sym_in = args.symbol.upper()
    sym = f"{sym_in}USDT" if not sym_in.endswith("USDT") else sym_in

    interval = str(args.interval)
    limit = int(args.limit)
    if interval not in INTERVAL_MINUTES:
        print(f"Unsupported --interval {interval}. Use one of: {sorted(INTERVAL_MINUTES)}", file=sys.stderr)
        return 1
    limit = max(50, min(1000, limit))

    try:
        lv, ta_context, closes = _levels_and_context_from_bot(sym, interval=interval, limit=limit)
    except Exception as e:
        print(f"bot / kline failed ({e}), using CLI overrides or abort")
        if args.support is None or args.resistance is None:
            print("Pass --support and --resistance or fix finance_agent import.")
            return 1
        lv = {"support": args.support, "resistance": args.resistance}
        ta_context = {"interval": interval, "kline_limit": limit, "note": "manual levels; no movement metrics"}
        closes = np.array([], dtype=np.float64)

    price = _spot_price(sym)
    ts = _utc_now()
    t0 = ts.replace(microsecond=0)

    movement: dict | None = None
    if closes.size > 100:
        movement = _movement_block_for_interval(
            closes,
            interval,
            float(ta_context.get("atr_pct") or 0.5),
        )

    snap = {
        "schema_version": 2,
        "symbol": sym,
        "created_utc": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "spot_usd": price,
        "levels": lv,
        "ta_context": ta_context,
        "horizons": {
            "check_24h_utc": (t0 + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "check_48h_utc": (t0 + timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "check_72h_utc": (t0 + timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "thresholds": {
            "range_ok_low": lv["support"],
            "range_ok_high": lv["resistance"],
            "break_down": lv["support"],
            "break_up": lv["resistance"],
        },
        "note": args.note or "",
    }
    if movement:
        snap["movement_probability"] = movement

    stem = ts.strftime("%Y%m%d_%H%M%SZ")
    path = DATA_DIR / f"{sym}_{stem}.json"
    raw = json.dumps(snap, indent=2, ensure_ascii=False)
    path.write_text(raw + "\n", encoding="utf-8")
    latest = DATA_DIR / f"{sym}_latest.json"
    latest.write_text(raw + "\n", encoding="utf-8")
    print(f"Saved {path}")
    print(f"Latest -> {latest}")
    print(f"interval={interval} limit={limit}  levels S={lv['support']} R={lv['resistance']}")
    print(f"Suggested checks (UTC): 24h @ {snap['horizons']['check_24h_utc']}")
    print(f"                        48h @ {snap['horizons']['check_48h_utc']}")
    print(f"                        72h @ {snap['horizons']['check_72h_utc']}")
    if movement:
        print("movement_probability (empirical forward returns on this TF):")
        for hk, row in movement.get("by_horizon", {}).items():
            print(f"  {hk}h: n={row.get('n_samples')} p_up={row.get('p_up')} p_down={row.get('p_down')} "
                  f"p_neutral={row.get('p_neutral_abs')} P(|r|>sigma_hat)={row.get('p_abs_gt_sigma_hat')}")
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
    path = Path(args.snapshot or DATA_DIR / f"{args.symbol.upper()}USDT_latest.json")
    if not path.exists():
        alt = DATA_DIR / f"{args.symbol.upper()}_latest.json"
        path = alt if alt.exists() else path
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
    tc = snap.get("ta_context") or {}
    if tc:
        print(f"TA context: interval={tc.get('interval')} bars={tc.get('n_bars')} "
              f"RSI={tc.get('rsi14')} trend={tc.get('trend')}")
    print(f"Now ({_utc_now().strftime('%Y-%m-%d %H:%M:%S')} UTC): spot=${now_p:.6g}  ({ch:+.2f}% vs snapshot)")
    print(f"Verdict: {v}")
    mp = snap.get("movement_probability")
    if mp and abs(ch) >= 0.0001:
        direction = "up" if ch > 0 else "down"
        print(f"Realized move direction vs snapshot: {direction} ({ch:+.2f}%)")
        for hk, row in (mp.get("by_horizon") or {}).items():
            pu, pd = row.get("p_up"), row.get("p_down")
            if pu is not None and pd is not None:
                print(f"  At save, empirical p_up={pu} p_down={pd} over {hk}h horizon ({row.get('bars_forward')} bars)")
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

    s = sub.add_parser("save", help="Write snapshot (+ SYMBOL_latest.json)")
    s.add_argument("--symbol", default="DOGE")
    s.add_argument("--interval", default="60", help="Bybit kline interval (e.g. 60=1h, 240=4h)")
    s.add_argument("--limit", type=int, default=0, help="Kline limit (default: 200 for 60m, 1000 else)")
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
    if args.cmd == "save" and args.limit == 0:
        args.limit = 200 if args.interval == "60" else 1000
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
