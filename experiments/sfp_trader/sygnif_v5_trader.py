#!/usr/bin/env python3
"""sygnif_v5_trader — sole perp opener using the v5 fib-SR signal.

Wires the proven v5 backtest signal (5m fib_0.618 + SFP + RSI + RSRS +
FVG-as-TP) to live Bybit demo orders. Both LONG (v5 proven) and SHORT
(structural mirror — NOT backtested).

Architecture (per the 2026-05-13 design doc — swarm + brain are
PERIPHERAL, never gate the signal):

  [Bybit kline.5.BTCUSDT WS]
       ↓ closed 5m bars
  [FibSrV5State long + FibSrV5StateShort]
       ↓ payload (or None)
  [Mutex / cooldown / intel context]
       ↓ allow
  [Bybit demo place-order: market entry + TP + SL]
       ↓ on success
  [Swarm publisher: forecast row + trade.open]
  [Brain publisher: POST /api/input/text with thesis]

Heartbeat (every 5 minutes): trader.heartbeat row to swarm with
current state, position, intel snapshot, last-fire timestamps.

Kill-switches (defense in depth):
  SYGNIF_V5_TRADER_ENABLED=0      hard kill (no orders, signals logged)
  SYGNIF_V5_DRY_RUN=1             order-shape logging only, no API call
  SYGNIF_V5_ALLOW_SHORT=0         disable mirror short side
  SYGNIF_V5_SIGNALS_TO_SWARM_ONLY=1   record-only mode (no orders)

Risk:
  Equity ~ $2k demo. $10 risk per trade @ 0.25% SL = ~$4k notional.
  Position size = SYGNIF_V5_RISK_USD / (SL_PCT * mid_price).
  Leverage tier: 2-3x sufficient.

Sources:
  - experiments/sfp_trader/fib_sr_v5_trigger.py (proven backtest signal)
  - experiments/sfp_trader/sygnif_sfp_trader.py (daemon scaffold pattern)
  - SYGNIF.md §4.1 (priority routing) + §3.2 (leverage doctrine)
"""
from __future__ import annotations

import collections
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
import traceback
import urllib.parse
import urllib.request
from typing import Optional

# Load env files BEFORE importing the trigger (so config-side env vars apply)
def _load_env(path: str) -> None:
    if not os.path.exists(path): return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env("/etc/sygnif/trader.env")
_load_env("/etc/sygnif/v5-trader.env")

# Trigger code lives alongside this file on EC2 (/opt/sygnif-services/)
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from fib_sr_v5_trigger import FibSrV5State, FibSrV5StateShort  # noqa: E402

# ---------------------------------------------------------------------------
# Config (all env-driven, conservative defaults)
# ---------------------------------------------------------------------------
ENABLED         = os.environ.get("SYGNIF_V5_TRADER_ENABLED", "0") == "1"
DRY_RUN         = os.environ.get("SYGNIF_V5_DRY_RUN", "1") == "1"
ALLOW_SHORT     = os.environ.get("SYGNIF_V5_ALLOW_SHORT", "1") == "1"
SWARM_ONLY      = os.environ.get("SYGNIF_V5_SIGNALS_TO_SWARM_ONLY", "0") == "1"

SYMBOL          = os.environ.get("SYGNIF_V5_SYMBOL", "BTCUSDT")
TF_MIN          = int(os.environ.get("SYGNIF_V5_TF_MIN", "5"))  # 5m TF per backtest
RISK_USD        = float(os.environ.get("SYGNIF_V5_RISK_USD", "10"))    # $10 = 0.5% of $2k
LEVERAGE        = int(os.environ.get("SYGNIF_V5_LEVERAGE", "3"))
SL_PCT          = float(os.environ.get("SYGNIF_V5_SL_PCT", "0.25")) / 100  # 0.25%
COOLDOWN_S      = int(os.environ.get("SYGNIF_V5_COOLDOWN_S", "1800"))  # 30min per direction
HEARTBEAT_S     = int(os.environ.get("SYGNIF_V5_HEARTBEAT_S", "300"))  # 5min
MAX_OPEN        = int(os.environ.get("SYGNIF_V5_MAX_OPEN", "1"))

