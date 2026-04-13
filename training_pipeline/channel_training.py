#!/usr/bin/env python3
"""
Sygnif training channel: data inflow → recognition (probabilities) → risk outflow JSON.

Designed for Docker (see docker-compose.training-pipeline.yml). After optional
`btc_predict_runner.py`, reads OHLCV + prediction JSON and writes
`training_channel_output.json` with channel metadata, calibrated-style probs,
and conservative risk stats (not live trading advice).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# prediction_agent on PYTHONPATH (Docker: /app/prediction_agent)
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
PA = Path(os.environ.get("PREDICTION_AGENT_DIR", REPO_ROOT / "prediction_agent"))
sys.path.insert(0, str(PA))

from btc_predict_runner import (  # noqa: E402
    DATA_DIR,
    add_ta_features,
    build_windowed_dataset,
    load_bybit_ohlcv,
)
from rule_tag_journal import append_channel_training_event  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def channel_inflow_report() -> list[dict]:
    out = []
    for name, rel in (
        ("bybit_1h", "btc_1h_ohlcv.json"),
        ("bybit_daily", "btc_daily_90d.json"),
        ("nautilus_spot_btc_bundle", "nautilus_spot_btc_market_bundle.json"),
    ):
        p = Path(DATA_DIR) / rel
        if not p.exists():
            out.append({"name": name, "path": str(p), "status": "missing", "rows": 0})
            continue
        with open(p) as f:
            raw = json.load(f)
        last_ts = None
        if isinstance(raw, list):
            nrows = len(raw)
            if raw:
                last_ts = raw[-1].get("t")
        elif isinstance(raw, dict) and name == "nautilus_spot_btc_bundle":
            # Nautilus sink: summary object, not a candle list
            b1h = raw.get("bars_1h") or {}
            nrows = int(b1h.get("count") or 0)
            last_ts = b1h.get("last_t")
        else:
            nrows = len(raw) if hasattr(raw, "__len__") else 0
        out.append(
            {
                "name": name,
                "path": str(p),
                "status": "ok",
                "rows": nrows,
                "last_candle_ms": last_ts,
            }
        )
    return out


def simple_returns(close: pd.Series) -> pd.Series:
    return close.pct_change().dropna()


def max_drawdown(eq: np.ndarray) -> float:
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    return float(np.min(dd)) * 100 if len(dd) else 0.0


def run_subprocess_predict_runner() -> bool:
    exe = Path(os.environ.get("BTC_PREDICT_RUNNER", PA / "btc_predict_runner.py"))
    if os.environ.get("SKIP_PREDICT_RUNNER", "").lower() in ("1", "true", "yes"):
        return False
    cmd = [sys.executable, str(exe), "--timeframe", os.environ.get("RUNNER_TIMEFRAME", "1h")]
    print("[channel] running:", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=str(PA))
    if r.returncode != 0:
        print("[channel] btc_predict_runner failed:", r.returncode, flush=True)
    return r.returncode == 0


def main() -> None:
    run_subprocess_predict_runner()

    tf = os.environ.get("RUNNER_TIMEFRAME", "1h")
    data_file = (Path(DATA_DIR) / ("btc_1h_ohlcv.json" if tf == "1h" else "btc_daily_90d.json")).resolve()
    window = int(os.environ.get("WINDOW", "5"))
    test_ratio = float(os.environ.get("TEST_RATIO", "0.2"))

    df = load_bybit_ohlcv(str(data_file))
    df = add_ta_features(df)
    feature_cols = [c for c in df.columns if c not in ("Date", "Mean")]
    X, y, _dates = build_windowed_dataset(df, feature_cols, "Mean", window)
    split_idx = int(len(X) * (1 - test_ratio))
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    y_train_dir = (np.diff(np.concatenate([[y_train[0]], y_train])) > 0).astype(int)[1:]
    y_test_dir = (np.diff(np.concatenate([[y_test[0]], y_test])) > 0).astype(int)[1:]
    X_tr, X_te = X_train[1:], X_test[1:]

    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_te_s = scaler.transform(X_te)

    clf = LogisticRegression(solver="liblinear", max_iter=400, random_state=42)
    clf.fit(X_tr_s, y_train_dir)
    proba = clf.predict_proba(X_te_s)
    # class order 0=down, 1=up
    p_up = float(proba[:, 1][-1])
    p_down = float(proba[:, 0][-1])
    brier = float(brier_score_loss(y_test_dir, proba[:, 1]))

    # empirical win rate when model says UP vs actual next bar
    pred = (proba[:, 1] >= 0.5).astype(int)
    when_up = pred == 1
    win_if_trade_up = float(np.mean(y_test_dir[when_up] == 1)) if np.any(when_up) else None
    loss_if_trade_up = float(np.mean(y_test_dir[when_up] == 0)) if np.any(when_up) else None

    r_all = df["Close"].pct_change().dropna().values * 100
    var_95 = float(np.percentile(r_all, 5)) if len(r_all) > 10 else None
    var_99 = float(np.percentile(r_all, 1)) if len(r_all) > 30 else None

    # Holdout bar returns aligned to predictions (same length as pred / y_test_dir)
    test_close = df["Close"].iloc[-(len(y_test_dir) + 1) :].values
    bar_ret = np.diff(test_close) / test_close[:-1]
    min_len = min(len(pred), len(bar_ret), len(y_test_dir))
    pred_aligned = pred[-min_len:]
    bar_ret = bar_ret[-min_len:]
    strat_ret = np.where(pred_aligned == 1, bar_ret, 0.0)
    eq = np.cumprod(1.0 + strat_ret)
    mdd = max_drawdown(eq)

    pred_path = PA / "btc_prediction_output.json"
    runner_block: dict = {}
    if pred_path.exists():
        with open(pred_path, encoding="utf-8") as f:
            runner_block = json.load(f)

    channel_completed_utc = _utc_now()
    runner_utc = runner_block.get("generated_utc") if isinstance(runner_block.get("generated_utc"), str) else None
    # Single proof boundary: top-level generated_utc matches runner file when that snapshot is embedded (§8).
    generated_utc = runner_utc or channel_completed_utc
    runner_snapshot = dict(runner_block) if runner_block else {}
    alignment = {
        "aligned_to_runner_generated_utc": bool(runner_utc),
        "runner_generated_utc": runner_utc,
        "channel_completed_utc": channel_completed_utc,
        "runner_source_path": str(pred_path) if pred_path.exists() else None,
    }
    ruleprediction_briefing = {
        "r01": "L0: next-bar / runner extremes in training JSON are timing context — not sole basis to widen long risk; re-run channel if stale.",
        "r02": "Governance: HTF regime + dump script + btc_trend_long_row before trusting LTF pine-style overlays; R03 sleeve stays subordinate.",
    }

    out = {
        "generated_utc": generated_utc,
        "channel_completed_utc": channel_completed_utc,
        "predict_runner_alignment": alignment,
        "ruleprediction_briefing": ruleprediction_briefing,
        "inflow": {
            "channels": channel_inflow_report(),
            "timeframe": tf,
            "window": window,
            "test_ratio": test_ratio,
        },
        "recognition": {
            "model": "sklearn_logistic_next_bar_direction",
            "holdout_brier_score": round(brier, 4),
            "last_bar_probability_up_pct": round(p_up * 100, 2),
            "last_bar_probability_down_pct": round(p_down * 100, 2),
            "holdout_when_predicted_up_empirical_win_rate": (
                round(win_if_trade_up * 100, 2) if win_if_trade_up is not None else None
            ),
            "holdout_when_predicted_up_empirical_loss_rate": (
                round(loss_if_trade_up * 100, 2) if loss_if_trade_up is not None else None
            ),
            "btc_predict_runner_snapshot": runner_snapshot,
        },
        "risk_assessment": {
            "disclaimer": "Educational / research only. No fees, slippage, or funding in naive stats; regime shift breaks backtests.",
            "historical_1bar_return_var_95_pct": var_95,
            "historical_1bar_return_var_99_pct": var_99,
            "naive_long_if_model_up_max_drawdown_pct": round(mdd, 2),
            "naive_strategy_note": "Follow-holdout-predictions on close-to-close returns; not SygnifStrategy execution.",
        },
    }

    out_path = Path(os.environ.get("TRAINING_CHANNEL_OUT", PA / "training_channel_output.json"))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"[channel] wrote {out_path}", flush=True)
    try:
        append_channel_training_event(out_path, out, alignment=alignment)
    except Exception as exc:  # noqa: BLE001
        print(f"[channel] rule_tag_journal: {exc}", flush=True)


if __name__ == "__main__":
    main()
