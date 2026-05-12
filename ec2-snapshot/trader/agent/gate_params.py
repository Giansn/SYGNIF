"""agent/gate_params.py — Phase 3.1 single source of truth for tunable
gate thresholds. Reads /var/lib/sygnif/gate_params.json and exposes get()
with a hard-coded fallback so callers degrade gracefully when the file
is missing or malformed.

Two files in /var/lib/sygnif/:
  gate_params.json              — CHAMPION (active in production)
  gate_params_challenger.json   — what the gate optimizer proposes next

The trader/optimizer/drift-monitor/circuit-breaker all read CHAMPION.
The optimizer WRITES challenger; promotion is operator-only (cp + restart).

API:
  from agent.gate_params import get, get_all, set_param, list_history
  iv_min = get("theta_iv_rv_min", default=1.10)   # always returns a value
  bounds = get_bounds("theta_iv_rv_min")          # tuple or None

Bounds are HARD limits — the gate optimizer cannot push past them. They
are operator-curated; everything between bounds is system-tunable.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
from typing import Any

PARAMS_DIR = pathlib.Path("/var/lib/sygnif")
CHAMPION = PARAMS_DIR / "gate_params.json"
CHALLENGER = PARAMS_DIR / "gate_params_challenger.json"

# Hard-coded defaults + bounds (operator-curated). When the file is absent
# or a key is missing, get() returns the default below. When the file
# exists, it overrides. Bounds are absolute — optimizer will not write
# challenger values outside [lo, hi].
_DEFAULTS = {
    # Gate name             default   [lo,    hi]    description
    "theta_iv_rv_min":       (1.10,  (1.00,  1.50)),  # short_iron_condor IV/RV min
    "theta_iv_rv_min_loose": (0.85,  (0.70,  1.05)),  # weekly opening / friday gate
    "vol_buy_iv_rv_max":     (0.70,  (0.50,  0.90)),  # buy-volatility threshold
    "perp_min_score":        (0.40,  (0.20,  0.80)),  # perp_runner total score min
    "min_liq_buffer_bps":    (8.0,   (5.0,   30.0)),  # G5 liquidation buffer
    "psych_barrier_dist_bps": (50.0, (25.0,  200.0)), # tier-candidate psych proximity
    "predict_consecutive_min": (3,    (2,     10)),    # regime override window
    "max_concurrent_open":   (7,     (3,     15)),    # global cap on open positions
    "max_risk_pct_default":  (1.5,   (0.5,   3.0)),   # default risk %ile cap
    "preferred_leverage":    (5.0,   (2.0,   15.0)),  # default perp leverage
    "tier_full_min_delta_r": (0.30,  (0.10,  0.60)),  # tier_audit promote threshold
    "loss_streak_threshold": (5,     (3,     10)),    # circuit-breaker N losses
    "loss_streak_min_dd_pct": (2.0,  (1.0,   10.0)),  # AND drawdown >= X%
    "drift_kl_alert":        (0.20,  (0.05,  0.50)),  # drift monitor KL threshold

    # 2026-05-10 — bounce protocol tunables (agent/bounce_protocol.py).
    # Only bounce_move_threshold_pct is auto-swept by the gate optimizer
    # (see GATES registry in sygnif_gate_optimizer.py). The other three
    # are operator-tunable: ratio is post-hoc (target size, not gate),
    # horizon and cooldown shape the WS daemon's alert cadence.
    "bounce_move_threshold_pct": (1.5,  (0.8,   3.0)),    # min move % to trigger
    "bounce_ratio":              (0.40, (0.20,  0.70)),   # expected counter-move ratio
    "bounce_horizon_min":        (30,   (10,    90)),     # how long setup is valid
    "bounce_cooldown_min":       (15,   (5,     60)),     # min between same-dir alerts

    # 2026-05-10 Path C — whale-alignment gate (off by default, can be promoted
    # by operator after whale_alignment audit shows aligned cohort beats
    # diverged cohort by ≥0.30 R/trade across ≥20 trades per bucket).
    #
    # whale_alignment_required: 0 = ignore whales, 1 = only trade when our
    # direction matches whale direction (with strength ≥ threshold).
    "whale_alignment_required":  (0,     (0,     1)),
    # Minimum whale-flow strength (0..1) required for alignment check.
    # 0.0 = always count alignment; 0.5 = only when whales clearly directional.
    "whale_strength_min":        (0.30,  (0.0,   1.0)),
    # Multiplier for trade size when DIVERGING from whales (only applied when
    # whale_alignment_required=0). 1.0 = no change. <1.0 = bet smaller against
    # the herd. >1.0 = bet bigger when contrarian (only set if audit shows
    # divergence is profitable).
    "whale_divergence_size_mult": (1.0,  (0.3,   2.0)),
}


def _load_file(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open() as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _now_utc_iso() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).isoformat()


def _ensure_initialized() -> None:
    """Create gate_params.json from _DEFAULTS if missing. Idempotent."""
    if CHAMPION.exists():
        return
    PARAMS_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "version":     1,
        "updated_utc": _now_utc_iso(),
        "params":      {k: v[0] for k, v in _DEFAULTS.items()},
        "bounds":      {k: list(v[1]) for k, v in _DEFAULTS.items()},
        "history":     [{"version": 1, "updated_utc": _now_utc_iso(),
                          "changes": {}, "reason": "initial"}],
    }
    try:
        with CHAMPION.open("w") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        import sys
        sys.stderr.write(
            f"[gate_params._ensure_initialized] {type(e).__name__}: {e}\n")


def get(name: str, default: Any = None) -> Any:
    """Return the active value for a gate parameter. Falls back to the
    hard-coded default in _DEFAULTS, then the caller-supplied default,
    then None. Never raises — this is on the hot path."""
    try:
        _ensure_initialized()
        data = _load_file(CHAMPION)
        if isinstance(data.get("params"), dict) and name in data["params"]:
            return data["params"][name]
    except Exception:
        pass
    if name in _DEFAULTS:
        return _DEFAULTS[name][0]
    return default


def get_all(file: str = "champion") -> dict:
    """Return the full params dict from champion or challenger."""
    path = CHALLENGER if file == "challenger" else CHAMPION
    _ensure_initialized()
    data = _load_file(path)
    return data.get("params") or {}


def get_bounds(name: str) -> tuple | None:
    """Return [lo, hi] bounds tuple, or None if not specified."""
    _ensure_initialized()
    data = _load_file(CHAMPION)
    bounds = data.get("bounds") or {}
    if name in bounds:
        b = bounds[name]
        if isinstance(b, list) and len(b) == 2:
            return tuple(b)
    if name in _DEFAULTS:
        return _DEFAULTS[name][1]
    return None


def set_param(name: str, value: Any, *, file: str = "challenger",
              reason: str = "manual", actor: str = "operator") -> dict:
    """Update one parameter. Default writes to challenger (gate optimizer's
    proposal staging area); pass file='champion' to update production
    directly (operator promotion).

    Returns the updated record. Validates bounds; raises ValueError on
    out-of-bounds."""
    _ensure_initialized()
    path = CHALLENGER if file == "challenger" else CHAMPION
    bounds = get_bounds(name)
    if bounds is not None and isinstance(value, (int, float)):
        lo, hi = bounds
        if not (lo <= value <= hi):
            raise ValueError(
                f"value {value!r} out of bounds [{lo}, {hi}] for {name!r}")
    # Load (or seed from champion if challenger doesn't exist)
    data = _load_file(path)
    if not data:
        data = _load_file(CHAMPION)
    params = dict(data.get("params") or {})
    old = params.get(name)
    params[name] = value
    history = list(data.get("history") or [])
    new_version = (data.get("version") or 0) + 1
    history.append({
        "version":     new_version,
        "updated_utc": _now_utc_iso(),
        "actor":       actor,
        "changes":     {name: {"from": old, "to": value}},
        "reason":      reason,
    })
    new = {
        "version":     new_version,
        "updated_utc": _now_utc_iso(),
        "params":      params,
        "bounds":      data.get("bounds") or {
            k: list(v[1]) for k, v in _DEFAULTS.items()},
        "history":     history[-50:],   # last 50 changes only
    }
    PARAMS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(new, f, indent=2)
    os.replace(tmp, path)
    return new


def list_history(limit: int = 20, file: str = "champion") -> list[dict]:
    """Return recent change history."""
    path = CHALLENGER if file == "challenger" else CHAMPION
    _ensure_initialized()
    data = _load_file(path)
    h = data.get("history") or []
    return h[-limit:]


def champion_vs_challenger() -> dict:
    """Return diff between current champion and pending challenger."""
    _ensure_initialized()
    champ = (_load_file(CHAMPION).get("params") or {})
    chal_data = _load_file(CHALLENGER)
    chal = (chal_data.get("params") or {})
    diffs = {}
    all_keys = set(champ) | set(chal)
    for k in sorted(all_keys):
        c = champ.get(k)
        ch = chal.get(k)
        if c != ch:
            diffs[k] = {"champion": c, "challenger": ch}
    return {
        "challenger_present":  bool(chal_data),
        "challenger_version":  chal_data.get("version"),
        "challenger_updated":  chal_data.get("updated_utc"),
        "diffs":               diffs,
    }
