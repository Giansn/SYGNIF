"""sygnif_funding_harvester.py — funding-rate harvest scanner (Tier 2.4).

Polls Bybit perp funding rate every cycle. When rate > THRESHOLD per
funding interval (8h), logs a "would harvest" decision with full context
to the swarm. Does NOT execute trades yet — execution path requires a
hedge construction (delta-neutral perp + spot or perp + offsetting
synthetic) that needs careful work to ship safely.

This skeleton:
  • Establishes the polling cadence
  • Captures funding history for backtesting
  • Logs "would harvest" decisions for review (so we can backtest the
    strategy's expectancy before letting it touch the wallet)
  • Emits to swarm topic funding_harvester.signal so future executor
    can consume

When ready to go live, implement:
  • Hedge construction (long perp + short equivalent quantity in spot,
    or perp + short option synthetic for the funding period)
  • Position close logic at the end of the funding interval
  • PnL attribution = funding payment received - hedge slippage - fees

Env:
  SYGNIF_FUNDING_HARVEST_THRESHOLD_PER_8H  default 0.01 (= 0.01%/8h ≈ 11% APR)
  SYGNIF_FUNDING_HARVEST_INTERVAL          default 300  (poll cadence sec)
  SYGNIF_FUNDING_HARVEST_DRY_RUN           default 1   (1 = log only)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/home/ubuntu/sygnif-agent-mirror")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

THRESHOLD_PCT_8H  = float(os.environ.get("SYGNIF_FUNDING_HARVEST_THRESHOLD_PER_8H", "0.01"))
INTERVAL_SEC      = int(os.environ.get("SYGNIF_FUNDING_HARVEST_INTERVAL", "300"))
DRY_RUN           = os.environ.get("SYGNIF_FUNDING_HARVEST_DRY_RUN", "1") == "1"
SYMBOL            = os.environ.get("SYGNIF_FUNDING_HARVEST_SYMBOL", "BTCUSDT")

LOG_PATH = Path.home() / ".sygnif" / "funding-harvester.log"
HISTORY_PATH = Path.home() / ".sygnif" / "funding-history.ndjson"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler()])
log = logging.getLogger("funding")


def fetch_funding(symbol: str) -> dict | None:
    """Public Bybit endpoint — no auth needed."""
    url = f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}"
    try:
        r = json.loads(urllib.request.urlopen(url, timeout=10).read())
        items = r.get("result", {}).get("list", []) or []
        if not items:
            return None
        t = items[0]
        return {
            "symbol": symbol,
            "last_price": float(t.get("lastPrice") or 0),
            "mark_price": float(t.get("markPrice") or 0),
            "funding_rate": float(t.get("fundingRate") or 0),
            "next_funding_ts_ms": int(t.get("nextFundingTime") or 0),
            "open_interest": float(t.get("openInterest") or 0),
            "ts": time.time(),
        }
    except Exception as e:
        log.warning("fetch_funding err: %s", e)
        return None


def emit_signal(payload: dict, *, level: str = "info") -> None:
    """Write to swarm + brain so the decision is visible."""
    rate = payload.get("funding_rate", 0) * 100  # to %
    rate_per_8h_pct = rate  # Bybit funding is per 8h period, value is fraction
    next_ms = payload.get("next_funding_ts_ms", 0)
    next_dt = datetime.fromtimestamp(next_ms/1000, tz=timezone.utc).isoformat() if next_ms else "?"
    apr = rate * 3 * 365  # 3 funding periods per day
    content = (f"FUNDING_HARVEST {level.upper()} {payload['symbol']} "
               f"rate={rate_per_8h_pct:+.4f}%/8h apr={apr:+.1f}% "
               f"next={next_dt} mark={payload['mark_price']:.2f}")
    try:
        import sygnif_neurons as N
        N.run("swarm.write", {
            "content": content, "swarm_id": "trading",
            "agent_id": "funding_harvester",
            "topic": "funding_harvester.signal",
            "tags": ["funding", "harvester", level, payload["symbol"]],
            "meta": payload,
        })
    except Exception as e:
        log.debug("swarm.write err: %s", e)
    # Also POST to brain for STDP context
    try:
        body = json.dumps({"text": content, "source": "funding_harvester"}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:8889/api/input/text",
            data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def append_history(payload: dict) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a") as f:
        f.write(json.dumps(payload) + "\n")


def cycle() -> None:
    f = fetch_funding(SYMBOL)
    if f is None:
        return
    append_history(f)
    rate = f["funding_rate"]  # fraction per 8h
    rate_pct = rate * 100
    apr = rate * 100 * 3 * 365
    above_thr = abs(rate_pct) >= THRESHOLD_PCT_8H

    if above_thr:
        # Decision: harvest the funding payment
        # Direction:
        #   funding_rate > 0  →  longs PAY shorts → harvest by going SHORT perp + LONG hedge
        #   funding_rate < 0  →  shorts PAY longs → harvest by going LONG perp + SHORT hedge
        side = "Sell" if rate > 0 else "Buy"
        decision = {
            **f, "decision": "would_harvest" if DRY_RUN else "harvest",
            "side": side, "rate_pct_8h": rate_pct, "apr_pct": apr,
            "threshold_pct_8h": THRESHOLD_PCT_8H, "dry_run": DRY_RUN,
        }
        emit_signal(decision, level="harvest")
        log.info("HARVEST opportunity: rate=%+.4f%%/8h apr=%+.1f%% side=%s dry_run=%s",
                 rate_pct, apr, side, DRY_RUN)
        if not DRY_RUN:
            log.warning("DRY_RUN=0 set but execution path not yet implemented; "
                        "would need delta-neutral hedge construction. Logging only.")
    else:
        log.info("funding=%+.4f%%/8h (apr %+.1f%%) — below threshold ±%.4f%%",
                 rate_pct, apr, THRESHOLD_PCT_8H)


def main() -> None:
    log.info("funding_harvester started symbol=%s threshold=%.4f%%/8h interval=%ds dry_run=%s",
             SYMBOL, THRESHOLD_PCT_8H, INTERVAL_SEC, DRY_RUN)
    while True:
        try:
            cycle()
        except Exception:
            log.exception("cycle crashed")
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    if "--once" in sys.argv:
        cycle()
    else:
        main()