ORDER_PREFIX_L  = "sygV5L"
ORDER_PREFIX_S  = "sygV5S"

# Paths (EC2 standard)
DB_PATH         = pathlib.Path("/var/lib/sygnif/swarm.db")
INTEL_FILE      = pathlib.Path("/var/lib/sygnif/intel_summary.json")
STRATEGY_CLAIM_FILE = pathlib.Path("/var/lib/sygnif/strategy_claim.json")
BRAIN_URL       = os.environ.get("SYGNIF_BRAIN_URL", "http://localhost:8889")

# Bybit
API_BASE = (os.environ.get("BYBIT_DEMO_API_BASE")
            or "https://api-demo.bybit.com").rstrip("/")
API_KEY  = (os.environ.get("BYBIT_DEMO_API_KEY")
            or os.environ.get("BYBIT_API_KEY"))
API_SEC  = (os.environ.get("BYBIT_DEMO_API_SECRET")
            or os.environ.get("BYBIT_API_SECRET"))

# WS topic
WS_URL          = "wss://stream.bybit.com/v5/public/linear"
WS_TOPIC        = f"kline.{TF_MIN}.{SYMBOL}"

# Bootstrap depth — fib_window=240 + RSRS_M=600 + warmup = 1000 bars
BOOTSTRAP_BARS  = int(os.environ.get("SYGNIF_V5_BOOTSTRAP_BARS", "1000"))

AGENT_ID        = "sygnif-v5-trader"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
state = {
    "lock":         threading.Lock(),
    "fib_long":     FibSrV5State(),
    "fib_short":    FibSrV5StateShort() if ALLOW_SHORT else None,
    "last_fire_ts_long":  0,
    "last_fire_ts_short": 0,
    "n_open":       0,
    "ws_status":    "starting",
    "started_at":   time.time(),
    "last_msg_ts":  0,
    "n_kline_events": 0,
    "n_fires":      0,
    "n_fires_long": 0,
    "n_fires_short": 0,
    "last_heartbeat": 0,
    "warmup_done":  False,
    "bootstrap_count": 0,
}

_running = True


# ---------------------------------------------------------------------------
# Swarm publisher — direct SQLite insert (we're on EC2 with file access)
# ---------------------------------------------------------------------------
def swarm_insert(topic: str, content: str, meta: Optional[dict] = None) -> bool:
    """Append a row to the master swarm.db. Idempotent on (created, topic, agent_id)."""
    try:
        meta_json = json.dumps(meta or {}, separators=(",", ":"))
        with sqlite3.connect(str(DB_PATH), timeout=2.0) as conn:
            conn.execute(
                "INSERT INTO swarm_entries (created, agent_id, topic, content, meta) "
                "VALUES (?, ?, ?, ?, ?)",
                (int(time.time()), AGENT_ID, topic, content, meta_json),
            )
            conn.commit()
        return True
    except Exception as e:
        print(f"  [swarm] insert error: {type(e).__name__}: {e}", flush=True)
        return False


# ---------------------------------------------------------------------------
# Brain publisher (Phase-1 text ingest, non-blocking)
# ---------------------------------------------------------------------------
def brain_post_text(text: str, source: str = "v5_trader", boost: float = 1.0) -> bool:
    """POST /api/input/text to NeuroLinked. Best-effort, short timeout."""
    try:
        body = json.dumps({
            "text": text, "source": source, "executive_boost": boost
        }).encode()
        req = urllib.request.Request(
            f"{BRAIN_URL}/api/input/text",
            method="POST", data=body,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=1.5).read()
        return True
    except Exception:
        # Brain offline shouldn't kill the trader
        return False


# ---------------------------------------------------------------------------
# Intel context (read-only — no gating for v5, just attach to meta)
# ---------------------------------------------------------------------------
_intel_cache = {"mtime": 0, "data": None}

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


