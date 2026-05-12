#!/usr/bin/env python3
"""sygnif_bounce_watcher.py V2 — confluence-based one-trade-per-swing.

V1 (deprecated): detected "1.5% move in 30min" and fired opposite. Crude
and led to multiple shotgun entries during a single spike (see 22:31-22:40
postmortem on 2026-05-10).

V2 architecture:
  1. Every kline.1 WS update, run swing_detector.detect()
  2. If side=top_short or bottom_long with score >= 5:
       → strategy_claim.acquire(owner="bounce_v2", kind=side, ...)
       → if acquire succeeds (no existing claim): place ONE market trade
            with WIDE bracket (TP at 60% retrace of swing, SL 0.40%)
       → if acquire fails (claim already active): NO-OP, observe
  3. After fill, track exit via Bybit V5 position events
       (handled by bybit_daemon's WS — emits trade.close)
  4. When position closes: claim.mark_closed → released → ready for next swing

Other strategies (training_scanner, fast_reactor, standing_orders) call
strategy_claim.compatible_with(direction) before firing — they skip
same-direction trades while bounce owns the claim.

Output:
  /var/lib/sygnif/bounce_setup.json — last detector result (every 5s)
  /var/lib/sygnif/strategy_claim.json — active claim (when set)
  swarm topic agent.bounce_alert — when a claim is acquired
"""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import hmac
import json
import os
import pathlib
import signal
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid

try:
    import websocket
except ImportError:
    print("FATAL: websocket-client missing", file=sys.stderr); sys.exit(1)

sys.path.insert(0, "/home/ubuntu/sygnif-agent-mirror")
try:
    from agent import swing_detector as SD
    from agent import strategy_claim as CL
    from agent import decision_snapshot as DS
except Exception as e:
    print(f"FATAL: import: {e}", file=sys.stderr); sys.exit(1)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
WS_URL  = os.environ.get("SYGNIF_BOUNCE_WS_URL",
                          "wss://stream.bybit.com/v5/public/linear")
SYMBOL  = os.environ.get("SYGNIF_BOUNCE_SYMBOL", "BTCUSDT")
INTERVAL = os.environ.get("SYGNIF_BOUNCE_INTERVAL", "1")

MIN_SCORE      = int(os.environ.get("SYGNIF_BOUNCE_MIN_SCORE", "5"))
RISK_USD       = float(os.environ.get("SYGNIF_BOUNCE_RISK_USD", "15"))
LEVERAGE       = int(os.environ.get("SYGNIF_BOUNCE_LEVERAGE", "10"))
DRY_RUN        = os.environ.get("SYGNIF_BOUNCE_DRY_RUN", "0") == "1"
CLAIM_TTL_MIN  = int(os.environ.get("SYGNIF_BOUNCE_CLAIM_TTL_MIN", "60"))
DETECT_EVERY_S = int(os.environ.get("SYGNIF_BOUNCE_DETECT_EVERY_S", "10"))
FLUSH_S        = int(os.environ.get("SYGNIF_BOUNCE_FLUSH_S", "5"))

BOUNCE_FILE = pathlib.Path("/var/lib/sygnif/bounce_setup.json")
DB = "/var/lib/sygnif/swarm.db"

API_BASE = (os.environ.get("BYBIT_DEMO_API_BASE")
            or "https://api-demo.bybit.com").rstrip("/")
API_KEY  = (os.environ.get("BYBIT_DEMO_API_KEY")
            or os.environ.get("BYBIT_API_KEY"))
API_SEC  = (os.environ.get("BYBIT_DEMO_API_SECRET")
            or os.environ.get("BYBIT_API_SECRET"))


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
state = {
    "ws_status":      "starting",
    "last_msg_ts":    0,
    "n_kline_events": 0,
    "recent_closes": collections.deque(maxlen=10),
    "last_detect_ts": 0,
    "last_result":    None,
    "n_claims_acquired": 0,
    "started_at":     time.time(),
}


# ---------------------------------------------------------------------------
# Bybit REST
# ---------------------------------------------------------------------------
def _sign(payload: str):
    ts = str(int(time.time() * 1000))
    sig = hmac.new(API_SEC.encode(),
                    (ts + API_KEY + "5000" + payload).encode(),
                    hashlib.sha256).hexdigest()
    return ts, sig


def _signed_post(path: str, body: dict) -> dict:
    body_str = json.dumps(body)
    ts, sig = _sign(body_str)
    req = urllib.request.Request(
        f"{API_BASE}{path}", data=body_str.encode(), method="POST",
        headers={"Content-Type":"application/json","X-BAPI-API-KEY":API_KEY,
                  "X-BAPI-SIGN":sig,"X-BAPI-SIGN-TYPE":"2",
                  "X-BAPI-TIMESTAMP":ts,"X-BAPI-RECV-WINDOW":"5000"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=5).read())
    except Exception as e:
        return {"retCode": -1, "retMsg": f"{type(e).__name__}: {e}"}


