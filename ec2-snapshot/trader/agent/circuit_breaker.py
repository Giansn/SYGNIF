"""agent/circuit_breaker.py — Phase 3.5 loss-streak circuit breaker.

Trips when BOTH conditions hold for the SAME env:
  N consecutive losing closes (default 5)
  AND drawdown ≥ X% from start of streak (default 2%)

When tripped:
  • Writes /var/lib/sygnif/circuit_breaker.json with {state, env, since_utc,
    streak_n, streak_dd_pct, reason}
  • Forces in-process: SYGNIF_TIER_PROMOTION → "0" (no high-conf sizing)
  • Emits agent.circuit_breaker swarm row (DLP relay → Telegram)
  • Lets the trader keep running — but planner sizes default-tier only

Reset is OPERATOR-ONLY (deliberate):
  rm /var/lib/sygnif/circuit_breaker.json
  systemctl restart sygnif-trader

This is a hot-path module. Every cycle calls check_and_apply(N) BEFORE
plan/execute. Failure modes never block planning.

Usage from agent/loop.py:
  from agent import circuit_breaker as CB
  CB.check_and_apply(N)   # call once per cycle, top of run_one_cycle
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
from typing import Any

STATE_FILE = pathlib.Path("/var/lib/sygnif/circuit_breaker.json")
DEFAULT_THRESHOLD_N = 5         # consecutive losses
DEFAULT_THRESHOLD_DD = 2.0      # % drawdown


def _now_utc_iso() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).isoformat()


def _resolve_env() -> str:
    mode = (os.environ.get("SYGNIF_ORDERS_MODE") or "paper").strip().lower()
    return mode if mode in ("paper", "demo", "live") else "paper"


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"state": "ok"}
    try:
        with STATE_FILE.open() as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"state": "ok"}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def is_tripped() -> bool:
    s = _load_state()
    return s.get("state") == "tripped"


def get_state() -> dict:
    return _load_state()


def _get_thresholds() -> tuple[int, float]:
    """Read thresholds from gate_params, fallback to defaults."""
    n_thr = DEFAULT_THRESHOLD_N
    dd_thr = DEFAULT_THRESHOLD_DD
    try:
        from agent import gate_params as GP
        n_thr = int(GP.get("loss_streak_threshold", DEFAULT_THRESHOLD_N))
        dd_thr = float(GP.get("loss_streak_min_dd_pct", DEFAULT_THRESHOLD_DD))
    except Exception:
        pass
    return n_thr, dd_thr


def _recent_outcomes_for_env(N, env: str, limit: int = 20) -> list[dict]:
    """Pull recent outcome.attributed rows for this env, newest first."""
    try:
        r = N.run("swarm.recent", {"limit": limit,
                                       "topic": "outcome.attributed"})
    except Exception:
        return []
    if not r.get("ok"):
        return []
    entries = (r.get("data") or {}).get("entries") or []
    out = []
    for e in entries:
        meta_raw = e.get("meta") or "{}"
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
        except (json.JSONDecodeError, TypeError):
            continue
        if (meta.get("env") or "").lower() != env:
            continue
        try:
            pnl = float(meta.get("closed_pnl") or 0)
        except (ValueError, TypeError):
            continue
        out.append({"pnl": pnl, "ts": e.get("created"),
                    "symbol": meta.get("symbol"),
                    "structure": meta.get("structure")})
    return out   # newest-first per swarm.recent contract


def evaluate(N) -> dict:
    """Compute current breaker state. Returns:
      {trip: bool, env, n_streak, dd_pct, reason, threshold_n, threshold_dd}
    Pure function — does not write state."""
    env = _resolve_env()
    n_thr, dd_thr = _get_thresholds()
    info = {
        "env":          env,
        "n_streak":     0,
        "dd_pct":       0.0,
        "trip":         False,
        "reason":       None,
        "threshold_n":  n_thr,
        "threshold_dd": dd_thr,
    }
    if env == "paper":
        return info

    closes = _recent_outcomes_for_env(N, env, limit=max(n_thr * 2, 20))
    # Walk from most recent backwards counting consecutive losses
    streak = 0
    streak_pnl = 0.0
    for c in closes:
        if c["pnl"] < 0:
            streak += 1
            streak_pnl += c["pnl"]
        else:
            break
    info["n_streak"] = streak
    if streak == 0:
        return info
    # Drawdown vs current equity
    equity = None
    try:
        nm = "wallet.demo" if env == "demo" else "wallet.live"
        r = N.run(nm, {})
        if r.get("ok"):
            d = r.get("data") or {}
            res = d.get("result") or {}
            lst = res.get("list") or []
            if lst:
                equity = float((lst[0].get("totalEquity") or 0))
    except Exception:
        pass
    if equity and equity > 0:
        dd_pct = abs(streak_pnl) / equity * 100.0
        info["dd_pct"] = round(dd_pct, 3)
    if streak >= n_thr and info["dd_pct"] >= dd_thr:
        info["trip"] = True
        info["reason"] = (
            f"{streak} consecutive losses on {env} "
            f"(streak P&L ${streak_pnl:+.2f}, "
            f"{info['dd_pct']:.2f}% drawdown vs ${equity:.0f} equity)")
    return info


def trip(reason: str, env: str, info: dict) -> dict:
    """Persist tripped state + force tier promotion off in-process. Returns
    the saved state dict."""
    state = {
        "state":          "tripped",
        "env":            env,
        "since_utc":      _now_utc_iso(),
        "reason":         reason,
        "n_streak":       info.get("n_streak"),
        "dd_pct":         info.get("dd_pct"),
        "threshold_n":    info.get("threshold_n"),
        "threshold_dd":   info.get("threshold_dd"),
    }
    _save_state(state)
    # Force in-process disables (preserve for any subsequent module loads)
    os.environ["SYGNIF_TIER_PROMOTION"] = "0"
    os.environ["SYGNIF_TIER_FULL"] = "0"
    # Also disable model veto if/when wired in Phase 2.5
    os.environ["SYGNIF_MODEL_VETO_ACTIVE"] = "0"
    return state


def emit_swarm_alert(N, state: dict) -> None:
    """Write agent.circuit_breaker row so DLP+Telegram see it."""
    try:
        N.run("swarm.write", {
            "content": (f"⚠️ CIRCUIT BREAKER TRIPPED [{state.get('env')}] "
                        f"{state.get('reason')}"),
            "swarm_id": "trading",
            "agent_id": "sygnif-trader-loop",
            "topic":    "agent.circuit_breaker",
            "tags":     ["alert", "circuit_breaker",
                          state.get("env") or "unknown"],
            "meta":     state,
        })
    except Exception:
        pass


def check_and_apply(N) -> dict:
    """Hot-path entry. Call once per cycle BEFORE plan. Returns current
    state dict; loop should NOT skip planning if tripped (planner just
    sizes default-tier when SYGNIF_TIER_PROMOTION=0).

    Behavior:
      * If already tripped (state file says so) → re-apply env disables,
        return state.
      * If not tripped → evaluate. If conditions met → trip + alert.
        If conditions NOT met → no action.
    """
    cur = _load_state()
    if cur.get("state") == "tripped":
        # Persistently tripped — re-apply env disables on every cycle
        # (defensive: parent process restart wouldn't see them otherwise).
        os.environ["SYGNIF_TIER_PROMOTION"] = "0"
        os.environ["SYGNIF_TIER_FULL"] = "0"
        os.environ["SYGNIF_MODEL_VETO_ACTIVE"] = "0"
        return cur

    info = evaluate(N)
    if info["trip"]:
        new = trip(info["reason"], info["env"], info)
        emit_swarm_alert(N, new)
        return new
    return {"state": "ok", **info}
