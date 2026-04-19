#!/usr/bin/env python3
"""
**Hyperparameter search** for ``finance_agent/swarm_order_gate.swarm_fusion_allows`` using the **offline**
simulator ``predict_protocol_offline_swarm_backtest.run_simulation`` (public 5m klines, no venue).

Engines:

- **optuna** (default if installed): **TPE sampler** (sequential model-based / Bayesian-style search).
- **random**: uniform independent samples (no extra deps).
- **pyswarms**: **particle swarm** on **continuous** knobs only; boolean / categorical gates come from
  ``--pyswarms-bool-preset`` (``relaxed`` | ``mid`` | ``strict``).

**Hivemind:** default **synthetic** ``offline_hm_vote`` ∈ {-1,0,1} in search space; use ``--offline-hm-source demo_*``
to drive ``sources.hm`` from **Bybit demo** ``position/list`` (``BYBIT_DEMO_*``) instead — then HM is **not** hyperopt-sampled.

**Nautilus age:** if ``SWARM_ORDER_NAUTILUS_MAX_AGE_MIN`` > 0, the sim **patches** ``generated_utc`` to **now**
each run so the freshness gate is testable on a static JSON file.

Outputs **best gate env** JSON to stdout (and optional ``--log-jsonl``).

Install::

  pip install optuna          # Bayesian-style (recommended)
  pip install pyswarms        # optional PSO engine

Examples::

  cd ~/SYGNIF && python3 scripts/predict_protocol_gate_optimizer.py --trials 30 --seed 1 --json-summary

  python3 scripts/predict_protocol_gate_optimizer.py --engine random --trials 50 --hours 36 --step 6

  python3 scripts/predict_protocol_gate_optimizer.py --engine pyswarms --iters 15 --particles 12 \\
    --pyswarms-bool-preset relaxed --hours 24 --step 8

  # Walk-forward: optimize on **first** slice of the trailing ``--hours`` window, report OOS on the rest::

  python3 scripts/predict_protocol_gate_optimizer.py --walk-forward --wf-folds 4 --trials 40 --hours 72

  # Flat OOS slices (no position carry from IS / prior folds)::

  python3 scripts/predict_protocol_gate_optimizer.py --walk-forward --wf-folds 4 --wf-independent-folds ...
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
_PA = _REPO / "prediction_agent"
_DATA = _REPO / "finance_agent" / "btc_specialist" / "data"


def _cat01(rng: random.Random, trial: Any | None, name: str, *, engine: str) -> str:
    if engine == "optuna" and trial is not None:
        return trial.suggest_categorical(name, ["0", "1"])
    return "1" if rng.random() >= 0.5 else "0"


def _float_param(
    rng: random.Random,
    trial: Any | None,
    name: str,
    lo: float,
    hi: float,
    *,
    engine: str,
) -> float:
    if engine == "optuna" and trial is not None:
        return trial.suggest_float(name, lo, hi)
    return lo + (hi - lo) * rng.random()


def _int_cat(
    rng: random.Random,
    trial: Any | None,
    name: str,
    choices: list[int],
    *,
    engine: str,
) -> int:
    if engine == "optuna" and trial is not None:
        return int(trial.suggest_categorical(name, choices))
    return rng.choice(choices)


def suggest_full_gate_trial(
    *,
    rng: random.Random,
    trial: Any | None,
    engine: str,
    search_offline_hm_vote: bool = True,
) -> tuple[dict[str, str], int, bool]:
    """Return (gate_env, offline_hm_vote, patch_nautilus_ts).

    When ``search_offline_hm_vote`` is False, ``offline_hm_vote`` is always 0; ``run_simulation`` should use
    ``offline_hm_source=demo_*`` so ``sources.hm`` comes from Bybit demo position instead.
    """
    ge: dict[str, str] = {}
    ge["SWARM_ORDER_MIN_MEAN_LONG"] = str(
        _float_param(rng, trial, "SWARM_ORDER_MIN_MEAN_LONG", 0.0, 0.55, engine=engine)
    )
    ge["SWARM_ORDER_MAX_MEAN_SHORT"] = str(
        _float_param(rng, trial, "SWARM_ORDER_MAX_MEAN_SHORT", -0.55, 0.0, engine=engine)
    )
    ge["SWARM_ORDER_BLOCK_CONFLICT"] = _cat01(rng, trial, "SWARM_ORDER_BLOCK_CONFLICT", engine=engine)
    ge["SWARM_ORDER_REQUIRE_BTC_FUTURE"] = _cat01(rng, trial, "SWARM_ORDER_REQUIRE_BTC_FUTURE", engine=engine)
    ge["SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE"] = _cat01(
        rng, trial, "SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE", engine=engine
    )
    ge["SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS"] = _cat01(
        rng, trial, "SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS", engine=engine
    )
    ge["SYGNIF_SWARM_BTC_FUTURE_GOVERNANCE"] = _cat01(
        rng, trial, "SYGNIF_SWARM_BTC_FUTURE_GOVERNANCE", engine=engine
    )
    ge["SWARM_ORDER_REQUIRE_HIVEMIND_VOTE"] = _cat01(
        rng, trial, "SWARM_ORDER_REQUIRE_HIVEMIND_VOTE", engine=engine
    )
    ge["SWARM_ORDER_HIVEMIND_VOTE_FLAT_PASS"] = _cat01(
        rng, trial, "SWARM_ORDER_HIVEMIND_VOTE_FLAT_PASS", engine=engine
    )
    if search_offline_hm_vote:
        hm_vote = _int_cat(rng, trial, "offline_hm_vote", [-1, 0, 1], engine=engine)
    else:
        hm_vote = 0
    ge["SWARM_ORDER_BLOCK_SWARM_BEAR_LABEL"] = _cat01(
        rng, trial, "SWARM_ORDER_BLOCK_SWARM_BEAR_LABEL", engine=engine
    )
    ge["SWARM_ORDER_BLOCK_SWARM_BULL_LABEL"] = _cat01(
        rng, trial, "SWARM_ORDER_BLOCK_SWARM_BULL_LABEL", engine=engine
    )
    naut_age = _float_param(rng, trial, "SWARM_ORDER_NAUTILUS_MAX_AGE_MIN", 0.0, 10080.0, engine=engine)
    ge["SWARM_ORDER_NAUTILUS_MAX_AGE_MIN"] = str(max(0.0, naut_age))
    ge["SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY"] = _cat01(
        rng, trial, "SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY", engine=engine
    )
    ge["SWARM_ORDER_REQUIRE_NAUTILUS_ALIGN"] = _cat01(
        rng, trial, "SWARM_ORDER_REQUIRE_NAUTILUS_ALIGN", engine=engine
    )
    ge["SWARM_ORDER_NAUTILUS_FLAT_PASS"] = _cat01(rng, trial, "SWARM_ORDER_NAUTILUS_FLAT_PASS", engine=engine)
    ge["SWARM_ORDER_ML_LOGREG_MIN_CONF"] = str(
        _float_param(rng, trial, "SWARM_ORDER_ML_LOGREG_MIN_CONF", 0.0, 92.0, engine=engine)
    )
    ge["SWARM_ORDER_REQUIRE_FUSION_ALIGN"] = _cat01(
        rng, trial, "SWARM_ORDER_REQUIRE_FUSION_ALIGN", engine=engine
    )
    ge["SWARM_ORDER_FUSION_ALIGN_LABEL"] = _cat01(rng, trial, "SWARM_ORDER_FUSION_ALIGN_LABEL", engine=engine)
    ge["SWARM_ORDER_FUSION_REQUIRE_STRONG"] = _cat01(
        rng, trial, "SWARM_ORDER_FUSION_REQUIRE_STRONG", engine=engine
    )
    ge["SWARM_ORDER_FUSION_ALIGN_BTC_FUTURE"] = _cat01(
        rng, trial, "SWARM_ORDER_FUSION_ALIGN_BTC_FUTURE", engine=engine
    )
    patch_naut = float(ge["SWARM_ORDER_NAUTILUS_MAX_AGE_MIN"] or 0) > 1e-6
    return ge, hm_vote, patch_naut


def _bool_preset(name: str) -> dict[str, str]:
    """Fixed categorical env for pyswarms (continuous-only PSO)."""
    if name == "strict":
        return {
            "SWARM_ORDER_BLOCK_CONFLICT": "1",
            "SWARM_ORDER_REQUIRE_BTC_FUTURE": "1",
            "SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE": "1",
            "SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS": "0",
            "SYGNIF_SWARM_BTC_FUTURE_GOVERNANCE": "1",
            "SWARM_ORDER_REQUIRE_HIVEMIND_VOTE": "0",
            "SWARM_ORDER_HIVEMIND_VOTE_FLAT_PASS": "1",
            "SWARM_ORDER_BLOCK_SWARM_BEAR_LABEL": "1",
            "SWARM_ORDER_BLOCK_SWARM_BULL_LABEL": "1",
            "SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY": "1",
            "SWARM_ORDER_REQUIRE_NAUTILUS_ALIGN": "0",
            "SWARM_ORDER_NAUTILUS_FLAT_PASS": "1",
            "SWARM_ORDER_REQUIRE_FUSION_ALIGN": "1",
            "SWARM_ORDER_FUSION_ALIGN_LABEL": "1",
            "SWARM_ORDER_FUSION_REQUIRE_STRONG": "0",
            "SWARM_ORDER_FUSION_ALIGN_BTC_FUTURE": "1",
        }
    if name == "mid":
        return {
            "SWARM_ORDER_BLOCK_CONFLICT": "1",
            "SWARM_ORDER_REQUIRE_BTC_FUTURE": "1",
            "SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE": "1",
            "SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS": "1",
            "SYGNIF_SWARM_BTC_FUTURE_GOVERNANCE": "0",
            "SWARM_ORDER_REQUIRE_HIVEMIND_VOTE": "0",
            "SWARM_ORDER_HIVEMIND_VOTE_FLAT_PASS": "1",
            "SWARM_ORDER_BLOCK_SWARM_BEAR_LABEL": "0",
            "SWARM_ORDER_BLOCK_SWARM_BULL_LABEL": "0",
            "SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY": "1",
            "SWARM_ORDER_REQUIRE_NAUTILUS_ALIGN": "0",
            "SWARM_ORDER_NAUTILUS_FLAT_PASS": "1",
            "SWARM_ORDER_REQUIRE_FUSION_ALIGN": "1",
            "SWARM_ORDER_FUSION_ALIGN_LABEL": "1",
            "SWARM_ORDER_FUSION_REQUIRE_STRONG": "0",
            "SWARM_ORDER_FUSION_ALIGN_BTC_FUTURE": "0",
        }
    # relaxed
    return {
        "SWARM_ORDER_BLOCK_CONFLICT": "0",
        "SWARM_ORDER_REQUIRE_BTC_FUTURE": "1",
        "SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE": "0",
        "SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS": "1",
        "SYGNIF_SWARM_BTC_FUTURE_GOVERNANCE": "0",
        "SWARM_ORDER_REQUIRE_HIVEMIND_VOTE": "0",
        "SWARM_ORDER_HIVEMIND_VOTE_FLAT_PASS": "1",
        "SWARM_ORDER_BLOCK_SWARM_BEAR_LABEL": "0",
        "SWARM_ORDER_BLOCK_SWARM_BULL_LABEL": "0",
        "SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY": "0",
        "SWARM_ORDER_REQUIRE_NAUTILUS_ALIGN": "0",
        "SWARM_ORDER_NAUTILUS_FLAT_PASS": "1",
        "SWARM_ORDER_REQUIRE_FUSION_ALIGN": "0",
        "SWARM_ORDER_FUSION_ALIGN_LABEL": "1",
        "SWARM_ORDER_FUSION_REQUIRE_STRONG": "0",
        "SWARM_ORDER_FUSION_ALIGN_BTC_FUTURE": "0",
    }


def gate_from_pyswarms_row(
    row: list[float],
    *,
    preset: str,
    hm_vote: int = 0,
) -> tuple[dict[str, str], int, bool]:
    """row: [min_long, max_short, ml_conf, naut_age] — bounds applied in objective."""
    min_l = max(0.0, min(0.55, float(row[0])))
    max_s = max(-0.55, min(0.0, float(row[1])))
    ml_c = max(0.0, min(92.0, float(row[2])))
    naut_age = max(0.0, min(10080.0, float(row[3])))
    ge = dict(_bool_preset(preset))
    ge["SWARM_ORDER_MIN_MEAN_LONG"] = str(min_l)
    ge["SWARM_ORDER_MAX_MEAN_SHORT"] = str(max_s)
    ge["SWARM_ORDER_ML_LOGREG_MIN_CONF"] = str(ml_c)
    ge["SWARM_ORDER_NAUTILUS_MAX_AGE_MIN"] = str(naut_age)
    patch = naut_age > 1e-6
    return ge, hm_vote, patch


def _load_sim():
    sys.path.insert(0, str(_REPO / "scripts"))
    import predict_protocol_offline_swarm_backtest as mod  # noqa: PLC0415

    return mod


def _walk_forward_plan(
    *,
    symbol: str,
    kline_limit: int,
    hours: float,
    wf_folds: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Fetch klines only; build trailing bar bounds and WF slices (no ML)."""
    sys.path.insert(0, str(_REPO / "prediction_agent"))
    from btc_predict_live import fetch_linear_5m_klines  # noqa: PLC0415

    lim = max(200, min(1000, int(kline_limit)))
    hours = max(1.0, float(hours))
    bars_eval = int(math.ceil(hours * 12))
    try:
        df = fetch_linear_5m_klines(symbol, limit=lim)
    except Exception as exc:  # noqa: BLE001
        return None, {"error": f"kline_fetch_failed:{exc}"}
    n = len(df)
    if n < bars_eval + 80:
        return None, {"error": "not_enough_klines", "n": n, "bars_eval": bars_eval}
    sys.path.insert(0, str(_REPO / "scripts"))
    import predict_protocol_offline_swarm_backtest as mod  # noqa: PLC0415

    es, ee = mod.default_eval_bar_bounds(n=n, hours=hours)
    try:
        slices = mod.walk_forward_bar_slices(es, ee, wf_folds)
    except ValueError as exc:
        return None, {"error": str(exc), "es": es, "ee": ee, "n": n}
    return {"n": n, "es": es, "ee": ee, "slices": slices}, None


