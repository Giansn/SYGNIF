"""
Sygnif Risk Manager — modeled on NautilusTrader's RiskEngine.

Used from Freqtrade strategies (e.g. ``SygnifStrategy``). **Nautilus** live nodes use the
library ``LiveRiskEngine`` in ``research/nautilus_lab/run_sygnif_btc_trading_node.py``, not this module.

NT reference implementation:
  - crates/risk/src/engine/mod.rs       → RiskEngine struct + pre-trade checks
  - crates/risk/src/engine/config.rs    → RiskEngineConfig
  - crates/risk/src/sizing.rs           → calculate_fixed_risk_position_size()
  - nautilus_trader/risk/config.py      → RiskEngineConfig (Python)

NT RiskEngine responsibilities (mapped to SYGNIF):
  1. Pre-trade validation        → check_entry_allowed()
  2. Order rate throttling       → throttle_check() (max_order_submit_rate)
  3. Max notional per order      → check_max_notional()
  4. Trading state management    → TradingState (Active/Halted/Reducing)
  5. Position sizing             → get_position_size() (from sizing.rs)
  6. Trailing stop calculation   → NT delegates to crates/execution/src/trailing.rs

NT architecture: Strategy → RiskEngine → ExecutionEngine → ExecClient
SYGNIF mapping: SygnifStrategy → RiskManager → Freqtrade engine → Bybit

Usage:
  from trade_overseer.risk_manager import RiskManager, TradingState
  rm = RiskManager(config)
  rm.check_entry_allowed(pair, tag, open_trades, leverage)
  rm.get_leverage(pair, side, atr_pct)
  rm.get_stoploss(tag, current_profit, leverage, is_futures, sf_sl)
  rm.calculate_position_size(equity, entry, stop_loss, risk_pct)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class TradingState(Enum):
    """NT crates/model/src/enums.rs → TradingState.

    Active   = normal trading
    Halted   = no new orders (circuit breaker)
    Reducing = only reduce-only orders (wind down)
    """
    ACTIVE = "active"
    HALTED = "halted"
    REDUCING = "reducing"


@dataclass
class RiskEngineConfig:
    """Mirrors NT RiskEngineConfig (nautilus_trader/risk/config.py).

    NT fields: bypass, max_order_submit_rate, max_order_modify_rate,
    max_notional_per_order, debug
    """
    bypass: bool = False
    max_order_submit_rate: int = 100
    max_order_submit_interval_secs: float = 1.0
    max_notional_per_order: dict[str, float] = field(default_factory=dict)

    # SYGNIF-specific risk limits
    doom_sl_spot: float = 0.20
    doom_sl_futures: float = 0.20
    soft_sl_ratio_spot: float = 0.60
    soft_sl_ratio_futures: float = 0.60

    leverage_default: float = 3.0
    leverage_majors: float = 5.0
    leverage_short_cap: float = 2.0
    leverage_atr_high_cap: float = 2.0
    leverage_atr_medium_cap: float = 3.0

    major_pairs: tuple = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT")

    max_slots_strong: int = 6
    max_slots_swing: int = 4
    premium_nonreserved_max: int = 10

    premium_tags: frozenset = frozenset(
        {"fa_s-5", "fa_swing_short", "sygnif_s-5", "sygnif_swing_short", "claude_s-5", "claude_swing_short"}
    )
    swing_tags: frozenset = frozenset({
        "swing_failure",
        "fa_swing",
        "sygnif_swing",
        "claude_swing",
        "swing_failure_short",
        "fa_swing_short",
        "sygnif_swing_short",
        "claude_swing_short",
    })

    doom_cooldown_secs: int = 14400
    consecutive_loss_window: int = 86400
    consecutive_loss_limit: int = 2
    futures_min_volume: float = 25000

    # NT-style ratcheting trail (price-based, on-exchange)
    # +1% and +2% tiers removed: they clipped winners before indicator exits could fire
    ratchet_tiers: tuple = (
        (0.10, 0.015),
        (0.05, 0.02),
    )

    swing_ratchet_tiers: tuple = (
        (0.05, 0.02),
        (0.02, 0.03),
    )


@dataclass
class EntryDecision:
    """Result of pre-trade risk check (NT: deny_order → OrderDenied event)."""
    allowed: bool
    reason: str = ""
    denied_by: str = ""


class Throttler:
    """Simple rate limiter modeled on NT's Throttler (crates/common/src/throttler.rs).

    NT throttler: limit N commands per interval, with success/failure handlers.
    Failure handler generates OrderDenied("REJECTED BY THROTTLER").
    """

    def __init__(self, limit: int, interval_secs: float):
        self.limit = limit
        self.interval_secs = interval_secs
        self._timestamps: list[float] = []

    def check(self) -> bool:
        now = time.time()
        cutoff = now - self.interval_secs
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        if len(self._timestamps) >= self.limit:
            return False
        self._timestamps.append(now)
        return True


class RiskManager:
    """Pre-trade risk engine modeled on NT RiskEngine.

    NT flow: SubmitOrder → RiskEngine.execute() → validate → throttle → forward
    SYGNIF flow: entry signal → RiskManager.check_entry_allowed() → confirm_trade_entry
    """

    def __init__(self, config: Optional[RiskEngineConfig] = None):
        self.config = config or RiskEngineConfig()
        self.trading_state = TradingState.ACTIVE
        self._submit_throttler = Throttler(
            self.config.max_order_submit_rate,
            self.config.max_order_submit_interval_secs,
        )
        self._doom_cooldown: dict[str, float] = {}
        self._doom_loss_count: dict[str, list[float]] = {}

    def set_trading_state(self, state: TradingState):
        """NT: RiskEngine.set_trading_state() — halt/resume trading globally."""
        prev = self.trading_state
        self.trading_state = state
        logger.info("TradingState: %s → %s", prev.value, state.value)

    def check_entry_allowed(
        self,
        pair: str,
        tag: str,
        open_trades: list,
        volume_avg: float = 0.0,
        is_futures: bool = False,
        notional: float = 0.0,
    ) -> EntryDecision:
        """Full pre-trade risk check (NT RiskEngine.execute → SubmitOrder path).

        Check order: bypass → state → throttle → notional → cooldown → slots → volume
        """
        cfg = self.config

        if cfg.bypass:
            return EntryDecision(True)

        # Trading state gate (NT: TradingState::Halted → deny all)
        if self.trading_state == TradingState.HALTED:
            return EntryDecision(False, "trading_halted", "TradingState")
        if self.trading_state == TradingState.REDUCING:
            return EntryDecision(False, "reducing_only", "TradingState")

        # Throttle check (NT: Throttler → "REJECTED BY THROTTLER")
        if not self._submit_throttler.check():
            return EntryDecision(False, "order_rate_exceeded", "Throttler")

        # Max notional per order (NT: max_notional_per_order config)
        if notional > 0 and pair in cfg.max_notional_per_order:
            limit = cfg.max_notional_per_order[pair]
            if notional > limit:
                return EntryDecision(
                    False,
                    f"max_notional_exceeded ({notional:.2f} > {limit:.2f})",
                    "MaxNotional",
                )

        now = time.time()

        # Doom cooldown
        cooldown_since = self._doom_cooldown.get(pair, 0)
        if now - cooldown_since < cfg.doom_cooldown_secs:
            remaining = cfg.doom_cooldown_secs - (now - cooldown_since)
            return EntryDecision(False, f"doom_cooldown ({pair}, {remaining:.0f}s left)", "Cooldown")

        # Consecutive loss lockout
        losses = self._doom_loss_count.get(pair, [])
        recent = [t for t in losses if now - t < cfg.consecutive_loss_window]
        if len(recent) >= cfg.consecutive_loss_limit:
            return EntryDecision(
                False,
                f"consecutive_loss_lockout ({pair}, {len(recent)} in 24h)",
                "ConsecutiveLoss",
            )

        # Slot caps
        if tag == "strong_ta" or tag == "strong_ta_short":
            count = sum(1 for t in open_trades if getattr(t, "enter_tag", "") == tag)
            if count >= cfg.max_slots_strong:
                return EntryDecision(False, f"strong_ta_cap ({count}/{cfg.max_slots_strong})", "SlotCap")

        if tag in cfg.swing_tags:
            count = sum(1 for t in open_trades if getattr(t, "enter_tag", "") in cfg.swing_tags)
            if count >= cfg.max_slots_swing:
                return EntryDecision(False, f"swing_cap ({count}/{cfg.max_slots_swing})", "SlotCap")

        if tag not in cfg.premium_tags:
            if len(open_trades) >= cfg.premium_nonreserved_max:
                return EntryDecision(
                    False,
                    f"premium_reserve ({len(open_trades)}/{cfg.premium_nonreserved_max})",
                    "PremiumReserve",
                )

        # Futures volume gate
        if is_futures and tag not in cfg.swing_tags:
            if volume_avg < cfg.futures_min_volume:
                return EntryDecision(
                    False,
                    f"futures_volume_gate ({volume_avg:.0f} < {cfg.futures_min_volume})",
                    "VolumeGate",
                )

        return EntryDecision(True)

    def get_leverage(self, pair: str, side: str, atr_pct: float = 0.0) -> float:
        """Determine leverage with volatility caps (continuous, not step function)."""
        cfg = self.config
        base_pair = pair.split(":")[0] if ":" in pair else pair

        lev = cfg.leverage_majors if base_pair in cfg.major_pairs else cfg.leverage_default

        if side == "short":
            lev = min(lev, cfg.leverage_short_cap)

        if atr_pct > 3.0:
            lev = min(lev, cfg.leverage_atr_high_cap)
        elif atr_pct > 2.0:
            lev = min(lev, cfg.leverage_atr_medium_cap)

        return lev

    def get_stoploss(
        self,
        tag: str,
        current_profit: float,
        leverage: float,
        is_futures: bool,
        sf_sl: float = 0.03,
    ) -> float:
        """Dynamic stoploss (negative price distance).

        Ratcheting trail follows NT trailing_stop_calculate() pattern
        (crates/execution/src/trailing.rs) but with profit-tiered thresholds.
        """
        cfg = self.config

        if tag in cfg.swing_tags:
            for threshold, trail in cfg.swing_ratchet_tiers:
                if current_profit >= threshold:
                    return -trail
            return -sf_sl

        for threshold, trail in cfg.ratchet_tiers:
            if current_profit >= threshold:
                return -trail

        sl = cfg.doom_sl_futures if is_futures else cfg.doom_sl_spot
        if is_futures:
            return -(sl / leverage)
        return -sl

    def get_soft_sl(self, is_futures: bool) -> float:
        cfg = self.config
        if is_futures:
            return -(cfg.doom_sl_futures * cfg.soft_sl_ratio_futures)
        return -(cfg.doom_sl_spot * cfg.soft_sl_ratio_spot)

    def calculate_position_size(
        self,
        equity: float,
        entry: float,
        stop_loss: float,
        risk_pct: float = 0.01,
        commission_rate: float = 0.00055,
        exchange_rate: float = 1.0,
    ) -> float:
        """Fixed-risk position sizing modeled on NT's calculate_fixed_risk_position_size.

        NT ref: crates/risk/src/sizing.rs
        Formula: size = risk_money / (risk_points * exchange_rate)
        where risk_money = equity * risk_pct - round_trip_commission
        """
        if exchange_rate == 0 or equity <= 0:
            return 0.0

        risk_points = abs(entry - stop_loss)
        if risk_points <= 0:
            return 0.0

        risk_money = equity * risk_pct
        commission = risk_money * commission_rate * 2  # round-turn
        riskable = risk_money - commission

        position_size = (riskable / exchange_rate) / risk_points
        return max(position_size, 0.0)

    def get_stake_amount(
        self,
        tag: str,
        wallet_balance: float,
        max_open_trades: int,
        tradable_ratio: float = 0.80,
    ) -> float:
        """Simple equal-weight sizing (current SYGNIF default)."""
        tradable = wallet_balance * tradable_ratio
        return tradable / max(max_open_trades, 1)

    def record_loss_exit(self, pair: str):
        """Record doom exit for cooldown/lockout tracking."""
        now = time.time()
        self._doom_cooldown[pair] = now
        losses = self._doom_loss_count.get(pair, [])
        losses = [t for t in losses if now - t < self.config.consecutive_loss_window] + [now]
        self._doom_loss_count[pair] = losses

    def export_state(self) -> dict:
        return {
            "trading_state": self.trading_state.value,
            "doom_cooldown": dict(self._doom_cooldown),
            "doom_loss_count": {k: list(v) for k, v in self._doom_loss_count.items()},
        }

    def import_state(self, state: dict):
        ts = state.get("trading_state", "active")
        self.trading_state = TradingState(ts)
        self._doom_cooldown = state.get("doom_cooldown", {})
        self._doom_loss_count = {
            k: list(v) for k, v in state.get("doom_loss_count", {}).items()
        }
