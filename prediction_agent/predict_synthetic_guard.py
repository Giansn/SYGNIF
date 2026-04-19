"""
Block **new** predict-loop entries when optional guardrails say **no directional trade**.

Reads (best-effort, missing files → no block):

- ``prediction_agent/btc_24h_movement_prediction.json`` — ``synthesis.bias_24h == NEUTRAL`` (24h blend flat).
- ``prediction_agent/swarm_btc_synth.json`` — ``order_signal`` HOLD + ``side`` FLAT (Swarm card / conflict).

Enable in ``btc_predict_protocol_loop`` with ``SYGNIF_PREDICT_BLOCK_SYNTHETIC_HOLD=1`` (see script docstring).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from swarm_btc_flow_constants import K_ORDER_SIGNAL
from swarm_btc_flow_constants import K_SIDE


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def evaluate_synthetic_entry_block(repo: Path) -> tuple[bool, str]:
    """
    Return (should_block_new_entry, compact_reason).

    Missing JSON files do **not** block (fail-open) so a minimal install still runs.
    """
    reasons: list[str] = []
    pa = repo / "prediction_agent"

    m24 = _read_json(pa / "btc_24h_movement_prediction.json")
    syn = m24.get("synthesis") if isinstance(m24.get("synthesis"), dict) else {}
    bias = str(syn.get("bias_24h") or "").strip().upper()
    if bias == "NEUTRAL":
        reasons.append("24h_NEUTRAL")

    card = _read_json(pa / "swarm_btc_synth.json")
    sig = str(card.get(K_ORDER_SIGNAL) or "").strip().upper()
    side = str(card.get(K_SIDE) or "").strip().upper()
    if sig == "HOLD" and side == "FLAT":
        reasons.append("swarm_card_HOLD_FLAT")

    if not reasons:
        return False, ""
    return True, "+".join(reasons)
