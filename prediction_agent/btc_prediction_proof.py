#!/usr/bin/env python3
"""
When the 1h bar *after* ``last_candle_utc`` exists in ``btc_1h_ohlcv.json``,
compare realized direction vs ``btc_prediction_output.json`` (LogReg label)
and append one JSON line to ``btc_prediction_proof_log.jsonl`` (idempotent).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "finance_agent" / "btc_specialist" / "data"
PRED_PATH = SCRIPT_DIR / "btc_prediction_output.json"
LOG_PATH = SCRIPT_DIR / "btc_prediction_proof_log.jsonl"


def _parse_pred_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def main() -> int:
    if not PRED_PATH.exists():
        print("btc_prediction_proof: no btc_prediction_output.json", flush=True)
        return 0
    ohlcv_path = DATA_DIR / "btc_1h_ohlcv.json"
    if not ohlcv_path.exists():
        print("btc_prediction_proof: no btc_1h_ohlcv.json", flush=True)
        return 0

    with open(PRED_PATH, encoding="utf-8") as f:
        pred = json.load(f)
    last_s = pred.get("last_candle_utc")
    gen_s = pred.get("generated_utc")
    if not last_s or not gen_s:
        return 0

    proof_key = f"{last_s}|{gen_s}"
    if LOG_PATH.exists():
        tail = LOG_PATH.read_text(encoding="utf-8")[-8000:]
        for line in tail.splitlines():
            if not line.strip():
                continue
            try:
                prev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if prev.get("proof_key") == proof_key:
                print("btc_prediction_proof: already logged", proof_key, flush=True)
                return 0

    with open(ohlcv_path, encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list) or len(rows) < 2:
        return 0

    target_ms = int(_parse_pred_ts(last_s).timestamp() * 1000)
    j = None
    for idx, r in enumerate(rows):
        if not isinstance(r, dict) or "t" not in r:
            continue
        if int(r["t"]) == target_ms:
            j = idx
            break
    if j is None:
        print("btc_prediction_proof: last_candle not in OHLCV", last_s, flush=True)
        return 0
    if j + 1 >= len(rows):
        print("btc_prediction_proof: next bar not yet in feed", flush=True)
        return 0

    c0 = float(rows[j]["c"])
    c1 = float(rows[j + 1]["c"])
    h1 = float(rows[j + 1]["h"])
    l1 = float(rows[j + 1]["l"])
    actual_mean = (h1 + l1) / 2.0
    actual_up = c1 > c0

    plab = (pred.get("predictions") or {}).get("direction_logistic") or {}
    label = str(plab.get("label") or "").upper()
    pred_up = label == "UP"

    rec = {
        "proof_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "proof_key": proof_key,
        "last_candle_utc": last_s,
        "prediction_generated_utc": gen_s,
        "predicted_direction_up": pred_up,
        "actual_direction_up": actual_up,
        "direction_correct": pred_up == actual_up,
        "actual_close_next": round(c1, 2),
        "actual_mean_next": round(actual_mean, 2),
        "pred_rf_next_mean": (pred.get("predictions") or {}).get("random_forest", {}).get("next_mean"),
        "pred_xgb_next_mean": (pred.get("predictions") or {}).get("xgboost", {}).get("next_mean"),
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as lf:
        lf.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps({"btc_prediction_proof": rec["direction_correct"], "proof_key": proof_key}), flush=True)

    if os.environ.get("RULE_TAG_JOURNAL", "").lower() in ("1", "true", "yes", "on"):
        try:
            from rule_tag_journal import append_event

            append_event(
                "prediction_proof",
                detail={"proof_key": proof_key, "direction_correct": rec["direction_correct"]},
            )
        except Exception as exc:  # noqa: BLE001
            print(f"btc_prediction_proof: rule_tag_journal skipped ({exc})", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
