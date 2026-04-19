#!/usr/bin/env python3
"""
**Swarm sync protocol** — refresh every Swarm input + fused sidecars so all consumers share up-to-date knowledge.

Runs (default, in order):

1. ``prediction_agent/btc_predict_runner.py`` — ML JSON (**ml** vote)
2. ``training_pipeline/channel_training.py`` — channel recognition (**ch**)
3. ``finance_agent/btc_specialist/scripts/pull_btc_context.py`` — bundle + **TA** snapshot (**ta**)
4. ``finance_agent/swarm_knowledge.py`` — writes ``swarm_knowledge_output.json`` (all votes incl. **mn**, **ac**, **bf** per env; **ac**+**trade** **bf** fuse to one **bf** vote when symbols match)
5. ``prediction_agent/nautilus_protocol_fusion.py sync`` — ``swarm_nautilus_protocol_sidecar.json`` (needs swarm JSON for keypoints)
6. ``finance_agent/swarm_btc_future_tpsl_apply.py`` — optional **demo** TP/SL/trailing from ``btc_prediction_output.json`` onto open linear position (see env below)

Optional: ``scripts/write_system_snapshot.py`` (HUD + swarm keypoints embed).

**Order:** Swarm JSON **before** fusion sync so ``swarm_keypoints`` in the fusion file match the latest ``compute_swarm()`` output.

Env (defaults applied if unset):

- ``SYGNIF_SWARM_BYBIT_MAINNET=1`` — public mainnet ticker (**mn**)
- ``SYGNIF_SWARM_BTC_FUTURE=1`` — demo linear position (**bf**), needs ``BYBIT_DEMO_*``; ``SYGNIF_SWARM_BTC_FUTURE=trade`` — **bf** from mainnet ``BYBIT_API_*``
- ``SYGNIF_SWARM_BYBIT_ACCOUNT`` / ``SWARM_SYNC_ENABLE_AC=1`` — signed mainnet position; with **trade** **bf** and the same symbol on both envs, Swarm emits **bf** only (one read; ``bybit_account`` JSON stays with ``fused_with_btc_future_trade``)
- ``SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL`` — default **on** in this script: POST demo trading-stop from predictions; set ``0`` to skip
- ``SYGNIF_SWARM_TPSL_PROFILE=reward_risk`` — default here: wider TP / tighter SL base vs legacy; override per-key env as needed
- Full pipeline + finance-agent prompt stub: ``scripts/swarm_channel_finance_consult.sh``

Examples::

  python3 scripts/swarm_sync_protocol.py
  python3 scripts/swarm_sync_protocol.py --quick
  SWARM_SYNC_ENABLE_AC=1 python3 scripts/swarm_sync_protocol.py --snapshot
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_dotenv_file(path: Path, *, override: bool = False) -> None:
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if not k:
            continue
        v = v.strip().strip('"').strip("'")
        if override:
            os.environ[k] = v
        else:
            os.environ.setdefault(k, v)


def load_repo_env() -> None:
    raw = (os.environ.get("SYGNIF_SECRETS_ENV_FILE") or "").strip()
    if raw:
        _load_dotenv_file(Path(raw).expanduser())
    _load_dotenv_file(Path.home() / "xrp_claude_bot" / ".env")
    _load_dotenv_file(_repo_root() / ".env")


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _apply_swarm_satellite_defaults() -> None:
    """Enable all read-only Swarm branches unless explicitly disabled."""
    os.environ.setdefault("SYGNIF_SWARM_BYBIT_MAINNET", "1")
    os.environ.setdefault("SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL", "1")
    os.environ.setdefault("SYGNIF_SWARM_TPSL_PROFILE", "reward_risk")
    if _env_truthy("SWARM_SYNC_ENABLE_AC"):
        os.environ.setdefault("SYGNIF_SWARM_BYBIT_ACCOUNT", "1")
        # Align **bf** with mainnet **ac** (single fused vote when symbols match; override with SYGNIF_SWARM_BTC_FUTURE=1 for demo bf)
        os.environ.setdefault("SYGNIF_SWARM_BTC_FUTURE", "trade")
    else:
        os.environ.setdefault("SYGNIF_SWARM_BTC_FUTURE", "1")


def _run_step(name: str, argv: list[str], *, cwd: Path, env: dict[str, str], optional: bool) -> int:
    print(f"[swarm-sync] {name}", flush=True)
    r = subprocess.run(argv, cwd=str(cwd), env=env, check=False)
    if r.returncode != 0:
        msg = f"[swarm-sync] WARN {name} exit={r.returncode}"
        if optional:
            print(msg, flush=True)
            return r.returncode
        print(msg, file=sys.stderr, flush=True)
        sys.exit(r.returncode)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Full Swarm knowledge sync (inputs + swarm + fusion)")
    ap.add_argument("--quick", action="store_true", help="Skip runner, channel, pull_btc_context")
    ap.add_argument("--no-runner", action="store_true")
    ap.add_argument("--no-channel", action="store_true")
    ap.add_argument("--no-pull-ta", action="store_true")
    ap.add_argument("--no-fusion", action="store_true")
    ap.add_argument("--no-swarm", action="store_true", help="Skip compute_swarm (not recommended)")
    ap.add_argument("--snapshot", action="store_true", help="Run write_system_snapshot.py after fusion")
    ap.add_argument("--no-tpsl", action="store_true", help="Skip swarm_btc_future_tpsl_apply (demo TP/SL from predictions)")
    args = ap.parse_args()

    repo = _repo_root()
    load_repo_env()
    _apply_swarm_satellite_defaults()

    py = sys.executable
    env = os.environ.copy()

    do_runner = not args.quick and not args.no_runner
    do_channel = not args.quick and not args.no_channel
    do_ta = not args.quick and not args.no_pull_ta

    if do_runner:
        _run_step(
            "btc_predict_runner",
            [py, str(repo / "prediction_agent" / "btc_predict_runner.py")],
            cwd=repo,
            env=env,
            optional=True,
        )
    if do_channel:
        cenv = env.copy()
        if do_runner:
            cenv["SKIP_PREDICT_RUNNER"] = "1"
        _run_step(
            "channel_training",
            [py, str(repo / "training_pipeline" / "channel_training.py")],
            cwd=repo,
            env=cenv,
            optional=True,
        )
    if do_ta:
        _run_step(
            "pull_btc_context",
            [py, str(repo / "finance_agent" / "btc_specialist" / "scripts" / "pull_btc_context.py")],
            cwd=repo,
            env=env,
            optional=True,
        )

    if not args.no_swarm:
        _run_step(
            "swarm_knowledge",
            [py, str(repo / "finance_agent" / "swarm_knowledge.py")],
            cwd=repo,
            env=env,
            optional=False,
        )

    if not args.no_fusion:
        _run_step(
            "nautilus_protocol_fusion sync",
            [py, str(repo / "prediction_agent" / "nautilus_protocol_fusion.py"), "sync"],
            cwd=repo,
            env=env,
            optional=False,
        )

    if not args.no_tpsl:
        _run_step(
            "swarm_btc_future_tpsl_apply",
            [py, str(repo / "finance_agent" / "swarm_btc_future_tpsl_apply.py")],
            cwd=repo,
            env=env,
            optional=True,
        )

    if args.snapshot:
        _run_step(
            "write_system_snapshot",
            [py, str(repo / "scripts" / "write_system_snapshot.py")],
            cwd=repo,
            env=env,
            optional=True,
        )

    print("[swarm-sync] done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
