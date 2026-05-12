#!/usr/bin/env python3
"""SYGNIF foundational-news → NeuroLinked feed.

Polls macro/crypto news sources every NEWS_POLL_SEC (default 300s) and
posts severity-tagged events to the NeuroLinked brain.

Sources (in priority order, all are graceful-degradation):
  1. GDELT 2.0 doc API           — free, no auth, broad macro/geopolitical
  2. CryptoPanic public API      — only used if CRYPTOPANIC_API_KEY is set
  3. CoinTelegraph RSS feed      — fallback / supplement

Output to NL brain (POST /api/input/text):
  SYGNIF_MACRO_EVENT v1 src=gdelt severity=high tags=[geopol,war]
    headline="..." url=... ts=2026-05-05T12:34:56Z

Severity classification: keyword match (no LLM call). Keywords listed in
``SEVERITY_KEYWORDS``. Default severity is "low".

Dedup: per-source, content-hash of (source + url + title) cached on disk
at ``/var/cache/sygnif-news/seen.txt`` (cap 5000, FIFO).

Run: python3 sygnif_news_feed.py
Deployed at /opt/sygnif-services/sygnif_news_feed.py on EC2,
managed by systemd unit sygnif-news-feed.service.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path
from xml.etree import ElementTree as ET

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sygnif_news_feed")

NL_URL = (os.environ.get("SYGNIF_NEUROLINKED_HOST_URL")
          or "http://127.0.0.1:8889").rstrip("/")
POLL_SEC = int(os.environ.get("NEWS_POLL_SEC", "300"))
POST_TIMEOUT = int(os.environ.get("NEWS_POST_TIMEOUT_SEC", "30"))
HTTP_TIMEOUT = int(os.environ.get("NEWS_HTTP_TIMEOUT_SEC", "12"))
CACHE_DIR = Path(os.environ.get("NEWS_CACHE_DIR", "/var/cache/sygnif-news"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
SEEN_FILE = CACHE_DIR / "seen.txt"
SEEN_CAP = int(os.environ.get("NEWS_SEEN_CAP", "5000"))

# --- severity keyword tables (lowercase) ---
SEVERITY_KEYWORDS = {
    "high": [
        "war", "invasion", "nuclear", "missile", "fed emergency", "rate cut",
        "rate hike", "exchange hack", "bitcoin etf rejected", "btc etf approved",
        "binance shutdown", "coinbase shutdown", "default", "bankruptcy filed",
        "circuit breaker", "treasury collapse", "bank run", "currency collapse",
        "tariff", "sanctions", "ceasefire", "regime change",
    ],
    "med": [
        "fomc", "cpi", "ppi", "jobs report", "nonfarm", "unemployment",
        "sec lawsuit", "sec charges", "doj investigation", "congress",
        "regulation", "stablecoin", "ethereum upgrade", "halving",
        "rate decision", "powell", "yellen", "treasury", "ecb", "boj",
        "moody's", "downgrade", "election", "shutdown",
    ],
    "low": [
        "exchange listing", "delisting", "partnership", "integration",
        "airdrop", "fund launch", "etf inflow", "etf outflow",
    ],
}

CRYPTO_TAG_KEYWORDS = {
    "geopol": ["war", "invasion", "missile", "sanctions", "tariff", "regime"],
    "fed":    ["fomc", "fed", "powell", "rate cut", "rate hike", "rate decision"],
    "macro":  ["cpi", "ppi", "jobs report", "nonfarm", "unemployment"],
    "regulation": ["sec", "doj", "regulation", "congress"],
    "exchange": ["binance", "coinbase", "kraken", "ftx", "bybit", "okx"],
    "etf":    ["etf", "spot etf"],
    "tech":   ["upgrade", "halving", "fork", "merge"],
}


def _load_seen() -> deque:
    if SEEN_FILE.exists():
        try:
            return deque(SEEN_FILE.read_text().splitlines(), maxlen=SEEN_CAP)
        except Exception:
            pass
    return deque(maxlen=SEEN_CAP)


def _save_seen(seen: deque) -> None:
    try:
        SEEN_FILE.write_text("\n".join(seen))
    except Exception as e:
        log.warning("seen save failed: %s", e)


def _hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8", "replace")).hexdigest()[:16]


def _classify(headline: str) -> tuple[str, list[str]]:
    """Return (severity, tags) from headline keyword match."""
    h = headline.lower()
    severity = "low"
    for sev in ("high", "med"):
        if any(kw in h for kw in SEVERITY_KEYWORDS[sev]):
            severity = sev
            break
    tags = [tag for tag, kws in CRYPTO_TAG_KEYWORDS.items()
            if any(kw in h for kw in kws)]
    return severity, tags


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



def _post_swarm(src: str, severity: str, tags: list[str],
                headline: str, url: str, ts: str) -> bool:
    """Write the news event to swarm topic ``news.event`` so downstream
    decision-snapshot readers (agent.news_features) can correlate event flow
    with trade outcomes. Best-effort — never blocks the main poll loop."""
    try:
        import sygnif_neurons as N
    except Exception:
        return False
    safe_headline = headline.replace("\n", " ").replace("\r", " ")[:240]
    content = (f"NEWS {severity.upper()} {src} "
               f"tags=[{','.join(tags) if tags else 'untagged'}] "
               f"headline=\"{safe_headline}\"")
    try:
        N.run("swarm.write", {
            "content":  content,
            "swarm_id": "trading",
            "agent_id": "sygnif-news-feed",
            "topic":    "news.event",
            "tags":     ["news", severity, src] + (tags or []),
            "meta": {
                "src":      src,
                "severity": severity,
                "tags":     tags,
                "headline": safe_headline,
                "url":      url,
                "ts":       ts,
            },
        })
        return True
    except Exception as e:
        log.debug("swarm.write failed: %s", e)
        return False


def _format_event(src: str, severity: str, tags: list[str],
                  headline: str, url: str, ts: str) -> str:
    tags_s = ",".join(tags) if tags else "untagged"
    safe_headline = headline.replace("\n", " ").replace("\r", " ")[:240]
    return (f"SYGNIF_MACRO_EVENT v1 src={src} severity={severity} "
            f"tags=[{tags_s}] ts={ts} url={url} headline=\"{safe_headline}\"")


# --- source: GDELT 2.0 doc API ---
def _fetch_gdelt() -> list[dict]:
    """Pulls last-24h Bitcoin + macro articles from GDELT."""
    out = []
    queries = [
        ("bitcoin OR (\"crypto\" AND market)", "btc"),
        ("(fed OR fomc) AND (\"interest rate\" OR \"rate decision\")", "fed"),
        ("(war OR invasion OR missile) AND (russia OR ukraine OR china OR israel OR iran)", "geopol"),
    ]
    for q, qtag in queries:
        params = {
            "query": q,
            "mode": "ArtList",
            "maxrecords": "10",
            "format": "json",
            "sort": "DateDesc",
            "timespan": "24h",
        }
        url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as r:
                data = json.loads(r.read())
        except Exception as e:
            log.warning("gdelt %s failed: %s", qtag, e)
            continue
        for art in data.get("articles", [])[:10]:
            out.append({
                "src": f"gdelt-{qtag}",
                "url": art.get("url", ""),
                "title": art.get("title", "").strip(),
                "ts": art.get("seendate", ""),
            })
    return out


# --- source: CryptoPanic (auth-keyed) ---
def _fetch_cryptopanic() -> list[dict]:
    key = os.environ.get("CRYPTOPANIC_API_KEY")
    if not key:
        return []
    url = ("https://cryptopanic.com/api/v1/posts/?"
           f"auth_token={key}&public=true&kind=news&filter=hot")
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as r:
            data = json.loads(r.read())
    except Exception as e:
        log.warning("cryptopanic failed: %s", e)
        return []
    out = []
    for p in data.get("results", [])[:20]:
        out.append({
            "src": "cryptopanic",
            "url": p.get("url", ""),
            "title": (p.get("title") or "").strip(),
            "ts": p.get("published_at", ""),
        })
    return out


# --- source: CoinTelegraph RSS ---
def _fetch_cointelegraph() -> list[dict]:
    url = "https://cointelegraph.com/rss"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "sygnif-news/1.0"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            xml = r.read()
        root = ET.fromstring(xml)
    except Exception as e:
        log.warning("cointelegraph failed: %s", e)
        return []
    out = []
    for item in root.findall(".//item")[:15]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        ts = (item.findtext("pubDate") or "").strip()
        if title and link:
            out.append({"src": "cointelegraph", "url": link, "title": title, "ts": ts})
    return out


def _process(events: list[dict], seen: deque) -> int:
    """Filter, classify, emit. Returns count posted."""
    posted = 0
    for ev in events:
        if not ev.get("title") or not ev.get("url"):
            continue
        h = _hash(ev["src"], ev["url"], ev["title"])
        if h in seen:
            continue
        sev, tags = _classify(ev["title"])
        # Skip low-severity untagged noise to keep brain signal-dense
        if sev == "low" and not tags:
            seen.append(h)
            continue
        text = _format_event(ev["src"], sev, tags, ev["title"], ev["url"], ev.get("ts", ""))
        if _post_nl(text):
            posted += 1
            log.info("posted %s/%s %s — %s", sev, ",".join(tags) or "-",
                     ev["src"], ev["title"][:80])
        # Mirror to swarm so agent.news_features can read across cycles.
        _post_swarm(ev["src"], sev, tags, ev["title"],
                    ev.get("url", ""), ev.get("ts", ""))
        seen.append(h)
    return posted


def main() -> int:
    seen = _load_seen()
    log.info("sygnif-news-feed starting; cache=%d events; poll=%ds; nl=%s",
             len(seen), POLL_SEC, NL_URL)
    while True:
        t0 = time.time()
        all_events = []
        for fetcher, name in (
            (_fetch_gdelt, "gdelt"),
            (_fetch_cryptopanic, "cryptopanic"),
            (_fetch_cointelegraph, "cointelegraph"),
        ):
            try:
                events = fetcher()
                log.info("source=%s fetched=%d", name, len(events))
                all_events.extend(events)
            except Exception as e:
                log.warning("source=%s err: %s", name, e)
        posted = _process(all_events, seen)
        _save_seen(seen)
        elapsed = time.time() - t0
        log.info("cycle done: posted=%d elapsed=%.1fs sleep=%ds",
                 posted, elapsed, POLL_SEC)
        time.sleep(max(POLL_SEC - elapsed, 30))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
