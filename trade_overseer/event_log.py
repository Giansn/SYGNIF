"""
Sygnif Event Log — NautilusTrader-aligned event stream.

Models SYGNIF events after NT's actual event architecture:
  - crates/model/src/events/order/any.rs  → OrderEventAny (16 variants)
  - crates/model/src/events/position/mod.rs → PositionEvent (4 variants)

Each event mirrors NT's event fields:
  event_id (UUID4), ts_event (ns), ts_init (ns), trader_id, strategy_id,
  instrument_id, client_order_id — mapped to Freqtrade equivalents.

NT event types mapped to SYGNIF:

  ORDER LIFECYCLE (from NT OrderEventAny):
    order_initialized   – Strategy signals intent (populate_entry_trend)
    order_submitted     – Order sent to exchange (confirm_trade_entry passed)
    order_accepted      – Exchange acknowledged
    order_denied        – RiskEngine or slot cap rejected
    order_filled        – Fill confirmed (entry or exit)
    order_canceled      – Order canceled (cooldown, force)
    order_expired       – GTD/timeout expiry
    order_updated       – SL/TP modification (ratcheting trail)

  POSITION LIFECYCLE (from NT PositionEvent):
    position_opened     – First fill on new position
    position_changed    – Size/PnL change (partial fill, unrealized update)
    position_closed     – Position fully closed

  SYGNIF-SPECIFIC (no NT equivalent, custom Actor events):
    signal_generated    – TA score / failure swing detected
    overseer_action     – Overseer HOLD/TRAIL/CUT recommendation
    risk_check          – RiskEngine pre-trade check result
    system_event        – Container start/stop/error

Usage:
  from event_log import EventLog

  log = EventLog(instance="freqtrade-futures")
  log.emit("order_filled", instrument_id="BTC/USDT:USDT",
           data={"side": "buy", "last_px": 65000, "last_qty": 0.01,
                 "strategy_id": "SygnifStrategy", "trade_id": 42})
"""
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_LOG_DIR = os.path.join(_BASE_DIR, "data")

# NT OrderEventAny: 16 variants (crates/model/src/events/order/any.rs)
ORDER_EVENT_TYPES = frozenset({
    "order_initialized",
    "order_denied",
    "order_submitted",
    "order_accepted",
    "order_rejected",
    "order_canceled",
    "order_expired",
    "order_triggered",
    "order_pending_update",
    "order_pending_cancel",
    "order_modify_rejected",
    "order_cancel_rejected",
    "order_updated",
    "order_filled",
})

# NT PositionEvent: 4 variants (crates/model/src/events/position/mod.rs)
POSITION_EVENT_TYPES = frozenset({
    "position_opened",
    "position_changed",
    "position_closed",
    "position_adjusted",
})

# SYGNIF-specific events (custom Actor signals, no NT equivalent)
SYGNIF_EVENT_TYPES = frozenset({
    "signal_generated",
    "overseer_action",
    "risk_check",
    "system_event",
})

EVENT_TYPES = ORDER_EVENT_TYPES | POSITION_EVENT_TYPES | SYGNIF_EVENT_TYPES


def _ts_ns() -> int:
    """Current timestamp in nanoseconds (NT uses UnixNanos throughout)."""
    return int(time.time_ns())


def _uuid4_hex() -> str:
    """NT uses UUID4 for all event IDs."""
    return uuid.uuid4().hex


