"""sygnif PositionExecutor — Hummingbot-style state machine, one per position.

Each open position owns a small state machine that ticks every cycle. On
each tick the executor checks the TripleBarrierConfig (take-profit /
stop-loss / trailing-stop / time-limit) against current price and time,
and emits a ClosePositionAction when an exit barrier is hit.

This replaces ad-hoc exit logic in `trader.py` with deterministic,
trace-able state-machine output. Every closed position can answer
"why was I closed?" with a single field — perfect for the friend-facing
audit at end of May.

Lifecycle:

    NOT_STARTED  ──▶ ACTIVE  ──▶ CLOSING  ──▶ CLOSED
                       │            │            ▲
                       └─tick fires─┘            │
                            (exit barrier)       │
                                                 │
                       (executor confirms close)─┘

Stdlib only.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

# Ensure agent/ is on the path (so agent.actions imports cleanly when
# this module is loaded by sygnif_neurons.py from the repo root).
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.actions import ClosePositionAction, TripleBarrierConfig


class PositionState(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    ACTIVE      = "ACTIVE"
    CLOSING     = "CLOSING"
    CLOSED      = "CLOSED"


class CloseReason(str, Enum):
    TAKE_PROFIT   = "take_profit"
    STOP_LOSS     = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    TIME_LIMIT    = "time_limit"
    SIGNAL_FLIP   = "signal_flip"
    MANUAL        = "manual"
    EARLY_STOP    = "early_stop"


def _now_utc() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _parse_iso(ts: str) -> datetime:
    """Lenient ISO8601 parser — handles trailing Z and offsets."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


@dataclass
class PositionExecutor:
    """Manages exactly one position from open through close.

    Construct with the entry details + barrier; call .tick(price, ts) every
    cycle. Returns a ClosePositionAction when an exit fires (or None).

    Once closed, the executor's `state == CLOSED` and `close_reason` is set.
    Garbage-collect closed executors from any registry holding them.
    """

    position_id: str
    venue:       str
    symbol:      str
    side:        str                          # "long" | "short"
    entry_price: float
    qty:         float
    barrier:     TripleBarrierConfig
    controller_id: str = "main"

    state:           PositionState = PositionState.NOT_STARTED
    opened_at_utc:   str = field(default_factory=_now_utc)
    closed_at_utc:   Optional[str] = None
    close_reason:    Optional[CloseReason] = None
    realized_pnl_pct: Optional[float] = None
    high_water_mark: Optional[float] = None   # tracks max favourable price for trailing stop
    last_tick_price: Optional[float] = None
    last_tick_utc:   Optional[str] = None

    # ----- lifecycle -----

    def start(self, ts: Optional[str] = None) -> None:
        """Mark the position as active. Call after the open order confirms."""
        self.state = PositionState.ACTIVE
        if ts:
            self.opened_at_utc = ts
        if self.side.lower() == "long":
            self.high_water_mark = self.entry_price
        else:
            self.high_water_mark = self.entry_price

    def tick(self, current_price: float, current_time_utc: Optional[str] = None) -> Optional[ClosePositionAction]:
        """Run one cycle of barrier checks. Returns a close action if any
        exit barrier fires; otherwise None.

        Order of precedence: stop_loss > trailing_stop > take_profit > time_limit.
        Stop-loss takes precedence so a fast adverse move can't be eaten by a
        previously-set TP.
        """
        if self.state != PositionState.ACTIVE:
            return None

        ts = current_time_utc or _now_utc()
        self.last_tick_price = current_price
        self.last_tick_utc = ts

        # update high-water-mark for trailing
        if self.barrier.trailing_stop_pct is not None:
            if self.side.lower() == "long":
                if self.high_water_mark is None or current_price > self.high_water_mark:
                    self.high_water_mark = current_price
            else:  # short
                if self.high_water_mark is None or current_price < self.high_water_mark:
                    self.high_water_mark = current_price

        pnl_pct = self.pnl_pct(current_price)

        # 1. stop loss (priority)
        if self.barrier.stop_loss_pct is not None and pnl_pct <= -abs(self.barrier.stop_loss_pct):
            return self._fire_close(CloseReason.STOP_LOSS, current_price, ts)

        # 2. trailing stop
        if (self.barrier.trailing_stop_pct is not None
                and self.high_water_mark is not None):
            if self.side.lower() == "long":
                drawdown = (self.high_water_mark - current_price) / self.high_water_mark
            else:
                drawdown = (current_price - self.high_water_mark) / self.high_water_mark
            if drawdown >= self.barrier.trailing_stop_pct:
                return self._fire_close(CloseReason.TRAILING_STOP, current_price, ts)

        # 3. take profit
        if self.barrier.take_profit_pct is not None and pnl_pct >= self.barrier.take_profit_pct:
            return self._fire_close(CloseReason.TAKE_PROFIT, current_price, ts)

        # 4. time limit
        if self.barrier.time_limit_seconds is not None:
            try:
                t_open = _parse_iso(self.opened_at_utc)
                t_now = _parse_iso(ts)
                if (t_now - t_open).total_seconds() >= self.barrier.time_limit_seconds:
                    return self._fire_close(CloseReason.TIME_LIMIT, current_price, ts)
            except Exception:
                pass  # bad ts format → no time check this tick

        return None

    def force_close(self, reason: CloseReason = CloseReason.MANUAL,
                     current_price: Optional[float] = None,
                     ts: Optional[str] = None) -> ClosePositionAction:
        """External trigger (signal flip, kill switch, manual). Returns the
        Close action for the caller to execute."""
        return self._fire_close(reason, current_price or self.last_tick_price or self.entry_price, ts or _now_utc())

    def confirm_closed(self, exit_price: float, ts: Optional[str] = None) -> None:
        """Caller invokes this after the close order confirms on-venue.
        Locks in realized_pnl and moves to CLOSED."""
        self.state = PositionState.CLOSED
        self.closed_at_utc = ts or _now_utc()
        self.realized_pnl_pct = self.pnl_pct(exit_price)

    # ----- helpers -----

    def pnl_pct(self, price: float) -> float:
        """Unrealized P&L percentage at the given price."""
        if self.entry_price == 0:
            return 0.0
        if self.side.lower() == "long":
            return (price - self.entry_price) / self.entry_price
        return (self.entry_price - price) / self.entry_price

    def status_line(self) -> str:
        """One-line human summary, useful for status reports."""
        pnl = self.pnl_pct(self.last_tick_price or self.entry_price)
        return (f"{self.position_id[:8]}  {self.venue}/{self.symbol} "
                f"{self.side} qty={self.qty} entry={self.entry_price:.4f} "
                f"now={self.last_tick_price or '?'}  pnl={pnl:+.2%}  state={self.state.value}")

    def _fire_close(self, reason: CloseReason, price: float, ts: str) -> ClosePositionAction:
        self.state = PositionState.CLOSING
        return ClosePositionAction(
            controller_id=self.controller_id,
            decided_at_utc=ts,
            position_id=self.position_id,
            venue=self.venue,
            symbol=self.symbol,
            why=reason.value,
            reason=f"{reason.value}: pnl={self.pnl_pct(price):+.2%} entry={self.entry_price} now={price}",
        )


