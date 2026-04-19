#!/usr/bin/env python3
"""
**BTC Langzeit-Regime + aktueller Swarm/Hivemind-Snapshot** (Research, read-only).

Was dies **ist**
    Reproduzierbare **Auswertungs-Algorithmen** (Zählung, Tages-Logreturns, grobe Halving-Ären) auf
    verfügbaren **Daily-OHLCV-JSONs** plus einmal ``compute_swarm()`` (optional Truthcoin/Hivemind, wenn
    deine ``.env`` das einschaltet).

Was dies **nicht** ist
    Kein „verborgener Satoshi-Algorithmus“, kein Beweis zukünftiger Renditen. **Hivemind** in Sygnif ist
    primär **Liveness/Protokoll-Signal** für Swarm (siehe ``truthcoin_hivemind_swarm_core.py``), **kein**
    Orakel über die komplette BTC-Historie.

**Volle Historie (annähernd „seit Beginn“)**
    Bybit-Spot reicht **nicht** bis 2009. Für sehr lange Preisreihen: führe aus dem Repo-Root aus::

      python3 finance_agent/btc_specialist/scripts/pull_btc_extended_history.py \\
        --daily-bars 5000 --no-coingecko

    Für **2013+** (CoinGecko) **ohne** Pro-Key vorsichtig mit Delays — siehe Script-Doku dort::

      python3 finance_agent/btc_specialist/scripts/pull_btc_extended_history.py

    Danach erneut dieses Report-Skript.

**Ausgabe**
    Standard: ``prediction_agent/btc_longhistory_swarm_regime_report.json``
    Override: ``SYGNIF_BTC_REGIME_REPORT_JSON=/path.json``

Examples::

  python3 scripts/btc_longhistory_swarm_regime_report.py
  SYGNIF_SWARM_CORE_ENGINE=hivemind python3 scripts/btc_longhistory_swarm_regime_report.py
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
_DATA = _REPO / "finance_agent" / "btc_specialist" / "data"
_FA = _REPO / "finance_agent"


# Approximate Bitcoin halving dates (UTC calendar day) for coarse era labels only.
_HALVINGS: tuple[tuple[date, str], ...] = (
    (date(2012, 11, 28), "after_1st_halving"),
    (date(2016, 7, 9), "after_2nd_halving"),
    (date(2020, 5, 11), "after_3rd_halving"),
    (date(2024, 4, 20), "after_4th_halving"),
)


def _report_path() -> Path:
    raw = (os.environ.get("SYGNIF_BTC_REGIME_REPORT_JSON") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _REPO / "prediction_agent" / "btc_longhistory_swarm_regime_report.json"


def _load_daily_json(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path} must be a JSON list")
    return [x for x in raw if isinstance(x, dict)]


def _ts_ms_to_date(ts_ms: int) -> date:
    return datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc).date()


def _era_label(d: date) -> str:
    if d < _HALVINGS[0][0]:
        return "genesis_to_1st_halving"
    label = "after_4th_halving"
    for halve_day, name in _HALVINGS:
        if d < halve_day:
            break
        label = name
    return label


@dataclass(frozen=True)
class _Bar:
    day: date
    close: float


def _bars_from_rows(rows: list[dict[str, Any]]) -> list[_Bar]:
    out: list[_Bar] = []
    for r in rows:
        try:
            ts = int(r.get("t") or 0)
            c = float(r.get("c") or 0)
        except (TypeError, ValueError):
            continue
        if ts <= 0 or c <= 0:
            continue
        out.append(_Bar(day=_ts_ms_to_date(ts), close=c))
    out.sort(key=lambda b: b.day)
    # de-dupe by day (last wins)
    by_d: dict[date, _Bar] = {}
    for b in out:
        by_d[b.day] = b
    return [by_d[k] for k in sorted(by_d)]


def _log_returns(closes: list[float]) -> list[float]:
    lr: list[float] = []
    for i in range(1, len(closes)):
        a, b = closes[i - 1], closes[i]
        if a > 0 and b > 0:
            lr.append(math.log(b / a))
    return lr


def _summary_stats(closes: list[float]) -> dict[str, Any]:
    lr = _log_returns(closes)
    if len(lr) < 2:
        return {"n_daily_returns": len(lr), "note": "too_few_returns"}
    mu = statistics.fmean(lr)
    sd = statistics.pstdev(lr) if len(lr) > 1 else 0.0
    up = sum(1 for x in lr if x > 0)
    return {
        "n_daily_returns": len(lr),
        "pct_up_days": round(100.0 * up / len(lr), 4),
        "mean_log_return_daily": round(mu, 8),
        "stdev_log_return_daily": round(sd, 8),
        "annualized_vol_log_approx": round(sd * math.sqrt(365.0), 6),
    }


def _per_era(bars: list[_Bar]) -> dict[str, Any]:
    buckets: dict[str, list[float]] = {}
    for b in bars:
        era = _era_label(b.day)
        buckets.setdefault(era, []).append(b.close)
    out: dict[str, Any] = {}
    for era, cls in sorted(buckets.items(), key=lambda x: x[0]):
        if len(cls) < 3:
            out[era] = {"n_days": len(cls), "note": "too_short"}
            continue
        st = _summary_stats(cls)
        st["n_days"] = len(cls)
        out[era] = st
    return out


def _pick_data_path(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    for name in ("btc_daily_ohlcv_long.json", "btc_daily_90d.json"):
        p = _DATA / name
        if p.is_file():
            return p
    raise FileNotFoundError(
        f"No daily data under {_DATA} (expected btc_daily_ohlcv_long.json or btc_daily_90d.json). "
        "Run: python3 finance_agent/btc_specialist/scripts/pull_btc_extended_history.py"
    )


def _compute_swarm_safe() -> dict[str, Any]:
    if str(_FA) not in sys.path:
        sys.path.insert(0, str(_FA))
    try:
        import swarm_knowledge as sk  # noqa: PLC0415
    except ImportError as e:
        return {"ok": False, "error": f"import swarm_knowledge: {e}"}
    try:
        doc = sk.compute_swarm()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if not isinstance(doc, dict):
        return {"ok": False, "error": "compute_swarm not a dict"}
    hm = doc.get("hivemind_explore")
    src = doc.get("sources") if isinstance(doc.get("sources"), dict) else {}
    hm_src = src.get("hm") if isinstance(src.get("hm"), dict) else {}
    slim = {
        "ok": True,
        "swarm_mean": doc.get("swarm_mean"),
        "swarm_label": doc.get("swarm_label"),
        "swarm_conflict": doc.get("swarm_conflict"),
        "swarm_core_engine": doc.get("swarm_core_engine"),
        "swarm_engine": doc.get("swarm_engine"),
        "swarm_engine_detail": doc.get("swarm_engine_detail"),
        "sources_n": doc.get("sources_n"),
        "hm_vote": hm_src.get("vote"),
        "hm_detail": hm_src.get("detail"),
        "hivemind_explore_ok": hm.get("ok") if isinstance(hm, dict) else None,
        "hivemind_explore_slots_voting_n": hm.get("slots_voting_n") if isinstance(hm, dict) else None,
    }
    return slim


def main() -> int:
    ap = argparse.ArgumentParser(description="BTC long-history regime stats + Swarm/Hivemind snapshot")
    ap.add_argument(
        "--data",
        type=Path,
        default=None,
        help=f"Override daily OHLCV JSON (default: {_DATA}/btc_daily_ohlcv_long.json or btc_daily_90d.json)",
    )
    args = ap.parse_args()

    path = _pick_data_path(args.data)
    rows = _load_daily_json(path)
    bars = _bars_from_rows(rows)
    if len(bars) < 5:
        print(f"Too few bars after parse: {len(bars)} from {path}", file=sys.stderr)
        return 2

    closes = [b.close for b in bars]
    first_d, last_d = bars[0].day, bars[-1].day

    report: dict[str, Any] = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "disclaimer": (
            "Regime statistics are descriptive only. Hivemind vote reflects Truthcoin liveness/heuristic, "
            "not a fitted model on full Bitcoin history."
        ),
        "data": {
            "path": str(path.resolve()),
            "n_candles": len(bars),
            "first_date": str(first_d),
            "last_date": str(last_d),
            "span_days": (last_d - first_d).days,
        },
        "whole_sample": _summary_stats(closes),
        "by_halving_era": _per_era(bars),
        "algorithms_used": [
            "daily_log_return = ln(close_t / close_{t-1})",
            "pct_up_days = fraction(log_return > 0)",
            "annualized_vol_approx = stdev(daily_log_returns) * sqrt(365)",
            "era_label = coarse bucket vs known halving calendar dates",
            "swarm = finance_agent.swarm_knowledge.compute_swarm()",
        ],
        "swarm_hivemind_snapshot": _compute_swarm_safe(),
    }

    out = _report_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(out)

    print(json.dumps({"ok": True, "wrote": str(out), "bars": len(bars), "from": str(first_d), "to": str(last_d)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
