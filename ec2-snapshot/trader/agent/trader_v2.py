"""sygnif trader_v2 — three-method controller lifecycle (Hummingbot V2 pattern).

Demonstrates the clean separation of:
    update_processed_data()    →  pure: fetch market + portfolio data, compute features
    determine_actions()        →  pure: decide what to do, return list of typed actions
    render_status()            →  pure: human-readable summary for logs/Telegram

The runtime owns side effects: take the action list, call the executor, persist
both proposals and outcomes to swarm.db. This makes:
  - dry-run trivial   (run determine_actions, log without executing)
  - replay trivial    (replay processed_data → identical actions)
  - audit trail clean (each closed trade has the action that opened it +
                       the action that closed it + the executor's state-machine trace)

This file is INTENT-DEMO. It does not yet replace `agent/loop.py`. The existing
trader keeps running; you migrate one cycle at a time when ready.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.actions import (
    ActionBase, ActionOutcome, ClosePositionAction, CreateOrderAction,
    TripleBarrierConfig, serialize_action, serialize_outcome,
)
from agent.executors.position_executor import PositionExecutor


@dataclass
class ProcessedData:
    """Pure snapshot of market + portfolio + signal state. Captured once per
    cycle. determine_actions() reads ONLY this — never the network — so the
    function is replayable from any historical snapshot."""

    ts_utc: str
    regime:           str = "UNKNOWN"
    btc_price:        Optional[float] = None
    btc_funding_bps:  Optional[float] = None
    iv_pct:           Optional[float] = None
    open_positions:   list[dict] = field(default_factory=list)   # from order.paper.portfolio
    equity_usdc:      float = 0.0
    sizing:           dict = field(default_factory=dict)          # output of sizing_tuner
    risk:             dict = field(default_factory=dict)          # output of risk.exposure.scan
    signal:           Optional[dict] = None                       # output of discovery.predict


class TraderControllerV2:
    """Skeleton controller in the V2 pattern. One instance per strategy.

    Subclass and override `_decide()` to implement specific signals.
    The orchestration (data → decide → render) stays the same.
    """

    controller_id: str = "trader_v2"

    def __init__(self) -> None:
        self.processed: Optional[ProcessedData] = None
        self.executors: dict[str, PositionExecutor] = {}    # active position state machines

    # -- 1. data --

    def update_processed_data(self) -> ProcessedData:
        """Pure: gather everything needed for this cycle. NO side effects."""
        import sygnif_neurons as N
        import datetime as dt

        # Market regime + BTC focus
        disc = N.run("discovery.read", {})
        disc_data = disc.get("data", {}) if disc.get("ok") else {}
        regime = disc_data.get("regime", "UNKNOWN")

        # Live BTC tape (cheap)
        tape = N.run("btc.ticker", {"symbol": "BTCUSDT"})
        tape_data = tape.get("data", {}) if tape.get("ok") else {}

        # Paper portfolio
        port = N.run("order.paper.portfolio", {})
        port_data = port.get("data", {}) if port.get("ok") else {}

        # Sizing tuner (deterministic)
        sizing_n = N.run("expertise.tune", {})
        sizing = sizing_n.get("data", {}) if sizing_n.get("ok") else {}

        # Risk exposure
        risk_n = N.run("risk.exposure.scan", {})
        risk = risk_n.get("data", {}) if risk_n.get("ok") else {}

        # Signal (rule-based predict)
        signal = None  # extend with discovery.predict integration when needed

        return ProcessedData(
            ts_utc=dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            regime=regime,
            btc_price=tape_data.get("last_price"),
            btc_funding_bps=disc_data.get("funding_bps"),
            iv_pct=disc_data.get("iv_pct"),
            open_positions=port_data.get("open", []),
            equity_usdc=port_data.get("equity_usdc", 0.0),
            sizing=sizing,
            risk=risk,
            signal=signal,
        )

    # -- 2. decide --

    def determine_actions(self, p: ProcessedData) -> list[ActionBase]:
        """Pure: given the snapshot, decide what to do. Returns typed actions."""
        actions: list[ActionBase] = []

        # exit logic: tick every active position-executor against current price
        if p.btc_price:
            for pid, pe in list(self.executors.items()):
                a = pe.tick(p.btc_price, p.ts_utc)
                if a is not None:
                    actions.append(a)

        # entry logic: subclass extension point
        actions.extend(self._decide(p))

        # risk gate: filter actions that would breach concentration
        actions = [a for a in actions if self._risk_ok(a, p)]

        return actions

    def _decide(self, p: ProcessedData) -> list[ActionBase]:
        """Override in subclass. Default = no entries (review-only mode)."""
        return []

    def _risk_ok(self, a: ActionBase, p: ProcessedData) -> bool:
        """Lightweight pre-flight: rejects entries that obviously breach
        concentration. Heavy check goes through risk.exposure.check_action."""
        if not isinstance(a, CreateOrderAction):
            return True
        warnings = (p.risk.get("concentration") or {}).get("warnings", [])
        if any("cap" in w for w in warnings):
            return False
        return True

    # -- 3. render --

    def render_status(self, p: ProcessedData, actions: list[ActionBase]) -> list[str]:
        """Pure: produce a human-readable status block. Caller writes to log."""
        lines = [
            f"[{p.ts_utc}] regime={p.regime}  btc=${p.btc_price}  funding={p.btc_funding_bps}bps  iv={p.iv_pct}%",
            f"  equity=${p.equity_usdc:.2f}  open={len(p.open_positions)}  active_executors={len(self.executors)}",
        ]
        for w in (p.risk.get("concentration", {}) or {}).get("warnings", []):
            lines.append(f"  ⚠ {w}")
        if actions:
            lines.append(f"  → {len(actions)} action(s) proposed:")
            for a in actions:
                lines.append(f"     · {a.__class__.__name__:<22} {a.reason or '(no reason)'}")
        else:
            lines.append("  → no actions this cycle")
        return lines

    # -- runtime glue (the side effect zone) --

    def cycle(self, executor_fn=None, persist_fn=None) -> dict[str, Any]:
        """One full lifecycle. executor_fn(action) → ActionOutcome, persist_fn(action_dict) → swarm id.
        Both are injectable; default = print-only dry-run."""
        p = self.update_processed_data()
        self.processed = p
        actions = self.determine_actions(p)
        status_lines = self.render_status(p, actions)

        outcomes: list[ActionOutcome] = []
        for a in actions:
            if persist_fn:
                persist_fn({"topic": "action.proposed", "content": serialize_action(a)})
            if executor_fn:
                outcome = executor_fn(a)
            else:
                outcome = ActionOutcome(action_id=a.action_id, succeeded=False,
                                        status="DRY_RUN", error="no executor_fn provided")
            outcomes.append(outcome)
            if persist_fn:
                persist_fn({"topic": "action.outcome", "content": serialize_outcome(outcome)})

        return {
            "processed": p,
            "actions":   [serialize_action(a) for a in actions],
            "outcomes":  [serialize_outcome(o) for o in outcomes],
            "status":    status_lines,
        }
