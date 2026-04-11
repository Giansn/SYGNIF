#!/usr/bin/env python3
"""
BTC Price Prediction Runner — wired to live Bybit data from btc_specialist/data/.

Models used (all free, local, no API keys):
  1. RandomForest  (sklearn)  — continuous next-bar mean price
  2. XGBoost       (xgboost)  — continuous next-bar mean price
  3. LogisticRegression (sklearn) — binary next-bar direction (+1 up / 0 down)

Data: finance_agent/btc_specialist/data/btc_1h_ohlcv.json  (Bybit 1h candles)
      finance_agent/btc_specialist/data/btc_daily_90d.json  (Bybit daily candles)

Usage:
  python3 btc_predict_runner.py              # defaults to 1h data
  python3 btc_predict_runner.py --timeframe daily
  python3 btc_predict_runner.py --window 10  # look-back window size
"""

import json
import os
import sys
import argparse
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler, MinMaxScaler
from sklearn.metrics import (
    mean_absolute_error, mean_squared_error,
    accuracy_score, precision_score, recall_score, f1_score,
)
import xgboost as xgb

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "../../finance_agent/btc_specialist/data")

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_bybit_ohlcv(path):
    with open(path) as f:
        raw = json.load(f)
    rows = []
    for c in raw:
        rows.append({
            "Date": datetime.fromtimestamp(c["t"] / 1000, tz=timezone.utc),
            "Open": float(c["o"]),
            "High": float(c["h"]),
            "Low": float(c["l"]),
            "Close": float(c["c"]),
            "Volume": float(c["v"]),
        })
    df = pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)
    df["Mean"] = (df["High"] + df["Low"]) / 2
    return df

# ---------------------------------------------------------------------------
# Feature engineering (pure pandas/numpy — no external TA lib needed)
# ---------------------------------------------------------------------------

