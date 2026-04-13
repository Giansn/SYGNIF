#!/usr/bin/env python3
"""
In-process **live** retrain + predict for the Nautilus bar node (same feature stack as
``btc_predict_runner``, smaller estimators for sub-second to low-second fits on a sliding window).

- Seeds history from Bybit **public** linear 5m klines (no API keys).
- Each bar close: append OHLCV, ``add_ta_features``, windowed X/y, light RF + XGB + LogReg,
  then consensus + ``nautilus_enhanced_consensus`` (reads sidecar/bundle JSON from ``data_dir``).
- Optionally writes ``btc_prediction_output.json`` for dashboards (same shape as runner).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from datetime import timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.preprocessing import StandardScaler
import xgboost as xgb

# Reuse feature pipeline + consensus helpers from the batch runner
from btc_predict_runner import add_ta_features
from btc_predict_runner import build_windowed_dataset
from btc_predict_runner import load_nautilus_research_hints
from btc_predict_runner import nautilus_enhanced_consensus

BYBIT_KLINE = "https://api.bybit.com/v5/market/kline"


def fetch_linear_5m_klines(
    symbol: str = "BTCUSDT",
    limit: int = 800,
    *,
    timeout_sec: float = 20.0,
) -> pd.DataFrame:
    """Public Bybit v5 klines (linear perpetual). ``limit`` max 1000."""
    limit = max(10, min(int(limit), 1000))
    q = urllib.parse.urlencode(
        {"category": "linear", "symbol": symbol, "interval": "5", "limit": str(limit)}
    )
    url = f"{BYBIT_KLINE}?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": "sygnif-btc-live-predict/1"})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        raw = json.loads(resp.read().decode())
    lst = (raw.get("result") or {}).get("list") or []
    if not lst:
        raise RuntimeError("Bybit kline: empty list")
    rows = []
    for c in lst:
        # [startTime, open, high, low, close, volume, turnover]
        rows.append(
            {
                "Date": datetime.fromtimestamp(int(c[0]) / 1000.0, tz=timezone.utc),
                "Open": float(c[1]),
                "High": float(c[2]),
                "Low": float(c[3]),
                "Close": float(c[4]),
                "Volume": float(c[5]),
            }
        )
    df = pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)
    df["Mean"] = (df["High"] + df["Low"]) / 2.0
    return df


def _direction_accuracy(actual: np.ndarray, predicted: np.ndarray) -> float:
    act_dir = np.diff(actual) > 0
    pred_dir = np.diff(predicted) > 0
    return float(np.mean(act_dir == pred_dir) * 100.0)


def _run_rf_light(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    *,
    n_estimators: int,
) -> tuple[np.ndarray, dict]:
    model = RandomForestRegressor(
        n_estimators=max(10, int(n_estimators)),
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)
    preds = model.predict(X_test)
    return preds, {
        "MAE": float(mean_absolute_error(y_test, preds)),
        "RMSE": float(np.sqrt(mean_squared_error(y_test, preds))),
        "Direction_Acc": _direction_accuracy(y_test, preds),
    }


def _run_xgb_light(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    *,
    n_estimators: int,
) -> tuple[np.ndarray, dict]:
    model = xgb.XGBRegressor(
        n_estimators=max(20, int(n_estimators)),
        max_depth=4,
        learning_rate=0.08,
        subsample=0.85,
        colsample_bytree=0.85,
        random_state=42,
    )
    model.fit(X_train, y_train, verbose=False)
    preds = model.predict(X_test)
    return preds, {
        "MAE": float(mean_absolute_error(y_test, preds)),
        "RMSE": float(np.sqrt(mean_squared_error(y_test, preds))),
        "Direction_Acc": _direction_accuracy(y_test, preds),
    }


def _make_lr(C: float = 1.0) -> LogisticRegression:
    return LogisticRegression(
        solver="saga",
        penalty="elasticnet",
        l1_ratio=1.0,
        C=float(C),
        max_iter=2000,
        random_state=42,
    )


def fit_predict_live(
    df: pd.DataFrame,
    *,
    window: int,
    data_dir: str,
    rf_trees: int = 64,
    xgb_estimators: int = 120,
    dir_C: float = 1.0,
    test_ratio: float = 0.12,
    write_json_path: str | None = None,
) -> tuple[bool, str, dict]:
    """
    Train on a time split, then score the **last** window row (same logic as batch runner tail).

    Returns:
        (allow_buy for consensus_nautilus_enhanced BULLISH/STRONG_BULLISH, enhanced label, full output dict)
    """
    window = max(3, int(window))
    df = df.copy()
    df = add_ta_features(df)
    if len(df) < window + 50:
        raise ValueError(f"Too few rows after TA: {len(df)}")

    feature_cols = [c for c in df.columns if c not in ("Date", "Mean")]
    target_col = "Mean"
    X, y, dates = build_windowed_dataset(df, feature_cols, target_col, window)
    if len(X) < 30:
        raise ValueError(f"Too few windowed samples: {len(X)}")

    split_idx = max(window + 5, int(len(X) * (1.0 - float(test_ratio))))
    split_idx = min(split_idx, len(X) - 1)
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    rf_preds, rf_metrics = _run_rf_light(
        X_train, y_train, X_test, y_test, n_estimators=rf_trees
    )
    xgb_preds, xgb_metrics = _run_xgb_light(
        X_train, y_train, X_test, y_test, n_estimators=xgb_estimators
    )

    y_train_dir = (np.diff(np.concatenate([[y_train[0]], y_train])) > 0).astype(int)[1:]
    y_test_dir = (np.diff(np.concatenate([[y_test[0]], y_test])) > 0).astype(int)[1:]
    X_tr_lr, X_te_lr = X_train[1:], X_test[1:]
    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr_lr)
    X_te_s = scaler.transform(X_te_lr)
    lr = _make_lr(C=dir_C)
    lr.fit(X_tr_s, y_train_dir)
    dir_preds = lr.predict(X_te_s)
    dir_proba = lr.predict_proba(X_te_s)

    last_window = X[-1:].copy()
    last_date = pd.Timestamp(dates[-1], tz="UTC")
    last_close = float(df["Close"].iloc[-1])

    rf_model = RandomForestRegressor(
        n_estimators=max(10, int(rf_trees)), random_state=42, n_jobs=-1
    )
    rf_model.fit(X_train, y_train)
    rf_next = float(rf_model.predict(last_window)[0])

    xgb_model = xgb.XGBRegressor(
        n_estimators=max(20, int(xgb_estimators)),
        max_depth=4,
        learning_rate=0.08,
        subsample=0.85,
        colsample_bytree=0.85,
        random_state=42,
    )
    xgb_model.fit(X_train, y_train, verbose=False)
    xgb_next = float(xgb_model.predict(last_window)[0])

    X_tr_tail = X_train[1:]
    scaler2 = StandardScaler()
    scaler2.fit(X_tr_tail)
    last_scaled = scaler2.transform(last_window)
    y_dir_train = (np.diff(np.concatenate([[y_train[0]], y_train])) > 0).astype(int)[1:]
    lr_final = _make_lr(C=dir_C)
    lr_final.fit(scaler2.transform(X_tr_tail), y_dir_train)
    dir_next = int(lr_final.predict(last_scaled)[0])
    dir_proba_next = lr_final.predict_proba(last_scaled)[0]

    rf_delta = rf_next - last_close
    xgb_delta = xgb_next - last_close
    logreg_up = dir_next == 1
    consensus_up = sum([rf_delta > 0, xgb_delta > 0, logreg_up])
    if consensus_up >= 2:
        consensus = "BULLISH"
    elif consensus_up <= 1:
        consensus = "BEARISH"
    else:
        consensus = "MIXED"

    nautilus_hints = load_nautilus_research_hints(data_dir)
    enhanced, n_consensus_meta = nautilus_enhanced_consensus(
        consensus, consensus_up, logreg_up, nautilus_hints
    )

    allow = enhanced.upper().strip() in ("BULLISH", "STRONG_BULLISH")

    out = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timeframe": "5m_live",
        "window_size": window,
        "model_options": {
            "dir_C": dir_C,
            "calibrate": False,
            "regime_features": True,
            "live_rf_trees": rf_trees,
            "live_xgb_estimators": xgb_estimators,
        },
        "nautilus_research": nautilus_hints,
        "nautilus_consensus_meta": n_consensus_meta,
        "last_candle_utc": last_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "current_close": round(last_close, 2),
        "predictions": {
            "random_forest": {"next_mean": round(rf_next, 2), "delta": round(rf_delta, 2)},
            "xgboost": {"next_mean": round(xgb_next, 2), "delta": round(xgb_delta, 2)},
            "direction_logistic": {
                "label": "UP" if dir_next == 1 else "DOWN",
                "confidence": round(float(max(dir_proba_next)) * 100.0, 1),
            },
            "consensus": consensus,
            "consensus_nautilus_enhanced": enhanced,
        },
        "backtest_metrics": {
            "random_forest": {k: round(float(v), 4) for k, v in rf_metrics.items()},
            "xgboost": {k: round(float(v), 4) for k, v in xgb_metrics.items()},
            "direction_logistic": {
                "Accuracy": round(float(np.mean(dir_preds == y_test_dir) * 100.0), 2),
            },
        },
    }

    if write_json_path:
        p = Path(write_json_path)
        p.parent.mkdir(parents=True, exist_ok=True)

        def _ser(o):
            if isinstance(o, (np.floating, np.float32, np.float64)):
                return float(o)
            if isinstance(o, (np.integer, np.int32, np.int64)):
                return int(o)
            if isinstance(o, np.ndarray):
                return o.tolist()
            return o

        class _Enc(json.JSONEncoder):
            def default(self, o):
                v = _ser(o)
                if v is not o:
                    return v
                return super().default(o)

        with open(p, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, cls=_Enc)

    return allow, enhanced, out


def append_bar_row(
    df: pd.DataFrame,
    *,
    ts_event_ns: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
) -> pd.DataFrame:
    """Append one closed bar; drop duplicate timestamps (keep last)."""
    ts = datetime.fromtimestamp(ts_event_ns / 1e9, tz=timezone.utc)
    row = {
        "Date": ts,
        "Open": float(open_),
        "High": float(high),
        "Low": float(low),
        "Close": float(close),
        "Volume": float(volume),
        "Mean": (float(high) + float(low)) / 2.0,
    }
    out = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    out = out.drop_duplicates(subset=["Date"], keep="last").sort_values("Date").reset_index(
        drop=True
    )
    return out


def main() -> int:
    """Smoke: fetch + one live fit."""
    try:
        df = fetch_linear_5m_klines(limit=400)
        allow, enhanced, _ = fit_predict_live(
            df, window=5, data_dir=os.environ.get("NAUTILUS_BTC_OHLCV_DIR", "."), write_json_path=None
        )
        print(f"live ok allow_buy={allow} enhanced={enhanced} rows={len(df)}")
        return 0
    except (urllib.error.URLError, OSError, ValueError, RuntimeError) as e:
        print(f"live smoke failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
