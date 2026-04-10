#!/usr/bin/env python3
"""
Trading success extractor — periodic cron-friendly reporting.

Queries both spot and futures Freqtrade SQLite databases and produces a
consolidated success report with:
  - Win/loss/draw counts and win rate
  - Total and average P&L (absolute + percentage)
  - Breakdown by entry tag family and exit reason family
  - Best/worst trades
  - Streak analysis (consecutive wins/losses)
  - Daily/weekly P&L trend

Designed to be run by cron on the EC2 instance. Outputs JSON to a log file
and optionally sends a Telegram summary.

Usage:
  python3 trade_overseer/trading_success.py                    # last 24h, stdout + log
  python3 trade_overseer/trading_success.py --days 7           # last 7 days
  python3 trade_overseer/trading_success.py --telegram         # send summary to Telegram
  python3 trade_overseer/trading_success.py --json             # JSON to stdout
  python3 trade_overseer/trading_success.py --no-print         # silent, log only
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

DB_PATHS = {
    "spot": Path("user_data/tradesv3.sqlite"),
    "futures": Path("user_data/tradesv3-futures.sqlite"),
}
LOG_DIR = Path("user_data/logs")
LOG_FILE = LOG_DIR / "trading_success.jsonl"

TG_TOKEN = os.environ.get("FINANCE_BOT_TOKEN", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")


# ---------------------------------------------------------------------------
# Entry/exit family classification (shared taxonomy with touch_rate_tracker)
# ---------------------------------------------------------------------------

import re

ENTRY_FAMILIES = {
    "strong_ta": re.compile(r"^strong_ta$"),
    "strong_ta_short": re.compile(r"^strong_ta_short$"),
    "fa_s": re.compile(r"^((fa_(short_)?s)|(claude_(short_)?s))-?\d+$"),
    "fa_swing": re.compile(r"^((fa_swing)|(claude_swing))(_short)?$"),
    "swing_failure": re.compile(r"^swing_failure(_short)?$"),
}

EXIT_FAMILIES = {
    "rsi_exit":            re.compile(r"^exit_(short_)?profit_rsi_"),
    "willr_reversal":      re.compile(r"^exit_(short_)?willr_reversal$"),
    "soft_stoploss":       re.compile(r"^exit_(short_)?stoploss_conditional$"),
    "sf_ema_tp":           re.compile(r"^exit_sf_(short_)?ema_tp$"),
    "sf_vol_sl":           re.compile(r"^exit_sf_(short_)?vol_sl$"),
    "stoploss_on_exchange": re.compile(r"^stoploss_on_exchange$"),
    "trailing_stop":       re.compile(r"^trailing_stop_loss$"),
    "roi":                 re.compile(r"^roi$"),
    "force_exit":          re.compile(r"^force_exit$"),
    "emergency_exit":      re.compile(r"^emergency_exit$"),
}


def classify(tag: Optional[str], families: dict) -> str:
    if not tag:
        return "unknown"
    for name, rx in families.items():
        if rx.match(tag):
            return name
    return "other"


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_closed_trades(db_path: Path, days: int) -> list[dict]:
    if not db_path.exists():
        return []
    sql = """
        SELECT id, pair, enter_tag, exit_reason, is_short, leverage,
               open_rate, close_rate, close_profit, close_profit_abs,
               stake_amount, open_date, close_date, max_rate, min_rate
        FROM trades
        WHERE is_open = 0
    """
    params: list = []
    if days > 0:
        sql += " AND close_date >= datetime('now', ?)"
        params.append(f"-{days} days")
    sql += " ORDER BY close_date ASC"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def fetch_open_trades(db_path: Path) -> list[dict]:
    if not db_path.exists():
        return []
    sql = """
        SELECT id, pair, enter_tag, is_short, leverage,
               open_rate, stake_amount, open_date
        FROM trades
        WHERE is_open = 1
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(sql).fetchall()]
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze(trades: list[dict]) -> dict:
    if not trades:
        return _empty_analysis()

    wins = [t for t in trades if (t["close_profit"] or 0) > 0]
    losses = [t for t in trades if (t["close_profit"] or 0) < 0]
    draws = [t for t in trades if (t["close_profit"] or 0) == 0]

    total_pnl_abs = sum(t.get("close_profit_abs") or 0 for t in trades)
    total_pnl_pct = sum((t.get("close_profit") or 0) * 100 for t in trades)
    avg_pnl_pct = total_pnl_pct / len(trades) if trades else 0

    win_pnl_pct = sum((t.get("close_profit") or 0) * 100 for t in wins)
    loss_pnl_pct = sum((t.get("close_profit") or 0) * 100 for t in losses)

    avg_win = win_pnl_pct / len(wins) if wins else 0
    avg_loss = loss_pnl_pct / len(losses) if losses else 0
    profit_factor = abs(win_pnl_pct / loss_pnl_pct) if loss_pnl_pct != 0 else float("inf")

    sorted_by_profit = sorted(trades, key=lambda t: t.get("close_profit") or 0)
    best = sorted_by_profit[-1] if trades else None
    worst = sorted_by_profit[0] if trades else None

    # Streak analysis
    max_win_streak = max_loss_streak = cur_win = cur_loss = 0
    for t in trades:
        if (t["close_profit"] or 0) > 0:
            cur_win += 1
            cur_loss = 0
        elif (t["close_profit"] or 0) < 0:
            cur_loss += 1
            cur_win = 0
        else:
            cur_win = 0
            cur_loss = 0
        max_win_streak = max(max_win_streak, cur_win)
        max_loss_streak = max(max_loss_streak, cur_loss)

    # By entry family
    by_entry: dict[str, dict] = defaultdict(lambda: {"n": 0, "wins": 0, "pnl_pct": 0.0, "pnl_abs": 0.0})
    for t in trades:
        fam = classify(t.get("enter_tag"), ENTRY_FAMILIES)
        b = by_entry[fam]
        b["n"] += 1
        if (t["close_profit"] or 0) > 0:
            b["wins"] += 1
        b["pnl_pct"] += (t.get("close_profit") or 0) * 100
        b["pnl_abs"] += t.get("close_profit_abs") or 0

    # By exit family
    by_exit: dict[str, dict] = defaultdict(lambda: {"n": 0, "pnl_pct": 0.0, "pnl_abs": 0.0})
    for t in trades:
        fam = classify(t.get("exit_reason"), EXIT_FAMILIES)
        b = by_exit[fam]
        b["n"] += 1
        b["pnl_pct"] += (t.get("close_profit") or 0) * 100
        b["pnl_abs"] += t.get("close_profit_abs") or 0

    # Long vs short
    longs = [t for t in trades if not t.get("is_short")]
    shorts = [t for t in trades if t.get("is_short")]

    def side_summary(ts):
        if not ts:
            return {"n": 0, "wins": 0, "win_rate": 0, "pnl_pct": 0, "avg_pnl_pct": 0}
        w = sum(1 for t in ts if (t["close_profit"] or 0) > 0)
        pnl = sum((t.get("close_profit") or 0) * 100 for t in ts)
        return {
            "n": len(ts),
            "wins": w,
            "win_rate": round(100 * w / len(ts), 1),
            "pnl_pct": round(pnl, 3),
            "avg_pnl_pct": round(pnl / len(ts), 3),
        }

    # Daily P&L breakdown
    daily: dict[str, float] = defaultdict(float)
    for t in trades:
        cd = t.get("close_date", "")
        if cd:
            day = cd[:10]
            daily[day] += (t.get("close_profit") or 0) * 100

    def _trade_summary(t):
        if not t:
            return None
        return {
            "pair": t["pair"],
            "enter_tag": t.get("enter_tag"),
            "exit_reason": t.get("exit_reason"),
            "pnl_pct": round((t.get("close_profit") or 0) * 100, 3),
            "pnl_abs": round(t.get("close_profit_abs") or 0, 4),
            "leverage": t.get("leverage", 1),
            "close_date": t.get("close_date"),
        }

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "draws": len(draws),
        "win_rate": round(100 * len(wins) / len(trades), 1),
        "total_pnl_pct": round(total_pnl_pct, 3),
        "total_pnl_abs": round(total_pnl_abs, 4),
        "avg_pnl_pct": round(avg_pnl_pct, 3),
        "avg_win_pct": round(avg_win, 3),
        "avg_loss_pct": round(avg_loss, 3),
        "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else None,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "best_trade": _trade_summary(best),
        "worst_trade": _trade_summary(worst),
        "long": side_summary(longs),
        "short": side_summary(shorts),
        "by_entry_family": {k: _round_family(v) for k, v in sorted(by_entry.items(), key=lambda x: -x[1]["pnl_pct"])},
        "by_exit_family": {k: _round_family(v) for k, v in sorted(by_exit.items(), key=lambda x: -x[1]["pnl_pct"])},
        "daily_pnl": {d: round(v, 3) for d, v in sorted(daily.items())},
    }


