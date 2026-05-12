#!/usr/bin/env python3
"""SYGNIF microstructure → NeuroLinked feed (Phase B-1).

Polls Bybit V5 mainnet REST endpoints we currently DON'T surface:

  • /v5/market/orderbook       — L2 depth (top-5 walls, imbalance, total)
  • /v5/market/insurance       — insurance pool size (systemic stress)
  • /v5/market/tickers (perp)  — predicted next funding rate

Emits to NeuroLinked brain via POST /api/input/text:

  SYGNIF_ORDERBOOK BTCUSDT bid_top5_btc=12.5 ask_top5_btc=10.8 imb=+0.07
    bid_top5_usd=1010500 ask_top5_usd=872400 wall_bid=80100 wall_ask=80450
  SYGNIF_INSURANCE pool_usdt=523_000_000 delta_1d_usdt=+1_200_000
  SYGNIF_FUNDING_PRED BTCUSDT next_funding_ts=1672304400000
    last_funding=+0.00010 predicted_funding=+0.00015

Cadence (env-overridable):
  ORDERBOOK_POLL_SEC = 30      # depth changes fast
  INSURANCE_POLL_SEC = 300     # slow-moving
  FUNDING_PRED_POLL_SEC = 60   # ticker has it cheap

Run: python3 sygnif_microstructure_feed.py
Deploy at /opt/sygnif-services/sygnif_microstructure_feed.py on EC2.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sygnif_microstructure")

NL_URL = (os.environ.get("SYGNIF_NEUROLINKED_HOST_URL")
          or "http://127.0.0.1:8889").rstrip("/")
ORDERBOOK_POLL_SEC = int(os.environ.get("ORDERBOOK_POLL_SEC", "30"))
INSURANCE_POLL_SEC = int(os.environ.get("INSURANCE_POLL_SEC", "300"))
FUNDING_PRED_POLL_SEC = int(os.environ.get("FUNDING_PRED_POLL_SEC", "60"))
POST_TIMEOUT = int(os.environ.get("MICRO_POST_TIMEOUT_SEC", "30"))
HTTP_TIMEOUT = int(os.environ.get("MICRO_HTTP_TIMEOUT_SEC", "10"))
BASE_PUBLIC = os.environ.get("BYBIT_PUBLIC_BASE", "https://api.bybit.com").rstrip("/")
SYMBOLS = [s.strip().upper() for s in
           os.environ.get("MICRO_SYMBOLS", "BTCUSDT").split(",") if s.strip()]
# Snapshot path consumed by the trader's entry gates. Same convention as
# the daemon's liquidation snapshot (~/.sygnif/...). When run as ubuntu,
# this becomes /home/ubuntu/.sygnif/microstructure-snapshot.json.
from pathlib import Path  # noqa: E402
SNAPSHOT_PATH = Path(os.environ.get("MICRO_SNAPSHOT_PATH",
                                       str(Path.home() / ".sygnif" / "microstructure-snapshot.json")))
SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
# Per-symbol latest values (the snapshot is a small mutable map updated
# by each emit function — orderbook every 30s, others rarer).
_LATEST: dict = {"symbols": {}, "insurance": {}, "funding": {}}


def _get(path: str, params: dict, timeout: int = HTTP_TIMEOUT) -> dict:
    q = urllib.parse.urlencode(params)
    url = f"{BASE_PUBLIC}{path}?{q}"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def _post_nl(text: str) -> bool:
    body = json.dumps({"text": text, "skip_claude_bridge": True}).encode("utf-8")
    req = urllib.request.Request(f"{NL_URL}/api/input/text", data=body,
                                  headers={"Content-Type": "application/json"},
                                  method="POST")
    try:
        with urllib.request.urlopen(req, timeout=POST_TIMEOUT) as r:
            return r.status == 200
    except Exception as e:
        log.warning("NL post failed: %s", e)
        return False


# ---------------- orderbook ----------------


def fetch_orderbook(symbol: str) -> dict | None:
    try:
        r = _get("/v5/market/orderbook", {"category": "linear",
                                            "symbol": symbol, "limit": 50})
        return r["result"]
    except Exception as e:
        log.warning("orderbook %s err: %s", symbol, e)
        return None


def _orderbook_summary(ob: dict) -> dict:
    """Top-5 walls + imbalance + estimated USD notionals."""
    bids = ob.get("b") or []
    asks = ob.get("a") or []
    top5_bid = bids[:5]
    top5_ask = asks[:5]
    bid_qty_btc = sum(float(p[1]) for p in top5_bid) if top5_bid else 0.0
    ask_qty_btc = sum(float(p[1]) for p in top5_ask) if top5_ask else 0.0
    bid_top_px = float(top5_bid[0][0]) if top5_bid else 0.0
    ask_top_px = float(top5_ask[0][0]) if top5_ask else 0.0
    mid = (bid_top_px + ask_top_px) / 2.0 if (bid_top_px and ask_top_px) else 0.0
    bid_usd = bid_qty_btc * mid
    ask_usd = ask_qty_btc * mid
    tot = bid_usd + ask_usd
    imb = (bid_usd - ask_usd) / tot if tot > 0 else 0.0
    # biggest wall in each side
    wall_bid = max(top5_bid, key=lambda p: float(p[1]))[0] if top5_bid else "0"
    wall_ask = max(top5_ask, key=lambda p: float(p[1]))[0] if top5_ask else "0"
    return {
        "bid_top5_btc": round(bid_qty_btc, 3),
        "ask_top5_btc": round(ask_qty_btc, 3),
        "bid_top5_usd": int(bid_usd),
        "ask_top5_usd": int(ask_usd),
        "imbalance": round(imb, 3),
        "wall_bid": wall_bid,
        "wall_ask": wall_ask,
        "spread_bps": round((ask_top_px - bid_top_px) / mid * 10_000, 2) if mid > 0 else 0.0,
    }


def _write_snapshot() -> None:
    """Atomic write of the latest microstructure state for trader consumption."""
    try:
        payload = dict(_LATEST)
        payload["ts_ms"] = int(time.time() * 1000)
        tmp = SNAPSHOT_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(SNAPSHOT_PATH)
    except Exception as e:
        log.warning("snapshot write err: %s", e)


def emit_orderbook(symbol: str) -> bool:
    ob = fetch_orderbook(symbol)
    if not ob:
        return False
    s = _orderbook_summary(ob)
    line = (f"SYGNIF_ORDERBOOK {symbol} "
            f"bid_top5_btc={s['bid_top5_btc']} ask_top5_btc={s['ask_top5_btc']} "
            f"imb={s['imbalance']:+.3f} "
            f"bid_top5_usd={s['bid_top5_usd']} ask_top5_usd={s['ask_top5_usd']} "
            f"wall_bid={s['wall_bid']} wall_ask={s['wall_ask']} "
            f"spread_bps={s['spread_bps']}")
    ok = _post_nl(line)
    _LATEST["symbols"][symbol] = {**s, "ts_ms": int(time.time() * 1000)}
    _write_snapshot()
    log.info("orderbook %s: imb=%+.3f bid=%dk ask=%dk ok=%s",
             symbol, s["imbalance"], s["bid_top5_usd"] // 1000,
             s["ask_top5_usd"] // 1000, ok)
    return ok


# ---------------- insurance pool ----------------


_LAST_INSURANCE: dict[str, float] = {}
_INSURANCE_HISTORY: list[tuple[float, float]] = []  # (ts, usdt_pool)


def emit_insurance() -> bool:
    """Bybit insurance pool — sentiment proxy. Big drops mean cascading
    losses overflowed individual maint margin and tapped the pool."""
    try:
        r = _get("/v5/market/insurance", {})
        rows = r["result"]["list"]
    except Exception as e:
        log.warning("insurance err: %s", e)
        return False
    # 2026-05-10 fix: Bybit returns 70+ USDT-coin sub-pool entries.
    # The old code took the FIRST USDT row and broke — but Bybit doesn't
    # guarantee order, so the picked pool varied poll-to-poll ($371M main
    # vs $2M sub-pools), making the 24h delta math fire phantom -99%
    # stress halts every other cycle. Now sums ALL USDT entries (the
    # author's original intent per the docstring comment).
    usdt_pool = 0.0
    for row in rows:
        coin = row.get("coin", "")
        bal = float(row.get("balance", "0") or 0)
        if coin == "USDT":
            usdt_pool += bal
    if usdt_pool <= 0:
        return False
    delta_1d = usdt_pool - _LAST_INSURANCE.get("usdt_d1", usdt_pool)
    _LAST_INSURANCE["usdt_d1"] = usdt_pool
    # rolling 24h history (cap 300 entries = 24h at 5min cadence)
    now = time.time()
    _INSURANCE_HISTORY.append((now, usdt_pool))
    cutoff = now - 24 * 3600
    while _INSURANCE_HISTORY and _INSURANCE_HISTORY[0][0] < cutoff:
        _INSURANCE_HISTORY.pop(0)
    pool_24h_ago = _INSURANCE_HISTORY[0][1] if _INSURANCE_HISTORY else usdt_pool
    delta_24h_pct = ((usdt_pool - pool_24h_ago) / pool_24h_ago * 100.0) if pool_24h_ago > 0 else 0.0
    line = (f"SYGNIF_INSURANCE pool_usdt={usdt_pool:,.0f} "
            f"delta_period_usdt={delta_1d:+,.0f} "
            f"delta_24h_pct={delta_24h_pct:+.2f}")
    ok = _post_nl(line)
    _LATEST["insurance"] = {
        "pool_usdt": usdt_pool,
        "delta_period_usdt": delta_1d,
        "delta_24h_pct": delta_24h_pct,
        "ts_ms": int(time.time() * 1000),
    }
    _write_snapshot()
    log.info("insurance: pool=$%s delta_24h=%.2f%% ok=%s",
             f"{usdt_pool:,.0f}", delta_24h_pct, ok)
    return ok


# ---------------- funding prediction ----------------


def emit_funding_pred() -> bool:
    """Bybit ticker exposes both fundingRate (last) and predictedFundingRate
    + nextFundingTime — we surface the predicted as a forward signal."""
    posted = 0
    for sym in SYMBOLS:
        try:
            r = _get("/v5/market/tickers", {"category": "linear", "symbol": sym})
            t = r["result"]["list"][0]
        except Exception as e:
            log.warning("tickers %s err: %s", sym, e)
            continue
        last_fr = t.get("fundingRate") or "0"
        pred_fr = t.get("predictedFundingRate")  # may not exist on every contract
        next_ts = t.get("nextFundingTime") or "0"
        line_parts = [f"SYGNIF_FUNDING_PRED {sym}",
                      f"last_funding={float(last_fr):+.6f}",
                      f"next_funding_ts={next_ts}"]
        if pred_fr:
            try:
                line_parts.append(f"predicted_funding={float(pred_fr):+.6f}")
            except (TypeError, ValueError):
                pass
        line = " ".join(line_parts)
        if _post_nl(line):
            posted += 1
        _LATEST["funding"][sym] = {
            "last": float(last_fr or 0),
            "next_funding_ts": next_ts,
            "ts_ms": int(time.time() * 1000),
        }
        if pred_fr:
            try:
                _LATEST["funding"][sym]["predicted"] = float(pred_fr)
            except (TypeError, ValueError):
                pass
    _write_snapshot()
    log.info("funding_pred: posted=%d/%d", posted, len(SYMBOLS))
    return posted > 0


# ---------------- main loop ----------------


def main() -> int:
    log.info("sygnif-microstructure-feed starting; symbols=%s nl=%s "
             "orderbook=%ds insurance=%ds funding_pred=%ds",
             SYMBOLS, NL_URL, ORDERBOOK_POLL_SEC, INSURANCE_POLL_SEC,
             FUNDING_PRED_POLL_SEC)
    last_ob = 0.0
    last_ins = 0.0
    last_fp = 0.0
    while True:
        now = time.time()
        try:
            if now - last_ob >= ORDERBOOK_POLL_SEC:
                for sym in SYMBOLS:
                    emit_orderbook(sym)
                last_ob = now
            if now - last_ins >= INSURANCE_POLL_SEC:
                emit_insurance()
                last_ins = now
            if now - last_fp >= FUNDING_PRED_POLL_SEC:
                emit_funding_pred()
                last_fp = now
        except Exception as e:
            log.exception("loop iter failed: %s", e)
        time.sleep(min(ORDERBOOK_POLL_SEC, INSURANCE_POLL_SEC, FUNDING_PRED_POLL_SEC, 30))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
