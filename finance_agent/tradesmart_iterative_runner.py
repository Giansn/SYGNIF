"""
Iterative TradeSmart (Noren) trading loop with **adaptive** timing and pluggable strategies.

Starting behaviour: flat → **intraday market** entry → next ticks **flatten** when ``netqty`` shows a
position (same pattern as SYGNIF's fast predict-loop cadence, but for NSE-style symbols).

**Strategy evolution** (lightweight, on-disk): after each tick the runner updates a JSON state file
with iteration counts, error streaks, and bounded ``interval_sec`` / optional ``quantity`` adjustments.
Subclasses of ``IterativeStrategy`` can override ``decide`` / ``after_tick`` to grow richer logic
(quoting, spreads, ML filters) without changing the CLI.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

log = logging.getLogger(__name__)

Action = Literal["noop", "open_buy", "open_sell", "close_flat"]


@dataclass
class RunnerConfig:
    exchange: str = "NSE"
    tradingsymbol: str = "INFY-EQ"
    quantity: int = 1
    product_type: str = "I"  # intraday — adjust (C/M) per your broker rules
    interval_sec: float = 5.0
    min_interval_sec: float = 2.0
    max_interval_sec: float = 60.0
    state_path: Path = field(default_factory=lambda: Path("prediction_agent/tradesmart_iter_state.json"))
    dry_run: bool = False


@dataclass
class IterationState:
    version: int = 1
    iteration: int = 0
    generation: int = 0
    interval_sec: float = 5.0
    quantity: int = 1
    consecutive_errors: int = 0
    successful_closes: int = 0
    last_netqty: int = 0
    last_mtm: float = 0.0
    last_action: str = ""
    last_note: str = ""
    history_tail: list[dict[str, Any]] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_json_dict(cls, d: dict[str, Any]) -> IterationState:
        hist = d.get("history_tail")
        if not isinstance(hist, list):
            hist = []
        return cls(
            version=int(d.get("version") or 1),
            iteration=int(d.get("iteration") or 0),
            generation=int(d.get("generation") or 0),
            interval_sec=float(d.get("interval_sec") or 5.0),
            quantity=max(1, int(d.get("quantity") or 1)),
            consecutive_errors=int(d.get("consecutive_errors") or 0),
            successful_closes=int(d.get("successful_closes") or 0),
            last_netqty=int(d.get("last_netqty") or 0),
            last_mtm=float(d.get("last_mtm") or 0.0),
            last_action=str(d.get("last_action") or ""),
            last_note=str(d.get("last_note") or ""),
            history_tail=[x for x in hist if isinstance(x, dict)][-30:],
        )


def load_state(path: Path, *, defaults: RunnerConfig) -> IterationState:
    if not path.is_file():
        return IterationState(interval_sec=defaults.interval_sec, quantity=defaults.quantity)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return IterationState(interval_sec=defaults.interval_sec, quantity=defaults.quantity)
        st = IterationState.from_json_dict(raw)
        st.interval_sec = float(st.interval_sec)
        st.quantity = max(1, int(st.quantity))
        return st
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return IterationState(interval_sec=defaults.interval_sec, quantity=defaults.quantity)


def save_state(path: Path, st: IterationState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st.to_json_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _position_row_for_symbol(positions: list[dict[str, Any]] | None, symbol: str) -> dict[str, Any] | None:
    if not positions:
        return None
    sym = (symbol or "").strip().upper()
    for row in positions:
        if not isinstance(row, dict):
            continue
        ts = str(row.get("tsym") or row.get("tradingsymbol") or "").strip().upper()
        if ts == sym:
            return row
    return None


def _netqty(row: dict[str, Any] | None) -> int:
    if not row:
        return 0
    for k in ("netqty", "netQty", "NetQty"):
        if k in row:
            try:
                return int(float(str(row[k]).strip()))
            except (TypeError, ValueError):
                return 0
    return 0


def _mtm(row: dict[str, Any] | None) -> float:
    if not row:
        return 0.0
    for k in ("urmtom", "urmtm", "rpnl"):
        if k in row:
            try:
                return float(str(row[k]).strip())
            except (TypeError, ValueError):
                continue
    return 0.0


class IterativeStrategy:
    """Override ``decide`` for richer behaviour; default is pulse in/out."""

    name = "pulse"

    def decide(
        self,
        *,
        netqty: int,
        cfg: RunnerConfig,
        st: IterationState,
    ) -> tuple[Action, str]:
        if netqty == 0:
            return "open_buy", "flat→open_buy pulse"
        return "close_flat", f"flatten netqty={netqty}"

    def after_tick(
        self,
        *,
        cfg: RunnerConfig,
        st: IterationState,
        pos_before: int,
        netqty_after: int | None,
        action: Action,
        ok: bool,
        err: str,
        dry_run: bool,
    ) -> None:
        """Mutate ``st`` for evolution (interval / generation)."""
        if dry_run:
            return
        net_a = netqty_after if netqty_after is not None else pos_before
        if ok:
            st.consecutive_errors = 0
            if action == "close_flat" and pos_before != 0 and net_a == 0:
                st.successful_closes += 1
                st.generation += 1
                # tighten cadence slightly when stable, bounded
                st.interval_sec = max(
                    cfg.min_interval_sec,
                    st.interval_sec * 0.95,
                )
        else:
            st.consecutive_errors += 1
            bump = 1.0 + min(5, st.consecutive_errors) * 0.15
            st.interval_sec = min(cfg.max_interval_sec, st.interval_sec * bump)


class AlternatingSideStrategy(IterativeStrategy):
    """
    Long-only pulse with an **A/B clip** tag by ``generation`` (safe default on NSE cash-style books).
    Extend this class to swap in real directional filters (quotes, ML) without changing the runner.
    """

    name = "alternate"

    def decide(self, *, netqty: int, cfg: RunnerConfig, st: IterationState) -> tuple[Action, str]:
        if netqty != 0:
            return "close_flat", f"flatten netqty={netqty}"
        clip = "A" if st.generation % 2 == 0 else "B"
        return "open_buy", f"entry_clip={clip} gen={st.generation}"


def _place(
    api: Any,
    *,
    action: Action,
    cfg: RunnerConfig,
    qty: int,
    remarks: str,
) -> tuple[bool, str, dict[str, Any] | None]:
    if action in ("noop",):
        return True, "noop", None
    if action == "close_flat":
        return False, "close_flat_should_use_direct_close", None
    bs = "B" if action == "open_buy" else "S"
    try:
        ret = api.place_order(
            bs,
            cfg.product_type,
            cfg.exchange,
            cfg.tradingsymbol,
            qty,
            0,
            "MKT",
            0.0,
            None,
            "DAY",
            None,
            remarks[:20] if remarks else "sygTS",
        )
    except Exception as exc:  # pragma: no cover - network
        return False, str(exc), None
    if not ret or (isinstance(ret, dict) and ret.get("stat") != "Ok"):
        return False, str(ret), ret if isinstance(ret, dict) else None
    return True, "ok", ret if isinstance(ret, dict) else None


def _close_flat(
    api: Any,
    *,
    row: dict[str, Any],
    cfg: RunnerConfig,
    remarks: str,
) -> tuple[bool, str, dict[str, Any] | None]:
    try:
        n = _netqty(row)
    except Exception:
        n = 0
    if n == 0:
        return True, "already_flat", None
    bs = "S" if n > 0 else "B"
    q = abs(int(n))
    try:
        ret = api.place_order(
            bs,
            str(row.get("prd") or cfg.product_type),
            str(row.get("exch") or cfg.exchange),
            str(row.get("tsym") or cfg.tradingsymbol),
            q,
            0,
            "MKT",
            0.0,
            None,
            "DAY",
            None,
            remarks[:20] if remarks else "sygTSx",
        )
    except Exception as exc:  # pragma: no cover
        return False, str(exc), None
    if not ret or (isinstance(ret, dict) and ret.get("stat") != "Ok"):
        return False, str(ret), ret if isinstance(ret, dict) else None
    return True, "ok", ret if isinstance(ret, dict) else None


def run_iteration(
    api: Any,
    cfg: RunnerConfig,
    st: IterationState,
    strategy: IterativeStrategy,
) -> None:
    st.iteration += 1
    positions = api.get_positions()
    if positions is None:
        positions = []
    row = _position_row_for_symbol(positions, cfg.tradingsymbol)
    netqty = _netqty(row)
    pos_before = netqty
    mtm = _mtm(row)
    st.last_mtm = mtm
    st.last_netqty = netqty

    action, note = strategy.decide(netqty=netqty, cfg=cfg, st=st)
    st.last_action = action
    st.last_note = note

    ok = True
    err = ""
    detail: dict[str, Any] | None = None

    net_after: int | None = None
    if cfg.dry_run:
        log.info("DRY_RUN iter=%s action=%s note=%s netqty=%s", st.iteration, action, note, netqty)
    elif action == "close_flat":
        if netqty == 0:
            ok, err, detail = True, "skip_close_already_flat", None
        else:
            ok, err, detail = _close_flat(api, row=row or {}, cfg=cfg, remarks=f"ts{st.iteration}x")
        if ok and err != "skip_close_already_flat":
            pos2 = api.get_positions()
            row2 = _position_row_for_symbol(pos2 if pos2 is not None else [], cfg.tradingsymbol)
            net_after = _netqty(row2)
    elif action in ("open_buy", "open_sell"):
        ok, err, detail = _place(api, action=action, cfg=cfg, qty=st.quantity, remarks=f"ts{st.iteration}")
        if ok:
            pos2 = api.get_positions()
            row2 = _position_row_for_symbol(pos2 if pos2 is not None else [], cfg.tradingsymbol)
            net_after = _netqty(row2)
    else:
        ok, err = True, "noop"

    strategy.after_tick(
        cfg=cfg,
        st=st,
        pos_before=pos_before,
        netqty_after=net_after,
        action=action,
        ok=ok,
        err=err,
        dry_run=cfg.dry_run,
    )

    tail = st.history_tail + [
        {
            "iter": st.iteration,
            "action": action,
            "ok": ok,
            "err": err[:500],
            "netqty": netqty,
            "mtm": round(mtm, 4),
            "interval_next": round(st.interval_sec, 3),
            "gen": st.generation,
        }
    ]
    st.history_tail = tail[-30:]

    if not ok:
        log.warning("iter=%s action=%s failed: %s detail=%s", st.iteration, action, err, detail)


def run_loop(
    *,
    api_factory: Callable[[], Any],
    cfg: RunnerConfig,
    strategy: IterativeStrategy | None = None,
    max_iterations: int = 0,
    on_iteration: Callable[[IterationState], None] | None = None,
) -> None:
    strat = strategy or IterativeStrategy()
    st = load_state(cfg.state_path, defaults=cfg)
    st.interval_sec = max(cfg.min_interval_sec, min(cfg.max_interval_sec, float(st.interval_sec)))
    st.quantity = max(1, int(st.quantity or cfg.quantity))

    api = api_factory()
    n = 0
    while True:
        run_iteration(api, cfg, st, strat)
        save_state(cfg.state_path, st)
        if on_iteration:
            on_iteration(st)
        n += 1
        if max_iterations and n >= max_iterations:
            break
        delay = max(cfg.min_interval_sec, min(cfg.max_interval_sec, float(st.interval_sec)))
        time.sleep(delay)


def strategy_from_name(name: str) -> IterativeStrategy:
    key = (name or "pulse").strip().lower()
    if key == "alternate":
        return AlternatingSideStrategy()
    return IterativeStrategy()


class StubFlatPositionsApi:
    """Offline stub: always flat book (for dry-run demos without OAuth)."""

    def get_positions(self) -> list[dict[str, Any]]:
        return []