def _round_family(d: dict) -> dict:
    out = dict(d)
    for k in ("pnl_pct", "pnl_abs"):
        if k in out:
            out[k] = round(out[k], 3)
    if "wins" in out and "n" in out and out["n"] > 0:
        out["win_rate"] = round(100 * out["wins"] / out["n"], 1)
    return out


def _empty_analysis() -> dict:
    return {
        "total_trades": 0, "wins": 0, "losses": 0, "draws": 0,
        "win_rate": 0, "total_pnl_pct": 0, "total_pnl_abs": 0,
        "avg_pnl_pct": 0, "avg_win_pct": 0, "avg_loss_pct": 0,
        "profit_factor": None, "max_win_streak": 0, "max_loss_streak": 0,
        "best_trade": None, "worst_trade": None,
        "long": {"n": 0, "wins": 0, "win_rate": 0, "pnl_pct": 0, "avg_pnl_pct": 0},
        "short": {"n": 0, "wins": 0, "win_rate": 0, "pnl_pct": 0, "avg_pnl_pct": 0},
        "by_entry_family": {}, "by_exit_family": {}, "daily_pnl": {},
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def build_record(args, results: dict) -> dict:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "scope_days": args.days,
        **results,
    }


def format_telegram(results: dict, days: int) -> str:
    scope = f"last {days}d" if days > 0 else "all-time"
    lines = [f"*SYGNIF SUCCESS REPORT* — {scope}", ""]

    for instance in ("spot", "futures"):
        data = results.get(instance, {})
        n = data.get("total_trades", 0)
        if n == 0:
            lines.append(f"*{instance.upper()}:* _no closed trades_")
            continue

        wr = data.get("win_rate", 0)
        pnl = data.get("total_pnl_pct", 0)
        pnl_abs = data.get("total_pnl_abs", 0)
        w, l = data.get("wins", 0), data.get("losses", 0)
        avg_w = data.get("avg_win_pct", 0)
        avg_l = data.get("avg_loss_pct", 0)
        pf = data.get("profit_factor")
        pf_str = f"{pf:.2f}" if pf is not None else "inf"

        lines.append(f"*{instance.upper()}* ({n} trades)")
        lines.append(f"  W/L: {w}/{l} (`{wr:.1f}%` win rate)")
        lines.append(f"  P&L: `{pnl:+.2f}%` (`{pnl_abs:+.4f}` USDT)")
        lines.append(f"  Avg win: `{avg_w:+.2f}%` | Avg loss: `{avg_l:+.2f}%`")
        lines.append(f"  PF: `{pf_str}` | Streaks: W{data.get('max_win_streak', 0)}/L{data.get('max_loss_streak', 0)}")

        # Side breakdown
        for side_key, side_label in [("long", "Long"), ("short", "Short")]:
            s = data.get(side_key, {})
            if s.get("n", 0) > 0:
                lines.append(f"  {side_label}: {s['n']} trades, `{s.get('win_rate', 0):.0f}%` WR, `{s.get('pnl_pct', 0):+.2f}%`")

        # Best / worst
        best = data.get("best_trade")
        worst = data.get("worst_trade")
        if best:
            lines.append(f"  Best: {best['pair']} `{best['pnl_pct']:+.2f}%` ({best.get('enter_tag', '?')})")
        if worst:
            lines.append(f"  Worst: {worst['pair']} `{worst['pnl_pct']:+.2f}%` ({worst.get('enter_tag', '?')})")

        # Top entry families
        by_entry = data.get("by_entry_family", {})
        if by_entry:
            top_entries = list(by_entry.items())[:3]
            entry_strs = [f"{k}(`{v.get('win_rate', 0):.0f}%`, `{v['pnl_pct']:+.1f}%`)" for k, v in top_entries]
            lines.append(f"  Top entries: {', '.join(entry_strs)}")

        lines.append("")

    # Open trades summary
    for instance in ("spot", "futures"):
        open_n = results.get(f"{instance}_open", 0)
        if open_n > 0:
            lines.append(f"_{instance.upper()}: {open_n} open trades_")

    return "\n".join(lines)