def _place_market_with_bracket(side: str, qty: float, tp: float, sl: float,
                                 olid: str) -> dict:
    # 2026-05-11: TRAILING-ONLY — no fixed TP at entry. The detector's
    # swing-range target (tp arg) is preserved for the swarm row meta but
    # NOT sent to Bybit. Trail manager handles all exits.
    body = {
        "category":    "linear", "symbol": SYMBOL, "side": side,
        "orderType":   "Market", "qty": str(qty), "orderLinkId": olid,
        "timeInForce": "IOC",
        "stopLoss":    str(round(sl, 1)),
        "tpslMode":    "Full",
        "slTriggerBy": "LastPrice",
    }
    t0 = time.time()
    r = _signed_post("/v5/order/create", body)
    r["_latency_ms"] = int((time.time() - t0) * 1000)
    return r


def _calc_qty(entry: float, sl: float) -> float:
    dist = abs(entry - sl)
    if dist <= 0: return 0
    return round(min(RISK_USD / dist, 0.5), 3)


# ---------------------------------------------------------------------------
# Core swing-detect loop (called from kline WS messages)
# ---------------------------------------------------------------------------
def maybe_detect_and_fire():
    """Run detector + try to claim + fire trade if we got the claim."""
    now = time.time()
    if now - state["last_detect_ts"] < DETECT_EVERY_S:
        return
    state["last_detect_ts"] = now

    try:
        result = SD.detect(SYMBOL)
    except Exception as e:
        print(f"  detect err: {e}", file=sys.stderr, flush=True)
        return

    state["last_result"] = result
    side = result.get("side")
    score = result.get("score", 0)
    fired = result.get("signals_fired", [])

    # Log every detection
    print(f"  [detect] side={side} score={score}/10 fired={fired[:3]}",
          flush=True)

    if side == "none" or score < MIN_SCORE:
        return

    direction = "short" if side == "top_short" else "long"

    # 2026-05-11: M5 momentum veto — don't short rallies / long dumps
    M5_VETO_PCT = float(os.environ.get("SYGNIF_M5_VETO_PCT", "0.15"))
    closes = list(state.get("recent_closes") or [])
    if len(closes) >= 6:
        px_now, px_then = closes[-1], closes[-6]
        if px_then > 0:
            m5 = (px_now - px_then) / px_then * 100.0
            if direction == "short" and m5 >= M5_VETO_PCT:
                print(f"  [M5 VETO] SHORT into m5 momentum +{m5:.2f}% — blocked",
                      flush=True)
                return
            if direction == "long" and m5 <= -M5_VETO_PCT:
                print(f"  [M5 VETO] LONG into m5 momentum {m5:.2f}% — blocked",
                      flush=True)
                return

    # Try to acquire claim
    bybit_side = "Sell" if direction == "short" else "Buy"
    entry = result["entry"]; tp = result["tp"]; sl = result["sl"]

    claim = CL.acquire(
        owner="bounce_v2", kind=side,
        entry=entry, tp=tp, sl=sl,
        order_link_id=f"sygBNCE{uuid.uuid4().hex[:12]}",
        ttl_min=CLAIM_TTL_MIN,
        symbol=SYMBOL,
        thesis=result.get("thesis", ""),
        confluence_score=score,
        confluence_signals=fired,
    )
    if claim is None:
        # Existing claim active — observe, don't fire
        active = CL.active()
        print(f"  [claim BLOCKED] active={active.get('owner') if active else '?'} "
              f"kind={active.get('kind') if active else '?'} — waiting",
              flush=True)
        return

    olid = claim["order_link_id"]
    qty = _calc_qty(entry, sl)
    if qty <= 0:
        CL.release("bounce_v2", reason="qty=0")
        print("  [claim RELEASED] qty=0", flush=True); return

    # Always emit snapshot + alert
    _emit_alert(claim, result)

    if DRY_RUN:
        print(f"  [DRY_RUN] would fire {bybit_side} qty={qty} @${entry} "
              f"tp ${tp} sl ${sl}", flush=True)
        return

    # FIRE
    state["n_claims_acquired"] += 1
    print(f"  [FIRE] {bybit_side} qty={qty} @${entry:.0f} tp ${tp:.0f} "
          f"sl ${sl:.0f} score={score}/10 olid={olid[:14]}", flush=True)

    r = _place_market_with_bracket(bybit_side, qty, tp, sl, olid)
    rc = r.get("retCode")
    latency = r.get("_latency_ms", "?")
    if rc == 0:
        order_id = (r.get("result") or {}).get("orderId", "")
        CL.mark_filled("bounce_v2", entry, order_id)
        print(f"    OK latency={latency}ms order_id={order_id[:14]}", flush=True)
    else:
        # Order failed → release the claim immediately
        msg = r.get("retMsg", "")
        CL.release("bounce_v2", reason=f"order_fail_{rc}: {msg[:60]}")
        print(f"    FAILED retCode={rc} msg={msg[:120]} — claim released",
              flush=True)


