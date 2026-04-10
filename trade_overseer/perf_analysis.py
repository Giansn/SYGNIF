#!/usr/bin/env python3
"""
Sygnif Performance Analysis — modeled on NautilusTrader's portfolio accounting.

NT reference implementation:
  - crates/portfolio/src/manager.rs          → PortfolioManager (PnL, positions)
  - crates/analysis/src/statistics/mod.rs    → PortfolioStatistics trait
  - nautilus_trader/analysis/statistics.py   → PortfolioStatisticCalculator
  - nautilus_trader/analysis/reporter.py     → ReportProvider (generate_*)

NT portfolio analysis chain:
  1. PortfolioManager tracks open/closed positions with realized PnL
  2. ReportProvider.generate_order_fills_report() → DataFrame of all fills
  3. ReportProvider.generate_positions_report() → closed position summary
  4. PortfolioStatisticCalculator calculates: win_rate, expectancy,
     profit_factor, sharpe, sortino, max_drawdown, avg_win/loss, payoff_ratio

SYGNIF mapping: Query Freqtrade SQLite, compute NT-equivalent portfolio stats.

Usage:
  python3 trade_overseer/perf_analysis.py
  python3 trade_overseer/perf_analysis.py --db user_data/tradesv3-futures.sqlite
  python3 trade_overseer/perf_analysis.py --days 14 --side long
  python3 trade_overseer/perf_analysis.py --json
"""
import argparse
import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional


def _connect(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        print(f"ERROR: Database not found: {db_path}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _collapse_tag(tag: str) -> str:
    """Collapse fa_s{N} / fa_short_s{N} (and sygnif_* / legacy claude_*) to family names."""
    if not tag:
        return "unknown"
    if tag.startswith(("fa_swing_short", "claude_swing_short", "sygnif_swing_short")):
        return "fa_swing_shortN"
    if tag.startswith(("fa_swing", "claude_swing", "sygnif_swing")):
        return "fa_swingN"
    if tag.startswith(("fa_short_s", "claude_short_s", "sygnif_short_s")):
        return "fa_short_sN"
    if tag.startswith(("fa_s", "claude_s", "sygnif_s")) and "swing" not in tag:
        return "fa_sN"
    return tag


def fetch_trades(
    conn: sqlite3.Connection,
    days: Optional[int] = None,
    side: Optional[str] = None,
) -> list[dict]:
    query = "SELECT * FROM trades WHERE is_open = 0"
    params: list = []

    if days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        query += " AND close_date >= ?"
        params.append(cutoff)

    if side == "long":
        query += " AND (is_short = 0 OR is_short IS NULL)"
    elif side == "short":
        query += " AND is_short = 1"

    query += " ORDER BY close_date DESC"
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# ── NT-style PortfolioStatistics (nautilus_trader/analysis/statistics.py) ──

def _safe_div(a: float, b: float) -> float:
    return a / b if b != 0 else 0.0


def compute_portfolio_stats(returns: list[float]) -> dict:
    """Compute NT PortfolioStatisticCalculator-equivalent metrics.

    NT calculates these in nautilus_trader/analysis/statistics.py:
      - win_rate, avg_winner, avg_loser, expectancy, profit_factor
      - sharpe_ratio, sortino_ratio, max_drawdown, avg_drawdown
    """
    if not returns:
        return {
            "count": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "total_return": 0.0, "avg_return": 0.0,
            "avg_winner": 0.0, "avg_loser": 0.0,
            "best": 0.0, "worst": 0.0,
            "profit_factor": 0.0, "expectancy": 0.0, "payoff_ratio": 0.0,
            "sharpe_ratio": 0.0, "sortino_ratio": 0.0,
            "max_drawdown": 0.0, "avg_drawdown": 0.0,
            "consecutive_wins": 0, "consecutive_losses": 0,
        }

    winners = [r for r in returns if r > 0]
    losers = [r for r in returns if r <= 0]

    total = sum(returns)
    avg = total / len(returns)
    avg_w = sum(winners) / len(winners) if winners else 0.0
    avg_l = sum(losers) / len(losers) if losers else 0.0
    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))

    win_rate = len(winners) / len(returns)
    profit_factor = _safe_div(gross_profit, gross_loss)
    payoff_ratio = _safe_div(avg_w, abs(avg_l))
    expectancy = (win_rate * avg_w) + ((1 - win_rate) * avg_l)

    # Sharpe / Sortino (annualized assuming 5m bars, ~288 trades/day)
    std = _std(returns)
    downside_std = _std([r for r in returns if r < 0])
    annual_factor = math.sqrt(365)  # daily → annual
    sharpe = _safe_div(avg, std) * annual_factor if std > 0 else 0.0
    sortino = _safe_div(avg, downside_std) * annual_factor if downside_std > 0 else 0.0

    # Drawdown (NT: max_drawdown, avg_drawdown in portfolio stats)
    max_dd, avg_dd = _drawdown_stats(returns)

    # Consecutive streaks
    max_con_w, max_con_l = _consecutive_streaks(returns)

    return {
        "count": len(returns),
        "wins": len(winners),
        "losses": len(losers),
        "win_rate": win_rate * 100,
        "total_return": total,
        "avg_return": avg,
        "avg_winner": avg_w,
        "avg_loser": avg_l,
        "best": max(returns),
        "worst": min(returns),
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "payoff_ratio": payoff_ratio,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown": max_dd,
        "avg_drawdown": avg_dd,
        "consecutive_wins": max_con_w,
        "consecutive_losses": max_con_l,
    }


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = sum(values) / len(values)
    return math.sqrt(sum((v - avg) ** 2 for v in values) / (len(values) - 1))