def add_ta_features(df):
    c = df["Close"]
    h = df["High"]
    l = df["Low"]
    m = df["Mean"]
    v = df["Volume"]

    df["RSI_14"] = compute_rsi(c, 14)
    df["RSI_6"] = compute_rsi(c, 6)
    df["EMA_12"] = c.ewm(span=12, adjust=False).mean()
    df["EMA_26"] = c.ewm(span=26, adjust=False).mean()
    df["MACD"] = df["EMA_12"] - df["EMA_26"]
    df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_hist"] = df["MACD"] - df["MACD_signal"]
    df["BB_mid"] = c.rolling(20).mean()
    df["BB_std"] = c.rolling(20).std()
    df["BB_upper"] = df["BB_mid"] + 2 * df["BB_std"]
    df["BB_lower"] = df["BB_mid"] - 2 * df["BB_std"]
    df["ATR_14"] = compute_atr(h, l, c, 14)
    df["MOM_10"] = c - c.shift(10)
    df["ROC_12"] = (c - c.shift(12)) / c.shift(12) * 100
    df["WILLR_14"] = compute_williams_r(h, l, c, 14)
    df["VOL_SMA_20"] = v.rolling(20).mean()
    df["Close_pct"] = c.pct_change()
    df["Mean_pct"] = m.pct_change()

    df.dropna(inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def compute_rsi(series, period):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def compute_atr(high, low, close, period):
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def compute_williams_r(high, low, close, period):
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    return -100 * (hh - close) / (hh - ll)

# ---------------------------------------------------------------------------
# Sliding-window dataset builder
# ---------------------------------------------------------------------------

def build_windowed_dataset(df, feature_cols, target_col, window_size):
    """
    For each row i, flatten features from [i-window .. i-1] and set target = value at row i.
    """
    X_rows, y_rows, dates = [], [], []
    arr = df[feature_cols].values
    tgt = df[target_col].values
    dt = df["Date"].values

    for i in range(window_size, len(arr)):
        flat = arr[i - window_size:i].reshape(-1)
        X_rows.append(flat)
        y_rows.append(tgt[i])
        dates.append(dt[i])

    return np.array(X_rows), np.array(y_rows, dtype=float), np.array(dates)

# ---------------------------------------------------------------------------
# Model runners
# ---------------------------------------------------------------------------

def run_random_forest(X_train, y_train, X_test, y_test):
    model = RandomForestRegressor(n_estimators=500, random_state=42, n_jobs=-1)
    model.fit(X_train, y_train)
    preds = model.predict(X_test)
    return preds, {
        "MAE": mean_absolute_error(y_test, preds),
        "RMSE": np.sqrt(mean_squared_error(y_test, preds)),
        "MAPE": np.mean(np.abs((y_test - preds) / y_test)) * 100,
        "Direction_Acc": direction_accuracy(y_test, preds),
    }


def run_xgboost(X_train, y_train, X_test, y_test):
    model = xgb.XGBRegressor(
        n_estimators=500, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8, random_state=42,
    )
    model.fit(X_train, y_train, verbose=False)
    preds = model.predict(X_test)
    return preds, {
        "MAE": mean_absolute_error(y_test, preds),
        "RMSE": np.sqrt(mean_squared_error(y_test, preds)),
        "MAPE": np.mean(np.abs((y_test - preds) / y_test)) * 100,
        "Direction_Acc": direction_accuracy(y_test, preds),
    }


def run_direction_classifier(X_train, y_train_raw, X_test, y_test_raw):
    y_train_dir = (np.diff(np.concatenate([[y_train_raw[0]], y_train_raw])) > 0).astype(int)[1:]
    y_test_dir = (np.diff(np.concatenate([[y_test_raw[0]], y_test_raw])) > 0).astype(int)[1:]

    X_tr = X_train[1:]
    X_te = X_test[1:]

    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_te_s = scaler.transform(X_te)

    model = LogisticRegression(solver="liblinear", l1_ratio=1, C=1.0, max_iter=300)
    model.fit(X_tr_s, y_train_dir)
    preds = model.predict(X_te_s)
    proba = model.predict_proba(X_te_s)

    return preds, proba, {
        "Accuracy": accuracy_score(y_test_dir, preds),
        "Precision": precision_score(y_test_dir, preds, zero_division=0),
        "Recall": recall_score(y_test_dir, preds, zero_division=0),
        "F1": f1_score(y_test_dir, preds, zero_division=0),
    }, y_test_dir


def direction_accuracy(actual, predicted):
    act_dir = np.diff(actual) > 0
    pred_dir = np.diff(predicted) > 0
    return np.mean(act_dir == pred_dir) * 100

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_usd(v):
    return f"${v:,.2f}"

def fmt_pct(v):
    return f"{v:.2f}%"

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="BTC Prediction Runner")
    parser.add_argument("--timeframe", choices=["1h", "daily"], default="1h")
    parser.add_argument("--window", type=int, default=5, help="Look-back window size")
    parser.add_argument("--test-ratio", type=float, default=0.2, help="Fraction held out for testing")
    args = parser.parse_args()

    data_file = {
        "1h": os.path.join(DATA_DIR, "btc_1h_ohlcv.json"),
        "daily": os.path.join(DATA_DIR, "btc_daily_90d.json"),
    }[args.timeframe]

    print(f"\n{'='*70}")
    print(f"  BTC Price Prediction — Bybit {args.timeframe} candles")
    print(f"  Window: {args.window} bars  |  Test split: {args.test_ratio*100:.0f}%")
    print(f"{'='*70}\n")

    # Load & feature-engineer
    df = load_bybit_ohlcv(data_file)
    print(f"  Loaded {len(df)} candles  ({df['Date'].iloc[0].strftime('%Y-%m-%d %H:%M')} → {df['Date'].iloc[-1].strftime('%Y-%m-%d %H:%M')})")
    df = add_ta_features(df)
    print(f"  After TA features: {len(df)} rows, {len(df.columns)} columns\n")

    feature_cols = [c for c in df.columns if c not in ("Date", "Mean")]
    target_col = "Mean"

    X, y, dates = build_windowed_dataset(df, feature_cols, target_col, args.window)

    split_idx = int(len(X) * (1 - args.test_ratio))
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]
    dates_test = dates[split_idx:]

    print(f"  Train: {len(X_train)} samples  |  Test: {len(X_test)} samples\n")

    # --- Model 1: RandomForest (price regression) ---
    print(f"{'─'*70}")
    print("  MODEL 1: RandomForest Regressor (next-bar mean price)")
    print(f"{'─'*70}")
    rf_preds, rf_metrics = run_random_forest(X_train, y_train, X_test, y_test)
    for k, v in rf_metrics.items():
        print(f"    {k:20s}  {fmt_usd(v) if 'MAE' in k or 'RMSE' in k else fmt_pct(v)}")

    # --- Model 2: XGBoost (price regression) ---
    print(f"\n{'─'*70}")
    print("  MODEL 2: XGBoost Regressor (next-bar mean price)")
    print(f"{'─'*70}")
    xgb_preds, xgb_metrics = run_xgboost(X_train, y_train, X_test, y_test)
    for k, v in xgb_metrics.items():
        print(f"    {k:20s}  {fmt_usd(v) if 'MAE' in k or 'RMSE' in k else fmt_pct(v)}")

    # --- Model 3: LogisticRegression (direction classifier) ---
    print(f"\n{'─'*70}")
    print("  MODEL 3: Logistic Regression (next-bar direction: UP / DOWN)")
    print(f"{'─'*70}")
    dir_preds, dir_proba, dir_metrics, y_test_dir = run_direction_classifier(X_train, y_train, X_test, y_test)
    for k, v in dir_metrics.items():
        print(f"    {k:20s}  {fmt_pct(v * 100)}")

    # --- Latest prediction (the actionable part) ---
    last_window = X[-1:].copy()
    last_date = pd.Timestamp(dates[-1], tz="UTC")
    last_close = df["Close"].iloc[-1]

    rf_next = rf_preds[-1]
    xgb_next = xgb_preds[-1]

    scaler = StandardScaler()
    scaler.fit(X_train[1:])
    last_scaled = scaler.transform(last_window)
    lr_model = LogisticRegression(solver="liblinear", l1_ratio=1, C=1.0, max_iter=300)
    y_dir_train = (np.diff(np.concatenate([[y_train[0]], y_train])) > 0).astype(int)[1:]
    lr_model.fit(scaler.transform(X_train[1:]), y_dir_train)
    dir_next = lr_model.predict(last_scaled)[0]
    dir_proba_next = lr_model.predict_proba(last_scaled)[0]

    rf_delta = rf_next - last_close
    xgb_delta = xgb_next - last_close

    print(f"\n{'='*70}")
    print(f"  PREDICTIONS (next bar after {last_date.strftime('%Y-%m-%d %H:%M UTC')})")
    print(f"{'='*70}")
    print(f"  Current Close:           {fmt_usd(last_close)}")
    print()
    print(f"  RandomForest predicted:  {fmt_usd(rf_next)}  ({'+' if rf_delta >= 0 else ''}{fmt_usd(rf_delta)})")
    print(f"  XGBoost predicted:       {fmt_usd(xgb_next)}  ({'+' if xgb_delta >= 0 else ''}{fmt_usd(xgb_delta)})")
    print(f"  Direction (LogReg):      {'UP' if dir_next == 1 else 'DOWN'}  (confidence: {max(dir_proba_next)*100:.1f}%)")
    print()

    consensus_up = sum([rf_delta > 0, xgb_delta > 0, dir_next == 1])
    if consensus_up >= 2:
        consensus = "BULLISH"
    elif consensus_up <= 1:
        consensus = "BEARISH"
    else:
        consensus = "MIXED"
    print(f"  Consensus ({consensus_up}/3 up):       {consensus}")
    print(f"{'='*70}\n")

    # Save predictions to JSON
    out = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timeframe": args.timeframe,
        "window_size": args.window,
        "last_candle_utc": last_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "current_close": round(last_close, 2),
        "predictions": {
            "random_forest": {"next_mean": round(rf_next, 2), "delta": round(rf_delta, 2)},
            "xgboost": {"next_mean": round(xgb_next, 2), "delta": round(xgb_delta, 2)},
            "direction_logistic": {
                "label": "UP" if dir_next == 1 else "DOWN",
                "confidence": round(max(dir_proba_next) * 100, 1),
            },
            "consensus": consensus,
        },
        "backtest_metrics": {
            "random_forest": {k: round(v, 4) for k, v in rf_metrics.items()},
            "xgboost": {k: round(v, 4) for k, v in xgb_metrics.items()},
            "direction_logistic": {k: round(v * 100, 2) for k, v in dir_metrics.items()},
        },
    }
    out_path = os.path.join(SCRIPT_DIR, "btc_prediction_output.json")

    def make_serializable(obj):
        if isinstance(obj, (np.floating, np.float32, np.float64)):
            return float(obj)
        if isinstance(obj, (np.integer, np.int32, np.int64)):
            return int(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return obj

    class NumpyEncoder(json.JSONEncoder):
        def default(self, o):
            v = make_serializable(o)
            if v is not o:
                return v
            return super().default(o)

    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, cls=NumpyEncoder)
    print(f"  Results saved → {out_path}\n")


if __name__ == "__main__":
    main()
