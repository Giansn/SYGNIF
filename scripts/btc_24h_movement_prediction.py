#!/usr/bin/env python3
"""
24h BTC/USDT movement outlook: empirical distribution on 1h closes (same math as
``prediction_horizon_check``) + optional blend with ``btc_prediction_output.json``
next-bar consensus. Writes ``prediction_agent/btc_24h_movement_prediction.json``.

Educational / research — not a trading signal guarantee.

Examples:
  python3 scripts/btc_24h_movement_prediction.py
  python3 scripts/btc_24h_movement_prediction.py --quiet --no-runner
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import prediction_horizon_check as ph  # noqa: E402


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_ohlcv_hlc(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or len(raw) < 80:
        raise RuntimeError(f"OHLCV list too short or invalid: {path}")
    h = np.array([float(c["h"]) for c in raw], dtype=np.float64)
    l = np.array([float(c["l"]) for c in raw], dtype=np.float64)
    c = np.array([float(c["c"]) for c in raw], dtype=np.float64)
    return h, l, c


def atr_pct_bar(h: np.ndarray, l: np.ndarray, c: np.ndarray, period: int = 14) -> float:
    prev = np.empty_like(c)
    prev[0] = c[0]
    prev[1:] = c[:-1]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    tail = tr[-period:]
    tc = c[-period:]
    v = float(np.mean(tail / np.maximum(tc, 1e-12)) * 100.0)
    return max(v, 0.05)


def load_runner_signal(repo: Path) -> dict | None:
    p = repo / "prediction_agent" / "btc_prediction_output.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    pred = data.get("predictions") or {}
    if not isinstance(pred, dict):
        return None
    cons = str(pred.get("consensus") or "").upper()
    d = pred.get("direction_logistic") or {}
    label = str(d.get("label") or "").upper()
    conf = d.get("confidence")
    try:
        conf_f = float(conf) if conf is not None else None
    except (TypeError, ValueError):
        conf_f = None
    return {"consensus": cons, "direction_label": label, "direction_confidence_pct": conf_f}


def runner_score_up(run: dict | None) -> float | None:
    if not run:
        return None
    cons = run["consensus"]
    lab = run["direction_label"]
    if cons == "BULLISH":
        base = 0.68
    elif cons == "BEARISH":
        base = 0.32
    else:
        base = 0.5
    if lab == "UP":
        return min(0.92, base + 0.12)
    if lab == "DOWN":
        return max(0.08, base - 0.12)
    return base


def synthesize(p_up: float, p_down: float, runner: dict | None, w_runner: float) -> dict:
    r = runner_score_up(runner)
    if r is None:
        blend = p_up
        used_runner = False
    else:
        w = max(0.0, min(0.55, w_runner))
        blend = (1.0 - w) * p_up + w * r
        used_runner = True

    margin = 0.03
    if blend >= 0.5 + margin:
        bias = "UP"
    elif blend <= 0.5 - margin:
        bias = "DOWN"
    else:
        bias = "NEUTRAL"

    strength = abs(blend - 0.5) * 2.0
    confidence = int(round(min(100.0, max(0.0, strength * 100.0))))

    return {
        "bias_24h": bias,
        "p_up_blended": round(blend, 4),
        "p_up_empirical": round(p_up, 4),
        "p_down_empirical": round(p_down, 4),
        "used_runner": used_runner,
        "runner_weight": w_runner if used_runner else 0.0,
        "confidence_0_100": confidence,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="BTC 24h movement prediction (empirical + optional runner)")
    ap.add_argument(
        "--ohlcv",
        type=Path,
        default=REPO_ROOT / "finance_agent" / "btc_specialist" / "data" / "btc_1h_ohlcv.json",
        help="1h Bybit OHLCV JSON (Sygnif / Nautilus sink)",
    )
    ap.add_argument("--symbol", default="BTC", help="Base symbol for spot ticker (BTC -> BTCUSDT)")
    ap.add_argument("--runner-weight", type=float, default=0.35, help="Blend weight for runner signal (0–0.55)")
    ap.add_argument("--output", type=Path, default=REPO_ROOT / "prediction_agent" / "btc_24h_movement_prediction.json")
    ap.add_argument("--no-runner", action="store_true", help="Ignore btc_prediction_output.json")
    ap.add_argument("--quiet", action="store_true", help="No stdout (still writes JSON)")
    args = ap.parse_args()

    if not args.ohlcv.is_file():
        print(f"Missing OHLCV: {args.ohlcv}", file=sys.stderr)
        return 1

    h, l, c = load_ohlcv_hlc(args.ohlcv)
    atrb = atr_pct_bar(h, l, c)
    bars_24 = ph.bars_for_horizon(24, "60")
    emp = ph.compute_movement_probability_metrics(c, bars_24, atr_pct_bar=atrb)
    if not emp:
        print("Insufficient history for 24h empirical metrics.", file=sys.stderr)
        return 1

    sym = args.symbol.upper()
    if not sym.endswith("USDT"):
        sym_t = f"{sym}USDT"
    else:
        sym_t = sym

    spot = ph._spot_price(sym)  # noqa: SLF001

    runner = None if args.no_runner else load_runner_signal(REPO_ROOT)
    p_up = float(emp["p_up"])
    p_down = float(emp["p_down"])
    syn = synthesize(p_up, p_down, runner, args.runner_weight)

    out = {
        "schema_version": 1,
        "symbol": sym_t,
        "horizon_hours": 24,
        "interval": "60",
        "generated_utc": _utc_iso(),
        "spot_usd": round(spot, 2),
        "data": {
            "ohlcv_path": str(args.ohlcv.resolve()),
            "n_closes": int(len(c)),
            "atr_pct_bar": round(atrb, 6),
        },
        "empirical_24h": emp,
        "runner_snapshot": runner,
        "synthesis": syn,
        "disclaimer": (
            "Empirical forward-return frequencies on past 1h data + optional short-horizon model blend; "
            "not investment advice. Regimes shift."
        ),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if not args.quiet:
        s = syn
        print(f"BTC 24h outlook @ {out['generated_utc']}  spot=${out['spot_usd']:,.2f}")
        print(
            f"  Empirical: p_up={emp['p_up']} p_down={emp['p_down']} "
            f"median_fwd={emp.get('median_fwd_return_pct')}% "
            f"p90|move|={emp.get('p90_abs_fwd_pct')}%"
        )
        if runner:
            print(
                f"  Runner: consensus={runner.get('consensus')} "
                f"dir={runner.get('direction_label')} "
                f"conf={runner.get('direction_confidence_pct')}%"
            )
        print(
            f"  Synthesis: bias={s['bias_24h']} blended_p_up={s['p_up_blended']} "
            f"confidence={s['confidence_0_100']}/100"
        )
        print(f"  Wrote {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
