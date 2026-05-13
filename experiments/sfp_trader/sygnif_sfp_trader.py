#!/usr/bin/env python3
"""sygnif_sfp_trader.py — dedicated SFP-priority perp opener.

Architecture decision (2026-05-13): separate the SFP signal from fast-reactor.

When an SFP signal fires:
  1. SFP trader has PRIORITY over fast-reactor.
  2. SFP trader can only fire if no other strategy holds an open perp.
  3. Once SFP holds a position, fast-reactor must stand down on perp opens.

When the market is in "casual" mode (no SFP, just drift + small momentum +
whale flow):
  - fast-reactor handles those as before (sygFAST prefix)
  - SFP trader stays silent

The SFP signal itself (per PR #15's 30-day backtest) currently does NOT have
positive expectancy on BTC 1m. This daemon ships DISABLED by default
(SYGNIF_SFP_TRADER_ENABLED != "1"). Enable only after pursuing one of:
  - Regime filter (only fire in range-bound conditions)
  - Inverted directionality (treat SFP as breakout-confirmation, not mean-rev)
  - Higher-timeframe Fib levels (1h/4h instead of 1m × 240)
  - Trailing-stop exit instead of fixed TP
  - Intel-confluence ≥ 2 boosts before firing

This file ships the SCAFFOLDING:
  - WS connection + bar buffer (mirrors fast-reactor's pattern)
  - SFP + Fib detector (reuses fib_sfp_trigger from fast_reactor_v2)
  - strategy_claim mutex check (SFP gets priority)
  - Order placement via bybit-mcp vault
  - orderLinkID prefix `sygSFP`
  - Hard gate: refuses to fire while SYGNIF_SFP_TRADER_ENABLED != "1"

Run:
  /opt/sygnif/.venv/bin/python /opt/sygnif-services/sygnif_sfp_trader.py
"""
from __future__ import annotations

import collections
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
from typing import Any, Optional

try:
    import websocket
except ImportError:
    print("FATAL: websocket-client not installed", file=sys.stderr)
    sys.exit(1)

# Reuse fast-reactor's intel helper + the fib_sfp_trigger module from
# experiments/fast_reactor_v2/. In production both live in /opt/sygnif-services/.
sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, "/opt/sygnif-services")
try:
    from fib_sfp_trigger import FibSfpState
except ImportError as e:
    print(f"FATAL: cannot import fib_sfp_trigger: {e}", file=sys.stderr)
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
_load_env("/etc/sygnif/sfp-trader.env")


# ---------------------------------------------------------------------------
# Config — all env-driven, all OFF by default
# ---------------------------------------------------------------------------
ENABLED              = os.environ.get("SYGNIF_SFP_TRADER_ENABLED", "0") == "1"
DRY_RUN              = os.environ.get("SYGNIF_SFP_DRY_RUN", "1") == "1"   # safe default
WS_URL               = "wss://stream.bybit.com/v5/public/linear"
SYMBOL               = os.environ.get("SYGNIF_SFP_SYMBOL", "BTCUSDT")

ORDER_PREFIX         = "sygSFP"
DB                   = "/var/lib/sygnif/swarm.db"
INTEL_FILE           = pathlib.Path("/var/lib/sygnif/intel_summary.json")
STRATEGY_CLAIM_FILE  = pathlib.Path("/var/lib/sygnif/strategy_claim.json")

# Trade params (intentionally conservative; tighter than fast-reactor defaults)
SFP_RISK_USD         = float(os.environ.get("SYGNIF_SFP_RISK_USD", "5"))
SFP_TP_PCT           = float(os.environ.get("SYGNIF_SFP_TP_PCT", "0.5")) / 100
SFP_SL_PCT           = float(os.environ.get("SYGNIF_SFP_SL_PCT", "0.25")) / 100
SFP_LEVERAGE         = int(os.environ.get("SYGNIF_SFP_LEVERAGE", "10"))

# Rate limits — SFP fires are rare by design
COOLDOWN_S           = int(os.environ.get("SYGNIF_SFP_COOLDOWN_S", "1800"))   # 30min
MAX_OPEN_SFP         = int(os.environ.get("SYGNIF_SFP_MAX_OPEN", "1"))

