"""
Market Strategy 3 — metrics bundle (design feed).

Aggregates NT-style portfolio stats (perf_analysis), entry-tag families
(entry_performance), trading_success 7d rollup, and closed_trades_analysis
entry/exit families. Writes JSON + JSONL for evidence / future MS3 regime work.

Used by:
  - scripts/collect_ms3_metrics.py (daily cron)
  - scripts/weekly_strategy_analysis.py (embeds into weekly sidecar)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def _compact_family(stats: dict[str, Any]) -> dict[str, Any]:
    er = stats.get("exit_reasons") or {}
    if isinstance(er, dict):
        top = dict(sorted(er.items(), key=lambda kv: -kv[1])[:5])
    else:
        top = {}
    keys = (
        "count",
        "wins",
        "losses",
        "win_rate",
        "total_return",
        "avg_return",
        "profit_factor",
        "expectancy",
        "payoff_ratio",
        "sharpe_ratio",
        "sortino_ratio",
        "max_drawdown",
        "avg_drawdown",
        "consecutive_wins",
        "consecutive_losses",
        "avg_duration_min",
    )
    out: dict[str, Any] = {k: stats.get(k) for k in keys}
    out["exit_reasons_top5"] = top
    return out


def _compact_perf_analysis(analysis: dict[str, Any], *, max_families: int = 12) -> dict[str, Any]:
    if "error" in analysis:
        return {"error": analysis["error"]}
    fams = analysis.get("families") or {}
    ranked = sorted(fams.items(), key=lambda kv: -(kv[1].get("count") or 0))[:max_families]
    return {
        "total_trades": analysis.get("total_trades"),
        "days": analysis.get("days"),
        "side": analysis.get("side"),
        "portfolio": analysis.get("portfolio"),
        "families": {k: _compact_family(v) for k, v in ranked},
    }


def _import_trade_overseer(repo: Path):
    td = str(repo / "trade_overseer")
    if td not in sys.path:
        sys.path.insert(0, td)
    from perf_analysis import run_analysis  # type: ignore

    return run_analysis


def _import_entry_performance(repo: Path):
    td = str(repo / "trade_overseer")
    if td not in sys.path:
        sys.path.insert(0, td)
    from entry_performance import (  # type: ignore
        aggregate,
        append_log,
        fetch_trades_sqlite,
    )

    return aggregate, append_log, fetch_trades_sqlite


def _import_closed_trades(repo: Path):
    fa = str(repo / "finance_agent")
    if fa not in sys.path:
        sys.path.insert(0, fa)
    from closed_trades_analysis import analyze_closed_trades  # type: ignore
    from closed_trades_reader import fetch_closed_trades  # type: ignore

    return analyze_closed_trades, fetch_closed_trades


def _run_trading_success_json(repo: Path, days: int = 7) -> dict[str, Any] | None:
    cmd = [
        sys.executable,
        str(repo / "trade_overseer" / "trading_success.py"),
        "--days",
        str(days),
        "--json",
        "--no-print",
    ]
    try:
        p = subprocess.run(
            cmd,
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"error": str(e)}
    if p.returncode != 0:
        return {"error": p.stderr.strip() or f"exit {p.returncode}"}
    raw = (p.stdout or "").strip()
    if not raw:
        return {"error": "empty stdout"}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        return {"error": f"invalid json: {e}"}


def build_ms3_metrics_bundle(
    repo: Path | None = None,
    *,
    windows: tuple[int, ...] = (7, 30),
    append_entry_perf_log: bool = False,
) -> dict[str, Any]:
    repo = repo or _repo()
    run_analysis = _import_trade_overseer(repo)
    aggregate, append_log, fetch_trades_sqlite = _import_entry_performance(repo)
    analyze_closed_trades, fetch_closed_trades = _import_closed_trades(repo)

    spot_db = repo / "user_data" / "tradesv3.sqlite"
    fut_db = repo / "user_data" / "tradesv3-futures.sqlite"

    perf: dict[str, Any] = {}
    for name, path in (("spot", spot_db), ("futures", fut_db)):
        perf[name] = {}
        if not path.is_file():
            perf[name] = {"error": f"missing {path.name}"}
            continue
        p = str(path)
        for d in windows:
            try:
                analysis = run_analysis(p, days=d, side=None)
                perf[name][f"{d}d"] = _compact_perf_analysis(analysis)
            except Exception as e:
                perf[name][f"{d}d"] = {"error": str(e)}

    entry_families: dict[str, Any] = {}
    db_paths = {"spot": spot_db, "futures": fut_db}
    for d in windows:
        try:
            trades = fetch_trades_sqlite(db_paths, d, "both")
            stats = aggregate(trades)
            matched = sum(s.n for s in stats.values())
            entry_families[f"{d}d"] = {
                "matched_trades": matched,
                "families": {
                    fam: {
                        "n": s.n,
                        "wins": s.wins,
                        "losses": s.losses,
                        "win_rate_pct": round(s.win_rate, 2) if s.n else 0.0,
                        "total_profit_pct": round(s.profit_sum, 3),
                    }
                    for fam, s in stats.items()
                    if s.n > 0
                },
            }
            if append_entry_perf_log and matched > 0:
                log_path = repo / "user_data" / "logs" / "entry_performance.jsonl"
                append_log(stats, f"ms3_feed last {d}d — SQLite", log_path)
        except Exception as e:
            entry_families[f"{d}d"] = {"error": str(e)}

    closed_merged: dict[str, Any] = {}
    for d in windows:
        merged: list[dict[str, Any]] = []
        for path in (spot_db, fut_db):
            if path.is_file():
                merged.extend(fetch_closed_trades(path, days=d))
        try:
            closed_merged[f"{d}d"] = analyze_closed_trades(merged) if merged else {"trade_count": 0}
        except Exception as e:
            closed_merged[f"{d}d"] = {"error": str(e)}

    trading_success = _run_trading_success_json(repo, days=7)

    bundle = {
        "schema": "ms3_metrics_bundle",
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": str(repo),
        "windows_days": list(windows),
        "perf_nt": perf,
        "entry_tag_families": entry_families,
        "closed_trades_analysis": closed_merged,
        "trading_success_7d": trading_success,
    }
    return bundle


def write_ms3_metrics(repo: Path, bundle: dict[str, Any]) -> tuple[Path, Path]:
    repo = Path(repo)
    out_json = repo / "user_data" / "market_strategy_3_metrics.json"
    out_jsonl = repo / "user_data" / "logs" / "market_strategy_3_metrics.jsonl"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)

    compact = {
        "schema": bundle.get("schema"),
        "schema_version": bundle.get("schema_version"),
        "generated_utc": bundle.get("generated_utc"),
        "perf_nt": bundle.get("perf_nt"),
        "entry_tag_families": bundle.get("entry_tag_families"),
        "closed_trades_analysis": {
            k: {
                kk: vv
                for kk, vv in v.items()
                if kk
                in (
                    "error",
                    "trade_count",
                    "wins",
                    "losses",
                    "win_rate",
                    "profit_factor",
                    "total_return_pct",
                    "avg_return_pct",
                    "by_entry_family",
                    "by_exit_family",
                )
            }
            for k, v in (bundle.get("closed_trades_analysis") or {}).items()
            if isinstance(v, dict)
        },
        "trading_success_7d": bundle.get("trading_success_7d"),
    }

    tmp = out_json.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(compact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(out_json)

    line = json.dumps(compact, separators=(",", ":"), ensure_ascii=False, default=str)
    with out_jsonl.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

    return out_json, out_jsonl


def summarize_for_console(bundle: dict[str, Any]) -> str:
    """One short line + optional second line for cron Telegram limits."""
    parts: list[str] = ["MS3 metrics"]
    perf = bundle.get("perf_nt") or {}
    for leg in ("futures", "spot"):
        w7 = (perf.get(leg) or {}).get("7d") or {}
        if "error" in w7:
            parts.append(f"{leg}7d=err")
            continue
        port = w7.get("portfolio") or {}
        n = port.get("count", 0)
        sh = port.get("sharpe_ratio", 0.0)
        wr = port.get("win_rate", 0.0)
        parts.append(f"{leg}7d n={n} sharpe={sh:.2f} wr={wr:.0f}%")
    return " | ".join(parts)
