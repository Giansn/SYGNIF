"""SYGNIF trade journal — entry/exit/block path tracking with full context.

Solves: "is this strategy winning because of itself, or because of an
accumulation of triggers that happen to co-occur with it?"

Captures per-event context vectors so multivariate ablation analysis can
distinguish a real-edge entry path from one that's riding a trigger
correlation.

Three event kinds, each appended atomically to ~/.sygnif/journal/ as
NDJSON (one record per line, write-once, never pruned):

  entry-YYYY-MM.ndjson   — every action="open" decision (with full ctx + gate trace)
  exit-YYYY-MM.ndjson    — every CLOSE decision from the R-ladder
  block-YYYY-MM.ndjson   — every gate-blocked attempt (counterfactual analysis)

Each event has a `decision_id` (UUID) so entries and exits can be joined
later via `linked_entry_decision_id`. The position_id (Bybit) is recorded
when known so positions can be tracked across the daemon/trader split.

Multi-process safety: NDJSON append from independent writers is safe as
long as each line is a single short write (POSIX guarantees atomicity for
writes < PIPE_BUF). We flush after every record.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Journal location. Override with SYGNIF_JOURNAL_DIR for testing.
JOURNAL_DIR = Path(os.environ.get("SYGNIF_JOURNAL_DIR",
                                    str(Path.home() / ".sygnif" / "journal")))
JOURNAL_DIR.mkdir(parents=True, exist_ok=True)


def _ymd_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _path(kind: str) -> Path:
    return JOURNAL_DIR / f"{kind}-{_ymd_month()}.ndjson"


def _write(kind: str, record: dict) -> None:
    """Append one NDJSON record atomically. Best-effort; never raises."""
    try:
        line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
        with open(_path(kind), "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        # NEVER let journaling break trading. Log to stderr only.
        try:
            import sys
            sys.stderr.write(f"[journal] write {kind} failed: "
                              f"{type(e).__name__}: {e}\n")
        except Exception:
            pass


def new_decision_id() -> str:
    return uuid.uuid4().hex[:16]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------------
# Context-vector helpers
# ----------------------------------------------------------------------


def _walk(d: Any, *keys, default=None) -> Any:
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d


def context_from_snap(snap: dict | None) -> dict:
    """Extract a compact context vector from a discovery snapshot.

    These are the SAME features the gates read, so we can later run
    counterfactual analysis: "would this trade have been blocked by
    gate X if it existed at decision time?"
    """
    if not isinstance(snap, dict):
        return {}
    spot = (snap.get("btc_perp_last")
            or _walk(snap, "btc_focus", "perp", "last"))
    options = snap.get("options") or {}
    return {
        "regime": snap.get("regime_label") or snap.get("regime"),
        "spot": float(spot) if isinstance(spot, (int, float)) else None,
        "btc_24h_hi": snap.get("btc_24h_high"),
        "btc_24h_lo": snap.get("btc_24h_low"),
        "iv_pct": snap.get("atm_iv_nearest") or snap.get("iv_pct"),
        "iv_rv_ratio": snap.get("iv_realized_ratio_1h"),
        "funding_last_pct": snap.get("funding_last_pct"),
        "funding_pctile": snap.get("funding_pctile"),
        "max_pain": options.get("max_pain_strike") or _walk(options, "max_pain", "strike"),
        "nearest_expiry": options.get("nearest_expiry") or options.get("nearest_live_expiry"),
        "liq_asymmetry": _walk(snap, "liquidity_levels", "asymmetry"),
        "nearest_low_below": _walk(snap, "liquidity_levels", "nearest_low_below"),
        "nearest_high_above": _walk(snap, "liquidity_levels", "nearest_high_above"),
    }


def context_from_microstructure() -> dict:
    """Pull the latest snapshot from microstructure-feed (orderbook,
    insurance, funding-prediction). Tolerant of missing/stale data."""
    p = Path.home() / ".sygnif" / "microstructure-snapshot.json"
    try:
        if not p.exists() or (time.time() - p.stat().st_mtime) > 300:
            return {}
        d = json.loads(p.read_text())
    except Exception:
        return {}
    sym = (d.get("symbols") or {}).get("BTCUSDT") or {}
    ins = d.get("insurance") or {}
    fnd = (d.get("funding") or {}).get("BTCUSDT") or {}
    return {
        "ob_imbalance": sym.get("imbalance"),
        "ob_bid_top5_usd": sym.get("bid_top5_usd"),
        "ob_ask_top5_usd": sym.get("ask_top5_usd"),
        "ob_spread_bps": sym.get("spread_bps"),
        "insurance_pool_usdt": ins.get("pool_usdt"),
        "insurance_delta_24h_pct": ins.get("delta_24h_pct"),
        "funding_predicted": fnd.get("predicted"),
    }


def context_from_hivemind() -> dict:
    """Skew + IV-rank snapshot from hivemind-options feed."""
    p = Path.home() / ".sygnif" / "hivemind-snapshot.json"
    try:
        if not p.exists() or (time.time() - p.stat().st_mtime) > 600:
            return {}
        d = json.loads(p.read_text())
    except Exception:
        return {}
    expiries = d.get("expiries") or []
    if not expiries:
        return {}
    nearest = expiries[0]
    return {
        "skew_25d_nearest": nearest.get("skew_25d"),
        "atm_iv_nearest": nearest.get("atm_iv"),
        "iv_p50_nearest": nearest.get("iv_p50"),
        "Pr_break_24h_hi_nearest": nearest.get("Pr_break_24h_hi"),
        "Pr_break_24h_lo_nearest": nearest.get("Pr_break_24h_lo"),
    }


def context_from_liquidations() -> dict:
    """Liquidation cluster summary from daemon."""
    p = Path.home() / ".sygnif" / "liquidation-summary.json"
    try:
        if not p.exists() or (time.time() - p.stat().st_mtime) > 90:
            return {}
        d = json.loads(p.read_text())
    except Exception:
        return {}
    btc = (d.get("symbols") or {}).get("BTCUSDT") or {}
    w5 = btc.get("300s") or {}
    w1 = btc.get("60s") or {}
    return {
        "liq_5m_long_usd": w5.get("longs_liq_usd"),
        "liq_5m_short_usd": w5.get("shorts_liq_usd"),
        "liq_5m_imbalance": w5.get("imbalance"),
        "liq_1m_n_events": w1.get("n_events"),
    }


def full_context(snap: dict | None = None) -> dict:
    """Merge context from all sources. Safe with missing snapshots."""
    ctx = context_from_snap(snap)
    ctx.update(context_from_microstructure())
    ctx.update(context_from_hivemind())
    ctx.update(context_from_liquidations())
    return ctx


# ----------------------------------------------------------------------
# Public log API
# ----------------------------------------------------------------------


def log_entry(*,
              entry_path: str,
              structure: str,
              side: str | None,
              qty: float | None,
              entry_price_intended: float | None,
              snap: dict | None,
              gates_trace: dict | None = None,
              gates_size_multiplier: float = 1.0,
              rationale: str = "",
              confidence: str | None = None,
              extra: dict | None = None) -> str:
    """Log an entry decision (action=open). Returns the decision_id so
    callers can correlate downstream events.

    `gates_trace` should map gate_name → "PASS" / rule_name / "REDUCED:0.7"
    so the counterfactual analyzer can reconstruct the full decision."""
    decision_id = new_decision_id()
    record = {
        "ts_utc": now_iso(),
        "kind": "entry",
        "decision_id": decision_id,
        "entry_path": entry_path,
        "structure": structure,
        "side": side,
        "qty": qty,
        "entry_price_intended": entry_price_intended,
        "rationale": rationale[:240] if rationale else "",
        "confidence": confidence,
        "gates_trace": gates_trace or {},
        "gates_size_multiplier": round(float(gates_size_multiplier), 3),
        "ctx": full_context(snap),
    }
    if extra:
        record["extra"] = extra
    _write("entry", record)
    return decision_id


def log_block(*,
              would_be_entry_path: str,
              would_be_structure: str,
              blocking_gate: str,
              blocking_rule: str,
              reason: str,
              snap: dict | None,
              extra: dict | None = None) -> str:
    """Log a gate-blocked attempt. These are GOLD for counterfactual
    backtesting: 30 days from now we can replay 'would this blocked
    trade have won?' and tune the gate accordingly."""
    decision_id = new_decision_id()
    record = {
        "ts_utc": now_iso(),
        "kind": "block",
        "decision_id": decision_id,
        "would_be_entry_path": would_be_entry_path,
        "would_be_structure": would_be_structure,
        "blocking_gate": blocking_gate,
        "blocking_rule": blocking_rule,
        "reason": reason[:240],
        "ctx": full_context(snap),
    }
    if extra:
        record["extra"] = extra
    _write("block", record)
    return decision_id


