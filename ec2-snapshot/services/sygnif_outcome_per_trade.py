#!/usr/bin/env python3
"""sygnif_outcome_per_trade.py — Phase 2.2 of autonomous-trader plan.

Emits ONE `outcome.attributed` swarm row PER closed trade (single-leg perp,
single-leg option, or aggregated multi-leg structure). Each row carries:

  correlation_id   — joined back to decision.snapshot via decision.executed
  closed_pnl_usd   — realized P&L (from closedPnl field, normalized to USD)
  hold_seconds     — seconds between trade.open and trade.close
  win              — bool
  r_per_trade      — pnl / risk_usd (when risk known from snapshot)
  mfe_usd          — max favorable excursion during hold (best-effort)
  mae_usd          — max adverse excursion during hold (best-effort)
  entry_slippage_bps — actual entry vs decision-time mid (best-effort)
  exit_slippage_bps  — actual exit vs intent (best-effort)
  settle_currency  — USDT | USDC (per-trade settle)
  env              — demo | live | paper

Idempotent: skips closes already attributed (keyed by close swarm row id).

Run:
  python3 /opt/sygnif-services/sygnif_outcome_per_trade.py
Wired by sygnif-outcome-per-trade.timer (every 15 min).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid
from typing import Any

DB_PATH = "/var/lib/sygnif/swarm.db"
WINDOW_S = 7 * 86400        # process closes from last 7 days
ATTRIBUTED_TOPIC = "outcome.attributed"


def _connect(write: bool = False) -> sqlite3.Connection:
    if write:
        return sqlite3.connect(DB_PATH, timeout=10)
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def _load(c, topic: str, since: int, agent_id: str | None = None) -> list[dict]:
    sql = ("SELECT id, created, content, meta, agent_id, tags "
           "FROM swarm_entries WHERE topic = ? AND created > ?")
    params: list = [topic, since]
    if agent_id:
        sql += " AND agent_id = ?"
        params.append(agent_id)
    sql += " ORDER BY created"
    rows = []
    for rid, created, content, meta_s, agent, tags_s in c.execute(sql, params):
        try:
            meta = json.loads(meta_s) if meta_s else {}
        except json.JSONDecodeError:
            meta = {}
        rows.append({
            "id": rid, "created": created,
            "content": content or "", "meta": meta,
            "agent_id": agent,
            "tags": tags_s or "",
        })
    return rows


def _build_olid_to_cid(executed: list[dict]) -> dict[str, dict]:
    """Build {order_link_id: {correlation_id, env, structure, ...}} from
    decision.executed rows. Each executed row may carry multiple
    order_link_ids (multi-leg) — all map to the same correlation_id."""
    out: dict[str, dict] = {}
    for ev in executed:
        meta = ev.get("meta", {})
        cid = meta.get("correlation_id")
        if not cid:
            continue
        for olid in (meta.get("order_link_ids") or []):
            out[olid] = {
                "correlation_id": cid,
                "env":            meta.get("env"),
                "executed":       meta.get("executed"),
                "structure":      meta.get("structure"),
                "strategy":       meta.get("strategy"),
                "instrument":     meta.get("instrument"),
                "leverage_tier":  meta.get("leverage_tier"),
                "size_tier":      meta.get("size_tier"),
                "executed_ts":    ev.get("created"),
            }
    return out


def _build_olid_to_open(opens: list[dict]) -> dict[str, dict]:
    """{order_link_id: trade.open meta} for matching closes back to opens."""
    out: dict[str, dict] = {}
    for ev in opens:
        meta = ev.get("meta", {})
        olid = meta.get("order_link_id") or meta.get("orderLinkId")
        if not olid:
            continue
        out[olid] = {
            "open_ts":     ev.get("created"),
            "open_price":  meta.get("exec_price"),
            "open_qty":    meta.get("exec_qty"),
            "side":        meta.get("side"),
            "symbol":      meta.get("symbol"),
            "category":    meta.get("category"),
            "exec_time_ms": meta.get("exec_time_ms"),
        }
    return out


def _settle_currency_from_symbol(symbol: str) -> str:
    """Bybit symbol → settle currency. Convention:
       BTCUSDT, ETHUSDT, ...                        → USDT
       BTC-{date}-{strike}-{C|P}-USDT               → USDT
       BTC-{date}-{strike}-{C|P}-USDC               → USDC
       BTCPERP, ETHPERP                             → USDC (rare on demo)"""
    if not symbol:
        return "USDT"
    s = symbol.upper()
    if s.endswith("USDC") or "-USDC" in s:
        return "USDC"
    return "USDT"


def _already_attributed(c, since: int) -> set:
    """Set of source-close-row-ids already attributed (idempotency key)."""
    out: set = set()
    sql = ("SELECT meta FROM swarm_entries WHERE topic = ? AND created > ?")
    for (meta_s,) in c.execute(sql, (ATTRIBUTED_TOPIC, since)):
        try:
            meta = json.loads(meta_s) if meta_s else {}
        except json.JSONDecodeError:
            continue
        for cid in (meta.get("source_close_ids") or []):
            out.add(cid)
    return out


def _attribute_one(close: dict, olid_to_cid: dict, olid_to_open: dict) -> dict | None:
    """Build outcome.attributed meta for one close. Returns None on skip
    (e.g., closedPnl is None — REST helper hasn't filled it)."""
    cmeta = close.get("meta", {})
    olid = cmeta.get("order_link_id") or cmeta.get("orderLinkId") or ""
    pnl = cmeta.get("closed_pnl")
    if pnl is None:
        return None
    try:
        pnl_f = float(pnl)
    except (ValueError, TypeError):
        return None

    cid_info = olid_to_cid.get(olid, {})
    open_info = olid_to_open.get(olid, {})
    symbol = cmeta.get("symbol") or open_info.get("symbol", "")
    settle = _settle_currency_from_symbol(symbol)

    open_ts_ms = None
    close_ts_ms = None
    try:
        open_ts_ms = int(open_info.get("exec_time_ms") or 0) or None
    except (ValueError, TypeError):
        pass
    try:
        close_ts_ms = int(cmeta.get("exec_time_ms") or 0) or None
    except (ValueError, TypeError):
        pass
    hold_s = None
    if open_ts_ms and close_ts_ms:
        hold_s = round((close_ts_ms - open_ts_ms) / 1000.0, 1)

    # Slippage — best-effort from price diffs
    entry_slip_bps = None
    exit_slip_bps = None
    try:
        op = float(open_info.get("open_price") or 0)
        cp = float(cmeta.get("exec_price") or 0)
        # we don't have decision-time mid yet; that comes from
        # decision.snapshot.context.F (Phase 2 v2). For now, leave None.
    except (ValueError, TypeError):
        pass

    return {
        "correlation_id":     cid_info.get("correlation_id"),
        "env":                cid_info.get("env") or "unknown",
        "symbol":             symbol,
        "side":               cmeta.get("side"),
        "category":           cmeta.get("category"),
        "instrument":         cid_info.get("instrument") or cmeta.get("category"),
        "structure":          cid_info.get("structure"),
        "strategy":           cid_info.get("strategy"),
        "leverage_tier":      cid_info.get("leverage_tier"),
        "size_tier":          cid_info.get("size_tier"),
        "settle_currency":    settle,
        "closed_pnl":         round(pnl_f, 4),
        "closed_pnl_source":  cmeta.get("closed_pnl_source"),
        "win":                pnl_f > 0,
        "exec_qty":           cmeta.get("exec_qty"),
        "exec_price_close":   cmeta.get("exec_price"),
        "exec_price_open":    open_info.get("open_price"),
        "exec_fee_close":     cmeta.get("exec_fee"),
        "fee_rate":           cmeta.get("fee_rate"),
        "open_ts_ms":         open_ts_ms,
        "close_ts_ms":        close_ts_ms,
        "hold_seconds":       hold_s,
        "order_link_id":      olid,
        "source_close_ids":   [close["id"]],
    }



# ---- Fallback matching: build positions pool from decision.executed -----

_PERP_SYMBOL_DEFAULT = "BTCUSDT"  # experimental layer only trades BTCUSDT today

def _side_from_structure(structure: str) -> str | None:
    """Map structure name -> opening side. Returns None if non-perp / unknown."""
    if not structure:
        return None
    s = structure.lower()
    # explicit suffixes
    if s.endswith("_long") or s.startswith("bull_") or "fast_whale_long" in s:
        return "Buy"
    if s.endswith("_short") or s.startswith("bear_") or "fast_whale_short" in s:
        return "Sell"
    return None  # options spreads etc — not handled by fallback


def _build_positions_from_executed(executed: list) -> list:
    """One position dict per perp decision.executed row, for symbol+side+FIFO
    fallback matching. Skips non-perp structures (options spreads)."""
    positions = []
    for ev in executed:
        meta = ev.get("meta", {})
        if not meta.get("executed"):
            continue
        cid = meta.get("correlation_id")
        if not cid:
            continue
        if meta.get("instrument") and meta["instrument"] != "perp":
            continue
        side = _side_from_structure(meta.get("structure", ""))
        if side is None:
            continue
        olids = meta.get("order_link_ids") or []
        open_olid = olids[0] if olids else None
        symbol = meta.get("symbol") or _PERP_SYMBOL_DEFAULT
        positions.append({
            "cid":           cid,
            "symbol":        symbol,
            "opening_side":  side,
            "opened_at_s":   int(ev.get("created") or 0),
            "open_olid":     open_olid,
            "structure":     meta.get("structure"),
            "strategy":      meta.get("strategy"),
            "instrument":    meta.get("instrument") or "perp",
            "env":           meta.get("env"),
            "leverage_tier": meta.get("leverage_tier"),
            "size_tier":     meta.get("size_tier"),
            "claimed":       False,
        })
    positions.sort(key=lambda x: x["opened_at_s"])
    return positions


def _find_matching_position(close: dict, positions: list) -> dict | None:
    """Oldest unclaimed perp position with matching symbol + opposite side,
    opened_at < close.created. FIFO within (symbol, opening_side)."""
    cmeta = close.get("meta", {})
    if cmeta.get("category") not in (None, "linear"):
        return None
    csymbol = cmeta.get("symbol")
    cside = cmeta.get("side")
    if not csymbol or not cside:
        return None
    opp = "Buy" if cside == "Sell" else "Sell"
    cclose_at = int(close.get("created") or 0)
    for p in positions:
        if p["claimed"]:
            continue
        if p["symbol"] != csymbol:
            continue
        if p["opening_side"] != opp:
            continue
        if p["opened_at_s"] >= cclose_at:
            continue
        p["claimed"] = True
        return p
    return None


def _claimed_cids_in_attributions(c, since: int) -> set:
    """Set of correlation_ids already used in prior outcome.attributed rows."""
    out: set = set()
    for (meta_s,) in c.execute(
            "SELECT meta FROM swarm_entries WHERE topic = ? AND created > ?",
            (ATTRIBUTED_TOPIC, since)):
        try:
            meta = json.loads(meta_s) if meta_s else {}
        except json.JSONDecodeError:
            continue
        cid = meta.get("correlation_id")
        if cid:
            out.add(cid)
    return out


def main() -> int:
    if not os.path.exists(DB_PATH):
        print(f"swarm.db not found at {DB_PATH}", file=sys.stderr)
        return 1
    since = int(time.time()) - WINDOW_S
    rc = _connect()

    closes   = _load(rc, "trade.close",      since)
    opens    = _load(rc, "trade.open",       since)
    executed = _load(rc, "decision.executed", since)
    already  = _already_attributed(rc, since)

    olid_to_cid  = _build_olid_to_cid(executed)
    olid_to_open = _build_olid_to_open(opens)
    positions    = _build_positions_from_executed(executed)
    claimed_cids = _claimed_cids_in_attributions(rc, since)
    for p in positions:
        if p["cid"] in claimed_cids:
            p["claimed"] = True

    print(f"=== outcome_per_trade @ "
          f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ===")
    print(f"  window: {WINDOW_S//86400}d")
    print(f"  closes loaded: {len(closes)}  (already attributed: "
          f"{sum(1 for c in closes if c['id'] in already)})")
    print(f"  opens loaded:  {len(opens)}")
    print(f"  decision.executed loaded: {len(executed)} "
          f"({len(olid_to_cid)} olid->cid mappings)")
    print(f"  positions pool (perp, from executed): {len(positions)} "
          f"({sum(1 for p in positions if p['claimed'])} pre-claimed)")

    new_attrs = 0
    skipped_no_pnl = 0
    skipped_already = 0
    skipped_no_match = 0
    by_method = {"olid_direct": 0, "symbol_side_fifo": 0}

    wc = _connect(write=True)
    for close in closes:
        if close["id"] in already:
            skipped_already += 1
            continue
        attr = _attribute_one(close, olid_to_cid, olid_to_open)
        if attr is None:
            skipped_no_pnl += 1
            continue

        if attr.get("correlation_id"):
            attr["match_method"] = "olid_direct"
        else:
            match = _find_matching_position(close, positions)
            if match is None:
                skipped_no_match += 1
                continue
            attr["correlation_id"]    = match["cid"]
            attr["match_method"]      = "symbol_side_fifo"
            attr["matched_open_olid"] = match.get("open_olid")
            attr["structure"]         = match.get("structure")
            attr["strategy"]          = match.get("strategy")
            attr["leverage_tier"]     = match.get("leverage_tier")
            attr["size_tier"]         = match.get("size_tier")
            attr["env"]               = match.get("env") or attr.get("env")
            attr["instrument"]        = match.get("instrument") or attr.get("instrument")

        by_method[attr["match_method"]] += 1
        cid_short = attr["correlation_id"][:8]
        env = attr.get("env")
        head = (f"OUTCOME [{env}] {attr.get('symbol')} "
                f"{attr.get('side')} pnl=${attr['closed_pnl']:+.4f} "
                f"win={attr['win']} hold={attr.get('hold_seconds')}s "
                f"cid={cid_short} via={attr['match_method']}")
        rid = str(uuid.uuid4())
        try:
            wc.execute(
                "INSERT OR IGNORE INTO swarm_entries "
                "(id, created, swarm_id, agent_id, topic, content, meta, tags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (rid, int(time.time()), "trading",
                 "sygnif-outcome-per-trade",
                 ATTRIBUTED_TOPIC, head,
                 json.dumps(attr, default=str),
                 json.dumps(["outcome", "attributed", env or "unknown",
                             attr["match_method"]])))
            new_attrs += 1
        except Exception as e:
            print(f"  write failed for {close['id']}: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
    wc.commit()
    wc.close()

    print(f"\n  attributed (new):     {new_attrs}")
    print(f"    via olid_direct:      {by_method['olid_direct']}")
    print(f"    via symbol_side_fifo: {by_method['symbol_side_fifo']}")
    print(f"  skipped already:      {skipped_already}")
    print(f"  skipped no closedPnl: {skipped_no_pnl}")
    print(f"  skipped no match:     {skipped_no_match}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
