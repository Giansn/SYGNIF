#!/usr/bin/env python3
"""
**Swarm auto-improvement flow** — observe → persist deltas → surface hints (no autonomous orders).

Each run (or loop):

1. ``compute_swarm()`` (+ optional Nautilus fusion sidecar refresh).
2. Compare to last ``prediction_agent/swarm_auto_improvement_state.json`` (mean, label, conflict, source votes).
3. Append one JSON line to ``prediction_agent/swarm_auto_improvement_history.jsonl`` (trimmed).
4. Write fresh state + short **hints** for operators (e.g. sustained conflict, mean flip).
5. Optional **weak-points bundle** (``finance_agent/swarm_weak_points_solution``): live ``compute_swarm`` digest,
   demo closed-PnL tail, predict-loop gate stats → ``weak_points`` in state + history; persists
   ``prediction_agent/swarm_weak_points_latest.json``.
6. Optional **demo runtime hints** (``SYGNIF_SWARM_IMPROVEMENT_AUTO_DEMO_TUNING=1``): writes
   ``prediction_agent/swarm_demo_runtime_hints.json`` for the predict launcher when
   ``SYGNIF_SWARM_RUNTIME_HINTS_APPLY=1`` (whitelist env overrides — see ``swarm_improvement_runtime.py``).
   Multi-day windows: set ``SYGNIF_SWARM_RUNTIME_HINTS_TTL_HOURS`` (e.g. **72**); long-running loops should set
   ``SYGNIF_SWARM_RUNTIME_HINTS_RELOAD_EACH_ITER=1`` in ``btc_predict_protocol_loop`` so hints are re-read from disk.

This complements ``finance_agent/auto_improvement_workflow.md`` (Freqtrade/cron path) with a **Swarm-native**
telemetry trail for gates, fusion, and briefing tuning — **not** a strategy auto-writer.

Env:

- ``SYGNIF_PREDICTION_AGENT_DIR`` — prediction_agent root (default: repo ``prediction_agent``).
- ``SYGNIF_SWARM_IMPROVEMENT_FUSION_SYNC=1`` — call ``write_fused_sidecar`` before ``compute_swarm``.
- ``SYGNIF_SWARM_IMPROVEMENT_HISTORY_MAX`` — max lines kept in history JSONL (default **5000**).
- ``SYGNIF_SWARM_IMPROVEMENT_WEAK_POINTS`` — default **on**: attach weak-points telemetry + latest JSON.
- ``SYGNIF_SWARM_IMPROVEMENT_AUTO_DEMO_TUNING`` — default **off**: when **on**, emit ``swarm_demo_runtime_hints.json``.
- ``SYGNIF_SWARM_OPEN_TRADES`` — passed through indirectly via ``compute_swarm`` embed (default on in module).

Examples::

  cd ~/SYGNIF && python3 scripts/swarm_auto_improvement_flow.py
  SYGNIF_SWARM_IMPROVEMENT_FUSION_SYNC=1 python3 scripts/swarm_auto_improvement_flow.py --json
  SWARM_IMPROVE_INTERVAL_SEC=300 python3 scripts/swarm_auto_improvement_flow.py --loop
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def _pa_dir() -> Path:
    raw = (os.environ.get("SYGNIF_PREDICTION_AGENT_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _repo() / "prediction_agent"


def _state_path() -> Path:
    return _pa_dir() / "swarm_auto_improvement_state.json"


def _history_path() -> Path:
    return _pa_dir() / "swarm_auto_improvement_history.jsonl"


def _env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _history_max_lines() -> int:
    try:
        return max(100, int(float(os.environ.get("SYGNIF_SWARM_IMPROVEMENT_HISTORY_MAX", "5000"))))
    except ValueError:
        return 5000


def _vote_digest(sources: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not isinstance(sources, dict):
        return out
    for name, blob in sources.items():
        if isinstance(blob, dict):
            out[name] = {"vote": blob.get("vote"), "detail": blob.get("detail")}
    return out


def _hints(prev: dict[str, Any] | None, cur: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    if not prev:
        return ["first_run_baseline_saved"]
    try:
        pm = float(prev.get("swarm_mean") or 0.0)
        cm = float(cur.get("swarm_mean") or 0.0)
        if pm * cm < 0 and abs(pm) > 0.05 and abs(cm) > 0.05:
            hints.append("swarm_mean_sign_flip_vs_last")
    except (TypeError, ValueError):
        pass
    if cur.get("swarm_conflict") and prev.get("swarm_conflict"):
        hints.append("conflict_persisted_review_ta_vs_ml_ch")
    elif cur.get("swarm_conflict") and not prev.get("swarm_conflict"):
        hints.append("conflict_new_check_channel_ta_alignment")
    if str(cur.get("swarm_label") or "") != str(prev.get("swarm_label") or ""):
        hints.append(f"label_change:{prev.get('swarm_label')}→{cur.get('swarm_label')}")
    pv = prev.get("vote_digest") or {}
    cv = cur.get("vote_digest") or {}
    if isinstance(pv, dict) and isinstance(cv, dict):
        for k in set(pv) | set(cv):
            if pv.get(k) != cv.get(k):
                hints.append(f"source_shift:{k}")
    return hints


def _trim_jsonl(path: Path, max_lines: int) -> None:
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= max_lines:
        return
    keep = lines[-max_lines:]
    try:
        path.write_text("\n".join(keep) + "\n", encoding="utf-8")
    except OSError:
        pass


def _atomic_write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_once(*, as_json: bool) -> dict[str, Any]:
    sys.path.insert(0, str(_repo() / "finance_agent"))
    import swarm_knowledge as sk  # noqa: PLC0415

    repo = _repo()
    if _env_truthy("SYGNIF_SWARM_IMPROVEMENT_FUSION_SYNC"):
        try:
            sys.path.insert(0, str(repo / "prediction_agent"))
            from nautilus_protocol_fusion import write_fused_sidecar  # noqa: PLC0415

            write_fused_sidecar(repo_root=repo)
        except Exception as exc:
            if not as_json:
                print(f"[swarm-improve] fusion_sync skipped: {exc}", file=sys.stderr)

    swarm = sk.compute_swarm()
    prev: dict[str, Any] | None = None
    sp = _state_path()
    if sp.is_file():
        try:
            prev = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prev = None

    digest = _vote_digest(swarm.get("sources") if isinstance(swarm.get("sources"), dict) else None)
    row_core = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "swarm_mean": swarm.get("swarm_mean"),
        "swarm_label": swarm.get("swarm_label"),
        "swarm_conflict": swarm.get("swarm_conflict"),
        "sources_n": swarm.get("sources_n"),
        "missing_files": swarm.get("missing_files"),
        "vote_digest": digest,
    }
    hints = _hints(prev, {**row_core, "vote_digest": digest})
    row: dict[str, Any] = {**row_core, "hints": hints}

    if _env_truthy("SYGNIF_SWARM_IMPROVEMENT_WEAK_POINTS", default=True):
        try:
            from swarm_instance_paths import apply_swarm_instance_env  # noqa: PLC0415

            envf = repo / "swarm_operator.env"
            apply_swarm_instance_env(repo, extra_env_file=envf if envf.is_file() else None)
            from swarm_improvement_runtime import (  # noqa: PLC0415
                build_demo_runtime_hints,
                compact_weak_points_for_state,
                write_demo_runtime_hints,
                write_weak_points_latest,
            )
            from swarm_weak_points_solution import build_swarm_weak_points_bundle  # noqa: PLC0415

            bundle = build_swarm_weak_points_bundle(repo)
            row["weak_points"] = compact_weak_points_for_state(bundle)
            write_weak_points_latest(repo, bundle)
            if _env_truthy("SYGNIF_SWARM_IMPROVEMENT_AUTO_DEMO_TUNING"):
                hints_obj = build_demo_runtime_hints(bundle)
                write_demo_runtime_hints(repo, hints_obj)
        except Exception as exc:  # noqa: BLE001
            row["weak_points_error"] = str(exc)[:240]

    hp = _history_path()
    try:
        hp.parent.mkdir(parents=True, exist_ok=True)
        with hp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass
    _trim_jsonl(hp, _history_max_lines())

    state_out: dict[str, Any] = {
        "schema_version": 1,
        "updated_utc": row["ts"],
        "last_swarm": {
            "swarm_mean": swarm.get("swarm_mean"),
            "swarm_label": swarm.get("swarm_label"),
            "swarm_conflict": swarm.get("swarm_conflict"),
            "sources_n": swarm.get("sources_n"),
            "missing_files": swarm.get("missing_files"),
        },
        "vote_digest": digest,
        "last_hints": hints,
        "history_path": str(hp),
    }
    if "weak_points" in row:
        state_out["weak_points"] = row["weak_points"]
    if "weak_points_error" in row:
        state_out["weak_points_error"] = row["weak_points_error"]
    _atomic_write_json(sp, state_out)

    out = {"ok": True, "row": row, "state_path": str(sp)}
    if as_json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(f"[swarm-improve] mean={row_core.get('swarm_mean')} label={row_core.get('swarm_label')} "
              f"conflict={row_core.get('swarm_conflict')} hints={hints}")
        print(f"[swarm-improve] state={sp} history={hp}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Swarm observe → state + history (auto-improvement telemetry)")
    ap.add_argument("--json", action="store_true", help="Print one JSON object")
    ap.add_argument(
        "--loop",
        action="store_true",
        help="Repeat forever; sleep SWARM_IMPROVE_INTERVAL_SEC (default 300)",
    )
    args = ap.parse_args()
    if not args.loop:
        run_once(as_json=args.json)
        return 0
    try:
        interval = max(15, int(os.environ.get("SWARM_IMPROVE_INTERVAL_SEC", "300")))
    except ValueError:
        interval = 300
    while True:
        run_once(as_json=args.json)
        time.sleep(float(interval))


if __name__ == "__main__":
    raise SystemExit(main())
