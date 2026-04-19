#!/usr/bin/env python3
"""
Delegate **swarm** fusion + **R01** registry into one governance packet (JSON-serializable).

Swarm implementation lives in ``finance_agent.swarm_knowledge``; this module only orchestrates.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from r01_registry_bridge import load_r01_governance
from r01_registry_bridge import registry_path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _ensure_finance_agent_import() -> None:
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _prediction_agent_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_training_channel_summary() -> dict[str, Any]:
    p = _prediction_agent_dir() / "training_channel_output.json"
    out: dict[str, Any] = {"path": str(p), "exists": False}
    if not p.is_file():
        return out
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        out["exists"] = True
        out["error"] = "invalid_json"
        return out
    out["exists"] = True
    out["generated_utc"] = raw.get("generated_utc")
    rec = raw.get("recognition") if isinstance(raw.get("recognition"), dict) else {}
    out["last_bar_probability_up_pct"] = rec.get("last_bar_probability_up_pct")
    out["last_bar_probability_down_pct"] = rec.get("last_bar_probability_down_pct")
    snap = rec.get("btc_predict_runner_snapshot") if isinstance(rec.get("btc_predict_runner_snapshot"), dict) else {}
    preds = snap.get("predictions") if isinstance(snap.get("predictions"), dict) else {}
    out["snapshot_consensus"] = preds.get("consensus")
    return out


@dataclass
class GovernancePacket:
    """Compact operator view: swarm + R01 + training channel summary."""

    generated_utc: str
    swarm: dict[str, Any]
    r01: dict[str, Any]
    training_channel: dict[str, Any]
    registry_path: str
    delegate_notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def r01_governance_dict() -> dict[str, Any]:
    en, p_min, cons = load_r01_governance()
    return {
        "enabled": en,
        "p_down_min_pct": p_min,
        "runner_consensus_equals": cons,
    }


def compute_governance_packet(*, include_training_summary: bool = True) -> GovernancePacket:
    _ensure_finance_agent_import()
    from finance_agent import swarm_knowledge as sk  # noqa: PLC0415

    swarm = sk.compute_swarm()
    notes: list[str] = [
        "swarm: finance_agent.swarm_knowledge.compute_swarm",
        "r01: r01_registry_bridge.load_r01_governance",
    ]
    tc: dict[str, Any] = {}
    if include_training_summary:
        tc = _load_training_channel_summary()
        notes.append("training_channel: prediction_agent/training_channel_output.json (summary only)")

    gen = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return GovernancePacket(
        generated_utc=gen,
        swarm=swarm,
        r01=r01_governance_dict(),
        training_channel=tc,
        registry_path=str(registry_path()),
        delegate_notes=notes,
    )


def write_governance_json(path: Path | None = None) -> Path:
    """Write ``btc_governance_output.json`` under ``prediction_agent/``."""
    pkt = compute_governance_packet()
    dest = path or (_prediction_agent_dir() / "btc_governance_output.json")
    dest.write_text(json.dumps(pkt.to_dict(), indent=2) + "\n", encoding="utf-8")
    return dest
