#!/usr/bin/env python3
"""
**Bitcoin Truthcoin / Hivemind** as an optional **Swarm processing core**.

When ``SYGNIF_SWARM_CORE_ENGINE=hivemind`` and the Truthcoin CLI snapshot is **reachable**,
``compute_swarm()`` drives ``swarm_mean`` / ``swarm_label`` / ``swarm_conflict`` from the Hivemind
signal only (file + Bybit votes remain in ``sources`` for audit). When the node is down, Swarm
falls back to the usual Python mean over all sources (including optional ``hm``).

**Full root access** (operator visibility, read-only): ``SYGNIF_SWARM_FULL_ROOT_ACCESS=1`` adds
``swarm_processing_roots`` and ``swarm_host_root_manifest`` top-level entries — first-level names
under ``/`` and ``$HOME`` (capped). This does **not** run Python as UNIX ``root``; use container
capabilities if you need privileged ports.

Env (core):

- ``SYGNIF_SWARM_CORE_ENGINE`` — ``python`` (default) or ``hivemind``.
- ``SYGNIF_SWARM_HIVEMIND_VOTE`` — ``1`` appends ``sources.hm`` even when core is ``python``.
- ``SYGNIF_SWARM_HM_VOTE_MIN_VOTING_SLOTS`` — minimum ``slots_voting_n`` for ``hm`` vote ``+1`` (default ``1``).
- ``SYGNIF_TRUTHCOIN_DC_ROOT`` — Truthcoin repo root (default ``~/truthcoin-dc``).

Env (gate — see ``swarm_order_gate``): ``SWARM_ORDER_REQUIRE_HIVEMIND_VOTE``,
``SWARM_ORDER_HIVEMIND_VOTE_FLAT_PASS``.

Env (Bybit ↔ Hivemind explore enrichment, read-only public ticker):

- ``SYGNIF_SWARM_HIVEMIND_BYBIT_PUBLIC_TICKER`` — unset defaults **on** when Truthcoin / Hivemind vote / ``hivemind``
  core is active; set ``0`` to disable the extra ``GET /v5/market/tickers`` when ``SYGNIF_SWARM_BYBIT_MAINNET`` is off.
- ``SYGNIF_SWARM_HIVEMIND_BYBIT_VOTE_FALLBACK`` — unset defaults **on**: when Truthcoin has no voting slots (or is
  unreachable but ``bybit_reference`` was merged), map ``price24hPcnt`` to ``hm`` vote using
  ``SYGNIF_SWARM_HIVEMIND_BYBIT_PCT_THR`` (percent, default ``0.25``).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def swarm_core_engine() -> str:
    return (os.environ.get("SYGNIF_SWARM_CORE_ENGINE") or "python").strip().lower()


def sygnif_repo_root() -> Path:
    raw = (os.environ.get("SYGNIF_REPO_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def processing_roots() -> list[Path]:
    """Configured processing trees (SYGNIF + Truthcoin + optional colon list)."""
    from finance_agent.truthcoin_dc_swarm_bridge import truthcoin_dc_repo_root

    seen: set[Path] = set()
    roots: list[Path] = []
    for p in (sygnif_repo_root(), truthcoin_dc_repo_root()):
        p = p.resolve()
        if p.is_dir() and p not in seen:
            seen.add(p)
            roots.append(p)
    raw = (os.environ.get("SYGNIF_SWARM_PROCESSING_ROOTS") or "").strip()
    if raw:
        for part in raw.split(":"):
            part = part.strip()
            if not part:
                continue
            p = Path(part).expanduser().resolve()
            if p.is_dir() and p not in seen:
                seen.add(p)
                roots.append(p)
    return roots


def hivemind_explore_needed() -> bool:
    """Whether to call the Truthcoin CLI snapshot this tick."""
    if _env_truthy("SYGNIF_SWARM_TRUTHCOIN_DC"):
        return True
    if _env_truthy("SYGNIF_SWARM_HIVEMIND_VOTE"):
        return True
    return swarm_core_engine() == "hivemind"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _parse_float(x: Any) -> float | None:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def slim_bybit_row_for_hivemind(row: dict[str, Any]) -> dict[str, Any]:
    """Non-secret subset of Bybit v5 ``/market/tickers`` row for ``hivemind_explore`` JSON."""
    keys = (
        "symbol",
        "lastPrice",
        "markPrice",
        "indexPrice",
        "prevPrice24h",
        "price24hPcnt",
        "highPrice24h",
        "lowPrice24h",
        "volume24h",
        "turnover24h",
        "fundingRate",
        "openInterestValue",
        "bid1Price",
        "bid1Size",
        "ask1Price",
        "ask1Size",
    )
    out: dict[str, Any] = {k: row[k] for k in keys if k in row and row[k] is not None}
    bp = _parse_float(out.get("bid1Price"))
    ap = _parse_float(out.get("ask1Price"))
    mp = _parse_float(out.get("markPrice")) or _parse_float(out.get("lastPrice"))
    if bp and ap and ap >= bp and mp and mp > 0:
        mid = 0.5 * (bp + ap)
        out["spread_bps"] = round((ap - bp) / mid * 10000.0, 3)
    return out


def truthcoin_structural_consensus(doc: dict[str, Any]) -> dict[str, Any]:
    """Best-effort Truthcoin fields for operator dashboards (no extra RPC)."""
    out: dict[str, Any] = {
        "slots_voting_n": int(doc.get("slots_voting_n") or 0),
        "markets_trading_n": int(doc.get("markets_trading_n") or 0),
    }
    ss = doc.get("slot_status")
    if isinstance(ss, dict):
        out["slot_status_keys"] = sorted(ss.keys())[:40]
        for k in ("voting", "Voting", "in_voting", "active_voting_count", "ActiveVoting"):
            if k in ss:
                v = ss[k]
                if isinstance(v, (int, float)):
                    out["status_scalar"] = int(v)
                else:
                    out["status_scalar"] = str(v)[:120]
                break
    sv = doc.get("slots_voting")
    if isinstance(sv, list) and sv and isinstance(sv[0], dict):
        out["first_slot_keys"] = sorted(sv[0].keys())[:24]
    return out


def _vote_int_from_bybit_reference(br: dict[str, Any], *, thr_pct: float) -> tuple[int, str]:
    """Map slim Bybit row → {-1,0,+1} using 24h %% (same convention as ``vote_bybit_mainnet_from_row``)."""
    pfrac = _parse_float(br.get("price24hPcnt"))
    if pfrac is None:
        return 0, "no_pct"
    pct = pfrac * 100.0
    t = max(0.01, float(thr_pct))
    lp = _parse_float(br.get("lastPrice")) or 0.0
    fr = (_parse_float(br.get("fundingRate")) or 0.0) * 100.0
    if pct >= t:
        v = 1
    elif pct <= -t:
        v = -1
    else:
        v = 0
    detail = f"24h{pct:+.2f}%|px{lp:.0f}|f{fr:.4f}%"
    return v, detail[:100]


def _hivemind_bybit_vote_fallback_enabled() -> bool:
    raw = os.environ.get("SYGNIF_SWARM_HIVEMIND_BYBIT_VOTE_FALLBACK", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def _hivemind_bybit_public_ticker_wanted() -> bool:
    raw = os.environ.get("SYGNIF_SWARM_HIVEMIND_BYBIT_PUBLIC_TICKER", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return (
        _env_truthy("SYGNIF_SWARM_TRUTHCOIN_DC")
        or _env_truthy("SYGNIF_SWARM_HIVEMIND_VOTE")
        or swarm_core_engine() == "hivemind"
    )


def merge_bybit_market_into_hivemind_explore(
    doc: dict[str, Any] | None,
    ticker_row: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """
    Mutate ``hivemind_explore`` dict in place: attach ``bybit_reference`` + ``consensus_summary``.

    Safe when ``doc`` is minimal (e.g. Truthcoin disabled) — still attaches Bybit context for audits.
    """
    if not isinstance(doc, dict) or not ticker_row:
        return doc
    doc["bybit_reference"] = slim_bybit_row_for_hivemind(ticker_row)
    tc = truthcoin_structural_consensus(doc)
    br = doc.get("bybit_reference") or {}
    pfrac = _parse_float(br.get("price24hPcnt"))
    pct100 = (pfrac * 100.0) if pfrac is not None else None
    thr = _env_float("SYGNIF_SWARM_HIVEMIND_BYBIT_PCT_THR", 0.25)
    v_hint, _ = _vote_int_from_bybit_reference(br, thr_pct=thr)
    doc["consensus_summary"] = {
        **tc,
        "bybit_24h_pct": None if pct100 is None else round(pct100, 4),
        "bybit_direction_vote_hint": v_hint,
        "spread_bps": br.get("spread_bps"),
        "volume24h": br.get("volume24h"),
        "turnover24h": br.get("turnover24h"),
        "open_interest_value": br.get("openInterestValue"),
    }
    return doc


def vote_hivemind_from_explore(doc: dict[str, Any]) -> tuple[int, str]:
    """
    Map ``hivemind_explore_snapshot()`` (+ optional ``bybit_reference``) → Swarm vote in ``{-1, 0, +1}``.

    Priority: Truthcoin voting slots (liveness) → optional Bybit 24h consensus when slots quiet / node down.
    """
    br = doc.get("bybit_reference") if isinstance(doc.get("bybit_reference"), dict) else None
    fallback_on = _hivemind_bybit_vote_fallback_enabled()
    thr_pct = _env_float("SYGNIF_SWARM_HIVEMIND_BYBIT_PCT_THR", 0.25)

    if not doc.get("ok"):
        if fallback_on and br:
            v_b, d_b = _vote_int_from_bybit_reference(br, thr_pct=thr_pct)
            if v_b != 0:
                return v_b, f"hivemind_unreachable_bybit_{d_b}"
        return 0, "hivemind_unreachable"
    try:
        thr = int(os.environ.get("SYGNIF_SWARM_HM_VOTE_MIN_VOTING_SLOTS", "1") or 1)
    except ValueError:
        thr = 1
    n = int(doc.get("slots_voting_n") or 0)
    nm = int(doc.get("markets_trading_n") or 0)
    if n >= thr:
        return 1, f"hivemind_active_slots_voting={n}_markets_trading={nm}"
    if fallback_on and br:
        v_b, d_b = _vote_int_from_bybit_reference(br, thr_pct=thr_pct)
        if v_b != 0:
            return v_b, f"hivemind_truthcoin_quiet_bybit_{d_b}"
    return 0, f"hivemind_quiet_slots_voting={n}_markets_trading={nm}"


def build_processing_roots_manifest() -> dict[str, Any] | None:
    if not _env_truthy("SYGNIF_SWARM_FULL_ROOT_ACCESS"):
        return None
    from finance_agent.truthcoin_dc_swarm_bridge import truthcoin_dc_repo_root

    roots_paths = processing_roots()
    roots = [str(p) for p in roots_paths]
    manifest: dict[str, Any] = {
        "sygnif_repo": str(sygnif_repo_root()),
        "truthcoin_dc_root": str(truthcoin_dc_repo_root()),
        "processing_roots": roots,
    }
    try:
        root_entries = sorted(os.listdir("/"))[:200]
        manifest["host_root_entries"] = root_entries
    except OSError as exc:
        manifest["host_root_error"] = str(exc)
    try:
        home = Path.home()
        manifest["home_entries"] = sorted(os.listdir(home))[:200]
    except OSError as exc:
        manifest["home_entries_error"] = str(exc)
    return manifest
