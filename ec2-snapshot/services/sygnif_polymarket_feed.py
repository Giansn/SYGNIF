#!/usr/bin/env python3
"""SYGNIF Polymarket → NeuroLinked feed.

Polls the public Polymarket Gamma API for active prediction markets,
filters to BTC- and macro-relevant questions with non-trivial liquidity,
and publishes their YES probabilities to the NeuroLinked brain.

This is the real, liquid crowd-sourced "hivemind" signal — Polymarket
markets have actual user volume (often $100k+ each) so probabilities
reflect real-money conviction, not sentiment polls.

Output (one line per relevant market):
  SYGNIF_HIVEMIND_POLYMARKET q="Will BTC hit $100k by end-2026?"
    yes=0.42 vol_total_usd=2400000 liquidity_usd=320000 ends=2026-12-31
    tag=bitcoin slug=btc-100k-2026

Cadence: every POLYMARKET_POLL_SEC (default 300s).
Sources: gamma-api.polymarket.com (no auth).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sygnif_polymarket")

NL_URL = (os.environ.get("SYGNIF_NEUROLINKED_HOST_URL")
          or "http://127.0.0.1:8889").rstrip("/")
POLL_SEC = int(os.environ.get("POLYMARKET_POLL_SEC", "300"))
POST_TIMEOUT = int(os.environ.get("POLYMARKET_POST_TIMEOUT_SEC", "30"))
HTTP_TIMEOUT = int(os.environ.get("POLYMARKET_HTTP_TIMEOUT_SEC", "20"))
MAX_MARKETS = int(os.environ.get("POLYMARKET_MAX_MARKETS", "30"))
MIN_LIQUIDITY_USD = float(os.environ.get("POLYMARKET_MIN_LIQ_USD", "5000"))
PAGE_SIZE = int(os.environ.get("POLYMARKET_PAGE_SIZE", "100"))
PAGES = int(os.environ.get("POLYMARKET_PAGES", "3"))
GAMMA_BASE = os.environ.get("POLYMARKET_GAMMA_BASE",
                              "https://gamma-api.polymarket.com").rstrip("/")

# Tag-style matching keywords. Order matters — first hit wins as `tag`.
# Priority tags are emitted by default; non-priority require POLYMARKET_INCLUDE_NONPRIORITY=1.
PRIORITY_TAGS = {"bitcoin", "ethereum", "solana", "crypto", "fed", "geopol", "macro", "etf"}
TAG_KEYWORDS = [
    ("bitcoin", ["bitcoin", " btc ", " btc?", "btc>", "btc to "]),
    ("ethereum", ["ethereum", " eth ", " eth?", "eth to "]),
    ("solana", ["solana", " sol ", "sol to "]),
    ("crypto", ["crypto", "altcoin", "shitcoin", "memecoin", "stablecoin"]),
    ("fed", ["fed ", "fomc", "powell", "rate cut", "rate hike", "interest rate", "yellen"]),
    ("geopol", ["russia", "ukraine", "china tariff", "iran", "israel", "war", "ceasefire", "putin", "xi jinping", "missile"]),
    ("etf", ["etf", "spot etf", "etf approval"]),
    ("macro", ["recession", "cpi", "inflation", "gdp", "jobs report", "unemployment", "yield curve"]),
    ("election", ["election", "presidential", "senate", "midterm"]),
]
INCLUDE_NONPRIORITY = os.environ.get("POLYMARKET_INCLUDE_NONPRIORITY", "0").strip() in ("1", "true", "yes")
MIN_YES_PROB = float(os.environ.get("POLYMARKET_MIN_YES_PROB", "0.02"))  # skip dead-money
MAX_YES_PROB = float(os.environ.get("POLYMARKET_MAX_YES_PROB", "0.98"))  # skip done-deals


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


def _classify_tag(question: str, slug: str) -> str | None:
    """Return first matching tag, or None if not market-relevant."""
    haystack = (question + " " + slug).lower()
    for tag, kws in TAG_KEYWORDS:
        if any(kw in haystack for kw in kws):
            return tag
    return None


def _fetch_markets_page(offset: int) -> list[dict]:
    params = {
        "active": "true",
        "closed": "false",
        "limit": str(PAGE_SIZE),
        "offset": str(offset),
    }
    url = f"{GAMMA_BASE}/markets?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "sygnif-polymarket/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            data = json.loads(r.read())
    except Exception as e:
        log.warning("polymarket fetch offset=%d failed: %s", offset, e)
        return []
    return data if isinstance(data, list) else []


def fetch_relevant_markets() -> list[dict]:
    """Pull a few pages of active markets and filter to relevant ones."""
    all_markets: list[dict] = []
    seen_ids: set[str] = set()
    for page in range(PAGES):
        rows = _fetch_markets_page(page * PAGE_SIZE)
        if not rows:
            break
        for m in rows:
            mid = str(m.get("id") or "")
            if mid and mid in seen_ids:
                continue
            seen_ids.add(mid)
            all_markets.append(m)
    log.info("polymarket fetched: %d markets across %d pages", len(all_markets), PAGES)
    relevant: list[dict] = []
    for m in all_markets:
        q = m.get("question") or ""
        slug = m.get("slug") or ""
        tag = _classify_tag(q, slug)
        if not tag:
            continue
        # parse outcomePrices (string-encoded JSON list "[\"0.42\",\"0.58\"]")
        prices_raw = m.get("outcomePrices") or "[]"
        try:
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        except json.JSONDecodeError:
            continue
        if not prices or not isinstance(prices, list):
            continue
        try:
            yes = float(prices[0])
        except (TypeError, ValueError):
            continue
        try:
            liq = float(m.get("liquidity") or 0)
        except ValueError:
            liq = 0.0
        if liq < MIN_LIQUIDITY_USD:
            continue
        # Skip dead-money + done-deal extremes — these add noise not signal
        if yes < MIN_YES_PROB or yes > MAX_YES_PROB:
            continue
        # Drop non-priority tags by default
        if tag not in PRIORITY_TAGS and not INCLUDE_NONPRIORITY:
            continue
        try:
            vol_total = float(m.get("volume") or 0)
        except ValueError:
            vol_total = 0.0
        end_date = (m.get("endDate") or "")[:10]
        relevant.append({
            "tag": tag,
            "question": q[:200],
            "slug": slug[:80],
            "yes": yes,
            "liq_usd": liq,
            "vol_usd": vol_total,
            "end_date": end_date,
        })
    # sort by liquidity descending (most "money on the line" first)
    relevant.sort(key=lambda m: m["liq_usd"], reverse=True)
    return relevant[:MAX_MARKETS]


def emit_markets(markets: list[dict]) -> int:
    posted = 0
    for m in markets:
        # Build a single line per market — keep it compact for NL ingest.
        line = (f"SYGNIF_HIVEMIND_POLYMARKET tag={m['tag']} yes={m['yes']:.3f} "
                f"liq_usd={int(m['liq_usd'])} vol_usd={int(m['vol_usd'])} "
                f"ends={m['end_date']} slug={m['slug']} q=\"{m['question']}\"")
        if _post_nl(line):
            posted += 1
            log.info("posted %s yes=%.2f liq=$%dk q=%s",
                     m["tag"], m["yes"], int(m["liq_usd"] / 1000),
                     m["question"][:60])
    return posted


def cycle() -> int:
    markets = fetch_relevant_markets()
    if not markets:
        log.warning("no relevant markets returned")
        return 0
    posted = emit_markets(markets)
    log.info("cycle done: relevant=%d posted=%d", len(markets), posted)
    return posted


def main() -> int:
    log.info("sygnif-polymarket-feed starting; poll=%ds nl=%s gamma=%s "
             "min_liq=$%d max_markets=%d",
             POLL_SEC, NL_URL, GAMMA_BASE, int(MIN_LIQUIDITY_USD), MAX_MARKETS)
    while True:
        t0 = time.time()
        try:
            cycle()
        except Exception as e:
            log.exception("cycle failed: %s", e)
        elapsed = time.time() - t0
        time.sleep(max(POLL_SEC - elapsed, 60))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
