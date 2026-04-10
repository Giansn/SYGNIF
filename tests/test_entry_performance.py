"""Tests for entry_performance.py — tag-level performance analysis."""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "trade_overseer"))

from entry_performance import (
    classify,
    aggregate,
    TagStats,
    _duration_hours,
    fetch_trades_sqlite,
    report_text,
    report_json,
    append_log,
    GHOSTED_EXIT_REASONS,
)


# ---------------------------------------------------------------------------
# classify()
# ---------------------------------------------------------------------------

class TestClassify:
    def test_swing_failure_long(self):
        assert classify("swing_failure") == "swing_failure"

    def test_swing_failure_short(self):
        assert classify("swing_failure_short") == "swing_failure"

    def test_claude_swing_long(self):
        assert classify("claude_swing") == "claude_swing"

    def test_claude_swing_short(self):
        assert classify("claude_swing_short") == "claude_swing"

    def test_claude_s_zero(self):
        assert classify("claude_s0") == "claude_s"

    def test_claude_s_positive(self):
        assert classify("claude_s5") == "claude_s"

    def test_claude_s_negative(self):
        assert classify("claude_s-5") == "claude_s"

    def test_claude_short_s(self):
        assert classify("claude_short_s3") == "claude_s"

    def test_claude_short_s_negative(self):
        assert classify("claude_short_s-2") == "claude_s"

    def test_strong_ta_excluded(self):
        assert classify("strong_ta") is None

    def test_strong_ta_short_excluded(self):
        assert classify("strong_ta_short") is None

    def test_none_tag(self):
        assert classify(None) is None

    def test_empty_tag(self):
        assert classify("") is None


# ---------------------------------------------------------------------------
# _duration_hours()
# ---------------------------------------------------------------------------

class TestDurationHours:
    def test_standard_format(self):
        h = _duration_hours("2026-04-01 10:00:00", "2026-04-01 12:30:00")
        assert h == pytest.approx(2.5, abs=0.01)

    def test_iso_format(self):
        h = _duration_hours("2026-04-01T10:00:00", "2026-04-01T13:00:00")
        assert h == pytest.approx(3.0, abs=0.01)

    def test_with_microseconds(self):
        h = _duration_hours("2026-04-01 10:00:00.123456", "2026-04-01 11:00:00.654321")
        assert h is not None
        assert h == pytest.approx(1.0, abs=0.01)

    def test_with_tz_suffix(self):
        h = _duration_hours("2026-04-01 10:00:00+00:00", "2026-04-01 12:00:00+00:00")
        assert h == pytest.approx(2.0, abs=0.01)

    def test_invalid_returns_none(self):
        assert _duration_hours("bad", "data") is None

    def test_none_returns_none(self):
        assert _duration_hours(None, None) is None


# ---------------------------------------------------------------------------
# aggregate()
# ---------------------------------------------------------------------------

def _make_trade(tag: str, profit: float, open_d: str = "2026-04-01 10:00:00",
                close_d: str = "2026-04-01 12:00:00", is_short: bool = False,
                exit_reason: str = "stoploss_on_exchange"):
    return {
        "id": 1,
        "pair": "BTC/USDT",
        "enter_tag": tag,
        "exit_reason": exit_reason,
        "is_open": False,
        "is_short": is_short,
        "leverage": 3,
        "close_profit": profit / 100,  # stored as ratio
        "open_date": open_d,
        "close_date": close_d,
        "instance": "futures",
    }


