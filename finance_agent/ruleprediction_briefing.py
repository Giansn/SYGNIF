#!/usr/bin/env python3
"""
Optional /briefing appendices: BTC-0.1 rule governance (R01/R02/R03) + compact ML runner line.

Env:
  SYGNIF_BRIEFING_INCLUDE_RULE_PREDICT=1 — R01/R02/R03 lines when training_channel or registry mtimes change (in-process cache).
  SYGNIF_BRIEFING_INCLUDE_BTC_PREDICT=1 — one line from btc_prediction_output.json if file is fresh.
  SYGNIF_BRIEFING_BTC_PREDICT_MAX_AGE_H — max age hours for predict line (default 24).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

_prev_rule_sig: tuple[int, int] | None = None


def _prediction_agent_dir() -> Path:
    for key in ("PREDICTION_AGENT_DIR", "SYGNIF_PREDICTION_AGENT_DIR"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return Path(raw).expanduser()
    here = Path(__file__).resolve().parent
    cand = here.parent / "prediction_agent"
    return cand


def _letscrash_dir() -> Path:
    here = Path(__file__).resolve().parent
    return here.parent / "letscrash"


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes", "on")


def _rule_lines_on_mtime_change(*, max_chars: int) -> str:
    """Up to two compact lines; only when training/registry files change since last call."""
    global _prev_rule_sig
    if not _env_truthy("SYGNIF_BRIEFING_INCLUDE_RULE_PREDICT"):
        return ""
    pa = _prediction_agent_dir()
    train = pa / "training_channel_output.json"
    reg = _letscrash_dir() / "btc_strategy_0_1_rule_registry.json"
    if not train.exists():
        return ""
    try:
        mt_train = train.stat().st_mtime_ns
    except OSError:
        return ""
    mt_reg = reg.stat().st_mtime_ns if reg.exists() else 0
    sig = (mt_train, mt_reg)
    if _prev_rule_sig == sig:
        return ""
    _prev_rule_sig = sig
    try:
        data = json.loads(train.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    gen = str(data.get("generated_utc", "?"))[:24]
    align = data.get("predict_runner_alignment") or {}
    aligned = align.get("aligned_to_runner_generated_utc")
    if aligned is True:
        al = "runnerUTC=trainUTC"
    elif aligned is False:
        al = "runnerUTC≠trainUTC"
    else:
        al = "align:legacy-rerun-channel"
    line1 = f"BTC0.1-R01|train={gen}|{al}|L0:next-bar channel JSON≠widen long risk"
    line2 = "BTC0.1-R02|governance:HTF regime/dump script before LTF pine overlays"
    line3 = "BTC0.1-R03|sleeve:engine pullback proxy;Pine=BullByte ref only;horizon→journal before L3 FT"
    out = line1 + "\n" + line2 + "\n" + line3
    if len(out) > max_chars:
        out = out[: max_chars - 3] + "..."
    return out


def _btc_predict_line(*, max_chars: int) -> str:
    if not _env_truthy("SYGNIF_BRIEFING_INCLUDE_BTC_PREDICT"):
        return ""
    try:
        max_h = float(os.environ.get("SYGNIF_BRIEFING_BTC_PREDICT_MAX_AGE_H", "24"))
    except ValueError:
        max_h = 24.0
    pred = _prediction_agent_dir() / "btc_prediction_output.json"
    if not pred.exists():
        return ""
    try:
        age_s = time.time() - pred.stat().st_mtime
    except OSError:
        return ""
    if age_s > max(1.0, max_h * 3600.0):
        return ""
    try:
        data = json.loads(pred.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    gen = str(data.get("generated_utc", "?"))[:19]
    pr = data.get("predictions") or {}
    cons = pr.get("consensus", "?")
    dlr = pr.get("direction_logistic") or {}
    lab = dlr.get("label", "?")
    line = f"BTC_PRED|utc={gen}|consensus={cons}|dirLR={lab}"
    if len(line) > max_chars:
        line = line[: max_chars - 3] + "..."
    return line


def extra_briefing_lines(*, max_chars: int = 320) -> str:
    """Pipe-friendly block (no leading newline). Empty if disabled or nothing to add."""
    parts: list[str] = []
    budget = max_chars
    rule = _rule_lines_on_mtime_change(max_chars=budget)
    if rule:
        parts.append(rule)
        budget -= len(rule) + 1
    pred = _btc_predict_line(max_chars=max(80, budget)) if budget > 80 else ""
    if pred:
        parts.append(pred)
    body = "\n".join(parts).strip()
    if len(body) > max_chars:
        body = body[: max_chars - 3] + "..."
    return body