def format_text(results: dict, days: int) -> str:
    scope = f"last {days}d" if days > 0 else "all-time"
    lines = [f"\n=== SYGNIF TRADING SUCCESS — {scope} ===\n"]

    for instance in ("spot", "futures"):
        data = results.get(instance, {})
        n = data.get("total_trades", 0)
        if n == 0:
            lines.append(f"  {instance.upper()}: no closed trades\n")
            continue

        wr = data.get("win_rate", 0)
        pnl = data.get("total_pnl_pct", 0)
        pnl_abs = data.get("total_pnl_abs", 0)
        w, l = data.get("wins", 0), data.get("losses", 0)
        pf = data.get("profit_factor")
        pf_str = f"{pf:.2f}" if pf is not None else "inf"

        lines.append(f"  {instance.upper()} — {n} trades")
        lines.append(f"  {'─' * 50}")
        lines.append(f"  Win/Loss:       {w}/{l} ({wr:.1f}% win rate)")
        lines.append(f"  Total P&L:      {pnl:+.2f}% ({pnl_abs:+.4f} USDT)")
        lines.append(f"  Avg win:        {data.get('avg_win_pct', 0):+.2f}%")
        lines.append(f"  Avg loss:       {data.get('avg_loss_pct', 0):+.2f}%")
        lines.append(f"  Profit factor:  {pf_str}")
        lines.append(f"  Win streaks:    W{data.get('max_win_streak', 0)} / L{data.get('max_loss_streak', 0)}")

        for side_key, side_label in [("long", "Long"), ("short", "Short")]:
            s = data.get(side_key, {})
            if s.get("n", 0) > 0:
                lines.append(f"  {side_label}:          {s['n']} trades, {s.get('win_rate', 0):.0f}% WR, {s.get('pnl_pct', 0):+.2f}%")

        best = data.get("best_trade")
        worst = data.get("worst_trade")
        if best:
            lines.append(f"  Best trade:     {best['pair']} {best['pnl_pct']:+.2f}% [{best.get('enter_tag', '?')}]")
        if worst:
            lines.append(f"  Worst trade:    {worst['pair']} {worst['pnl_pct']:+.2f}% [{worst.get('enter_tag', '?')}]")

        by_entry = data.get("by_entry_family", {})
        if by_entry:
            lines.append(f"\n  Entry families:")
            lines.append(f"  {'family':<22} {'n':>4} {'WR%':>6} {'P&L%':>9} {'USDT':>10}")
            lines.append(f"  {'─' * 55}")
            for fam, v in by_entry.items():
                lines.append(
                    f"  {fam:<22} {v['n']:>4} {v.get('win_rate', 0):>5.1f}% "
                    f"{v['pnl_pct']:>+8.2f}% {v.get('pnl_abs', 0):>+9.4f}"
                )

        by_exit = data.get("by_exit_family", {})
        if by_exit:
            lines.append(f"\n  Exit families:")
            lines.append(f"  {'family':<24} {'n':>4} {'P&L%':>9} {'USDT':>10}")
            lines.append(f"  {'─' * 50}")
            for fam, v in by_exit.items():
                lines.append(
                    f"  {fam:<24} {v['n']:>4} {v['pnl_pct']:>+8.2f}% {v.get('pnl_abs', 0):>+9.4f}"
                )

        daily = data.get("daily_pnl", {})
        if daily:
            lines.append(f"\n  Daily P&L:")
            for day, pnl_d in daily.items():
                bar = "█" * max(1, int(abs(pnl_d) / 0.5)) if pnl_d != 0 else ""
                sign = "+" if pnl_d >= 0 else ""
                lines.append(f"    {day}  {sign}{pnl_d:.2f}%  {'🟢' if pnl_d >= 0 else '🔴'} {bar}")

        lines.append("")

    return "\n".join(lines)


