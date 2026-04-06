#!/usr/bin/env python3
"""
Sygnif Bot — Telegram bot for spot trading status.
Responds to /status with a formatted list of open trades from Freqtrade API.

Usage:
  python3 sygnif_bot.py
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

# --- Config ---
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
FT_API = os.environ.get("FT_API", "http://127.0.0.1:8080/api/v1")
FT_USER = os.environ.get("FT_USER", "freqtrader")
FT_PASS = os.environ.get("FT_PASS", "CHANGE_ME")

# Load .env if present (simple key=value parser)
def load_dotenv():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

load_dotenv()
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", TG_TOKEN)
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", TG_CHAT)


def tg_send(text, parse_mode="Markdown"):
    resp = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={
            "chat_id": TG_CHAT,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        },
        timeout=10,
    )
    return resp.json()


def tg_get_updates(offset=0):
    resp = requests.get(
        f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
        params={"offset": offset, "timeout": 30},
        timeout=35,
    )
    return resp.json().get("result", [])


def ft_login():
    resp = requests.post(f"{FT_API}/token/login", auth=(FT_USER, FT_PASS), timeout=5)
    resp.raise_for_status()
    return resp.json()["access_token"]


def ft_get(endpoint, token):
    return requests.get(
        f"{FT_API}/{endpoint}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    ).json()


def duration_str(seconds):
    if not seconds or seconds < 0:
        return "--"
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h}h{m:02d}m" if h else f"{m}m"


def build_status():
    """Build /status report: list of open trades + portfolio summary."""
    token = ft_login()
    endpoints = ["status", "profit", "show_config", "whitelist"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda ep: ft_get(ep, token), endpoints))
    trades, profit, config, whitelist = results

    now_utc = datetime.now(timezone.utc)
    now_str = now_utc.strftime("%H:%M UTC")
    mode = "DRY" if config.get("dry_run") else "LIVE"
    state = config.get("state", "?").upper()
    pairs_count = len(whitelist.get("whitelist", []))
    max_trades = config.get("max_open_trades", "?")

    lines = []
    lines.append(f"*SYGNIF* | {state} | {mode}")
    lines.append(f"_{now_str} | {pairs_count} pairs | {len(trades)}/{max_trades} slots_")
    lines.append("")

    # Portfolio
    wallet = config.get("dry_run_wallet", 100) if config.get("dry_run") else "?"
    total_profit = profit.get("profit_all_coin", 0) or 0
    closed_profit = profit.get("profit_closed_coin", 0) or 0
    wins = profit.get("winning_trades", 0)
    losses = profit.get("losing_trades", 0)
    total_closed = wins + losses
    win_rate = f"{wins / total_closed * 100:.0f}%" if total_closed else "--"

    lines.append(f"*Wallet:* `{wallet}` USDT")
    lines.append(f"*P/L:* `{total_profit:+.4f}` USDT | Closed: `{closed_profit:+.4f}`")
    lines.append(f"*W/L:* {wins}/{losses} ({win_rate})")

    # Best / Worst
    if profit.get("best_pair"):
        br = (profit.get("best_rate", 0) or 0) * 100
        lines.append(f"*Best:* {profit['best_pair']} `{br:+.2f}%`")
    if profit.get("worst_pair"):
        wr = (profit.get("worst_rate", 0) or 0) * 100
        lines.append(f"*Worst:* {profit['worst_pair']} `{wr:+.2f}%`")

    lines.append("")

    # Open trades list
    if trades:
        lines.append("*Open Trades:*")
        for i, t in enumerate(
            sorted(trades, key=lambda x: x.get("profit_ratio", 0), reverse=True), 1
        ):
            pair = t["pair"].replace("/USDT", "")
            pct = t.get("profit_ratio", 0) * 100
            pnl = t.get("profit_abs", 0) or 0
            dur = duration_str(t.get("trade_duration"))
            tag = (t.get("enter_tag") or "")[:12]
            emoji = "\U0001f7e2" if pct >= 0 else "\U0001f534"

            line = f"{emoji} *{pair}* `{pct:+.2f}%` ({pnl:+.4f}) | {dur}"
            if tag:
                line += f" | _{tag}_"
            lines.append(line)

        total_unreal = sum(t.get("profit_abs", 0) or 0 for t in trades)
        total_stake = sum(t.get("stake_amount", 0) or 0 for t in trades)
        lines.append("")
        lines.append(f"*Total:* `{total_unreal:+.4f}` USDT | Stake: `{total_stake:.2f}`")
    else:
        lines.append("_No open trades_")

    return "\n".join(lines)


def handle_command(text):
    """Handle incoming commands. Returns response text or None."""
    cmd = text.strip().lower().split()[0] if text.strip() else ""

    if cmd == "/status":
        try:
            return build_status()
        except requests.ConnectionError:
            return "*Error:* Cannot reach Freqtrade API.\nIs the bot running?"
        except Exception as e:
            return f"*Error:* `{e}`"

    if cmd == "/start":
        return (
            "*Sygnif Bot*\n\n"
            "Commands:\n"
            "`/status` — Open trades & portfolio\n"
        )

    return None


def main():
    if not TG_TOKEN or not TG_CHAT:
        print("Error: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
        print("Set them in .env or as environment variables")
        sys.exit(1)

    print(f"Sygnif Bot started (chat_id: {TG_CHAT})")
    tg_send("*Sygnif Bot online* \U0001f4c8\nType /status for trading overview")

    offset = 0
    while True:
        try:
            updates = tg_get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                text = msg.get("text", "")
                chat_id = str(msg.get("chat", {}).get("id", ""))

                if chat_id != str(TG_CHAT):
                    continue

                reply = handle_command(text)
                if reply:
                    tg_send(reply)

        except KeyboardInterrupt:
            print("\nStopped")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
