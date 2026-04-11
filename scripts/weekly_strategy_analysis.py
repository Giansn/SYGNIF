#!/usr/bin/env python3
"""
Weekly Sygnif strategy analysis: horizon check, fresh TA snapshot, spot/futures trade stats.

Writes a sidecar JSON (default: user_data/strategy_adaptation_weekly.json) — hot path for
Freqtrade overrides remains strategy_adaptation.json (manual / agent).

Env:
  WEEKLY_STRATEGY_SYMBOL   default XRP
  WEEKLY_TOUCH_ADAPTATION  if "1", merge summary into strategy_adaptation.json top-level
                            weekly_report (optional; creates git noise if that file is tracked)
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
HORIZON = REPO / "scripts" / "prediction_horizon_check.py"
DATA_DIR = Path.home() / ".local/share/sygnif-agent/predictions"
WEEKLY_PATH = REPO / "user_data" / "strategy_adaptation_weekly.json"
ADAPT_PATH = REPO / "user_data" / "strategy_adaptation.json"


def _run_horizon(args: list[str]) -> tuple[int, str]:
    cmd = [sys.executable, str(HORIZON)] + args
    p = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=120)
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out.strip()


def _sqlite_stats(path: Path, days: int = 7) -> dict:
    if not path.exists():
        return {"error": f"missing {path.name}"}
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            cur = con.cursor()
            cur.execute(
                """
                SELECT COUNT(*), COALESCE(AVG(close_profit), 0), COALESCE(SUM(realized_profit), 0)
                FROM trades
                WHERE is_open = 0 AND close_date IS NOT NULL AND close_date >= ?
                """,
                (since,),
            )
            row = cur.fetchone()
            n, avg_pct, sum_abs = row[0], float(row[1] or 0), float(row[2] or 0)
            return {
                "closed_trades": n,
                "avg_close_profit_pct": round(avg_pct * 100.0, 4) if avg_pct else 0.0,
                "sum_realized_profit_quote": round(sum_abs, 6),
                "window_days": days,
            }
        finally:
            con.close()
    except Exception as e:
        return {"error": str(e)}


def _merge_adaptation_weekly(summary: dict) -> None:
    """Optional: add top-level weekly_report to strategy_adaptation.json (preserves overrides)."""
    data: dict = {}
    if ADAPT_PATH.exists():
        try:
            data = json.loads(ADAPT_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"weekly: skip merge into strategy_adaptation.json: {e}")
            return
    if not isinstance(data, dict):
        data = {}
    data["weekly_report"] = summary
    tmp = ADAPT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(ADAPT_PATH)


def main() -> int:
    sym = os.environ.get("WEEKLY_STRATEGY_SYMBOL", "XRP").upper()
    touch = os.environ.get("WEEKLY_TOUCH_ADAPTATION", "").strip() in ("1", "true", "yes")

    lines: list[str] = []
    lines.append(f"Weekly strategy analysis — symbol {sym} (UTC {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')})")
    lines.append("")

    check_exit: int | None = None
    check_out = ""

    # 1) Horizon: compare previous snapshot if present
    latest = DATA_DIR / f"{sym}_latest.json"
    if latest.exists():
        check_exit, check_out = _run_horizon(["check", "--symbol", sym])
        lines.append("=== prediction_horizon check (vs latest snapshot) ===")
        lines.append(check_out if check_out else f"(no output, exit {check_exit})")
        lines.append("")
    else:
        lines.append("=== prediction_horizon check ===")
        lines.append(f"No prior snapshot at {latest} — skipping check; save will create it.")
        lines.append("")

    # 2) New weekly snapshot
    code, out = _run_horizon(["save", "--symbol", sym, "--note", "weekly cron"])
    lines.append("=== prediction_horizon save (new weekly baseline) ===")
    lines.append(out if out else f"(no output, exit {code})")
    if code != 0:
        lines.append("")
        lines.append("ERROR: save failed — fix finance_agent import or pass --support/--resistance.")
        print("\n".join(lines))
        return code

    # 3) Trade DB stats (closed trades, last 7d)
    spot = REPO / "user_data" / "tradesv3.sqlite"
    fut = REPO / "user_data" / "tradesv3-futures.sqlite"
    stats_spot = _sqlite_stats(spot)
    stats_fut = _sqlite_stats(fut)
    lines.append("")
    lines.append("=== closed trades (7d) ===")
    lines.append(f"spot:    {json.dumps(stats_spot, ensure_ascii=False)}")
    lines.append(f"futures: {json.dumps(stats_fut, ensure_ascii=False)}")

    report = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbol": sym,
        "horizon_check": (
            {"exit": check_exit, "summary": (check_out or "")[:4000]}
            if latest.exists()
            else None
        ),
        "horizon_save_ok": True,
        "trades_7d": {"spot": stats_spot, "futures": stats_fut},
    }

    try:
        from ms3_metrics_feed import build_ms3_metrics_bundle

        report["ms3_metrics"] = build_ms3_metrics_bundle(
            REPO, append_entry_perf_log=False
        )
    except Exception as e:
        report["ms3_metrics"] = {"error": str(e)}

    WEEKLY_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEEKLY_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines.append("")
    lines.append(f"Sidecar report: {WEEKLY_PATH}")

    if touch:
        _merge_adaptation_weekly(report)
        lines.append(f"Merged weekly_report into {ADAPT_PATH}")

    text = "\n".join(lines)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
