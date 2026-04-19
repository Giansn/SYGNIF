#!/usr/bin/env python3
"""
Hivemind vote gate accuracy simulation — connects to Bybit public kline API.

Runs the same offline predict-loop backtest under 3 hivemind gate scenarios and
measures raw model accuracy (did price move in predicted direction N bars later?).

Scenarios
---------
A  no_hm_gate     SWARM_ORDER_REQUIRE_HIVEMIND_VOTE=0  — trades on model signal alone
B  hm_quiet       hm_vote=0 (flat/quiet), flat_pass=1  — current config if CLI returned 0
C  hm_liveness    hm_vote=1 (always active = current live state) — blocks all shorts

Usage::

  cd ~/SYGNIF
  python3 scripts/swarm_hm_vote_accuracy_sim.py
  python3 scripts/swarm_hm_vote_accuracy_sim.py --hours 48 --step 4 --accuracy-bars 3
  python3 scripts/swarm_hm_vote_accuracy_sim.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", module="sklearn")

_REPO = Path(__file__).resolve().parents[1]
_PA = _REPO / "prediction_agent"
_DATA = _REPO / "finance_agent" / "btc_specialist" / "data"

sys.path.insert(0, str(_PA))
sys.path.insert(0, str(_REPO / "finance_agent"))
sys.path.insert(0, str(_REPO / "scripts"))

from predict_protocol_offline_swarm_backtest import (  # noqa: E402
    build_default_gate_env,
    run_simulation,
)


# ---------------------------------------------------------------------------
# Bybit kline fetch (public, no keys needed)
# ---------------------------------------------------------------------------

def fetch_btc_klines(symbol: str, limit: int = 1000) -> Any:
    """Return DataFrame from Bybit public kline endpoint."""
    from btc_predict_live import fetch_linear_5m_klines  # noqa: PLC0415

    return fetch_linear_5m_klines(symbol, limit=limit)


# ---------------------------------------------------------------------------
# Raw signal accuracy (price-direction test, no gate)
# ---------------------------------------------------------------------------

def measure_raw_accuracy(
    symbol: str,
    *,
    hours: float,
    step: int,
    kline_limit: int,
    window: int,
    rf_trees: int,
    xgb_estimators: int,
    forward_bars: int,
) -> dict[str, Any]:
    """
    For each refit bar: record model signal and whether price moved in that
    direction ``forward_bars`` bars later.  No gate, no PnL simulation.
    """
    import math

    from btc_asap_predict_core import decide_side  # noqa: PLC0415
    from btc_predict_live import fetch_linear_5m_klines  # noqa: PLC0415
    from btc_predict_live import fit_predict_live  # noqa: PLC0415

    training_path = _PA / "training_channel_output.json"
    training: dict[str, Any] = {}
    if training_path.is_file():
        try:
            training = json.loads(training_path.read_text()) or {}
        except Exception:
            pass

    lim = max(200, min(1000, kline_limit))
    bars_eval = int(math.ceil(hours * 12))
    df = fetch_linear_5m_klines(symbol, limit=lim)
    n = len(df)
    if n < bars_eval + 80:
        return {"ok": False, "error": "not_enough_klines", "n": n}

    es = max(120, n - bars_eval)
    ee = n - 1 - forward_bars  # need forward_bars lookahead

    idxs = list(range(es, ee, step))
    if not idxs:
        return {"ok": False, "error": "empty_idxs"}

    closes = df["Close"].astype(float)

    records: list[dict[str, Any]] = []
    for i in idxs:
        sub = df.iloc[: i + 1].copy()
        try:
            _a, _e, out = fit_predict_live(
                sub,
                window=window,
                data_dir=str(_DATA),
                rf_trees=rf_trees,
                xgb_estimators=xgb_estimators,
                write_json_path=None,
            )
        except ValueError:
            continue
        target, why = decide_side(out, training)
        if target not in ("long", "short"):
            continue

        c_now = float(closes.iloc[i])
        c_fwd = float(closes.iloc[min(i + forward_bars, n - 1)])
        price_up = c_fwd > c_now
        correct = (target == "long" and price_up) or (target == "short" and not price_up)
        records.append(
            {
                "bar": i,
                "target": target,
                "c_now": round(c_now, 2),
                "c_fwd": round(c_fwd, 2),
                "price_up": price_up,
                "correct": correct,
            }
        )

    if not records:
        return {"ok": False, "error": "no_signals"}

    total = len(records)
    correct_n = sum(1 for r in records if r["correct"])
    longs = [r for r in records if r["target"] == "long"]
    shorts = [r for r in records if r["target"] == "short"]

    return {
        "ok": True,
        "forward_bars": forward_bars,
        "forward_minutes": forward_bars * 5,
        "total_signals": total,
        "correct": correct_n,
        "accuracy_pct": round(correct_n / total * 100, 1),
        "long_signals": len(longs),
        "long_correct": sum(1 for r in longs if r["correct"]),
        "long_accuracy_pct": round(sum(1 for r in longs if r["correct"]) / len(longs) * 100, 1) if longs else None,
        "short_signals": len(shorts),
        "short_correct": sum(1 for r in shorts if r["correct"]),
        "short_accuracy_pct": round(sum(1 for r in shorts if r["correct"]) / len(shorts) * 100, 1) if shorts else None,
        "sample": records[:10],
    }


# ---------------------------------------------------------------------------
# Gate scenario runner
# ---------------------------------------------------------------------------

def run_scenario(
    name: str,
    *,
    hm_vote: int,
    require_hm: bool,
    hm_flat_pass: bool,
    symbol: str,
    hours: float,
    step: int,
    kline_limit: int,
    window: int,
    rf_trees: int,
    xgb_estimators: int,
    notional: float,
    leverage: float,
    training_path: Path,
    nautilus_path: Path,
) -> dict[str, Any]:
    gate_env = build_default_gate_env(
        min_mean_long=-0.25,
        max_mean_short=0.25,
        block_conflict=False,
        fusion_align=False,
    )
    gate_env["SWARM_ORDER_REQUIRE_HIVEMIND_VOTE"] = "1" if require_hm else "0"
    gate_env["SWARM_ORDER_HIVEMIND_VOTE_FLAT_PASS"] = "1" if hm_flat_pass else "0"

    result = run_simulation(
        hours=hours,
        step=step,
        kline_limit=kline_limit,
        window=window,
        rf_trees=rf_trees,
        xgb_estimators=xgb_estimators,
        notional=notional,
        leverage=leverage,
        margin_usdt=None,
        hold_on_no_edge=True,
        training_path=training_path,
        nautilus_path=nautilus_path,
        apply_swarm_gate=require_hm,
        gate_env=gate_env,
        tp_pct=None,
        sl_pct=None,
        symbol=symbol,
        offline_hm_vote=hm_vote,
        offline_hm_source="synthetic",
        patch_nautilus_generated_utc=True,
    )
    result["scenario"] = name
    result["hm_vote_fixed"] = hm_vote
    result["require_hm_gate"] = require_hm
    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _pnl_str(r: dict[str, Any]) -> str:
    if not r.get("ok"):
        return f"ERROR: {r.get('error')}"
    pnl = r.get("pnl_usdt_approx", 0)
    roi = r.get("roi_on_margin_approx") or 0
    trades = r.get("segments_total", 0)
    return f"PnL={pnl:+,.0f} USDT  ROI={roi*100:+.1f}%  trades={trades}"


def print_report(
    scenarios: list[dict[str, Any]],
    accuracy: dict[str, Any],
    *,
    as_json: bool = False,
) -> None:
    report = {
        "raw_model_accuracy": accuracy,
        "gate_scenarios": [
            {
                "scenario": r["scenario"],
                "hm_vote": r.get("hm_vote_fixed"),
                "require_hm_gate": r.get("require_hm_gate"),
                "ok": r.get("ok"),
                "pnl_usdt": r.get("pnl_usdt_approx"),
                "roi_pct": round((r.get("roi_on_margin_approx") or 0) * 100, 2),
                "trades": r.get("segments_total"),
                "eval_window_utc": r.get("eval_window_utc"),
            }
            for r in scenarios
        ],
    }

    if as_json:
        print(json.dumps(report, indent=2))
        return

    print("\n" + "=" * 60)
    print("HIVEMIND VOTE ACCURACY SIMULATION")
    print("=" * 60)

    acc = accuracy
    if acc.get("ok"):
        print(f"\nRaw ML Signal Accuracy ({acc['forward_minutes']}min lookahead):")
        print(f"  Total signals : {acc['total_signals']}")
        print(f"  Overall       : {acc['correct']}/{acc['total_signals']}  = {acc['accuracy_pct']}%")
        if acc.get("long_signals"):
            print(f"  Long          : {acc['long_correct']}/{acc['long_signals']} = {acc['long_accuracy_pct']}%")
        if acc.get("short_signals"):
            print(f"  Short         : {acc['short_correct']}/{acc['short_signals']} = {acc['short_accuracy_pct']}%")
    else:
        print(f"\nAccuracy check failed: {acc.get('error')}")

    print("\nGate Scenario Comparison:")
    header = f"  {'Scenario':<18} {'hm_vote':>8} {'HM gate':>8} {'PnL USDT':>12} {'ROI':>7} {'Trades':>7}"
    print(header)
    print("  " + "-" * 60)
    for r in scenarios:
        if not r.get("ok"):
            print(f"  {r['scenario']:<18}  ERROR: {r.get('error')}")
            continue
        pnl = r.get("pnl_usdt_approx", 0)
        roi = (r.get("roi_on_margin_approx") or 0) * 100
        trades = r.get("segments_total", 0)
        hm = r.get("hm_vote_fixed")
        gate = "on" if r.get("require_hm_gate") else "off"
        print(f"  {r['scenario']:<18} {hm:>8} {gate:>8} {pnl:>+12,.0f} {roi:>+6.1f}% {trades:>7}")

    print("\nLegend:")
    print("  A no_hm_gate    = ML signal only, no Hivemind gate")
    print("  B hm_quiet      = hm_vote=0 (Hivemind quiet), flat_pass=1 → shorts allowed")
    print("  C hm_liveness   = hm_vote=1 (CURRENT live state: always active) → shorts blocked")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Hivemind vote accuracy simulation vs Bybit live data")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--hours", type=float, default=24.0, help="Evaluation window in hours")
    ap.add_argument("--step", type=int, default=6, help="Refit every N bars (5m each)")
    ap.add_argument("--kline-limit", type=int, default=1000)
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--rf-trees", type=int, default=int(os.environ.get("ASAP_RF_TREES", "32") or 32))
    ap.add_argument("--xgb-estimators", type=int, default=int(os.environ.get("ASAP_XGB_ESTIMATORS", "32") or 32))
    ap.add_argument("--notional", type=float, default=80000.0)
    ap.add_argument("--leverage", type=float, default=50.0)
    ap.add_argument("--accuracy-bars", type=int, default=2, help="Forward bars for accuracy test (5m each)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    training_path = _PA / "training_channel_output.json"
    nautilus_path = _DATA / "nautilus_strategy_signal.json"

    common = dict(
        symbol=args.symbol,
        hours=args.hours,
        step=args.step,
        kline_limit=args.kline_limit,
        window=args.window,
        rf_trees=args.rf_trees,
        xgb_estimators=args.xgb_estimators,
        notional=args.notional,
        leverage=args.leverage,
        training_path=training_path,
        nautilus_path=nautilus_path,
    )

    if not args.as_json:
        print(f"Fetching {args.kline_limit} BTC/USDT 5m klines from Bybit...")
        print(f"Eval window: {args.hours}h  refit every {args.step} bars  notional={args.notional:,.0f} USDT")
        print(f"Accuracy lookahead: {args.accuracy_bars} bars ({args.accuracy_bars * 5}min)")
        print()

    # 1. Raw model accuracy (no gate)
    if not args.as_json:
        print("Running raw accuracy measurement...")
    accuracy = measure_raw_accuracy(
        args.symbol,
        hours=args.hours,
        step=args.step,
        kline_limit=args.kline_limit,
        window=args.window,
        rf_trees=args.rf_trees,
        xgb_estimators=args.xgb_estimators,
        forward_bars=args.accuracy_bars,
    )

    # 2. Scenario A: no HM gate
    if not args.as_json:
        print("Scenario A: no HM gate...")
    sc_a = run_scenario(
        "A_no_hm_gate",
        hm_vote=0,
        require_hm=False,
        hm_flat_pass=True,
        **common,
    )

    # 3. Scenario B: HM gate ON, hm_vote=0 (quiet / flat)
    if not args.as_json:
        print("Scenario B: hm_vote=0, flat_pass=1 (Hivemind quiet)...")
    sc_b = run_scenario(
        "B_hm_quiet",
        hm_vote=0,
        require_hm=True,
        hm_flat_pass=True,
        **common,
    )

    # 4. Scenario C: HM gate ON, hm_vote=1 (current live state — always blocks shorts)
    if not args.as_json:
        print("Scenario C: hm_vote=1, flat_pass=1 (current live state)...")
    sc_c = run_scenario(
        "C_hm_liveness",
        hm_vote=1,
        require_hm=True,
        hm_flat_pass=True,
        **common,
    )

    print_report([sc_a, sc_b, sc_c], accuracy, as_json=args.as_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
