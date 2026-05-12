#!/usr/bin/env python3
"""sygnif_trailing_daemon.py - REAL-TIME trailing-stop manager.

Replaces the 20s-timer-driven sygnif_trailing_manager.py with a long-running
daemon that:

  1. Subscribes to Bybit public WebSocket (mark price ticker for BTCUSDT)
     -> reacts to every price tick (~100ms cadence).
  2. Polls /v5/position/list every 3s to detect new fills / closures
     and pick up any external SL/TP changes.
  3. On every tick: updates per-position high-water-mark in memory,
     evaluates ratchet, and (if condition met) POSTs /v5/position/trading-stop.
  4. Rate-limits REST writes: max 1 trading-stop call per position per
     RATCHET_COOLDOWN_S seconds (default 0.5s). Prevents Bybit throttling.

Why: the 20s timer means a fast 0.5% move can run past activation and back to
breakeven without ratcheting once. WebSocket + tight loop closes that gap to
~200ms (Bybit's tick cadence plus our REST RTT).

Compatibility: reads same env vars as the old manager
  SYGNIF_TRAIL_SYMBOL          (default BTCUSDT)
  SYGNIF_TRAIL_ACTIVATION_PCT  (default 0.5)
  SYGNIF_TRAIL_DISTANCE_PCT    (default 0.4)
  SYGNIF_TRAIL_INITIAL_SL_PCT  (default 0.30)
  SYGNIF_TRAIL_PREFIXES        (default sygSTND,sygBNCE,sygFAST)
  SYGNIF_TRAIL_DRY_RUN         (default 0)
  SYGNIF_TRAIL_COOLDOWN_S      (default 0.5)
  SYGNIF_TRAIL_POSITION_POLL_S (default 3)
  BYBIT_DEMO_API_KEY / SECRET
  BYBIT_DEMO_API_BASE          (default api-demo.bybit.com)
  SYGNIF_TRAIL_WS_URL          (default wss://stream.bybit.com/v5/public/linear)

State persistence: dumps to /var/lib/sygnif/trailing_state.json every 30s
so a restart picks up the existing HWM without losing the trail.
"""
from __future__ import annotations

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

import websocket  # websocket-client


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
def _load_env(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
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
RATCHET_COOLDOWN_S = float(os.environ.get("SYGNIF_TRAIL_COOLDOWN_S", "0.5"))
POSITION_POLL_S  = float(os.environ.get("SYGNIF_TRAIL_POSITION_POLL_S", "3"))
PREFIXES         = tuple(os.environ.get("SYGNIF_TRAIL_PREFIXES",
                                          "sygSTND,sygBNCE,sygFAST").split(","))

STATE_FILE       = pathlib.Path("/var/lib/sygnif/trailing_state.json")
STATE_FLUSH_S    = 30.0  # dump every 30s

API_BASE = (os.environ.get("BYBIT_DEMO_API_BASE")
            or "https://api-demo.bybit.com").rstrip("/")
API_KEY  = (os.environ.get("BYBIT_DEMO_API_KEY")
            or os.environ.get("BYBIT_API_KEY"))
API_SEC  = (os.environ.get("BYBIT_DEMO_API_SECRET")
            or os.environ.get("BYBIT_API_SECRET"))

WS_URL   = os.environ.get("SYGNIF_TRAIL_WS_URL",
                          "wss://stream.bybit.com/v5/public/linear")


# ---------------------------------------------------------------------------
# Bybit REST helpers (signed)
# ---------------------------------------------------------------------------
def _sign(payload: str) -> tuple[str, str]:
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
        headers={"X-BAPI-API-KEY": API_KEY, "X-BAPI-SIGN": sig,
                  "X-BAPI-SIGN-TYPE": "2", "X-BAPI-TIMESTAMP": ts,
                  "X-BAPI-RECV-WINDOW": "5000"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=8).read())
    except Exception as e:
        return {"retCode": -1, "retMsg": f"{type(e).__name__}: {e}"}


