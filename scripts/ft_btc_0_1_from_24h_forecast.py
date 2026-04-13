#!/usr/bin/env python3
"""
Map ``prediction_agent/btc_24h_movement_prediction.json`` → ``ft_btc_0_1_forceenter.py``.

**Governance (L3):** no HTTP unless ``FORECAST_FORCEENTER_OK=YES`` in the environment.
Optional ``FORECAST_MIN_CONFIDENCE`` (default ``0``): if synthesis ``confidence_0_100`` is below,
exit 3 unless ``FORECAST_ALLOW_LOW_CONF=1``.

Neutral bias → exit 0 (no trade).

Examples::

  FORECAST_FORCEENTER_OK=YES python3 scripts/ft_btc_0_1_from_24h_forecast.py --dry-run
  FORECAST_FORCEENTER_OK=YES FORECAST_STAKE=50 python3 scripts/ft_btc_0_1_from_24h_forecast.py
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DEFAULT_PRED = _REPO / "prediction_agent" / "btc_24h_movement_prediction.json"
_FORCE = _REPO / "scripts" / "ft_btc_0_1_forceenter.py"


def main() -> int:
    ap = argparse.ArgumentParser(description="forceenter freqtrade-btc-0-1 from 24h movement JSON")
    ap.add_argument("--prediction-json", type=Path, default=_DEFAULT_PRED)
    ap.add_argument("--stake", type=float, default=float(os.environ.get("FORECAST_STAKE", "50")))
    ap.add_argument("--pair", default="BTC/USDT:USDT")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if os.environ.get("FORECAST_FORCEENTER_OK", "").strip() != "YES":
        print(
            "Refusing: set FORECAST_FORCEENTER_OK=YES to acknowledge governed execution "
            "(demo/live per your Freqtrade config).",
            file=sys.stderr,
        )
        return 2

    if not args.prediction_json.is_file():
        print(f"Missing {args.prediction_json}", file=sys.stderr)
        return 1

    data = json.loads(args.prediction_json.read_text(encoding="utf-8"))
    syn = data.get("synthesis") if isinstance(data, dict) else None
    if not isinstance(syn, dict):
        print("Invalid prediction JSON: no synthesis", file=sys.stderr)
        return 1

    bias = str(syn.get("bias_24h") or "NEUTRAL").upper()
    conf = syn.get("confidence_0_100")
    try:
        conf_i = int(conf) if conf is not None else 0
    except (TypeError, ValueError):
        conf_i = 0

    min_c = int(os.environ.get("FORECAST_MIN_CONFIDENCE", "0"))
    if conf_i < min_c and os.environ.get("FORECAST_ALLOW_LOW_CONF", "").strip() != "1":
        print(
            f"Refusing: confidence {conf_i} < FORECAST_MIN_CONFIDENCE={min_c} "
            "(set FORECAST_ALLOW_LOW_CONF=1 to override).",
            file=sys.stderr,
        )
        return 3

    if bias == "NEUTRAL":
        print(json.dumps({"ok": True, "action": "skip", "reason": "bias_neutral", "synthesis": syn}, indent=2))
        return 0

    side = "long" if bias == "UP" else "short"
    tag = f"manual_24h_forecast_{side}"
    print(
        json.dumps(
            {
                "ok": True,
                "action": "forceenter",
                "bias_24h": bias,
                "side": side,
                "confidence_0_100": conf_i,
                "entry_tag": tag,
                "stakeamount": args.stake,
                "pair": args.pair,
                "prediction_generated_utc": data.get("generated_utc"),
            },
            indent=2,
        ),
        flush=True,
    )

    cmd = [
        sys.executable,
        str(_FORCE),
        "--pair",
        args.pair,
        "--side",
        side,
        "--stake",
        str(args.stake),
        "--entry-tag",
        tag,
    ]
    if args.dry_run:
        cmd.append("--dry-run")

    return int(subprocess.call(cmd, cwd=str(_REPO)))


if __name__ == "__main__":
    raise SystemExit(main())