def _drawdown_stats(returns: list[float]) -> tuple[float, float]:
    """NT-style drawdown: track cumulative equity curve, measure peak-to-trough."""
    if not returns:
        return 0.0, 0.0
    equity = 0.0
    peak = 0.0
    drawdowns = []
    max_dd = 0.0
    for r in returns:
        equity += r
        peak = max(peak, equity)
        dd = peak - equity
        if dd > 0:
            drawdowns.append(dd)
        max_dd = max(max_dd, dd)
    avg_dd = sum(drawdowns) / len(drawdowns) if drawdowns else 0.0
    return max_dd, avg_dd


def _consecutive_streaks(returns: list[float]) -> tuple[int, int]:
    max_w = max_l = cur_w = cur_l = 0
    for r in returns:
        if r > 0:
            cur_w += 1
            cur_l = 0
        else:
            cur_l += 1
            cur_w = 0
        max_w = max(max_w, cur_w)
        max_l = max(max_l, cur_l)
    return max_w, max_l


def analyze_family(trades: list[dict]) -> dict:
    """Compute NT-style stats for a group of trades."""
    returns = [(t.get("close_profit") or 0) for t in trades]
    stats = compute_portfolio_stats(returns)

    durations = []
    for t in trades:
        try:
            od = t.get("open_date", "")
            cd = t.get("close_date", "")
            if od and cd:
                if isinstance(od, str):
                    od = datetime.fromisoformat(od.replace("Z", "+00:00"))
                if isinstance(cd, str):
                    cd = datetime.fromisoformat(cd.replace("Z", "+00:00"))
                durations.append((cd - od).total_seconds() / 60.0)
        except Exception:
            pass

    stats["avg_duration_min"] = sum(durations) / len(durations) if durations else 0

    exit_reasons: dict[str, int] = defaultdict(int)
    for t in trades:
        er = t.get("exit_reason") or "unknown"
        exit_reasons[er] += 1
    stats["exit_reasons"] = dict(exit_reasons)

    return stats


def compare_vs_baseline(family_stats: dict, baseline_stats: dict) -> dict:
    if baseline_stats["count"] == 0:
        return {"verdict": "NO_BASELINE", "deltas": {}}

    deltas = {}
    for key in ("win_rate", "avg_return", "profit_factor", "expectancy", "sharpe_ratio"):
        deltas[key] = family_stats.get(key, 0) - baseline_stats.get(key, 0)

    score = 0
    if deltas["win_rate"] > 2:
        score += 1
    elif deltas["win_rate"] < -2:
        score -= 1
    if deltas["expectancy"] > 0.002:
        score += 1
    elif deltas["expectancy"] < -0.002:
        score -= 1
    if deltas["profit_factor"] > 0.1:
        score += 1
    elif deltas["profit_factor"] < -0.1:
        score -= 1

    if family_stats["count"] < 5:
        verdict = "INSUFFICIENT_DATA"
    elif score >= 2:
        verdict = "OUTPERFORMS"
    elif score >= 1:
        verdict = "BETTER"
    elif score <= -2:
        verdict = "UNDERPERFORMS"
    elif score <= -1:
        verdict = "WORSE"
    else:
        verdict = "MIXED"

    return {"verdict": verdict, "deltas": deltas, "score": score}


def run_analysis(
    db_path: str,
    days: Optional[int] = None,
    side: Optional[str] = None,
) -> dict:
    conn = _connect(db_path)
    trades = fetch_trades(conn, days=days, side=side)
    conn.close()

    if not trades:
        return {"error": "No closed trades found", "families": {}}

    families: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        tag = t.get("enter_tag") or "unknown"
        family = _collapse_tag(tag)
        families[family].append(t)

    results: dict[str, dict] = {}
    for family, family_trades in sorted(families.items(), key=lambda x: -len(x[1])):
        results[family] = analyze_family(family_trades)

    # Portfolio-level stats (all trades combined)
    all_returns = [(t.get("close_profit") or 0) for t in trades]
    portfolio = compute_portfolio_stats(all_returns)

    baseline = results.get("fa_sN", analyze_family([]))
    comparisons = {}
    for family, stats in results.items():
        if family != "fa_sN":
            comparisons[family] = compare_vs_baseline(stats, baseline)

    return {
        "total_trades": len(trades),
        "days": days,
        "side": side,
        "portfolio": portfolio,
        "families": results,
        "vs_baseline": comparisons,
        "baseline": "fa_sN",
    }


