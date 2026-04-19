"""
When ``SYGNIF_SWARM_ACTIVATE_IMPROVEMENTS=1``, apply **setdefault** overrides for:

- Live ML ↔ Truthcoin **Hivemind fusion** (``SYGNIF_PREDICT_HIVEMIND_FUSION``).
- Demo **closed PnL** telemetry in Swarm JSON (``SYGNIF_SWARM_BYBIT_CLOSED_PNL``).
- **Strategy guideline** gate (``SWARM_ORDER_SYGNIF_STRATEGY_GUIDELINES``) plus **guideline ↔ Hivemind fusion**
  (``SWARM_ORDER_GUIDELINE_HIVEMIND_FUSION``) and **unreachable-ML substitute** when explore is down
  (``SWARM_ORDER_GUIDELINE_HIVEMIND_UNREACHABLE_ML``).
- Optional **Truthcoin DC CLI** path discovery under ``SYGNIF_TRUTHCOIN_DC_ROOT`` (default ``~/truthcoin-dc``).

Swarm / Hivemind **core** flags (``SYGNIF_SWARM_TRUTHCOIN_DC``, ``SYGNIF_SWARM_CORE_ENGINE``, …) stay in
``swarm_operator.env``; this bundle only fills gaps so BTC Truth Hivemind is wired through **predict** + **gates**.
"""
from __future__ import annotations

import os
from pathlib import Path


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def apply_swarm_activate_improvements_defaults() -> list[str]:
    """
    If ``SYGNIF_SWARM_ACTIVATE_IMPROVEMENTS`` is on, ``setdefault`` known-good knobs.

    Returns list of keys newly set (for launcher logging).
    """
    if not _env_truthy("SYGNIF_SWARM_ACTIVATE_IMPROVEMENTS"):
        return []
    pairs = {
        "SYGNIF_PREDICT_HIVEMIND_FUSION": "1",
        "SYGNIF_SWARM_BYBIT_CLOSED_PNL": "1",
        "SWARM_ORDER_SYGNIF_STRATEGY_GUIDELINES": "1",
        "SWARM_ORDER_GUIDELINE_HIVEMIND_FUSION": "1",
        "SWARM_ORDER_GUIDELINE_HIVEMIND_UNREACHABLE_ML": "1",
    }
    applied: list[str] = []
    for k, v in pairs.items():
        if not (os.environ.get(k) or "").strip():
            os.environ[k] = v
            applied.append(k)

    if not (os.environ.get("SYGNIF_TRUTHCOIN_DC_ROOT") or "").strip():
        os.environ["SYGNIF_TRUTHCOIN_DC_ROOT"] = str((Path.home() / "truthcoin-dc").resolve())
        applied.append("SYGNIF_TRUTHCOIN_DC_ROOT")
    tc_root = Path(os.environ["SYGNIF_TRUTHCOIN_DC_ROOT"]).expanduser().resolve()

    if not (os.environ.get("SYGNIF_TRUTHCOIN_DC_CLI") or "").strip():
        for sub in ("target/debug/truthcoin_dc_app_cli", "target/release/truthcoin_dc_app_cli"):
            guess = tc_root / sub
            if guess.is_file() and os.access(guess, os.X_OK):
                os.environ["SYGNIF_TRUTHCOIN_DC_CLI"] = str(guess.resolve())
                applied.append("SYGNIF_TRUTHCOIN_DC_CLI")
                break

    return applied
