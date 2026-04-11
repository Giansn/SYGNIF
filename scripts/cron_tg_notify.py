#!/usr/bin/env python3
"""
Send cron job output to the Sygnif Agent Telegram (same token/chat as finance_agent).
Reads message body from stdin. Title from argv (optional).
Env: .env — AGENT_BOT_TOKEN + AGENT_CHAT_ID (preferred), or legacy SYGNIF_HEDGE/FINANCE + TELEGRAM_CHAT_ID.

Resolution order (first file that contains usable keys wins):
  1) $SYGNIF_AGENT_ENV_PATH
  2) $SYGNIF_REPO/.env
  3) ~/SYGNIF/.env
  4) ~/xrp_claude_bot/.env  (legacy tree; often holds @sygnif_agent_bot keys)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

_default_repo = Path(os.environ.get("SYGNIF_REPO", str(Path.home() / "SYGNIF")))
TG_MAX = 4096


def _read_telegram_kv(path: str) -> dict[str, str]:
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
    return kv


def _token_chat_from_kv(kv: dict[str, str]) -> tuple[str, str]:
    token = (
        (kv.get("AGENT_BOT_TOKEN") or "").strip()
        or (kv.get("SYGNIF_HEDGE_BOT_TOKEN") or "").strip()
        or (kv.get("FINANCE_BOT_TOKEN") or "").strip()
    )
    chat = (kv.get("AGENT_CHAT_ID") or "").strip() or (
        kv.get("TELEGRAM_CHAT_ID") or ""
    ).strip()
    return token, chat


def resolve_agent_env_path_or_none() -> str | None:
    """Return path to first .env that defines agent Telegram keys, or None."""
    home = Path.home()
    explicit = (os.environ.get("SYGNIF_AGENT_ENV_PATH") or "").strip()
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(Path(os.environ.get("SYGNIF_REPO", str(home / "SYGNIF"))) / ".env")
    candidates.append(home / "SYGNIF" / ".env")
    candidates.append(home / "xrp_claude_bot" / ".env")

    for p in candidates:
        if not p.is_file():
            continue
        sp = str(p.resolve())
        kv = _read_telegram_kv(sp)
        token, chat = _token_chat_from_kv(kv)
        if token and chat:
            return sp
    return None


def resolve_agent_env_path() -> str:
    """Return absolute path to a .env that defines agent Telegram keys; exit 2 if none."""
    tried = _agent_env_candidates_tried()
    p = resolve_agent_env_path_or_none()
    if p:
        return p
    print(
        "cron_tg_notify: no .env with AGENT_BOT_TOKEN+AGENT_CHAT_ID "
        "(or legacy FINANCE/TELEGRAM). Tried:\n  "
        + "\n  ".join(tried or ["(no candidate files)"]),
        file=sys.stderr,
    )
    sys.exit(2)
    return ""


def _agent_env_candidates_tried() -> list[str]:
    home = Path.home()
    explicit = (os.environ.get("SYGNIF_AGENT_ENV_PATH") or "").strip()
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(Path(os.environ.get("SYGNIF_REPO", str(home / "SYGNIF"))) / ".env")
    candidates.append(home / "SYGNIF" / ".env")
    candidates.append(home / "xrp_claude_bot" / ".env")
    out: list[str] = []
    for p in candidates:
        if p.is_file():
            out.append(str(p.resolve()))
    return out


def load_keys(path: str) -> tuple[str, str]:
    kv = _read_telegram_kv(path)
    token, chat = _token_chat_from_kv(kv)
    if not token or not chat:
        print(
            "cron_tg_notify: set AGENT_BOT_TOKEN + AGENT_CHAT_ID (or legacy FINANCE/TELEGRAM keys)",
            file=sys.stderr,
        )
        sys.exit(2)
    return token, chat


def send_agent_telegram(title: str, body: str, *, env_path: str | None = None) -> None:
    """
    Send one message to @sygnif_agent_bot (AGENT_BOT_TOKEN + AGENT_CHAT_ID in .env).
    Used by cron wrappers and sentiment_health_watch urgent alerts.
    """
    if env_path:
        path = env_path
    else:
        path = resolve_agent_env_path_or_none() or resolve_agent_env_path()
    token, chat = load_keys(path)
    text = f"{title.strip()}\n\n{(body or '').strip() or '(no body)'}"
    if len(text) > TG_MAX - 50:
        text = text[: TG_MAX - 80] + "\n\n…(truncated for Telegram)"
    send_chunk(token, chat, text)


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
    send_agent_telegram(title, body)


if __name__ == "__main__":
    main()
