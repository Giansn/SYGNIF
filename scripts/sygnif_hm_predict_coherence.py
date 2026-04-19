#!/usr/bin/env python3
"""
Read ``btc_prediction_output.json`` (+ optional ``neurolinked_swarm_channel.json``)
and print **coherence flags**: trees vs logreg vs Hivemind vote vs enhanced label.

No network; safe to cron. Defaults: repo ``prediction_agent/`` paths next to ``scripts/``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        o = json.loads(path.read_text(encoding="utf-8"))
        return o if isinstance(o, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _f(x: Any) -> float | None:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="HM / BTC predict coherence check (local JSON).")
    ap.add_argument(
        "--predict-json",
        type=Path,
        default=None,
        help="Path to btc_prediction_output.json (default: <repo>/prediction_agent/btc_prediction_output.json)",
    )
    ap.add_argument(
        "--channel-json",
        type=Path,
        default=None,
        help="Path to neurolinked_swarm_channel.json (optional)",
    )
    ap.add_argument("--json", action="store_true", help="Emit one JSON object instead of text.")
    args = ap.parse_args()
    root = _repo_root()
    pred_path = args.predict_json or (root / "prediction_agent" / "btc_prediction_output.json")
    chan_path = args.channel_json or (root / "prediction_agent" / "neurolinked_swarm_channel.json")

    pred = _load(pred_path)
    if not pred:
        print(f"missing_or_invalid:{pred_path}", file=sys.stderr)
        return 2

    pr = pred.get("predictions") if isinstance(pred.get("predictions"), dict) else {}
    rf = pr.get("random_forest") if isinstance(pr.get("random_forest"), dict) else {}
    xg = pr.get("xgboost") if isinstance(pr.get("xgboost"), dict) else {}
    dlr = pr.get("direction_logistic") if isinstance(pr.get("direction_logistic"), dict) else {}
    hm = pr.get("hivemind") if isinstance(pr.get("hivemind"), dict) else {}

    rf_d = _f(rf.get("delta"))
    xg_d = _f(xg.get("delta"))
    lr_label = str(dlr.get("label") or "").strip().upper()
    lr_conf = _f(dlr.get("confidence"))
    logreg_up = lr_label == "UP"

    votes_up: list[bool] = []
    if rf_d is not None:
        votes_up.append(rf_d > 0)
    if xg_d is not None:
        votes_up.append(xg_d > 0)
    votes_up.append(logreg_up)
    consensus_up = sum(1 for v in votes_up if v)

    consensus = str(pr.get("consensus") or "").strip().upper()
    enhanced = str(pr.get("consensus_nautilus_enhanced") or "").strip().upper()
    try:
        hm_vote = int(hm.get("vote") or 0)
    except (TypeError, ValueError):
        hm_vote = 0

    meta = pred.get("nautilus_consensus_meta") if isinstance(pred.get("nautilus_consensus_meta"), dict) else {}
    hm_note = str(meta.get("hivemind_prediction_note") or "")

    na = pred.get("nautilus_research") if isinstance(pred.get("nautilus_research"), dict) else {}
    side = na.get("sidecar_signal") if isinstance(na.get("sidecar_signal"), dict) else {}
    side_bias = str(side.get("bias") or "").strip().lower()

    trees_agree = (rf_d is not None and xg_d is not None and (rf_d > 0) == (xg_d > 0))
    trees_bull = rf_d is not None and xg_d is not None and rf_d > 0 and xg_d > 0
    trees_bear = rf_d is not None and xg_d is not None and rf_d < 0 and xg_d < 0
    logreg_bull = logreg_up
    trees_vs_logreg = None
    if rf_d is not None and xg_d is not None and lr_label in ("UP", "DOWN"):
        same = (trees_bull and logreg_bull) or (trees_bear and not logreg_bull)
        split = (trees_bull and not logreg_bull) or (trees_bear and logreg_bull)
        if trees_bull or trees_bear:
            trees_vs_logreg = "aligned" if same else ("conflict" if split else "mixed_trees")

    majority_bull = consensus_up >= 2
    majority_bear = consensus_up <= 1
    hm_bullish = hm_vote >= 1
    hm_bearish = hm_vote <= -1
    enhanced_bull = enhanced in ("BULLISH", "STRONG_BULLISH")
    enhanced_bear = enhanced in ("BEARISH", "STRONG_BEARISH")

    strong_boost_eligible = (
        enhanced == "BULLISH"
        and consensus_up >= 2
        and logreg_up
        and consensus == "BULLISH"
        and hm_vote >= 1
    )

    out: dict[str, Any] = {
        "predict_json": str(pred_path),
        "generated_utc": pred.get("generated_utc"),
        "current_close": pred.get("current_close"),
        "rf_delta": rf_d,
        "xgb_delta": xg_d,
        "direction_logistic": {"label": lr_label, "confidence": lr_conf},
        "consensus_up_votes": consensus_up,
        "consensus": consensus,
        "consensus_nautilus_enhanced": enhanced,
        "hivemind_vote": hm_vote,
        "hivemind_prediction_note": hm_note or None,
        "nautilus_sidecar_bias": side_bias or None,
        "flags": {
            "trees_agree_same_sign": trees_agree,
            "trees_vs_logreg": trees_vs_logreg,
            "hm_agrees_majority_bull": hm_bullish and majority_bull,
            "hm_agrees_majority_bear": hm_bearish and majority_bear,
            "hm_aligned_enhanced_bull": hm_bullish and enhanced_bull,
            "hm_aligned_enhanced_bear": hm_bearish and enhanced_bear,
            "strong_bull_boost_eligible": strong_boost_eligible,
        },
    }

    ch = _load(chan_path) if chan_path else {}
    if ch:
        out["channel_json"] = str(chan_path)
        out["channel_swarm_label"] = ch.get("swarm_label")
        out["channel_swarm_mean"] = ch.get("swarm_mean")
        out["channel_swarm_conflict"] = ch.get("swarm_conflict")
        lab = str(ch.get("swarm_label") or "").upper()
        if enhanced and lab.startswith("SWARM_"):
            core = lab.replace("SWARM_", "")
            e2 = enhanced.replace("STRONG_", "")
            out["flags"]["channel_vs_enhanced_mismatch"] = (
                (core in ("BULL", "MIXED") and e2 == "BEARISH")
                or (core == "BEAR" and e2 == "BULLISH")
            )

    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0

    fl = out["flags"]
    print(f"file: {pred_path}")
    print(f"generated_utc: {out.get('generated_utc')}")
    print(f"close: {out.get('current_close')}")
    print(f"RFΔ={rf_d}  XGBΔ={xg_d}  logreg={lr_label}/{lr_conf}%  consensus_up={consensus_up}")
    print(f"consensus={consensus}  enhanced={enhanced}  hm_vote={hm_vote}  hm_note={hm_note or '-'}")
    print(f"nautilus_sidecar_bias={side_bias or '-'}")
    print("flags:")
    for k, v in sorted(fl.items()):
        print(f"  {k}: {v}")
    if "channel_swarm_label" in out:
        print(f"channel: label={out.get('channel_swarm_label')} mean={out.get('channel_swarm_mean')} "
              f"conflict={out.get('channel_swarm_conflict')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
