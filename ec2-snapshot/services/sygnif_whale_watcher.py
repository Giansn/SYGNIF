#!/usr/bin/env python3
"""sygnif_whale_watcher.py — long-lived WS watcher for large-trade flow.

Subscribes to Bybit V5 publicTrade.BTCUSDT, maintains a 15-min rolling
window of WHALE trades (notional ≥ SYGNIF_WHALE_NOTIONAL_USD, default
$250k), and writes a summary JSON file every flush_interval that
decision_snapshot reads at trade-decision time.

Output:
  /var/lib/sygnif/whale_flow.json    rolling window summary
  /var/lib/sygnif/whale_flow.ndjson  per-whale-trade audit log (rotated daily)

Summary shape:
  {
    "updated_utc":              "2026-05-10T20:55:00Z",
    "window_minutes":           15,
    "whale_threshold_usd":      250000,
    "n_whale_trades":           42,
    "whale_buy_notional_usd":   8_300_000,
    "whale_sell_notional_usd":  2_100_000,
    "whale_imbalance":          0.798,        # buys / (buys+sells), 0.5 = balanced
    "n_large_buys":             34,
    "n_large_sells":            8,
    "largest_buy_usd":          1_250_000,
    "largest_sell_usd":         480_000,
    "buy_to_sell_n_ratio":      4.25,
    "by_window": {
        "1m":  {...same shape, 1-minute window},
        "5m":  {...},
        "15m": {...},
    },
    "ws_status": "connected|reconnecting|stale",
    "n_total_trades_seen":     8_421,         # total publicTrade events processed
}

Run:
  python3 /opt/sygnif-services/sygnif_whale_watcher.py
Wired by sygnif-whale-watcher.service (Type=simple, restarts on failure).
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import pathlib
import signal
import sys
import threading
import time
from typing import Any, Deque

try:
    import websocket  # websocket-client
except ImportError:
    print("FATAL: websocket-client not installed. "
          "pip install websocket-client", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
WS_URL              = os.environ.get("SYGNIF_WHALE_WS_URL",
                                       "wss://stream.bybit.com/v5/public/linear")
SYMBOL              = os.environ.get("SYGNIF_WHALE_SYMBOL", "BTCUSDT")
WHALE_NOTIONAL_USD  = float(os.environ.get("SYGNIF_WHALE_NOTIONAL_USD", "250000"))
WINDOW_MINUTES_MAX  = int(os.environ.get("SYGNIF_WHALE_WINDOW_MIN", "15"))
FLUSH_INTERVAL_S    = int(os.environ.get("SYGNIF_WHALE_FLUSH_S", "5"))
OUT_JSON            = pathlib.Path("/var/lib/sygnif/whale_flow.json")
OUT_NDJSON          = pathlib.Path("/var/lib/sygnif/whale_flow.ndjson")

# Sub-windows reported in summary
SUB_WINDOWS_MIN = (1, 5, 15)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
state = {
    "lock":             threading.Lock(),
    "whales":           collections.deque(),   # deque[(ts_ms, side, price, qty, notional_usd)]
    "n_total_seen":     0,
    "n_whales_seen":    0,
    "ws_status":        "starting",
    "last_msg_ts":      0,
    "started_at":       time.time(),
}


def _trim_old(now_ms: int) -> None:
    """Drop whale records older than max-window from the deque."""
    cutoff = now_ms - WINDOW_MINUTES_MAX * 60 * 1000
    while state["whales"] and state["whales"][0][0] < cutoff:
        state["whales"].popleft()


def _summary_for_window(window_min: int, now_ms: int) -> dict:
    cutoff = now_ms - window_min * 60 * 1000
    relevant = [w for w in state["whales"] if w[0] >= cutoff]
    if not relevant:
        return {
            "n":                 0,
            "buy_notional_usd":  0,
            "sell_notional_usd": 0,
            "imbalance":         0.5,
            "n_buys":            0,
            "n_sells":           0,
            "largest_buy_usd":   0,
            "largest_sell_usd":  0,
        }
    buys  = [w for w in relevant if w[1] == "Buy"]
    sells = [w for w in relevant if w[1] == "Sell"]
    buy_notional  = sum(w[4] for w in buys)
    sell_notional = sum(w[4] for w in sells)
    total = buy_notional + sell_notional
    return {
        "n":                 len(relevant),
        "buy_notional_usd":  round(buy_notional, 0),
        "sell_notional_usd": round(sell_notional, 0),
        "imbalance":         round(buy_notional / total if total > 0 else 0.5, 4),
        "n_buys":            len(buys),
        "n_sells":           len(sells),
        "largest_buy_usd":   round(max((w[4] for w in buys),  default=0), 0),
        "largest_sell_usd":  round(max((w[4] for w in sells), default=0), 0),
    }


def write_summary() -> None:
    now_ms = int(time.time() * 1000)
    age_s_since_msg = (now_ms - state["last_msg_ts"]) / 1000 if state["last_msg_ts"] else None
    with state["lock"]:
        _trim_old(now_ms)
        sub = {f"{w}m": _summary_for_window(w, now_ms) for w in SUB_WINDOWS_MIN}
        primary = sub.get("15m", {})
        out = {
            "updated_utc":            dt.datetime.utcfromtimestamp(now_ms / 1000)
                                            .replace(tzinfo=dt.timezone.utc).isoformat(),
            "symbol":                 SYMBOL,
            "window_minutes":         WINDOW_MINUTES_MAX,
            "whale_threshold_usd":    WHALE_NOTIONAL_USD,
            "n_whale_trades":         primary.get("n", 0),
            "whale_buy_notional_usd": primary.get("buy_notional_usd", 0),
            "whale_sell_notional_usd": primary.get("sell_notional_usd", 0),
            "whale_imbalance":        primary.get("imbalance", 0.5),
            "n_large_buys":           primary.get("n_buys", 0),
            "n_large_sells":          primary.get("n_sells", 0),
            "largest_buy_usd":        primary.get("largest_buy_usd", 0),
            "largest_sell_usd":       primary.get("largest_sell_usd", 0),
            "buy_to_sell_n_ratio":    (primary.get("n_buys", 0)
                                          / max(primary.get("n_sells", 1), 1)),
            "by_window":              sub,
            "ws_status":              state["ws_status"],
            "n_total_trades_seen":    state["n_total_seen"],
            "n_whales_seen_total":    state["n_whales_seen"],
            "uptime_s":               int(time.time() - state["started_at"]),
            "age_s_since_last_msg":   round(age_s_since_msg, 1)
                                          if age_s_since_msg is not None else None,
        }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_JSON.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(out, f, indent=2)
    os.replace(tmp, OUT_JSON)


def append_audit_line(rec: dict) -> None:
    """Append one whale trade to the per-day audit ndjson."""
    day = time.strftime("%Y-%m-%d", time.gmtime(rec["ts_s"]))
    path = pathlib.Path(f"/var/lib/sygnif/whale_flow_{day}.ndjson")
    try:
        with path.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# WebSocket handlers
# ---------------------------------------------------------------------------
def on_open(ws):
    state["ws_status"] = "connected"
    print(f"  ws connected — subscribing to publicTrade.{SYMBOL}", flush=True)
    ws.send(json.dumps({"op": "subscribe", "args": [f"publicTrade.{SYMBOL}"]}))


def on_message(ws, message):
    try:
        msg = json.loads(message)
    except json.JSONDecodeError:
        return
    state["last_msg_ts"] = int(time.time() * 1000)
    # Subscription ack
    if msg.get("op") == "subscribe":
        return
    data = msg.get("data") or []
    if not isinstance(data, list):
        return
    for trade in data:
        try:
            ts_ms  = int(trade.get("T", 0))
            side   = trade.get("S")              # "Buy" or "Sell" (taker side)
            price  = float(trade.get("p", 0))
            qty    = float(trade.get("v", 0))    # in BTC for linear BTCUSDT
        except (ValueError, TypeError):
            continue
        notional = price * qty
        state["n_total_seen"] += 1
        if notional < WHALE_NOTIONAL_USD:
            continue
        rec = (ts_ms, side, price, qty, notional)
        with state["lock"]:
            state["whales"].append(rec)
            state["n_whales_seen"] += 1
            _trim_old(ts_ms)
        append_audit_line({
            "ts_s":      ts_ms / 1000,
            "ts_iso":    dt.datetime.utcfromtimestamp(ts_ms / 1000)
                            .replace(tzinfo=dt.timezone.utc).isoformat(),
            "side":      side, "price": price, "qty": qty,
            "notional":  notional, "symbol": SYMBOL,
        })


def on_error(ws, error):
    state["ws_status"] = f"error: {error}"
    print(f"  ws error: {error}", file=sys.stderr, flush=True)


def on_close(ws, code, reason):
    state["ws_status"] = "disconnected"
    print(f"  ws closed: {code} {reason}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Periodic writers
# ---------------------------------------------------------------------------
def flush_loop():
    while True:
        try:
            write_summary()
        except Exception as e:
            print(f"  flush failed: {e}", file=sys.stderr, flush=True)
        time.sleep(FLUSH_INTERVAL_S)


def status_loop():
    """Print stats every 60s so the operator sees the daemon is alive."""
    while True:
        time.sleep(60)
        with state["lock"]:
            n = len(state["whales"])
            buys = sum(1 for w in state["whales"] if w[1] == "Buy")
            sells = sum(1 for w in state["whales"] if w[1] == "Sell")
            tot_seen = state["n_total_seen"]
            wh_seen = state["n_whales_seen"]
            status = state["ws_status"]
        print(f"  stats: ws={status} window_size={n} buys={buys} sells={sells} "
              f"total_seen={tot_seen} whales_seen={wh_seen}", flush=True)


# ---------------------------------------------------------------------------
# Main loop with auto-reconnect
# ---------------------------------------------------------------------------
def main():
    print(f"=== sygnif_whale_watcher starting ===", flush=True)
    print(f"  ws url:              {WS_URL}", flush=True)
    print(f"  symbol:              {SYMBOL}", flush=True)
    print(f"  whale threshold:     ${WHALE_NOTIONAL_USD:,.0f} notional", flush=True)
    print(f"  window:              {WINDOW_MINUTES_MAX} min", flush=True)
    print(f"  flush interval:      {FLUSH_INTERVAL_S} s", flush=True)
    print(f"  output:              {OUT_JSON}", flush=True)

    # Background writers
    threading.Thread(target=flush_loop, daemon=True).start()
    threading.Thread(target=status_loop, daemon=True).start()

    # WS reconnect loop
    backoff = 5
    while True:
        try:
            ws = websocket.WebSocketApp(
                WS_URL,
                on_open=on_open, on_message=on_message,
                on_error=on_error, on_close=on_close)
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except KeyboardInterrupt:
            print("  interrupted", flush=True)
            return 0
        except Exception as e:
            print(f"  run_forever failed: {e}", file=sys.stderr, flush=True)
        # reconnect with backoff
        print(f"  reconnecting in {backoff}s...", flush=True)
        state["ws_status"] = "reconnecting"
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    sys.exit(main())
