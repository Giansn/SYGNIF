"""agent/news_features.py — read recent news headlines for snapshot embedding.

Reads from swarm topic ``news.event`` (written by sygnif-news-feed.service)
and / or, as a fallback, parses the news-feed log tail for events posted
before the swarm-write enhancement landed.

Used by decision_snapshot.build_snapshot() to embed news features in every
training row so a downstream model can correlate macro / geopolitical event
flow with trade outcomes.

The news feed produces structured rows with severity (high/med/low) and
tags ({geopol, fed, macro, regulation, exchange, etf, tech}). It does NOT
emit per-article sentiment scores today — adding that would need a separate
scorer (out of scope here). For now we treat severity as a coarse intensity
proxy: high=1.0, med=0.5, low=0.0.

Module is read-only and tolerant: any error → "ok": False with zero-filled
fields so callers can rely on the same key shape.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import sqlite3
import time
from typing import Any

SWARM_DB = pathlib.Path("/var/lib/sygnif/swarm.db")
NEWS_LOG = pathlib.Path("/var/log/sygnif/news-feed.log")
NEWS_TOPIC = "news.event"
NEWS_AGENT = "sygnif-news-feed"

SEVERITY_INTENSITY = {"high": 1.0, "med": 0.5, "low": 0.0}

# Log line shape (regex):
# 2026-05-10 20:51:50,389 INFO posted high/geopol gdelt-geopol — Adolescence ...
_LOG_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO posted "
    r"(?P<sev>high|med|low)/(?P<tags>[\w\-,]+) (?P<src>[\w\-]+) — (?P<headline>.+)$"
)


def _empty(reason: str = "no_signal") -> dict:
    return {
        "ok":                 False,
        "reason":             reason,
        "age_s":              None,
        "n_articles":         0,
        "n_recent":           0,
        "sentiment_avg":      None,
        "sentiment_max_abs":  0.0,
        "btc_mentioned":      0,
        "categories":         {},
        "severity_counts":    {"high": 0, "med": 0, "low": 0},
        "latest_headline":    "",
        "latest_source":      "",
        "fresh_event_flag":   False,
    }


def _read_swarm(lookback_s: int) -> list[dict]:
    """Pull recent news rows from swarm. Returns [] if topic absent or db locked."""
    if not SWARM_DB.exists():
        return []
    cutoff = time.time() - lookback_s
    try:
        # Read-only, short busy timeout — never block planner.
        uri = f"file:{SWARM_DB.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            cur = conn.execute(
                "SELECT created, content, meta FROM swarm_entries "
                "WHERE topic = ? AND created >= ? "
                "ORDER BY created DESC LIMIT 200",
                (NEWS_TOPIC, cutoff),
            )
            return [
                {"created": r[0], "content": r[1] or "", "meta_raw": r[2] or "{}"}
                for r in cur.fetchall()
            ]
        finally:
            conn.close()
    except Exception:
        return []


def _read_log_tail(lookback_s: int, max_lines: int = 500) -> list[dict]:
    """Parse the news-feed log tail for posted events. Bridge for the period
    before the swarm-write enhancement lands or when swarm has no rows yet."""
    if not NEWS_LOG.exists():
        return []
    cutoff = time.time() - lookback_s
    out: list[dict] = []
    try:
        # Tail-read: grab last ~64KB which holds many cycles of log lines.
        sz = NEWS_LOG.stat().st_size
        with NEWS_LOG.open("rb") as f:
            f.seek(max(0, sz - 65536))
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        for line in text.splitlines()[-max_lines:]:
            m = _LOG_LINE_RE.match(line)
            if not m:
                continue
            ts_s = m.group("ts")
            try:
                # Log timestamps are local-naive; news-feed.service runs UTC
                # on the EC2 host (verified — the log line ts matches recent
                # posted= entries shown by `journalctl --since`). We treat
                # naive as UTC.
                ts_dt = dt.datetime.strptime(ts_s, "%Y-%m-%d %H:%M:%S")
                created = ts_dt.replace(tzinfo=dt.timezone.utc).timestamp()
            except ValueError:
                continue
            if created < cutoff:
                continue
            tags = [t for t in m.group("tags").split(",") if t and t != "-"]
            out.append({
                "created":  created,
                "content":  "",
                "meta_raw": json.dumps({
                    "severity": m.group("sev"),
                    "tags":     tags,
                    "src":      m.group("src"),
                    "headline": m.group("headline"),
                }),
            })
    except Exception:
        return []
    return out


def _categorize(tags: list[str], headline: str, severity: str,
                buckets: dict[str, int]) -> None:
    """Increment category buckets in-place. macro / geopol / crypto are the
    three top-level groups the trader cares about."""
    h = headline.lower()
    has_geopol = ("geopol" in tags) or any(
        kw in h for kw in ("war", "missile", "sanction", "tariff", "regime"))
    has_macro = (("fed" in tags) or ("macro" in tags) or ("regulation" in tags)
                 or any(kw in h for kw in ("fomc", "cpi", "powell", "treasury")))
    has_crypto = (("etf" in tags) or ("exchange" in tags) or ("tech" in tags)
                  or any(kw in h for kw in ("bitcoin", "btc", "crypto",
                                            "ethereum", "eth", "binance",
                                            "coinbase", "bybit")))
    if has_geopol:
        buckets["geopol"] = buckets.get("geopol", 0) + 1
    if has_macro:
        buckets["macro"] = buckets.get("macro", 0) + 1
    if has_crypto:
        buckets["crypto"] = buckets.get("crypto", 0) + 1


def get_news_features(lookback_minutes: int = 60) -> dict:
    """Aggregate recent news into a small feature dict for the snapshot.

    Returns:
        {
          "ok":                bool,    # True if any rows found in window
          "age_s":             float,   # seconds since most recent row
          "n_articles":        int,     # in lookback window
          "n_recent":          int,     # in last 10 min
          "sentiment_avg":     None,    # no scorer yet
          "sentiment_max_abs": float,   # severity-as-intensity proxy [0..1]
          "btc_mentioned":     int,     # n articles mentioning BTC/crypto
          "categories":        dict,    # {"macro": N, "geopol": N, "crypto": N}
          "severity_counts":   dict,    # {"high": N, "med": N, "low": N}
          "latest_headline":   str,     # truncated to 120 chars
          "latest_source":     str,
          "fresh_event_flag":  bool,    # True if any high/med in last 10 min
        }
    """
    lookback_s = max(60, int(lookback_minutes) * 60)
    rows = _read_swarm(lookback_s)
    used_log_fallback = False
    if not rows:
        rows = _read_log_tail(lookback_s)
        used_log_fallback = bool(rows)

    if not rows:
        return _empty("no_rows_in_window")

    out = _empty("populated")
    out["ok"] = True
    out["reason"] = "log_fallback" if used_log_fallback else "swarm"

    now = time.time()
    cutoff_recent = now - (10 * 60)

    n_articles = 0
    n_recent = 0
    btc_count = 0
    cats: dict[str, int] = {}
    sev_counts = {"high": 0, "med": 0, "low": 0}
    max_intensity = 0.0
    fresh_flag = False
    latest_headline = ""
    latest_source = ""
    latest_created = 0.0
    intensities_recent: list[float] = []

    for r in rows:
        try:
            meta = json.loads(r.get("meta_raw") or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}

        sev = (meta.get("severity") or "low").lower()
        if sev not in sev_counts:
            sev = "low"
        tags = meta.get("tags") or []
        if isinstance(tags, str):
            tags = [t for t in tags.split(",") if t]
        headline = meta.get("headline") or r.get("content") or ""
        src = meta.get("src") or ""

        n_articles += 1
        sev_counts[sev] += 1
        intensity = SEVERITY_INTENSITY.get(sev, 0.0)
        if intensity > max_intensity:
            max_intensity = intensity

        # Recent sub-window (last 10 min)
        created = r.get("created") or 0
        if created >= cutoff_recent:
            n_recent += 1
            intensities_recent.append(intensity)
            if sev in ("high", "med"):
                fresh_flag = True

        # BTC / crypto mention proxy
        h_low = headline.lower()
        if any(kw in h_low for kw in ("bitcoin", "btc", "crypto", "ethereum",
                                       "eth", "etf")) or "exchange" in tags or "tech" in tags or "etf" in tags:
            btc_count += 1

        _categorize(tags, headline, sev, cats)

        # Track latest (rows already DESC by created)
        if created > latest_created:
            latest_created = created
            latest_headline = headline[:120]
            latest_source = src

    age_s = round(now - latest_created, 1) if latest_created else None

    out.update({
        "age_s":              age_s,
        "n_articles":         n_articles,
        "n_recent":           n_recent,
        "sentiment_avg":      None,    # no scorer wired yet
        "sentiment_max_abs":  round(max_intensity, 3),
        "btc_mentioned":      btc_count,
        "categories":         cats,
        "severity_counts":    sev_counts,
        "latest_headline":    latest_headline,
        "latest_source":      latest_source,
        "fresh_event_flag":   bool(fresh_flag),
    })
    return out


if __name__ == "__main__":
    # Quick CLI sanity check: python3 -m agent.news_features [minutes]
    import sys
    mins = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    print(json.dumps(get_news_features(mins), indent=2, default=str))