def read_regime() -> Optional[str]:
    """Read the most recent regime classification from swarm."""
    try:
        with sqlite3.connect(str(DB_PATH), timeout=1.0) as conn:
            row = conn.execute(
                "SELECT content FROM swarm_entries WHERE topic='regime' "
                "ORDER BY created DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Strategy-claim mutex (read-only check — we respect other strategies)
# ---------------------------------------------------------------------------
def read_claim() -> dict:
    try:
        return json.loads(STRATEGY_CLAIM_FILE.read_text())
    except Exception:
        return {}


def has_other_owner_open(claim: dict) -> bool:
    """If another strategy has an open position, defer."""
    owner = claim.get("owner")
    state_ = claim.get("state")
    if owner and owner.startswith("sygV5"):
        return False  # our own claim
    return state_ == "open"


# ---------------------------------------------------------------------------
# Bybit V5 REST — HMAC-signed (matches SFP daemon pattern)
# ---------------------------------------------------------------------------
def _sign(payload: str) -> tuple[str, str]:
    ts_ms = str(int(time.time() * 1000))
    sig = hmac.new(
        API_SEC.encode(),
        f"{ts_ms}{API_KEY}5000{payload}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return ts_ms, sig


def fetch_klines(limit: int = 1000, end_ms: Optional[int] = None) -> list:
    """Bybit V5 kline (public endpoint, no auth)."""
    url = (
        f"{API_BASE.replace('api-demo', 'api')}/v5/market/kline"
        f"?category=linear&symbol={SYMBOL}&interval={TF_MIN}&limit={limit}"
    )
    if end_ms is not None:
        url += f"&end={end_ms}"
    req = urllib.request.Request(url, headers={"User-Agent": "sygnif-v5-trader/1.0"})
    r = json.loads(urllib.request.urlopen(req, timeout=10).read())
    if r.get("retCode") != 0:
        raise RuntimeError(f"kline fetch retCode={r.get('retCode')} msg={r.get('retMsg')}")
    rows = r["result"]["list"]
    out = []
    for row in sorted(rows, key=lambda b: int(b[0])):
        out.append({
            "ts_ms_open": int(row[0]),
            "open":   float(row[1]),
            "high":   float(row[2]),
            "low":    float(row[3]),
            "close":  float(row[4]),
            "volume": float(row[5]),
            "confirm": True,
        })
    return out


def place_market_with_tp_sl(side: str, qty: float, tp: float, sl: float,
                            olid: str) -> dict:
    """Place market entry with attached TP/SL. side='Buy'|'Sell'."""
    if not ENABLED:
        return {"ok": False, "blocked": "SYGNIF_V5_TRADER_ENABLED != 1"}
    if SWARM_ONLY:
        return {"ok": False, "blocked": "swarm_only_mode"}
    if DRY_RUN:
        print(f"  DRY side={side} qty={qty} tp=${tp:.2f} sl=${sl:.2f} olid={olid}",
              flush=True)
        return {"ok": True, "dry_run": True}
    if not API_KEY or not API_SEC:
        return {"ok": False, "err": "no_api_credentials"}

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
    req = urllib.request.Request(
        f"{API_BASE}/v5/order/create", method="POST",
        data=payload.encode(),
        headers={
            "X-BAPI-API-KEY":    API_KEY,
            "X-BAPI-SIGN":       sig,
            "X-BAPI-TIMESTAMP":  ts_ms,
            "X-BAPI-RECV-WINDOW": "5000",
            "Content-Type":      "application/json",
        },
    )
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=10).read())
    except Exception as e:
        return {"ok": False, "err": f"{type(e).__name__}: {e}"}
    return {"ok": r.get("retCode") == 0, "result": r}


