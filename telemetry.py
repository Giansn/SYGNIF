#!/usr/bin/env python3
"""
Claude NFI Bot Telemetry — Read-only Telegram reports.
Queries Freqtrade API locally and sends formatted overview to Telegram.

Usage:
  python3 telemetry.py              # Send report once
  python3 telemetry.py --loop 4h    # Send every 4 hours
  python3 telemetry.py --loop 30m   # Send every 30 minutes
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

# --- Config ---
FT_API = "http://127.0.0.1:8080/api/v1"
FT_USER = "freqtrader"
FT_PASS = "CHANGE_ME"
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")


def ft_login():
    resp = requests.post(f"{FT_API}/token/login", auth=(FT_USER, FT_PASS), timeout=5)
    resp.raise_for_status()
    return resp.json()["access_token"]


def ft_get(endpoint, token):
    return requests.get(f"{FT_API}/{endpoint}", headers={"Authorization": f"Bearer {token}"}, timeout=5).json()


def tg_send(text):
    requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True},
        timeout=10,
    )


def duration_str(seconds):
    if not seconds:
        return "--"
    h, m = divmod(int(seconds), 3600)
    m = m // 60
    return f"{h}h{m:02d}m" if h else f"{m}m"


def build_report():
    token = ft_login()
    trades = ft_get("status", token)
    profit = ft_get("profit", token)
    config = ft_get("show_config", token)
    whitelist = ft_get("whitelist", token)
    perf = ft_get("performance", token)

    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    mode = "DRY RUN" if config.get("dry_run") else "LIVE"
    state = config.get("state", "unknown").upper()
    pairs_count = len(whitelist.get("whitelist", []))
    max_trades = config.get("max_open_trades", "?")

    # --- Header ---
    lines = [f"*Claude NFI Bot* | {state} | {mode}", f"_{now} | {pairs_count} pairs | {len(trades)}/{max_trades} trades_", ""]

    # --- Portfolio ---
    wallet = config.get("dry_run_wallet", 100) if config.get("dry_run") else "?"
    total_profit = profit.get("profit_all_coin", 0) or 0
    total_pct = (profit.get("profit_all_ratio_mean", 0) or 0) * 100
    closed_profit = profit.get("profit_closed_coin", 0) or 0
    wins = profit.get("winning_trades", 0)
    losses = profit.get("losing_trades", 0)
    total_closed = wins + losses
    win_rate = f"{(wins/total_closed*100):.0f}%" if total_closed > 0 else "--"

    sign = "+" if total_profit >= 0 else ""
    lines.append(f"*Portfolio:* `{wallet}` USDT | P/L: `{sign}{total_profit:.4f}` ({total_pct:+.2f}%)")
    lines.append(f"*Closed:* `{sign}{closed_profit:.4f}` | W/L: {wins}/{losses} ({win_rate})")

    # --- Best/Worst ---
    if profit.get("best_pair"):
        bp = profit["best_pair"]
        br = (profit.get("best_rate", 0) or 0) * 100
        lines.append(f"*Best:* {bp} `{br:+.2f}%`")
    if profit.get("worst_pair"):
        wp = profit["worst_pair"]
        wr = (profit.get("worst_rate", 0) or 0) * 100
        lines.append(f"*Worst:* {wp} `{wr:+.2f}%`")

    lines.append("")

    # --- Open Trades ---
    if trades:
        lines.append("*Open Trades:*")
        lines.append("`ID  Pair          P/L%     Dur    Tag`")
        for t in sorted(trades, key=lambda x: x.get("profit_ratio", 0), reverse=True):
            tid = str(t["trade_id"]).ljust(3)
            pair = t["pair"].replace("/USDT", "").ljust(12)
            pct = f"{t.get('profit_ratio', 0) * 100:+.2f}%".rjust(7)
            dur = duration_str(t.get("trade_duration")).rjust(6)
            tag = (t.get("enter_tag") or "--")[:10]
            lines.append(f"`{tid} {pair} {pct} {dur}  {tag}`")

        # Total unrealized
        total_unreal = sum(t.get("profit_abs", 0) or 0 for t in trades)
        total_stake = sum(t.get("stake_amount", 0) or 0 for t in trades)
        lines.append(f"`{'':3} {'TOTAL':12} {total_unreal:+.4f} USDT  (stake: {total_stake:.2f})`")
    else:
        lines.append("_No open trades_")

    # --- Performance Top 5 ---
    if perf and len(perf) > 0:
        lines.append("")
        lines.append("*Performance (closed):*")
        for p in perf[:5]:
            pair = p.get("pair", "?").replace("/USDT", "")
            pnl = p.get("profit", 0)
            count = p.get("count", 0)
            lines.append(f"  {pair}: `{pnl:+.4f}` ({count} trades)")

    return "\n".join(lines)


def parse_interval(s):
    m = re.match(r"(\d+)\s*(h|m|s)?", s, re.I)
    if not m:
        return 3600
    val = int(m.group(1))
    unit = (m.group(2) or "h").lower()
    return val * {"h": 3600, "m": 60, "s": 1}[unit]


def main():
    parser = argparse.ArgumentParser(description="Claude NFI Bot Telemetry")
    parser.add_argument("--loop", type=str, help="Send reports on interval (e.g. 4h, 30m)")
    parser.add_argument("--stdout", action="store_true", help="Print to stdout instead of Telegram")
    args = parser.parse_args()

    if args.loop:
        interval = parse_interval(args.loop)
        print(f"Telemetry loop: every {interval}s ({args.loop})")
        while True:
            try:
                report = build_report()
                if args.stdout:
                    print(report.replace("*", "").replace("`", "").replace("_", ""))
                else:
                    tg_send(report)
                    print(f"[{datetime.now().strftime('%H:%M')}] Report sent")
            except Exception as e:
                print(f"Error: {e}")
            time.sleep(interval)
    else:
        try:
            report = build_report()
            if args.stdout:
                print(report.replace("*", "").replace("`", "").replace("_", ""))
            else:
                tg_send(report)
                print("Report sent to Telegram")
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