def main() -> int:
    ap = argparse.ArgumentParser(description="Optimize swarm gate env vs offline predict-protocol sim")
    ap.add_argument("--engine", choices=("optuna", "random", "pyswarms"), default="optuna")
    ap.add_argument("--trials", type=int, default=30, help="Optuna trials or random samples")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--hours", type=float, default=36.0)
    ap.add_argument("--step", type=int, default=6)
    ap.add_argument("--kline-limit", type=int, default=1000)
    ap.add_argument("--notional-usdt", type=float, default=2000.0)
    ap.add_argument("--hold-on-no-edge", action="store_true")
    ap.add_argument("--training-json", type=Path, default=_PA / "training_channel_output.json")
    ap.add_argument("--nautilus-json", type=Path, default=_DATA / "nautilus_strategy_signal.json")
    ap.add_argument("--tp-pct", type=float, default=None)
    ap.add_argument("--sl-pct", type=float, default=None)
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--particles", type=int, default=10, help="pyswarms: swarm size")
    ap.add_argument("--iters", type=int, default=12, help="pyswarms: iterations")
    ap.add_argument(
        "--pyswarms-bool-preset",
        choices=("relaxed", "mid", "strict"),
        default="relaxed",
    )
    ap.add_argument("--pyswarms-hm-vote", type=int, default=0, choices=[-1, 0, 1])
    ap.add_argument("--log-jsonl", type=Path, default=None, help="Append one JSON line per trial")
    ap.add_argument("--json-summary", action="store_true", help="Print best payload as JSON")
    ap.add_argument(
        "--walk-forward",
        action="store_true",
        help="Optimize on first WF slice of trailing --hours; evaluate best gates on remaining slices (OOS)",
    )
    ap.add_argument(
        "--wf-folds",
        type=int,
        default=3,
        help="Number of contiguous slices (>=2); slice 0 = in-sample for search",
    )
    ap.add_argument(
        "--wf-independent-folds",
        action="store_true",
        help="Walk-forward: each OOS slice starts flat (no position carry from IS / prior OOS)",
    )
    ap.add_argument(
        "--offline-hm-source",
        choices=("synthetic", "demo_once", "demo_refresh"),
        default="synthetic",
        help="sources.hm: synthetic (hyperopt) vs Bybit demo position/list (BYBIT_DEMO_*; not bar-accurate history)",
    )
    ap.add_argument(
        "--offline-hm-symbol",
        default=None,
        help="Linear symbol for demo HM (default: --symbol)",
    )
    args = ap.parse_args()

    rng = random.Random(args.seed)
    search_offline_hm_vote = args.offline_hm_source == "synthetic"
    sim_mod = _load_sim()
    run_simulation = sim_mod.run_simulation

    wf_slices: list[tuple[int, int]] | None = None
    if args.walk_forward:
        plan, err = _walk_forward_plan(
            symbol=args.symbol,
            kline_limit=args.kline_limit,
            hours=args.hours,
            wf_folds=max(2, int(args.wf_folds)),
        )
        if err:
            print(json.dumps({"ok": False, "walk_forward_error": err}, indent=2), file=sys.stderr)
            return 2
        assert plan is not None
        wf_slices = plan["slices"]

    wf_is_kwargs: dict[str, int] = {}
    if wf_slices:
        _lo, _hi = wf_slices[0]
        wf_is_kwargs = {"eval_bar_start": _lo, "eval_bar_end": _hi}

    def evaluate(
        ge: dict[str, str],
        hm_vote: int,
        patch_naut: bool,
        *,
        eval_bar_start: int | None = None,
        eval_bar_end: int | None = None,
        initial_sim_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return run_simulation(
            hours=args.hours,
            step=args.step,
            kline_limit=args.kline_limit,
            window=5,
            rf_trees=max(10, int(os.environ.get("ASAP_RF_TREES", "32") or 32)),
            xgb_estimators=max(20, int(os.environ.get("ASAP_XGB_N_ESTIMATORS", "60") or 60)),
            notional=args.notional_usdt,
            leverage=50.0,
            margin_usdt=None,
            hold_on_no_edge=args.hold_on_no_edge,
            training_path=args.training_json,
            nautilus_path=args.nautilus_json,
            apply_swarm_gate=True,
            gate_env=ge,
            tp_pct=args.tp_pct,
            sl_pct=args.sl_pct,
            symbol=args.symbol,
            patch_nautilus_generated_utc=patch_naut,
            offline_hm_vote=hm_vote,
            offline_hm_source=args.offline_hm_source,
            offline_hm_symbol=args.offline_hm_symbol,
            eval_bar_start=eval_bar_start,
            eval_bar_end=eval_bar_end,
            initial_sim_state=initial_sim_state,
        )

    best: dict[str, Any] | None = None
    best_pnl = -1e30
    log_lines: list[dict[str, Any]] = []

    if args.engine in ("optuna", "random"):
        try:
            import optuna  # noqa: PLC0415
        except ImportError:
            optuna = None  # type: ignore[assignment]
        if args.engine == "optuna" and optuna is None:
            print("predict_protocol_gate_optimizer: optuna not installed; use pip install optuna", file=sys.stderr)
            return 2

        def objective(trial: Any) -> float:
            nonlocal best, best_pnl
            ge, hm_vote, patch_naut = suggest_full_gate_trial(
                rng=rng,
                trial=trial,
                engine="optuna",
                search_offline_hm_vote=search_offline_hm_vote,
            )
            r = evaluate(ge, hm_vote, patch_naut, **wf_is_kwargs)
            pnl = float(r.get("pnl_usdt_approx") or -1e18) if r.get("ok") else -1e18
            rec: dict[str, Any] = {
                "pnl": pnl,
                "gate_env": ge,
                "offline_hm_vote": hm_vote,
                "patch_nautilus": patch_naut,
                "offline_hm_source": args.offline_hm_source,
            }
            if wf_slices:
                rec["wf_phase"] = "is_search"
            log_lines.append(rec)
            if args.log_jsonl:
                args.log_jsonl.parent.mkdir(parents=True, exist_ok=True)
                with args.log_jsonl.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            if pnl > best_pnl:
                best_pnl = pnl
                best = rec
            return -pnl

        if args.engine == "optuna":
            study = optuna.create_study(
                direction="minimize",
                sampler=optuna.samplers.TPESampler(seed=args.seed),
            )
            study.optimize(objective, n_trials=max(1, args.trials), show_progress_bar=False)
        else:
            for _ in range(max(1, args.trials)):
                ge, hm_vote, patch_naut = suggest_full_gate_trial(
                    rng=rng,
                    trial=None,
                    engine="random",
                    search_offline_hm_vote=search_offline_hm_vote,
                )
                r = evaluate(ge, hm_vote, patch_naut, **wf_is_kwargs)
                pnl = float(r.get("pnl_usdt_approx") or -1e18) if r.get("ok") else -1e18
                rec = {
                    "pnl": pnl,
                    "gate_env": ge,
                    "offline_hm_vote": hm_vote,
                    "patch_nautilus": patch_naut,
                    "offline_hm_source": args.offline_hm_source,
                }
                if wf_slices:
                    rec["wf_phase"] = "is_search"
                log_lines.append(rec)
                if args.log_jsonl:
                    args.log_jsonl.parent.mkdir(parents=True, exist_ok=True)
                    with args.log_jsonl.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, separators=(",", ":")) + "\n")
                if pnl > best_pnl:
                    best_pnl = pnl
                    best = rec

    elif args.engine == "pyswarms":
        try:
            import numpy as np  # noqa: PLC0415
            from pyswarms.single.global_best import GlobalBestPSO  # noqa: PLC0415
        except ImportError:
            print(
                "predict_protocol_gate_optimizer: pyswarms+numpy required "
                "(pip install pyswarms numpy)",
                file=sys.stderr,
            )
            return 2

        bounds = (
            np.array([0.0, -0.55, 0.0, 0.0]),
            np.array([0.55, 0.0, 92.0, 10080.0]),
        )
        hm = int(args.pyswarms_hm_vote)

        def obj_pso(x: Any) -> Any:
            pnls = []
            for i in range(x.shape[0]):
                ge, hv, patch_n = gate_from_pyswarms_row(list(x[i]), preset=args.pyswarms_bool_preset, hm_vote=hm)
                r = evaluate(ge, hv, patch_n, **wf_is_kwargs)
                pnl = float(r.get("pnl_usdt_approx") or -1e18) if r.get("ok") else -1e18
                pnls.append(-pnl)
            return np.array(pnls, dtype=np.float64)

        opt = GlobalBestPSO(n_particles=max(3, args.particles), dimensions=4, options={"c1": 0.5, "c2": 0.3, "w": 0.9})
        cost, pos = opt.optimize(obj_pso, iters=max(1, args.iters), verbose=False)
        ge, hm_vote, patch_naut = gate_from_pyswarms_row(list(pos), preset=args.pyswarms_bool_preset, hm_vote=hm)
        r = evaluate(ge, hm_vote, patch_naut, **wf_is_kwargs)
        pnl = float(r.get("pnl_usdt_approx") or -1e18) if r.get("ok") else -1e18
        best = {
            "pnl": pnl,
            "gate_env": ge,
            "offline_hm_vote": hm_vote,
            "patch_nautilus": patch_naut,
            "pyswarms_best_cost": float(cost),
            "pyswarms_position": [float(x) for x in pos],
        }
        best_pnl = pnl

    wf_report: dict[str, Any] | None = None
    if wf_slices and best:
        carry = not bool(args.wf_independent_folds)
        oos_rows: list[dict[str, Any]] = []
        oos_sum = 0.0
        r_is_rerun: dict[str, Any] | None = None
        state_next: dict[str, Any] | None = None
        if carry:
            is_lo, is_hi = wf_slices[0]
            r_is_rerun = evaluate(
                best["gate_env"],
                int(best["offline_hm_vote"]),
                bool(best["patch_nautilus"]),
                eval_bar_start=is_lo,
                eval_bar_end=is_hi,
                initial_sim_state=None,
            )
            state_next = r_is_rerun.get("sim_state_out") if r_is_rerun.get("ok") else None
            if args.log_jsonl and r_is_rerun.get("ok"):
                rec_is = {
                    "wf_phase": "is_rerun_carry_seed",
                    "pnl": float(r_is_rerun.get("pnl_usdt_approx") or 0.0),
                    "gate_env": best["gate_env"],
                    "offline_hm_vote": best["offline_hm_vote"],
                    "patch_nautilus": best["patch_nautilus"],
                    "slice_bars": [is_lo, is_hi],
                    "sim_state_out": r_is_rerun.get("sim_state_out"),
                }
                args.log_jsonl.parent.mkdir(parents=True, exist_ok=True)
                with args.log_jsonl.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec_is, separators=(",", ":")) + "\n")

        for j, (lo, hi) in enumerate(wf_slices[1:], start=1):
            st_in = None if args.wf_independent_folds else state_next
            r_o = evaluate(
                best["gate_env"],
                int(best["offline_hm_vote"]),
                bool(best["patch_nautilus"]),
                eval_bar_start=lo,
                eval_bar_end=hi,
                initial_sim_state=st_in,
            )
            pnl_o = float(r_o.get("pnl_usdt_approx") or 0.0) if r_o.get("ok") else None
            if pnl_o is not None:
                oos_sum += pnl_o
            row: dict[str, Any] = {
                "fold": j,
                "slice_bars": [lo, hi],
                "pnl_usdt_approx": pnl_o,
                "ok": r_o.get("ok"),
                "eval_window_utc": r_o.get("eval_window_utc"),
                "sim_state_in": r_o.get("sim_state_in"),
                "sim_state_out": r_o.get("sim_state_out"),
            }
            oos_rows.append(row)
            if carry and r_o.get("ok"):
                state_next = r_o.get("sim_state_out")
            if args.log_jsonl:
                rec_o = {
                    "wf_phase": "oos_fold",
                    "fold": j,
                    "pnl": pnl_o,
                    "gate_env": best["gate_env"],
                    "offline_hm_vote": best["offline_hm_vote"],
                    "patch_nautilus": best["patch_nautilus"],
                    "slice_bars": [lo, hi],
                }
                args.log_jsonl.parent.mkdir(parents=True, exist_ok=True)
                with args.log_jsonl.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec_o, separators=(",", ":")) + "\n")

        wf_report = {
            "mode": "trailing_hours_then_slices",
            "folds": len(wf_slices),
            "wf_carry_state": carry,
            "slices_bars": [[a, b] for a, b in wf_slices],
            "is_slice_bars": [wf_slices[0][0], wf_slices[0][1]],
            "is_best_pnl_usdt_approx": best.get("pnl"),
            "is_rerun_pnl_usdt_approx": (
                float(r_is_rerun["pnl_usdt_approx"]) if r_is_rerun and r_is_rerun.get("ok") else None
            ),
            "oos": oos_rows,
            "oos_pnl_sum_usdt_approx": round(oos_sum, 2),
        }

    disc = "Offline only; no fees/funding; static Nautilus; synthetic HM in optimizer. Review OOS before live .env."
    if args.walk_forward:
        disc += (
            " Walk-forward: best gates on IS slice 0; OOS uses forward slices. "
            "Default: position state carries IS→OOS→OOS (see wf_carry_state); "
            "use --wf-independent-folds for flat each slice."
        )

    out = {
        "engine": args.engine,
        "walk_forward": args.walk_forward,
        "offline_hm_source": args.offline_hm_source,
        "offline_hm_symbol": args.offline_hm_symbol or args.symbol,
        "best_pnl_usdt_approx": best_pnl,
        "best": best,
        "walk_forward_report": wf_report,
        "disclaimer": disc,
    }
    if args.json_summary:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