# ---------------------------------------------------------------------------
# Fire path
# ---------------------------------------------------------------------------
def fire_trade(payload: dict) -> dict:
    direction = payload["direction"]
    mid       = float(payload["mid"])
    tp        = float(payload["tp"])
    sl        = float(payload["sl"])

    # Hard kill-switches
    if not ENABLED:
        print(f"  [v5] FIRE BLOCKED: SYGNIF_V5_TRADER_ENABLED != 1", flush=True)
        return {"ok": False, "blocked": "disabled"}

    # Mutex
    claim = read_claim()
    if has_other_owner_open(claim):
        print(f"  [v5] FIRE BLOCKED: another strategy holds an open trade", flush=True)
        return {"ok": False, "blocked": "other_strategy_open"}

    # Per-direction cooldown
    now = time.time()
    last_fire_key = "last_fire_ts_long" if direction == "long" else "last_fire_ts_short"
    if now - state[last_fire_key] < COOLDOWN_S:
        return {"ok": False, "blocked": "cooldown"}

    # Position size: RISK_USD / SL distance
    sl_distance = abs(mid - sl)
    if sl_distance <= 0:
        return {"ok": False, "err": "sl_zero"}
    qty = round(RISK_USD / sl_distance, 4)
    if qty <= 0:
        return {"ok": False, "err": "qty_zero"}

    side = "Buy" if direction == "long" else "Sell"
    prefix = ORDER_PREFIX_L if direction == "long" else ORDER_PREFIX_S
    olid = f"{prefix}-{int(now)}"

    print(f"  [v5] FIRING {direction.upper()} qty={qty} mid={mid:.2f} "
          f"tp={tp:.2f} sl={sl:.2f} olid={olid}", flush=True)
    result = place_market_with_tp_sl(side, qty, tp, sl, olid)
    if result.get("ok"):
        state[last_fire_key] = now
        state["n_fires"] += 1
        state[f"n_fires_{direction}"] += 1
        meta_for_publish = dict(payload.get("meta", {}))
        meta_for_publish.update({
            "side":   side, "qty": qty, "mid": mid,
            "tp":     tp, "sl": sl, "olid": olid,
            "regime": read_regime(),
            "intel":  read_intel(),
            "result": result.get("result"),
        })
        # Swarm: trade.open
        swarm_insert(
            "trade.open",
            f"v5 {direction} {SYMBOL} qty={qty} @${mid:.2f} -> tp ${tp:.2f} sl ${sl:.2f}",
            meta_for_publish,
        )
        # Brain: thesis text
        thesis = (
            f"V5 {direction.upper()} {SYMBOL} entry ${mid:.0f} "
            f"qty={qty} tp=${tp:.0f} sl=${sl:.0f} "
            f"rsi={meta_for_publish.get('rsi')} rsrs_z={meta_for_publish.get('rsrs_z')} "
            f"tp_type={meta_for_publish.get('tp_type')}"
        )
        brain_post_text(thesis, source="v5_trader", boost=1.2)
    return result


# ---------------------------------------------------------------------------
# Bar processing (called on each closed bar)
# ---------------------------------------------------------------------------
def process_bar(bar: dict, *, log_signals: bool = True) -> None:
    with state["lock"]:
        # Always feed BOTH states so internal warmup proceeds in parallel
        payload_long  = state["fib_long"].evaluate(bar)
        payload_short = state["fib_short"].evaluate(bar) if state["fib_short"] else None

    if payload_long:
        if log_signals:
            print(f"  [v5][LONG signal] {payload_long}", flush=True)
        # Always publish forecast row (additive observability)
        swarm_insert(
            "forecast",
            f"v5 LONG signal: tp_type={payload_long['meta'].get('tp_type')} "
            f"rsi={payload_long['meta'].get('rsi')} rsrs_z={payload_long['meta'].get('rsrs_z')}",
            payload_long,
        )
        if not SWARM_ONLY:
            fire_trade(payload_long)

    if payload_short and ALLOW_SHORT:
        if log_signals:
            print(f"  [v5][SHORT signal — MIRROR, untested] {payload_short}", flush=True)
        swarm_insert(
            "forecast",
            f"v5 SHORT (mirror) signal: tp_type={payload_short['meta'].get('tp_type')} "
            f"rsi={payload_short['meta'].get('rsi')} rsrs_z={payload_short['meta'].get('rsrs_z')}",
            payload_short,
        )
        if not SWARM_ONLY:
            fire_trade(payload_short)


