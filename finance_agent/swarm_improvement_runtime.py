"""
Bridge **Swarm auto-improvement** → **Bybit demo predict-loop** tuning.

- ``build_demo_runtime_hints(bundle)`` — derive ``prediction_agent/swarm_demo_runtime_hints.json``
  from ``swarm_weak_points_solution.build_swarm_weak_points_bundle`` output (conservative rules only).
- ``write_demo_runtime_hints(repo, hints)`` — atomic JSON write.
- ``apply_demo_runtime_hints_env(repo)`` — if ``SYGNIF_SWARM_RUNTIME_HINTS_APPLY=1`` and hints exist / unexpired,
  **assign** whitelisted env vars (run **after** launcher ``setdefault`` block so hints can override defaults).
  Hint lifetime: ``SYGNIF_SWARM_RUNTIME_HINTS_TTL_HOURS`` (default **2**, max **168**) when building JSON.

**Safety:** only keys in ``_HINT_ENV_ALLOWLIST`` may be applied; values are clamped. No order placement.
Hints may set gate relaxations (``SWARM_ORDER_REQUIRE_*``, ``SWARM_ORDER_ML_LOGREG_MIN_CONF``,
``SWARM_ORDER_NAUTILUS_MAX_AGE_MIN``, ``SYGNIF_PREDICT_DEFAULT_NOTIONAL_USDT``) when weak-point rules fire.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _repo_default(repo: Path | None) -> Path:
    return repo or Path(__file__).resolve().parent.parent


def _prediction_agent(repo: Path) -> Path:
    raw = (os.environ.get("SYGNIF_PREDICTION_AGENT_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return repo / "prediction_agent"


def runtime_hints_path(repo: Path | None = None) -> Path:
    return _prediction_agent(_repo_default(repo)) / "swarm_demo_runtime_hints.json"


def weak_points_latest_path(repo: Path | None = None) -> Path:
    return _prediction_agent(_repo_default(repo)) / "swarm_weak_points_latest.json"


def _env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _parse_iso_utc(s: str | None) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    t = s.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(t)
    except ValueError:
        return None


def _runtime_hints_ttl_hours(*, explicit: float | None) -> float:
    """TTL for ``swarm_demo_runtime_hints.json`` (``expires_utc``). Env when ``explicit`` is None."""
    if explicit is not None:
        return max(0.25, min(168.0, float(explicit)))
    raw = (os.environ.get("SYGNIF_SWARM_RUNTIME_HINTS_TTL_HOURS") or "").strip()
    if not raw:
        return 2.0
    try:
        v = float(raw)
    except ValueError:
        return 2.0
    return max(1.0, min(168.0, v))


# Keys the predict launcher may pick up from hints (values clamped in build).
_URL_HINT_KEYS: frozenset[str] = frozenset(
    {
        "SYGNIF_NEUROLINKED_HTTP_URL",
        "SYGNIF_NEUROLINKED_HOST_URL",
    }
)


def _put_url(env_apply: dict[str, str], key: str, val: str) -> None:
    try:
        from finance_agent.neurolinked_connectivity import sanitize_loopback_neurolinked_url  # noqa: PLC0415
    except ImportError:
        from neurolinked_connectivity import sanitize_loopback_neurolinked_url  # noqa: PLC0415

    if key not in _URL_HINT_KEYS:
        return
    s = sanitize_loopback_neurolinked_url(val)
    if s:
        env_apply[key] = s


_HINT_ENV_ALLOWLIST: dict[str, tuple[str, str, float, float]] = {
    # name: (type char 'f'|'i', default ignored here — min max for numeric clamp)
    "SYGNIF_SWARM_LOOP_INTERVAL_SEC": ("f", "", 15.0, 600.0),
    "SWARM_BYBIT_ENTRY_COOLDOWN_SEC": ("f", "", 30.0, 600.0),
    "SYGNIF_PREDICT_MIN_DISCRETIONARY_CLOSE_SEC": ("f", "", 0.0, 600.0),
    "PREDICT_LOOP_REFRESH_ALIGNED_EVERY_N": ("i", "", 0.0, 24.0),
    "SYGNIF_PREDICT_OPEN_IMMEDIATE": ("i", "", 0.0, 1.0),  # 0/1
    "SWARM_ORDER_NAUTILUS_MAX_AGE_MIN": ("f", "", 0.0, 180.0),
    "SWARM_ORDER_ML_LOGREG_MIN_CONF": ("f", "", 0.0, 100.0),
    "SWARM_ORDER_REQUIRE_HIVEMIND_VOTE": ("i", "", 0.0, 1.0),
    "SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY": ("i", "", 0.0, 1.0),
    "SWARM_ORDER_REQUIRE_FUSION_ALIGN": ("i", "", 0.0, 1.0),
    "SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE": ("i", "", 0.0, 1.0),
    "SWARM_BYBIT_MAX_CONSEC_OPEN_FAILS": ("i", "", 0.0, 200.0),
    "SYGNIF_PREDICT_DEFAULT_NOTIONAL_USDT": ("f", "", 5000.0, 250000.0),
}


def _clamp_numeric(key: str, val: str) -> str:
    spec = _HINT_ENV_ALLOWLIST.get(key)
    if not spec:
        return val
    kind, _d, lo, hi = spec
    try:
        if kind == "i":
            n = int(float(val))
            n = int(max(lo, min(hi, float(n))))
            return str(n)
        n = float(val)
        n = max(lo, min(hi, n))
        # stringify int-like floats for loop interval
        if n == int(n):
            return str(int(n))
        return str(n)
    except (TypeError, ValueError):
        return val


def _put(env_apply: dict[str, str], key: str, val: str) -> None:
    """Set hint env (clamped). Later calls may overwrite same key."""
    if key in _HINT_ENV_ALLOWLIST:
        env_apply[key] = _clamp_numeric(key, val)


def build_demo_runtime_hints(bundle: dict[str, Any], *, ttl_hours: float | None = None) -> dict[str, Any]:
    """
    Map **weak-points recommendations** + dataset heuristics → ``env_apply`` for the predict launcher.

    Covers: ``venue_churn``, ``gate_rate_very_low``, ``hivemind_unreachable``, ``nautilus_model_tension``,
    ``logreg_direction_gate``, ``bf_alignment``, ``consec_open_fails`` (plus optional notion reduction on churn).

    ``ttl_hours``: hint file lifetime (``expires_utc``). Default **2** unless ``SYGNIF_SWARM_RUNTIME_HINTS_TTL_HOURS``
    is set (clamped **1–168** for multi-day tuning windows).
    """
    eff_ttl = _runtime_hints_ttl_hours(explicit=ttl_hours)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=eff_ttl)
    rec_ids = [str(r.get("id") or "") for r in (bundle.get("recommendations") or []) if isinstance(r, dict)]
    rec_set = set(rec_ids)
    triggered: list[str] = []
    env_apply: dict[str, str] = {}
    notes: list[str] = []

    ds = bundle.get("predict_loop_dataset") or {}
    top_blocks = (ds.get("top_block_reasons") or []) if ds.get("ok") else []
    top_reason = str(top_blocks[0][0]) if top_blocks else ""

    # --- High severity: venue churn (demo leg tail) ---
    if "venue_churn" in rec_set:
        triggered.append("venue_churn")
        _put(env_apply, "SYGNIF_PREDICT_OPEN_IMMEDIATE", "0")
        _put(env_apply, "SYGNIF_SWARM_LOOP_INTERVAL_SEC", "120")
        _put(env_apply, "SWARM_BYBIT_ENTRY_COOLDOWN_SEC", "200")
        _put(env_apply, "SYGNIF_PREDICT_MIN_DISCRETIONARY_CLOSE_SEC", "60")
        _put(env_apply, "PREDICT_LOOP_REFRESH_ALIGNED_EVERY_N", "2")
        _put(env_apply, "SYGNIF_PREDICT_DEFAULT_NOTIONAL_USDT", "80000")
        notes.append(
            "venue_churn: paced opens, longer loop interval, cooldown, discretionary close wall, lower notional"
        )

    # --- Gate pass rate very low (even without churn recommendation) ---
    if ds.get("ok"):
        rate = float(ds.get("gate_ok_rate") or 0.0)
        if rate < 0.25:
            if "gate_rate_very_low" not in triggered:
                triggered.append("gate_rate_very_low")
            if "SYGNIF_SWARM_LOOP_INTERVAL_SEC" not in env_apply:
                _put(env_apply, "SYGNIF_SWARM_LOOP_INTERVAL_SEC", "90")
            notes.append(f"gate_ok_rate={rate:.2f}: calmer loop interval")

    # --- Hivemind unreachable: rely on flat-pass only by dropping hard hm requirement ---
    if "hivemind_unreachable" in rec_set:
        triggered.append("hivemind_unreachable")
        _put(env_apply, "SWARM_ORDER_REQUIRE_HIVEMIND_VOTE", "0")
        notes.append("hivemind_unreachable: SWARM_ORDER_REQUIRE_HIVEMIND_VOTE=0 (flat-pass semantics elsewhere)")

    # --- Nautilus vs model tension: relax research veto (short-term unblock) ---
    if "nautilus_model_tension" in rec_set:
        triggered.append("nautilus_model_tension")
        _put(env_apply, "SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY", "0")
        _put(env_apply, "SWARM_ORDER_NAUTILUS_MAX_AGE_MIN", "12")
        notes.append("nautilus_model_tension: not_contrary off + max sidecar age 12m")

    # --- Logreg direction gate pressure ---
    logreg_heavy = "logreg_direction_gate" in rec_set or ("ml_logreg" in top_reason.lower())
    if logreg_heavy:
        if "logreg_direction_gate" not in triggered:
            triggered.append("logreg_direction_gate")
        _put(env_apply, "SWARM_ORDER_ML_LOGREG_MIN_CONF", "52")
        notes.append("logreg_direction_gate: lower SWARM_ORDER_ML_LOGREG_MIN_CONF floor")

    # --- bf vote vs target blocks ---
    bf_top_n = 0
    if top_blocks and str(top_blocks[0][0]).startswith("swarm_bf_vote"):
        try:
            bf_top_n = int(top_blocks[0][1])
        except (TypeError, ValueError):
            bf_top_n = 0
    if "bf_alignment" in rec_set or bf_top_n >= 15:
        if "bf_alignment" not in triggered:
            triggered.append("bf_alignment")
        _put(env_apply, "SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE", "0")
        notes.append("bf_alignment: SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE=0 (flat-pass knobs still apply)")

    # --- Venue open failures: raise consec cap so transient API errors do not hard-stop opens ---
    if "consec_open_fails" in rec_set:
        triggered.append("consec_open_fails")
        _put(env_apply, "SWARM_BYBIT_MAX_CONSEC_OPEN_FAILS", "40")
        _put(env_apply, "SWARM_BYBIT_ENTRY_COOLDOWN_SEC", "240")
        notes.append("consec_open_fails: higher consec cap + longer entry cooldown")

    # --- Fusion-align: relax when logreg or nautilus heuristics fire ---
    if logreg_heavy and "SWARM_ORDER_REQUIRE_FUSION_ALIGN" not in env_apply:
        _put(env_apply, "SWARM_ORDER_REQUIRE_FUSION_ALIGN", "0")
        notes.append("logreg_direction_gate: SWARM_ORDER_REQUIRE_FUSION_ALIGN=0")

    if "nautilus_model_tension" in rec_set and "SWARM_ORDER_REQUIRE_FUSION_ALIGN" not in env_apply:
        _put(env_apply, "SWARM_ORDER_REQUIRE_FUSION_ALIGN", "0")
        notes.append("nautilus_model_tension: SWARM_ORDER_REQUIRE_FUSION_ALIGN=0")

    if "swarm_compute_fallback" in rec_set:
        if "swarm_compute_fallback" not in triggered:
            triggered.append("swarm_compute_fallback")
        notes.append("swarm_compute_fallback: verify BYBIT_DEMO_* + SYGNIF_SWARM_BTC_FUTURE (no env auto-set)")

    if "neurolinked_use_loopback_8889" in rec_set:
        triggered.append("neurolinked_use_loopback_8889")
        fixed = "http://127.0.0.1:8889"
        _put_url(env_apply, "SYGNIF_NEUROLINKED_HTTP_URL", fixed)
        _put_url(env_apply, "SYGNIF_NEUROLINKED_HOST_URL", fixed)
        notes.append(
            "neurolinked_use_loopback_8889: set SYGNIF_NEUROLINKED_HTTP_URL + HOST to match sygnif-neurolinked (:8889)"
        )

    # Stable ordering for operators / diffs
    triggered = list(dict.fromkeys(triggered))

    return {
        "schema_version": 1,
        "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_utc": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "swarm_improvement_runtime.build_demo_runtime_hints",
        "triggered_by": triggered,
        "env_apply": env_apply,
        "notes": notes,
    }


def write_demo_runtime_hints(repo: Path, hints: dict[str, Any]) -> Path:
    path = runtime_hints_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(hints, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def write_weak_points_latest(repo: Path, bundle: dict[str, Any]) -> Path:
    """Persist full bundle for dashboards (bounded recommendations list)."""
    path = weak_points_latest_path(repo)
    out = dict(bundle)
    recs = out.get("recommendations")
    if isinstance(recs, list) and len(recs) > 24:
        out["recommendations"] = recs[:24]
        out["recommendations_truncated"] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def compact_weak_points_for_state(bundle: dict[str, Any]) -> dict[str, Any]:
    """Small dict for ``swarm_auto_improvement_state.json`` + history rows."""
    ds = bundle.get("predict_loop_dataset") or {}
    cp = bundle.get("closed_pnl") or {}
    tail = cp.get("last_50_legs") if isinstance(cp, dict) else {}
    top = ds.get("top_block_reasons") if isinstance(ds, dict) else []
    top_one = top[0] if isinstance(top, list) and top and isinstance(top[0], (list, tuple)) else None
    return {
        "generated_utc": bundle.get("generated_utc"),
        "swarm_live_ok": (bundle.get("swarm_live") or {}).get("ok"),
        "swarm_label": (bundle.get("swarm_live") or {}).get("swarm_label"),
        "gate_ok_rate": ds.get("gate_ok_rate") if ds.get("ok") else None,
        "target_side_counts": ds.get("target_side_counts") if ds.get("ok") else None,
        "last_row": ds.get("last_row") if ds.get("ok") else None,
        "closed_tail": tail if isinstance(tail, dict) else None,
        "top_block": {"reason": top_one[0], "n": top_one[1]} if top_one else None,
        "recommendation_ids": [r.get("id") for r in (bundle.get("recommendations") or []) if isinstance(r, dict)],
    }


def apply_demo_runtime_hints_env(repo: Path | None = None) -> dict[str, Any]:
    """
    Apply ``swarm_demo_runtime_hints.json`` to ``os.environ`` when enabled.

    Set ``SYGNIF_SWARM_RUNTIME_HINTS_APPLY=1`` on the **predict-loop** process (e.g. systemd unit).
    """
    if not _env_truthy("SYGNIF_SWARM_RUNTIME_HINTS_APPLY"):
        return {"ok": True, "applied": False, "reason": "SYGNIF_SWARM_RUNTIME_HINTS_APPLY_off"}

    r = _repo_default(repo)
    path = runtime_hints_path(r)
    if not path.is_file():
        return {"ok": True, "applied": False, "reason": "no_hints_file"}

    try:
        hints = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "applied": False, "reason": str(exc)[:120]}

    exp = _parse_iso_utc(str(hints.get("expires_utc") or ""))
    if exp is not None and datetime.now(timezone.utc) > exp:
        return {"ok": True, "applied": False, "reason": "hints_expired", "expires_utc": hints.get("expires_utc")}

    env_apply = hints.get("env_apply")
    if not isinstance(env_apply, dict):
        return {"ok": True, "applied": False, "reason": "no_env_apply"}

    applied: dict[str, str] = {}
    try:
        from finance_agent.neurolinked_connectivity import sanitize_loopback_neurolinked_url  # noqa: PLC0415
    except ImportError:
        from neurolinked_connectivity import sanitize_loopback_neurolinked_url  # noqa: PLC0415

    for k, v in env_apply.items():
        if k in _URL_HINT_KEYS:
            if not isinstance(v, (str, int, float)):
                continue
            su = sanitize_loopback_neurolinked_url(str(v).strip())
            if su:
                os.environ[k] = su
                applied[k] = su
            continue
        if k not in _HINT_ENV_ALLOWLIST:
            continue
        if not isinstance(v, (str, int, float)):
            continue
        s = _clamp_numeric(k, str(v).strip())
        os.environ[k] = s
        applied[k] = s

    return {
        "ok": True,
        "applied": True,
        "from": str(path),
        "triggered_by": hints.get("triggered_by"),
        "keys": applied,
    }
