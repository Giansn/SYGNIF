#!/usr/bin/env python3
"""SYGNIF trade → NeuroLinked publisher.

Closes the trade-outcome learning loop. Every cycle:

  1. Read recent rows from swarm_id="trading" with topic ∈ {
       trade.close, rt.event, rt.close, trade.open_rejected,
       sfp.outcome, sfp.ab_shadow, mm.counter_play
     }
  2. Skip rows already published (tracked in /var/lib/sygnif/nl-published.json)
  3. POST each new row to neurolinked at /api/input/text with:
       - text   : a one-line human-readable summary tagged "SYGNIF_TRADE …"
       - tags   : "sygnif,trade,<topic>,<symbol>,<side>"
       - source : "sygnif_trade"

  4. Persist last-seen-id watermark so restarts don't re-publish.

Why this matters:
  Default bybit-nl-feed posts MARKET data only. The brain learns market
  patterns, but it NEVER sees SYGNIF's actual trade decisions or outcomes.
  Without this, the dopamine signal is driven by market novelty alone —
  no credit assignment for which trade rules worked.

  This publisher gives the brain the SECOND half of the loop:
  market_state → SYGNIF_decision → outcome → reinforcement.

Env:
  SYGNIF_NL_URL          default http://127.0.0.1:8889
  SYGNIF_NL_LOOKBACK_MIN default 60   (look back N min for missed rows)
  SYGNIF_NL_INTERVAL     default 30   (seconds between cycles)
  SYGNIF_NL_WATERMARK    default /var/lib/sygnif/nl-published.json
  SYGNIF_AGENT_DIR       default /home/ubuntu/sygnif-agent-mirror

Run:
  python3 sygnif-trade-nl-publisher.py            # daemon (loops)
  python3 sygnif-trade-nl-publisher.py --once     # single pass

Logs: stdout / stderr (systemd journal).
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path

NL_URL = os.environ.get("SYGNIF_NL_URL", "http://127.0.0.1:8889").rstrip("/")
LOOKBACK_MIN = int(os.environ.get("SYGNIF_NL_LOOKBACK_MIN", "60"))
INTERVAL_S = int(os.environ.get("SYGNIF_NL_INTERVAL", "30"))
WATERMARK_PATH = Path(os.environ.get(
    "SYGNIF_NL_WATERMARK", "/var/lib/sygnif/nl-published.json"))
AGENT_DIR = Path(os.environ.get(
    "SYGNIF_AGENT_DIR", "/home/ubuntu/sygnif-agent-mirror"))

# Topics we publish. Stop-list anything trader/heartbeat/regime that's noise.
PUBLISH_TOPICS = {
    "trade.close",
    "trade.open_rejected",
    "rt.close",
    "rt.event",
    "sfp.outcome",
    "sfp.ab_shadow",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("sygnif-trade-nl")


def load_watermark() -> dict:
    if not WATERMARK_PATH.exists():
        return {"last_published_ids": {}}  # topic → max id seen
    try:
        return json.loads(WATERMARK_PATH.read_text())
    except Exception as e:
        log.warning(f"watermark parse error: {e}; resetting")
        return {"last_published_ids": {}}


def save_watermark(wm: dict) -> None:
    try:
        WATERMARK_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = WATERMARK_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(wm, separators=(",", ":")))
        tmp.replace(WATERMARK_PATH)
    except Exception as e:
        log.warning(f"watermark save failed: {e}")


def post_to_nl(text: str, tags: str, source: str = "sygnif_trade") -> bool:
    """POST one knowledge entry to neurolinked.

    Mirrors bybit_nl_market_feed.py payload exactly:
      {"text": <prefixed-text>, "skip_claude_bridge": True}

    The endpoint stores source='user' regardless; we identify our entries
    by the SYGNIF_TRADE text prefix and embed tags/source as text tokens
    so the FTS index makes them searchable.
    """
    # Embed tags + source as searchable text prefix
    full_text = f"SYGNIF_TRADE_v1 src={source} tags={tags} | {text}"
    payload = {"text": full_text, "skip_claude_bridge": True}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{NL_URL}/api/input/text",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return 200 <= r.status < 300
    except Exception as e:
        log.warning(f"NL POST failed: {e}")
        return False


def format_row(row: dict) -> tuple[str, str] | None:
    """Convert a swarm row into (text, tags) for neurolinked. Returns None if
    the row should be skipped (malformed / not interesting)."""
    topic = (row.get("topic") or "").strip()
    content = row.get("content") or ""
    created = row.get("created", "")[:19]
    # Try to extract structured meta if present
    meta = {}
    try:
        if isinstance(content, str) and content.lstrip().startswith("{"):
            meta = json.loads(content)
        elif isinstance(content, dict):
            meta = content
    except Exception:
        meta = {}

    sym = (meta.get("symbol")
           or meta.get("sym")
           or _extract_symbol(content))
    side = meta.get("side") or _extract_side(content)
    tags_l = ["sygnif", "trade", topic.replace(".", "_")]
    if sym:
        tags_l.append(str(sym))
    if side:
        tags_l.append(str(side))
    tags = ",".join(tags_l)

    if topic == "trade.close":
        # Content shape: "TRADE CLOSE  BTC-...  Sell px=120 qty=1 closedSize=1.0 closedPnl=? olid=..."
        text = f"SYGNIF_TRADE close {created}  {content[:200]}"
    elif topic == "rt.event":
        text = f"SYGNIF_TRADE rt_event {created}  {content[:200]}"
    elif topic == "rt.close":
        text = f"SYGNIF_TRADE rt_close {created}  {content[:200]}"
    elif topic == "trade.open_rejected":
        text = f"SYGNIF_TRADE open_rejected {created}  {content[:200]}"
    elif topic == "sfp.outcome":
        # Outcome row from the SFP shadow settler
        side_v = meta.get("side") or "?"
        outcome = meta.get("outcome") or "?"
        R = meta.get("R")
        bars = meta.get("bars_held")
        action = meta.get("action_emitted") or "?"
        text = (f"SYGNIF_TRADE sfp_outcome {created}  signal={meta.get('signal')} "
                f"side={side_v} action={action} outcome={outcome} R={R} bars={bars}")
        tags_l.append(action)
        tags = ",".join(tags_l)
    elif topic == "sfp.ab_shadow":
        # Production vs variant decision pair
        prod = meta.get("production_overrode")
        vara = meta.get("variant_a_would_override")
        differ = meta.get("differ")
        text = (f"SYGNIF_TRADE sfp_ab {created}  signal={meta.get('signal')} "
                f"side={meta.get('side')} prod={prod} variant_a={vara} differ={differ}")
    else:
        text = f"SYGNIF_TRADE {topic} {created}  {content[:200]}"
    return text, tags


def _extract_symbol(content: str) -> str | None:
    """Pull a BTC option symbol from a content string if present."""
    if not isinstance(content, str):
        return None
    import re
    m = re.search(r"BTC-\d+\w+\d+-\d+-[CP]-USDT", content)
    if m:
        return m.group(0)
    if "BTCUSDT" in content:
        return "BTCUSDT"
    return None


def _extract_side(content: str) -> str | None:
    if not isinstance(content, str):
        return None
    if " Buy " in content or content.startswith("Buy"):
        return "Buy"
    if " Sell " in content or content.startswith("Sell"):
        return "Sell"
    return None


def fetch_recent_rows(topic: str, limit: int = 50) -> list[dict]:
    """Pull recent rows for a single topic from the trading swarm."""
    sys.path.insert(0, str(AGENT_DIR))
    try:
        import sygnif_neurons as N  # type: ignore
    except Exception as e:
        log.error(f"can't import sygnif_neurons: {e}")
        return []
    r = N.run("swarm.recent",
              {"swarm_id": "trading", "topic": topic, "limit": limit})
    if not r.get("ok"):
        log.debug(f"swarm.recent {topic} err: {r.get('error')}")
        return []
    return list(r.get("data") or [])


def fetch_recent_rows_btc_demo(topic: str, limit: int = 50) -> list[dict]:
    """sfp.outcome / sfp.ab_shadow live in btc_demo / trading respectively."""
    sys.path.insert(0, str(AGENT_DIR))
    try:
        import sygnif_neurons as N  # type: ignore
    except Exception as e:
        log.error(f"can't import sygnif_neurons: {e}")
        return []
    r = N.run("swarm.recent",
              {"swarm_id": "btc_demo", "topic": topic, "limit": limit})
    if not r.get("ok"):
        return []
    return list(r.get("data") or [])


def _row_hash(row: dict) -> str:
    """Stable identity for a row when `id` is missing.

    EC2's swarm.recent returns rows with id=None and created="" because the
    swarm-x1-mirror replication strips those fields. We still need to dedup
    so the brain doesn't see the same trade.close 1000× per day.

    Hash: md5(topic|content). Trade rows have unique olid so content is
    unique per real event.
    """
    import hashlib
    content = row.get("content") or ""
    if isinstance(content, dict):
        content = json.dumps(content, sort_keys=True, default=str)
    topic = row.get("topic") or ""
    h = hashlib.md5()
    h.update(topic.encode("utf-8", errors="replace"))
    h.update(b"|")
    h.update(str(content).encode("utf-8", errors="replace"))
    return h.hexdigest()


def cycle(wm: dict) -> int:
    """One pass: read + publish all new rows. Returns count published.

    Watermark is a per-topic set of content-hashes (capped at 1000 each)
    when row.id isn't available. Falls back to integer-id watermark when
    rows DO carry id (X1 pathway).
    """
    n_new = 0
    last_ids = wm.setdefault("last_published_ids", {})    # int-id path
    seen_hashes = wm.setdefault("seen_hashes", {})         # content-hash path
    for topic in PUBLISH_TOPICS:
        if topic in ("sfp.outcome",):
            rows = fetch_recent_rows_btc_demo(topic, limit=50)
        else:
            rows = fetch_recent_rows(topic, limit=50)
        if not rows:
            continue
        # Determine if this fetch carries integer ids (X1 swarm.db) or
        # we need to fall back to content-hash dedup (EC2 mirrored swarm.db)
        has_int_ids = any(isinstance(r.get("id"), int) for r in rows)
        topic_hashes = set(seen_hashes.get(topic, []))

        if has_int_ids:
            max_seen = int(last_ids.get(topic, 0))
            new_rows = [r for r in rows
                        if isinstance(r.get("id"), int) and r["id"] > max_seen]
            new_rows.sort(key=lambda r: r["id"])
        else:
            new_rows = [r for r in rows
                        if _row_hash(r) not in topic_hashes]
            # Newest-first → reverse to oldest-first for chronological POSTs
            new_rows.reverse()

        for row in new_rows:
            formatted = format_row(row)
            if not formatted:
                continue
            text, tags = formatted
            ok = post_to_nl(text, tags)
            if ok:
                n_new += 1
                if has_int_ids:
                    last_ids[topic] = row["id"]
                else:
                    topic_hashes.add(_row_hash(row))
            else:
                log.warning(f"failed to publish {topic}")
                break

        # Cap hash-set size to prevent unbounded growth (1000/topic ≈ 32 KB)
        if topic_hashes:
            if len(topic_hashes) > 1000:
                # FIFO trim — convert to list, keep last 800 (most recently added are last)
                topic_hashes = set(list(topic_hashes)[-800:])
            seen_hashes[topic] = list(topic_hashes)
    if n_new:
        save_watermark(wm)
    return n_new


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true",
                    help="single pass then exit")
    args = ap.parse_args()

    log.info(f"sygnif-trade-nl starting — NL={NL_URL} interval={INTERVAL_S}s "
             f"agent_dir={AGENT_DIR} watermark={WATERMARK_PATH}")

    wm = load_watermark()
    log.info(f"watermark loaded: {wm.get('last_published_ids', {})}")

    if args.once:
        n = cycle(wm)
        log.info(f"single pass — {n} new rows published")
        return 0

    # Forever loop
    while True:
        try:
            n = cycle(wm)
            if n:
                log.info(f"published {n} trade rows to neurolinked")
        except KeyboardInterrupt:
            log.info("interrupted, exiting")
            return 0
        except Exception as e:
            log.exception(f"cycle error: {e}")
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    sys.exit(main())
