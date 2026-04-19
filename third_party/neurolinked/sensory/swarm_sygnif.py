"""
SYGNIF **Swarm (BTC) knowledge** → NeuroLinked sensory **text** channel.

Used when NeuroLinked is run from ``SYGNIF/third_party/neurolinked``. Resolves the SYGNIF repo root
(three levels above this file: ``sensory`` → ``neurolinked`` → ``third_party`` → repo).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def _sygnif_repo() -> Path:
    return Path(__file__).resolve().parents[3]


def inject_sygnif_swarm_btc(brain: Any, *, write_channel: bool = True) -> dict[str, Any]:
    """
    Pull ``compute_swarm()`` and push into ``brain`` via ``inject_sensory_input("text", …)``.

    Returns a small metadata dict (safe to log).
    """
    repo = _sygnif_repo()
    fa = str(repo / "finance_agent")
    if fa not in sys.path:
        sys.path.insert(0, fa)
    from neurolinked_swarm_adapter import NeurolinkedSwarmBridge  # noqa: PLC0415

    bridge = NeurolinkedSwarmBridge(repo)
    return bridge.inject_into_brain(brain, write_channel=write_channel)
