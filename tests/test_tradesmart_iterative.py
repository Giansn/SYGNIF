"""finance_agent/tradesmart_iterative_runner.py — state + strategy (no live Noren)."""
from __future__ import annotations

from pathlib import Path

from finance_agent.tradesmart_iterative_runner import (
    AlternatingSideStrategy,
    IterationState,
    IterativeStrategy,
    RunnerConfig,
    StubFlatPositionsApi,
    load_state,
    run_iteration,
    run_loop,
    save_state,
    strategy_from_name,
)


def test_load_save_state_roundtrip(tmp_path: Path) -> None:
    cfg = RunnerConfig(state_path=tmp_path / "st.json", interval_sec=7.0, quantity=2)
    st = load_state(cfg.state_path, defaults=cfg)
    st.iteration = 3
    st.interval_sec = 4.2
    save_state(cfg.state_path, st)
    st2 = load_state(cfg.state_path, defaults=cfg)
    assert st2.iteration == 3
    assert abs(st2.interval_sec - 4.2) < 1e-6


def test_pulse_strategy_close_then_evolve(tmp_path: Path) -> None:
    cfg = RunnerConfig(
        state_path=tmp_path / "st.json",
        interval_sec=10.0,
        min_interval_sec=2.0,
        max_interval_sec=60.0,
        dry_run=False,
        tradingsymbol="INFY-EQ",
    )
    st = IterationState(interval_sec=10.0, quantity=1)

    class Api:
        def __init__(self) -> None:
            self._net = 1

        def get_positions(self):
            row = {"tsym": "INFY-EQ", "exch": "NSE", "prd": "I", "netqty": self._net}
            return [row]

        def place_order(self, *args: object, **kwargs: object) -> dict:
            bs = str(args[0]) if args else ""
            if bs == "S":
                self._net = 0
            elif bs == "B":
                self._net = 1
            return {"stat": "Ok", "norenordno": "mock1"}

    api = Api()
    strat = IterativeStrategy()
    run_iteration(api, cfg, st, strat)
    assert st.last_action == "close_flat"
    assert st.successful_closes >= 1
    assert st.generation >= 1
    assert st.interval_sec < 10.0
    run_iteration(api, cfg, st, strat)
    assert st.last_action == "open_buy"


def test_dry_run_skips_evolution(tmp_path: Path) -> None:
    cfg = RunnerConfig(state_path=tmp_path / "st.json", interval_sec=10.0, dry_run=True)
    st = IterationState(interval_sec=10.0)

    class Api:
        def get_positions(self):
            return [{"tsym": "INFY-EQ", "netqty": 0}]

    run_iteration(Api(), cfg, st, IterativeStrategy())
    assert st.interval_sec == 10.0
    assert st.consecutive_errors == 0


def test_strategy_from_name() -> None:
    assert strategy_from_name("pulse").name == "pulse"
    assert strategy_from_name("alternate").name == "alternate"


def test_run_loop_stub_offline(tmp_path: Path) -> None:
    cfg = RunnerConfig(
        state_path=tmp_path / "st.json",
        interval_sec=0.01,
        min_interval_sec=0.01,
        max_interval_sec=1.0,
        dry_run=True,
    )
    run_loop(api_factory=StubFlatPositionsApi, cfg=cfg, max_iterations=3)
    st = load_state(cfg.state_path, defaults=cfg)
    assert st.iteration >= 3


def test_alternating_only_long_when_flat() -> None:
    s = AlternatingSideStrategy()
    act, note = s.decide(netqty=0, cfg=RunnerConfig(), st=IterationState(generation=3))
    assert act == "open_buy"
    assert "entry_clip" in note
