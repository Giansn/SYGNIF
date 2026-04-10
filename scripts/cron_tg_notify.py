#!/usr/bin/env python3
"""
Send cron job output to the Sygnif Agent Telegram (same token/chat as finance_agent).
Reads message body from stdin. Title from argv (optional).
Env: .env — AGENT_BOT_TOKEN + AGENT_CHAT_ID (preferred), or legacy SYGNIF_HEDGE/FINANCE + TELEGRAM_CHAT_ID.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

_default_repo = Path(os.environ.get("SYGNIF_REPO", str(Path.home() / "SYGNIF")))
ENV_PATH = str(_default_repo / ".env")
TG_MAX = 4096


def load_keys(path: str) -> tuple[str, str]:
    kv: dict[str, str] = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k in (
                "AGENT_BOT_TOKEN",
                "AGENT_CHAT_ID",
                "SYGNIF_HEDGE_BOT_TOKEN",
                "FINANCE_BOT_TOKEN",
                "TELEGRAM_CHAT_ID",
            ):
                kv[k] = v
    token = (
        (kv.get("AGENT_BOT_TOKEN") or "").strip()
        or (kv.get("SYGNIF_HEDGE_BOT_TOKEN") or "").strip()
        or (kv.get("FINANCE_BOT_TOKEN") or "").strip()
    )
    chat = (kv.get("AGENT_CHAT_ID") or "").strip() or (
        kv.get("TELEGRAM_CHAT_ID") or ""
    ).strip()
    if not token or not chat:
        print(
            "cron_tg_notify: set AGENT_BOT_TOKEN + AGENT_CHAT_ID (or legacy FINANCE/TELEGRAM keys)",
            file=sys.stderr,
        )
        sys.exit(2)
    return token, chat


def send_chunk(token: str, chat: str, text: str) -> None:
    data = json.dumps(
        {
            "chat_id": chat,
            "text": text,
            "disable_web_page_preview": True,
        }
    ).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode())
        if not body.get("ok"):
            print(f"Telegram API: {body}", file=sys.stderr)
            sys.exit(1)


def main() -> None:
    title = " ".join(sys.argv[1:]).strip() or "cron"
    body = sys.stdin.read()
    if not body.strip():
        body = "(no output)"
    text = f"{title}\n\n{body}"
    if len(text) > TG_MAX - 50:
        text = text[: TG_MAX - 80] + "\n\n…(truncated for Telegram)"

    token, chat = load_keys(ENV_PATH)
    send_chunk(token, chat, text)


if __name__ == "__main__":
    main()
