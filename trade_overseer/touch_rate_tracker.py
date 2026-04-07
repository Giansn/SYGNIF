"""
Touch-rate tracker for Sygnif futures trades.

Reports per-entry-family statistics including:
  - n: total trades
  - open_n: currently open
  - touched: trades whose unrealized P&L peak ever reached the threshold
  - hit%: touch rate
  - avg_peak: average peak unrealized P&L (leverage-adjusted)
  - avg_real: average realized close P&L
  - slip: peak - realized (how much was given back)

Also reports per-exit-family realized P&L distribution.

Tags like `claude_s-5`, `claude_s3`, `claude_s5` are collapsed into the
`claude_s` family. Same for `claude_short_s*` and dynamic
`exit_profit_rsi_*` exit reasons. The full strategy taxonomy is enumerated
explicitly so families that have never fired still show up as 0-row entries
- this surfaces dead code paths.

Usage:
  python trade_overseer/touch_rate_tracker.py [--db PATH] [--days N]
                                              [--threshold 0.01] [--side both|long|short]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# --------------------------------------------------------------------------
# Strategy taxonomy — enumerated explicitly so dead paths are visible.
# --------------------------------------------------------------------------

ENTRY_FAMILIES = [
    # (family_id, side, regex, description)
    ("strong_ta",            "long",  r"^strong_ta$",                "TA score >= 65 + vol > 1.2x"),
    ("claude_s",             "long",  r"^claude_s-?\d+$",            "TA 40-70 + Claude sentiment"),
    ("claude_swing",         "long",  r"^claude_swing$",             "Failure swing + TA >= 50"),
    ("swing_failure",        "long",  r"^swing_failure$",            "Failure swing standalone"),
    ("strong_ta_short",      "short", r"^strong_ta_short$",          "TA score <= 25"),
    ("claude_short_s",       "short", r"^claude_short_s-?\d+$",      "TA 30-60 + bearish Claude sentiment"),
    ("claude_swing_short",   "short", r"^claude_swing_short$",       "Failure swing + TA <= 50"),
    ("swing_failure_short",  "short", r"^swing_failure_short$",      "Failure swing standalone short"),
]

EXIT_FAMILIES = [
    # (family_id, regex, kind, description)
    # custom_exit long
    ("exit_profit_rsi",            r"^exit_profit_rsi_",         "custom",   "Profit-tiered RSI exit (long)"),
    ("exit_willr_reversal",        r"^exit_willr_reversal$",     "custom",   "Williams %R reversal (long)"),
    ("exit_stoploss_conditional",  r"^exit_stoploss_conditional$", "custom", "Soft SL with RSI slope confirm (long)"),
    ("exit_sf_ema_tp",             r"^exit_sf_ema_tp$",          "custom",   "Swing failure EMA TP (long)"),
    ("exit_sf_vol_sl",             r"^exit_sf_vol_sl$",          "custom",   "Swing failure vol-adjusted SL (long)"),
    # custom_exit short
    ("exit_short_profit_rsi",      r"^exit_short_profit_rsi_",   "custom",   "Profit-tiered RSI exit (short)"),
    ("exit_short_willr_reversal",  r"^exit_short_willr_reversal$", "custom", "Williams %R reversal (short)"),
    ("exit_short_stoploss_conditional", r"^exit_short_stoploss_conditional$", "custom", "Soft SL (short)"),
    ("exit_sf_short_ema_tp",       r"^exit_sf_short_ema_tp$",    "custom",   "Swing failure EMA TP (short)"),
    ("exit_sf_short_vol_sl",       r"^exit_sf_short_vol_sl$",    "custom",   "Swing failure vol-adjusted SL (short)"),
    # Freqtrade infrastructure
    ("stoploss_on_exchange",       r"^stoploss_on_exchange$",    "infra",    "Doom + ratchet trail (exchange order)"),
    ("trailing_stop_loss",         r"^trailing_stop_loss$",      "infra",    "custom_stoploss tightened (in-strategy)"),
    ("emergency_exit",             r"^emergency_exit$",          "infra",    "Forced close on order failure"),
    ("force_exit",                 r"^force_exit$",              "infra",    "Manual close"),
    ("liquidation",                r"^liquidation$",             "infra",    "Margin liquidation"),
    ("roi",                        r"^roi$",                     "infra",    "minimal_roi target"),
    # Legacy (renamed)
    ("exit_willr_overbought",      r"^exit_willr_overbought$",   "legacy",   "OLD name → exit_willr_reversal"),
    ("exit_short_willr_oversold",  r"^exit_short_willr_oversold$", "legacy", "OLD name → exit_short_willr_reversal"),
]

ENTRY_REGEX = [(fid, side, re.compile(p), desc) for fid, side, p, desc in ENTRY_FAMILIES]
EXIT_REGEX  = [(fid, re.compile(p), kind, desc) for fid, p, kind, desc in EXIT_FAMILIES]


def classify_entry(tag: Optional[str]) -> Optional[str]:
    if not tag:
        return None
    for fid, _side, rx, _desc in ENTRY_REGEX:
        if rx.match(tag):
            return fid
    return None  # unknown — flagged separately


def classify_exit(reason: Optional[str]) -> Optional[str]:
    if not reason:
        return None
    for fid, rx, _kind, _desc in EXIT_REGEX:
        if rx.match(reason):
            return fid
    return None


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

@dataclass
class EntryStats:
    family: str
    side: str
    n: int = 0
    open_n: int = 0
    touched: int = 0
    peak_sum: float = 0.0
    peak_n: int = 0
    realized_sum: float = 0.0
    realized_n: int = 0
    raw_tags: set = None  # type: ignore

    def __post_init__(self):
        if self.raw_tags is None:
            self.raw_tags = set()

    @property
    def hit_pct(self) -> float:
        return 100.0 * self.touched / self.n if self.n else 0.0

    @property
    def avg_peak(self) -> float:
        return self.peak_sum / self.peak_n if self.peak_n else 0.0

    @property
    def avg_realized(self) -> float:
        return self.realized_sum / self.realized_n if self.realized_n else 0.0

    @property
    def slippage(self) -> float:
        return self.avg_peak - self.avg_realized


@dataclass
class ExitStats:
    family: str
    kind: str
    n: int = 0
    realized_sum: float = 0.0
    best: float = float("-inf")
    worst: float = float("inf")
    raw_reasons: set = None  # type: ignore

    def __post_init__(self):
        if self.raw_reasons is None:
            self.raw_reasons = set()

    @property
    def avg(self) -> float:
        return self.realized_sum / self.n if self.n else 0.0


def fetch_trades(db_path: Path, days: int):
    sql = """
        SELECT enter_tag, exit_reason, is_open, is_short, leverage,
               open_rate, max_rate, min_rate, close_profit
        FROM trades
        WHERE open_rate > 0
    """
    params: list = []
    if days > 0:
        sql += " AND COALESCE(close_date, open_date) >= datetime('now', ?)"
        params.append(f"-{days} days")
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def aggregate(rows, threshold: float, side_filter: str):
    entries = {fid: EntryStats(family=fid, side=side) for fid, side, _, _ in ENTRY_FAMILIES}
    exits = {fid: ExitStats(family=fid, kind=kind) for fid, _, kind, _ in EXIT_FAMILIES}
    unknown_entries: dict[str, int] = {}
    unknown_exits: dict[str, int] = {}

    for tag, reason, is_open, is_short, lev, open_rate, max_rate, min_rate, close_profit in rows:
        side = "short" if is_short else "long"
        if side_filter != "both" and side != side_filter:
            continue

        # Entry classification
        ef = classify_entry(tag)
        if ef is None:
            unknown_entries[tag or "<null>"] = unknown_entries.get(tag or "<null>", 0) + 1
        else:
            es = entries[ef]
            es.n += 1
            es.raw_tags.add(tag)
            if is_open:
                es.open_n += 1

            # Peak unrealized P&L (leverage-adjusted)
            if max_rate and min_rate and lev:
                if is_short:
                    peak = (open_rate - min_rate) / open_rate * lev
                else:
                    peak = (max_rate - open_rate) / open_rate * lev
                es.peak_sum += peak * 100
                es.peak_n += 1
                if peak >= threshold:
                    es.touched += 1

            # Realized P&L (closed only)
            if not is_open and close_profit is not None:
                es.realized_sum += close_profit * 100
                es.realized_n += 1

        # Exit classification (closed only)
        if not is_open:
            xf = classify_exit(reason)
            if xf is None:
                unknown_exits[reason or "<null>"] = unknown_exits.get(reason or "<null>", 0) + 1
            else:
                xs = exits[xf]
                xs.n += 1
                xs.raw_reasons.add(reason)
                if close_profit is not None:
                    pct = close_profit * 100
                    xs.realized_sum += pct
                    xs.best = max(xs.best, pct)
                    xs.worst = min(xs.worst, pct)

    return entries, exits, unknown_entries, unknown_exits


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def fmt_pct(v: float, signed: bool = True) -> str:
    if v == 0 and signed is False:
        return "    -"
    sign = "+" if signed and v >= 0 else ""
    return f"{sign}{v:.2f}%"


def report_entries(entries: dict, threshold: float):
    print(f"\n=== ENTRY FAMILIES (touch threshold {threshold*100:+.1f}%) ===\n")
    print(f"{'family':<22} {'side':<6} {'n':>4} {'open':>5} {'touch':>6} {'hit%':>7} {'avg_peak':>10} {'avg_real':>10} {'slip':>8}  raw_tags")
    print("-" * 110)
    for fid, _side, _, _desc in ENTRY_FAMILIES:
        s = entries[fid]
        if s.n == 0:
            print(f"{s.family:<22} {s.side:<6} {0:>4} {0:>5} {0:>6} {'  -':>7} {'  -':>10} {'  -':>10} {'  -':>8}  (never fired)")
            continue
        raw = ", ".join(sorted(s.raw_tags - {None})) if s.raw_tags else ""
        if len(raw) > 30:
            raw = raw[:27] + "..."
        print(
            f"{s.family:<22} {s.side:<6} {s.n:>4} {s.open_n:>5} {s.touched:>6} "
            f"{s.hit_pct:>6.1f}% "
            f"{fmt_pct(s.avg_peak):>10} "
            f"{fmt_pct(s.avg_realized):>10} "
            f"{fmt_pct(s.slippage):>8}  {raw}"
        )


def report_exits(exits: dict):
    print(f"\n=== EXIT FAMILIES ===\n")
    print(f"{'family':<32} {'kind':<7} {'n':>4} {'avg':>9} {'best':>9} {'worst':>9}")
    print("-" * 75)
    for fid, _, kind, _desc in EXIT_FAMILIES:
        s = exits[fid]
        if s.n == 0:
            tag = "(legacy)" if kind == "legacy" else "(never fired)"
            print(f"{s.family:<32} {kind:<7} {0:>4} {'  -':>9} {'  -':>9} {'  -':>9}  {tag}")
            continue
        print(
            f"{s.family:<32} {kind:<7} {s.n:>4} "
            f"{fmt_pct(s.avg):>9} "
            f"{fmt_pct(s.best):>9} "
            f"{fmt_pct(s.worst):>9}"
        )


def report_unknown(unknown_entries, unknown_exits):
    if unknown_entries:
        print(f"\n=== UNCLASSIFIED ENTRY TAGS ===")
        for tag, n in sorted(unknown_entries.items(), key=lambda x: -x[1]):
            print(f"  {tag}: {n}")
    if unknown_exits:
        print(f"\n=== UNCLASSIFIED EXIT REASONS ===")
        for reason, n in sorted(unknown_exits.items(), key=lambda x: -x[1]):
            print(f"  {reason}: {n}")


def build_log_record(db_path: Path, args, total_rows, entries, exits, ue, ux) -> dict:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "db": db_path.name,
        "scope_days": args.days,
        "side": args.side,
        "threshold": args.threshold,
        "total_rows": total_rows,
        "entries": {
            fid: {
                "side": s.side,
                "n": s.n,
                "open": s.open_n,
                "touched": s.touched,
                "hit_pct": round(s.hit_pct, 2),
                "avg_peak_pct": round(s.avg_peak, 3),
                "avg_realized_pct": round(s.avg_realized, 3),
                "slippage_pct": round(s.slippage, 3),
                "raw_tags": sorted(t for t in s.raw_tags if t),
            }
            for fid, s in entries.items()
        },
        "exits": {
            fid: {
                "kind": s.kind,
                "n": s.n,
                "avg_pct": round(s.avg, 3),
                "best_pct": round(s.best, 3) if s.n else None,
                "worst_pct": round(s.worst, 3) if s.n else None,
            }
            for fid, s in exits.items()
        },
        "unknown_entries": ue,
        "unknown_exits": ux,
    }


def append_log(record: dict, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="user_data/tradesv3-futures.sqlite")
    p.add_argument("--days", type=int, default=0, help="0 = all time")
    p.add_argument("--threshold", type=float, default=0.01, help="touch threshold (default 0.01 = +1%)")
    p.add_argument("--side", choices=["both", "long", "short"], default="both")
    p.add_argument("--log", default="user_data/logs/touch_rate_tracker.jsonl",
                   help="JSONL log file to append run results to (set empty to disable)")
    p.add_argument("--no-print", action="store_true", help="suppress stdout report (logging only)")
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    rows = fetch_trades(db_path, args.days)
    if not rows:
        print("No trades found.")
        return

    entries, exits, ue, ux = aggregate(rows, args.threshold, args.side)

    if not args.no_print:
        scope = f"last {args.days}d" if args.days else "all-time"
        print(f"\nSygnif touch-rate tracker — {db_path.name} — {scope} — side={args.side}")
        print(f"Total trades scanned: {len(rows)}")
        report_entries(entries, args.threshold)
        report_exits(exits)
        report_unknown(ue, ux)
        print()

    if args.log:
        record = build_log_record(db_path, args, len(rows), entries, exits, ue, ux)
        log_path = Path(args.log)
        append_log(record, log_path)
        if not args.no_print:
            print(f"Logged run to {log_path}")


if __name__ == "__main__":
    main()
