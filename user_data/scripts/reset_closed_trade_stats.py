#!/usr/bin/env python3
"""
Reset *closed-trade* aggregates for Freqtrade dashboard + Telegram /status.

Both UIs read RPC profit/trade stats from the SQLite DB. This script removes
only closed trades (and their order/custom rows) after a timestamped backup.

Does NOT modify:
  - config.json / config_futures.json (pairlists, stake, telegram, API, etc.)
  - strategy files, hyperopts, .env
  - open positions (is_open=1 trades are kept)

Stop the matching bot container(s) before running to avoid WAL corruption.

Examples:
  python user_data/scripts/reset_closed_trade_stats.py --futures
  python user_data/scripts/reset_closed_trade_stats.py --spot
  python user_data/scripts/reset_closed_trade_stats.py --both
  python user_data/scripts/reset_closed_trade_stats.py --both --dry-run
  python user_data/scripts/reset_closed_trade_stats.py --futures --also-doom-cooldown
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # .../sygnif
USER_DATA = ROOT / "user_data"
BACKUP_DIR = USER_DATA / "backups"


def _tables(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {r[0] for r in cur.fetchall()}


def _count_closed(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM trades WHERE is_open = 0")
    return int(cur.fetchone()[0])


def reset_closed_trades(conn: sqlite3.Connection, dry_run: bool) -> dict[str, int]:
    tables = _tables(conn)
    if "trades" not in tables:
        raise SystemExit("No trades table — not a Freqtrade DB?")

    n_closed = _count_closed(conn)
    deleted: dict[str, int] = {"trades_closed": n_closed}

    if dry_run:
        return deleted

    cur = conn.cursor()
    if "orders" in tables:
        for clause in (
            "trade_id IN (SELECT id FROM trades WHERE is_open = 0)",
            "ft_trade_id IN (SELECT id FROM trades WHERE is_open = 0)",
        ):
            try:
                cur.execute(f"DELETE FROM orders WHERE {clause}")
                deleted["orders"] = cur.rowcount
                break
            except sqlite3.OperationalError:
                continue
    if "trade_custom_data" in tables:
        try:
            cur.execute(
                "DELETE FROM trade_custom_data WHERE trade_id IN "
                "(SELECT id FROM trades WHERE is_open = 0)"
            )
            deleted["trade_custom_data"] = cur.rowcount
        except sqlite3.OperationalError:
            pass

    cur.execute("DELETE FROM trades WHERE is_open = 0")
    deleted["trades_deleted"] = cur.rowcount
    conn.commit()
    return deleted


def backup_db(src: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dst = BACKUP_DIR / f"{src.stem}_{ts}{src.suffix}"
    shutil.copy2(src, dst)
    return dst


def reset_doom_cooldown(dry_run: bool) -> None:
    path = USER_DATA / "doom_cooldown.json"
    if not path.exists():
        print("No doom_cooldown.json — skip.")
        return
    if dry_run:
        print(f"Would truncate {path} (cooldowns + loss_counts).")
        return
    path.write_text('{"cooldowns": {}, "loss_counts": {}}\n', encoding="utf-8")
    print(f"Reset {path} (empty cooldowns / loss_counts).")


def run_one(db_filename: str, *, dry_run: bool) -> int:
    """Returns 0 ok, 1 missing file, 2 error."""
    db_path = USER_DATA / db_filename
    if not db_path.is_file():
        print(f"Missing (skip): {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        n_closed = _count_closed(conn)
        n_open = int(conn.execute("SELECT COUNT(*) FROM trades WHERE is_open = 1").fetchone()[0])
        print(f"\n=== {db_path.name} === open={n_open} closed={n_closed}")
    finally:
        conn.close()

    if dry_run:
        print("  (dry-run: no backup/delete)")
        return 0

    conn = sqlite3.connect(str(db_path))
    try:
        dst = backup_db(db_path)
        print(f"  Backup: {dst}")
        stats = reset_closed_trades(conn, dry_run=False)
        print("  Done:", stats)
    except Exception as e:  # noqa: BLE001
        print(f"  ERROR: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Reset closed-trade stats (DB) for dashboard + Telegram.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--futures", action="store_true", help="tradesv3-futures.sqlite only")
    g.add_argument("--spot", action="store_true", help="tradesv3.sqlite only")
    g.add_argument("--both", action="store_true", help="futures then spot (skip missing files)")
    p.add_argument("--dry-run", action="store_true", help="Show counts only, no writes.")
    p.add_argument(
        "--also-doom-cooldown",
        action="store_true",
        help="Clear user_data/doom_cooldown.json (re-entry locks only; not config/calib).",
    )
    args = p.parse_args()

    if args.both:
        order = ("tradesv3-futures.sqlite", "tradesv3.sqlite")
    elif args.futures:
        order = ("tradesv3-futures.sqlite",)
    else:
        order = ("tradesv3.sqlite",)

    exit_code = 0
    for name in order:
        rc = run_one(name, dry_run=args.dry_run)
        if rc == 2:
            exit_code = 2
        # missing file on --both is OK (warn only)
        if rc == 1 and not args.both:
            exit_code = 1

    if args.dry_run and args.also_doom_cooldown:
        reset_doom_cooldown(dry_run=True)
    elif args.also_doom_cooldown and not args.dry_run:
        reset_doom_cooldown(dry_run=False)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
