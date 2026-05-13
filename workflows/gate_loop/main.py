"""
SYGNIF gate_loop — Render Workflow

Closes the Predict → Analyze → Proofread → Adjust loop for the swarm gate.

Flow (one tick, triggered by Render cron):
    1. sweep_challenger     — run predict_protocol_gate_optimizer, get new challenger gate env
    2. proofread_offline    — replay champion + challenger on held-out OOS window
    3. proofread_live       — join btc_eval_outcomes.jsonl + horizon snapshots
    4. decide_promotion     — apply conservative promotion gate
    5. append_ledger        — write verdict to prediction_agent/challenger_promotions.jsonl
                              (consumed by training_pipeline/finetune_with_promotions.py
                               in next sygnif-finetune-automation tick)

The existing scripts/predict_protocol_gate_optimizer.py and
scripts/predict_protocol_offline_swarm_backtest.py are reused as subprocesses
so this workflow can ship without refactoring them.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from render_sdk import Retry, Workflows

app = Workflows()

# When deployed: Render mounts the SYGNIF repo with Root Directory = workflows/gate_loop/
# but the rest of the repo is still in the container. Walk up two levels.
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
PREDICTION_AGENT = REPO_ROOT / "prediction_agent"
LEDGER = PREDICTION_AGENT / "challenger_promotions.jsonl"
CHAMPION_ENV = PREDICTION_AGENT / "champion_gate_env.json"
EVAL_OUTCOMES = PREDICTION_AGENT / "btc_eval_outcomes.jsonl"


# ---------------------------------------------------------------------------
# Phase 3.2 — analyze: sweep for a challenger gate env
# ---------------------------------------------------------------------------
@app.task(
    timeout_seconds=3600,
    retry=Retry(max_retries=1, wait_duration_ms=30_000, backoff_scaling=1.0),
)
def sweep_challenger(
    window_hours: int = 72,
    trials: int = 50,
    folds: int = 4,
    engine: str = "tpe",
    step: int = 6,
) -> dict:
    """Run the existing gate optimizer and return the proposed challenger env."""
    cmd = [
        sys.executable,
        str(SCRIPTS / "predict_protocol_gate_optimizer.py"),
        "--engine", engine,
        "--trials", str(trials),
        "--hours", str(window_hours),
        "--step", str(step),
        "--walk-forward",
        "--wf-folds", str(folds),
        "--json-summary",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3000)
    if proc.returncode != 0:
        raise RuntimeError(f"gate_optimizer failed: {proc.stderr[-800:]}")
    # Optimizer prints best-gate JSON to stdout (per its docstring).
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "challenger_env": payload.get("env") or payload.get("best_env") or payload,
        "in_sample_score": payload.get("score") or payload.get("best_score"),
        "engine": engine,
        "trials": trials,
        "folds": folds,
        "window_hours": window_hours,
    }


# ---------------------------------------------------------------------------
# Phase 3.x — proofread (offline OOS)
# ---------------------------------------------------------------------------
def _run_offline_backtest(gate_env: dict, hours: int, step: int = 4) -> dict:
    """Invoke predict_protocol_offline_swarm_backtest with a given gate env."""
    env = os.environ.copy()
    env.update({k: str(v) for k, v in gate_env.items()})
    cmd = [
        sys.executable,
        str(SCRIPTS / "predict_protocol_offline_swarm_backtest.py"),
        "--hours", str(hours),
        "--step", str(step),
        "--apply-swarm-gate",
        "--json",
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=1500, env=env
    )
    if proc.returncode != 0:
        raise RuntimeError(f"offline_backtest failed: {proc.stderr[-600:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


@app.task(
    timeout_seconds=2400,
    retry=Retry(max_retries=2, wait_duration_ms=10_000, backoff_scaling=2.0),
)
def proofread_offline(
    champion_env: dict,
    challenger_env: dict,
    oos_hours: int = 24,
    step: int = 4,
) -> dict:
    """Replay BOTH gates on the same held-out OOS window; return deltas."""
    champ = _run_offline_backtest(champion_env, oos_hours, step)
    chal = _run_offline_backtest(challenger_env, oos_hours, step)
    return {
        "oos_hours": oos_hours,
        "champion_pnl": champ.get("pnl_usdt_approx", 0.0),
        "challenger_pnl": chal.get("pnl_usdt_approx", 0.0),
        "delta_pnl_pct": _pct_delta(
            chal.get("pnl_usdt_approx", 0.0),
            champ.get("pnl_usdt_approx", 0.0),
        ),
        "champion_win_rate": champ.get("win_rate", 0.0),
        "challenger_win_rate": chal.get("win_rate", 0.0),
        "delta_win_rate": (chal.get("win_rate", 0.0) - champ.get("win_rate", 0.0)),
        "champion_max_dd": champ.get("max_drawdown", 0.0),
        "challenger_max_dd": chal.get("max_drawdown", 0.0),
        "delta_max_dd": (chal.get("max_drawdown", 0.0) - champ.get("max_drawdown", 0.0)),
        "n_trades_champion": champ.get("n_trades", 0),
        "n_trades_challenger": chal.get("n_trades", 0),
    }


# ---------------------------------------------------------------------------
# Phase 3.x — proofread (live horizon)
# ---------------------------------------------------------------------------
@app.task(timeout_seconds=600)
def proofread_live(oos_hours: int = 24) -> dict:
    """Read btc_eval_outcomes.jsonl resolved rows in the OOS window.

    Returns horizon pass-rate + Brier score over rows where the next-bar
    outcome was actually resolved (true hold-out per btc_forecast_eval).
    """
    if not EVAL_OUTCOMES.is_file():
        return {"n_resolved": 0, "live_pass_rate": None, "brier": None}

    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - oos_hours * 3600
    rows: list[dict] = []
    with EVAL_OUTCOMES.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = row.get("resolved_utc") or row.get("ts")
            if not ts:
                continue
            try:
                row_ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError):
                continue
            if row_ts >= cutoff:
                rows.append(row)

    if not rows:
        return {"n_resolved": 0, "live_pass_rate": None, "brier": None}

    correct = sum(1 for r in rows if r.get("direction_correct"))
    brier_sum = sum(
        (float(r.get("predicted_prob_up", 0.5)) - float(r.get("actual_up", 0.0))) ** 2
        for r in rows
    )
    return {
        "n_resolved": len(rows),
        "live_pass_rate": correct / len(rows),
        "brier": brier_sum / len(rows),
        "window_hours": oos_hours,
    }


# ---------------------------------------------------------------------------
# Decision gate — conservative promotion
# ---------------------------------------------------------------------------
@app.task
def decide_promotion(offline: dict, live: dict) -> dict:
    """Promote only if all criteria pass."""
    reasons: list[str] = []
    delta_pnl = float(offline.get("delta_pnl_pct") or 0.0)
    delta_win = float(offline.get("delta_win_rate") or 0.0)
    delta_dd = float(offline.get("delta_max_dd") or 0.0)
    n_resolved = int(live.get("n_resolved") or 0)
    live_pass = live.get("live_pass_rate")
    brier = live.get("brier")

    if delta_pnl < 2.0:
        reasons.append(f"delta_pnl_pct {delta_pnl:.2f}% < +2%")
    if delta_win < 0.0:
        reasons.append(f"delta_win_rate {delta_win:.3f} regressed")
    if delta_dd > 0.0:
        reasons.append(f"delta_max_dd {delta_dd:.3f} worsened")
    if n_resolved < 30:
        reasons.append(f"n_resolved {n_resolved} < 30 (insufficient live sample)")
    elif live_pass is not None and live_pass < 0.50:
        reasons.append(f"live_pass_rate {live_pass:.2f} below break-even")

    verdict = "reject" if reasons else "promote"
    if reasons and n_resolved < 30 and not any("delta_pnl" in r for r in reasons):
        verdict = "abstain"

    return {
        "verdict": verdict,
        "reason": "; ".join(reasons) if reasons else "all gates passed",
        "delta_pnl_pct": delta_pnl,
        "delta_win_rate": delta_win,
        "delta_max_dd": delta_dd,
        "live_pass_rate": live_pass,
        "brier": brier,
        "n_resolved": n_resolved,
    }


# ---------------------------------------------------------------------------
# Adjust — write to ledger (consumed by finetune_with_promotions)
# ---------------------------------------------------------------------------
@app.task
def append_ledger(row: dict) -> dict:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    row = {**row, "appended_utc": datetime.now(timezone.utc).isoformat()}
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    return {"ok": True, "path": str(LEDGER), "verdict": row.get("verdict")}


# ---------------------------------------------------------------------------
# Top-level orchestrator (cron entry point)
# ---------------------------------------------------------------------------
@app.task(timeout_seconds=7200)
async def orchestrate(
    window_hours: int = 72,
    trials: int = 50,
    folds: int = 4,
    oos_hours: int = 24,
    engine: str = "tpe",
) -> dict:
    sweep, champion = await asyncio.gather(
        sweep_challenger(window_hours, trials, folds, engine),
        _read_champion_env(),
    )
    offline = await proofread_offline(
        champion, sweep["challenger_env"], oos_hours
    )
    live = await proofread_live(oos_hours)
    verdict = await decide_promotion(offline, live)
    row = {
        **verdict,
        "challenger_env": sweep["challenger_env"],
        "champion_env_at_test": champion,
        "sweep": {
            "engine": sweep["engine"],
            "trials": sweep["trials"],
            "folds": sweep["folds"],
            "window_hours": sweep["window_hours"],
            "in_sample_score": sweep.get("in_sample_score"),
        },
        "offline": offline,
        "live": live,
    }
    await append_ledger(row)
    return verdict


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
async def _read_champion_env() -> dict:
    """Read the current promoted gate env (last `promote` row of ledger, else file)."""
    if LEDGER.is_file():
        last_promote: dict | None = None
        with LEDGER.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("verdict") == "promote" and r.get("challenger_env"):
                    last_promote = r["challenger_env"]
        if last_promote is not None:
            return last_promote
    if CHAMPION_ENV.is_file():
        return json.loads(CHAMPION_ENV.read_text(encoding="utf-8"))
    # Fall back to current process env (whatever is set in trader.env).
    return {
        k: v for k, v in os.environ.items()
        if k.startswith("SWARM_ORDER_") or k.startswith("SYGNIF_GATE_")
    }


def _pct_delta(new: float, old: float) -> float:
    if old == 0:
        return 0.0 if new == 0 else (100.0 if new > 0 else -100.0)
    return (new - old) / abs(old) * 100.0


if __name__ == "__main__":
    app.start()
