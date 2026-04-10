"""
Sygnif Execution Policies — modeled on NautilusTrader's ExecutionAlgorithm trait.

NT reference implementation:
  - crates/trading/src/algorithm/mod.rs  → ExecutionAlgorithm trait
  - crates/trading/src/algorithm/twap.rs → TwapAlgorithm (concrete impl)
  - crates/trading/src/algorithm/core.rs → ExecutionAlgorithmCore
  - crates/execution/src/trailing.rs     → trailing_stop_calculate()
  - nautilus_trader/execution/config.py  → ExecAlgorithmConfig

NT ExecAlgorithm architecture:
  1. Extends DataActor (not Strategy) — algorithms don't own positions
  2. Receives orders via on_order() with exec_algorithm_id routing
  3. Spawns child orders (spawn_market, spawn_limit) through RiskEngine
  4. Manages remaining qty and fill events for spawned orders
  5. Each has ExecAlgorithmConfig (id, log_events, log_commands)

NT trailing stop (crates/execution/src/trailing.rs):
  - TrailingOffsetType: Price | BasisPoints | Ticks
  - TriggerType: LastPrice | MarkPrice | BidAsk | LastOrBidAsk
  - Continuously recalculates, only updates if new price is "better"

SYGNIF mapping: Named policies that wrap these patterns for Freqtrade.

Usage:
  from execution_policies import PolicyEngine
  engine = PolicyEngine.from_config({"atr_trailing": {"enabled": True}})
  sl = engine.apply_trailing("atr_trailing", atr_pct=2.5, current_profit=0.03)
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class TrailingOffsetType(Enum):
    """NT crates/model/src/enums.rs → TrailingOffsetType."""
    PRICE = "price"
    BASIS_POINTS = "basis_points"
    TICKS = "ticks"


class TriggerType(Enum):
    """NT crates/model/src/enums.rs → TriggerType."""
    LAST_PRICE = "last_price"
    MARK_PRICE = "mark_price"
    BID_ASK = "bid_ask"


@dataclass
class ExecAlgorithmConfig:
    """Mirrors NT ExecAlgorithmConfig (nautilus_trader/execution/config.py).

    NT fields: exec_algorithm_id, log_events, log_commands
    Extended with SYGNIF policy parameters.
    """
    name: str
    enabled: bool = False
    params: dict[str, Any] = field(default_factory=dict)
    allowed_tags: Optional[set[str]] = None
    log_events: bool = True

    def is_active_for(self, tag: str) -> bool:
        if not self.enabled:
            return False
        if self.allowed_tags is None:
            return True
        return tag in self.allowed_tags


# ── Policy Implementations (NT ExecAlgorithm.on_order equivalents) ────────

def trailing_stop_atr(
    atr_pct: float,
    current_profit: float,
    params: dict,
) -> Optional[float]:
    """ATR-based trailing stop — models NT trailing_stop_calculate() with Price offset.

    NT ref: crates/execution/src/trailing.rs
    NT computes: offset_value = trailing_offset (price units)
    We compute: distance = multiplier * ATR%, tightened at profit threshold.

    TrailingOffsetType.PRICE equivalent where offset = ATR * multiplier.
    """
    base_mult = params.get("base_multiplier", 1.5)
    profit_tighten = params.get("profit_tighten_at", 0.05)
    tight_mult = params.get("tight_multiplier", 1.0)
    min_distance = params.get("min_distance", 0.005)

    mult = tight_mult if current_profit >= profit_tighten else base_mult
    distance = max(atr_pct / 100.0 * mult, min_distance)
    return -distance


def trailing_stop_basis_points(
    current_profit: float,
    params: dict,
) -> Optional[float]:
    """Basis-points trailing stop — models NT TrailingOffsetType.BasisPoints.

    NT formula: offset_value = basis * (offset_bps / 10_000)
    Here we use profit tiers with bps offsets.
    """
    tiers = params.get("tiers", [
        {"profit_pct": 0.10, "offset_bps": 150},
        {"profit_pct": 0.05, "offset_bps": 200},
        {"profit_pct": 0.02, "offset_bps": 300},
        {"profit_pct": 0.01, "offset_bps": 100},
    ])
    for tier in tiers:
        if current_profit >= tier["profit_pct"]:
            return -(tier["offset_bps"] / 10_000)
    return None


def vol_adjusted_sizing(
    base_size: float,
    atr_pct: float,
    params: dict,
) -> float:
    """Volatility-adjusted position sizing — NT calculate_fixed_risk_position_size analog.

    NT ref: crates/risk/src/sizing.rs
    NT adjusts by risk_points; we adjust by ATR ratio to target a constant risk amount.
    """
    normal_atr = params.get("normal_atr_pct", 2.0)
    min_factor = params.get("min_factor", 0.5)
    max_factor = params.get("max_factor", 1.5)

    if atr_pct <= 0:
        return base_size

    ratio = normal_atr / atr_pct
    factor = max(min_factor, min(ratio, max_factor))
    return base_size * factor


def vol_adjusted_leverage(
    base_leverage: float,
    atr_pct: float,
    params: dict,
) -> float:
    """Continuous volatility-adjusted leverage capping."""
    normal_atr = params.get("normal_atr_pct", 2.0)
    min_leverage = params.get("min_leverage", 1.0)

    if atr_pct <= 0:
        return base_leverage

    ratio = normal_atr / atr_pct
    adjusted = base_leverage * min(ratio, 1.0)
    return max(adjusted, min_leverage)


def ratchet_trail(
    current_profit: float,
    tiers: list[tuple[float, float]],
    params: dict,
) -> Optional[float]:
    """Profit-tiered ratcheting stoploss (SYGNIF's default exit mechanism).

    Maps to NT trailing stop with profit-tier activation thresholds.
    Each tier: (profit_threshold, trail_distance_price_pct).
    """
    for threshold, distance in tiers:
        if current_profit >= threshold:
            return -distance
    return None


def twap_scale_in(
    current_profit: float,
    num_entries: int,
    params: dict,
) -> Optional[dict]:
    """TWAP-style DCA — modeled on NT TwapAlgorithm (crates/trading/src/algorithm/twap.rs).

    NT TWAP: splits parent order into N equal child orders at time intervals.
    SYGNIF adaptation: splits across drawdown levels instead of time intervals.
    Each "slice" is a new entry when position is at drawdown_step * N.
    """
    max_entries = params.get("max_entries", 3)
    drawdown_step = params.get("drawdown_step", -0.03)
    scale_factor = params.get("scale_factor", 0.5)

    if num_entries >= max_entries:
        return None

    trigger = drawdown_step * num_entries
    if current_profit <= trigger:
        return {
            "action": "dca_entry",
            "child_order_number": num_entries + 1,
            "max_children": max_entries,
            "scale_factor": scale_factor,
            "trigger_profit": trigger,
        }
    return None


# ── Policy Engine ──────────────────────────────────────────────────────────

DEFAULT_POLICIES = {
    "atr_trailing": {
        "enabled": False,
        "params": {
            "base_multiplier": 1.5,
            "tight_multiplier": 1.0,
            "profit_tighten_at": 0.05,
            "min_distance": 0.005,
        },
        "allowed_tags": None,
    },
    "bps_trailing": {
        "enabled": False,
        "params": {
            "tiers": [
                {"profit_pct": 0.10, "offset_bps": 150},
                {"profit_pct": 0.05, "offset_bps": 200},
                {"profit_pct": 0.02, "offset_bps": 300},
                {"profit_pct": 0.01, "offset_bps": 100},
            ],
        },
        "allowed_tags": None,
    },
    "vol_sizing": {
        "enabled": False,
        "params": {
            "normal_atr_pct": 2.0,
            "min_factor": 0.5,
            "max_factor": 1.5,
        },
        "allowed_tags": None,
    },
    "vol_leverage": {
        "enabled": False,
        "params": {
            "normal_atr_pct": 2.0,
            "min_leverage": 1.0,
        },
        "allowed_tags": None,
    },
    "ratchet_trail": {
        "enabled": True,
        "params": {},
        "allowed_tags": None,
    },
    "twap_scale_in": {
        "enabled": False,
        "params": {
            "max_entries": 3,
            "drawdown_step": -0.03,
            "scale_factor": 0.5,
        },
        "allowed_tags": ["claude_s-5", "strong_ta"],
    },
}

_POLICY_FNS = {
    "atr_trailing": trailing_stop_atr,
    "bps_trailing": trailing_stop_basis_points,
    "vol_sizing": vol_adjusted_sizing,
    "vol_leverage": vol_adjusted_leverage,
    "ratchet_trail": ratchet_trail,
    "twap_scale_in": twap_scale_in,
}


class PolicyEngine:
    """Manages named execution policies (NT: Trader registers ExecAlgorithms by ID).

    NT pattern: Trader.add_exec_algorithm(algo) → routes orders by exec_algorithm_id.
    SYGNIF: PolicyEngine.apply_trailing(name, tag, **kwargs) → strategy queries by name.
    """

    def __init__(self, policies: dict[str, ExecAlgorithmConfig]):
        self.policies = policies

    @classmethod
    def from_config(cls, overrides: Optional[dict] = None) -> PolicyEngine:
        raw = dict(DEFAULT_POLICIES)
        if overrides:
            for name, ovr in overrides.items():
                if name in raw:
                    raw[name].update(ovr)
                else:
                    raw[name] = ovr

        policies = {}
        for name, cfg in raw.items():
            policies[name] = ExecAlgorithmConfig(
                name=name,
                enabled=cfg.get("enabled", False),
                params=cfg.get("params", {}),
                allowed_tags=set(cfg["allowed_tags"]) if cfg.get("allowed_tags") else None,
            )
        return cls(policies)

    @classmethod
    def from_json(cls, path: str) -> PolicyEngine:
        with open(path) as f:
            overrides = json.load(f)
        return cls.from_config(overrides)

    def is_active(self, policy_name: str, tag: str = "") -> bool:
        p = self.policies.get(policy_name)
        if not p:
            return False
        return p.is_active_for(tag)

    def apply_trailing(
        self,
        policy_name: str,
        tag: str = "",
        **kwargs,
    ) -> Optional[float]:
        """Apply a trailing stop policy (NT: ExecAlgorithm.on_order equivalent)."""
        p = self.policies.get(policy_name)
        if not p or not p.is_active_for(tag):
            return None
        fn = _POLICY_FNS.get(policy_name)
        if not fn:
            return None
        return fn(**kwargs, params=p.params)

    def apply_sizing(
        self,
        policy_name: str,
        tag: str = "",
        **kwargs,
    ) -> Optional[float]:
        p = self.policies.get(policy_name)
        if not p or not p.is_active_for(tag):
            return None
        fn = _POLICY_FNS.get(policy_name)
        if not fn:
            return None
        return fn(**kwargs, params=p.params)

    def get_all_active(self, tag: str = "") -> list[str]:
        return [name for name, p in self.policies.items() if p.is_active_for(tag)]

    def describe(self) -> dict[str, dict]:
        result = {}
        for name, p in self.policies.items():
            result[name] = {
                "enabled": p.enabled,
                "params": p.params,
                "allowed_tags": list(p.allowed_tags) if p.allowed_tags else "all",
                "log_events": p.log_events,
            }
        return result

    def to_json(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.describe(), f, indent=2)
