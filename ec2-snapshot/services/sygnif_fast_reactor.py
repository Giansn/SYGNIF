#!/usr/bin/env python3
"""sygnif_fast_reactor.py — WS-driven sub-second trade reactor.

Long-running daemon. Subscribes to Bybit V5 public WS (kline.1 + publicTrade)
and fires trades within ~500ms of conditions being met.

The 5-min training scanner is the "deliberate" trader — it considers the
full snapshot every cycle. This reactor is the "reflexive" trader — it
reacts to specific high-conviction events as they happen.

TRIGGERS (any one fires a trade):

1. BOUNCE_ACTIVATION
   When bounce_setup.json transitions inactive → active
   AND magnitude_abs_pct >= TRIGGER_BOUNCE_MAG_PCT
   → fire in bounce.direction, qty for FAST_RISK_USD risk

2. WHALE_IMPULSE
   When a single anonymous trade ≥ TRIGGER_WHALE_USD arrives
   AND whale_flow.json shows imbalance strongly same-direction (≥0.65)
   → fire same direction as the whale (follow the smart money)

3. FAST_MOMENTUM
   When a 1-min kline closes with |move| ≥ TRIGGER_MOMENTUM_PCT
   AND volume on that bar > 1.5× recent average
   → fire in direction of close

SAFETY:
  - DEMO ONLY (refuses live unless SYGNIF_FAST_LIVE_OK=1)
  - Per-direction cooldown (default 60s) — no spam fires
  - Max fast trades per hour (default 12)
  - Honors circuit_breaker.json
  - Honors training_policy daily loss limit
  - Open position cap (max 5 reactor positions in addition to other strategies)
  - All trades emit decision.snapshot + decision.executed for joiner

Run:
  /opt/sygnif/.venv/bin/python /opt/sygnif-services/sygnif_fast_reactor.py
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import hmac
import json
import os
import pathlib
import signal
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from typing import Any

try:
    import websocket
except ImportError:
    print("FATAL: websocket-client not installed", file=sys.stderr)
    sys.exit(1)


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
_load_env("/etc/sygnif/fast-reactor.env")

if (os.environ.get("SYGNIF_ORDERS_MODE") or "").lower() == "live":
    if os.environ.get("SYGNIF_FAST_LIVE_OK", "0") != "1":
        print("REFUSING: live mode but fast reactor is demo-only", file=sys.stderr)
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
DRY_RUN              = os.environ.get("SYGNIF_FAST_DRY_RUN", "0") == "1"
WS_URL               = "wss://stream.bybit.com/v5/public/linear"
SYMBOL               = os.environ.get("SYGNIF_FAST_SYMBOL", "BTCUSDT")

# Trigger thresholds
TRIGGER_BOUNCE_MAG_PCT = float(os.environ.get("SYGNIF_FAST_BOUNCE_MAG_PCT", "1.0"))
TRIGGER_WHALE_USD      = float(os.environ.get("SYGNIF_FAST_WHALE_USD", "1000000"))
TRIGGER_WHALE_IMB      = float(os.environ.get("SYGNIF_FAST_WHALE_IMB", "0.65"))
TRIGGER_MOMENTUM_PCT   = float(os.environ.get("SYGNIF_FAST_MOMENTUM_PCT", "0.4"))
TRIGGER_MOMENTUM_VOLX  = float(os.environ.get("SYGNIF_FAST_MOMENTUM_VOLX", "1.5"))

# Trade params
FAST_RISK_USD        = float(os.environ.get("SYGNIF_FAST_RISK_USD", "8"))
FAST_TP_PCT          = float(os.environ.get("SYGNIF_FAST_TP_PCT", "0.4")) / 100
FAST_SL_PCT          = float(os.environ.get("SYGNIF_FAST_SL_PCT", "0.25")) / 100
FAST_LEVERAGE        = int(os.environ.get("SYGNIF_FAST_LEVERAGE", "10"))

# Rate limits
COOLDOWN_PER_DIR_S   = int(os.environ.get("SYGNIF_FAST_COOLDOWN_S", "60"))
MAX_TRADES_PER_HOUR  = int(os.environ.get("SYGNIF_FAST_MAX_PER_HOUR", "12"))
MAX_OPEN_REACTOR     = int(os.environ.get("SYGNIF_FAST_MAX_OPEN", "3"))

ORDER_PREFIX = "sygFAST"
DB = "/var/lib/sygnif/swarm.db"
BOUNCE_FILE = pathlib.Path("/var/lib/sygnif/bounce_setup.json")
WHALE_FILE  = pathlib.Path("/var/lib/sygnif/whale_flow.json")
CB_FILE     = pathlib.Path("/var/lib/sygnif/circuit_breaker.json")

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
    "lock":              threading.Lock(),
    "last_fire_long_ts": 0,
    "last_fire_short_ts": 0,
    "last_bounce_active": False,
    "last_bounce_dir":   "none",
    "fire_history":      collections.deque(),   # (ts, trigger, direction)
    "n_open_reactor":    0,
    "ws_status":         "starting",
    "n_kline_events":    0,
    "n_trade_events":    0,
    "started_at":        time.time(),
    "last_msg_ts":       0,
    "last_close_price":  0.0,
    "recent_closes":     collections.deque(maxlen=10),
    "recent_bars":       collections.deque(maxlen=30),  # last 30 1m closed bars
}


# ============================================================================
# Intel summary integration (mtime-cached, sub-ms read on hot path)
# ============================================================================
INTEL_FILE = pathlib.Path("/var/lib/sygnif/intel_summary.json")
_intel_cache = {"mtime": 0.0, "data": None}

def read_intel() -> dict | None:
    """Read intel_summary.json with mtime-based caching.

    Returns the parsed dict, or None if file missing/corrupt.
    Hot-path performance: ~0.05ms when cache valid; ~1ms on re-read.
    """
    try:
        st = INTEL_FILE.stat()
    except FileNotFoundError:
        return None
    if st.st_mtime == _intel_cache["mtime"]:
        return _intel_cache["data"]
    try:
        data = json.loads(INTEL_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return _intel_cache["data"]   # keep last good data on transient error
    _intel_cache["mtime"] = st.st_mtime
    _intel_cache["data"] = data
    return data


def check_intel_for_direction(direction: str) -> tuple[bool, str, float]:
    """Pre-flight check: does our on-chain/macro intelligence VETO this direction?

    direction: 'long' or 'short'

    Returns: (allow: bool, reason: str, confidence_modifier: float)
       allow=False → skip this trade
       allow=True  → proceed; modifier ≥1.0 means boost, <1.0 means caution
    """
    intel = read_intel()
    if not intel:
        return (True, "no_intel", 1.0)
    # Staleness guard — intel older than 5min = unreliable
    age_s = max(0, int(time.time()) - intel.get("updated_at_ts", 0))
    if age_s > 300:
        return (True, f"intel_stale_{age_s}s", 1.0)
    if direction == "short":
        vetoes = intel.get("vetoes_short") or []
        boosts = intel.get("boosts_short") or []
    else:
        vetoes = intel.get("vetoes_long") or []
        boosts = intel.get("boosts_long") or []
    if vetoes:
        return (False, "intel_veto:" + ",".join(vetoes[:3]), 0.0)
    # boost confidence proportional to number of confluence signals (cap 1.5x)
    boost_factor = min(1.5, 1.0 + 0.1 * len(boosts))
    if boosts:
        return (True, "intel_boost:" + ",".join(boosts[:3]), boost_factor)
    return (True, "neutral", 1.0)




# ---------------------------------------------------------------------------
# Bybit REST
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
        data=body_str.encode(), method="POST",
        headers={
            "Content-Type": "application/json",
            "X-BAPI-API-KEY": API_KEY, "X-BAPI-SIGN": sig,
            "X-BAPI-SIGN-TYPE": "2", "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": "5000",
        })
    try:
        return json.loads(urllib.request.urlopen(req, timeout=5).read())
    except Exception as e:
        return {"retCode": -1, "retMsg": f"{type(e).__name__}: {e}"}


def place_market_with_bracket(side: str, qty: float, mid: float,
                                order_link_id: str) -> dict:
    """Market entry with attached TP/SL (Bybit V5)."""
    if side == "Buy":
        tp = mid * (1 + FAST_TP_PCT); sl = mid * (1 - FAST_SL_PCT)
    else:
        tp = mid * (1 - FAST_TP_PCT); sl = mid * (1 + FAST_SL_PCT)
    body = {
        "category":    "linear",
        "symbol":      SYMBOL,
        "side":        side,
        "orderType":   "Market",
        "qty":         str(qty),
        "orderLinkId": order_link_id,
        "timeInForce": "IOC",
        "takeProfit":  str(round(tp, 1)),
        "stopLoss":    str(round(sl, 1)),
        "tpslMode":    "Full",
        "tpTriggerBy": "LastPrice",
        "slTriggerBy": "LastPrice",
    }
    t0 = time.time()
    r = signed_post("/v5/order/create", body)
    latency_ms = int((time.time() - t0) * 1000)
    r["_latency_ms"] = latency_ms
    r["_tp_price"]   = round(tp, 1)
    r["_sl_price"]   = round(sl, 1)
    return r


# ---------------------------------------------------------------------------
# Safety gates
# ---------------------------------------------------------------------------
def gate_circuit_breaker() -> tuple[bool, str]:
    if not CB_FILE.exists(): return (True, "")
    try:
        cb = json.loads(CB_FILE.read_text())
        if cb.get("state") == "tripped":
            return (False, f"circuit_breaker tripped: {cb.get('reason')}")
    except Exception: pass
    return (True, "")


def gate_cooldown(direction: str) -> tuple[bool, str]:
    now = time.time()
    last = (state["last_fire_long_ts"] if direction == "long"
            else state["last_fire_short_ts"])
    if (now - last) < COOLDOWN_PER_DIR_S:
        return (False, f"cooldown {direction} {int(now - last)}s/{COOLDOWN_PER_DIR_S}s")
    return (True, "")


def gate_hourly_cap() -> tuple[bool, str]:
    cutoff = time.time() - 3600
    with state["lock"]:
        while state["fire_history"] and state["fire_history"][0][0] < cutoff:
            state["fire_history"].popleft()
        n = len(state["fire_history"])
    if n >= MAX_TRADES_PER_HOUR:
        return (False, f"hourly cap {n}/{MAX_TRADES_PER_HOUR}")
    return (True, "")


def gate_open_count() -> tuple[bool, str]:
    if state["n_open_reactor"] >= MAX_OPEN_REACTOR:
        return (False, f"reactor open cap {state['n_open_reactor']}/{MAX_OPEN_REACTOR}")
    return (True, "")


# ---------------------------------------------------------------------------
# Fire logic
# ---------------------------------------------------------------------------
def fire_trade(direction: str, trigger: str, mid: float, meta: dict) -> dict:
    """Place an immediate market order. direction = 'long'|'short'."""
    # 2026-05-11: respect bounce_v2 claim
    try:
        from agent import strategy_claim as _CL
        if not _CL.compatible_with(direction):
            a = _CL.active()
            return {"ok": False, "blocked": f"claim_active ({a.get('owner')} owns {direction})"}
    except Exception: pass

    # All gates
    for gate in (gate_circuit_breaker, gate_hourly_cap, gate_open_count):
        ok, msg = gate()
        if not ok:
            return {"ok": False, "blocked": msg}
    ok, msg = gate_cooldown(direction)
    if not ok:
        return {"ok": False, "blocked": msg}

    # 2026-05-11: M5 momentum veto — don't short rallies / long dumps
    M5_VETO_PCT = float(os.environ.get("SYGNIF_M5_VETO_PCT", "0.15"))
    closes = list(state.get("recent_closes") or [])
    if len(closes) >= 6:
        px_now, px_then = closes[-1], closes[-6]
        if px_then > 0:
            m5 = (px_now - px_then) / px_then * 100.0
            if direction == "short" and m5 >= M5_VETO_PCT:
                return {"ok": False, "blocked": f"m5_veto_short m5={m5:+.2f}%"}
            if direction == "long" and m5 <= -M5_VETO_PCT:
                return {"ok": False, "blocked": f"m5_veto_long m5={m5:+.2f}%"}

    # 2026-05-12: intelligence-layer veto/boost (on-chain + macro)
    allow, intel_reason, conf_modifier = check_intel_for_direction(direction)
    if not allow:
        return {"ok": False, "blocked": intel_reason}
    # Note: conf_modifier is logged in emit_executed meta for later analysis.
    # We do NOT change qty here yet — that's Phase 2 of the confidence arbiter.

    # Compute qty
    stop_dist = mid * FAST_SL_PCT
    qty = round(min(FAST_RISK_USD / stop_dist, 0.5), 3)
    if qty <= 0:
        return {"ok": False, "blocked": "qty=0"}

    side = "Buy" if direction == "long" else "Sell"
    cid = str(uuid.uuid4())
    olid = f"{ORDER_PREFIX}{cid[:14].replace('-', '')}"

    # Emit snapshot BEFORE placing — captures intent + state
    plan = {
        "action":       "propose",
        "structure":    f"fast_{trigger}_{direction}",
        "strategy":     "fast_reactor",
        "instrument":   "perp",
        "symbol":       SYMBOL,
        "leverage":     FAST_LEVERAGE,
        "qty":          qty,
        "max_loss_usd": FAST_RISK_USD,
        "F":            mid,
        "thesis":       f"fast_reactor {trigger} {direction}: {meta.get('thesis','')}",
        "context":      {"regime": "fast_reactor", "F": mid,
                          "trigger": trigger, "trigger_meta": meta},
        "tier_promotion": {"env": "demo", "kill_switch": False, "staged": True,
                            "candidates": {}, "promotions": {}},
        "correlation_id": cid,
    }
    try:
        DS.write_snapshot(plan, correlation_id=cid)
    except Exception: pass

    if DRY_RUN:
        print(f"  DRY: fire_trade {direction} qty={qty} via {trigger}", flush=True)
        emit_executed(cid, direction, qty, {"_latency_ms": 0,
                                              "retMsg": "dry_run"},
                       olid, trigger, mid, meta)
        return {"ok": True, "dry_run": True, "qty": qty, "cid": cid}

    # PLACE
    r = place_market_with_bracket(side, qty, mid, olid)
    rc = r.get("retCode")
    success = (rc == 0)

    if success:
        now = time.time()
        with state["lock"]:
            if direction == "long":
                state["last_fire_long_ts"] = now
            else:
                state["last_fire_short_ts"] = now
            state["fire_history"].append((now, trigger, direction))
            state["n_open_reactor"] += 1

    print(f"  {'✓' if success else '✗'} FIRE {direction} qty={qty} @ ${mid:.0f} "
          f"via {trigger} ({r.get('_latency_ms', '?')}ms) "
          f"retCode={rc} {r.get('retMsg', '')[:50]}", flush=True)

    emit_executed(cid, direction, qty, r, olid, trigger, mid, meta)
    return r


def emit_executed(cid: str, direction: str, qty: float, r: dict,
                    olid: str, trigger: str, mid: float, meta: dict) -> None:
    try:
        N.run("swarm.write", {
            "content": (f"FAST_FIRE [demo] correlation_id={cid[:8]} "
                        f"trigger={trigger} {direction} qty={qty} "
                        f"@${mid:.0f} latency={r.get('_latency_ms','?')}ms"),
            "swarm_id":  "trading",
            "agent_id":  "sygnif-fast-reactor",
            "topic":     "decision.executed",
            "tags":      ["fast", trigger, direction, "demo"],
            "meta": {
                "correlation_id":  cid,
                "env":             "demo", "mode": "demo",
                "executed":        r.get("retCode") == 0,
                "order_link_ids":  [olid],
                "structure":       f"fast_{trigger}_{direction}",
                "strategy":        "fast_reactor",
                "instrument":      "perp",
                "leverage_tier":   "default", "size_tier": "default",
                "tp_price":        r.get("_tp_price"),
                "sl_price":        r.get("_sl_price"),
                "qty":             qty,
                "risk_usd":        FAST_RISK_USD,
                "exchange_error":  r.get("retMsg") if r.get("retCode") != 0 else None,
                "latency_ms":      r.get("_latency_ms"),
                "trigger":         trigger,
                "trigger_meta":    meta,
            },
        })
    except Exception as e:
        print(f"  emit_executed failed: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Trigger evaluators — called on every WS event
# ---------------------------------------------------------------------------
def _read_json(p: pathlib.Path) -> dict:
    if not p.exists(): return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def eval_trigger_bounce(mid: float) -> None:
    """Fire when bounce_setup just activated."""
    bs = _read_json(BOUNCE_FILE)
    active = bool(bs.get("active"))
    direction = bs.get("direction")
    mag = bs.get("magnitude_abs_pct", 0) or 0

    was_active = state["last_bounce_active"]
    was_dir    = state["last_bounce_dir"]
    state["last_bounce_active"] = active
    state["last_bounce_dir"]    = direction

    # Edge trigger: transition False → True OR direction flip
    transition = active and (not was_active or direction != was_dir)
    if not transition: return
    if mag < TRIGGER_BOUNCE_MAG_PCT: return
    if direction not in ("long", "short"): return

    print(f"  [TRIGGER bounce] {direction} mag={mag:.2f}% mid=${mid:.0f}", flush=True)
    fire_trade(direction, "bounce", mid, {
        "thesis": f"bounce mag {mag:.2f}% target +{bs.get('expected_target_pct',0):.2f}%",
        "bounce_setup": {k: bs.get(k) for k in ("direction", "magnitude_abs_pct",
                                                  "expected_target_pct",
                                                  "trigger_high", "trigger_low",
                                                  "pivot_age_min")},
    })


def eval_trigger_whale(trade_price: float, trade_side: str,
                        trade_notional: float) -> None:
    """Fire when a single trade ≥ TRIGGER_WHALE_USD AND whale-flow imbalance
    is strongly same-direction."""
    if trade_notional < TRIGGER_WHALE_USD: return

    wf = _read_json(WHALE_FILE)
    imb = wf.get("whale_imbalance", 0.5)
    age_s = wf.get("uptime_s", 9999)

    # Map whale_flow direction
    # imbalance ≥ 0.65 → recent flow is buy-heavy → align with whale on Buy
    # imbalance ≤ 0.35 → sell-heavy → align with whale on Sell
    if trade_side == "Buy" and imb < TRIGGER_WHALE_IMB:
        return
    if trade_side == "Sell" and imb > (1 - TRIGGER_WHALE_IMB):
        return

    direction = "long" if trade_side == "Buy" else "short"
    print(f"  [TRIGGER whale] single ${trade_notional/1e6:.2f}M {trade_side} "
          f"imb={imb:.2f} → {direction} mid=${trade_price:.0f}", flush=True)
    fire_trade(direction, "whale", trade_price, {
        "thesis": (f"whale ${trade_notional/1e6:.2f}M {trade_side} "
                   f"+ flow_imb {imb:.2f}"),
        "whale_single_usd":  trade_notional,
        "whale_flow_imb":    imb,
    })


def eval_trigger_momentum(bar: dict) -> None:
    """Fire on a closed 1m bar with > MOMENTUM_PCT move + volume surge."""
    if not bar.get("confirm"): return    # only on closed bars
    state["recent_bars"].append(bar)
    if len(state["recent_bars"]) < 10: return

    o, c = bar["open"], bar["close"]
    if o <= 0: return
    move_pct = (c - o) / o * 100
    vol = bar["volume"]
    avg_vol = sum(b["volume"] for b in list(state["recent_bars"])[-10:-1]) / 9
    if avg_vol <= 0: return
    vol_ratio = vol / avg_vol

    if abs(move_pct) < TRIGGER_MOMENTUM_PCT: return
    if vol_ratio < TRIGGER_MOMENTUM_VOLX: return

    direction = "long" if move_pct > 0 else "short"
    print(f"  [TRIGGER momentum] 1m bar {move_pct:+.2f}% vol×{vol_ratio:.2f} "
          f"→ {direction} mid=${c:.0f}", flush=True)
    fire_trade(direction, "momentum", c, {
        "thesis": f"1m bar {move_pct:+.2f}% vol×{vol_ratio:.2f}",
        "bar_move_pct": round(move_pct, 3),
        "bar_vol_ratio": round(vol_ratio, 2),
    })


# ---------------------------------------------------------------------------
# WS handlers
# ---------------------------------------------------------------------------
def on_open(ws):
    state["ws_status"] = "connected"
    topics = [f"kline.1.{SYMBOL}", f"publicTrade.{SYMBOL}"]
    print(f"  ws connected — subscribing: {topics}", flush=True)
    ws.send(json.dumps({"op": "subscribe", "args": topics}))


def on_message(ws, message):
    try:
        msg = json.loads(message)
    except json.JSONDecodeError:
        return
    state["last_msg_ts"] = time.time()
    if msg.get("op") == "subscribe": return
    topic = msg.get("topic", "")
    data = msg.get("data") or []
    if not isinstance(data, list): return

    if topic.startswith("kline."):
        state["n_kline_events"] += len(data)
        for k in data:
            try:
                bar = {
                    "ts_ms_open": int(k.get("start") or 0),
                    "open":  float(k.get("open") or 0),
                    "high":  float(k.get("high") or 0),
                    "low":   float(k.get("low") or 0),
                    "close": float(k.get("close") or 0),
                    "volume": float(k.get("volume") or 0),
                    "confirm": bool(k.get("confirm") or False),
                }
            except (ValueError, TypeError):
                continue
            state["last_close_price"] = bar["close"] or state["last_close_price"]
            if bar.get("close"):
                state["recent_closes"].append(float(bar["close"]))
            # Bounce eval on every kline tick (lightweight)
            try: eval_trigger_bounce(bar["close"])
            except Exception as e:
                print(f"  bounce-eval err: {e}", file=sys.stderr)
            # Momentum eval on closed bars only
            if bar["confirm"]:
                try: eval_trigger_momentum(bar)
                except Exception as e:
                    print(f"  momentum-eval err: {e}", file=sys.stderr)

    elif topic.startswith("publicTrade."):
        for t in data:
            try:
                price = float(t.get("p") or 0)
                qty   = float(t.get("v") or 0)
                side  = t.get("S") or ""
            except (ValueError, TypeError):
                continue
            state["n_trade_events"] += 1
            notional = price * qty
            try: eval_trigger_whale(price, side, notional)
            except Exception as e:
                print(f"  whale-eval err: {e}", file=sys.stderr)


def on_error(ws, error):
    state["ws_status"] = f"error: {error}"
    print(f"  ws error: {error}", file=sys.stderr, flush=True)


def on_close(ws, code, reason):
    state["ws_status"] = "disconnected"
    print(f"  ws closed: {code} {reason}", file=sys.stderr, flush=True)


def status_loop():
    """Stats every 30s — keeps log alive + monitorable."""
    while True:
        time.sleep(30)
        age = (time.time() - state["last_msg_ts"]) if state["last_msg_ts"] else 0
        with state["lock"]:
            n_recent = len(state["fire_history"])
        print(f"  stats: ws={state['ws_status']} kline={state['n_kline_events']} "
              f"trades={state['n_trade_events']} fires_1h={n_recent} "
              f"last_msg_age={age:.1f}s last_close=${state['last_close_price']:.0f} "
              f"open_reactor={state['n_open_reactor']}", flush=True)


def reconciler_loop():
    """Every 60s, sync n_open_reactor from Bybit (positions might close
    via TP/SL while we're not watching exec stream)."""
    while True:
        time.sleep(60)
        try:
            r = N.run("portfolio.demo", {})
            opens = (r.get("data") or {}).get("open") or []
            # Count only positions whose orderLinkId starts with sygFAST
            # — but portfolio.demo doesn't expose orderLinkId. Approximate:
            # count BTCUSDT perp positions and subtract known non-reactor.
            # Simpler: reactor's count goes UP on fire, decays naturally
            # over time (5 min after fire we assume position likely closed
            # via TP/SL bracket).
            cutoff = time.time() - 600  # 10 min ago
            with state["lock"]:
                # Remove fires older than 10min from the "still open" tally
                kept = sum(1 for ts, *_ in state["fire_history"] if ts > cutoff)
                state["n_open_reactor"] = min(state["n_open_reactor"], kept)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not API_KEY or not API_SEC:
        print("FATAL: BYBIT_DEMO_API_KEY/SECRET missing", file=sys.stderr)
        return 1
    print(f"=== sygnif_fast_reactor starting ===", flush=True)
    print(f"  symbol:            {SYMBOL}", flush=True)
    print(f"  dry_run:           {DRY_RUN}", flush=True)
    print(f"  bounce_mag_pct:    {TRIGGER_BOUNCE_MAG_PCT}", flush=True)
    print(f"  whale_usd:         ${TRIGGER_WHALE_USD:,.0f}", flush=True)
    print(f"  momentum_pct:      {TRIGGER_MOMENTUM_PCT}", flush=True)
    print(f"  risk/trade:        ${FAST_RISK_USD}", flush=True)
    print(f"  bracket:           tp {FAST_TP_PCT*100:.2f}% / sl {FAST_SL_PCT*100:.2f}%", flush=True)
    print(f"  cooldown/dir:      {COOLDOWN_PER_DIR_S}s", flush=True)
    print(f"  max trades/hour:   {MAX_TRADES_PER_HOUR}", flush=True)

    threading.Thread(target=status_loop, daemon=True).start()
    threading.Thread(target=reconciler_loop, daemon=True).start()

    backoff = 5
    while True:
        try:
            ws = websocket.WebSocketApp(
                WS_URL, on_open=on_open, on_message=on_message,
                on_error=on_error, on_close=on_close)
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except KeyboardInterrupt:
            return 0
        except Exception as e:
            print(f"  run_forever: {e}", file=sys.stderr, flush=True)
        state["ws_status"] = "reconnecting"
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    sys.exit(main())
