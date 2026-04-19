#!/usr/bin/env python3
"""
Continuous Bybit **WebSocket** monitor (no per-click REST “force”).

Per Bybit demo docs:
  - **Public** orderbook & trades: mainnet ``wss://stream.bybit.com/v5/public/linear``
    (demo uses the same public market data).
  - **Private** orders / executions / positions (demo keys): ``wss://stream-demo.bybit.com/v5/private``

Writes a small JSON snapshot (best bid/ask, last trade, last private event) for dashboards
and logs concise lines to stdout / journald — **constant data-flow hook**: public + private WS threads,
auto-reconnect, 20s ``ping``, atomic snapshot replace (unique temp file per flush).

Env (demo private — optional; public runs without keys):
  ``BYBIT_DEMO_API_KEY`` / ``BYBIT_DEMO_API_SECRET`` — same as Freqtrade demo trader.
  ``BYBIT_WS_DISABLE_PRIVATE=1`` — public streams only.

Optional:
  ``BYBIT_WS_SYMBOL`` (default ``BTCUSDT``)
  ``BYBIT_WS_SNAPSHOT_PATH`` — default ``user_data/bybit_ws_monitor_state.json`` under repo root
  ``BYBIT_WS_JSONL_LOG`` — append one JSON object per line (full messages, can be large)
  ``BYBIT_WS_VERBOSE_ORDERBOOK=1`` — log every orderbook push (default: off; snapshot still updates)

**Public liquidations (Bybit V5 ``allLiquidation.{symbol}``)** — same linear public WS; no keys.
  ``BYBIT_WS_LIQUIDATION=1`` (default) / ``0`` to disable.
  ``BYBIT_WS_LIQUIDATION_SYMBOLS`` — comma list (default: same as ``BYBIT_WS_SYMBOL``).
  ``BYBIT_WS_NL_LIQ_INTERVAL_SEC`` — min seconds between ``BYBIT_LIQ …`` lines to NeuroLinked (default ``1``).
  Snapshot JSON: ``liquidations_recent`` (ring), ``liquidation_ingress_total``, ``last_liquidation_summary``.

**REST:** Bybit publishes liquidations on this **WebSocket** topic only; there is no v5 public REST
for the same cascade history — use ``liquidations_recent`` / JSONL as your local time series.

**Dauerhaft / eine Instanz (Swarm tape lock-in):** ``scripts/bybit_stream_monitor_locked.sh`` (``flock``) +
``systemd/bybit-stream-monitor.service`` — lädt ``.env`` + ``swarm_operator.env``, ``Restart=always``.
Install: ``sudo cp systemd/bybit-stream-monitor.service /etc/systemd/system/`` dann
``sudo systemctl daemon-reload && sudo systemctl enable --now bybit-stream-monitor``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sys
import uuid
import threading
import time
import urllib.request as _urllib_req
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]

try:
    import websocket
except ImportError:
    print(
        "bybit_stream_monitor: install deps: pip install -r scripts/requirements_bybit_stream.txt",
        file=sys.stderr,
    )
    raise SystemExit(2)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("bybit_ws")

DEFAULT_PUBLIC = "wss://stream.bybit.com/v5/public/linear"
DEFAULT_PRIVATE_DEMO = "wss://stream-demo.bybit.com/v5/private"

_NL_URL = (os.environ.get("SYGNIF_NEUROLINKED_HOST_URL") or
           os.environ.get("SYGNIF_NEUROLINKED_HTTP_URL") or
           "http://127.0.0.1:8889").rstrip("/")
_nl_last_feed = 0.0
_NL_INTERVAL = 2.0  # seconds between market data pushes
_nl_liq_last = 0.0


def _nl_liq_interval() -> float:
    try:
        return max(0.25, float(os.environ.get("BYBIT_WS_NL_LIQ_INTERVAL_SEC", "1") or 1))
    except ValueError:
        return 1.0


def _nl_feed_liquidation(text: str) -> None:
    """Throttled NL push for liquidation lines (separate from orderbook ``_nl_feed``)."""
    global _nl_liq_last
    now = time.monotonic()
    if now - _nl_liq_last < _nl_liq_interval():
        return
    _nl_liq_last = now
    try:
        data = json.dumps({"text": text}).encode()
        req = _urllib_req.Request(
            f"{_NL_URL}/api/input/text",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        _urllib_req.urlopen(req, timeout=2)
    except Exception:
        pass


def _nl_feed(text: str) -> None:
    global _nl_last_feed
    now = time.monotonic()
    if now - _nl_last_feed < _NL_INTERVAL:
        return
    _nl_last_feed = now
    try:
        data = json.dumps({"text": text}).encode()
        req = _urllib_req.Request(f"{_NL_URL}/api/input/text", data=data,
                                   headers={"Content-Type": "application/json"})
        _urllib_req.urlopen(req, timeout=2)
    except Exception:
        pass


def _repo_path(rel: str) -> Path:
    return _REPO / rel


def _snapshot_path() -> Path:
    raw = os.environ.get("BYBIT_WS_SNAPSHOT_PATH", "").strip()
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else _REPO / p
    return _REPO / "user_data" / "bybit_ws_monitor_state.json"


def _auth_payload(api_key: str, api_secret: str) -> dict[str, Any]:
    expires = int((time.time() + 10) * 1000)
    sign = hmac.new(
        api_secret.encode("utf-8"),
        f"GET/realtime{expires}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {"op": "auth", "args": [api_key, expires, sign]}


class Snapshot:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {
            "updated_utc": None,
            "symbol": os.environ.get("BYBIT_WS_SYMBOL", "BTCUSDT").upper(),
            "public_ws": DEFAULT_PUBLIC,
            "private_ws": None,
            "best_bid": None,
            "best_ask": None,
            "orderbook_ts": None,
            "last_public_trade": None,
            "last_private_topic": None,
            "last_private_summary": None,
            "private_connected": False,
            "public_connected": False,
            "liquidations_recent": [],
            "liquidation_ingress_total": 0,
            "last_liquidation_ts": None,
            "last_liquidation_summary": None,
        }

    def append_liquidations(self, rows: list[dict[str, Any]], *, topic_ts: int | None) -> None:
        """Merge Bybit ``allLiquidation`` rows into snapshot + optional NeuroLinked feed."""
        norm: list[dict[str, Any]] = [r for r in rows if isinstance(r, dict)]
        if not norm:
            return
        with self._lock:
            lst = [x for x in (self._data.get("liquidations_recent") or []) if isinstance(x, dict)]
            for r in norm:
                lst.append(
                    {
                        "T": r.get("T"),
                        "s": r.get("s"),
                        "S": r.get("S"),
                        "v": r.get("v"),
                        "p": r.get("p"),
                        "topic_ts": topic_ts,
                        "recv_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                )
            self._data["liquidations_recent"] = lst[-80:]
            self._data["liquidation_ingress_total"] = int(self._data.get("liquidation_ingress_total") or 0) + len(
                norm
            )
            self._data["last_liquidation_ts"] = topic_ts
            brief = [f"{r.get('s')} {r.get('S')} v={r.get('v')} p={r.get('p')}" for r in norm[:4]]
            self._data["last_liquidation_summary"] = "; ".join(brief)[:500]
            self._flush_unlocked()
        for r in norm[:8]:
            sym = r.get("s") or "?"
            side = r.get("S") or "?"
            vol = r.get("v") or "?"
            px = r.get("p") or "?"
            t_ev = r.get("T") or ""
            _nl_feed_liquidation(f"BYBIT_LIQ {sym} liq_side={side} sz={vol} bnkr_px={px} T={t_ev}")
            log.info("liquidation %s side=%s v=%s p=%s", sym, side, vol, px)

    def merge(self, **kwargs: Any) -> None:
        with self._lock:
            self._data.update(kwargs)
            self._data["updated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._flush_unlocked()
            sym = self._data.get("symbol", "BTCUSDT")
            bid = self._data.get("best_bid")
            ask = self._data.get("best_ask")
            last = self._data.get("last_public_trade")
            if bid and ask:
                mid = (bid + ask) / 2
                spread_bps = round((ask - bid) / mid * 10000, 1)
                _nl_feed(f"MARKET {sym} mid={mid:.2f} bid={bid:.2f} ask={ask:.2f} spread={spread_bps}bps" +
                         (f" last={last}" if last else ""))

    def _flush_unlocked(self) -> None:
        path = _snapshot_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._data, indent=2) + "\n"
        tmp = path.parent / f".{path.stem}.{uuid.uuid4().hex}.tmp"
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise


def _best_bid_ask_from_ob(msg: dict[str, Any]) -> tuple[float | None, float | None]:
    d = (msg.get("data") or {})
    bids = d.get("b") or []
    asks = d.get("a") or []
    bid = float(bids[0][0]) if bids else None
    ask = float(asks[0][0]) if asks else None
    return bid, ask


def _jsonl_path() -> Path | None:
    raw = os.environ.get("BYBIT_WS_JSONL_LOG", "").strip()
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_absolute() else _REPO / p


def _append_jsonl(obj: dict[str, Any]) -> None:
    p = _jsonl_path()
    if not p:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n"
    with open(p, "a", encoding="utf-8") as f:
        f.write(line)


def _liquidation_subscribe_args(main_sym: str) -> list[str]:
    if os.environ.get("BYBIT_WS_LIQUIDATION", "1").strip().lower() in ("0", "false", "no", "off"):
        return []
    raw = os.environ.get("BYBIT_WS_LIQUIDATION_SYMBOLS", "").strip()
    if raw:
        syms = [s.strip().upper() for s in raw.split(",") if s.strip()]
    else:
        syms = [main_sym]
    return [f"allLiquidation.{s}" for s in syms]


def run_public_ws(snapshot: Snapshot, stop: threading.Event) -> None:
    sym = os.environ.get("BYBIT_WS_SYMBOL", "BTCUSDT").upper()
    url = os.environ.get("BYBIT_WS_PUBLIC_LINEAR", DEFAULT_PUBLIC)
    verbose_ob = os.environ.get("BYBIT_WS_VERBOSE_ORDERBOOK", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    liq_args = _liquidation_subscribe_args(sym)
    subs = [
        f"orderbook.50.{sym}",
        f"publicTrade.{sym}",
        *liq_args,
    ]
    last_ob_log = 0.0

    def on_message(_ws: Any, message: str) -> None:
        nonlocal last_ob_log
        try:
            msg = json.loads(message)
        except json.JSONDecodeError:
            return
        _append_jsonl({"channel": "public", "raw": msg})
        topic = msg.get("topic") or ""
        if topic.startswith("orderbook"):
            bid, ask = _best_bid_ask_from_ob(msg)
            ts = (msg.get("data") or {}).get("ts") or msg.get("ts")
            snapshot.merge(
                best_bid=bid,
                best_ask=ask,
                orderbook_ts=ts,
                public_connected=True,
            )
            if bid and ask and verbose_ob:
                now = time.time()
                if now - last_ob_log >= 5.0:
                    last_ob_log = now
                    log.info("orderbook %s bid=%s ask=%s", sym, bid, ask)
        elif topic.startswith("publicTrade"):
            rows = msg.get("data") or []
            if rows and isinstance(rows, list):
                last = rows[-1]
                snapshot.merge(last_public_trade=last, public_connected=True)
                log.info(
                    "trade %s side=%s price=%s qty=%s",
                    sym,
                    last.get("S") or last.get("side"),
                    last.get("p"),
                    last.get("v"),
                )
        elif topic.startswith("allLiquidation"):
            raw = msg.get("data")
            if isinstance(raw, dict):
                rows_l = [raw]
            elif isinstance(raw, list):
                rows_l = raw
            else:
                rows_l = []
            ts_raw = msg.get("ts")
            try:
                ts_liq = int(ts_raw) if ts_raw is not None else None
            except (TypeError, ValueError):
                ts_liq = None
            if rows_l:
                snapshot.append_liquidations(rows_l, topic_ts=ts_liq)
                snapshot.merge(public_connected=True)

    def on_error(_ws: Any, err: Any) -> None:
        log.warning("public ws error: %s", err)

    def on_close(_ws: Any, *args: Any) -> None:
        snapshot.merge(public_connected=False)
        log.info("public ws closed")

    def on_open(ws: Any) -> None:
        ws.send(json.dumps({"op": "subscribe", "args": subs}))
        snapshot.merge(public_connected=True)
        log.info("public ws subscribed %s", subs)

    def ping_loop(ws_ref: list[Any]) -> None:
        while not stop.is_set():
            time.sleep(20)
            w = ws_ref[0]
            if w:
                try:
                    w.send(json.dumps({"op": "ping"}))
                except Exception as e:
                    log.debug("public ping failed: %s", e)

    while not stop.is_set():
        ws_ref: list[Any] = [None]
        try:
            ws = websocket.WebSocketApp(
                url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            ws_ref[0] = ws
            t = threading.Thread(target=ping_loop, args=(ws_ref,), daemon=True)
            t.start()
            ws.run_forever(ping_interval=None, ping_timeout=None)
        except Exception as e:
            log.exception("public ws run_forever: %s", e)
        if stop.wait(3):
            break
        log.info("public ws reconnecting…")


def run_private_demo_ws(snapshot: Snapshot, stop: threading.Event) -> None:
    if os.environ.get("BYBIT_WS_DISABLE_PRIVATE", "").strip().lower() in ("1", "true", "yes"):
        log.info("private demo ws disabled (BYBIT_WS_DISABLE_PRIVATE)")
        return

    key = (os.environ.get("BYBIT_DEMO_API_KEY") or "").strip()
    sec = (os.environ.get("BYBIT_DEMO_API_SECRET") or "").strip()
    if not key or not sec:
        log.warning("no BYBIT_DEMO_API_KEY/SECRET — private demo stream skipped")
        return

    url = os.environ.get("BYBIT_WS_PRIVATE_DEMO", DEFAULT_PRIVATE_DEMO)
    snapshot.merge(private_ws=url)

    subs = ["order", "execution", "position"]

    def on_message(_ws: Any, message: str) -> None:
        try:
            msg = json.loads(message)
        except json.JSONDecodeError:
            return
        _append_jsonl({"channel": "private", "raw": msg})
        if msg.get("op") == "auth":
            if msg.get("success"):
                log.info("private ws auth ok")
                _ws.send(json.dumps({"op": "subscribe", "args": subs}))
            else:
                log.error("private ws auth failed: %s", msg)
            return
        topic = msg.get("topic") or ""
        data = msg.get("data")
        summary: Any = None
        if isinstance(data, list) and data:
            summary = data[0] if len(data) == 1 else {"n": len(data), "first": data[0]}
        elif isinstance(data, dict):
            summary = data
        snapshot.merge(
            last_private_topic=topic,
            last_private_summary=summary,
            private_connected=True,
        )
        log.info("private %s %s", topic, str(summary)[:240] if summary else "")

    def on_error(_ws: Any, err: Any) -> None:
        log.warning("private ws error: %s", err)

    def on_close(_ws: Any, *args: Any) -> None:
        snapshot.merge(private_connected=False)
        log.info("private ws closed")

    def on_open(ws: Any) -> None:
        ws.send(json.dumps(_auth_payload(key, sec)))

    def ping_loop(ws_ref: list[Any]) -> None:
        while not stop.is_set():
            time.sleep(20)
            w = ws_ref[0]
            if w:
                try:
                    w.send(json.dumps({"op": "ping"}))
                except Exception as e:
                    log.debug("private ping failed: %s", e)

    while not stop.is_set():
        ws_ref = [None]
        try:
            ws = websocket.WebSocketApp(
                url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            ws_ref[0] = ws
            t = threading.Thread(target=ping_loop, args=(ws_ref,), daemon=True)
            t.start()
            ws.run_forever(ping_interval=None, ping_timeout=None)
        except Exception as e:
            log.exception("private ws run_forever: %s", e)
        if stop.wait(3):
            break
        log.info("private ws reconnecting…")


def main() -> int:
    stop = threading.Event()
    snap = Snapshot()
    threads = [
        threading.Thread(target=run_public_ws, args=(snap, stop), name="bybit-public", daemon=True),
        threading.Thread(target=run_private_demo_ws, args=(snap, stop), name="bybit-private", daemon=True),
    ]
    for t in threads:
        t.start()
    log.info(
        "bybit_stream_monitor: snapshot=%s (Ctrl+C to stop)",
        _snapshot_path(),
    )
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log.info("shutdown")
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