class TestAggregate:
    def test_basic_counts(self):
        trades = [
            _make_trade("claude_s0", 2.5),
            _make_trade("claude_s0", -1.0),
            _make_trade("claude_s5", 3.0),
            _make_trade("swing_failure", -5.0),
        ]
        stats = aggregate(trades)
        assert stats["claude_s"].n == 3
        assert stats["claude_s"].wins == 2
        assert stats["claude_s"].losses == 1
        assert stats["swing_failure"].n == 1

    def test_win_rate(self):
        trades = [
            _make_trade("claude_s0", 1.0),
            _make_trade("claude_s0", 2.0),
            _make_trade("claude_s0", -1.0),
        ]
        stats = aggregate(trades)
        assert stats["claude_s"].win_rate == pytest.approx(66.67, abs=0.1)

    def test_avg_profit(self):
        trades = [
            _make_trade("claude_swing", 4.0),
            _make_trade("claude_swing", -2.0),
        ]
        stats = aggregate(trades)
        assert stats["claude_swing"].avg_profit == pytest.approx(1.0, abs=0.01)

    def test_best_worst(self):
        trades = [
            _make_trade("swing_failure", 5.0),
            _make_trade("swing_failure", -3.0),
            _make_trade("swing_failure", 1.0),
        ]
        stats = aggregate(trades)
        assert stats["swing_failure"].best == pytest.approx(5.0)
        assert stats["swing_failure"].worst == pytest.approx(-3.0)

    def test_by_tag_breakdown(self):
        trades = [
            _make_trade("claude_s0", 1.0),
            _make_trade("claude_s5", 2.0),
            _make_trade("claude_s-5", 3.0),
        ]
        stats = aggregate(trades)
        assert "claude_s0" in stats["claude_s"].by_tag
        assert "claude_s5" in stats["claude_s"].by_tag
        assert "claude_s-5" in stats["claude_s"].by_tag
        assert stats["claude_s"].by_tag["claude_s0"].n == 1
        assert stats["claude_s"].by_tag["claude_s5"].n == 1

    def test_short_tags_classified(self):
        trades = [
            _make_trade("claude_short_s3", -2.0, is_short=True),
            _make_trade("claude_swing_short", 1.0, is_short=True),
            _make_trade("swing_failure_short", -1.0, is_short=True),
        ]
        stats = aggregate(trades)
        assert stats["claude_s"].n == 1
        assert stats["claude_swing"].n == 1
        assert stats["swing_failure"].n == 1

    def test_unrelated_tags_ignored(self):
        trades = [
            _make_trade("strong_ta", 5.0),
            _make_trade("strong_ta_short", -3.0, is_short=True),
        ]
        stats = aggregate(trades)
        assert all(s.n == 0 for s in stats.values())

    def test_duration_tracked(self):
        trades = [
            _make_trade("claude_s0", 1.0,
                        open_d="2026-04-01 10:00:00",
                        close_d="2026-04-01 13:00:00"),  # 3h
        ]
        stats = aggregate(trades)
        assert stats["claude_s"].avg_duration_h == pytest.approx(3.0, abs=0.01)

    def test_empty_input(self):
        stats = aggregate([])
        assert all(s.n == 0 for s in stats.values())


# ---------------------------------------------------------------------------
# SQLite fetch
# ---------------------------------------------------------------------------