def signed_post(path: str, body: dict) -> dict:
    body_str = json.dumps(body)
    ts, sig = _sign(body_str)
    req = urllib.request.Request(
        f"{API_BASE}{path}", data=body_str.encode(), method="POST",
        headers={"Content-Type": "application/json",
                  "X-BAPI-API-KEY": API_KEY, "X-BAPI-SIGN": sig,
                  "X-BAPI-SIGN-TYPE": "2", "X-BAPI-TIMESTAMP": ts,
                  "X-BAPI-RECV-WINDOW": "5000"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=8).read())
    except Exception as e:
        return {"retCode": -1, "retMsg": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# Shared state (guarded by _state_lock)
# ---------------------------------------------------------------------------
_state_lock = threading.RLock()
_running = True

# positions[pkey] = {
#   side, entry, size, position_idx, cur_sl, cur_tp, hwm,
#   last_mark, last_ratchet_ts, tp_cleared, last_synced_at
# }
_positions: dict[str, dict] = {}
_latest_mark: float = 0.0
_latest_mark_ts: float = 0.0
_metrics = {"ticks": 0, "ratchets": 0, "ratchet_calls": 0,
             "ratchet_skips_cooldown": 0, "ws_reconnects": 0,
             "started_at": time.time()}


def _position_key(p: dict) -> str:
    """Stable per-position key. avgPrice + side identifies a unique entry."""
    return f"{p.get('symbol')}|{p.get('side')}|{p.get('avgPrice')}"


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------
def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        with STATE_FILE.open() as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state() -> None:
    with _state_lock:
        snapshot = {
            "positions": _positions,
            "metrics":   _metrics,
            "latest_mark": _latest_mark,
            "saved_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(snapshot, f, indent=2, default=str)
    os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------------------------
# Position polling thread (every POSITION_POLL_S seconds)
# ---------------------------------------------------------------------------
def position_poll_loop() -> None:
    while _running:
        try:
            r = signed_get("/v5/position/list",
                           {"category": "linear", "symbol": SYMBOL})
            if r.get("retCode") == 0:
                live = [p for p in (r.get("result", {}).get("list") or [])
                        if float(p.get("size", "0") or "0") > 0]
                _sync_positions(live)
            else:
                print(f"  position/list failed retCode={r.get('retCode')} "
                      f"msg={r.get('retMsg','')[:120]}", file=sys.stderr)
        except Exception as e:
            print(f"  position poll error: {type(e).__name__}: {e}",
                  file=sys.stderr)
        time.sleep(POSITION_POLL_S)


def _sync_positions(live: list[dict]) -> None:
    """Reconcile live position list with in-memory state."""
    now = time.time()
    live_keys: set[str] = set()
    with _state_lock:
        for p in live:
            pkey = _position_key(p)
            live_keys.add(pkey)
            side = p.get("side")
            entry = float(p.get("avgPrice", "0") or 0)
            mark  = float(p.get("markPrice", "0") or 0)
            cur_sl = float(p.get("stopLoss", "0") or 0) or None
            cur_tp = float(p.get("takeProfit", "0") or 0) or None
            size  = float(p.get("size", "0") or 0)
            pos_idx = int(p.get("positionIdx", 0))

            existing = _positions.get(pkey)
            if existing is None:
                # New position seen
                _positions[pkey] = {
                    "symbol":       p.get("symbol"),
                    "side":         side,
                    "entry":        entry,
                    "size":         size,
                    "position_idx": pos_idx,
                    "cur_sl":       cur_sl,
                    "cur_tp":       cur_tp,
                    "hwm":          mark if mark > 0 else entry,
                    "last_mark":    mark,
                    "last_ratchet_ts": 0.0,
                    "tp_cleared":   cur_tp is None,
                    "last_synced_at": now,
                    "created_at":   now,
                }
                print(f"  [SYNC] new position: {side} entry=${entry:.1f} "
                      f"size={size} sl=${cur_sl} tp=${cur_tp}")
            else:
                # Update REST-driven fields
                existing["cur_sl"]        = cur_sl
                existing["cur_tp"]        = cur_tp
                existing["size"]          = size
                existing["position_idx"]  = pos_idx
                existing["last_synced_at"] = now
                if cur_tp is None and not existing.get("tp_cleared"):
                    existing["tp_cleared"] = True

        # GC closed positions
        gone = set(_positions.keys()) - live_keys
        for k in gone:
            print(f"  [SYNC] closed: {k}")
            _positions.pop(k, None)


# ---------------------------------------------------------------------------
# Ratchet logic
# ---------------------------------------------------------------------------
def _compute_trail_sl(side: str, entry: float, mark: float,
                       hwm: float, cur_sl: float | None
                       ) -> tuple[float | None, str]:
    if entry <= 0 or mark <= 0:
        return (None, "no-data")
    is_long = side == "Buy"
    move_pct = ((mark - entry) / entry if is_long
                else (entry - mark) / entry)
    if move_pct < ACTIVATION_PCT:
        return (None, f"not_activated (move {move_pct*100:+.2f}%)")
    # Bybit min SL distance from mark: 0.05% (avoids instant-trigger reject)
    MIN_BUFFER = 0.0005
    if is_long:
        new_sl = hwm * (1 - TRAIL_PCT)
        # Cap SL to at most (mark - MIN_BUFFER) so Bybit accepts it
        max_allowed = mark * (1 - MIN_BUFFER)
        if new_sl > max_allowed:
            new_sl = max_allowed
        # Ratchet only: never lower an existing SL
        if cur_sl is not None and new_sl <= cur_sl + 1.0:  # $1 hysteresis on BTC
            return (None, f"no_ratchet new=${new_sl:.1f} cur=${cur_sl:.1f}")
    else:
        new_sl = hwm * (1 + TRAIL_PCT)
        min_allowed = mark * (1 + MIN_BUFFER)
        if new_sl < min_allowed:
            new_sl = min_allowed
        if cur_sl is not None and new_sl >= cur_sl - 1.0:
            return (None, f"no_ratchet new=${new_sl:.1f} cur=${cur_sl:.1f}")
    return (round(new_sl, 1), f"trail to ${new_sl:.1f}")


def _apply_trail(pos: dict, new_sl: float, clear_tp: bool) -> dict:
    body = {
        "category":     "linear",
        "symbol":       pos["symbol"],
        "positionIdx":  pos["position_idx"],
        "stopLoss":     str(new_sl),
        "slTriggerBy":  "LastPrice",
    }
    if clear_tp:
        body["takeProfit"] = "0"
    return signed_post("/v5/position/trading-stop", body)


def _maybe_ratchet(pkey: str, pos: dict, mark: float) -> None:
    """Update HWM and ratchet if conditions are met. Called under _state_lock."""
    side  = pos["side"]
    entry = pos["entry"]
    if entry <= 0:
        return
    # Update HWM
    if side == "Buy":
        pos["hwm"] = max(pos.get("hwm", mark), mark)
    else:
        pos["hwm"] = min(pos.get("hwm", mark), mark)
    pos["last_mark"] = mark

    new_sl, _ = _compute_trail_sl(side, entry, mark, pos["hwm"], pos.get("cur_sl"))
    if new_sl is None:
        return

    now = time.time()
    if now - pos.get("last_ratchet_ts", 0) < RATCHET_COOLDOWN_S:
        _metrics["ratchet_skips_cooldown"] += 1
        return

    clear_tp = (pos.get("cur_tp") is not None
                and not pos.get("tp_cleared", False))

    if DRY_RUN:
        print(f"  [DRY] {side} entry=${entry:.0f} mark=${mark:.0f} "
              f"hwm=${pos['hwm']:.0f} would set SL=${new_sl:.1f} "
              f"clear_tp={clear_tp}")
        pos["last_ratchet_ts"] = now
        return

    out = _apply_trail(pos, new_sl, clear_tp)
    _metrics["ratchet_calls"] += 1
    pos["last_ratchet_ts"] = now

    if out.get("retCode") == 0:
        _metrics["ratchets"] += 1
        pos["cur_sl"] = new_sl
        if clear_tp:
            pos["tp_cleared"] = True
            pos["cur_tp"] = None
        move_pct = (((mark - entry) / entry) * 100 if side == "Buy"
                    else ((entry - mark) / entry) * 100)
        print(f"  [RATCHET] {side} entry=${entry:.0f} mark=${mark:.0f} "
              f"hwm=${pos['hwm']:.0f} move={move_pct:+.2f}% "
              f"SL=${new_sl:.1f}"
              + (" + TP cleared" if clear_tp else ""))
    else:
        rc = out.get("retCode")
        msg = (out.get("retMsg") or "")[:160]
        print(f"  [RATCHET FAIL] retCode={rc} msg={msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# WebSocket tick handler
# ---------------------------------------------------------------------------
def _on_message(ws, message: str) -> None:
    global _latest_mark, _latest_mark_ts
    try:
        m = json.loads(message)
    except json.JSONDecodeError:
        return
    if m.get("op") == "subscribe":
        print(f"  WS subscribed: {m.get('args')} success={m.get('success')}")
        return
    if m.get("topic") != f"tickers.{SYMBOL}":
        return
    data = m.get("data") or {}
    # Bybit v5 tickers comes as a single dict or list depending on snapshot
    if isinstance(data, list):
        data = data[0] if data else {}
    mark_s = data.get("markPrice") or data.get("lastPrice")
    if not mark_s:
        return
    try:
        mark = float(mark_s)
    except (ValueError, TypeError):
        return
    if mark <= 0:
        return

    _metrics["ticks"] += 1
    with _state_lock:
        _latest_mark = mark
        _latest_mark_ts = time.time()
        for pkey, pos in list(_positions.items()):
            _maybe_ratchet(pkey, pos, mark)


def _on_open(ws) -> None:
    sub = {"op": "subscribe", "args": [f"tickers.{SYMBOL}"]}
    ws.send(json.dumps(sub))
    print(f"  WS open, sent subscribe for tickers.{SYMBOL}")


def _on_error(ws, error) -> None:
    print(f"  WS error: {type(error).__name__}: {error}", file=sys.stderr)


def _on_close(ws, status_code, msg) -> None:
    print(f"  WS closed status={status_code} msg={msg}")


def ws_loop() -> None:
    while _running:
        try:
            print(f"  WS connecting: {WS_URL}")
            ws = websocket.WebSocketApp(
                WS_URL,
                on_open=_on_open,
                on_message=_on_message,
                on_error=_on_error,
                on_close=_on_close,
            )
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as e:
            print(f"  WS run_forever crash: {type(e).__name__}: {e}",
                  file=sys.stderr)
        if _running:
            _metrics["ws_reconnects"] += 1
            print(f"  WS reconnect in 3s (count={_metrics['ws_reconnects']})")
            time.sleep(3)


# ---------------------------------------------------------------------------
# Heartbeat thread (logs every 30s)
# ---------------------------------------------------------------------------
def heartbeat_loop() -> None:
    last_flush = 0.0
    while _running:
        time.sleep(15)
        with _state_lock:
            mark = _latest_mark
            age = time.time() - _latest_mark_ts if _latest_mark_ts else 9999
            nrun = len(_positions)
            m = dict(_metrics)
            tick_rate = (_metrics["ticks"] / max(1, time.time() - _metrics["started_at"]))
            pos_summary = []
            for pkey, pos in _positions.items():
                pos_summary.append(
                    f"{pos['side']} entry=${pos['entry']:.0f} "
                    f"mark=${pos.get('last_mark', 0):.0f} "
                    f"hwm=${pos.get('hwm', 0):.0f} "
                    f"sl=${pos.get('cur_sl')}")
        print(f"  [HB] mark=${mark:.1f} (age={age:.1f}s) "
              f"positions={nrun} "
              f"ticks={m['ticks']} ({tick_rate:.1f}/s) "
              f"ratchets={m['ratchets']}/{m['ratchet_calls']} calls "
              f"ws_rc={m['ws_reconnects']}")
        for s in pos_summary:
            print(f"    · {s}")
        if time.time() - last_flush >= STATE_FLUSH_S:
            try:
                _save_state()
            except Exception as e:
                print(f"  state save error: {type(e).__name__}: {e}",
                      file=sys.stderr)
            last_flush = time.time()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    global _running
    if not API_KEY or not API_SEC:
        print("FATAL: BYBIT_DEMO_API_KEY / SECRET missing", file=sys.stderr)
        return 1
    print(f"=== trailing_daemon started @ "
          f"{dt.datetime.now(dt.timezone.utc).isoformat()} ===")
    print(f"  symbol={SYMBOL} activation={ACTIVATION_PCT*100:.2f}% "
          f"trail={TRAIL_PCT*100:.2f}% cooldown={RATCHET_COOLDOWN_S}s "
          f"poll={POSITION_POLL_S}s dry={DRY_RUN}")
    print(f"  api_base={API_BASE}")
    print(f"  ws_url={WS_URL}")

    # Restore previous state (HWM continuity)
    prev = _load_state()
    if prev.get("positions"):
        with _state_lock:
            _positions.update(prev["positions"])
        print(f"  restored {len(prev['positions'])} positions from state file")

    def _sigterm(sig, frame):
        global _running
        print(f"  signal {sig}, shutting down")
        _running = False

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT,  _sigterm)

    # Start threads
    t_poll = threading.Thread(target=position_poll_loop,
                               name="position-poll", daemon=True)
    t_hb   = threading.Thread(target=heartbeat_loop,
                               name="heartbeat", daemon=True)
    t_poll.start()
    t_hb.start()

    # WS runs in main thread (blocks until shutdown)
    try:
        ws_loop()
    except KeyboardInterrupt:
        _running = False

    print("  shutting down, flushing state")
    try:
        _save_state()
    except Exception as e:
        print(f"  final save error: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
