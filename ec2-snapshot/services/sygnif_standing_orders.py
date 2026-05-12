#!/usr/bin/env python3
"""sygnif_standing_orders.py — always-positioned breakout ladder.

Maintains a set of pending STOP-MARKET orders above and below current
price so the trader is positioned to catch volatility events without
having to react in real-time.

Strategy:
  Every cycle (5 min):
    1. Cancel ALL prior sygSTND-* orders that are still PENDING
    2. Read current BTC mid + recent ATR
    3. Place 4 NEW stop-market orders:
       - long  stop-buy at mid + LONG_NEAR_PCT  (e.g., +0.5%)
       - long  stop-buy at mid + LONG_FAR_PCT   (e.g., +1.2%)
       - short stop-sell at mid - SHORT_NEAR_PCT (e.g., -0.5%)
       - short stop-sell at mid - SHORT_FAR_PCT  (e.g., -1.2%)
    4. Each carries TP/SL bracket (0.4% / 0.25%) so it auto-manages
       on fill

When BTC pops, ONE side fills (the trigger that's crossed). The other
side's orders just sit unfilled, get cancelled on next cycle, replaced
with new levels relative to the new mid.

Risk: 4 pending orders × $5 each = $20 theoretical max if all filled
simultaneously (basically impossible — only adjacent triggers fire).

Safety:
  - DEMO-only (refuses live)
  - Per-cycle order_link_id prefix = "sygSTND" + cycle_id[:10]
  - On startup AND every cycle: cancel all sygSTND-* pending orders
  - Skips if open_count >= MAX_TOTAL_OPEN (default 5)

Run:
  python3 /opt/sygnif-services/sygnif_standing_orders.py
  python3 /opt/sygnif-services/sygnif_standing_orders.py --dry-run
Wired by sygnif-standing-orders.timer (every 5 min).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
def _load_env(path):
    if not os.path.exists(path): return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env("/etc/sygnif/trader.env")
_load_env("/etc/sygnif/standing-orders.env")

if (os.environ.get("SYGNIF_ORDERS_MODE") or "").lower() == "live":
    if os.environ.get("SYGNIF_STANDING_LIVE_OK", "0") != "1":
        print("REFUSING: live mode but standing orders are demo-only",
              file=sys.stderr)
        sys.exit(2)
os.environ["SYGNIF_ORDERS_MODE"] = "demo"

sys.path.insert(0, "/home/ubuntu/sygnif-agent-mirror")
try:
    import sygnif_neurons as N
    from agent import decision_snapshot as DS
except Exception as e:
    print(f"FATAL: import: {e}", file=sys.stderr); sys.exit(1)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DRY_RUN          = os.environ.get("SYGNIF_STANDING_DRY_RUN", "0") == "1"
SYMBOL           = "BTCUSDT"
LONG_NEAR_PCT    = float(os.environ.get("SYGNIF_STANDING_LONG_NEAR_PCT", "0.5"))   / 100
LONG_FAR_PCT     = float(os.environ.get("SYGNIF_STANDING_LONG_FAR_PCT",  "1.2"))   / 100
SHORT_NEAR_PCT   = float(os.environ.get("SYGNIF_STANDING_SHORT_NEAR_PCT", "0.5"))  / 100
SHORT_FAR_PCT    = float(os.environ.get("SYGNIF_STANDING_SHORT_FAR_PCT",  "1.2"))  / 100
RISK_USD         = float(os.environ.get("SYGNIF_STANDING_RISK_USD", "5"))
TP_PCT           = float(os.environ.get("SYGNIF_STANDING_TP_PCT", "0.4")) / 100
SL_PCT           = float(os.environ.get("SYGNIF_STANDING_SL_PCT", "0.25")) / 100
LEVERAGE         = int(os.environ.get("SYGNIF_STANDING_LEVERAGE", "10"))
MAX_TOTAL_OPEN   = int(os.environ.get("SYGNIF_STANDING_MAX_TOTAL_OPEN", "5"))
ORDER_PREFIX     = "sygSTND"

API_BASE = (os.environ.get("BYBIT_DEMO_API_BASE")
            or "https://api-demo.bybit.com").rstrip("/")
API_KEY  = (os.environ.get("BYBIT_DEMO_API_KEY")
            or os.environ.get("BYBIT_API_KEY"))
API_SEC  = (os.environ.get("BYBIT_DEMO_API_SECRET")
            or os.environ.get("BYBIT_API_SECRET"))


# ---------------------------------------------------------------------------
# Bybit V5 helpers — direct REST since the perp order helper doesn't expose
# triggerPrice for stop-market entries
# ---------------------------------------------------------------------------
def _sign(payload: str) -> tuple[str, str]:
    ts = str(int(time.time() * 1000))
    sig = hmac.new(API_SEC.encode(),
                    (ts + API_KEY + "5000" + payload).encode(),
                    hashlib.sha256).hexdigest()
    return ts, sig


def signed_post(path: str, body: dict) -> dict:
    body_str = json.dumps(body)
    ts, sig = _sign(body_str)
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=body_str.encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-BAPI-API-KEY": API_KEY,
            "X-BAPI-SIGN": sig, "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": "5000",
        })
    try:
        return json.loads(urllib.request.urlopen(req, timeout=8).read())
    except Exception as e:
        return {"retCode": -1, "retMsg": f"{type(e).__name__}: {e}"}


def signed_get(path: str, params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    ts, sig = _sign(qs)
    req = urllib.request.Request(
        f"{API_BASE}{path}?{qs}",
        headers={
            "X-BAPI-API-KEY": API_KEY,
            "X-BAPI-SIGN": sig, "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": "5000",
        })
    try:
        return json.loads(urllib.request.urlopen(req, timeout=8).read())
    except Exception as e:
        return {"retCode": -1, "retMsg": f"{type(e).__name__}: {e}"}


def cancel_standing_orders() -> dict:
    """Cancel ALL pending orders whose orderLinkId starts with sygSTND.
    Returns {cancelled, errors}."""
    # Read open orders
    r = signed_get("/v5/order/realtime",
                    {"category": "linear", "symbol": SYMBOL, "limit": 50})
    if r.get("retCode") != 0:
        return {"cancelled": 0, "errors": [r.get("retMsg")]}
    orders = (r.get("result") or {}).get("list") or []
    cancelled = 0
    errors = []
    for o in orders:
        olid = o.get("orderLinkId", "")
        if not olid.startswith(ORDER_PREFIX): continue
        if (o.get("orderStatus") or "") not in ("New", "Untriggered",
                                                  "PartiallyFilled"):
            continue
        # Cancel
        cr = signed_post("/v5/order/cancel", {
            "category": "linear", "symbol": SYMBOL,
            "orderLinkId": olid,
        })
        if cr.get("retCode") == 0:
            cancelled += 1
        else:
            errors.append(f"{olid[:14]}: {cr.get('retMsg')}")
    return {"cancelled": cancelled, "errors": errors}


def count_open_positions() -> int:
    r = N.run("portfolio.demo", {})
    if not r.get("ok"): return 0
    d = r.get("data") or {}
    return int(d.get("open_count") or 0)


def get_current_mid() -> float:
    r = N.run("btc.ticker", {"symbol": SYMBOL})
    d = r.get("data") or {}
    snap = d.get("snapshot") or {}
    last = snap.get("last")
    if last: return float(last)
    # Fallback: read orderbook
    ob = signed_get("/v5/market/orderbook",
                     {"category": "linear", "symbol": SYMBOL, "limit": 1})
    res = ob.get("result") or {}
    bids = res.get("b") or []; asks = res.get("a") or []
    if bids and asks:
        return (float(bids[0][0]) + float(asks[0][0])) / 2
    return 0.0


def calc_qty(stop_distance_usd: float, risk_usd: float,
              max_qty_btc: float = 0.5) -> float:
    """qty so that stop_distance × qty = risk_usd, capped at max."""
    if stop_distance_usd <= 0: return 0
    qty = risk_usd / stop_distance_usd
    return round(min(qty, max_qty_btc), 3)


def place_stop_entry(side: str, trigger_price: float, qty: float,
                     order_link_id: str, sl_price: float,
                     tp_price: float) -> dict:
    """Place a conditional MARKET order that fires when triggerPrice is
    hit. triggerDirection: 1 = trigger when price rises to trigger
    (stop-buy above mid OR stop-sell below mid as breakout); we use
    direction 1 for stop-buy long (price rising), 2 for stop-sell short
    (price falling)."""
    if side == "Buy":
        trigger_direction = 1   # trigger when price >= triggerPrice
    else:
        trigger_direction = 2   # trigger when price <= triggerPrice

    # 2026-05-11: TRAILING-ONLY — no fixed TP. Trail manager (every 20s) takes
    # over once position moves +0.50% in favor. Winners run until trail SL fires.
    # Fixed initial SL still caps downside.
    body = {
        "category":         "linear",
        "symbol":           SYMBOL,
        "side":             side,
        "orderType":        "Market",
        "qty":              str(qty),
        "triggerPrice":     str(round(trigger_price, 1)),
        "triggerBy":        "LastPrice",
        "triggerDirection": trigger_direction,
        "orderLinkId":      order_link_id,
        "timeInForce":      "GTC",
        "stopLoss":         str(round(sl_price, 1)),
        "tpslMode":         "Full",
        "slTriggerBy":      "LastPrice",
    }
    return signed_post("/v5/order/create", body)


# ---------------------------------------------------------------------------
# Snapshot integration — emit decision.snapshot per cycle
# ---------------------------------------------------------------------------
def emit_snapshot(side: str, kind: str, trigger: float, mid: float, qty: float,
                   sl: float, tp: float, executed: bool, err: str) -> str:
    """Build a synthetic plan dict + emit decision.snapshot so the joiner
    captures these stop orders in training_pairs."""
    plan = {
        "action":       "propose" if executed else "skip",
        "structure":    f"standing_perp_{side.lower()}_{kind}",
        "strategy":     "standing_orders",
        "instrument":   "perp",
        "symbol":       SYMBOL,
        "leverage":     LEVERAGE,
        "qty":          qty,
        "risk_pct":     round(RISK_USD / 1900 * 100, 4),
        "max_loss_usd": RISK_USD,
        "F":            mid,
        "thesis":       (f"standing_orders {side} {kind} trigger=${trigger:.0f} "
                          f"mid=${mid:.0f} tp=${tp:.0f} sl=${sl:.0f}"),
        "reason":       None if executed else err,
        "rule":         None if executed else "standing_order_place_failed",
        "context":      {
            "regime":         "standing_orders",
            "F":              mid,
            "trigger_price":  trigger,
            "tp_price":       tp,
            "sl_price":       sl,
            "trigger_kind":   kind,
        },
        "tier_promotion": {
            "env":  "demo", "kill_switch": False,
            "staged": True, "candidates": {}, "promotions": {},
            "skipped_reason": "standing_orders_uses_fixed_size",
        },
    }
    try:
        return DS.write_snapshot(plan)
    except Exception as e:
        print(f"  snapshot write failed: {e}", file=sys.stderr)
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cancel-only", action="store_true",
                    help="just cancel existing sygSTND-* and exit")
    args = ap.parse_args()

    cycle_start = dt.datetime.now(dt.timezone.utc)
    print(f"=== standing_orders @ {cycle_start.isoformat()} ===")

    if not API_KEY or not API_SEC:
        print("FATAL: BYBIT_DEMO_API_KEY/SECRET missing", file=sys.stderr)
        return 1

    # 1. Cancel any sygSTND-* still pending
    cancel = cancel_standing_orders()
    print(f"  cancelled {cancel['cancelled']} prior standing orders "
          f"({len(cancel['errors'])} errors)")
    for e in cancel["errors"][:3]: print(f"    err: {e}")

    if args.cancel_only:
        return 0

    # 2. Check we have room
    n_open = count_open_positions()
    print(f"  current open positions: {n_open}/{MAX_TOTAL_OPEN}")
    if n_open >= MAX_TOTAL_OPEN:
        print(f"  → SKIP: too many open positions, won't add standing orders")
        return 0

    # 3. Read current mid
    mid = get_current_mid()
    if mid <= 0:
        print("  → SKIP: couldn't read current price")
        return 1
    print(f"  current mid: ${mid:,.2f}")

    # 4. Place 4 stop orders
    cycle_id = str(uuid.uuid4())[:14].replace("-", "")
    plans = [
        # (side, kind, trigger_pct, name)
        ("Buy",  "near", LONG_NEAR_PCT,   "L1"),
        ("Buy",  "far",  LONG_FAR_PCT,    "L2"),
        ("Sell", "near", SHORT_NEAR_PCT,  "S1"),
        ("Sell", "far",  SHORT_FAR_PCT,   "S2"),
    ]

    n_placed = 0
    # 2026-05-11: skip same-direction sides if a claim is active
    try:
        from agent import strategy_claim as _CL
        _claim_active = _CL.active()
        _blocked_dir = (_claim_active or {}).get("direction")
    except Exception: _blocked_dir = None

    for side, kind, trigger_pct, label in plans:
        # Skip the side that matches an active claim direction
        if _blocked_dir == "long" and side == "Buy":
            print(f"  → SKIP Buy {kind} (claim owns long)")
            continue
        if _blocked_dir == "short" and side == "Sell":
            print(f"  → SKIP Sell {kind} (claim owns short)")
            continue
        if side == "Buy":
            trigger = mid * (1 + trigger_pct)
            sl      = trigger * (1 - SL_PCT)
            tp      = trigger * (1 + TP_PCT)
        else:
            trigger = mid * (1 - trigger_pct)
            sl      = trigger * (1 + SL_PCT)
            tp      = trigger * (1 - TP_PCT)

        stop_dist = abs(trigger - sl)
        qty = calc_qty(stop_dist, RISK_USD)
        if qty <= 0:
            print(f"  → SKIP {side} {kind}: qty=0")
            continue

        olid = f"{ORDER_PREFIX}{cycle_id[:8]}{label}"
        if args.dry_run or DRY_RUN:
            print(f"  DRY: {side} {kind} qty={qty} @ ${trigger:,.1f}  "
                  f"tp ${tp:,.1f}  sl ${sl:,.1f}  olid={olid}")
            emit_snapshot(side, kind, trigger, mid, qty, sl, tp,
                           executed=False, err="dry_run")
            continue

        r = place_stop_entry(side, trigger, qty, olid, sl, tp)
        rc = r.get("retCode")
        msg = r.get("retMsg", "")
        if rc == 0:
            order_id = (r.get("result") or {}).get("orderId", "?")
            print(f"  PLACED {side} {kind} qty={qty} @ ${trigger:,.1f}  "
                  f"tp/sl ${tp:,.1f}/${sl:,.1f}  olid={olid}  id={order_id[:14]}")
            emit_snapshot(side, kind, trigger, mid, qty, sl, tp,
                           executed=True, err="")
            n_placed += 1
        else:
            print(f"  FAIL {side} {kind} retCode={rc} {msg}")
            emit_snapshot(side, kind, trigger, mid, qty, sl, tp,
                           executed=False, err=msg)

    print(f"\n  === placed {n_placed}/{len(plans)} standing orders ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
