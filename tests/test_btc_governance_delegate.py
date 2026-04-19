"""BTC governance delegate (swarm + R01 wiring)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def gov_paths():
    pa = REPO / "prediction_agent"
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    if str(pa) not in sys.path:
        sys.path.insert(0, str(pa))


def test_compute_governance_packet_includes_r01(gov_paths) -> None:
    from btc_governance.delegate import compute_governance_packet

    fake_swarm = {
        "swarm_label": "SWARM_MIXED",
        "swarm_mean": 0.0,
        "sources_n": 4,
        "sources": {},
    }

    with patch("finance_agent.swarm_knowledge.compute_swarm", return_value=fake_swarm):
        pkt = compute_governance_packet(include_training_summary=False)
    assert pkt.swarm == fake_swarm
    assert "p_down_min_pct" in pkt.r01
    assert pkt.training_channel == {}
    assert any("swarm_knowledge" in n for n in pkt.delegate_notes)


def test_archive_dry_run_no_crash(gov_paths, tmp_path: Path) -> None:
    from btc_governance.archive import archive_one_file
    from btc_governance.archive import run_archive_pass

    f = tmp_path / "old.log"
    f.write_text("x" * 100, encoding="utf-8")
    lines = run_archive_pass(
        repo_root=tmp_path,
        days=0.0,
        globs=["old.log"],
        dry_run=True,
    )
    assert any("dry_run" in x for x in lines)
    assert f.is_file()

    msg = archive_one_file(f, dry_run=True)
    assert msg and "dry_run" in msg