class EventLog:
    """Append-only JSONL event log mirroring NT's event sourcing pattern.

    NT writes events through MessageBus pub/sub with nanosecond timestamps
    and UUID4 identifiers. We replicate the schema in JSONL for Freqtrade.

    NT ref: crates/execution/src/engine/mod.rs (ExecutionEngine.process)
    """

    def __init__(
        self,
        log_dir: Optional[str] = None,
        filename: str = "events.jsonl",
        max_size_mb: float = 50.0,
        instance: str = "unknown",
        trader_id: str = "SYGNIF-001",
    ):
        self.log_dir = log_dir or _DEFAULT_LOG_DIR
        self.filename = filename
        self.max_size_bytes = int(max_size_mb * 1024 * 1024)
        self.instance = instance
        self.trader_id = trader_id
        self._path = os.path.join(self.log_dir, self.filename)
        os.makedirs(self.log_dir, exist_ok=True)

    @property
    def path(self) -> str:
        return self._path

    def emit(
        self,
        event_type: str,
        instrument_id: str = "",
        strategy_id: str = "SygnifStrategy",
        client_order_id: Optional[str] = None,
        trade_id: Optional[int] = None,
        data: Optional[dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> dict:
        """Append one event to the log. Returns the event dict.

        Field mapping to NT:
          event_id     → NT OrderEvent.event_id (UUID4)
          ts_event     → NT OrderEvent.ts_event (nanoseconds)
          ts_init      → NT OrderEvent.ts_init (nanoseconds)
          trader_id    → NT OrderEvent.trader_id
          strategy_id  → NT OrderEvent.strategy_id
          instrument_id → NT OrderEvent.instrument_id
          type         → NT OrderEventAny variant name
        """
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown event type: {event_type}. Valid: {sorted(EVENT_TYPES)}")

        ts_ns = _ts_ns()
        event = {
            "event_id": _uuid4_hex(),
            "type": event_type,
            "ts_event": ts_ns,
            "ts_init": ts_ns,
            "ts_iso": datetime.now(timezone.utc).isoformat(),
            "trader_id": self.trader_id,
            "strategy_id": strategy_id,
            "instrument_id": instrument_id,
            "instance": self.instance,
            "client_order_id": client_order_id,
            "trade_id": trade_id,
            "correlation_id": correlation_id,
            "data": data or {},
        }

        self._rotate_if_needed()
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, separators=(",", ":"), default=str) + "\n")

        return event

    def emit_order_filled(
        self,
        instrument_id: str,
        side: str,
        last_px: float,
        last_qty: float,
        trade_id: Optional[int] = None,
        commission: float = 0.0,
        strategy_id: str = "SygnifStrategy",
        **extra,
    ) -> dict:
        """Convenience: emit NT-style OrderFilled event.

        NT ref: crates/model/src/events/order/filled.rs
        Fields: order_side, last_qty, last_px, currency, liquidity_side, commission
        """
        return self.emit(
            "order_filled",
            instrument_id=instrument_id,
            strategy_id=strategy_id,
            trade_id=trade_id,
            data={
                "order_side": side,
                "last_px": last_px,
                "last_qty": last_qty,
                "commission": commission,
                **extra,
            },
        )

    def emit_position_closed(
        self,
        instrument_id: str,
        entry_side: str,
        avg_px_open: float,
        avg_px_close: float,
        realized_pnl: float,
        duration_ns: int = 0,
        trade_id: Optional[int] = None,
        exit_reason: str = "",
        strategy_id: str = "SygnifStrategy",
        **extra,
    ) -> dict:
        """Convenience: emit NT-style PositionClosed event.

        NT ref: crates/model/src/events/position/closed.rs
        Fields: entry, avg_px_open, avg_px_close, realized_pnl, duration
        """
        return self.emit(
            "position_closed",
            instrument_id=instrument_id,
            strategy_id=strategy_id,
            trade_id=trade_id,
            data={
                "entry": entry_side,
                "avg_px_open": avg_px_open,
                "avg_px_close": avg_px_close,
                "realized_pnl": realized_pnl,
                "duration_ns": duration_ns,
                "exit_reason": exit_reason,
                **extra,
            },
        )

    def emit_risk_check(
        self,
        instrument_id: str,
        allowed: bool,
        reason: str = "",
        strategy_id: str = "SygnifStrategy",
        **extra,
    ) -> dict:
        """Convenience: emit RiskEngine pre-trade check result.

        NT ref: crates/risk/src/engine/mod.rs (deny_order / throttler)
        """
        return self.emit(
            "risk_check",
            instrument_id=instrument_id,
            strategy_id=strategy_id,
            data={"allowed": allowed, "reason": reason, **extra},
        )

    def _rotate_if_needed(self):
        if not os.path.exists(self._path):
            return
        if os.path.getsize(self._path) < self.max_size_bytes:
            return
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        rotated = self._path.replace(".jsonl", f".{ts}.jsonl")
        os.rename(self._path, rotated)

    def read(
        self,
        event_type: Optional[str] = None,
        instrument_id: Optional[str] = None,
        since_ns: Optional[int] = None,
        strategy_id: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Read events with optional filters (NT Cache.events_* pattern)."""
        if not os.path.exists(self._path):
            return []
        results = []
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event_type and event.get("type") != event_type:
                    continue
                if instrument_id and event.get("instrument_id") != instrument_id:
                    continue
                if since_ns and event.get("ts_event", 0) < since_ns:
                    continue
                if strategy_id and event.get("strategy_id") != strategy_id:
                    continue
                results.append(event)
                if len(results) >= limit:
                    break
        return results

    def tail(self, n: int = 50) -> list[dict]:
        if not os.path.exists(self._path):
            return []
        events = []
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return events[-n:]

    def correlate(self, correlation_id: str) -> list[dict]:
        """Get all events in a signal → order → fill chain (NT message bus correlation)."""
        if not os.path.exists(self._path):
            return []
        results = []
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    event = json.loads(line.strip())
                    if event.get("correlation_id") == correlation_id:
                        results.append(event)
                except (json.JSONDecodeError, ValueError):
                    continue
        return results

    def count_by_type(self, since_ns: Optional[int] = None) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in self.read(since_ns=since_ns, limit=100000):
            t = event.get("type", "unknown")
            counts[t] = counts.get(t, 0) + 1
        return counts

    def position_lifecycle(self, trade_id: int) -> list[dict]:
        """Reconstruct NT-style position lifecycle for a trade.

        Returns all events for a trade_id, ordered by ts_event,
        mirroring NT's PositionOpened → PositionChanged → PositionClosed chain.
        """
        events = self.read(limit=100000)
        related = [e for e in events if e.get("trade_id") == trade_id]
        return sorted(related, key=lambda e: e.get("ts_event", 0))


_default_log: Optional[EventLog] = None


def get_event_log(instance: str = "overseer") -> EventLog:
    global _default_log
    if _default_log is None:
        _default_log = EventLog(instance=instance)
    return _default_log
