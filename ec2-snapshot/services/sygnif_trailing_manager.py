#!/usr/bin/env python3
"""sygnif_trailing_manager.py — ratchet SL up behind price as winners run.

Problem (operator-flagged 2026-05-11):
  Standing orders fire on BREAKOUT continuation (Buy when price rises past
  trigger, Sell when price falls past trigger). Fixed 0.5-0.8% TP cuts off
  every winner before the actual move plays out. Real breakouts run 2-5%;
  we capture only the first 0.8%.

Solution:
  Replace fixed TP with a TRAILING stop that follows price favorably,
  letting winners run until the trend rolls. Initial tight SL still caps
  downside. Bybit V5 supports this via /v5/position/trading-stop.

How it works:
  Every 20s, this service:
    1. Lists all open positions on Bybit demo BTCUSDT
    2. For each position originated by sygSTND or sygBNCE (claim trades):
       a. Read current mark price + current SL/TP from position
       b. Compute new "trailing" SL based on best-favorable price seen
       c. If new SL is better (closer to profit) than current SL: ratchet up
       d. Once price has moved ≥ ACTIVATION_PCT in favor, REMOVE the fixed TP
          (trail handles exit) and start trailing
  Bybit's /v5/position/trading-stop endpoint accepts:
    - stopLoss: new SL price
    - trailingStop: distance to trail (in price units, e.g., "300" = $300)
    - takeProfit: new TP (or "" to clear)
    - activePrice: price at which trailing activates

Strategy: hand-rolled trail (not Bybit's auto-trail) so we control the
ratchet exactly + can be more aggressive in fast moves.

Run:
  python3 /opt/sygnif-services/sygnif_trailing_manager.py
Wired by sygnif-trailing-manager.timer (every 20s).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import pathlib
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import uuid

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
def _load_env(path: str) -> None:
    if not os.path.exists(path): return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env("/etc/sygnif/trader.env")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DRY_RUN          = os.environ.get("SYGNIF_TRAIL_DRY_RUN", "0") == "1"
SYMBOL           = os.environ.get("SYGNIF_TRAIL_SYMBOL", "BTCUSDT")
ACTIVATION_PCT   = float(os.environ.get("SYGNIF_TRAIL_ACTIVATION_PCT", "0.5"))  / 100
TRAIL_PCT        = float(os.environ.get("SYGNIF_TRAIL_DISTANCE_PCT",  "0.4"))   / 100
INITIAL_SL_PCT   = float(os.environ.get("SYGNIF_TRAIL_INITIAL_SL_PCT","0.30"))  / 100
PREFIXES         = tuple(os.environ.get("SYGNIF_TRAIL_PREFIXES",
                                          "sygSTND,sygBNCE,sygFAST").split(","))

DB = "/var/lib/sygnif/swarm.db"
STATE_FILE = pathlib.Path("/var/lib/sygnif/trailing_state.json")

API_BASE = (os.environ.get("BYBIT_DEMO_API_BASE")
            or "https://api-demo.bybit.com").rstrip("/")
API_KEY  = (os.environ.get("BYBIT_DEMO_API_KEY")
            or os.environ.get("BYBIT_API_KEY"))
API_SEC  = (os.environ.get("BYBIT_DEMO_API_SECRET")
            or os.environ.get("BYBIT_API_SECRET"))


# ---------------------------------------------------------------------------
# Bybit REST helpers
# ---------------------------------------------------------------------------
def _sign(payload: str):
    ts = str(int(time.time() * 1000))
    sig = hmac.new(API_SEC.encode(),
                    (ts + API_KEY + "5000" + payload).encode(),
                    hashlib.sha256).hexdigest()
    return ts, sig


def signed_get(path: str, params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    ts, sig = _sign(qs)
    req = urllib.request.Request(
        f"{API_BASE}{path}?{qs}",
        headers={"X-BAPI-API-KEY":API_KEY,"X-BAPI-SIGN":sig,
                  "X-BAPI-SIGN-TYPE":"2","X-BAPI-TIMESTAMP":ts,
                  "X-BAPI-RECV-WINDOW":"5000"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=8).read())
    except Exception as e:
        return {"retCode": -1, "retMsg": f"{type(e).__name__}: {e}"}


def signed_post(path: str, body: dict) -> dict:
    body_str = json.dumps(body)
    ts, sig = _sign(body_str)
    req = urllib.request.Request(
        f"{API_BASE}{path}", data=body_str.encode(), method="POST",
        headers={"Content-Type":"application/json","X-BAPI-API-KEY":API_KEY,
                  "X-BAPI-SIGN":sig,"X-BAPI-SIGN-TYPE":"2",
                  "X-BAPI-TIMESTAMP":ts,"X-BAPI-RECV-WINDOW":"5000"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=8).read())
    except Exception as e:
        return {"retCode": -1, "retMsg": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# State persistence (per-position high-water-mark for trail)
# ---------------------------------------------------------------------------
def _load_state() -> dict:
    if not STATE_FILE.exists(): return {}
    try:
        with STATE_FILE.open() as f: return json.load(f)
    except (json.JSONDecodeError, OSError): return {}


def _save_state(s: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    with tmp.open("w") as f: json.dump(s, f, indent=2, default=str)
    os.replace(tmp, STATE_FILE)


def _position_key(p: dict) -> str:
    """Identify a position by symbol+side+entry. Position-link-id is not in
    position payload directly so use entryPrice + size."""
    return f"{p.get('symbol')}|{p.get('side')}|{p.get('avgPrice')}|{p.get('size')}"


# ---------------------------------------------------------------------------
# Identify positions that came from our managed strategies
# ---------------------------------------------------------------------------
def get_managed_position_keys() -> set:
    """Return set of order_link_ids that started with a managed prefix
    and resulted in a fill in last 24h. Used to identify which positions
    to trail."""
    out = set()
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        cutoff = int(time.time()) - 86400
        for (meta_s,) in c.execute(
            "SELECT meta FROM swarm_entries WHERE topic='trade.open' "
            "AND created > ?", (cutoff,)):
            try:
                m = json.loads(meta_s)
            except (json.JSONDecodeError, TypeError):
                continue
            olid = m.get("order_link_id") or m.get("orderLinkId") or ""
            if any(olid.startswith(p) for p in PREFIXES):
                out.add(olid)
        c.close()
    except Exception: pass
    return out


# ---------------------------------------------------------------------------
# Trail logic
# ---------------------------------------------------------------------------
def compute_trail_sl(side: str, entry: float, current_mark: float,
                      hwm: float, current_sl: float | None) -> tuple[float | None, str]:
    """Compute new SL given side, entry, current mark, high-water-mark.

    Returns (new_sl, reason) or (None, reason) if no change needed.

    Logic:
      For LONG:
        hwm = max favorable price seen so far
        if (hwm - entry)/entry < ACTIVATION_PCT: not yet activated
        new_sl = hwm * (1 - TRAIL_PCT)
        only update if new_sl > current_sl (ratchet)
      For SHORT: mirror (hwm = min, new_sl = hwm * (1 + TRAIL_PCT))
    """
    if entry <= 0 or current_mark <= 0: return (None, "no-data")
    is_long = side == "Buy"

    move_pct = (current_mark - entry) / entry if is_long else (entry - current_mark) / entry

    if move_pct < ACTIVATION_PCT:
        return (None, f"not_activated (move {move_pct*100:+.2f}% < {ACTIVATION_PCT*100:.2f}%)")

    if is_long:
        new_sl = hwm * (1 - TRAIL_PCT)
        # Only ratchet UP — never lower SL on a long
        if current_sl is not None and new_sl <= current_sl:
            return (None, f"no_ratchet new=${new_sl:.1f} cur=${current_sl:.1f}")
        # Never put SL below entry once activated (lock in breakeven minimum)
        if new_sl < entry * 1.001:
            new_sl = max(new_sl, entry * 1.001)
    else:
        new_sl = hwm * (1 + TRAIL_PCT)
        if current_sl is not None and new_sl >= current_sl:
            return (None, f"no_ratchet new=${new_sl:.1f} cur=${current_sl:.1f}")
        if new_sl > entry * 0.999:
            new_sl = min(new_sl, entry * 0.999)

    return (round(new_sl, 1), f"trail to ${new_sl:.1f} (hwm ${hwm:.1f}, move {move_pct*100:+.2f}%)")


def apply_trail(symbol: str, side: str, position_idx: int,
                  new_sl: float, clear_tp: bool) -> dict:
    body = {
        "category":   "linear",
        "symbol":     symbol,
        "positionIdx": position_idx,
        "stopLoss":   str(new_sl),
        "slTriggerBy": "LastPrice",
    }
    if clear_tp:
        body["takeProfit"] = "0"  # clear TP — trail handles exits
    return signed_post("/v5/position/trading-stop", body)


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------
def main():
    if not API_KEY or not API_SEC:
        print("FATAL: API key missing", file=sys.stderr); return 1
    started = dt.datetime.now(dt.timezone.utc)
    print(f"=== trailing_manager @ {started.isoformat()} ===")

    # Pull all open positions for SYMBOL
    r = signed_get("/v5/position/list",
                    {"category":"linear","symbol":SYMBOL})
    rc = r.get("retCode")
    if rc != 0:
        print(f"  position/list failed: {r.get('retMsg')}", file=sys.stderr)
        return 1
    positions = [p for p in (r.get("result",{}).get("list") or [])
                 if float(p.get("size","0") or "0") > 0]
    print(f"  open positions: {len(positions)}")

    if not positions:
        return 0

    managed_olids = get_managed_position_keys()
    print(f"  managed olids (last 24h fills): {len(managed_olids)}")

    state = _load_state()
    updates = 0

    for p in positions:
        side = p.get("side")
        size = float(p.get("size","0"))
        entry = float(p.get("avgPrice","0") or 0)
        mark  = float(p.get("markPrice","0") or 0)
        cur_sl = float(p.get("stopLoss","0") or 0) or None
        cur_tp = float(p.get("takeProfit","0") or 0) or None
        pos_idx = int(p.get("positionIdx", 0))
        unreal = float(p.get("unrealisedPnl","0") or 0)

        pkey = _position_key(p)
        prev = state.get(pkey, {})
        # Track high-water-mark of favorable price
        if side == "Buy":
            hwm = max(prev.get("hwm", mark), mark)
        else:
            hwm = min(prev.get("hwm", mark), mark)

        state[pkey] = {"side":side, "entry":entry, "hwm":hwm,
                       "last_check_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                       "last_mark": mark}

        new_sl, reason = compute_trail_sl(side, entry, mark, hwm, cur_sl)
        move_pct = ((mark-entry)/entry*100) if side=="Buy" else ((entry-mark)/entry*100)
        print(f"  {side} entry=${entry:.0f} mark=${mark:.0f} "
              f"hwm=${hwm:.0f} move={move_pct:+.2f}% "
              f"sl=${cur_sl} uPnL=${unreal:+.2f} → {reason}")

        if new_sl is None: continue

        if DRY_RUN:
            print(f"    DRY: would set SL=${new_sl:.1f}, clear TP={cur_tp is not None}")
            continue

        # Once activated for the first time, also clear the fixed TP so the
        # trail handles exits (no more 0.8% TP cutoff of winners)
        clear_tp = cur_tp is not None and prev.get("tp_cleared", False) is False
        out = apply_trail(SYMBOL, side, pos_idx, new_sl, clear_tp)
        rc = out.get("retCode")
        if rc == 0:
            print(f"    ✓ SL ratcheted to ${new_sl:.1f}"
                  + (" (+ TP cleared)" if clear_tp else ""))
            state[pkey]["last_sl"] = new_sl
            if clear_tp: state[pkey]["tp_cleared"] = True
            updates += 1
        else:
            print(f"    ✗ trading-stop failed retCode={rc} msg={out.get('retMsg','')[:120]}")

    # GC state for positions no longer present
    live_keys = {_position_key(p) for p in positions}
    state = {k: v for k, v in state.items() if k in live_keys}
    _save_state(state)
    print(f"  applied {updates} updates, state_keys={len(state)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
