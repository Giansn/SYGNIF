#!/usr/bin/env python3
"""
Interactive terminal channel for the SYGNIF BTC Swarm prediction system.
Swarm = order authority: forceenter via btc_analysis_forceenter.py --execute
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent
PRED_DIR = REPO / "prediction_agent"
SCRIPTS_DIR = REPO / "scripts"

PREDICTION_OUTPUT = PRED_DIR / "btc_prediction_output.json"
SWARM_STATE = PRED_DIR / "swarm_analyze_btc_state.json"
SWARM_SYNTH = PRED_DIR / "swarm_btc_synth.json"
RUNNER = PRED_DIR / "btc_predict_runner.py"
FORCEENTER = SCRIPTS_DIR / "btc_analysis_forceenter.py"

BANNER = """
╔══════════════════════════════════════════╗
║    SYGNIF BTC SWARM CHANNEL v2.0         ║
║    swarm = order authority               ║
╚══════════════════════════════════════════╝
Type  help  for commands.
"""

HELP = """
Commands:
  predict          — run fresh ML prediction (RF + XGBoost + LogReg)
  last             — show last prediction output
  state            — show swarm consensus state + votes
  synth            — show swarm synth card (signal + side)
  intent           — evaluate forceenter intent (dry-run, no order)
  order            — execute forceenter from swarm consensus (REAL ORDER)
  order short      — execute short forceenter (REAL ORDER, allow-short)
  quit / q         — exit
"""


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _ts(utc: str | None) -> str:
    if not utc:
        return "N/A"
    return utc.replace("T", " ").replace("Z", " UTC")


def cmd_predict() -> None:
    print("  Running btc_predict_runner.py …")
    result = subprocess.run([sys.executable, str(RUNNER)], capture_output=True, text=True)
    lines = (result.stdout + result.stderr).splitlines()
    for line in lines:
        if line.strip() and not line.startswith("/"):
            print(f"  {line}")
    _print_prediction(_read_json(PREDICTION_OUTPUT))


def _print_prediction(out: dict) -> None:
    if not out:
        return
    preds = out.get("predictions", {})
    close = out.get("current_close", "N/A")
    rf = preds.get("random_forest", {})
    xgb = preds.get("xgboost", {})
    logr = preds.get("direction_logistic", {})
    consensus = preds.get("consensus_nautilus_enhanced") or preds.get("consensus", "N/A")
    metrics = out.get("backtest_metrics", {})
    print()
    print(f"  Close:  ${close:,.2f}" if isinstance(close, (int, float)) else f"  Close:  {close}")
    print(f"  RF:     ${rf.get('next_mean', 0):,.2f}  ({rf.get('delta', 0):+.2f})  "
          f"[acc {metrics.get('random_forest', {}).get('Direction_Acc', 0):.1f}%]")
    print(f"  XGB:    ${xgb.get('next_mean', 0):,.2f}  ({xgb.get('delta', 0):+.2f})  "
          f"[acc {metrics.get('xgboost', {}).get('Direction_Acc', 0):.1f}%]")
    print(f"  LogReg: {logr.get('label', 'N/A')}  ({logr.get('confidence', 0):.1f}% conf)  "
          f"[acc {metrics.get('direction_logistic', {}).get('Accuracy', 0):.1f}%]")
    print(f"  ► Consensus: {consensus}")


def cmd_last() -> None:
    out = _read_json(PREDICTION_OUTPUT)
    if not out:
        print("  No prediction output. Run: predict")
        return
    print(f"  Generated : {_ts(out.get('generated_utc'))}")
    _print_prediction(out)


def cmd_state() -> None:
    s = _read_json(SWARM_STATE)
    latest = s.get("latest", {})
    if not latest:
        print("  No state data. Run: predict")
        return
    sources = latest.get("sources_compact", {})
    print(f"  Generated : {_ts(latest.get('loop_utc'))}")
    print(f"  Mean      : {latest.get('swarm_mean', 'N/A')}")
    print(f"  Label     : {latest.get('swarm_label', 'N/A')}")
    print(f"  Conflict  : {latest.get('swarm_conflict', 'N/A')}")
    print(f"  Sources   : {latest.get('sources_n', 'N/A')}")
    if sources:
        print("  Votes:")
        for k, v in sources.items():
            print(f"    {k:6s} → {v}")


def cmd_synth() -> None:
    s = _read_json(SWARM_SYNTH)
    if not s:
        print("  No synth data found.")
        return
    print(f"  Generated : {_ts(s.get('generated_utc'))}")
    price = s.get("btc_usd_price")
    print(f"  BTC price : {'${:,.2f}'.format(price) if price else 'N/A'}")
    print(f"  Signal    : {s.get('order_signal', 'N/A')}")
    print(f"  Side      : {s.get('side', 'N/A')}")
    print(f"  Bull/Bear : {s.get('bull_bear', 'N/A')}")
    print(f"  Dump risk : {s.get('btc_dump_risk_pct', 'N/A')}%")
    print(f"  Swarm     : {s.get('swarm_label', 'N/A')}  (mean {s.get('swarm_mean', 'N/A')})")


def _run_forceenter(*, execute: bool, allow_short: bool = False) -> None:
    if not FORCEENTER.is_file():
        print(f"  ERROR: {FORCEENTER} not found.")
        return
    cmd = [sys.executable, str(FORCEENTER)]
    if allow_short:
        cmd.append("--allow-short")
    if execute:
        cmd.append("--execute")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO))
    output = (result.stdout + result.stderr).strip()
    for line in output.splitlines():
        print(f"  {line}")
    if result.returncode != 0:
        print(f"  [exit code {result.returncode}]")


def cmd_intent() -> None:
    print("  Evaluating forceenter intent (dry-run) …")
    _run_forceenter(execute=False)


def cmd_order(allow_short: bool = False) -> None:
    side_hint = " (long + short)" if allow_short else " (long only)"
    print(f"  ⚠  SWARM ORDER AUTHORITY — forceenter{side_hint}")
    try:
        confirm = input("  Confirm execute? [yes/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n  Aborted.")
        return
    if confirm != "yes":
        print("  Aborted.")
        return
    print("  Executing …")
    _run_forceenter(execute=True, allow_short=allow_short)


def main() -> None:
    print(BANNER)
    while True:
        try:
            raw = input("swarm> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Disconnected.")
            break
        if not raw:
            continue
        if raw in ("quit", "q", "exit"):
            print("  Bye.")
            break
        if raw == "help":
            print(HELP)
        elif raw == "predict":
            print()
            cmd_predict()
            print()
        elif raw == "last":
            print()
            cmd_last()
            print()
        elif raw == "state":
            print()
            cmd_state()
            print()
        elif raw == "synth":
            print()
            cmd_synth()
            print()
        elif raw == "intent":
            print()
            cmd_intent()
            print()
        elif raw == "order":
            print()
            cmd_order(allow_short=False)
            print()
        elif raw == "order short":
            print()
            cmd_order(allow_short=True)
            print()
        else:
            print(f"  Unknown command: '{raw}'  — type help")


if __name__ == "__main__":
    main()
