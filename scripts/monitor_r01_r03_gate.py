#!/usr/bin/env python3
"""
What-if monitor for **BTC-0.1-R01–R03** gates vs live training artifacts.

Read-only: loads ``prediction_agent/training_channel_output.json``,
``btc_prediction_output.json``, and (for **R03** sleeve math) 1h OHLCV
via ``btc_predict_runner`` — same sources as ``channel_training`` / Freqtrade engine.

Exit code **0** always unless ``--strict-stale`` and training JSON is older than max age.

Environment:
  ``RULE_TAG_JOURNAL_MONITOR=YES`` — append one CSV row via ``rule_tag_journal.append_r01_r03_monitor_event``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _parse_iso_utc(s: str | None) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Monitor R01–R03 training / pattern gates (read-only)")
    ap.add_argument("--json", action="store_true", help="print one JSON object to stdout")
    ap.add_argument(
        "--strict-stale",
        action="store_true",
        help="exit 2 if training_channel_output.json is older than --max-age-hours",
    )
    ap.add_argument("--max-age-hours", type=float, default=48.0, help="with --strict-stale")
    args = ap.parse_args()

    root = _repo_root()
    pa = root / "prediction_agent"
    sys.path.insert(0, str(pa))
    sys.path.insert(0, str(root / "user_data" / "strategies"))

    import btc_predict_runner as bpr  # noqa: E402
    from btc_strategy_0_1_engine import (  # noqa: E402
        r01_training_runner_bearish,
        r03_pullback_long,
        training_channel_path,
    )

    ch_path = training_channel_path()
    training: dict = {}
    if ch_path.exists():
        training = json.loads(ch_path.read_text(encoding="utf-8"))
    rec = training.get("recognition") or {}
    p_down = float(rec.get("last_bar_probability_down_pct") or 0.0)
    p_up = float(rec.get("last_bar_probability_up_pct") or 0.0)
    snap = rec.get("btc_predict_runner_snapshot") or {}
    pred = snap.get("predictions") or {}
    consensus = str(pred.get("consensus", "") or "").upper()

    r01_bearish_stack = r01_training_runner_bearish()
    r01_condition_p_down = p_down >= 90.0
    r01_condition_consensus = consensus == "BEARISH"
    r01_blocks_aggressive_timing = bool(r01_bearish_stack)

    ohlcv = Path(bpr.DATA_DIR) / "btc_1h_ohlcv.json"
    r03_pattern = None
    r03_error = None
    df = None
    if ohlcv.exists():
        try:
            df = bpr.load_bybit_ohlcv(str(ohlcv))
            df = bpr.add_ta_features(df)
            df["close"] = df["Close"].astype(float)
            r03_pattern = bool(r03_pullback_long(df))
        except Exception as e:  # noqa: BLE001
            r03_error = str(e)
    else:
        r03_error = "missing btc_1h_ohlcv.json"

    r02_trend_long = None
    r02_skip = None
    try:
        from btc_trend_regime import btc_trend_long_row

        if df is not None and len(df) > 0:
            last = df.iloc[-1]
            need = ("RSI_14_1h", "RSI_14_4h", "EMA_200_1h", "ADX_14")
            if all(c in last.index for c in need):
                r02_trend_long = bool(btc_trend_long_row(last))
            else:
                r02_skip = (
                    "missing MTF cols (RSI_14_1h / RSI_14_4h / EMA_200_1h / ADX_14) — "
                    "not in btc_predict_runner TA; use Freqtrade/MS2 dataframe for full R02"
                )
    except Exception as e:  # noqa: BLE001
        r02_skip = str(e)

    gen = training.get("generated_utc") or training.get("channel_completed_utc")
    gen_dt = _parse_iso_utc(gen) if isinstance(gen, str) else None
    age_hours = None
    stale = False
    if gen_dt:
        age_hours = (datetime.now(timezone.utc) - gen_dt).total_seconds() / 3600.0
        stale = age_hours > args.max_age_hours

    out = {
        "checked_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "training_channel_path": str(ch_path),
        "training_generated_utc": gen,
        "training_age_hours": round(age_hours, 2) if age_hours is not None else None,
        "training_stale_vs_max_hours": stale,
        "recognition_last_bar_p_down_pct": p_down,
        "recognition_last_bar_p_up_pct": p_up,
        "runner_consensus": consensus or None,
        "r01_bearish_stack": r01_bearish_stack,
        "r01_subconditions": {
            "p_down_ge_90": r01_condition_p_down,
            "consensus_bearish": r01_condition_consensus,
        },
        "r01_would_block_aggressive_long_timing": r01_blocks_aggressive_timing,
        "r02_btc_trend_long_last_bar": r02_trend_long,
        "r02_eval_note": r02_skip,
        "r03_scalp_pullback_pattern_last_bar": r03_pattern,
        "r03_eval_error": r03_error,
        "references": {
            "engine": "user_data/strategies/btc_strategy_0_1_engine.py",
            "r02_regime": "user_data/strategies/btc_trend_regime.py",
            "docs": "docs/btc_expertise_proven_formulas.md",
        },
    }

    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(
            f"[r01-r03-monitor] training={ch_path.name} age_h={out['training_age_hours']} "
            f"stale={stale} | R01_stack={r01_bearish_stack} (p_down>={90}: {r01_condition_p_down}, "
            f"consensus_BEARISH: {r01_condition_consensus}) | R03_pattern={r03_pattern} | "
            f"R02_trend={r02_trend_long} ({r02_skip or 'ok'})",
            flush=True,
        )

    if os.environ.get("RULE_TAG_JOURNAL_MONITOR", "").strip().upper() == "YES":
        try:
            from rule_tag_journal import append_r01_r03_monitor_event  # noqa: E402

            append_r01_r03_monitor_event(out)
        except Exception as exc:  # noqa: BLE001
            print(f"[r01-r03-monitor] journal: {exc}", file=sys.stderr, flush=True)

    if args.strict_stale and stale:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
