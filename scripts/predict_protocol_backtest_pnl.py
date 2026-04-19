#!/usr/bin/env python3
"""
**Naive P/L simulation** for the same signal stack as ``btc_predict_protocol_loop``:
``fit_predict_live`` + ``decide_side`` on Bybit **public** 5m linear klines.

**Not** exchange-grade backtest: no fees/slippage/funding by default; refits only every
``--step`` bars; PnL is the sum of **fixed-qty** moves between sampled closes (long = +qty·Δ,
short = −qty·Δ). **Notional 2k** sets qty ≈ 2000/price at each (re)entry.

**Leverage** does not multiply this USDT PnL if size is fixed at 2k notional — it only affects
margin in live trading. Optional ``--margin-usdt`` prints **return on margin** ≈ PnL / margin.

Example (last 48h, 20m refit grid)::

  python3 scripts/predict_protocol_backtest_pnl.py --hours 48 --step 4 --json
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


def _qty_from_notional(notional: float, close: float) -> float:
    if close <= 0 or notional <= 0:
        return 0.0
    raw = notional / close
    step = 0.001
    q = math.floor(raw / step) * step
    return q if q >= step else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description="Subsampled PnL sim for predict-protocol signals on 5m BTC")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--hours", type=float, default=48.0, help="Evaluation window at tail of series")
    ap.add_argument(
        "--kline-limit",
        type=int,
        default=1000,
        help="Fetch up to this many 5m bars (max 1000); must cover warmup + hours",
    )
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--rf-trees", type=int, default=max(10, int(os.environ.get("ASAP_RF_TREES", "32") or 32)))
    ap.add_argument(
        "--xgb-estimators",
        type=int,
        default=max(20, int(os.environ.get("ASAP_XGB_N_ESTIMATORS", "60") or 60)),
    )
    ap.add_argument("--step", type=int, default=4, help="Refit every N bars inside eval window")
    ap.add_argument("--notional-usdt", type=float, default=2000.0)
    ap.add_argument(
        "--leverage",
        type=float,
        default=50.0,
        help="For margin ROI only: margin ≈ notional / leverage when --margin-usdt unset",
    )
    ap.add_argument(
        "--margin-usdt",
        type=float,
        default=None,
        help="If set, ROI on margin = pnl / margin_usdt",
    )
    ap.add_argument(
        "--hold-on-no-edge",
        action="store_true",
        help="If set, do not flatten on no-edge (else flatten like --exit-on-no-edge)",
    )
    ap.add_argument(
        "--training-json",
        type=Path,
        default=_PA / "training_channel_output.json",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    sys.path.insert(0, str(_PA))
    from btc_asap_predict_core import decide_side  # noqa: E402
    from btc_predict_live import fetch_linear_5m_klines  # noqa: E402
    from btc_predict_live import fit_predict_live  # noqa: E402

    training = _load_training(args.training_json)
    lim = max(200, min(1000, int(args.kline_limit)))
    step = max(1, int(args.step))
    hours = max(1.0, float(args.hours))
    bars_eval = int(math.ceil(hours * 12))  # 5m bars

    df = fetch_linear_5m_klines(args.symbol, limit=lim)
    n = len(df)
    if n < bars_eval + 80:
        print("predict_protocol_backtest_pnl: not enough klines", n, file=sys.stderr)
        return 1

    eval_start = max(0, n - bars_eval)
    min_fit = 120
    first_i = max(min_fit, eval_start)
    idxs = list(range(first_i, n - 1, step))
    if not idxs:
        print("predict_protocol_backtest_pnl: empty index list", file=sys.stderr)
        return 1
    if idxs[-1] != n - 2:
        idxs.append(n - 2)

    pos = 0  # -1 short, 0 flat, +1 long
    qty = 0.0
    pnl = 0.0
    closes = df["Close"].astype(float)
    times = df["Date"]

    hold_no_edge = bool(args.hold_on_no_edge)
    notional = float(args.notional_usdt)
    lev = max(1.0, float(args.leverage))
    margin = float(args.margin_usdt) if args.margin_usdt is not None else notional / lev

    segments: list[dict] = []

    for k in range(len(idxs) - 1):
        i = idxs[k]
        i2 = idxs[k + 1]
        sub = df.iloc[: i + 1].copy()
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
            print(f"predict_protocol_backtest_pnl skip i={i}: {exc}", file=sys.stderr)
            continue

        target, why = decide_side(out, training)
        c_i = float(closes.iloc[i])

        if not hold_no_edge and target is None:
            pos = 0
            qty = 0.0
        elif target == "long":
            if pos <= 0:
                pos = 1
                qty = _qty_from_notional(notional, c_i)
        elif target == "short":
            if pos >= 0:
                pos = -1
                qty = _qty_from_notional(notional, c_i)
        else:
            if not hold_no_edge:
                pos = 0
                qty = 0.0

        c_i2 = float(closes.iloc[i2])
        dpx = c_i2 - c_i
        move_pnl = pos * qty * dpx
        pnl += move_pnl
        if pos != 0 and qty > 0:
            segments.append(
                {
                    "from_idx": i,
                    "to_idx": i2,
                    "from_utc": times.iloc[i].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "to_utc": times.iloc[i2].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "pos": pos,
                    "qty": qty,
                    "d_close": round(dpx, 2),
                    "pnl_usdt": round(move_pnl, 2),
                    "target": target,
                    "reason_tail": (why or "")[:120],
                }
            )

    t0 = times.iloc[eval_start].strftime("%Y-%m-%dT%H:%M:%SZ")
    t1 = times.iloc[n - 1].strftime("%Y-%m-%dT%H:%M:%SZ")

    summary = {
        "symbol": args.symbol,
        "eval_window_utc": [t0, t1],
        "hours_requested": hours,
        "bars_in_window": bars_eval,
        "kline_bars_fetched": n,
        "refit_step_bars": step,
        "refit_points": len(idxs),
        "notional_usdt": notional,
        "leverage_assumed": lev,
        "margin_usdt_for_roi": round(margin, 2),
        "pnl_usdt_approx": round(pnl, 2),
        "roi_on_margin_approx": round(pnl / margin, 6) if margin > 0 else None,
        "disclaimer": (
            "Subsampled bars; no fees/slippage/funding; signal stale between refits; research only."
        ),
        "segments": segments if args.json else segments[:40],
    }

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print(f"SYGNIF_PROTOCOL_BT window_utc {t0} .. {t1} (~{hours}h)")
    print(f"  refit_step={step} points={len(idxs)} notional={notional} USDT (lev {lev}x → margin≈{margin:.2f} USDT)")
    print(f"  approx P/L (USDT): {pnl:.2f}")
    print(f"  approx ROI on margin: {100.0 * pnl / margin:.4f}%" if margin > 0 else "")
    print(f"  {summary['disclaimer']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
