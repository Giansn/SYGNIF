#!/usr/bin/env python3
"""
Remove common stale artifacts under the Sygnif repo (safe: no DBs, no .env).

- __pycache__ / *.pyc
- .pytest_cache
- *.tmp next to atomic JSON writes (prediction_agent, btc_specialist/data)
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SKIP_DIR_NAMES = frozenset(
    {".git", "node_modules", ".venv", "venv", "NostalgiaForInfinity", "ann_text_project"}
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def should_skip_dir(p: Path) -> bool:
    return p.name in SKIP_DIR_NAMES


def main() -> int:
    ap = argparse.ArgumentParser(description="Clean stale Sygnif cache/tmp files.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    root = repo_root()
    removed_dirs = 0
    removed_files = 0

    import os

    for base, dirnames, filenames in os.walk(root, topdown=False):
        base_p = Path(base)
        if should_skip_dir(base_p):
            dirnames[:] = []
            continue
        parts = set(base_p.parts)
        if ".git" in parts:
            continue

        if Path(base).name == "__pycache__":
            if args.dry_run:
                print(f"would remove dir {base}")
            else:
                shutil.rmtree(base, ignore_errors=True)
            removed_dirs += 1
            continue

        for fn in filenames:
            fp = Path(base) / fn
            if fn.endswith(".pyc"):
                if args.dry_run:
                    print(f"would remove {fp}")
                else:
                    fp.unlink(missing_ok=True)
                removed_files += 1
            elif fn.endswith(".tmp") and (
                "prediction_agent" in fp.parts or "btc_specialist" in fp.parts and "data" in fp.parts
            ):
                if args.dry_run:
                    print(f"would remove {fp}")
                else:
                    fp.unlink(missing_ok=True)
                removed_files += 1

    pc = root / ".pytest_cache"
    if pc.is_dir():
        if args.dry_run:
            print(f"would remove dir {pc}")
        else:
            shutil.rmtree(pc, ignore_errors=True)
        removed_dirs += 1

    print(f"done dry_run={args.dry_run} removed_dirs~{removed_dirs} removed_files~{removed_files}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
