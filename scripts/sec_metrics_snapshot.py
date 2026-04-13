#!/usr/bin/env python3
"""
Non-secret security posture snapshot for Sygnif (local / CI-friendly).

Prints file metadata, dry_run flags, and whether secret filenames are git-tracked.
Does **not** print values from .env or any API material.

Usage (repo root):
  python3 scripts/sec_metrics_snapshot.py
  python3 scripts/sec_metrics_snapshot.py --json   # machine-readable line
"""
from __future__ import annotations

import argparse
import json
import stat
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _git_ls_files(repo: Path, paths: list[str]) -> list[str]:
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "ls-files", *paths],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ["(git unavailable)"]
    if r.returncode != 0:
        return []
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def _file_meta(path: Path) -> dict:
    if not path.is_file():
        return {"path": str(path), "exists": False}
    st = path.stat()
    return {
        "path": str(path),
        "exists": True,
        "mode_oct": oct(st.st_mode & 0o777),
        "writable_by_others": bool(st.st_mode & stat.S_IWOTH),
    }


def _env_key_names_only(path: Path) -> dict:
    """Count KEY= lines; never emit values."""
    if not path.is_file():
        return {"path": str(path), "keys_defined": 0}
    n = 0
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k = line.split("=", 1)[0].strip()
            if k:
                n += 1
    except OSError:
        return {"path": str(path), "keys_defined": None, "error": "read_failed"}
    return {"path": str(path), "keys_defined": n}


def _ft_config(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        j = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"path": str(path), "error": "invalid_json"}
    return {
        "path": str(path),
        "dry_run": j.get("dry_run"),
        "dry_run_wallet": j.get("dry_run_wallet"),
        "trading_mode": j.get("trading_mode"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="Emit one JSON object.")
    args = ap.parse_args()

    repo = _repo_root()
    secret_files = [repo / ".env", repo / ".env.local", repo / ".env.secrets"]
    tracked = _git_ls_files(repo, [p.name for p in secret_files])

    out: dict = {
        "repo": str(repo),
        "policy_note": (
            "Demo/honeypot keys are an operational choice; they still must not be committed "
            "to git or pasted into public tickets. This script never prints secret values."
        ),
        "git_tracked_secret_filenames": tracked,
        "git_tracked_secret_count": len(tracked),
        "env_files": [{**_file_meta(p), **_env_key_names_only(p)} for p in secret_files],
        "freqtrade_spot_config": _ft_config(repo / "user_data" / "config.json"),
        "freqtrade_futures_config": _ft_config(repo / "user_data" / "config_futures.json"),
    }

    if args.json:
        print(json.dumps(out, indent=2))
        return 0

    print("=== Sygnif security metrics snapshot (no secrets) ===\n")
    print(f"Repo: {out['repo']}")
    print(f"Git-tracked secret filenames (should be empty): {out['git_tracked_secret_filenames'] or '[]'}")
    print()
    for row in out["env_files"]:
        ex = row.get("exists")
        print(f"  {row['path']}")
        print(f"    exists={ex}  mode={row.get('mode_oct', 'n/a')}  keys_defined={row.get('keys_defined', 'n/a')}")
        if row.get("writable_by_others"):
            print("    WARNING: world-writable file")
    print()
    for label in ("freqtrade_spot_config", "freqtrade_futures_config"):
        block = out.get(label)
        print(f"{label}:", json.dumps(block, indent=2) if block else "  (missing)")
    print()
    print("Policy:", out["policy_note"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