def tg_send(text: str) -> bool:
    if not TG_TOKEN or not TG_CHAT:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHAT,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        return resp.ok
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Sygnif trading success extractor")
    p.add_argument("--days", type=int, default=1, help="Lookback window in days (0 = all-time, default 1)")
    p.add_argument("--db-dir", default="user_data", help="Directory containing SQLite databases")
    p.add_argument("--telegram", action="store_true", help="Send summary to Telegram")
    p.add_argument("--json", action="store_true", help="Output JSON to stdout")
    p.add_argument("--no-print", action="store_true", help="Suppress stdout (log-only mode)")
    p.add_argument("--no-log", action="store_true", help="Skip JSONL logging")
    args = p.parse_args()

    db_dir = Path(args.db_dir)
    dbs = {
        "spot": db_dir / "tradesv3.sqlite",
        "futures": db_dir / "tradesv3-futures.sqlite",
    }

    results: dict = {}
    for instance, db_path in dbs.items():
        closed = fetch_closed_trades(db_path, args.days)
        open_trades = fetch_open_trades(db_path)
        results[instance] = analyze(closed)
        results[f"{instance}_open"] = len(open_trades)

    if args.json:
        record = build_record(args, results)
        print(json.dumps(record, indent=2, default=str))
    elif not args.no_print:
        print(format_text(results, args.days))

    if not args.no_log:
        record = build_record(args, results)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")
        if not args.no_print and not args.json:
            print(f"Logged to {LOG_FILE}")

    if args.telegram:
        msg = format_telegram(results, args.days)
        sent = tg_send(msg)
        if not args.no_print:
            print("Telegram: sent" if sent else "Telegram: FAILED (check tokens)")


if __name__ == "__main__":
    main()
