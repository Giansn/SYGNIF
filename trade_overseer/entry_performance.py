"""
Entry-tag performance analysis for Sygnif.

Compares strategy entry families head-to-head: P/L, win rate, trade count,
average duration.  Uses ``fa_s0`` as the baseline reference point.

Data sources (in priority order):
  1. Freqtrade REST API  (--api, requires running instances)
  2. SQLite DB files     (default, works offline)

Families analysed:
  - swing_failure  (long + short)
  - fa_swing   (long + short; legacy claude_swing* still classified)
  - fa_s       (all fa_s* / fa_short_s* collapsed; baseline = fa_s0)

Usage:
  # From SQLite (default — both spot + futures)
  python trade_overseer/entry_performance.py

  # Futures only, last 7 days
  python trade_overseer/entry_performance.py --db user_data/tradesv3-futures.sqlite --days 7

  # Via Freqtrade REST API
  python trade_overseer/entry_performance.py --api

  # JSON output
  python trade_overseer/entry_performance.py --json

  # Filter by side
  python trade_overseer/entry_performance.py --side long
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Config (reuse trade_overseer patterns)
# ---------------------------------------------------------------------------

FT_INSTANCES = [
    {
        "name": "spot",
        "url": "http://127.0.0.1:8080/api/v1",
        "user": os.environ.get("FT_SPOT_USER", "freqtrader"),
        "pass": os.environ.get("FT_SPOT_PASS", "CHANGE_ME"),
    },
    {
        "name": "futures",
        "url": "http://127.0.0.1:8081/api/v1",
        "user": os.environ.get("FT_FUTURES_USER") or os.environ.get("FT_SPOT_USER", "freqtrader"),
        "pass": os.environ.get("FT_FUTURES_PASS") or os.environ.get("FT_SPOT_PASS", "CHANGE_ME"),
    },
]

DEFAULT_DBS = {
    "spot": Path("user_data/tradesv3.sqlite"),
    "futures": Path("user_data/tradesv3-futures.sqlite"),
}

BASELINE_TAG = "fa_s0"

# Families we care about for this report
TARGET_FAMILIES = {
    "swing_failure": re.compile(r"^swing_failure(_short)?$"),
    "fa_swing": re.compile(r"^((fa_swing)|(claude_swing))(_short)?$"),
    "fa_s": re.compile(r"^((fa_(short_)?s)|(claude_(short_)?s))-?\d+$"),
}

GHOSTED_EXIT_REASONS = {"force_exit", "emergency_exit", "liquidation"}

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class TagStats:
    family: str
    n: int = 0
    wins: int = 0
    losses: int = 0
    profit_sum: float = 0.0
    profit_abs_sum: float = 0.0
    best: float = float("-inf")
    worst: float = float("inf")
    duration_sum: float = 0.0
    duration_n: int = 0
    raw_tags: set = field(default_factory=set)
    by_tag: dict = field(default_factory=dict)

    @property
    def win_rate(self) -> float:
        closed = self.wins + self.losses
        return 100.0 * self.wins / closed if closed else 0.0

    @property
    def avg_profit(self) -> float:
        return self.profit_sum / self.n if self.n else 0.0

    @property
    def avg_duration_h(self) -> float:
        return self.duration_sum / self.duration_n if self.duration_n else 0.0

    @property
    def closed(self) -> int:
        return self.wins + self.losses


def classify(tag: Optional[str]) -> Optional[str]:
    if not tag:
        return None
    for fam, rx in TARGET_FAMILIES.items():
        if rx.match(tag):
            return fam
    return None


# ---------------------------------------------------------------------------
# Data fetching — SQLite
# ---------------------------------------------------------------------------

def fetch_trades_sqlite(db_paths: dict[str, Path], days: int, side_filter: str) -> list[dict]:
    trades = []
    for instance, path in db_paths.items():
        if not path.exists():
            continue
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        sql = """
            SELECT id, pair, enter_tag, exit_reason, is_open, is_short,
                   leverage, close_profit, open_date, close_date
            FROM trades
            WHERE is_open = 0
        """
        params: list = []
        if days > 0:
            sql += " AND close_date >= datetime('now', ?)"
            params.append(f"-{days} days")
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        for r in rows:
            d = dict(r)
            d["instance"] = instance
            is_short = bool(d.get("is_short", 0))
            side = "short" if is_short else "long"
            if side_filter != "both" and side != side_filter:
                continue
            if d.get("exit_reason") in GHOSTED_EXIT_REASONS:
                continue
            trades.append(d)
    return trades


# ---------------------------------------------------------------------------
# Data fetching — Freqtrade REST API
# ---------------------------------------------------------------------------

_tokens: dict[str, str] = {}


def _api_login(inst: dict) -> str:
    resp = requests.post(
        f"{inst['url']}/token/login",
        auth=(inst["user"], inst["pass"]),
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _api_get(inst: dict, endpoint: str, params: dict | None = None) -> dict | list:
    name = inst["name"]
    if name not in _tokens:
        _tokens[name] = _api_login(inst)

    url = f"{inst['url']}/{endpoint}"
    headers = {"Authorization": f"Bearer {_tokens[name]}"}

    resp = requests.get(url, headers=headers, params=params, timeout=10)
    if resp.status_code == 401:
        _tokens[name] = _api_login(inst)
        headers["Authorization"] = f"Bearer {_tokens[name]}"
        resp = requests.get(url, headers=headers, params=params, timeout=10)

    resp.raise_for_status()
    return resp.json()


def fetch_trades_api(side_filter: str) -> list[dict]:
    trades = []
    for inst in FT_INSTANCES:
        try:
            data = _api_get(inst, "trades", {"limit": 500})
        except Exception as e:
            print(f"warn: API fetch failed for {inst['name']}: {e}", file=sys.stderr)
            continue
        raw_trades = data.get("trades", data) if isinstance(data, dict) else data
        for t in raw_trades:
            if t.get("is_open", False):
                continue
            is_short = bool(t.get("is_short", False))
            side = "short" if is_short else "long"
            if side_filter != "both" and side != side_filter:
                continue
            exit_reason = t.get("exit_reason") or t.get("sell_reason", "")
            if exit_reason in GHOSTED_EXIT_REASONS:
                continue
            trades.append({
                "id": t.get("trade_id"),
                "pair": t.get("pair", ""),
                "enter_tag": t.get("enter_tag") or t.get("buy_tag", ""),
                "exit_reason": exit_reason,
                "is_open": False,
                "is_short": is_short,
                "leverage": t.get("leverage", 1),
                "close_profit": t.get("profit_ratio") or t.get("close_profit", 0),
                "open_date": t.get("open_date", ""),
                "close_date": t.get("close_date", ""),
                "instance": inst["name"],
            })
    return trades


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _duration_hours(open_date: str, close_date: str) -> Optional[float]:
    try:
        fmt_candidates = ["%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                          "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"]
        od = cd = None
        for fmt in fmt_candidates:
            try:
                od = datetime.strptime(str(open_date).replace("+00:00", "").replace("Z", ""), fmt)
                break
            except ValueError:
                continue
        for fmt in fmt_candidates:
            try:
                cd = datetime.strptime(str(close_date).replace("+00:00", "").replace("Z", ""), fmt)
                break
            except ValueError:
                continue
        if od and cd:
            return (cd - od).total_seconds() / 3600.0
    except Exception:
        pass
    return None


def aggregate(trades: list[dict]) -> dict[str, TagStats]:
    stats: dict[str, TagStats] = {fam: TagStats(family=fam) for fam in TARGET_FAMILIES}

    for t in trades:
        tag = t.get("enter_tag", "")
        fam = classify(tag)
        if fam is None:
            continue

        s = stats[fam]
        profit_pct = (t.get("close_profit") or 0) * 100
        s.n += 1
        s.profit_sum += profit_pct
        s.raw_tags.add(tag)

        if profit_pct > 0:
            s.wins += 1
        else:
            s.losses += 1

        s.best = max(s.best, profit_pct)
        s.worst = min(s.worst, profit_pct)

        dur = _duration_hours(t.get("open_date", ""), t.get("close_date", ""))
        if dur is not None:
            s.duration_sum += dur
            s.duration_n += 1

        if tag not in s.by_tag:
            s.by_tag[tag] = TagStats(family=tag)
        ts = s.by_tag[tag]
        ts.n += 1
        ts.profit_sum += profit_pct
        if profit_pct > 0:
            ts.wins += 1
        else:
            ts.losses += 1
        ts.best = max(ts.best, profit_pct)
        ts.worst = min(ts.worst, profit_pct)
        if dur is not None:
            ts.duration_sum += dur
            ts.duration_n += 1

    return stats


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _fp(v: float, signed: bool = True) -> str:
    if v == float("-inf") or v == float("inf"):
        return "    -"
    sign = "+" if signed and v >= 0 else ""
    return f"{sign}{v:.2f}%"


def _fh(v: float) -> str:
    if v <= 0:
        return "  -"
    if v < 1:
        return f"{v * 60:.0f}m"
    return f"{v:.1f}h"


def report_text(stats: dict[str, TagStats], scope: str, baseline_tag: str):
    print(f"\n{'=' * 80}")
    print(f"  ENTRY-TAG PERFORMANCE ANALYSIS — {scope}")
    print(f"  Baseline: {baseline_tag}")
    print(f"{'=' * 80}\n")

    # Summary table
    hdr = (f"{'family':<20} {'n':>5} {'wins':>5} {'losses':>6} {'win%':>7} "
           f"{'avg_pnl':>9} {'total_pnl':>10} {'best':>9} {'worst':>9} {'avg_dur':>8}")
    print(hdr)
    print("-" * len(hdr))

    order = ["swing_failure", "fa_swing", "fa_s"]
    for fam in order:
        s = stats.get(fam)
        if s is None or s.n == 0:
            print(f"{fam:<20} {'(no trades)':>5}")
            continue
        print(
            f"{s.family:<20} {s.n:>5} {s.wins:>5} {s.losses:>6} "
            f"{s.win_rate:>6.1f}% "
            f"{_fp(s.avg_profit):>9} "
            f"{_fp(s.profit_sum):>10} "
            f"{_fp(s.best):>9} "
            f"{_fp(s.worst):>9} "
            f"{_fh(s.avg_duration_h):>8}"
        )

    # Per-tag breakdown for fa_s family (shows baseline)
    cs = stats.get("fa_s")
    if cs and cs.by_tag:
        print(f"\n{'─' * 80}")
        print(f"  fa_s family breakdown (baseline = {baseline_tag})")
        print(f"{'─' * 80}\n")

        hdr2 = (f"  {'tag':<25} {'n':>4} {'win%':>7} {'avg_pnl':>9} "
                f"{'total_pnl':>10} {'best':>9} {'worst':>9} {'avg_dur':>8}")
        print(hdr2)
        print("  " + "-" * (len(hdr2) - 2))

        baseline_stats = cs.by_tag.get(baseline_tag)
        baseline_avg = baseline_stats.avg_profit if baseline_stats else 0.0

        for tag in sorted(cs.by_tag.keys()):
            ts = cs.by_tag[tag]
            marker = " *" if tag == baseline_tag else ""
            delta = f" ({ts.avg_profit - baseline_avg:+.2f})" if baseline_stats and tag != baseline_tag else ""
            print(
                f"  {tag:<25} {ts.n:>4} {ts.win_rate:>6.1f}% "
                f"{_fp(ts.avg_profit):>9}{delta} "
                f"{_fp(ts.profit_sum):>10} "
                f"{_fp(ts.best):>9} "
                f"{_fp(ts.worst):>9} "
                f"{_fh(ts.avg_duration_h):>8}{marker}"
            )

        if baseline_stats:
            print(f"\n  * = baseline ({baseline_tag}: avg {_fp(baseline_avg)}, "
                  f"win rate {baseline_stats.win_rate:.1f}%, n={baseline_stats.n})")

    # Swing breakdown
    for fam_key in ["swing_failure", "fa_swing"]:
        fam = stats.get(fam_key)
        if fam and len(fam.by_tag) > 1:
            print(f"\n{'─' * 80}")
            print(f"  {fam_key} breakdown")
            print(f"{'─' * 80}\n")
            hdr3 = (f"  {'tag':<30} {'n':>4} {'win%':>7} {'avg_pnl':>9} "
                    f"{'total_pnl':>10}")
            print(hdr3)
            print("  " + "-" * (len(hdr3) - 2))
            for tag in sorted(fam.by_tag.keys()):
                ts = fam.by_tag[tag]
                print(
                    f"  {tag:<30} {ts.n:>4} {ts.win_rate:>6.1f}% "
                    f"{_fp(ts.avg_profit):>9} "
                    f"{_fp(ts.profit_sum):>10}"
                )

    # Cross-family comparison vs baseline
    if cs and cs.n > 0:
        baseline_wp = cs.by_tag.get(baseline_tag)
        if baseline_wp:
            print(f"\n{'─' * 80}")
            print(f"  DELTA vs BASELINE ({baseline_tag})")
            print(f"{'─' * 80}\n")
            b_wr = baseline_wp.win_rate
            b_avg = baseline_wp.avg_profit
            hdr4 = f"  {'family':<20} {'win% Δ':>9} {'avg_pnl Δ':>11} {'verdict':<12}"
            print(hdr4)
            print("  " + "-" * (len(hdr4) - 2))
            for fam_key in order:
                s = stats.get(fam_key)
                if s is None or s.n == 0:
                    continue
                wr_d = s.win_rate - b_wr
                ap_d = s.avg_profit - b_avg
                verdict = "BETTER" if ap_d > 0 and wr_d >= 0 else (
                    "MIXED" if (ap_d > 0) != (wr_d > 0) else "WORSE"
                )
                if fam_key == "fa_s":
                    verdict = "(self)"
                print(f"  {fam_key:<20} {wr_d:>+8.1f}% {ap_d:>+10.2f}%  {verdict}")

    print()


def report_json(stats: dict[str, TagStats], scope: str, baseline_tag: str):
    out = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "scope": scope,
        "baseline": baseline_tag,
        "families": {},
    }
    for fam, s in stats.items():
        out["families"][fam] = {
            "n": s.n,
            "wins": s.wins,
            "losses": s.losses,
            "win_rate_pct": round(s.win_rate, 2),
            "avg_profit_pct": round(s.avg_profit, 3),
            "total_profit_pct": round(s.profit_sum, 3),
            "best_pct": round(s.best, 3) if s.n else None,
            "worst_pct": round(s.worst, 3) if s.n else None,
            "avg_duration_h": round(s.avg_duration_h, 2) if s.duration_n else None,
            "raw_tags": sorted(s.raw_tags),
            "by_tag": {
                tag: {
                    "n": ts.n,
                    "wins": ts.wins,
                    "losses": ts.losses,
                    "win_rate_pct": round(ts.win_rate, 2),
                    "avg_profit_pct": round(ts.avg_profit, 3),
                    "total_profit_pct": round(ts.profit_sum, 3),
                    "best_pct": round(ts.best, 3) if ts.n else None,
                    "worst_pct": round(ts.worst, 3) if ts.n else None,
                    "avg_duration_h": round(ts.avg_duration_h, 2) if ts.duration_n else None,
                }
                for tag, ts in sorted(s.by_tag.items())
            },
        }
    print(json.dumps(out, indent=2, default=str))
    return out


def append_log(stats: dict[str, TagStats], scope: str, log_path: Path):
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "scope": scope,
        "families": {
            fam: {
                "n": s.n,
                "wins": s.wins,
                "losses": s.losses,
                "win_rate_pct": round(s.win_rate, 2),
                "avg_profit_pct": round(s.avg_profit, 3),
                "total_profit_pct": round(s.profit_sum, 3),
            }
            for fam, s in stats.items()
        },
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")
    return log_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="Entry-tag performance analysis: swing_failure vs fa_swing vs fa_s baseline",
    )
    p.add_argument("--api", action="store_true",
                   help="Pull data from Freqtrade REST API instead of SQLite")
    p.add_argument("--db", nargs="*", default=None,
                   help="SQLite DB path(s). Default: both spot + futures DBs")
    p.add_argument("--days", type=int, default=0,
                   help="Restrict to last N days (0 = all time)")
    p.add_argument("--side", choices=["both", "long", "short"], default="both")
    p.add_argument("--baseline", default=BASELINE_TAG,
                   help=f"Baseline entry tag (default: {BASELINE_TAG})")
    p.add_argument("--json", action="store_true",
                   help="Output JSON instead of human-readable tables")
    p.add_argument("--log", default="user_data/logs/entry_performance.jsonl",
                   help="JSONL log path (empty to disable)")
    p.add_argument("--no-print", action="store_true",
                   help="Suppress stdout (logging only)")
    args = p.parse_args()

    scope_parts = []
    if args.days:
        scope_parts.append(f"last {args.days}d")
    else:
        scope_parts.append("all-time")
    if args.side != "both":
        scope_parts.append(f"side={args.side}")
    scope = " — ".join(scope_parts)

    if args.api:
        scope += " — API"
        trades = fetch_trades_api(args.side)
    else:
        if args.db:
            db_paths = {Path(d).stem: Path(d) for d in args.db}
        else:
            db_paths = DEFAULT_DBS
        missing = [str(p) for p in db_paths.values() if not p.exists()]
        if len(missing) == len(db_paths):
            print(f"No DB files found: {missing}", file=sys.stderr)
            sys.exit(1)
        if missing:
            print(f"warn: skipping missing DBs: {missing}", file=sys.stderr)
        scope += " — SQLite"
        trades = fetch_trades_sqlite(db_paths, args.days, args.side)

    if not trades:
        print("No closed trades found matching criteria.", file=sys.stderr)
        sys.exit(0)

    stats = aggregate(trades)
    total_matched = sum(s.n for s in stats.values())
    if total_matched == 0:
        print("No trades matched target families (swing_failure, fa_swing, fa_s).",
              file=sys.stderr)
        sys.exit(0)

    if args.json:
        report_json(stats, scope, args.baseline)
    elif not args.no_print:
        report_text(stats, scope, args.baseline)

    if args.log:
        log_path = append_log(stats, scope, Path(args.log))
        if not args.no_print and not args.json:
            print(f"Logged to {log_path}")


if __name__ == "__main__":
    main()