def log_exit(*,
             exit_path: str,
             exit_reason: str,
             position_id: str | None,
             symbol: str | None,
             side: str | None,
             realized_pnl_usdt: float | None = None,
             realized_R: float | None = None,
             max_R: float | None = None,
             min_R: float | None = None,
             duration_seconds: float | None = None,
             ladder_path: list | None = None,
             exit_price: float | None = None,
             linked_entry_decision_id: str | None = None,
             extra: dict | None = None) -> str:
    """Log an exit decision/fill. Should be called BOTH when the
    decision_engine schedules a CLOSE (intent) AND when execution
    confirms (fill) — caller distinguishes via `extra.stage`."""
    decision_id = new_decision_id()
    outcome = None
    if isinstance(realized_pnl_usdt, (int, float)):
        outcome = "win" if realized_pnl_usdt > 0 else ("loss" if realized_pnl_usdt < 0 else "scratch")
    record = {
        "ts_utc": now_iso(),
        "kind": "exit",
        "decision_id": decision_id,
        "linked_entry_decision_id": linked_entry_decision_id,
        "position_id": position_id,
        "symbol": symbol,
        "side": side,
        "exit_path": exit_path,
        "exit_reason": exit_reason[:240] if exit_reason else "",
        "realized_pnl_usdt": realized_pnl_usdt,
        "realized_R": realized_R,
        "max_R": max_R,
        "min_R": min_R,
        "duration_seconds": duration_seconds,
        "ladder_path": ladder_path or [],
        "exit_price": exit_price,
        "outcome": outcome,
        "ctx": full_context(None),  # context AT EXIT TIME
    }
    if extra:
        record["extra"] = extra
    _write("exit", record)
    return decision_id
