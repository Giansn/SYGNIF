"""sygnif_telegram_relay.py — relay DLP commentary → telegram.

Watches swarm topic `agent.commentary` for new entries. Filters by salience
(routine entries are skipped by default — too noisy). Forwards higher-salience
entries to the configured Telegram chat via the hedge bot.

Env (loaded from /etc/sygnif/telegram-relay.env or process env):
  SYGNIF_TG_RELAY_TOKEN       Telegram bot token (defaults to SYGNIF_HEDGE_BOT_TOKEN)
  SYGNIF_TG_RELAY_CHAT_ID     Chat to post to
  SYGNIF_TG_RELAY_INTERVAL    Poll cadence sec (default 30)
  SYGNIF_TG_RELAY_SKIP_LABELS Comma-separated labels to NOT relay
                              (default: "routine")
  SYGNIF_TG_RELAY_DRY_RUN     1 = log only, no send (default 0)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

INTERVAL    = int(os.environ.get("SYGNIF_TG_RELAY_INTERVAL", "30"))
TOKEN       = (os.environ.get("SYGNIF_TG_RELAY_TOKEN")
               or os.environ.get("SYGNIF_HEDGE_BOT_TOKEN")
               or os.environ.get("TELEGRAM_BOT_TOKEN") or "")
CHAT_ID     = os.environ.get("SYGNIF_TG_RELAY_CHAT_ID", "")
SKIP_LABELS = set(s.strip() for s in
                  os.environ.get("SYGNIF_TG_RELAY_SKIP_LABELS", "routine").split(",")
                  if s.strip())
DRY_RUN     = os.environ.get("SYGNIF_TG_RELAY_DRY_RUN", "0") == "1"

CURSOR_PATH = Path.home() / ".sygnif" / "tg-relay-cursor.json"
SWARM_DB    = "/var/lib/sygnif/swarm.db"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler()])
log = logging.getLogger("tg_relay")


def load_cursor() -> float:
    if not CURSOR_PATH.exists():
        return time.time() - 600
    try:
        return float(json.loads(CURSOR_PATH.read_text()).get("last_ts",
                                                              time.time() - 600))
    except Exception:
        return time.time() - 600


def save_cursor(ts: float) -> None:
    CURSOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_PATH.write_text(json.dumps({"last_ts": ts, "saved_at": time.time()}))


def fetch_new(since_ts: float) -> list[dict]:
    conn = sqlite3.connect(f"file:{SWARM_DB}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT created, content, meta, tags FROM swarm_entries "
        "WHERE topic='agent.commentary' AND created > ? "
        "ORDER BY created ASC LIMIT 20",
        (since_ts,),
    ).fetchall()
    conn.close()
    out = []
    for created, content, meta_str, tags in rows:
        try:
            m = json.loads(meta_str) if meta_str else {}
        except Exception:
            m = {}
        out.append({"ts": float(created), "text": content, "meta": m, "tags": tags or ""})
    return out


def send_telegram(text: str) -> bool:
    if not TOKEN or not CHAT_ID:
        log.warning("missing token or chat_id — skipping send")
        return False
    if DRY_RUN:
        log.info("DRY_RUN tg send: %s", text[:120])
        return True
    payload = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        with urllib.request.urlopen(url, data=payload, timeout=10) as r:
            return 200 <= r.status < 300
    except Exception as e:
        log.warning("telegram send failed: %s", e)
        return False


def format_for_tg(entry: dict) -> str:
    """Wrap commentary with tiny header showing salience + correlation."""
    meta = entry.get("meta") or {}
    salience = meta.get("salience", "?")
    template = meta.get("template_id", "?")
    text = entry["text"]
    # HTML-escape user content (simple: just the basics)
    safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<b>SYGNIF</b> · <i>{salience}</i> · <code>{template}</code>\n\n{safe}"


def cycle() -> None:
    cursor = load_cursor()
    entries = fetch_new(cursor)
    if not entries:
        return
    log.info("found %d new commentary entries since cursor %.0f", len(entries), cursor)
    new_cursor = cursor
    for e in entries:
        meta = e.get("meta") or {}
        salience = meta.get("salience", "")
        if salience in SKIP_LABELS:
            log.debug("skipping salience=%s", salience)
        else:
            msg = format_for_tg(e)
            ok = send_telegram(msg)
            log.info("relayed salience=%s ok=%s tpl=%s", salience, ok,
                     meta.get("template_id"))
        new_cursor = max(new_cursor, e["ts"])
    save_cursor(new_cursor)


def main() -> None:
    log.info("tg_relay started (interval=%ds dry_run=%s skip_labels=%s)",
             INTERVAL, DRY_RUN, sorted(SKIP_LABELS))
    if not TOKEN:
        log.warning("NO TELEGRAM TOKEN — relay will silently skip all sends")
    if not CHAT_ID:
        log.warning("NO CHAT_ID — relay will silently skip all sends")
    while True:
        try:
            cycle()
        except Exception:
            log.exception("cycle crashed")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    if "--once" in sys.argv:
        cycle()
    else:
        main()
