#!/usr/bin/env python3
"""
Replay **ASAP live fit** + **decide_side** (same stack as ``btc_predict_protocol_loop``) over a
**prefix** of Bybit public 5m linear klines to find **eligible** bars (target **long** or **short**,
not no-edge / not R01-blocked).

Use for “when would the protocol have fired?” without waiting on the clock. Heavy: each step runs a
full ``fit_predict_live`` (~1–3s); use ``--step`` > 1 to subsample.

Examples::

  python3 scripts/predict_protocol_eligible_scan.py --kline-limit 1000 --step 2
  python3 scripts/predict_protocol_eligible_scan.py --kline-limit 400 --step 1 --json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", module="sklearn")

_REPO = Path(__file__).resolve().parents[1]
_PA = _REPO / "prediction_agent"
_DATA = _REPO / "finance_agent" / "btc_specialist" / "data"


def _load_training(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        o = json.loads(path.read_text(encoding="utf-8"))
        return o if isinstance(o, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _merge_spans(
    rows: list[tuple[str, str | None, str]],
) -> list[dict]:
    """rows: (iso_utc, target_or_none, reason) in time order. Merge consecutive same non-None side."""
    spans: list[dict] = []
    cur_side: str | None = None
    cur_start: str | None = None
    cur_end: str | None = None
    cur_n = 0
    for ts, side, _why in rows:
        if side is None:
            if cur_side is not None and cur_start is not None:
                spans.append(
                    {
                        "side": cur_side,
                        "start_utc": cur_start,
                        "end_utc": cur_end,
                        "bars_in_sample": cur_n,
                    }
                )
            cur_side = None
            cur_start = None
            cur_end = None
            cur_n = 0
            continue
        if side != cur_side:
            if cur_side is not None and cur_start is not None:
                spans.append(
                    {
                        "side": cur_side,
                        "start_utc": cur_start,
                        "end_utc": cur_end,
                        "bars_in_sample": cur_n,
                    }
                )
            cur_side = side
            cur_start = ts
            cur_end = ts
            cur_n = 1
        else:
            cur_end = ts
            cur_n += 1
    if cur_side is not None and cur_start is not None:
        spans.append(
            {
                "side": cur_side,
                "start_utc": cur_start,
                "end_utc": cur_end,
                "bars_in_sample": cur_n,
            }
        )
    return spans


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Scan recent 5m history for predict-protocol eligible (long/short) bars",
    )
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument(
        "--kline-limit",
        type=int,
        default=800,
        help="Bybit public klines to fetch (max 1000)",
    )
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--rf-trees", type=int, default=max(10, int(os.environ.get("ASAP_RF_TREES", "32") or 32)))
    ap.add_argument(
        "--xgb-estimators",
        type=int,
        default=max(20, int(os.environ.get("ASAP_XGB_N_ESTIMATORS", "60") or 60)),
    )
    ap.add_argument(
        "--step",
        type=int,
        default=2,
        help="Advance end index by this many bars each fit (1 = every bar, slower)",
    )
    ap.add_argument(
        "--training-json",
        type=Path,
        default=_PA / "training_channel_output.json",
    )
    ap.add_argument("--manual-notional-usdt", type=float, default=2000.0)
    ap.add_argument("--manual-leverage", type=float, default=50.0)
    ap.add_argument("--json", action="store_true", help="Single JSON object on stdout")
    args = ap.parse_args()

    sys.path.insert(0, str(_PA))
    from btc_asap_predict_core import decide_side  # noqa: E402
    from btc_predict_live import fetch_linear_5m_klines  # noqa: E402
    from btc_predict_live import fit_predict_live  # noqa: E402

    training = _load_training(args.training_json)
    lim = max(120, min(1000, int(args.kline_limit)))
    step = max(1, int(args.step))

    df = fetch_linear_5m_klines(args.symbol, limit=lim)
    n = len(df)
    if n < 80:
        print("predict_protocol_eligible_scan: too few klines", n, file=sys.stderr)
        return 1

    # First end index: fit_predict_live needs window+50 rows after TA; start conservative
    start_end = max(120, args.window + 80)
    rows_out: list[tuple[str, str | None, str]] = []
    eligible = 0
    last_close_scan = 0.0

    for end in range(start_end, n, step):
        sub = df.iloc[: end + 1].copy()
        try:
            _a, _e, out = fit_predict_live(
                sub,
                window=args.window,
                data_dir=str(_DATA),
                rf_trees=args.rf_trees,
                xgb_estimators=args.xgb_estimators,
                write_json_path=None,
            )
        except ValueError as exc:
            print(f"predict_protocol_eligible_scan skip end={end}: {exc}", file=sys.stderr)
            continue
        target, why = decide_side(out, training)
        lc = float(out.get("current_close") or 0.0)
        last_close_scan = lc
        ts = str(out.get("last_candle_utc") or "")
        rows_out.append((ts, target, why))
        if target is not None:
            eligible += 1

    spans = _merge_spans(rows_out)
    notional = float(args.manual_notional_usdt)
    lev = float(args.manual_leverage)
    planned_qty = None
    if last_close_scan > 0 and notional > 0:
        q = math.floor((notional / last_close_scan) / 0.001) * 0.001
        planned_qty = round(q, 6)

    summary = {
        "symbol": args.symbol,
        "kline_bars": n,
        "step": step,
        "fits_run": len(rows_out),
        "eligible_fits": eligible,
        "eligible_frac": round(eligible / max(1, len(rows_out)), 4),
        "protocol_setup": {
            "manual_notional_usdt": notional,
            "manual_leverage": lev,
            "planned_qty_at_last_close": planned_qty,
            "last_close_for_qty": last_close_scan,
        },
        "spans_utc": spans,
        "note": "Spans follow --step sampling; bar counts are sampled fits, not wall-clock minutes.",
    }

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print(f"SYGNIF_ELIGIBLE_SCAN symbol={args.symbol} bars={n} step={step} fits={len(rows_out)}")
    print(
        f"  Protocol params: notional={notional} USDT  leverage={lev}  "
        f"qty≈{planned_qty} BTC (at last scanned close {last_close_scan})"
    )
    print(f"  Eligible fits (long|short): {eligible} ({100.0 * eligible / max(1, len(rows_out)):.1f}%)")
    print("  Merged spans (UTC):")
    if not spans:
        print("    (none — all no-edge or blocked in this sample)")
    for s in spans:
        print(
            f"    {s['side']:5s}  {s['start_utc']}  →  {s['end_utc']}  "
            f"({s['bars_in_sample']} sampled bars)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
