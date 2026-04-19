"""
Swarm → **annotations / keypoints**: stable, machine-readable facts for snapshot UI and tooling.

Each keypoint: ``id``, ``label``, ``value``, ``severity``, optional ``flow_node`` (SVG ``id`` in
``system_snapshot.html`` dataflow) to **connect** narrative to the graph.
"""

from __future__ import annotations

from typing import Any

# Map ``compute_swarm()`` source keys → dataflow node ``id`` in render_system_snapshot_html.
SOURCE_FLOW_NODE: dict[str, str] = {
    "ml": "n-ml",
    "ch": "n-ch",
    "sc": "n-sc",
    "ta": "n-ta",
    "mn": "n-fuse",
    "ac": "n-fuse",
    "bf": "n-fuse",
    "es": "n-fuse",
}


def _sev_from_vote(vote: int) -> str:
    if vote > 0:
        return "bull"
    if vote < 0:
        return "bear"
    return "neutral"


def build_swarm_keypoints(sw: dict[str, Any] | None) -> list[dict[str, Any]]:
    """
    Build keypoints from a full ``compute_swarm()`` dict (not the trimmed snapshot ``swarm`` block).

    Returns list of dicts: id, label, value, severity, flow_node (optional).
    """
    if not sw or not isinstance(sw, dict):
        return [
            {
                "id": "swarm_missing",
                "label": "Swarm",
                "value": "unavailable",
                "severity": "warn",
                "flow_node": "n-fuse",
            }
        ]

    kps: list[dict[str, Any]] = []

    label = sw.get("swarm_label")
    mean = sw.get("swarm_mean")
    conflict = bool(sw.get("swarm_conflict"))
    eng = sw.get("swarm_engine")
    eng_d = sw.get("swarm_engine_detail")

    kps.append(
        {
            "id": "swarm_label",
            "label": "Fused label",
            "value": str(label or "?"),
            "severity": "bull"
            if "BULL" in str(label or "").upper()
            else "bear"
            if "BEAR" in str(label or "").upper()
            else "mixed",
            "flow_node": "n-fuse",
        }
    )
    if mean is not None:
        kps.append(
            {
                "id": "swarm_mean",
                "label": "Mean vote",
                "value": str(mean),
                "severity": "neutral",
                "flow_node": "n-fuse",
            }
        )
    kps.append(
        {
            "id": "swarm_conflict",
            "label": "Conflict",
            "value": "yes" if conflict else "no",
            "severity": "warn" if conflict else "neutral",
            "flow_node": "n-fuse",
        }
    )
    if eng:
        kps.append(
            {
                "id": "swarm_engine",
                "label": "Engine",
                "value": f"{eng}" + (f" ({eng_d})" if eng_d else ""),
                "severity": "neutral",
                "flow_node": "n-fuse",
            }
        )

    missing = sw.get("missing_files")
    if isinstance(missing, list) and missing:
        kps.append(
            {
                "id": "swarm_missing_files",
                "label": "Missing files",
                "value": ", ".join(str(x) for x in missing[:12]),
                "severity": "warn",
                "flow_node": None,
            }
        )

    parts = sw.get("sources")
    if isinstance(parts, dict):
        for name in sorted(parts.keys()):
            block = parts[name]
            if not isinstance(block, dict):
                continue
            try:
                vote = int(block.get("vote", 0))
            except (TypeError, ValueError):
                vote = 0
            detail = str(block.get("detail") or "?")
            kps.append(
                {
                    "id": f"src_{name}",
                    "label": f"Source {name.upper()}",
                    "value": detail,
                    "severity": _sev_from_vote(vote),
                    "flow_node": SOURCE_FLOW_NODE.get(name),
                }
            )

    ot = sw.get("open_trades")
    if isinstance(ot, dict) and ot.get("source") not in (None, "error"):
        try:
            n = int(ot.get("open_n") or 0)
        except (TypeError, ValueError):
            n = 0
        src = str(ot.get("source") or "?")
        kps.append(
            {
                "id": "swarm_open_trades",
                "label": "Open trades",
                "value": f"{n} ({src})",
                "severity": "neutral",
                "flow_node": "n-fuse",
            }
        )

    return kps
