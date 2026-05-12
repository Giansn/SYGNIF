#!/usr/bin/env python3
"""sygnif_disk_janitor.py — hourly disk-maintenance worker.

Goal: keep / disk usage ≤ TARGET_PCT (default 80%). Apply progressively more
aggressive cleanup in tiers, stop as soon as we're under target.

Tier 1 (always-safe — runs every cycle regardless of disk %):
  - Delete stale .zip / .tar / .tar.gz files in sygnif dirs older than 6h
    (these are leaked archives — the recurring 26GB-zip incident)
  - Delete /tmp/* older than 7 days
  - Vacuum systemd journal to MAX_JOURNAL_MB (default 500 MB)
  - Rotate any /var/log/sygnif/*.log > MAX_LOG_MB (default 10 MB)

Tier 2 (fires if still above TARGET_PCT after Tier 1):
  - Delete .pre-* backup files older than 30 days
  - Delete per-day ndjson files older than 30 days
  - Delete discovery/baseline_*.json older than 14 days
  - Delete swarm.db.YYYYMMDD.snapshot beyond newest 3

Tier 3 (fires if still above TARGET_PCT + 10% — emergency):
  - Truncate large log files (keep last 1 MB tail)
  - Vacuum journal to 100 MB

Alerts:
  - >= ALERT_PCT (default 85%) after Tier 1: writes agent.disk_alert to swarm
  - >= 90%: same alert tagged "critical"

Always emits agent.review.disk_janitor swarm row with full action log.

Run:
  python3 /opt/sygnif-services/sygnif_disk_janitor.py
  python3 /opt/sygnif-services/sygnif_disk_janitor.py --dry-run
Wired by sygnif-disk-janitor.timer (hourly).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

# ---------------------------------------------------------------------------
# Config (env-overridable)
# ---------------------------------------------------------------------------
TARGET_PCT       = int(os.environ.get("SYGNIF_DISK_TARGET_PCT", "80"))
ALERT_PCT        = int(os.environ.get("SYGNIF_DISK_ALERT_PCT", "85"))
EMERGENCY_PCT    = int(os.environ.get("SYGNIF_DISK_EMERGENCY_PCT", "90"))
MAX_JOURNAL_MB   = int(os.environ.get("SYGNIF_DISK_MAX_JOURNAL_MB", "500"))
MAX_LOG_MB       = int(os.environ.get("SYGNIF_DISK_MAX_LOG_MB", "10"))
STALE_ZIP_HOURS  = int(os.environ.get("SYGNIF_DISK_STALE_ZIP_HOURS", "6"))
TMP_AGE_DAYS     = int(os.environ.get("SYGNIF_DISK_TMP_AGE_DAYS", "7"))
PRE_BACKUP_AGE_DAYS = int(os.environ.get("SYGNIF_DISK_PRE_BACKUP_AGE_DAYS", "30"))
NDJSON_AGE_DAYS  = int(os.environ.get("SYGNIF_DISK_NDJSON_AGE_DAYS", "30"))
BASELINE_AGE_DAYS = int(os.environ.get("SYGNIF_DISK_BASELINE_AGE_DAYS", "14"))
KEEP_SWARM_SNAPSHOTS = int(os.environ.get("SYGNIF_DISK_KEEP_SWARM_SNAPSHOTS", "3"))

DB = "/var/lib/sygnif/swarm.db"
ZIP_SEARCH_DIRS = [
    "/home/ubuntu/sygnif-agent-mirror",
    "/home/ubuntu/SYGNIF",
    "/home/ubuntu/sygnif-swarm",
    "/home/ubuntu",
]
PRE_BACKUP_DIRS = [
    "/home/ubuntu/sygnif-agent-mirror",
    "/home/ubuntu/sygnif-agent-mirror/agent",
    "/opt/sygnif-services",
]
NDJSON_DIR = pathlib.Path("/var/lib/sygnif")
BASELINE_DIR = pathlib.Path("/home/ubuntu/sygnif-agent-mirror/discovery")
SWARM_SNAPSHOT_DIR = pathlib.Path("/home/ubuntu/sygnif-agent-mirror")
SYGNIF_LOG_DIR = pathlib.Path("/var/log/sygnif")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def disk_pct(path: str = "/") -> int:
    """Return integer percent used for the filesystem containing path."""
    u = shutil.disk_usage(path)
    return int(round(u.used / u.total * 100))


def file_age_hours(p: pathlib.Path) -> float:
    try:
        return (time.time() - p.stat().st_mtime) / 3600
    except OSError:
        return 0.0


def file_size_mb(p: pathlib.Path) -> float:
    try:
        return p.stat().st_size / (1024 * 1024)
    except OSError:
        return 0.0


def run_cmd(cmd: list[str], dry_run: bool = False) -> tuple[int, str]:
    if dry_run:
        return (0, f"DRY_RUN: would run {' '.join(cmd)}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return (r.returncode, (r.stdout + r.stderr)[:200])
    except Exception as e:
        return (1, f"{type(e).__name__}: {e}")


def safe_unlink(p: pathlib.Path, dry_run: bool = False) -> tuple[bool, float]:
    """Return (deleted, freed_mb)."""
    size_mb = file_size_mb(p)
    if dry_run:
        return (False, size_mb)
    try:
        p.unlink()
        return (True, size_mb)
    except OSError:
        return (False, 0.0)


# ---------------------------------------------------------------------------
# Tier 1 — always-safe cleanups
# ---------------------------------------------------------------------------
def t1_stale_archives(report: dict, dry_run: bool) -> float:
    """Delete stale .zip / .tar / .tar.gz / .tgz files older than STALE_ZIP_HOURS."""
    freed = 0.0
    actions = []
    extensions = (".zip", ".tar", ".tar.gz", ".tgz")
    for d in ZIP_SEARCH_DIRS:
        p = pathlib.Path(d)
        if not p.exists(): continue
        # Don't recurse — only top-level + 1 level deep
        for ext in extensions:
            for f in list(p.glob(f"*{ext}")) + list(p.glob(f"*/*{ext}")):
                if not f.is_file(): continue
                age_h = file_age_hours(f)
                size_mb = file_size_mb(f)
                if age_h > STALE_ZIP_HOURS and size_mb > 10:
                    # Check no process is holding it open
                    try:
                        r = subprocess.run(["lsof", "--", str(f)],
                                            capture_output=True, timeout=5)
                        if r.returncode == 0 and r.stdout:
                            actions.append(f"SKIP {f} ({size_mb:.0f}MB, in use)")
                            continue
                    except Exception:
                        pass
                    ok, mb = safe_unlink(f, dry_run)
                    if ok or dry_run:
                        freed += mb
                        actions.append(f"{'DRY' if dry_run else 'DEL'} {f} "
                                        f"({mb:.0f}MB, {age_h:.1f}h old)")
    report["t1_stale_archives"] = {"freed_mb": round(freed, 1),
                                     "actions": actions}
    return freed


def t1_tmp_old(report: dict, dry_run: bool) -> float:
    """Delete /tmp files older than TMP_AGE_DAYS."""
    freed = 0.0
    actions = []
    cutoff = time.time() - TMP_AGE_DAYS * 86400
    tmp = pathlib.Path("/tmp")
    try:
        for f in tmp.iterdir():
            if not f.is_file(): continue
            try:
                if f.stat().st_mtime < cutoff:
                    mb = file_size_mb(f)
                    ok, _ = safe_unlink(f, dry_run)
                    if ok or dry_run:
                        freed += mb
                        if mb > 1:
                            actions.append(f"{'DRY' if dry_run else 'DEL'} {f} "
                                            f"({mb:.1f}MB)")
            except OSError:
                continue
    except OSError:
        pass
    report["t1_tmp_old"] = {"freed_mb": round(freed, 1),
                              "n_actions": len(actions),
                              "samples": actions[:5]}
    return freed


def t1_journal_vacuum(report: dict, dry_run: bool) -> float:
    """journalctl --vacuum-size keeps journal at most MAX_JOURNAL_MB."""
    before_kb = 0
    after_kb = 0
    try:
        r = subprocess.run(["journalctl", "--disk-usage"], capture_output=True,
                            text=True, timeout=10)
        # Output: "Archived and active journals take up 1.8G in the file system."
        import re
        m = re.search(r"take up\s+([\d.]+)([KMG])", r.stdout)
        if m:
            n = float(m.group(1))
            unit = m.group(2)
            mult = {"K": 1, "M": 1024, "G": 1024 * 1024}.get(unit, 1)
            before_kb = int(n * mult)
    except Exception: pass

    rc, out = run_cmd(["sudo", "journalctl", "--vacuum-size", f"{MAX_JOURNAL_MB}M"],
                        dry_run)

    try:
        r = subprocess.run(["journalctl", "--disk-usage"], capture_output=True,
                            text=True, timeout=10)
        m = re.search(r"take up\s+([\d.]+)([KMG])", r.stdout)
        if m:
            n = float(m.group(1))
            unit = m.group(2)
            mult = {"K": 1, "M": 1024, "G": 1024 * 1024}.get(unit, 1)
            after_kb = int(n * mult)
    except Exception: pass

    freed_mb = max(0, (before_kb - after_kb) / 1024)
    report["t1_journal_vacuum"] = {
        "before_kb":  before_kb,
        "after_kb":   after_kb,
        "freed_mb":   round(freed_mb, 1),
        "rc":         rc,
        "out":        out[:100],
        "dry_run":    dry_run,
    }
    return freed_mb


def t1_rotate_logs(report: dict, dry_run: bool) -> float:
    """Truncate sygnif logs > MAX_LOG_MB (keep last MAX_LOG_MB / 2)."""
    freed = 0.0
    actions = []
    if not SYGNIF_LOG_DIR.exists():
        report["t1_rotate_logs"] = {"skipped": "dir missing"}
        return 0
    keep_bytes = (MAX_LOG_MB // 2) * 1024 * 1024
    for log_path in SYGNIF_LOG_DIR.glob("*.log"):
        try:
            size_mb = file_size_mb(log_path)
            if size_mb < MAX_LOG_MB: continue
            if dry_run:
                actions.append(f"DRY rotate {log_path.name} ({size_mb:.1f}MB → {MAX_LOG_MB//2}MB)")
                freed += size_mb - (MAX_LOG_MB // 2)
                continue
            # Read last N bytes, write back with truncation marker
            with log_path.open("rb") as f:
                f.seek(max(0, log_path.stat().st_size - keep_bytes))
                tail = f.read()
            header = (f"# rotated by sygnif_disk_janitor "
                      f"{dt.datetime.now(dt.timezone.utc).isoformat()} "
                      f"— was {size_mb:.1f}MB\n").encode()
            with log_path.open("wb") as f:
                f.write(header + tail)
            new_mb = file_size_mb(log_path)
            freed += size_mb - new_mb
            actions.append(f"ROTATE {log_path.name} {size_mb:.1f}→{new_mb:.1f}MB")
        except OSError as e:
            actions.append(f"FAIL {log_path.name}: {e}")
    report["t1_rotate_logs"] = {"freed_mb": round(freed, 1),
                                  "actions": actions}
    return freed


# ---------------------------------------------------------------------------
# Tier 2 — older artifacts
# ---------------------------------------------------------------------------
def t2_pre_backups(report: dict, dry_run: bool) -> float:
    freed = 0.0
    actions = []
    cutoff = time.time() - PRE_BACKUP_AGE_DAYS * 86400
    for d in PRE_BACKUP_DIRS:
        p = pathlib.Path(d)
        if not p.exists(): continue
        for f in p.glob("*.pre-*"):
            if not f.is_file(): continue
            try:
                if f.stat().st_mtime < cutoff:
                    mb = file_size_mb(f)
                    ok, _ = safe_unlink(f, dry_run)
                    if ok or dry_run:
                        freed += mb
                        if mb > 0.1:
                            actions.append(f"{'DRY' if dry_run else 'DEL'} {f} ({mb:.2f}MB)")
            except OSError:
                continue
    report["t2_pre_backups"] = {"freed_mb": round(freed, 1),
                                  "n_actions": len(actions),
                                  "samples": actions[:5]}
    return freed


def t2_old_ndjson(report: dict, dry_run: bool) -> float:
    freed = 0.0
    actions = []
    cutoff = time.time() - NDJSON_AGE_DAYS * 86400
    if not NDJSON_DIR.exists():
        report["t2_old_ndjson"] = {"skipped": "dir missing"}
        return 0
    for f in NDJSON_DIR.glob("*_2026-*.ndjson"):
        # Per-day rotated files only — never touch main
        if "_2026-" not in f.name: continue
        try:
            if f.stat().st_mtime < cutoff:
                mb = file_size_mb(f)
                ok, _ = safe_unlink(f, dry_run)
                if ok or dry_run:
                    freed += mb
                    actions.append(f"{'DRY' if dry_run else 'DEL'} {f.name} ({mb:.2f}MB)")
        except OSError:
            continue
    report["t2_old_ndjson"] = {"freed_mb": round(freed, 1),
                                 "n_actions": len(actions)}
    return freed


def t2_old_baselines(report: dict, dry_run: bool) -> float:
    freed = 0.0
    actions = []
    cutoff = time.time() - BASELINE_AGE_DAYS * 86400
    if not BASELINE_DIR.exists():
        report["t2_old_baselines"] = {"skipped": "dir missing"}
        return 0
    for f in BASELINE_DIR.glob("baseline_*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                mb = file_size_mb(f)
                ok, _ = safe_unlink(f, dry_run)
                if ok or dry_run:
                    freed += mb
                    actions.append(f.name)
        except OSError:
            continue
    report["t2_old_baselines"] = {"freed_mb": round(freed, 1),
                                    "n_files": len(actions)}
    return freed


def t2_old_swarm_snapshots(report: dict, dry_run: bool) -> float:
    """Keep newest N swarm.db.YYYYMMDD.snapshot, delete the rest."""
    freed = 0.0
    actions = []
    snaps = sorted(SWARM_SNAPSHOT_DIR.glob("swarm.db.*.snapshot"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for f in snaps[KEEP_SWARM_SNAPSHOTS:]:
        mb = file_size_mb(f)
        ok, _ = safe_unlink(f, dry_run)
        if ok or dry_run:
            freed += mb
            actions.append(f"{'DRY' if dry_run else 'DEL'} {f.name} ({mb:.1f}MB)")
    report["t2_old_swarm_snapshots"] = {"freed_mb": round(freed, 1),
                                          "actions": actions,
                                          "kept_newest": KEEP_SWARM_SNAPSHOTS}
    return freed


# ---------------------------------------------------------------------------
# Tier 3 — emergency
# ---------------------------------------------------------------------------
def t3_aggressive_log_truncate(report: dict, dry_run: bool) -> float:
    freed = 0.0
    actions = []
    # Same as t1_rotate but with TINY tail keep
    if not SYGNIF_LOG_DIR.exists():
        report["t3_aggressive_log_truncate"] = {"skipped": "dir missing"}
        return 0
    keep_bytes = 1024 * 1024  # 1 MB
    for log_path in SYGNIF_LOG_DIR.glob("*.log"):
        size_mb = file_size_mb(log_path)
        if size_mb < 2: continue
        if dry_run:
            actions.append(f"DRY truncate {log_path.name} → 1MB")
            freed += size_mb - 1
            continue
        try:
            with log_path.open("rb") as f:
                f.seek(max(0, log_path.stat().st_size - keep_bytes))
                tail = f.read()
            with log_path.open("wb") as f:
                f.write(f"# EMERGENCY truncate {dt.datetime.now(dt.timezone.utc).isoformat()}\n".encode() + tail)
            freed += size_mb - file_size_mb(log_path)
            actions.append(f"EMERG_TRUNC {log_path.name}")
        except OSError:
            continue
    report["t3_aggressive_log_truncate"] = {"freed_mb": round(freed, 1),
                                              "actions": actions}
    return freed


def t3_journal_emergency(report: dict, dry_run: bool) -> float:
    rc, out = run_cmd(["sudo", "journalctl", "--vacuum-size", "100M"], dry_run)
    report["t3_journal_emergency"] = {"rc": rc, "out": out[:100]}
    return 0  # already accounted in journal vacuum


# ---------------------------------------------------------------------------
# Alert + report
# ---------------------------------------------------------------------------
def emit_swarm(topic: str, content: str, meta: dict, tags: list) -> None:
    try:
        c = sqlite3.connect(DB, timeout=10)
        c.execute(
            "INSERT OR IGNORE INTO swarm_entries "
            "(id, created, swarm_id, agent_id, topic, content, meta, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), int(time.time()), "trading",
             "sygnif-disk-janitor", topic, content,
             json.dumps(meta, default=str),
             json.dumps(tags)))
        c.commit()
        c.close()
    except Exception as e:
        print(f"  swarm emit failed: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be done without deleting")
    ap.add_argument("--force-tier2", action="store_true",
                    help="run tier 2 regardless of disk %")
    ap.add_argument("--force-tier3", action="store_true",
                    help="run all tiers regardless of disk %")
    args = ap.parse_args()

    started = dt.datetime.now(dt.timezone.utc)
    pct_before = disk_pct("/")
    report: dict = {
        "ts_utc":          started.isoformat(),
        "pct_before":      pct_before,
        "target_pct":      TARGET_PCT,
        "alert_pct":       ALERT_PCT,
        "emergency_pct":   EMERGENCY_PCT,
        "dry_run":         args.dry_run,
    }

    print(f"=== disk_janitor @ {started.isoformat()} ===")
    print(f"  before: {pct_before}%  target ≤{TARGET_PCT}%  alert ≥{ALERT_PCT}%")

    total_freed_mb = 0.0

    # Tier 1 always runs
    print(f"\n  --- TIER 1 (always-safe) ---")
    total_freed_mb += t1_stale_archives(report, args.dry_run)
    total_freed_mb += t1_tmp_old(report, args.dry_run)
    total_freed_mb += t1_journal_vacuum(report, args.dry_run)
    total_freed_mb += t1_rotate_logs(report, args.dry_run)
    print(f"  tier1 freed: {total_freed_mb:.1f} MB")

    pct_after_t1 = disk_pct("/")
    report["pct_after_t1"] = pct_after_t1
    print(f"  after tier1: {pct_after_t1}%")

    # Tier 2 if still above target
    if pct_after_t1 > TARGET_PCT or args.force_tier2 or args.force_tier3:
        print(f"\n  --- TIER 2 (older artifacts) ---")
        before_t2 = total_freed_mb
        total_freed_mb += t2_pre_backups(report, args.dry_run)
        total_freed_mb += t2_old_ndjson(report, args.dry_run)
        total_freed_mb += t2_old_baselines(report, args.dry_run)
        total_freed_mb += t2_old_swarm_snapshots(report, args.dry_run)
        print(f"  tier2 freed: {total_freed_mb - before_t2:.1f} MB")
        pct_after_t2 = disk_pct("/")
        report["pct_after_t2"] = pct_after_t2
        print(f"  after tier2: {pct_after_t2}%")
    else:
        pct_after_t2 = pct_after_t1

    # Tier 3 emergency only if above emergency or forced
    if pct_after_t2 > EMERGENCY_PCT or args.force_tier3:
        print(f"\n  --- TIER 3 (EMERGENCY) ---")
        before_t3 = total_freed_mb
        total_freed_mb += t3_aggressive_log_truncate(report, args.dry_run)
        t3_journal_emergency(report, args.dry_run)
        print(f"  tier3 freed: {total_freed_mb - before_t3:.1f} MB")

    pct_after = disk_pct("/")
    report["pct_after"] = pct_after
    report["total_freed_mb"] = round(total_freed_mb, 1)

    print(f"\n  === FINAL: {pct_before}% → {pct_after}%  "
          f"freed {total_freed_mb:.1f} MB ===")

    # Alerts
    if pct_after >= EMERGENCY_PCT:
        emit_swarm("agent.disk_alert",
                   f"CRITICAL DISK {pct_after}% (target ≤{TARGET_PCT}%) "
                   f"— cleanup couldn't recover",
                   report, ["disk", "alert", "critical"])
    elif pct_after >= ALERT_PCT:
        emit_swarm("agent.disk_alert",
                   f"DISK ALERT {pct_after}% (target ≤{TARGET_PCT}%) "
                   f"— above threshold after cleanup",
                   report, ["disk", "alert"])

    # Always emit summary review row
    head = (f"DISK JANITOR {pct_before}%→{pct_after}% "
            f"freed={total_freed_mb:.0f}MB"
            + (" DRY_RUN" if args.dry_run else ""))
    emit_swarm("agent.review.disk_janitor", head, report,
               ["disk", "janitor", "review"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
