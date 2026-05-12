#!/usr/bin/env python3
"""
Backup / restore utility for the sygnif-agent folder system.

- `create`  Zip the agent root (default: ~/sygnif-agent) into a timestamped archive.
- `restore` Unpack an archive back onto the agent root.
- `list`    List archives in the backup directory.

Excludes transient junk (__pycache__, *.pyc, .git, node_modules). The runtime
`outputs/` directory is INCLUDED by default so you get your run history; pass
--exclude-outputs to skip it for a lean archive.

Defaults are overridable via env:
  SYGNIF_AGENT_DIR         agent root (default: ~/sygnif-agent)
  SYGNIF_AGENT_BACKUP_DIR  where archives live (default: ~/sygnif-backups)

No external dependencies — stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_ROOT = Path(os.environ.get("SYGNIF_AGENT_DIR", str(Path(__file__).resolve().parent)))
DEFAULT_BACKUP_DIR = Path(
    os.environ.get("SYGNIF_AGENT_BACKUP_DIR", str(Path.home() / "sygnif-backups"))
)
ARCHIVE_PREFIX = "sygnif-agent-"
MANIFEST_NAME = "MANIFEST.json"

EXCLUDED_DIRS = {"__pycache__", ".git", "node_modules", ".venv", ".mypy_cache", ".pytest_cache"}
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".swp", ".tmp")
EXCLUDED_NAMES = {".DS_Store"}


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _should_skip(rel: Path, exclude_outputs: bool) -> bool:
    parts = rel.parts
    if exclude_outputs and parts and parts[0] == "outputs":
        return True
    for part in parts:
        if part in EXCLUDED_DIRS:
            return True
    name = rel.name
    if name in EXCLUDED_NAMES:
        return True
    if name.endswith(EXCLUDED_SUFFIXES):
        return True
    return False


def _iter_files(root: Path, exclude_outputs: bool):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if _should_skip(rel, exclude_outputs):
            continue
        yield path, rel


def _utc_now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_create(args: argparse.Namespace) -> int:
    root: Path = args.root
    if not root.is_dir():
        print(f"error: agent root not found: {root}", file=sys.stderr)
        return 2

    backup_dir: Path = args.output_dir
    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = _utc_now_stamp()
    archive = backup_dir / f"{ARCHIVE_PREFIX}{stamp}.zip"

    files = list(_iter_files(root, args.exclude_outputs))
    if not files:
        print(f"error: nothing to back up under {root}", file=sys.stderr)
        return 1

    manifest = {
        "schema": "sygnif_agent_backup/v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_root": str(root),
        "agent_dir_name": root.name,
        "exclude_outputs": bool(args.exclude_outputs),
        "file_count": len(files),
        "hostname": os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", "unknown"),
    }

    total_bytes = 0
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path, rel in files:
            arcname = root.name + "/" + rel.as_posix()
            zf.write(path, arcname)
            total_bytes += path.stat().st_size
        zf.writestr(root.name + "/" + MANIFEST_NAME, json.dumps(manifest, indent=2))

    print(
        f"created {archive}\n"
        f"  files: {len(files)}  raw: {_human_bytes(total_bytes)}  "
        f"archive: {_human_bytes(archive.stat().st_size)}"
        + ("  (outputs excluded)" if args.exclude_outputs else "")
    )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    backup_dir: Path = args.dir
    if not backup_dir.is_dir():
        print(f"(no backup dir at {backup_dir})")
        return 0
    archives = sorted(backup_dir.glob(f"{ARCHIVE_PREFIX}*.zip"), reverse=True)
    if not archives:
        print(f"(no backups in {backup_dir})")
        return 0
    print(f"{'archive':<50}  {'size':>10}  manifest")
    for a in archives:
        size = _human_bytes(a.stat().st_size)
        created = ""
        try:
            with zipfile.ZipFile(a) as zf:
                for name in zf.namelist():
                    if name.endswith("/" + MANIFEST_NAME):
                        m = json.loads(zf.read(name))
                        created = f"{m.get('file_count', '?')} files, {m.get('created_utc', '?')}"
                        break
        except zipfile.BadZipFile:
            created = "(corrupt)"
        print(f"{a.name:<50}  {size:>10}  {created}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    archive: Path = args.archive
    if not archive.is_file():
        print(f"error: archive not found: {archive}", file=sys.stderr)
        return 2

    target: Path = args.root
    if target.exists() and any(target.iterdir()) and not args.force:
        print(
            f"error: target {target} is not empty. Pass --force to overlay.",
            file=sys.stderr,
        )
        return 3

    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        top_dirs = {n.split("/", 1)[0] for n in names if "/" in n}
        if len(top_dirs) != 1:
            print(f"error: archive has {len(top_dirs)} top-level dirs; refusing.", file=sys.stderr)
            return 4
        (top,) = top_dirs

        with tempfile.TemporaryDirectory(prefix="sygnif-restore-") as td:
            tdp = Path(td)
            zf.extractall(tdp)
            src = tdp / top
            if not src.is_dir():
                print(f"error: extracted layout unexpected (no {top}/)", file=sys.stderr)
                return 5

            target.mkdir(parents=True, exist_ok=True)
            copied = 0
            for path in src.rglob("*"):
                rel = path.relative_to(src)
                dst = target / rel
                if path.is_dir():
                    dst.mkdir(parents=True, exist_ok=True)
                else:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, dst)
                    copied += 1

            manifest = src / MANIFEST_NAME
            if manifest.is_file():
                m = json.loads(manifest.read_text())
                print(
                    f"restored {copied} files into {target}\n"
                    f"  archive created: {m.get('created_utc', '?')}\n"
                    f"  source host:     {m.get('hostname', '?')}\n"
                    f"  outputs included: {not m.get('exclude_outputs', False)}"
                )
            else:
                print(f"restored {copied} files into {target} (no manifest in archive)")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="backup.py", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    pc = sub.add_parser("create", help="create a new backup archive")
    pc.add_argument("--root", type=Path, default=DEFAULT_ROOT, help=f"agent root (default: {DEFAULT_ROOT})")
    pc.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_BACKUP_DIR,
        help=f"backup dir (default: {DEFAULT_BACKUP_DIR})",
    )
    pc.add_argument(
        "--exclude-outputs",
        action="store_true",
        help="skip outputs/ (runtime run logs) for a lean archive",
    )
    pc.set_defaults(func=cmd_create)

    pl = sub.add_parser("list", help="list archives in backup dir")
    pl.add_argument("--dir", type=Path, default=DEFAULT_BACKUP_DIR)
    pl.set_defaults(func=cmd_list)

    pr = sub.add_parser("restore", help="restore an archive onto the agent root")
    pr.add_argument("archive", type=Path, help="path to a sygnif-agent-*.zip archive")
    pr.add_argument("--root", type=Path, default=DEFAULT_ROOT, help=f"target root (default: {DEFAULT_ROOT})")
    pr.add_argument("--force", action="store_true", help="overlay onto a non-empty target")
    pr.set_defaults(func=cmd_restore)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