def print_report(analysis: dict):
    if "error" in analysis:
        print(f"Error: {analysis['error']}")
        return

    port = analysis.get("portfolio", {})

    print(f"\n{'='*80}")
    print(f"  SYGNIF Performance Analysis — {analysis['total_trades']} closed trades")
    if analysis.get("days"):
        print(f"  Window: last {analysis['days']} days")
    if analysis.get("side"):
        print(f"  Side: {analysis['side']}")
    print(f"{'='*80}")

    # Portfolio summary (NT ReportProvider style)
    print(f"\n  Portfolio Summary (NT PortfolioStatisticCalculator)")
    print(f"  {'Win Rate:':<22} {port.get('win_rate', 0):>7.1f}%")
    print(f"  {'Profit Factor:':<22} {port.get('profit_factor', 0):>7.2f}")
    print(f"  {'Expectancy:':<22} {port.get('expectancy', 0):>+7.4f}")
    print(f"  {'Sharpe Ratio:':<22} {port.get('sharpe_ratio', 0):>7.2f}")
    print(f"  {'Sortino Ratio:':<22} {port.get('sortino_ratio', 0):>7.2f}")
    print(f"  {'Max Drawdown:':<22} {port.get('max_drawdown', 0):>7.2%}")
    print(f"  {'Payoff Ratio:':<22} {port.get('payoff_ratio', 0):>7.2f}")
    print(f"  {'Avg Winner:':<22} {port.get('avg_winner', 0):>+7.2%}")
    print(f"  {'Avg Loser:':<22} {port.get('avg_loser', 0):>+7.2%}")
    print(f"  {'Best:':<22} {port.get('best', 0):>+7.2%}")
    print(f"  {'Worst:':<22} {port.get('worst', 0):>+7.2%}")
    print(f"  {'Consec Wins:':<22} {port.get('consecutive_wins', 0):>7d}")
    print(f"  {'Consec Losses:':<22} {port.get('consecutive_losses', 0):>7d}")

    # Family breakdown
    header = (
        f"\n{'Family':<24} {'N':>5} {'Win%':>6} {'PF':>6} {'Exp':>8} "
        f"{'Sharpe':>7} {'MaxDD':>7} {'Avg Dur':>8}"
    )
    print(header)
    print("-" * len(header.strip()))

    baseline_name = analysis.get("baseline", "fa_sN")
    for family, stats in analysis["families"].items():
        dur = f"{stats.get('avg_duration_min', 0):.0f}m" if stats.get("avg_duration_min") else "--"
        marker = " *" if family == baseline_name else ""
        print(
            f"{family:<24} {stats['count']:>5} {stats['win_rate']:>5.1f}% "
            f"{stats.get('profit_factor', 0):>5.2f} {stats.get('expectancy', 0):>+7.4f} "
            f"{stats.get('sharpe_ratio', 0):>6.2f} {stats.get('max_drawdown', 0):>6.2%} "
            f"{dur:>8}{marker}"
        )

    print(f"\n  * = baseline ({baseline_name})\n")

    # Verdicts
    vs = analysis.get("vs_baseline", {})
    if vs:
        print("Vs Baseline (NT-style alpha comparison):")
        for family, comp in vs.items():
            verdict = comp["verdict"]
            score = comp.get("score", 0)
            d = comp.get("deltas", {})
            print(
                f"  {family:<24} {verdict:<16} score={score:+d}  "
                f"(WR {d.get('win_rate', 0):+.1f}%, PF {d.get('profit_factor', 0):+.2f}, "
                f"Exp {d.get('expectancy', 0):+.4f})"
            )

    # Exit reason summary
    print(f"\n{'='*80}")
    print("  Exit Reason Distribution (top families)")
    print(f"{'='*80}\n")
    for family, stats in list(analysis["families"].items())[:5]:
        if stats["count"] == 0:
            continue
        print(f"  {family}:")
        for reason, count in sorted(stats["exit_reasons"].items(), key=lambda x: -x[1]):
            pct = count / stats["count"] * 100
            print(f"    {reason:<40} {count:>4} ({pct:.0f}%)")
        print()


def main():
    parser = argparse.ArgumentParser(description="Sygnif Performance Analysis (NT-style)")
    parser.add_argument(
        "--db", default="user_data/tradesv3-futures.sqlite",
        help="Path to Freqtrade SQLite DB",
    )
    parser.add_argument("--days", type=int, help="Only analyze last N days")
    parser.add_argument("--side", choices=["long", "short"], help="Filter by side")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--spot", action="store_true", help="Use spot DB instead")
    args = parser.parse_args()

    db = "user_data/tradesv3.sqlite" if args.spot else args.db
    analysis = run_analysis(db, days=args.days, side=args.side)

    if args.json:
        print(json.dumps(analysis, indent=2, default=str))
    else:
        print_report(analysis)


if __name__ == "__main__":
    main()
