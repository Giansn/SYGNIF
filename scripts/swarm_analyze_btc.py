#!/usr/bin/env python3
"""
**Read-only** BTC swarm analysis loop — **no live orders**, no Freqtrade ``/forceenter``, no Bybit ``POST /v5/order``.

Flow: **train first** (existing Sygnif ML + channel JSON), then repeat **``compute_swarm()``** on a timer.

Uses:
  - ``prediction_agent/btc_predict_runner.py`` → ``btc_prediction_output.json``
  - ``training_pipeline/channel_training.py`` → ``training_channel_output.json`` (with ``SKIP_PREDICT_RUNNER=1`` after runner)
  - ``finance_agent/swarm_knowledge.compute_swarm()`` → fuse votes (optional PyTorch via ``SYGNIF_SWARM_PYTORCH``)
  - Optional: ``prediction_agent/nautilus_protocol_fusion.write_fused_sidecar`` when ``SWARM_ANALYZE_FUSION_SYNC=1``

Output: ``prediction_agent/swarm_analyze_btc_state.json`` (override ``SWARM_ANALYZE_STATE_JSON``).

Env:
  SWARM_ANALYZE_INTERVAL_SEC   — sleep between iterations (default 300); **0** = tight loop (use only with care).
  SWARM_ANALYZE_TRAIN_ON_START — ``0`` skips initial train (default **1**).
  SWARM_ANALYZE_TRAIN_EVERY_N  — re-run train every N loop iterations (**0** = never after start).
  SWARM_ANALYZE_RUNNER_TF      — ``--timeframe`` for runner (default **1h**).
  SWARM_ANALYZE_NO_WRITE_JSON  — ``1`` to skip state file.

Examples:
  python3 scripts/swarm_analyze_btc.py --once
  SWARM_ANALYZE_INTERVAL_SEC=120 python3 scripts/swarm_analyze_btc.py

Alias (typo): some docs refer to ``swarm_anlyze_btc.py`` — this file is canonical.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_STOP = False


def _on_signal(_sig: int, _frame: object | None) -> None:
    global _STOP
    _STOP = True


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _state_path() -> Path:
    raw = (os.environ.get("SWARM_ANALYZE_STATE_JSON") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _repo_root() / "prediction_agent" / "swarm_analyze_btc_state.json"


def _atomic_write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_train_phase(repo: Path, *, timeframe: str) -> dict[str, Any]:
    """Run existing runners (subprocess). Does not place orders."""
    py = sys.executable
    out: dict[str, Any] = {"runner_rc": None, "channel_rc": None, "errors": []}
    runner = [
        py,
        str(repo / "prediction_agent" / "btc_predict_runner.py"),
        "--timeframe",
        timeframe,
    ]
    r1 = subprocess.run(runner, cwd=str(repo), capture_output=True, text=True, timeout=3600)
    out["runner_rc"] = r1.returncode
    if r1.returncode != 0:
        out["errors"].append(f"btc_predict_runner rc={r1.returncode} stderr={r1.stderr[-800:]!r}")

    env = os.environ.copy()
    env["SKIP_PREDICT_RUNNER"] = "1"
    channel = [py, str(repo / "training_pipeline" / "channel_training.py")]
    r2 = subprocess.run(channel, cwd=str(repo), capture_output=True, text=True, timeout=3600, env=env)
    out["channel_rc"] = r2.returncode
    if r2.returncode != 0:
        out["errors"].append(f"channel_training rc={r2.returncode} stderr={r2.stderr[-800:]!r}")
    out["ok"] = r1.returncode == 0 and r2.returncode == 0
    return out


def _ensure_swarm_import(repo: Path) -> None:
    fa = repo / "finance_agent"
    if str(fa) not in sys.path:
        sys.path.insert(0, str(fa))


def run_analyze_iteration(repo: Path) -> dict[str, Any]:
    _ensure_swarm_import(repo)
    import swarm_knowledge as sk  # noqa: PLC0415

    swarm = sk.compute_swarm()
    row: dict[str, Any] = {
        "loop_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "swarm_mean": swarm.get("swarm_mean"),
        "swarm_label": swarm.get("swarm_label"),
        "swarm_conflict": swarm.get("swarm_conflict"),
        "swarm_engine": swarm.get("swarm_engine"),
        "swarm_engine_detail": swarm.get("swarm_engine_detail"),
        "sources_n": swarm.get("sources_n"),
        "missing_files": swarm.get("missing_files"),
        "sources_compact": {
            k: v.get("detail") for k, v in (swarm.get("sources") or {}).items() if isinstance(v, dict)
        },
    }

    if _env_truthy("SWARM_ANALYZE_FUSION_SYNC"):
        try:
            pa = repo / "prediction_agent"
            if str(pa) not in sys.path:
                sys.path.insert(0, str(pa))
            import nautilus_protocol_fusion as npf  # noqa: PLC0415

            npf.write_fused_sidecar(repo_root=repo)
            row["fusion_sync_ok"] = True
        except Exception as exc:  # noqa: BLE001
            row["fusion_sync_ok"] = False
            row["fusion_sync_error"] = str(exc)[:300]

    return {"swarm": swarm, "row": row}


def main() -> int:
    global _STOP
    ap = argparse.ArgumentParser(description="Train-then-read-only BTC swarm analysis loop (no orders)")
    ap.add_argument("--once", action="store_true", help="Single train (unless --no-train) + one analyze")
    ap.add_argument("--no-train", action="store_true", help="Skip all train subprocesses")
    ap.add_argument("--interval-sec", type=float, default=None, help="Override SWARM_ANALYZE_INTERVAL_SEC")
    ap.add_argument("--max-iterations", type=int, default=0, help="0 = until SIGINT/SIGTERM")
    ap.add_argument("--timeframe", default=os.environ.get("SWARM_ANALYZE_RUNNER_TF", "1h"), help="Runner TF")
    args = ap.parse_args()

    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_signal)

    repo = _repo_root()
    interval = args.interval_sec
    if interval is None:
        try:
            interval = float(os.environ.get("SWARM_ANALYZE_INTERVAL_SEC", "300"))
        except ValueError:
            interval = 300.0
    interval = max(0.0, float(interval))

    train_on_start = not args.no_train
    if os.environ.get("SWARM_ANALYZE_TRAIN_ON_START", "").strip().lower() in ("0", "false", "no", "off"):
        train_on_start = False

    try:
        train_every = int(os.environ.get("SWARM_ANALYZE_TRAIN_EVERY_N", "0") or "0")
    except ValueError:
        train_every = 0
    train_every = max(0, train_every)

    write_json = not _env_truthy("SWARM_ANALYZE_NO_WRITE_JSON")
    state_path = _state_path()

    meta: dict[str, Any] = {
        "contract": "read_only_no_orders",
        "repo": str(repo),
        "started_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    def do_train(tag: str) -> dict[str, Any]:
        print(f"SWARM_ANALYZE_TRAIN {tag} timeframe={args.timeframe!r}", flush=True)
        tr = run_train_phase(repo, timeframe=args.timeframe)
        print(json.dumps({"swarm_analyze_train": tr}, default=str), flush=True)
        return tr

    last_train: dict[str, Any] | None = None
    if train_on_start:
        last_train = do_train("on_start")

    n = 0
    while not _STOP:
        n += 1
        try:
            pack = run_analyze_iteration(repo)
            swarm = pack["swarm"]
            row = pack["row"]
            line = (
                f"SWARM_ANALYZE iter={n} label={row.get('swarm_label')} mean={row.get('swarm_mean')} "
                f"engine={row.get('swarm_engine')} conflict={row.get('swarm_conflict')} "
                f"missing={row.get('missing_files')}"
            )
            print(line, flush=True)

            if write_json:
                blob = {
                    "meta": meta,
                    "last_train": last_train,
                    "iteration": n,
                    "latest": row,
                    "swarm_full": swarm,
                }
                _atomic_write_json(state_path, blob)
        except Exception as exc:  # noqa: BLE001
            print(f"SWARM_ANALYZE_EXC {exc!r}", flush=True)

        if args.once:
            break

        if train_every > 0 and n % train_every == 0 and not args.no_train:
            last_train = do_train(f"every_{train_every}")

        max_iter = max(0, int(args.max_iterations))
        if max_iter > 0 and n >= max_iter:
            break

        if _STOP:
            break
        if interval > 0:
            t0 = time.monotonic()
            while not _STOP:
                if time.monotonic() - t0 >= interval:
                    break
                time.sleep(min(1.0, interval))

    print("SWARM_ANALYZE stop", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
