#!/usr/bin/env python3
"""
Disk hygiene: gzip **stale** text/JSONL artifacts under the repo (optional, conservative).

Does **not** delete secrets or ``.env``. Prefer cron + ``BTC_GOV_ARCHIVE_DRY_RUN=1`` first pass.
"""
from __future__ import annotations

import gzip
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _default_globs() -> list[str]:
    raw = os.environ.get("BTC_GOV_ARCHIVE_PATHS", "").strip()
    if raw:
        return [g.strip() for g in raw.split(":") if g.strip()]
    return [
        "prediction_agent/btc_nauti_prediction_journal.jsonl",
        "prediction_agent/btc_prediction_proof_log.jsonl",
        "prediction_agent/btc_iface_trade_tags.jsonl",
    ]


def _mtime_age_days(p: Path) -> float:
    try:
        mt = p.stat().st_mtime
    except OSError:
        return -1.0
    now = datetime.now(timezone.utc).timestamp()
    return (now - mt) / 86400.0


def archive_one_file(path: Path, *, dry_run: bool) -> str | None:
    """
    If ``path`` exists and ``path.gz`` missing, gzip to ``path.gz`` and remove ``path``.
    Returns action log line or None if skipped.
    """
    if not path.is_file():
        return None
    gz = path.with_suffix(path.suffix + ".gz")
    if gz.exists():
        return None
    if dry_run:
        return f"dry_run: would gzip {path} -> {gz}"
    with path.open("rb") as fin:
        data = fin.read()
    with gzip.open(gz, "wb", compresslevel=6) as fout:
        fout.write(data)
    path.unlink()
    return f"gzipped {path} -> {gz}"


def run_archive_pass(
    *,
    repo_root: Path | None = None,
    days: float | None = None,
    globs: Iterable[str] | None = None,
    dry_run: bool | None = None,
) -> list[str]:
    root = repo_root or _repo_root()
    age = float(days if days is not None else os.environ.get("BTC_GOV_ARCHIVE_DAYS", "14"))
    patterns = list(globs) if globs is not None else _default_globs()
    if dry_run is None:
        dry_run = os.environ.get("BTC_GOV_ARCHIVE_DRY_RUN", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )

    lines: list[str] = []
    for pat in patterns:
        # glob relative to repo
        for p in root.glob(pat):
            if not p.is_file():
                continue
            if p.suffix == ".gz":
                continue
            if _mtime_age_days(p) < age:
                continue
            msg = archive_one_file(p, dry_run=dry_run)
            if msg:
                lines.append(msg)
    return lines


def remove_older_gz_copies(
    *,
    repo_root: Path | None = None,
    keep_days: float = 90.0,
    dry_run: bool = True,
) -> list[str]:
    """
    Optional second pass: delete ``*.gz`` older than ``keep_days`` (default 90).
    Off by default for safety; enable with ``BTC_GOV_PURGE_GZ_DAYS`` in env from caller.
    """
    root = repo_root or _repo_root()
    lines: list[str] = []
    for gz in root.rglob("*.gz"):
        try:
            rel = gz.relative_to(root)
        except ValueError:
            continue
        if "node_modules" in rel.parts or ".git" in rel.parts:
            continue
        if _mtime_age_days(gz) < keep_days:
            continue
        if dry_run:
            lines.append(f"dry_run: would unlink {gz}")
        else:
            gz.unlink(missing_ok=True)
            lines.append(f"removed {gz}")
    return lines
