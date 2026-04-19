"""Read persisted Swarm JSON for overseer prompts (no finance_agent import)."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("overseer.swarm_snapshot")


def _prediction_agent_dir() -> Path:
    for key in ("SYGNIF_PREDICTION_AGENT_DIR", "PREDICTION_AGENT_DIR"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return Path(raw).expanduser()
    return Path(__file__).resolve().parent.parent / "prediction_agent"


def load_swarm_knowledge_path() -> Path:
    return _prediction_agent_dir() / "swarm_knowledge_output.json"


def load_swarm_dict() -> dict[str, Any] | None:
    path = load_swarm_knowledge_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("swarm snapshot read failed: %s", exc)
        return None


def format_swarm_prompt_line(sk: dict[str, Any], *, max_chars: int = 420) -> str:
    parts = [
        f"SWARM|utc={sk.get('generated_utc', '?')}",
        f"mean={float(sk.get('swarm_mean', 0)):+.3f}",
        str(sk.get("swarm_label") or "?"),
    ]
    if sk.get("swarm_conflict"):
        parts.append("CONFLICT")
    src = sk.get("sources") if isinstance(sk.get("sources"), dict) else {}
    for k in ("ml", "ch", "sc", "ta", "bf", "hm"):
        if k in src and isinstance(src[k], dict):
            parts.append(f"{k}={src[k].get('detail', '?')}")
    bf = sk.get("btc_future") if isinstance(sk.get("btc_future"), dict) else {}
    pos = bf.get("position") if isinstance(bf.get("position"), dict) else {}
    if pos and not pos.get("flat"):
        parts.append(f"bf_pos={pos.get('side', '?')}:{pos.get('size', '?')}")
    elif bf.get("enabled"):
        parts.append("bf_pos=flat")
    ot = sk.get("open_trades") if isinstance(sk.get("open_trades"), dict) else {}
    if ot.get("ok") and ot.get("open_n") is not None:
        parts.append(f"open_n={ot.get('open_n')}")
    line = "|".join(parts)
    return line[:max_chars]


def swarm_prompt_block() -> str:
    """Non-empty when ``OVERSEER_INCLUDE_SWARM_SNAPSHOT`` is on and JSON exists."""
    if os.environ.get("OVERSEER_INCLUDE_SWARM_SNAPSHOT", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return ""
    sk = load_swarm_dict()
    if not sk:
        return ""
    return format_swarm_prompt_line(sk)


def swarm_long_entry_allowed() -> tuple[bool, str]:
    """
    Optional gate for ``ensure_entry`` long bias.

    When ``OVERSEER_ENSURE_SWARM_GATE=1``, block long ``forceenter`` if label is bearish
    or mean is below ``OVERSEER_ENSURE_SWARM_MIN_MEAN`` (default ``-0.25``).
    """
    if os.environ.get("OVERSEER_ENSURE_SWARM_GATE", "").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return True, "swarm_gate_off"
    sk = load_swarm_dict()
    if not sk:
        return True, "swarm_snapshot_missing"
    label = str(sk.get("swarm_label") or "").upper()
    try:
        mean = float(sk.get("swarm_mean", 0))
    except (TypeError, ValueError):
        mean = 0.0
    raw_min = (os.environ.get("OVERSEER_ENSURE_SWARM_MIN_MEAN") or "").strip()
    try:
        min_mean = float(raw_min) if raw_min else -0.25
    except ValueError:
        min_mean = -0.25
    if "BEAR" in label or label.endswith("_BEAR"):
        return False, f"swarm_label_bearish({label})"
    if mean < min_mean:
        return False, f"swarm_mean_below_gate({mean:.3f}<{min_mean:.3f})"
    return True, "swarm_ok"
