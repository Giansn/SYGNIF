#!/usr/bin/env python3
"""
Bybit market data feed → NeuroLinked.

Polls Bybit **mainnet** public REST (``https://api.bybit.com``) on each interval and
pushes structured market signals (funding, OI, L/S ratio, volume, price change) to
NeuroLinked via POST /api/input/text.

Optional **Swarm + Hivemind layers** (same fusion as ``finance_agent.swarm_knowledge``):
set ``BYBIT_NL_INCLUDE_SWARM=1`` (default). On each ``BYBIT_NL_SWARM_INTERVAL`` tick,
runs ``compute_swarm()`` and emits ``BYBIT_SWARM_LAYER …`` plus ``BYBIT_HIVEMIND_LAYER …``
when Truthcoin explore data is present. Default **lite** swarm call skips
``open_trades`` / ``closed-pnl`` sub-reports to avoid extra signed Bybit traffic;
set ``BYBIT_NL_SWARM_FULL=1`` for a full ``compute_swarm()`` payload.

Run: python3 bybit_nl_market_feed.py
Env: SYGNIF_NEUROLINKED_HOST_URL (default http://127.0.0.1:8889)
     BYBIT_NL_POST_TIMEOUT_SEC (default 25) — POST /api/input/text timeout
     BYBIT_NL_SYMBOLS (default BTCUSDT,ETHUSDT,SOLUSDT)
     BYBIT_NL_INTERVAL (default 30 seconds)
     BYBIT_NL_INCLUDE_SWARM (default 1) — Swarm + hm lines for NL
     BYBIT_NL_SWARM_INTERVAL (default 90) — seconds between swarm ticks
     BYBIT_NL_SWARM_FULL (default 0) — 1 = do not strip heavy Swarm sub-reports
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bybit_nl_feed")

_NL_URL = (os.environ.get("SYGNIF_NEUROLINKED_HOST_URL") or
           os.environ.get("SYGNIF_NEUROLINKED_HTTP_URL") or
           "http://127.0.0.1:8889").rstrip("/")

SYMBOLS = [s.strip() for s in os.environ.get("BYBIT_NL_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",")]
INTERVAL = int(os.environ.get("BYBIT_NL_INTERVAL", "30"))
BEE_URL = (os.environ.get("SYGNIF_BEE_API_URL") or "").rstrip("/")

_SWARM_ENV_KEYS = (
    "SYGNIF_SWARM_OPEN_TRADES",
    "SYGNIF_SWARM_BYBIT_CLOSED_PNL",
    "SYGNIF_SWARM_BYBIT_OPEN_PNL",
)


def _env_flag(name: str, default: str) -> bool:
    raw = (os.environ.get(name) or default).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _include_swarm_nl() -> bool:
    return _env_flag("BYBIT_NL_INCLUDE_SWARM", "1")


def _swarm_nl_full() -> bool:
    return _env_flag("BYBIT_NL_SWARM_FULL", "0")


def _swarm_interval_sec() -> int:
    try:
        return max(30, int(os.environ.get("BYBIT_NL_SWARM_INTERVAL", "90")))
    except ValueError:
        return 90


def _compute_swarm_for_nl() -> dict:
    from finance_agent.swarm_knowledge import compute_swarm  # noqa: PLC0415

    if _swarm_nl_full():
        return compute_swarm()
    saved: dict[str, str | None] = {}
    try:
        for k in _SWARM_ENV_KEYS:
            saved[k] = os.environ.get(k)
            os.environ[k] = "0"
        return compute_swarm()
    finally:
        for k, prev in saved.items():
            if prev is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev


def _format_swarm_nl_line(sw: dict) -> str:
    parts = [
        f"mean={sw.get('swarm_mean')}",
        f"label={sw.get('swarm_label')}",
        f"conflict={sw.get('swarm_conflict')}",
        f"engine={sw.get('swarm_engine')}",
    ]
    src = sw.get("sources")
    if isinstance(src, dict):
        order = ("mn", "hm", "bf", "ac", "es", "ml", "ch", "sc", "ta")
        bits = []
        for name in order:
            cell = src.get(name)
            if isinstance(cell, dict) and "vote" in cell:
                bits.append(f"{name}={cell.get('vote')}")
        if bits:
            parts.append("votes=" + ",".join(bits))
    miss = sw.get("missing_files")
    if isinstance(miss, list) and miss:
        parts.append("missing=" + ",".join(str(x) for x in miss[:5]))
    return "BYBIT_SWARM_LAYER " + " ".join(parts)


def _format_hivemind_nl_line(sw: dict) -> str | None:
    src = sw.get("sources") if isinstance(sw.get("sources"), dict) else {}
    cell = src.get("hm") if isinstance(src.get("hm"), dict) else None
    hm = sw.get("hivemind_explore") if isinstance(sw.get("hivemind_explore"), dict) else {}
    if not hm and not cell:
        return None
    parts: list[str] = ["BYBIT_HIVEMIND_LAYER"]
    if "ok" in hm:
        parts.append(f"ok={hm.get('ok')}")
    if hm.get("slots_voting_n") is not None:
        parts.append(f"slots_voting={hm.get('slots_voting_n')}")
    if hm.get("markets_trading_n") is not None:
        parts.append(f"markets_trading={hm.get('markets_trading_n')}")
    if cell and cell.get("vote") is not None:
        parts.append(f"hm_vote={cell.get('vote')}")
    det = str((cell or {}).get("detail") or "")[:120]
    if det:
        parts.append(f"detail={det}")
    return " ".join(parts) if len(parts) > 1 else None


def _get(endpoint: str, params: str = "") -> dict:
    url = f"https://api.bybit.com{endpoint}" + (f"?{params}" if params else "")
    req = urllib.request.Request(url, headers={"User-Agent": "sygnif-nl-feed/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=8).read())


def _nl_post_timeout() -> float:
    raw = (os.environ.get("BYBIT_NL_POST_TIMEOUT_SEC") or "25").strip() or "25"
    try:
        return max(5.0, min(120.0, float(raw)))
    except ValueError:
        return 25.0


def _nl_feed(text: str) -> None:
    try:
        data = json.dumps(
            {
                "text": text,
                "skip_claude_bridge": True,
                "skip_sygnif_bridge": True,
            }
        ).encode()
        req = urllib.request.Request(
            f"{_NL_URL}/api/input/text", data=data,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=_nl_post_timeout())
    except Exception as e:
        log.warning("NeuroLinked feed error: %s", e)


def fetch_ticker(symbol: str) -> dict | None:
    try:
        r = _get("/v5/market/tickers", f"category=linear&symbol={symbol}")
        items = r.get("result", {}).get("list", [])
        return items[0] if items else None
    except Exception as e:
        log.warning("ticker %s error: %s", symbol, e)
        return None


def fetch_ls_ratio(symbol: str) -> tuple[float, float] | None:
    try:
        r = _get("/v5/market/account-ratio", f"category=linear&symbol={symbol}&period=1h&limit=1")
        items = r.get("result", {}).get("list", [])
        if items:
            return float(items[0]["buyRatio"]), float(items[0]["sellRatio"])
    except Exception:
        pass
    return None


def fetch_oi(symbol: str) -> float | None:
    try:
        r = _get("/v5/market/open-interest", f"category=linear&symbol={symbol}&intervalTime=1h&limit=2")
        items = r.get("result", {}).get("list", [])
        if len(items) >= 2:
            curr = float(items[0]["openInterest"])
            prev = float(items[1]["openInterest"])
            return curr, round((curr - prev) / prev * 100, 2) if prev else 0
        elif items:
            return float(items[0]["openInterest"]), 0
    except Exception:
        pass
    return None


def build_signal(symbol: str) -> str | None:
    ticker = fetch_ticker(symbol)
    if not ticker:
        return None

    price = float(ticker.get("lastPrice", 0))
    pct24h = float(ticker.get("price24hPcnt", 0)) * 100
    vol24h = float(ticker.get("volume24h", 0))
    turnover24h = float(ticker.get("turnover24h", 0))
    funding = float(ticker.get("fundingRate", 0)) * 100
    oi_val = float(ticker.get("openInterestValue", 0))
    mark = float(ticker.get("markPrice", price))
    index = float(ticker.get("indexPrice", price))
    basis_pct = round((mark - index) / index * 100, 4) if index else 0

    ls = fetch_ls_ratio(symbol)
    ls_str = f" longs={ls[0]:.1%} shorts={ls[1]:.1%}" if ls else ""

    oi_data = fetch_oi(symbol)
    oi_change = f" OI_chg={oi_data[1]:+.2f}%" if oi_data else ""

    # Interpret signals
    sentiment = "NEUTRAL"
    if funding < -0.01:
        sentiment = "BEARISH_FUNDING"
    elif funding > 0.01:
        sentiment = "BULLISH_FUNDING"
    if ls and ls[0] > 0.55:
        sentiment += "_OVERLEVERAGED_LONG"
    elif ls and ls[0] < 0.45:
        sentiment += "_OVERLEVERAGED_SHORT"

    return (
        f"BYBIT_MARKET {symbol} price={price:.2f} 24h={pct24h:+.2f}% "
        f"vol={vol24h:.0f} turnover={turnover24h/1e6:.1f}M "
        f"funding={funding:+.4f}% basis={basis_pct:+.4f}%"
        f"{ls_str}{oi_change} OI={oi_val/1e6:.1f}M sentiment={sentiment}"
    )


def fetch_bee_signal() -> str | None:
    if not BEE_URL:
        return None
    try:
        health = json.loads(urllib.request.urlopen(f"{BEE_URL}/health", timeout=3).read())
        topo = json.loads(urllib.request.urlopen(f"{BEE_URL}/topology", timeout=3).read())
        status = health.get("status", "?")
        peers = topo.get("connected", 0)
        population = topo.get("population", 0)
        depth = topo.get("depth", 0)
        return (f"SWARM_BEE status={status} peers={peers} population={population} "
                f"depth={depth} version={health.get('version','?')}")
    except Exception as e:
        log.warning("Bee probe error: %s", e)
        return None


def main() -> None:
    log.info(
        "Bybit NeuroLinked market feed starting — symbols=%s interval=%ss bee=%s swarm_nl=%s",
        SYMBOLS,
        INTERVAL,
        bool(BEE_URL),
        _include_swarm_nl(),
    )
    _nl_feed("MARKET_FEED online symbols=" + ",".join(SYMBOLS) + (" bee=connected" if BEE_URL else ""))

    bee_tick = 0
    last_swarm_mono = 0.0
    swarm_iv = float(_swarm_interval_sec())
    while True:
        loop_start = time.monotonic()
        for sym in SYMBOLS:
            signal = build_signal(sym)
            if signal:
                log.info(signal)
                _nl_feed(signal)
            time.sleep(1)

        # Feed Bee every 3rd cycle (~90s)
        bee_tick += 1
        if bee_tick % 3 == 0:
            bee_signal = fetch_bee_signal()
            if bee_signal:
                log.info(bee_signal)
                _nl_feed(bee_signal)

        if _include_swarm_nl() and (loop_start - last_swarm_mono) >= swarm_iv:
            last_swarm_mono = loop_start
            try:
                doc = _compute_swarm_for_nl()
                s_line = _format_swarm_nl_line(doc)
                log.info(s_line)
                _nl_feed(s_line)
                hm_line = _format_hivemind_nl_line(doc)
                if hm_line:
                    log.info(hm_line)
                    _nl_feed(hm_line)
            except Exception as exc:
                log.warning("Swarm/NL layer error: %s", exc)

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
