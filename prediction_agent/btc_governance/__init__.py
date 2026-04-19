"""
BTC governance package: delegate swarm predictions + R01 registry context.

Import with ``PYTHONPATH`` including repo root and ``prediction_agent/``::

    export PYTHONPATH="$PWD:$PWD/prediction_agent"
    from btc_governance.delegate import compute_governance_packet
"""

from __future__ import annotations

from .delegate import GovernancePacket
from .delegate import compute_governance_packet

__all__ = [
    "GovernancePacket",
    "compute_governance_packet",
]
