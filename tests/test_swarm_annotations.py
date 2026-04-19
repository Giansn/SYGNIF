"""prediction_agent/swarm_annotations.py"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_build_swarm_keypoints_maps_flow_nodes() -> None:
    sys.path.insert(0, str(REPO / "prediction_agent"))
    import swarm_annotations as sa  # noqa: E402

    sw = {
        "swarm_label": "SWARM_BULL",
        "swarm_mean": 0.5,
        "swarm_conflict": False,
        "swarm_engine": "python",
        "sources": {
            "ml": {"vote": 1, "detail": "BULLISH"},
            "ch": {"vote": 0, "detail": "flat50/50"},
        },
    }
    kps = sa.build_swarm_keypoints(sw)
    ids = {k["id"] for k in kps}
    assert "swarm_label" in ids
    assert "src_ml" in ids
    assert "src_ch" in ids
    ml = next(k for k in kps if k["id"] == "src_ml")
    assert ml["flow_node"] == "n-ml"
    assert ml["severity"] == "bull"


def test_build_swarm_keypoints_empty() -> None:
    sys.path.insert(0, str(REPO / "prediction_agent"))
    import swarm_annotations as sa  # noqa: E402

    kps = sa.build_swarm_keypoints(None)
    assert any(k["id"] == "swarm_missing" for k in kps)
