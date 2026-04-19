#!/usr/bin/env python3
"""
Sygnif governed swarm — single CLI (slots + layout + run). Shell: ./scripts/swarm/swarm <cmd>

Commands:
  list          Slots + resolved SWARM_ARTIFACT_ROOT
  init-layout   mkdir artifact root + slot_* dirs
  run ID [-- …] Allowlisted argv from swarm-slots.json (extra args only if slot allows)

Env:
  SWARM_REPO_ROOT, SWARM_ARTIFACT_ROOT, SWARM_SKIP_LOCK, SWARM_SLOTS_JSON (override registry path)
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path


def _swarm_dir() -> Path:
    return Path(__file__).resolve().parent


def _repo_root() -> Path:
    return _swarm_dir().parents[1]


def _config_path() -> Path:
    override = os.environ.get("SWARM_SLOTS_JSON", "").strip()
    if override:
        return Path(override).expanduser()
    return _swarm_dir() / "swarm-slots.json"


def _load_config() -> dict:
    path = _config_path()
    if not path.is_file():
        raise SystemExit(f"missing registry: {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("schema_version") != 1:
        raise SystemExit("swarm-slots.json: invalid schema")
    slots = doc.get("slots")
    if not isinstance(slots, list):
        raise SystemExit("swarm-slots.json: missing slots")
    return doc


def _artifact_root(doc: dict) -> Path:
    key = str(doc.get("artifact_root_env") or "SWARM_ARTIFACT_ROOT")
    v = os.environ.get(key, "").strip()
    if v:
        return Path(v).expanduser()
    sub = str(doc.get("default_artifact_subdir") or ".local/share/sygnif-swarm")
    return Path.home() / sub


def _find_slot(doc: dict, slot_id: str) -> dict:
    for s in doc["slots"]:
        if isinstance(s, dict) and str(s.get("id")) == str(slot_id):
            return s
    raise SystemExit(f"unknown slot id {slot_id!r}; try: ./scripts/swarm/swarm list")


def _check_require_env(slot: dict) -> None:
    for k in slot.get("require_env") or []:
        if not os.environ.get(str(k), "").strip():
            raise SystemExit(f"slot {slot.get('id')}: required env {k} is unset")


def cmd_list(_: argparse.Namespace) -> int:
    doc = _load_config()
    root = _artifact_root(doc)
    print(f"registry: {_config_path()}")
    print(f"artifact_root: {root}")
    for s in doc["slots"]:
        if not isinstance(s, dict):
            continue
        print(f"  {s.get('id')}\t{s.get('name')}\t{(s.get('description') or '')[:80]}")
    return 0


def cmd_init_layout(_: argparse.Namespace) -> int:
    doc = _load_config()
    base = _artifact_root(doc)
    base.mkdir(parents=True, exist_ok=True)
    for s in doc["slots"]:
        if not isinstance(s, dict):
            continue
        sub = s.get("slot_subdir") or f"slot_{s.get('id')}"
        (base / str(sub)).mkdir(parents=True, exist_ok=True)
    print(f"layout ok under {base}")
    return 0


def cmd_run(ns: argparse.Namespace) -> int:
    doc = _load_config()
    slot = _find_slot(doc, ns.slot_id)
    _check_require_env(slot)

    cwd = Path(os.environ.get("SWARM_REPO_ROOT", str(_repo_root()))).expanduser()
    if not cwd.is_dir():
        raise SystemExit(f"SWARM_REPO_ROOT not a directory: {cwd}")

    argv = list(slot.get("argv") or [])
    if not argv:
        raise SystemExit("slot has empty argv")
    if ns.extra and not slot.get("allow_extra_argv"):
        raise SystemExit("this slot does not allow extra argv after --")

    argv = argv + list(ns.extra or [])

    art = _artifact_root(doc)
    sub = slot.get("slot_subdir") or f"slot_{slot.get('id')}"
    slot_dir = art / str(sub)
    slot_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["SWARM_SLOT_ID"] = str(slot.get("id", ""))
    env["SWARM_SLOT_NAME"] = str(slot.get("name", ""))
    env["SWARM_SLOT_DIR"] = str(slot_dir.resolve())
    pp_extra = (slot.get("env") or {}).get("PYTHONPATH")
    if pp_extra:
        rel = cwd / str(pp_extra)
        if rel.is_dir():
            env["PYTHONPATH"] = f"{rel.resolve()}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(
                os.pathsep
            )

    lock_path = slot_dir / ".run.lock"
    skip_lock = os.environ.get("SWARM_SKIP_LOCK", "").lower() in ("1", "true", "yes")

    def _run() -> int:
        print(json.dumps({"swarm_slot": slot.get("id"), "cwd": str(cwd), "argv": argv}, indent=2))
        r = subprocess.run(argv, cwd=str(cwd), env=env)
        return int(r.returncode)

    if skip_lock:
        return _run()

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as lf:
        try:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"slot {slot.get('id')} busy (lock {lock_path})", file=sys.stderr)
            return 3
        lf.write(str(os.getpid()) + "\n")
        lf.flush()
        return _run()


def main() -> int:
    p = argparse.ArgumentParser(
        prog="swarm",
        description="Sygnif governed swarm (registry + layout + slot run).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  ./scripts/swarm/swarm list\n  ./scripts/swarm/swarm init-layout\n"
        "  ./scripts/swarm/swarm run 1\n  ./scripts/swarm/swarm run 5   # Nautilus feed once\n"
        "  ./scripts/swarm/swarm run 6   # fusion sidecar sync\n  ./scripts/swarm/swarm run 4 -- --symmetric-19\n",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List slots and artifact root").set_defaults(func=cmd_list)
    sub.add_parser("init-layout", help="Create SWARM_ARTIFACT_ROOT + slot subdirs").set_defaults(
        func=cmd_init_layout
    )

    pr = sub.add_parser("run", help="Run allowlisted slot by id")
    pr.add_argument("slot_id")
    pr.add_argument("extra", nargs=argparse.REMAINDER, help="after -- if slot allows")
    pr.set_defaults(func=cmd_run)

    args = p.parse_args()
    if args.cmd == "run" and args.extra and args.extra[0] == "--":
        args.extra = args.extra[1:]
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