# ---------------------------------------------------------------------------
# Self-test (run with: python -m agent.executors.position_executor)
# ---------------------------------------------------------------------------


def _selftest() -> int:
    failed = 0
    print("=== PositionExecutor self-test ===")

    # Case 1: long with TP fires correctly
    pe = PositionExecutor("p1", "bybit_paper", "BTCUSDT", "long", entry_price=80000, qty=0.001,
                            barrier=TripleBarrierConfig(take_profit_pct=0.02, stop_loss_pct=0.01))
    pe.start("2026-04-26T00:00:00+00:00")
    a = pe.tick(80500, "2026-04-26T00:01:00+00:00")
    assert a is None, f"shouldn't fire at +0.625%; got {a}"
    a = pe.tick(81700, "2026-04-26T00:02:00+00:00")
    assert a is not None and "take_profit" in a.why, f"TP should fire at +2.125%; got {a}"
    print("  ✓ long TP")

    # Case 2: short with SL fires
    pe = PositionExecutor("p2", "drift", "SOL-PERP", "short", entry_price=100, qty=0.05,
                            barrier=TripleBarrierConfig(stop_loss_pct=0.02))
    pe.start()
    a = pe.tick(102.5)
    assert a is not None and "stop_loss" in a.why, f"SL should fire at -2.5%; got {a}"
    print("  ✓ short SL")

    # Case 3: trailing stop on long
    pe = PositionExecutor("p3", "bybit_paper", "BTCUSDT", "long", entry_price=80000, qty=0.001,
                            barrier=TripleBarrierConfig(trailing_stop_pct=0.01))
    pe.start()
    pe.tick(82000)        # HWM = 82000
    pe.tick(83000)        # HWM = 83000
    a = pe.tick(82200)    # drawdown from 83k = 0.96%, no fire
    assert a is None
    a = pe.tick(82100)    # drawdown = 1.08%, FIRE
    assert a is not None and "trailing" in a.why, f"trailing should fire from 83k→82.1k (-1.08%); got {a}"
    print("  ✓ trailing stop")

    # Case 4: time limit
    pe = PositionExecutor("p4", "bybit_paper", "ETHUSDT", "long", entry_price=3000, qty=0.01,
                            barrier=TripleBarrierConfig(time_limit_seconds=60))
    pe.start("2026-04-26T00:00:00+00:00")
    a = pe.tick(3010, "2026-04-26T00:00:30+00:00")
    assert a is None, "shouldn't fire at 30s"
    a = pe.tick(3010, "2026-04-26T00:01:30+00:00")
    assert a is not None and "time_limit" in a.why, f"time should fire at 90s past 60s limit; got {a}"
    print("  ✓ time limit")

    # Case 5: SL takes precedence over TP when both could fire same tick
    pe = PositionExecutor("p5", "bybit_paper", "BTCUSDT", "long", entry_price=80000, qty=0.001,
                            barrier=TripleBarrierConfig(take_profit_pct=0.05, stop_loss_pct=0.01))
    pe.start()
    a = pe.tick(78000)    # both could trigger: SL=-2.5% YES, TP=-2.5% NO
    assert a is not None and "stop_loss" in a.why, f"SL precedence; got {a}"
    print("  ✓ SL precedence")

    print(f"\n{'PASS' if failed == 0 else 'FAIL'}: {failed} failure(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_selftest())
