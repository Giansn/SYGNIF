#!/usr/bin/env python3
"""
**Explicit BTC forecast evaluation** — log each live fit, then resolve **one unseen 5m bar** later.

Flow
1. ``append_forecast_pending`` — after ``fit_predict_live``, append one NDJSON row (``status=pending``).
2. ``process_pending_outcomes`` — for rows whose next bar should have closed (now ≥ last_bar + bar_min + slack),
   fetch fresh Bybit 5m klines, read the **first bar strictly after** ``last_candle_utc``, and append an outcome row.

Metrics (``--report`` in CLI) are computed only on **resolved** rows (true hold-out: actual next bar was not in the
training window at forecast time).

Env
- ``SYGNIF_PREDICT_EVAL_LOG`` — ``1``/``true`` (default **on**) to append pending rows.
- ``SYGNIF_PREDICT_EVAL_FORECAST_JSONL`` — pending log path (default ``prediction_agent/btc_eval_forecasts_pending.jsonl``).
- ``SYGNIF_PREDICT_EVAL_OUTCOMES_JSONL`` — resolved outcomes (default ``prediction_agent/btc_eval_outcomes.jsonl``).
- ``SYGNIF_PREDICT_EVAL_SLACK_SEC`` — wait after nominal next-bar close before resolving (default **120**).
- ``SYGNIF_PREDICT_EVAL_BAR_MIN`` — bar length in minutes (default **5**).
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

_REPO = Path(__file__).resolve().parents[1]


def _env_truthy(name: str, *, default: bool = True) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _prediction_dir() -> Path:
    for key in ("PREDICTION_AGENT_DIR", "SYGNIF_PREDICTION_AGENT_DIR"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return Path(raw).expanduser()
    return _REPO / "prediction_agent"


def forecast_pending_path() -> Path:
    raw = (os.environ.get("SYGNIF_PREDICT_EVAL_FORECAST_JSONL") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _prediction_dir() / "btc_eval_forecasts_pending.jsonl"


def outcomes_path() -> Path:
    raw = (os.environ.get("SYGNIF_PREDICT_EVAL_OUTCOMES_JSONL") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _prediction_dir() / "btc_eval_outcomes.jsonl"


def _eval_id(symbol: str, last_candle_utc: str, generated_utc: str) -> str:
    h = hashlib.sha256(f"{symbol}|{last_candle_utc}|{generated_utc}".encode()).hexdigest()
    return h[:24]


def append_forecast_pending(out: dict[str, Any], *, symbol: str) -> str | None:
    """
    Record a forecast for later outcome labelling.

    Returns ``eval_id`` or ``None`` when logging is disabled.
    """
    if not _env_truthy("SYGNIF_PREDICT_EVAL_LOG", default=True):
        return None
    if not isinstance(out, dict):
        return None
    last_c = str(out.get("last_candle_utc") or "").strip()
    gen = str(out.get("generated_utc") or "").strip()
    if not last_c or not gen:
        return None
    sym = (symbol or "BTCUSDT").replace("/", "").upper().strip()
    eid = _eval_id(sym, last_c, gen)
    pred = out.get("predictions") if isinstance(out.get("predictions"), dict) else {}
    rf = pred.get("random_forest") if isinstance(pred.get("random_forest"), dict) else {}
    xg = pred.get("xgboost") if isinstance(pred.get("xgboost"), dict) else {}
    lr = pred.get("direction_logistic") if isinstance(pred.get("direction_logistic"), dict) else {}
    row = {
        "schema": "sygnif.btc_forecast_eval_pending/v1",
        "eval_id": eid,
        "symbol": sym,
        "predicted_at_utc": gen,
        "last_candle_utc": last_c,
        "bar_minutes": int(_env_float("SYGNIF_PREDICT_EVAL_BAR_MIN", 5.0)),
        "current_close": out.get("current_close"),
        "forecast": {
            "rf_next_mean": rf.get("next_mean"),
            "xgb_next_mean": xg.get("next_mean"),
            "rf_delta": rf.get("delta"),
            "xgb_delta": xg.get("delta"),
            "logreg_label": lr.get("label"),
            "logreg_confidence": lr.get("confidence"),
            "consensus": pred.get("consensus"),
            "consensus_nautilus_enhanced": pred.get("consensus_nautilus_enhanced"),
        },
        "status": "pending",
    }
    path = forecast_pending_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
    return eid


def _parse_ts(s: str) -> pd.Timestamp:
    t = pd.Timestamp(s)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    return t


def _load_existing_eval_ids(path: Path) -> set[str]:
    """Stream the whole outcomes file so dedupe stays correct even for very large logs."""
    if not path.is_file():
        return set()
    out: set[str] = set()
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                eid = str(o.get("eval_id") or "").strip()
                if eid:
                    out.add(eid)
    except OSError:
        return set()
    return out


def _fetch_linear_5m_df(symbol: str, limit: int = 200) -> pd.DataFrame:
    """Local import to avoid circular imports at module load."""
    from btc_predict_live import fetch_linear_5m_klines  # noqa: PLC0415

    return fetch_linear_5m_klines(symbol, limit=limit)


def _first_row_after(df: pd.DataFrame, last_bar_ts: pd.Timestamp) -> dict[str, Any] | None:
    if df is None or df.empty or "Date" not in df.columns:
        return None
    dts = pd.to_datetime(df["Date"], utc=True)
    # First candle strictly after the as-of bar open/label time
    mask = dts > last_bar_ts
    if not bool(mask.any()):
        return None
    sub = df.loc[mask].iloc[0]
    return sub.to_dict()


def _due_ready(last_candle_utc: str, *, bar_min: float, slack_sec: float) -> bool:
    t0 = _parse_ts(last_candle_utc)
    # Conservative: next bar's close is at least ~one bar after ``last_candle_utc`` (open labels) + slack.
    need = t0 + timedelta(minutes=float(bar_min) * 2.0) + timedelta(seconds=max(0.0, slack_sec))
    return bool(pd.Timestamp.now(tz="UTC") >= need)


def process_pending_outcomes(
    *,
    symbol: str | None = None,
    max_rows: int = 2000,
    fetch_limit: int = 400,
) -> dict[str, Any]:
    """
    Resolve pending forecasts whose next bar should have printed on Bybit.

    Returns a small summary dict (processed, skipped, errors).
    """
    pending_p = forecast_pending_path()
    out_p = outcomes_path()
    done = _load_existing_eval_ids(out_p)
    if not pending_p.is_file():
        return {"processed": 0, "skipped": 0, "errors": 0, "reason": "no_pending_file"}

    bar_min = _env_float("SYGNIF_PREDICT_EVAL_BAR_MIN", 5.0)
    slack = _env_float("SYGNIF_PREDICT_EVAL_SLACK_SEC", 120.0)
    sym_filter = (symbol or "").replace("/", "").upper().strip()

    processed = 0
    skipped = 0
    errors = 0

    try:
        lines = pending_p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {"processed": 0, "skipped": 0, "errors": 1, "reason": "read_error"}

    tail = lines[-max_rows:] if len(lines) > max_rows else lines
    df_cache: dict[str, pd.DataFrame] = {}
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            errors += 1
            continue
        if row.get("status") != "pending":
            skipped += 1
            continue
        eid = str(row.get("eval_id") or "").strip()
        if not eid or eid in done:
            skipped += 1
            continue
        sym = str(row.get("symbol") or "BTCUSDT").upper()
        if sym_filter and sym != sym_filter:
            skipped += 1
            continue
        last_c = str(row.get("last_candle_utc") or "").strip()
        if not last_c:
            skipped += 1
            continue
        if not _due_ready(last_c, bar_min=bar_min, slack_sec=slack):
            skipped += 1
            continue

        try:
            last_ts = _parse_ts(last_c)
            if sym not in df_cache:
                df_cache[sym] = _fetch_linear_5m_df(sym, limit=fetch_limit)
            df = df_cache[sym]
            nxt = _first_row_after(df, last_ts)
            if not nxt:
                errors += 1
                continue
            o_close = float(nxt.get("Close") or 0.0)
            o_high = float(nxt.get("High") or 0.0)
            o_low = float(nxt.get("Low") or 0.0)
            o_open = float(nxt.get("Open") or 0.0)
            o_mean = (o_high + o_low) / 2.0 if o_high and o_low else o_close
            o_date = pd.Timestamp(nxt.get("Date"))
            if o_date.tzinfo is None:
                o_date = o_date.tz_localize("UTC")
            else:
                o_date = o_date.tz_convert("UTC")

            fc = row.get("forecast") if isinstance(row.get("forecast"), dict) else {}
            pred_close = float(row.get("current_close") or 0.0)
            rf_n = float(fc.get("rf_next_mean") or 0.0)
            xg_n = float(fc.get("xgb_next_mean") or 0.0)
            act_ret = o_close - pred_close if pred_close > 0 else 0.0
            act_dir = 1 if act_ret > 0 else (0 if act_ret == 0 else -1)

            lr_lab = str(fc.get("logreg_label") or "").strip().upper()
            pred_dir_lr = 1 if lr_lab == "UP" else (-1 if lr_lab == "DOWN" else 0)
            logreg_hit = pred_dir_lr != 0 and pred_dir_lr == act_dir

            rf_err_mean = abs(rf_n - o_mean) if rf_n > 0 and o_mean > 0 else None
            xg_err_mean = abs(xg_n - o_mean) if xg_n > 0 and o_mean > 0 else None
            rf_err_close = abs(rf_n - o_close) if rf_n > 0 else None
            xg_err_close = abs(xg_n - o_close) if xg_n > 0 else None

            cons = str(fc.get("consensus") or "").upper()
            pred_dir_cons = 1 if "BULL" in cons else (-1 if "BEAR" in cons else 0)
            cons_hit = pred_dir_cons != 0 and pred_dir_cons == act_dir

            out_row = {
                "schema": "sygnif.btc_forecast_eval_outcome/v1",
                "eval_id": eid,
                "symbol": sym,
                "predicted_at_utc": row.get("predicted_at_utc"),
                "last_candle_utc": last_c,
                "resolved_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "next_bar_date_utc": o_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "actual_next_open": round(o_open, 4),
                "actual_next_high": round(o_high, 4),
                "actual_next_low": round(o_low, 4),
                "actual_next_close": round(o_close, 4),
                "actual_next_mean_hl": round(o_mean, 4),
                "pred_reference_close": round(pred_close, 4),
                "realized_return_close": round(act_ret, 6),
                "realized_direction": act_dir,
                "logreg_direction_hit": logreg_hit,
                "consensus_direction_hit": cons_hit,
                "mae_rf_vs_next_mean": None if rf_err_mean is None else round(rf_err_mean, 4),
                "mae_xgb_vs_next_mean": None if xg_err_mean is None else round(xg_err_mean, 4),
                "mae_rf_vs_next_close": None if rf_err_close is None else round(rf_err_close, 4),
                "mae_xgb_vs_next_close": None if xg_err_close is None else round(xg_err_close, 4),
            }
            out_p.parent.mkdir(parents=True, exist_ok=True)
            with out_p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(out_row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
            done.add(eid)
            processed += 1
        except Exception:
            errors += 1
            continue

    return {"processed": processed, "skipped": skipped, "errors": errors}


def aggregate_report() -> dict[str, Any]:
    """Summarise resolved outcomes JSONL (single streaming pass, bounded memory)."""
    path = outcomes_path()
    if not path.is_file():
        return {"n": 0, "error": "no_outcomes_file"}

    n = 0
    lr_hit = 0
    lr_n = 0
    cons_hit = 0
    cons_n = 0
    mae_rf_sum = 0.0
    mae_rf_cnt = 0
    mae_xg_sum = 0.0
    mae_xg_cnt = 0

    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(o, dict) or o.get("schema") != "sygnif.btc_forecast_eval_outcome/v1":
                    continue
                n += 1
                v = o.get("logreg_direction_hit")
                if isinstance(v, bool):
                    lr_n += 1
                    if v:
                        lr_hit += 1
                v2 = o.get("consensus_direction_hit")
                if isinstance(v2, bool):
                    cons_n += 1
                    if v2:
                        cons_hit += 1
                mrf = o.get("mae_rf_vs_next_mean")
                if mrf is not None:
                    try:
                        mae_rf_sum += float(mrf)
                        mae_rf_cnt += 1
                    except (TypeError, ValueError):
                        pass
                mxg = o.get("mae_xgb_vs_next_mean")
                if mxg is not None:
                    try:
                        mae_xg_sum += float(mxg)
                        mae_xg_cnt += 1
                    except (TypeError, ValueError):
                        pass
    except OSError:
        return {"n": 0, "error": "read_error"}

    if n == 0:
        return {"n": 0}

    def frac(num: int, den: int) -> float | None:
        return float(num / den) if den else None

    return {
        "n": n,
        "logreg_direction_accuracy": frac(lr_hit, lr_n),
        "consensus_direction_accuracy": frac(cons_hit, cons_n),
        "mean_mae_rf_vs_next_mean": (mae_rf_sum / mae_rf_cnt) if mae_rf_cnt else None,
        "mean_mae_xgb_vs_next_mean": (mae_xg_sum / mae_xg_cnt) if mae_xg_cnt else None,
    }