# ---------------------------------------------------------------------------
# Bootstrap with historical bars
# ---------------------------------------------------------------------------
def bootstrap_history():
    print(f"  bootstrapping {BOOTSTRAP_BARS} historical {TF_MIN}m bars for {SYMBOL}...",
          flush=True)
    all_bars = []
    end_ms = int(time.time() * 1000)
    while len(all_bars) < BOOTSTRAP_BARS:
        batch = fetch_klines(limit=1000, end_ms=end_ms)
        if not batch:
            break
        all_bars = batch + all_bars
        # de-dup by ts
        seen = set(); deduped = []
        for b in all_bars:
            if b["ts_ms_open"] not in seen:
                seen.add(b["ts_ms_open"])
                deduped.append(b)
        all_bars = sorted(deduped, key=lambda b: b["ts_ms_open"])
        end_ms = int(all_bars[0]["ts_ms_open"]) - 60_000
        if len(batch) < 1000:
            break
    # Feed in chronological order — exclude the most recent (in-progress) bar
    all_bars = all_bars[:-1] if all_bars else all_bars
    print(f"  fed {len(all_bars)} historical bars to warmup", flush=True)
    for b in all_bars:
        process_bar(b, log_signals=False)
    state["bootstrap_count"] = len(all_bars)
    state["warmup_done"] = True


# ---------------------------------------------------------------------------
# Heartbeat to swarm
# ---------------------------------------------------------------------------
def emit_heartbeat():
    intel = read_intel() or {}
    regime = read_regime()
    body = {
        "agent":     AGENT_ID,
        "version":   "v5",
        "symbol":    SYMBOL,
        "tf_min":    TF_MIN,
        "enabled":   ENABLED,
        "dry_run":   DRY_RUN,
        "swarm_only": SWARM_ONLY,
        "allow_short": ALLOW_SHORT,
        "warmup_done": state["warmup_done"],
        "bootstrap_count": state["bootstrap_count"],
        "n_kline_events": state["n_kline_events"],
        "n_fires":   state["n_fires"],
        "n_fires_long":  state["n_fires_long"],
        "n_fires_short": state["n_fires_short"],
        "uptime_s":  int(time.time() - state["started_at"]),
        "last_msg_age_s": int(time.time() - state["last_msg_ts"]) if state["last_msg_ts"] else None,
        "regime":    regime,
        "intel_updated_age_s": int(time.time() - intel.get("updated_at_ts", 0))
                              if intel.get("updated_at_ts") else None,
    }
    swarm_insert("trader.heartbeat", f"v5 trader heartbeat: {state['n_fires']} fires", body)
    state["last_heartbeat"] = time.time()


# ---------------------------------------------------------------------------
# WebSocket loop
# ---------------------------------------------------------------------------
def on_open(ws):
    state["ws_status"] = "open"
    print(f"  WS opened, subscribing to {WS_TOPIC}", flush=True)
    ws.send(json.dumps({"op": "subscribe", "args": [WS_TOPIC]}))


def on_message(ws, message):
    try:
        m = json.loads(message)
    except Exception:
        return
    state["last_msg_ts"] = time.time()
    if "topic" not in m or "data" not in m:
        return
    if not m["topic"].startswith("kline."):
        return
    for k in m["data"]:
        if not k.get("confirm"):
            continue  # only act on closed bars
        bar = {
            "ts_ms_open": int(k["start"]),
            "open":   float(k["open"]),
            "high":   float(k["high"]),
            "low":    float(k["low"]),
            "close":  float(k["close"]),
            "volume": float(k["volume"]),
            "confirm": True,
        }
        state["n_kline_events"] += 1
        process_bar(bar)
        # Heartbeat cadence — emit every HEARTBEAT_S
        if time.time() - state["last_heartbeat"] >= HEARTBEAT_S:
            try: emit_heartbeat()
            except Exception as e:
                print(f"  heartbeat err: {type(e).__name__}: {e}", flush=True)


