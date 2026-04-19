#!/usr/bin/env python3
"""
**Swarm hook** after Nautilus **readings** (OHLCV/bundle sink or sidecar signal write).

Runs only when enabled by env — no silent wiring.

Env (any phase):
  NAUTILUS_SWARM_HOOK=1          — master switch (fusion + optional knowledge)
  NAUTILUS_FUSION_SIDECAR_SYNC=1 — legacy alias: fusion only (same as hook with knowledge off)

  NAUTILUS_SWARM_HOOK_FUSION=1   — default when ``NAUTILUS_SWARM_HOOK`` is on; set ``0`` to skip fusion
  NAUTILUS_SWARM_HOOK_KNOWLEDGE=1 — also write ``swarm_knowledge_output.json`` (plaintext; no seal in hook)
  SYGNIF_BYBIT_DEMO_PREDICTED_MOVE_EXPORT=1 — write ``bybitapidemo_btc_predicted_move_signal.json`` (swarm governance + min 75% prob by default)

Phases:
  ``training_feed`` — after ``bybit_nautilus_spot_btc_training_feed.py`` writes JSON
  ``sidecar``       — after ``nautilus_sidecar_strategy.py`` writes ``nautilus_strategy_signal.json``
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _env_falsy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("0", "false", "no", "off")


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _prediction_agent_out_dir(repo_root: Path) -> Path:
    for key in ("PREDICTION_AGENT_DIR", "SYGNIF_PREDICTION_AGENT_DIR"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return Path(raw).expanduser()
    return repo_root / "prediction_agent"


def _hook_master_on() -> bool:
    return _env_truthy("NAUTILUS_SWARM_HOOK")


def _fusion_requested() -> bool:
    if _env_falsy("NAUTILUS_SWARM_HOOK_FUSION"):
        return False
    return _hook_master_on() or _env_truthy("NAUTILUS_FUSION_SIDECAR_SYNC")


def _knowledge_requested() -> bool:
    return _hook_master_on() and _env_truthy("NAUTILUS_SWARM_HOOK_KNOWLEDGE")


def _bybit_demo_signal_requested() -> bool:
    return _env_truthy("SYGNIF_BYBIT_DEMO_PREDICTED_MOVE_EXPORT")


def run_nautilus_swarm_hook(*, phase: str, repo_root: Path | None = None) -> dict[str, Any]:
    """
    Execute enabled hook steps. Always returns a small status dict; prints one JSON line when not skipped.
    """
    root = repo_root or default_repo_root()
    out: dict[str, Any] = {
        "phase": phase,
        "skipped": True,
        "fusion_ok": None,
        "knowledge_ok": None,
        "bybit_demo_signal_ok": None,
    }

    do_fusion = _fusion_requested()
    do_knowledge = _knowledge_requested()
    do_demo_signal = _bybit_demo_signal_requested()
    if not do_fusion and not do_knowledge and not do_demo_signal:
        return out

    out["skipped"] = not (do_fusion or do_knowledge or do_demo_signal)
    pa = root / "prediction_agent"
    if not pa.is_dir():
        out["error"] = "missing_prediction_agent_dir"
        print(json.dumps({"nautilus_swarm_hook": out}), flush=True)
        return out

    if str(pa) not in sys.path:
        sys.path.insert(0, str(pa))

    if do_fusion:
        try:
            import nautilus_protocol_fusion as npf  # noqa: PLC0415

            npf.write_fused_sidecar(repo_root=root)
            out["fusion_ok"] = True
        except Exception as exc:  # noqa: BLE001
            out["fusion_ok"] = False
            out["fusion_error"] = str(exc)[:500]

    if do_knowledge:
        try:
            fa = root / "finance_agent"
            if str(fa) not in sys.path:
                sys.path.insert(0, str(fa))
            import swarm_knowledge as sk  # noqa: PLC0415

            doc = sk.compute_swarm()
            dest = _prediction_agent_out_dir(root) / "swarm_knowledge_output.json"
            dest.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            out["knowledge_ok"] = True
            out["knowledge_path"] = str(dest)
        except Exception as exc:  # noqa: BLE001
            out["knowledge_ok"] = False
            out["knowledge_error"] = str(exc)[:500]

    if do_demo_signal:
        try:
            import bybit_demo_predicted_move_export as bd  # noqa: PLC0415

            path, payload = bd.write_signal_json(repo_root=root)
            out["bybit_demo_signal_ok"] = True
            out["bybit_demo_signal_path"] = str(path)
            out["bybit_demo_signal_active"] = payload.get("signal_active")
        except Exception as exc:  # noqa: BLE001
            out["bybit_demo_signal_ok"] = False
            out["bybit_demo_signal_error"] = str(exc)[:500]

    print(json.dumps({"nautilus_swarm_hook": out}), flush=True)
    return out


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Run Nautilus swarm hook once (for tests / manual)")
    ap.add_argument("--phase", default="manual", help="training_feed|sidecar|manual")
    args = ap.parse_args()
    run_nautilus_swarm_hook(phase=args.phase)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
