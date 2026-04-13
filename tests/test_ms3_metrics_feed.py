"""Tests for scripts/ms3_metrics_feed.py (compact helpers)."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ms3_metrics_feed import _compact_family, _compact_perf_analysis  # noqa: E402


def test_compact_family_trims_exit_reasons():
    stats = {
        "count": 10,
        "win_rate": 55.0,
        "sharpe_ratio": 1.2,
        "exit_reasons": {"a": 5, "b": 3, "c": 2, "d": 1, "e": 1, "f": 1},
    }
    c = _compact_family(stats)
    assert c["count"] == 10
    assert len(c["exit_reasons_top5"]) == 5
    assert "f" not in c["exit_reasons_top5"]


def test_compact_perf_analysis_error_passthrough():
    assert _compact_perf_analysis({"error": "x"}) == {"error": "x"}


def test_build_bundle_minimal_smoke():
    from ms3_metrics_feed import build_ms3_metrics_bundle

    b = build_ms3_metrics_bundle(REPO, windows=(7,), append_entry_perf_log=False)
    assert b.get("schema") == "ms3_metrics_bundle"
    assert "perf_nt" in b
    assert "7d" in b["perf_nt"].get("spot", {}) or "error" in b["perf_nt"].get("spot", {})
