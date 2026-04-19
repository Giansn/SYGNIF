#!/usr/bin/env python3
"""
Export a **BTC predicted move** sidecar JSON for **Bybit API demo** consumers (dashboards, nodes, scripts).

**Governance:** directional signal is **active** only when swarm checks pass and
``governance_probability_pct >= SYGNIF_BYBIT_DEMO_GOVERNANCE_MIN_PROB`` (default **75**).

- **Output:** ``prediction_agent/bybitapidemo_btc_predicted_move_signal.json`` (override
  ``SYGNIF_BYBIT_DEMO_SIGNAL_JSON``).
- **Swarm:** ``finance_agent.swarm_knowledge.compute_swarm()`` — no demo REST writes.

Env:
  SYGNIF_BYBIT_DEMO_GOVERNANCE_MIN_PROB — minimum %% (default 75)
  SYGNIF_BYBIT_DEMO_SIGNAL_JSON — output path
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_GOVERNANCE_MIN_PCT = 75.0
DEFAULT_SIGNAL_FILENAME = "bybitapidemo_btc_predicted_move_signal.json"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _prediction_agent_dir(repo_root: Path | None = None) -> Path:
    base = repo_root or _repo_root()
    for key in ("PREDICTION_AGENT_DIR", "SYGNIF_PREDICTION_AGENT_DIR"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return Path(raw).expanduser()
    return base / "prediction_agent"


def signal_output_path(repo_root: Path | None = None) -> Path:
    raw = (os.environ.get("SYGNIF_BYBIT_DEMO_SIGNAL_JSON") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _prediction_agent_dir(repo_root) / DEFAULT_SIGNAL_FILENAME


def _governance_min_pct() -> float:
    raw = (os.environ.get("SYGNIF_BYBIT_DEMO_GOVERNANCE_MIN_PROB") or "").strip()
    if not raw:
        return DEFAULT_GOVERNANCE_MIN_PCT
    try:
        return max(0.0, min(100.0, float(raw)))
    except ValueError:
        return DEFAULT_GOVERNANCE_MIN_PCT


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def governance_probability_pct(
    training: dict[str, Any] | None,
    pred: dict[str, Any] | None,
) -> tuple[float, str]:
    """Return (0–100 probability, source id)."""
    if training:
        rec = training.get("recognition") if isinstance(training.get("recognition"), dict) else {}
        try:
            up = float(rec.get("last_bar_probability_up_pct") or 0.0)
            dn = float(rec.get("last_bar_probability_down_pct") or 0.0)
        except (TypeError, ValueError):
            up, dn = 0.0, 0.0
        m = max(up, dn)
        if m > 0.0:
            return m, "training_channel_max(up,down)"
    if pred:
        pr = pred.get("predictions") if isinstance(pred.get("predictions"), dict) else {}
        dlr = pr.get("direction_logistic") if isinstance(pr.get("direction_logistic"), dict) else {}
        try:
            conf = float(dlr.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        if conf > 0.0:
            return conf, "direction_logistic_confidence"
    return 0.0, "none"


def predicted_move_from_swarm(swarm: dict[str, Any]) -> tuple[str, str]:
    label = str(swarm.get("swarm_label") or "")
    if label == "SWARM_BULL":
        return "up", label
    if label == "SWARM_BEAR":
        return "down", label
    return "flat", label or "unknown"


def evaluate_swarm_governance(
    swarm: dict[str, Any],
    *,
    prob_pct: float,
    min_pct: float,
) -> tuple[bool, list[str]]:
    ok = True
    reasons: list[str] = []
    if swarm.get("swarm_conflict"):
        ok = False
        reasons.append("swarm_conflict")
    move, label = predicted_move_from_swarm(swarm)
    if move == "flat":
        ok = False
        reasons.append(f"swarm_not_directional:{label}")
    if prob_pct + 1e-9 < min_pct:
        ok = False
        reasons.append(f"governance_probability_{prob_pct:.2f}_lt_min_{min_pct:.2f}")
    return ok, reasons


def build_signal_payload(
    repo_root: Path | None = None,
    *,
    swarm: dict[str, Any] | None = None,
    training: dict[str, Any] | None = None,
    pred: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = repo_root or _repo_root()
    pa = _prediction_agent_dir(root)

    if swarm is None:
        root_path = root
        if str(root_path) not in sys.path:
            sys.path.insert(0, str(root_path))
        fa = root_path / "finance_agent"
        if str(fa) not in sys.path:
            sys.path.insert(0, str(fa))
        import swarm_knowledge as sk  # noqa: PLC0415

        swarm = sk.compute_swarm()

    if training is None:
        training = _read_json(pa / "training_channel_output.json")
    if pred is None:
        pred = _read_json(pa / "btc_prediction_output.json")

    min_pct = _governance_min_pct()
    prob_pct, prob_src = governance_probability_pct(training, pred)
    move, swarm_lbl = predicted_move_from_swarm(swarm)
    gov_ok, gov_reasons = evaluate_swarm_governance(swarm, prob_pct=prob_pct, min_pct=min_pct)

    active = gov_ok and move in ("up", "down")
    sym = os.environ.get("SYGNIF_BYBIT_DEMO_SIGNAL_SYMBOL", "BTCUSDT").strip().upper() or "BTCUSDT"

    return {
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "venue": "bybit_api_demo",
        "consumer_tag": "bybitapidemo",
        "symbol_linear": sym,
        "predicted_move": move,
        "signal_active": active,
        "governance": {
            "min_probability_pct": min_pct,
            "governance_probability_pct": round(prob_pct, 4),
            "governance_probability_source": prob_src,
            "passed": gov_ok,
            "reasons": gov_reasons,
        },
        "swarm": {
            "swarm_mean": swarm.get("swarm_mean"),
            "swarm_label": swarm.get("swarm_label"),
            "swarm_conflict": swarm.get("swarm_conflict"),
            "sources_n": swarm.get("sources_n"),
        },
    }


def write_signal_json(repo_root: Path | None = None) -> tuple[Path, dict[str, Any]]:
    payload = build_signal_payload(repo_root)
    dest = signal_output_path(repo_root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(dest)
    return dest, payload


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Write Bybit demo BTC predicted-move signal JSON")
    ap.add_argument("--print-json", action="store_true", help="Print payload to stdout")
    args = ap.parse_args()
    path, payload = write_signal_json()
    if args.print_json:
        print(json.dumps(payload, indent=2))
    else:
        print(json.dumps({"ok": True, "path": str(path), "signal_active": payload.get("signal_active")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
