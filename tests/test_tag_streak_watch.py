"""Tests for entry-tag loss streak detection (sentiment_health_watch)."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from sentiment_health_watch import find_entry_tag_loss_streaks  # noqa: E402


def _make_db(rows: list[tuple[str, float]]) -> Path:
    fd, name = tempfile.mkstemp(suffix=".sqlite")
    import os

    os.close(fd)
    p = Path(name)
    conn = sqlite3.connect(str(p))
    conn.execute(
        """CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            is_open INTEGER NOT NULL,
            enter_tag TEXT,
            close_date TEXT NOT NULL,
            close_profit REAL
        )"""
    )
    for dt, pr in rows:
        conn.execute(
            "INSERT INTO trades(is_open, enter_tag, close_date, close_profit) VALUES (0, 'foo', ?, ?)",
            (dt, pr),
        )
    conn.commit()
    conn.close()
    return p


def test_five_consecutive_losses_triggers():
    p = _make_db(
        [
            ("2024-01-05", 0.05),
            ("2024-01-06", -0.01),
            ("2024-01-07", -0.02),
            ("2024-01-08", -0.01),
            ("2024-01-09", -0.01),
            ("2024-01-10", -0.01),
        ]
    )
    try:
        hits = find_entry_tag_loss_streaks(p, "test", streak=5, scan_limit=50)
        assert len(hits) == 1
        assert hits[0][0] == "foo"
        assert "last **5** closes all losses" in hits[0][1]
    finally:
        p.unlink(missing_ok=True)


def test_four_losses_no_trigger():
    p = _make_db(
        [
            ("2024-01-07", -0.01),
            ("2024-01-08", -0.02),
            ("2024-01-09", -0.01),
            ("2024-01-10", -0.01),
        ]
    )
    try:
        hits = find_entry_tag_loss_streaks(p, "test", streak=5, scan_limit=50)
        assert hits == []
    finally:
        p.unlink(missing_ok=True)


def test_breakeven_breaks_streak():
    p = _make_db(
        [
            ("2024-01-05", -0.01),
            ("2024-01-06", 0.0),
            ("2024-01-07", -0.01),
            ("2024-01-08", -0.02),
            ("2024-01-09", -0.01),
            ("2024-01-10", -0.01),
        ]
    )
    try:
        hits = find_entry_tag_loss_streaks(p, "test", streak=5, scan_limit=50)
        assert hits == []
    finally:
        p.unlink(missing_ok=True)
