"""
Summaries for closed trades (JSON-serializable). Complements trade_overseer tools
without importing Telegram-heavy modules.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any, Optional


ENTRY_FAMILIES = {
    "strong_ta": re.compile(r"^strong_ta$"),
    "strong_ta_short": re.compile(r"^strong_ta_short$"),
    "fa_s": re.compile(r"^((fa_(short_)?s)|(claude_(short_)?s))-?\d+$"),
    "fa_swing": re.compile(r"^((fa_swing)|(claude_swing)|(sygnif_swing))(_short)?$"),
    "swing_failure": re.compile(r"^swing_failure(_short)?$"),
}

EXIT_FAMILIES = {
    "rsi_exit": re.compile(r"^exit_(short_)?profit_rsi_"),
    "willr_reversal": re.compile(r"^exit_(short_)?willr_reversal$"),
    "soft_stoploss": re.compile(r"^exit_(short_)?stoploss_conditional$"),
    "sf_ema_tp": re.compile(r"^exit_sf_(short_)?ema_tp$"),
    "sf_vol_sl": re.compile(r"^exit_sf_(short_)?vol_sl$"),
    "stoploss_on_exchange": re.compile(r"^stoploss_on_exchange$"),
    "trailing_stop": re.compile(r"^trailing_stop_loss$"),
    "roi": re.compile(r"^roi$"),
    "force_exit": re.compile(r"^force_exit$"),
    "emergency_exit": re.compile(r"^emergency_exit$"),
}


def _classify(tag: Optional[str], families: dict) -> str:
    if not tag:
        return "unknown"
    for name, rx in families.items():
        if rx.match(tag):
            return name
    return "other"


def analyze_closed_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Portfolio-style summary + breakdowns by pair, exit family, entry family."""
    if not trades:
        return {
            "trade_count": 0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "win_rate": 0.0,
            "total_return_pct": 0.0,
            "avg_return_pct": 0.0,
            "profit_factor": None,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
            "best_trade_pct": None,
            "worst_trade_pct": None,
            "by_pair": {},
            "by_exit_family": {},
            "by_entry_family": {},
        }

    wins = [t for t in trades if (t.get("close_profit") or 0) > 0]
    losses = [t for t in trades if (t.get("close_profit") or 0) < 0]
    flat = [t for t in trades if (t.get("close_profit") or 0) == 0]

    rets = [float(t.get("close_profit") or 0.0) * 100.0 for t in trades]
    total_ret = sum(rets)
    win_sum = sum(float(t.get("close_profit") or 0) for t in wins)
    loss_sum = abs(sum(float(t.get("close_profit") or 0) for t in losses))
    profit_factor = (win_sum / loss_sum) if loss_sum > 1e-12 else None

    by_pair: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "wins": 0, "pnl_pct_sum": 0.0}
    )
    for t in trades:
        p = (t.get("pair") or "?")[:32]
        b = by_pair[p]
        b["n"] += 1
        if (t.get("close_profit") or 0) > 0:
            b["wins"] += 1
        b["pnl_pct_sum"] += float(t.get("close_profit") or 0) * 100.0

    by_exit: dict[str, int] = defaultdict(int)
    for t in trades:
        fam = _classify(t.get("exit_reason"), EXIT_FAMILIES)
        by_exit[fam] += 1

    by_entry: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "wins": 0, "pnl_pct_sum": 0.0}
    )
    for t in trades:
        fam = _classify(t.get("enter_tag"), ENTRY_FAMILIES)
        b = by_entry[fam]
        b["n"] += 1
        if (t.get("close_profit") or 0) > 0:
            b["wins"] += 1
        b["pnl_pct_sum"] += float(t.get("close_profit") or 0) * 100.0

    for d in by_pair.values():
        d["win_rate"] = d["wins"] / d["n"] if d["n"] else 0.0
        d["avg_pnl_pct"] = d["pnl_pct_sum"] / d["n"] if d["n"] else 0.0
    for fam, d in by_entry.items():
        d["win_rate"] = d["wins"] / d["n"] if d["n"] else 0.0
        d["avg_pnl_pct"] = d["pnl_pct_sum"] / d["n"] if d["n"] else 0.0

    best = max(rets) if rets else None
    worst = min(rets) if rets else None

    return {
        "trade_count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(flat),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "total_return_pct": total_ret,
        "avg_return_pct": total_ret / len(trades),
        "profit_factor": profit_factor,
        "avg_win_pct": (sum(float(t.get("close_profit") or 0) for t in wins) / len(wins) * 100.0)
        if wins
        else 0.0,
        "avg_loss_pct": (sum(float(t.get("close_profit") or 0) for t in losses) / len(losses) * 100.0)
        if losses
        else 0.0,
        "best_trade_pct": best,
        "worst_trade_pct": worst,
        "by_pair": dict(sorted(by_pair.items(), key=lambda kv: -kv[1]["n"])[:24]),
        "by_exit_family": dict(sorted(by_exit.items(), key=lambda kv: -kv[1])),
        "by_entry_family": dict(by_entry),
    }


def format_analysis_text(summary: dict[str, Any]) -> str:
    """Human-readable block for logs / Telegram-sized snippets."""
    if summary["trade_count"] == 0:
        return "No closed trades in sample."
    pf = summary["profit_factor"]
    pf_s = f"{pf:.2f}" if pf is not None and not math.isinf(pf) else ("inf" if pf and math.isinf(pf) else "n/a")
    lines = [
        f"Closed trades: {summary['trade_count']} | W {summary['wins']} / L {summary['losses']} "
        f"| win rate {summary['win_rate']:.1%}",
        f"Avg return/trade: {summary['avg_return_pct']:.3f}% | total {summary['total_return_pct']:.2f}% "
        f"| profit factor {pf_s}",
        f"Avg win {summary['avg_win_pct']:.3f}% | avg loss {summary['avg_loss_pct']:.3f}% "
        f"| best {summary['best_trade_pct']:.3f}% worst {summary['worst_trade_pct']:.3f}%",
        "Top pairs by count: "
        + ", ".join(
            f"{p} (n={d['n']}, wr={d['win_rate']:.0%})"
            for p, d in list(summary["by_pair"].items())[:8]
        ),
    ]
    return "\n".join(lines)
