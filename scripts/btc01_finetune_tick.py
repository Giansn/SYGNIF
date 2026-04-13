#!/usr/bin/env python3
"""
One finetune tick for BTC 0.1: R01/R02 trade report + R01–R03 gate monitor.

Intended for ``sygnif-btc01-finetune.timer`` (host). Read-only except optional
CSV journal when ``RULE_TAG_JOURNAL_MONITOR=YES`` (see ``rule_tag_journal``).

Env:
  RULE_TAG_JOURNAL_MONITOR   default YES for this tick (override in unit if needed)
  BTC01_FINETUNE_REPORT_TRADES  passed to btc01_r01_r02_report.py (default 20)
  BTC01_FINETUNE_LOG          append human log (default ~/.local/share/sygnif/btc01_finetune_tick.log)
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _log_line(msg: str) -> None:
    raw = (os.environ.get("BTC01_FINETUNE_LOG") or "").strip()
    log_path = Path(raw).expanduser() if raw else Path.home() / ".local/share/sygnif/btc01_finetune_tick.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"{ts} {msg}\n")


def main() -> int:
    root = _root()
    py = sys.executable
    report = root / "scripts" / "btc01_r01_r02_report.py"
    monitor = root / "scripts" / "monitor_r01_r03_gate.py"
    if not report.is_file() or not monitor.is_file():
        _log_line("ERROR missing scripts")
        return 1

    env = os.environ.copy()
    if not (env.get("RULE_TAG_JOURNAL_MONITOR") or "").strip():
        env["RULE_TAG_JOURNAL_MONITOR"] = "YES"

    trades = (env.get("BTC01_FINETUNE_REPORT_TRADES") or "20").strip() or "20"

    r1 = subprocess.run(
        [py, str(report), "--trades", trades],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r1.returncode != 0:
        err = (r1.stderr or r1.stdout or "").strip()[:500]
        _log_line(f"report exit={r1.returncode} {err}")
        # still run monitor for journal continuity

    r2 = subprocess.run(
        [py, str(monitor)],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    summary = (r2.stdout or "").strip().replace("\n", " ")[:500]
    if r2.returncode != 0:
        err = (r2.stderr or summary or "").strip()[:500]
        _log_line(f"monitor exit={r2.returncode} {err}")
    else:
        _log_line(f"ok report_rc={r1.returncode} monitor={summary[:400]}")

    # propagate report failure if monitor also failed
    if r1.returncode != 0 and r2.returncode != 0:
        return max(r1.returncode, r2.returncode)
    return r2.returncode if r2.returncode != 0 else r1.returncode


if __name__ == "__main__":
    raise SystemExit(main())