API_BASE = (os.environ.get("BYBIT_DEMO_API_BASE")
            or "https://api-demo.bybit.com").rstrip("/")
API_KEY  = (os.environ.get("BYBIT_DEMO_API_KEY")
            or os.environ.get("BYBIT_API_KEY"))
API_SEC  = (os.environ.get("BYBIT_DEMO_API_SECRET")
            or os.environ.get("BYBIT_API_SECRET"))


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
state = {
    "lock":         threading.Lock(),
    "fib":          FibSfpState(),
    "last_fire_ts": 0,
    "n_open_sfp":   0,
    "ws_status":    "starting",
    "started_at":   time.time(),
    "last_msg_ts":  0,
    "n_kline_events": 0,
    "n_fires":      0,
}

_running = True


# ---------------------------------------------------------------------------
# Strategy-claim mutex (file-based, atomic via flock)
# ---------------------------------------------------------------------------
def read_claim() -> dict:
    """Read the strategy_claim.json mutex state."""
    try:
        return json.loads(STRATEGY_CLAIM_FILE.read_text())
    except Exception:
        return {"claims": []}


def has_other_owner_open(claim: dict) -> bool:
    """True iff any strategy OTHER than us currently holds an open position."""
    for c in claim.get("claims", []):
        if c.get("status") == "open" and not c.get("owner", "").startswith("sfp"):
            return True
    return False


