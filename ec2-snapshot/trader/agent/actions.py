"""sygnif typed actions — Hummingbot ExecutorAction pattern, retail scale.

A controller's `determine_actions()` returns a list of these dataclasses
INSTEAD of executing side effects directly. A separate executor consumes
the list. This makes dry-run, replay, and audit trail trivial.

Shapes:

    CreateOrderAction      — perp/spot order (Bybit, Hyperliquid, Drift, ...)
    ClosePositionAction    — close a known open position
    StopOrderAction        — cancel a pending order by id
    StakeAction            — Jito/Lido/Marinade liquid staking
    SwapAction             — D'CENT/Jupiter/0x DEX swap
    ActionOutcome          — what came back after execution

Every action carries (id, controller_id, decided_at_utc, reason). Persist
the action to swarm.db `topic="action.proposed"` BEFORE execution and the
outcome to `topic="action.outcome"` AFTER. That gives the friend-facing
audit a complete decided→executed trail.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional


def _now_utc() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Triple-Barrier exit config — bundled at position-open time
# ---------------------------------------------------------------------------


@dataclass
class TripleBarrierConfig:
    """Exit conditions attached to a position at the moment it's opened.
    All fields optional; None = barrier inactive. Mirrors Hummingbot's
    TripleBarrierConfig pattern.

    Conventions:
        take_profit_pct / stop_loss_pct: percentage move from entry
            (e.g. 0.02 = 2% TP, 0.01 = 1% SL)
        trailing_stop_pct: drawdown from high-water-mark to trigger close
            (e.g. 0.005 = 0.5% trail)
        time_limit_seconds: max hold duration; close at expiry regardless of P&L
    """

    take_profit_pct:    Optional[float] = None
    stop_loss_pct:      Optional[float] = None
    trailing_stop_pct:  Optional[float] = None
    time_limit_seconds: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def is_empty(self) -> bool:
        return all(v is None for v in asdict(self).values())


# ---------------------------------------------------------------------------
# Base action — every concrete action inherits these fields
# ---------------------------------------------------------------------------


@dataclass
class ActionBase:
    """Common envelope: every action has a stable id + provenance."""

    action_id: str = field(default_factory=_new_id)
    controller_id: str = "main"           # which controller produced this
    decided_at_utc: str = field(default_factory=_now_utc)
    reason: str = ""                       # human-readable WHY (sizing-tuner trace, rule fired, etc.)


# ---------------------------------------------------------------------------
# Concrete actions — what controllers emit
# ---------------------------------------------------------------------------


@dataclass
class CreateOrderAction(ActionBase):
    """Open a new perp/spot position."""

    venue:        str = ""                                                # bybit_paper | hyperliquid | drift | solana_spot
    symbol:       str = ""                                                # BTCUSDT, SOL-PERP, ETH/USDT
    side:         Literal["long", "short", "buy", "sell"] = "long"
    qty:          float = 0.0                                              # base-asset units
    order_type:   Literal["market", "limit"] = "market"
    limit_price:  Optional[float] = None
    leverage:     Optional[float] = None
    barrier:      Optional[TripleBarrierConfig] = None


@dataclass
class ClosePositionAction(ActionBase):
    """Close a known open position. size omitted = full close."""

    position_id: str = ""
    venue:       str = ""
    symbol:      str = ""
    size:        Optional[float] = None    # partial close
    why:         str = ""                  # take_profit | stop_loss | trailing_stop | time_limit | signal_flip | manual


@dataclass
class StopOrderAction(ActionBase):
    """Cancel a resting order. Position remains untouched."""

    order_id:      str = ""
    keep_position: bool = False


@dataclass
class StakeAction(ActionBase):
    """Liquid staking (Jito, Lido, Marinade). venue identifies provider."""

    venue: str = ""                # jito | lido | marinade
    asset: str = "SOL"
    qty:   float = 0.0


@dataclass
class SwapAction(ActionBase):
    """DEX-aggregator swap (D'CENT, Jupiter, 0x)."""

    venue:        str = ""         # dcent_swap | jupiter_swap | zerox_swap
    from_asset:   str = ""         # symbol or contract address
    to_asset:     str = ""
    qty:          float = 0.0
    slippage_bps: int = 50         # 50 = 0.5%


# ---------------------------------------------------------------------------
# Outcome — what execution returns
# ---------------------------------------------------------------------------


@dataclass
class ActionOutcome:
    """Result of executing one action. Persist to swarm `action.outcome`."""

    action_id:     str
    succeeded:     bool
    status:        str = ""                  # PENDING | QUEUED | CONFIRMED | FAILED | CANCELLED
    tx_hash:       Optional[str] = None
    daemon_tx_id:  Optional[str] = None      # WAIaaS daemon's id
    error:         Optional[str] = None
    executed_at_utc: str = field(default_factory=_now_utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def serialize_action(a: ActionBase) -> dict[str, Any]:
    """Flatten any action subclass to a JSON-safe dict (for swarm.write)."""
    d = asdict(a)
    d["__type__"] = a.__class__.__name__
    return d


def serialize_outcome(o: ActionOutcome) -> dict[str, Any]:
    return asdict(o)