@pytest.fixture
def test_db(tmp_path):
    db_path = tmp_path / "test_trades.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            pair TEXT,
            enter_tag TEXT,
            exit_reason TEXT,
            is_open INTEGER,
            is_short INTEGER,
            leverage REAL,
            close_profit REAL,
            open_date TEXT,
            close_date TEXT
        )
    """)
    trades = [
        (1, "BTC/USDT", "claude_s0", "stoploss_on_exchange", 0, 0, 3, 0.02, "2026-04-01 10:00:00", "2026-04-01 12:00:00"),
        (2, "ETH/USDT", "claude_s5", "exit_profit_rsi_1", 0, 0, 3, 0.05, "2026-04-01 11:00:00", "2026-04-01 14:00:00"),
        (3, "SOL/USDT", "swing_failure", "exit_sf_vol_sl", 0, 0, 3, -0.03, "2026-04-01 12:00:00", "2026-04-01 13:00:00"),
        (4, "XRP/USDT", "claude_swing", "exit_willr_reversal", 0, 0, 3, 0.04, "2026-04-01 13:00:00", "2026-04-01 16:00:00"),
        (5, "BTC/USDT", "strong_ta", "stoploss_on_exchange", 0, 0, 1, -0.10, "2026-04-01 10:00:00", "2026-04-01 11:00:00"),
        (6, "BTC/USDT", "claude_s0", "force_exit", 0, 0, 3, -0.01, "2026-04-01 10:00:00", "2026-04-01 11:00:00"),  # ghosted
        (7, "BTC/USDT", "claude_short_s3", "exit_short_profit_rsi_1", 0, 1, 3, 0.03, "2026-04-01 10:00:00", "2026-04-01 12:00:00"),
        (8, "BTC/USDT", "claude_s0", "stoploss_on_exchange", 1, 0, 3, None, "2026-04-01 15:00:00", None),  # still open
    ]
    conn.executemany(
        "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", trades
    )
    conn.commit()
    conn.close()
    return db_path


class TestSQLiteFetch:
    def test_fetches_closed_only(self, test_db):
        trades = fetch_trades_sqlite({"test": test_db}, days=0, side_filter="both")
        assert all(not t["is_open"] for t in trades)

    def test_excludes_ghosted(self, test_db):
        trades = fetch_trades_sqlite({"test": test_db}, days=0, side_filter="both")
        tags = [t["enter_tag"] for t in trades]
        assert tags.count("claude_s0") == 1  # force_exit one excluded

    def test_side_filter_long(self, test_db):
        trades = fetch_trades_sqlite({"test": test_db}, days=0, side_filter="long")
        assert all(not t.get("is_short") for t in trades)

    def test_side_filter_short(self, test_db):
        trades = fetch_trades_sqlite({"test": test_db}, days=0, side_filter="short")
        assert all(t.get("is_short") for t in trades)

    def test_missing_db_skipped(self, tmp_path):
        trades = fetch_trades_sqlite(
            {"gone": tmp_path / "nonexistent.sqlite"}, days=0, side_filter="both"
        )
        assert trades == []


# ---------------------------------------------------------------------------
# report_text (smoke test — just ensure no crash)
# ---------------------------------------------------------------------------

class TestReportText:
    def test_no_crash_with_data(self, capsys):
        trades = [
            _make_trade("claude_s0", 2.0),
            _make_trade("claude_s5", -1.0),
            _make_trade("swing_failure", 3.0),
            _make_trade("claude_swing", -0.5),
        ]
        stats = aggregate(trades)
        report_text(stats, "test scope", "claude_s0")
        out = capsys.readouterr().out
        assert "ENTRY-TAG PERFORMANCE ANALYSIS" in out
        assert "swing_failure" in out

    def test_no_crash_empty(self, capsys):
        stats = aggregate([])
        report_text(stats, "empty", "claude_s0")

    def test_baseline_marker(self, capsys):
        trades = [
            _make_trade("claude_s0", 2.0),
            _make_trade("claude_s5", 3.0),
        ]
        stats = aggregate(trades)
        report_text(stats, "test", "claude_s0")
        out = capsys.readouterr().out
        assert "baseline" in out.lower()


# ---------------------------------------------------------------------------
# report_json
# ---------------------------------------------------------------------------

class TestReportJson:
    def test_valid_json(self, capsys):
        trades = [
            _make_trade("claude_s0", 2.0),
            _make_trade("swing_failure", -1.0),
        ]
        stats = aggregate(trades)
        report_json(stats, "test", "claude_s0")
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "families" in data
        assert "claude_s" in data["families"]
        assert data["families"]["claude_s"]["n"] == 1


# ---------------------------------------------------------------------------
# append_log
# ---------------------------------------------------------------------------

class TestAppendLog:
    def test_creates_log_file(self, tmp_path):
        trades = [_make_trade("claude_s0", 1.0)]
        stats = aggregate(trades)
        log_path = tmp_path / "logs" / "test.jsonl"
        append_log(stats, "test", log_path)
        assert log_path.exists()
        record = json.loads(log_path.read_text().strip())
        assert "families" in record
        assert record["families"]["claude_s"]["n"] == 1