def acquire_claim(symbol: str, side: str, olid: str) -> bool:
    """Atomic-ish claim acquisition. Returns True if we got the slot."""
    claim = read_claim()
    # Refuse if anyone (even another SFP) has open same-symbol+side
    for c in claim.get("claims", []):
        if c.get("status") == "open" and c.get("symbol") == symbol and c.get("side") == side:
            return False
    claim.setdefault("claims", []).append({
        "owner":  f"sfp-trader",
        "symbol": symbol,
        "side":   side,
        "olid":   olid,
        "status": "open",
        "opened_at": int(time.time()),
    })
    try:
        STRATEGY_CLAIM_FILE.write_text(json.dumps(claim, indent=2))
        return True
    except Exception as e:
        print(f"  claim write err: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Intel — reuse fast-reactor's mtime-cached pattern
# ---------------------------------------------------------------------------
_intel_cache = {"mtime": 0.0, "data": None}

def read_intel() -> Optional[dict]:
    try:
        st = INTEL_FILE.stat()
    except FileNotFoundError:
        return None
    if st.st_mtime == _intel_cache["mtime"]:
        return _intel_cache["data"]
    try:
        data = json.loads(INTEL_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return _intel_cache["data"]
    _intel_cache["mtime"] = st.st_mtime
    _intel_cache["data"] = data
    return data


def intel_allows_long() -> tuple[bool, str]:
    """Confluence: require boosts_long non-empty AND vetoes_long empty.

    Returns (allow, reason).
    """
    d = read_intel()
    if not d:
        return (False, "no_intel")
    if int(time.time()) - d.get("updated_at_ts", 0) > 300:
        return (False, "intel_stale")
    if d.get("vetoes_long"):
        return (False, "intel_veto:" + ",".join(d["vetoes_long"][:3]))
    if not d.get("boosts_long"):
        return (False, "no_boost")
    return (True, "intel_boost:" + ",".join(d["boosts_long"][:3]))


# ---------------------------------------------------------------------------
# Order placement (via bybit-mcp vault to keep keys off this daemon)
# ---------------------------------------------------------------------------
def _sign(payload: str) -> tuple[str, str]:
    ts_ms = str(int(time.time() * 1000))
    sig = hmac.new(API_SEC.encode(), f"{ts_ms}{API_KEY}5000{payload}".encode(),
                   hashlib.sha256).hexdigest()
    return ts_ms, sig


def place_market(side: str, qty: float, mid: float, olid: str) -> dict:
    """Place a market order on Bybit demo. Refuses unless ENABLED."""
    if not ENABLED:
        return {"ok": False, "blocked": "SYGNIF_SFP_TRADER_ENABLED != 1"}
    if DRY_RUN:
        print(f"  DRY {side} qty={qty} mid=${mid:.0f} olid={olid}", flush=True)
        return {"ok": True, "dry_run": True}
    # Real fire path — TP/SL set as part of the place_order
    tp = mid * (1 + SFP_TP_PCT) if side == "Buy" else mid * (1 - SFP_TP_PCT)
    sl = mid * (1 - SFP_SL_PCT) if side == "Buy" else mid * (1 + SFP_SL_PCT)
    body = {
        "category":    "linear",
        "symbol":      SYMBOL,
        "side":        side,
        "orderType":   "Market",
        "qty":         str(qty),
        "orderLinkId": olid,
        "takeProfit":  f"{tp:.2f}",
        "stopLoss":    f"{sl:.2f}",
        "tpslMode":    "Full",
        "reduceOnly":  False,
    }
    payload = json.dumps(body)
    ts_ms, sig = _sign(payload)
    req = urllib.request.Request(f"{API_BASE}/v5/order/create", method="POST",
        data=payload.encode(),
        headers={"X-BAPI-API-KEY": API_KEY, "X-BAPI-SIGN": sig,
                 "X-BAPI-TIMESTAMP": ts_ms, "X-BAPI-RECV-WINDOW": "5000",
                 "Content-Type": "application/json"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=10).read())
    except Exception as e:
        return {"ok": False, "err": f"{type(e).__name__}: {e}"}
    return {"ok": r.get("retCode") == 0, "result": r}


# ---------------------------------------------------------------------------
# Fire path
# ---------------------------------------------------------------------------
def fire_trade(direction: str, mid: float, sig_meta: dict) -> dict:
    """Open an SFP-priority perp. Checks mutex + intel + enabled flag."""
    # Hard kill-switch
    if not ENABLED:
        print(f"  [SFP] FIRE BLOCKED: SYGNIF_SFP_TRADER_ENABLED != 1", flush=True)
        return {"ok": False, "blocked": "disabled"}

    # Mutex: refuse if any other strategy has an open position
    claim = read_claim()
    if has_other_owner_open(claim):
        print(f"  [SFP] FIRE BLOCKED: another strategy holds an open trade", flush=True)
        return {"ok": False, "blocked": "other_strategy_open"}

    # Cooldown
    now = time.time()
    if now - state["last_fire_ts"] < COOLDOWN_S:
        return {"ok": False, "blocked": "cooldown"}

    # Intel gate
    allow, reason = intel_allows_long() if direction == "long" else (False, "short_disabled")
    if not allow:
        print(f"  [SFP] FIRE BLOCKED: {reason}", flush=True)
        return {"ok": False, "blocked": reason}

    # Build qty for risk
    sl_distance = mid * SFP_SL_PCT
    if sl_distance <= 0: return {"ok": False, "err": "sl_zero"}
    qty = round(SFP_RISK_USD / sl_distance, 4)
    if qty <= 0: return {"ok": False, "err": "qty_zero"}

    cid = uuid.uuid4().hex
    olid = f"{ORDER_PREFIX}{cid[:14].replace('-', '')}"
    side = "Buy" if direction == "long" else "Sell"

    if not acquire_claim(SYMBOL, side, olid):
        return {"ok": False, "blocked": "claim_lost"}

    r = place_market(side, qty, mid, olid)
    if r.get("ok"):
        state["last_fire_ts"] = now
        state["n_fires"] += 1
        state["n_open_sfp"] += 1
        print(f"  [SFP] FIRED {direction} qty={qty} mid=${mid:.0f} olid={olid} intel={reason}",
              flush=True)
    return r


# ---------------------------------------------------------------------------
# WS handlers
# ---------------------------------------------------------------------------
def on_open(ws):
    state["ws_status"] = "connected"
    print(f"[SFP] WS connected, subscribing to kline.1.{SYMBOL}", flush=True)
    ws.send(json.dumps({"op": "subscribe", "args": [f"kline.1.{SYMBOL}"]}))


def on_message(ws, message):
    try:
        msg = json.loads(message)
    except json.JSONDecodeError:
        return
    state["last_msg_ts"] = time.time()
    if msg.get("op") == "subscribe": return
    topic = msg.get("topic", "")
    data = msg.get("data") or []
    if not topic.startswith("kline."): return
    if not isinstance(data, list): return

    for k in data:
        try:
            bar = {
                "ts_ms_open": int(k.get("start") or 0),
                "open":   float(k.get("open") or 0),
                "high":   float(k.get("high") or 0),
                "low":    float(k.get("low") or 0),
                "close":  float(k.get("close") or 0),
                "volume": float(k.get("volume") or 0),
                "confirm": bool(k.get("confirm") or False),
            }
        except (ValueError, TypeError):
            continue
        if not bar["confirm"]:
            continue
        state["n_kline_events"] += 1
        # Jules' FibSfpState.evaluate() takes the bar directly. Returns None
        # or a fire payload {direction, trigger, mid, meta}. Internal dedup
        # via _last_fire_ts_long/short — no separate mark_fired() needed.
        payload = state["fib"].evaluate(bar)
        if payload is None:
            continue
        direction = payload["direction"]
        # v1: only fire long. Bears are flat-EV per PR #15 backtest.
        if direction != "long":
            print(f"  [SFP] skipping {direction} signal — only longs enabled in v1",
                  flush=True)
            continue
        fire_trade(direction, payload["mid"], payload["meta"])


def on_error(ws, error):
    print(f"[SFP] WS error: {error}", file=sys.stderr)


def on_close(ws, code, reason):
    state["ws_status"] = "disconnected"
    print(f"[SFP] WS closed code={code} reason={reason}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Status loop
# ---------------------------------------------------------------------------
def status_loop():
    while _running:
        try:
            uptime = time.time() - state["started_at"]
            cur, target = state["fib"].warmup_progress()
            print(f"  [SFP] stats: enabled={ENABLED} ws={state['ws_status']} "
                  f"klines={state['n_kline_events']} fires={state['n_fires']} "
                  f"bars_buffered={cur}/{target} "
                  f"ready={cur >= target} uptime={uptime:.0f}s",
                  flush=True)
        except Exception as e:
            print(f"  status err: {e}", file=sys.stderr)
        time.sleep(60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def _sigterm(sig, frame):
    global _running
    _running = False
    print("  signal received, shutting down", flush=True)


def main():
    if not API_KEY or not API_SEC:
        print("FATAL: BYBIT_API_KEY / BYBIT_API_SECRET not set", file=sys.stderr)
        sys.exit(2)

    print(f"=== sygnif_sfp_trader started @ {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ===")
    print(f"  ENABLED:        {ENABLED}   (SYGNIF_SFP_TRADER_ENABLED)")
    print(f"  DRY_RUN:        {DRY_RUN}")
    print(f"  SYMBOL:         {SYMBOL}")
    print(f"  PREFIX:         {ORDER_PREFIX}")
    print(f"  TP/SL:          +{SFP_TP_PCT*100:.2f}% / -{SFP_SL_PCT*100:.2f}%")
    print(f"  Risk USD:       ${SFP_RISK_USD}")
    print(f"  Cooldown:       {COOLDOWN_S}s")
    print(f"  Max open:       {MAX_OPEN_SFP}")
    if not ENABLED:
        print(f"  [scaffold mode] daemon will track signals but refuse to fire")

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT,  _sigterm)

    threading.Thread(target=status_loop, daemon=True).start()

    while _running:
        try:
            ws = websocket.WebSocketApp(WS_URL,
                on_open=on_open, on_message=on_message,
                on_error=on_error, on_close=on_close)
            ws.run_forever(ping_interval=20, ping_timeout=10, ping_payload="ping")
        except Exception as e:
            print(f"[SFP] outer ws err: {e}", file=sys.stderr)
        if _running:
            time.sleep(5)


if __name__ == "__main__":
    main()