def on_error(ws, error):
    state["ws_status"] = f"error:{error}"
    print(f"  WS error: {error}", flush=True)


def on_close(ws, code, reason):
    state["ws_status"] = f"closed:{code}:{reason}"
    print(f"  WS closed code={code} reason={reason}", flush=True)


def status_loop():
    """Print a console status every 30s so journalctl shows liveness."""
    while _running:
        time.sleep(30)
        with state["lock"]:
            print(f"  [status] ws={state['ws_status']} "
                  f"warmup={state['warmup_done']} "
                  f"bars_seen={state['n_kline_events']} "
                  f"fires={state['n_fires']}(L{state['n_fires_long']}/S{state['n_fires_short']}) "
                  f"uptime={int(time.time()-state['started_at'])}s",
                  flush=True)


def _sigterm(sig, frame):
    global _running
    _running = False
    print("  signal received, shutting down", flush=True)


def main():
    print(f"=== sygnif-v5-trader starting ===", flush=True)
    print(f"  ENABLED={ENABLED} DRY_RUN={DRY_RUN} SWARM_ONLY={SWARM_ONLY}", flush=True)
    print(f"  ALLOW_SHORT={ALLOW_SHORT} SYMBOL={SYMBOL} TF={TF_MIN}m", flush=True)
    print(f"  RISK_USD=${RISK_USD} SL={SL_PCT*100:.2f}% LEV={LEVERAGE}x", flush=True)
    print(f"  COOLDOWN={COOLDOWN_S}s HEARTBEAT={HEARTBEAT_S}s", flush=True)
    print(f"  API_BASE={API_BASE}  HAS_KEY={'Y' if API_KEY else 'N'}", flush=True)

    if not API_KEY or not API_SEC:
        print(f"  ! BYBIT API credentials missing — orders will fail", flush=True)

    # Emit initial heartbeat so swarm knows we're alive
    swarm_insert("trader.heartbeat",
                 f"v5 trader booting: ENABLED={ENABLED} DRY_RUN={DRY_RUN}",
                 {"agent": AGENT_ID, "phase": "boot"})

    # Bootstrap historical bars
    try:
        bootstrap_history()
    except Exception as e:
        print(f"  ! bootstrap failed: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        return 1

    emit_heartbeat()

    # Start status loop
    t = threading.Thread(target=status_loop, daemon=True)
    t.start()

    # Connect WS — lazy import to keep daemon importable without websocket-client
    try:
        import websocket  # type: ignore
    except ImportError:
        print(f"  ! python websocket-client not installed; "
              f"falling back to polling", flush=True)
        polling_loop()
        return 0

    while _running:
        try:
            ws = websocket.WebSocketApp(
                WS_URL,
                on_open=on_open, on_message=on_message,
                on_error=on_error, on_close=on_close,
            )
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as e:
            print(f"  WS run_forever err: {type(e).__name__}: {e}", flush=True)
        if not _running:
            break
        print("  reconnecting in 5s...", flush=True)
        time.sleep(5)
    return 0


def polling_loop():
    """Fallback when websocket-client is unavailable. Polls every 60s."""
    last_ts = None
    while _running:
        try:
            bars = fetch_klines(limit=5)
            # Find most recent confirmed closed bar (not the in-progress one)
            now_bucket_ms = (int(time.time() * 1000) // (TF_MIN * 60_000)) * (TF_MIN * 60_000)
            closed = [b for b in bars if b["ts_ms_open"] < now_bucket_ms]
            if closed:
                new_bar = closed[-1]
                if last_ts is None or new_bar["ts_ms_open"] > last_ts:
                    last_ts = new_bar["ts_ms_open"]
                    state["n_kline_events"] += 1
                    state["last_msg_ts"] = time.time()
                    process_bar(new_bar)
            if time.time() - state["last_heartbeat"] >= HEARTBEAT_S:
                emit_heartbeat()
        except Exception as e:
            print(f"  poll err: {type(e).__name__}: {e}", flush=True)
        time.sleep(60)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT,  _sigterm)
    sys.exit(main())
