#!/usr/bin/env python3
"""
Strategy path tracker — entry→exit combination analysis.

Maps every observed entry_tag → exit_reason path and scores each path on
whether the strategy is proving worthy (profitable, consistent) or should
be retired.

Worthiness scoring model
------------------------
Each path gets a composite score (0–100) from four factors:

  1. Win rate           (0–30 pts)  — % of trades that closed positive
  2. Avg P&L            (0–25 pts)  — average return per trade
  3. Profit factor      (0–25 pts)  — gross wins / gross losses
  4. Sample size        (0–20 pts)  — more trades = more confidence

Paths are graded:
  - PROVEN   (>= 70)  — profitable and consistent, keep running
  - MARGINAL (40–69)  — mixed results, needs monitoring
  - FAILING  (< 40)   — net-negative, consider disabling
  - UNPROVEN (< 3 trades) — too few samples to judge

Usage:
  python3 trade_overseer/strategy_paths.py                      # all-time, futures
  python3 trade_overseer/strategy_paths.py --days 7             # last 7 days
  python3 trade_overseer/strategy_paths.py --instance spot      # spot only
  python3 trade_overseer/strategy_paths.py --telegram           # send to Telegram
  python3 trade_overseer/strategy_paths.py --json               # JSON output
  python3 trade_overseer/strategy_paths.py --min-trades 5       # hide paths < 5 trades
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATHS = {
    "spot": Path("user_data/tradesv3.sqlite"),
    "futures": Path("user_data/tradesv3-futures.sqlite"),
}
LOG_DIR = Path("user_data/logs")
LOG_FILE = LOG_DIR / "strategy_paths.jsonl"

TG_TOKEN = os.environ.get("FINANCE_BOT_TOKEN", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

MIN_TRADES_FOR_SCORING = 3

# ---------------------------------------------------------------------------
# Entry/exit family classification (shared taxonomy)
# ---------------------------------------------------------------------------

ENTRY_FAMILIES = {
    "strong_ta":           re.compile(r"^strong_ta$"),
    "claude_s":            re.compile(r"^claude_s-?\d+$"),
    "claude_swing":        re.compile(r"^claude_swing$"),
    "swing_failure":       re.compile(r"^swing_failure$"),
    "strong_ta_short":     re.compile(r"^strong_ta_short$"),
    "claude_short_s":      re.compile(r"^claude_short_s-?\d+$"),
    "claude_swing_short":  re.compile(r"^claude_swing_short$"),
    "swing_failure_short": re.compile(r"^swing_failure_short$"),
}

EXIT_FAMILIES = {
    "rsi_exit":             re.compile(r"^exit_(short_)?profit_rsi_"),
    "willr_reversal":       re.compile(r"^exit_(short_)?willr_reversal$"),
    "soft_stoploss":        re.compile(r"^exit_(short_)?stoploss_conditional$"),
    "sf_ema_tp":            re.compile(r"^exit_sf_(short_)?ema_tp$"),
    "sf_vol_sl":            re.compile(r"^exit_sf_(short_)?vol_sl$"),
    "stoploss_on_exchange": re.compile(r"^stoploss_on_exchange$"),
    "trailing_stop":        re.compile(r"^trailing_stop_loss$"),
    "roi":                  re.compile(r"^roi$"),
    "force_exit":           re.compile(r"^force_exit$"),
    "emergency_exit":       re.compile(r"^emergency_exit$"),
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


# ---------------------------------------------------------------------------
# Path statistics
# ---------------------------------------------------------------------------

@dataclass
class PathStats:
    entry_family: str
    exit_family: str
    side: str
    n: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl_pct: float = 0.0
    total_pnl_abs: float = 0.0
    best_pnl_pct: float = float("-inf")
    worst_pnl_pct: float = float("inf")
    durations_sec: list = field(default_factory=list)
    raw_entry_tags: set = field(default_factory=set)
    raw_exit_reasons: set = field(default_factory=set)
    pairs: set = field(default_factory=set)

    @property
    def win_rate(self) -> float:
        return 100.0 * self.wins / self.n if self.n else 0.0

    @property
    def avg_pnl_pct(self) -> float:
        return self.total_pnl_pct / self.n if self.n else 0.0

    @property
    def profit_factor(self) -> float:
        gross_win = sum(1 for _ in range(self.wins))  # placeholder
        win_pnl = self.total_pnl_pct - self._loss_pnl
        loss_pnl = self._loss_pnl
        if loss_pnl == 0:
            return float("inf") if win_pnl > 0 else 0.0
        return abs(win_pnl / loss_pnl)

    @property
    def avg_duration_str(self) -> str:
        if not self.durations_sec:
            return "--"
        avg = sum(self.durations_sec) / len(self.durations_sec)
        h = int(avg) // 3600
        m = (int(avg) % 3600) // 60
        return f"{h}h{m:02d}m" if h else f"{m}m"

    _loss_pnl: float = 0.0

    def add_trade(self, t: dict):
        pnl_pct = (t.get("close_profit") or 0) * 100
        pnl_abs = t.get("close_profit_abs") or 0
        self.n += 1
        if pnl_pct > 0:
            self.wins += 1
        elif pnl_pct < 0:
            self.losses += 1
            self._loss_pnl += pnl_pct
        self.total_pnl_pct += pnl_pct
        self.total_pnl_abs += pnl_abs
        self.best_pnl_pct = max(self.best_pnl_pct, pnl_pct)
        self.worst_pnl_pct = min(self.worst_pnl_pct, pnl_pct)
        self.raw_entry_tags.add(t.get("enter_tag") or "?")
        self.raw_exit_reasons.add(t.get("exit_reason") or "?")
        self.pairs.add(t.get("pair") or "?")

        od = t.get("open_date", "")
        cd = t.get("close_date", "")
        if od and cd:
            try:
                dur = (datetime.fromisoformat(cd) - datetime.fromisoformat(od)).total_seconds()
                if dur > 0:
                    self.durations_sec.append(dur)
            except (ValueError, TypeError):
                pass


# ---------------------------------------------------------------------------
# Worthiness scoring
# ---------------------------------------------------------------------------

def score_worthiness(ps: PathStats) -> dict:
    """Score a path 0–100 across four dimensions."""
    if ps.n < MIN_TRADES_FOR_SCORING:
        return {
            "total": None,
            "grade": "UNPROVEN",
            "win_rate_pts": 0,
            "avg_pnl_pts": 0,
            "pf_pts": 0,
            "sample_pts": 0,
            "note": f"Only {ps.n} trade(s) — need >= {MIN_TRADES_FOR_SCORING}",
        }

    # 1. Win rate → 0–30 pts
    wr = ps.win_rate
    if wr >= 70:
        wr_pts = 30
    elif wr >= 55:
        wr_pts = 20 + (wr - 55) / 15 * 10
    elif wr >= 40:
        wr_pts = 10 + (wr - 40) / 15 * 10
    else:
        wr_pts = max(0, wr / 40 * 10)

    # 2. Avg P&L → 0–25 pts (scaled, with negative penalty)
    avg = ps.avg_pnl_pct
    if avg >= 5.0:
        pnl_pts = 25
    elif avg >= 2.0:
        pnl_pts = 15 + (avg - 2) / 3 * 10
    elif avg >= 0:
        pnl_pts = avg / 2 * 15
    else:
        pnl_pts = max(0, 10 + avg * 2)  # -5% → 0 pts

    # 3. Profit factor → 0–25 pts
    pf = ps.profit_factor
    if pf == float("inf"):
        pf_pts = 25
    elif pf >= 2.0:
        pf_pts = 25
    elif pf >= 1.5:
        pf_pts = 18 + (pf - 1.5) / 0.5 * 7
    elif pf >= 1.0:
        pf_pts = 10 + (pf - 1.0) / 0.5 * 8
    elif pf >= 0.5:
        pf_pts = (pf - 0.5) / 0.5 * 10
    else:
        pf_pts = 0

    # 4. Sample size → 0–20 pts (logarithmic)
    if ps.n >= 30:
        sample_pts = 20
    elif ps.n >= 10:
        sample_pts = 12 + (ps.n - 10) / 20 * 8
    elif ps.n >= 5:
        sample_pts = 5 + (ps.n - 5) / 5 * 7
    else:
        sample_pts = ps.n / 5 * 5

    total = wr_pts + pnl_pts + pf_pts + sample_pts
    total = min(100, max(0, total))

    if total >= 70:
        grade = "PROVEN"
    elif total >= 40:
        grade = "MARGINAL"
    else:
        grade = "FAILING"

    return {
        "total": round(total, 1),
        "grade": grade,
        "win_rate_pts": round(wr_pts, 1),
        "avg_pnl_pts": round(pnl_pts, 1),
        "pf_pts": round(pf_pts, 1),
        "sample_pts": round(sample_pts, 1),
        "note": "",
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate_paths(trades: list[dict]) -> dict[tuple[str, str, str], PathStats]:
    paths: dict[tuple[str, str, str], PathStats] = {}

    for t in trades:
        ef = classify(t.get("enter_tag"), ENTRY_FAMILIES)
        xf = classify(t.get("exit_reason"), EXIT_FAMILIES)
        side = "short" if t.get("is_short") else "long"
        key = (ef, xf, side)

        if key not in paths:
            paths[key] = PathStats(entry_family=ef, exit_family=xf, side=side)
        paths[key].add_trade(t)

    return paths


def aggregate_entries(paths: dict) -> dict[str, dict]:
    """Roll up path stats into per-entry-family summaries."""
    entries: dict[str, dict] = defaultdict(lambda: {
        "n": 0, "wins": 0, "pnl_pct": 0.0, "pnl_abs": 0.0,
        "paths": 0, "proven": 0, "marginal": 0, "failing": 0, "unproven": 0,
    })
    for (ef, xf, side), ps in paths.items():
        e = entries[ef]
        e["n"] += ps.n
        e["wins"] += ps.wins
        e["pnl_pct"] += ps.total_pnl_pct
        e["pnl_abs"] += ps.total_pnl_abs
        e["paths"] += 1
        ws = score_worthiness(ps)
        grade = ws["grade"].lower()
        e[grade] = e.get(grade, 0) + 1
    return dict(entries)


def aggregate_exits(paths: dict) -> dict[str, dict]:
    """Roll up path stats into per-exit-family summaries."""
    exits: dict[str, dict] = defaultdict(lambda: {
        "n": 0, "pnl_pct": 0.0, "pnl_abs": 0.0,
    })
    for (ef, xf, side), ps in paths.items():
        x = exits[xf]
        x["n"] += ps.n
        x["pnl_pct"] += ps.total_pnl_pct
        x["pnl_abs"] += ps.total_pnl_abs
    return dict(exits)


# ---------------------------------------------------------------------------
# Text report
# ---------------------------------------------------------------------------

GRADE_ICON = {"PROVEN": "+", "MARGINAL": "~", "FAILING": "!", "UNPROVEN": "?"}


def format_text(all_results: dict, days: int) -> str:
    scope = f"last {days}d" if days > 0 else "all-time"
    lines = [f"\n=== STRATEGY PATH TRACKER — {scope} ===\n"]

    for instance, data in all_results.items():
        if instance.endswith("_summary"):
            continue
        paths = data.get("paths", {})
        if not paths:
            lines.append(f"  {instance.upper()}: no closed trades\n")
            continue

        lines.append(f"  {instance.upper()} — {sum(p['stats']['n'] for p in paths.values())} trades across {len(paths)} paths")
        lines.append(f"  {'─' * 85}")
        lines.append(
            f"  {'entry→exit':<40} {'side':<6} {'n':>3} {'WR%':>6} "
            f"{'avg%':>7} {'PF':>6} {'best':>7} {'worst':>7} {'dur':>7} {'score':>5} {'grade':<8}"
        )
        lines.append(f"  {'─' * 85}")

        sorted_paths = sorted(paths.items(), key=lambda x: -(x[1].get("score", {}).get("total") or -1))

        for path_key, pd_ in sorted_paths:
            ps = pd_["stats"]
            ws = pd_["score"]
            icon = GRADE_ICON.get(ws["grade"], " ")
            score_str = f"{ws['total']:.0f}" if ws["total"] is not None else "  ?"
            pf_str = f"{ps['profit_factor']:.2f}" if ps["profit_factor"] != float("inf") else " inf"
            lines.append(
                f" {icon}{ps['entry_family']}→{ps['exit_family']:<20s} "
                f"{ps['side']:<6} {ps['n']:>3} {ps['win_rate']:>5.1f}% "
                f"{ps['avg_pnl_pct']:>+6.2f}% {pf_str:>6} "
                f"{ps['best_pnl_pct']:>+6.2f}% {ps['worst_pnl_pct']:>+6.2f}% "
                f"{ps['avg_duration']:>7} {score_str:>5} {ws['grade']:<8}"
            )

        # Entry family rollup
        entry_summary = data.get("entry_summary", {})
        if entry_summary:
            lines.append(f"\n  Entry family rollup:")
            lines.append(f"  {'family':<22} {'n':>4} {'WR%':>6} {'P&L%':>9} {'paths':>5} {'proven':>6} {'marginal':>8} {'failing':>7}")
            lines.append(f"  {'─' * 70}")
            for fam, s in sorted(entry_summary.items(), key=lambda x: -x[1]["pnl_pct"]):
                wr = 100 * s["wins"] / s["n"] if s["n"] else 0
                lines.append(
                    f"  {fam:<22} {s['n']:>4} {wr:>5.1f}% {s['pnl_pct']:>+8.2f}% "
                    f"{s['paths']:>5} {s['proven']:>6} {s['marginal']:>8} {s['failing']:>7}"
                )

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telegram report
# ---------------------------------------------------------------------------

def format_telegram(all_results: dict, days: int) -> str:
    scope = f"last {days}d" if days > 0 else "all-time"
    lines = [f"*STRATEGY PATHS* — {scope}", ""]

    for instance, data in all_results.items():
        if instance.endswith("_summary"):
            continue
        paths = data.get("paths", {})
        if not paths:
            lines.append(f"*{instance.upper()}:* _no trades_")
            continue

        n_proven = sum(1 for p in paths.values() if p["score"]["grade"] == "PROVEN")
        n_marginal = sum(1 for p in paths.values() if p["score"]["grade"] == "MARGINAL")
        n_failing = sum(1 for p in paths.values() if p["score"]["grade"] == "FAILING")
        n_unproven = sum(1 for p in paths.values() if p["score"]["grade"] == "UNPROVEN")

        lines.append(f"*{instance.upper()}* ({len(paths)} paths)")
        lines.append(f"  Proven: {n_proven} | Marginal: {n_marginal} | Failing: {n_failing} | Unproven: {n_unproven}")

        # Top proven paths
        proven_paths = [
            (k, v) for k, v in sorted(paths.items(), key=lambda x: -(x[1]["score"].get("total") or -1))
            if v["score"]["grade"] == "PROVEN"
        ][:5]
        if proven_paths:
            lines.append("  *Top proven:*")
            for pk, pv in proven_paths:
                ps = pv["stats"]
                lines.append(
                    f"    `{ps['entry_family']}→{ps['exit_family']}` "
                    f"({ps['side']}) n={ps['n']} WR=`{ps['win_rate']:.0f}%` "
                    f"avg=`{ps['avg_pnl_pct']:+.1f}%` score=`{pv['score']['total']:.0f}`"
                )

        # Failing paths
        failing_paths = [
            (k, v) for k, v in sorted(paths.items(), key=lambda x: x[1]["score"].get("total") or 999)
            if v["score"]["grade"] == "FAILING"
        ][:5]
        if failing_paths:
            lines.append("  *Failing (consider disabling):*")
            for pk, pv in failing_paths:
                ps = pv["stats"]
                lines.append(
                    f"    `{ps['entry_family']}→{ps['exit_family']}` "
                    f"({ps['side']}) n={ps['n']} WR=`{ps['win_rate']:.0f}%` "
                    f"avg=`{ps['avg_pnl_pct']:+.1f}%` score=`{pv['score']['total']:.0f}`"
                )

        # Entry family rollup — highlight best and worst
        entry_summary = data.get("entry_summary", {})
        if entry_summary:
            best_entry = max(entry_summary.items(), key=lambda x: x[1]["pnl_pct"])
            worst_entry = min(entry_summary.items(), key=lambda x: x[1]["pnl_pct"])
            lines.append(
                f"  Best entry: `{best_entry[0]}` ({best_entry[1]['n']} trades, "
                f"`{best_entry[1]['pnl_pct']:+.1f}%`)"
            )
            if worst_entry[1]["pnl_pct"] < 0:
                lines.append(
                    f"  Worst entry: `{worst_entry[0]}` ({worst_entry[1]['n']} trades, "
                    f"`{worst_entry[1]['pnl_pct']:+.1f}%`)"
                )

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON serialization helpers
# ---------------------------------------------------------------------------

def _serialize_paths(paths: dict[tuple, PathStats]) -> dict:
    out = {}
    for (ef, xf, side), ps in paths.items():
        key = f"{ef}→{xf}|{side}"
        ws = score_worthiness(ps)
        out[key] = {
            "stats": {
                "entry_family": ef,
                "exit_family": xf,
                "side": side,
                "n": ps.n,
                "wins": ps.wins,
                "losses": ps.losses,
                "win_rate": round(ps.win_rate, 1),
                "avg_pnl_pct": round(ps.avg_pnl_pct, 3),
                "total_pnl_pct": round(ps.total_pnl_pct, 3),
                "total_pnl_abs": round(ps.total_pnl_abs, 4),
                "profit_factor": round(ps.profit_factor, 3) if ps.profit_factor != float("inf") else float("inf"),
                "best_pnl_pct": round(ps.best_pnl_pct, 3) if ps.best_pnl_pct != float("-inf") else None,
                "worst_pnl_pct": round(ps.worst_pnl_pct, 3) if ps.worst_pnl_pct != float("inf") else None,
                "avg_duration": ps.avg_duration_str,
                "unique_pairs": len(ps.pairs),
                "raw_entry_tags": sorted(ps.raw_entry_tags - {"?"}),
                "raw_exit_reasons": sorted(ps.raw_exit_reasons - {"?"}),
            },
            "score": ws,
        }
    return out


# ---------------------------------------------------------------------------
# Telegram send
# ---------------------------------------------------------------------------

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
    p = argparse.ArgumentParser(description="Sygnif strategy path tracker")
    p.add_argument("--days", type=int, default=0, help="Lookback window in days (0 = all-time)")
    p.add_argument("--db-dir", default="user_data", help="Directory containing SQLite databases")
    p.add_argument("--instance", choices=["both", "spot", "futures"], default="both",
                   help="Which instance to analyze (default: both)")
    p.add_argument("--min-trades", type=int, default=0, help="Hide paths with fewer trades")
    p.add_argument("--telegram", action="store_true", help="Send summary to Telegram")
    p.add_argument("--json", action="store_true", help="Output JSON to stdout")
    p.add_argument("--no-print", action="store_true", help="Suppress stdout (log-only mode)")
    p.add_argument("--no-log", action="store_true", help="Skip JSONL logging")
    args = p.parse_args()

    db_dir = Path(args.db_dir)
    instances = ["spot", "futures"] if args.instance == "both" else [args.instance]

    all_results: dict = {}

    for instance in instances:
        db_path = db_dir / ("tradesv3.sqlite" if instance == "spot" else "tradesv3-futures.sqlite")
        trades = fetch_closed_trades(db_path, args.days)

        if not trades:
            all_results[instance] = {"paths": {}, "entry_summary": {}, "exit_summary": {}}
            continue

        raw_paths = aggregate_paths(trades)

        serialized = _serialize_paths(raw_paths)
        if args.min_trades > 0:
            serialized = {k: v for k, v in serialized.items() if v["stats"]["n"] >= args.min_trades}

        entry_summary = aggregate_entries(raw_paths)
        exit_summary = aggregate_exits(raw_paths)

        all_results[instance] = {
            "paths": serialized,
            "entry_summary": {k: _round_summary(v) for k, v in entry_summary.items()},
            "exit_summary": {k: _round_summary(v) for k, v in exit_summary.items()},
        }

    # Output
    if args.json:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "scope_days": args.days,
            **all_results,
        }
        print(json.dumps(record, indent=2, default=_json_default))
    elif not args.no_print:
        print(format_text(all_results, args.days))

    # JSONL log
    if not args.no_log:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "scope_days": args.days,
            **all_results,
        }
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":"), default=_json_default) + "\n")
        if not args.no_print and not args.json:
            print(f"Logged to {LOG_FILE}")

    # Telegram
    if args.telegram:
        msg = format_telegram(all_results, args.days)
        sent = tg_send(msg)
        if not args.no_print:
            print("Telegram: sent" if sent else "Telegram: FAILED (check tokens)")


def _round_summary(d: dict) -> dict:
    out = dict(d)
    for k in ("pnl_pct", "pnl_abs"):
        if k in out:
            out[k] = round(out[k], 3)
    return out


def _json_default(obj):
    if isinstance(obj, float):
        if math.isinf(obj):
            return None
        if math.isnan(obj):
            return None
    if isinstance(obj, set):
        return sorted(obj)
    return str(obj)


if __name__ == "__main__":
    main()