def _emit_alert(claim: dict, result: dict):
    try:
        c = sqlite3.connect(DB, timeout=10)
        head = (f"BOUNCE V2 {claim['kind']} score={claim['confluence_score']}/10 "
                f"entry=${claim['entry_price']:.0f} tp=${claim['tp_price']:.0f} "
                f"sl=${claim['sl_price']:.0f} signals={','.join(result.get('signals_fired',[])[:4])}")
        c.execute(
            "INSERT OR IGNORE INTO swarm_entries "
            "(id, created, swarm_id, agent_id, topic, content, meta, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), int(time.time()), "trading",
             "sygnif-bounce-watcher", "agent.bounce_alert", head,
             json.dumps({"claim": claim, "detector": result}, default=str),
             json.dumps(["bounce", "v2", claim["kind"], "alert"])))
        c.commit(); c.close()
    except Exception as e:
        print(f"  alert emit err: {e}", file=sys.stderr, flush=True)


def write_state_file():
    """Mirror swing detector state to bounce_setup.json (for compat)."""
    BOUNCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "computed_utc":   dt.datetime.now(dt.timezone.utc).isoformat(),
        "ws_status":      state["ws_status"],
        "n_kline_events": state["n_kline_events"],
        "n_claims_acquired": state["n_claims_acquired"],
        "uptime_s":       int(time.time() - state["started_at"]),
        "last_result":    state["last_result"] or {},
        "active_claim":   CL.active(),
    }
    tmp = BOUNCE_FILE.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(out, f, indent=2, default=str)
    os.replace(tmp, BOUNCE_FILE)


# ---------------------------------------------------------------------------
# WS handlers
# ---------------------------------------------------------------------------
def on_open(ws):
    state["ws_status"] = "connected"
    topic = f"kline.{INTERVAL}.{SYMBOL}"
    print(f"  ws connected — sub {topic}", flush=True)
    ws.send(json.dumps({"op": "subscribe", "args": [topic]}))


def on_message(ws, msg):
    try:
        m = json.loads(msg)
    except json.JSONDecodeError:
        return
    state["last_msg_ts"] = time.time()
    if m.get("op") == "subscribe": return
    data = m.get("data") or []
    if not isinstance(data, list): return
    state["n_kline_events"] += len(data)
    # Track recent closes for momentum-veto
    for k in data:
        if k.get("confirm") and k.get("close"):
            try:
                state["recent_closes"].append(float(k["close"]))
            except (ValueError, TypeError):
                pass
    # Only run detector on closed bars (confirm=True) to avoid noise
    if any((k.get("confirm") for k in data)):
        try:
            maybe_detect_and_fire()
        except Exception as e:
            print(f"  detect/fire err: {e}", file=sys.stderr, flush=True)


def on_error(ws, e):
    state["ws_status"] = f"error: {e}"
    print(f"  ws err: {e}", file=sys.stderr, flush=True)


def on_close(ws, code, reason):
    state["ws_status"] = "disconnected"


def flush_loop():
    while True:
        try: write_state_file()
        except Exception as e: print(f"  flush err: {e}", file=sys.stderr, flush=True)
        time.sleep(FLUSH_S)


def status_loop():
    while True:
        time.sleep(60)
        active = CL.active()
        print(f"  stats: ws={state['ws_status']} klines={state['n_kline_events']} "
              f"claims_fired={state['n_claims_acquired']} "
              f"active_claim={active.get('id') if active else 'none'}",
              flush=True)


def main():
    if not API_KEY or not API_SEC:
        print("FATAL: BYBIT_DEMO_API_KEY/SECRET missing", file=sys.stderr)
        return 1
    print(f"=== sygnif_bounce_watcher V2 starting ===", flush=True)
    print(f"  symbol:   {SYMBOL}", flush=True)
    print(f"  min_score: {MIN_SCORE}/10", flush=True)
    print(f"  risk/trade: ${RISK_USD}", flush=True)
    print(f"  claim_ttl: {CLAIM_TTL_MIN}min", flush=True)
    print(f"  dry_run:   {DRY_RUN}", flush=True)

    threading.Thread(target=flush_loop, daemon=True).start()
    threading.Thread(target=status_loop, daemon=True).start()

    backoff = 5
    while True:
        try:
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open,
                                           on_message=on_message,
                                           on_error=on_error, on_close=on_close)
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except KeyboardInterrupt: return 0
        except Exception as e: print(f"  loop err: {e}", file=sys.stderr, flush=True)
        state["ws_status"] = "reconnecting"
        time.sleep(backoff); backoff = min(backoff*2, 60)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    sys.exit(main())
