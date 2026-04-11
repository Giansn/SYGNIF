#!/usr/bin/env python3
"""
Train XGBoost signal-ensemble model for Sygnif.

Usage:
    python3 scripts/train_ml_ensemble.py [--db user_data/tradesv3.sqlite] [--pairs BTC/USDT ETH/USDT]

Reads OHLCV from Bybit (5m), computes indicators, optionally merges **1h/4h** columns
via ``attach_btc_trend_htf_features`` and adds ``btc_trend_regime`` (see
``user_data/strategies/btc_trend_regime.py`` — same rule as ``SYGNIF_PROFILE=btc_trend``).

Labels bars as profitable_long = 1 if close[+12] > close (≈ 1h lookahead on 5m), then trains
XGBoost and saves to ``user_data/ml_models/xgb_signal_ensemble.json``.

Optional: ``--btc-trend-regime-only`` keeps rows where the trend regime is true (ablation).

Requirements (not in Docker by default):
    pip install xgboost scikit-learn
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).resolve().parent.parent / "user_data" / "ml_models"
MODEL_PATH = MODEL_DIR / "xgb_signal_ensemble.json"

FEATURE_COLS = [
    "RSI_14", "RSI_3", "ADX_14", "DMP_14", "DMN_14",
    "WILLR_14", "CMF_20", "STOCHRSIk_14_14_3_3",
    "BBP_20_2.0", "AROONU_14", "AROOND_14",
    "change_pct", "ATR_14",
    "cdl_net_bullish",
]

LOOKAHEAD_BARS = 12  # 12 × 5m = 1h


def _load_ohlcv_bybit(symbol: str = "BTCUSDT", interval: str = "5", limit: int = 1000) -> pd.DataFrame:
    """Fetch OHLCV from Bybit v5 public API (no auth needed)."""
    import requests
    url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={symbol}&interval={interval}&limit={limit}"
    resp = requests.get(url, timeout=15)
    data = resp.json().get("result", {}).get("list", [])
    if not data:
        raise ValueError(f"No data from Bybit for {symbol}")
    rows = []
    for r in data:
        rows.append({
            "date": pd.Timestamp(int(r[0]), unit="ms", tz="UTC"),
            "open": float(r[1]), "high": float(r[2]),
            "low": float(r[3]), "close": float(r[4]),
            "volume": float(r[5]),
        })
    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return df


def _load_btc_trend_regime_module():
    """Load ``user_data/strategies/btc_trend_regime.py`` without package install."""
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "user_data" / "strategies" / "btc_trend_regime.py"
    spec = importlib.util.spec_from_file_location("btc_trend_regime_ml", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def attach_btc_trend_htf_features(df: pd.DataFrame, symbol: str = "BTCUSDT") -> pd.DataFrame:
    """
    Merge 1h/4h RSI and 1h EMA200 onto 5m bars (same semantics as ``merge_informative_pair``).

    Uses ``merge_asof`` backward so each 5m row sees the last completed higher-TF values.
    """
    import pandas_ta as pta

    df = df.sort_values("date").reset_index(drop=True)
    h1 = _load_ohlcv_bybit(symbol, "60", limit=800)
    h1 = h1.sort_values("date")
    h1["RSI_14"] = pta.rsi(h1["close"], length=14)
    h1["EMA_200"] = pta.ema(h1["close"], length=200)
    h4 = _load_ohlcv_bybit(symbol, "240", limit=500)
    h4 = h4.sort_values("date")
    h4["RSI_14"] = pta.rsi(h4["close"], length=14)
    h1s = h1[["date", "RSI_14", "EMA_200"]].rename(
        columns={"RSI_14": "RSI_14_1h", "EMA_200": "EMA_200_1h"}
    )
    h4s = h4[["date", "RSI_14"]].rename(columns={"RSI_14": "RSI_14_4h"})
    out = pd.merge_asof(df, h1s, on="date", direction="backward")
    out = pd.merge_asof(out, h4s, on="date", direction="backward")
    for c in ("RSI_14_1h", "RSI_14_4h"):
        if c in out.columns:
            out[c] = out[c].fillna(50.0)
    return out


def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute the indicator subset needed for ML features."""
    import pandas_ta as pta

    df["RSI_14"] = pta.rsi(df["close"], length=14)
    df["RSI_3"] = pta.rsi(df["close"], length=3)
    adx_df = pta.adx(df["high"], df["low"], df["close"], length=14)
    if isinstance(adx_df, pd.DataFrame):
        for col in adx_df.columns:
            df[col] = adx_df[col]
    df["WILLR_14"] = pta.willr(df["high"], df["low"], df["close"], length=14)
    df["CMF_20"] = pta.cmf(df["high"], df["low"], df["close"], df["volume"], length=20)
    stochrsi = pta.stochrsi(df["close"])
    if isinstance(stochrsi, pd.DataFrame) and "STOCHRSIk_14_14_3_3" in stochrsi.columns:
        df["STOCHRSIk_14_14_3_3"] = stochrsi["STOCHRSIk_14_14_3_3"]
    bbands = pta.bbands(df["close"], length=20)
    if isinstance(bbands, pd.DataFrame) and "BBP_20_2.0" in bbands.columns:
        df["BBP_20_2.0"] = bbands["BBP_20_2.0"]
    aroon = pta.aroon(df["high"], df["low"], length=14)
    if isinstance(aroon, pd.DataFrame):
        df["AROONU_14"] = aroon.get("AROONU_14")
        df["AROOND_14"] = aroon.get("AROOND_14")
    df["ATR_14"] = pta.atr(df["high"], df["low"], df["close"], length=14)
    df["change_pct"] = (df["close"] - df["open"]) / df["open"] * 100.0
    df["volume_sma_25"] = pta.sma(df["volume"], length=25)

    try:
        cdl = pta.cdl_pattern(df["open"], df["high"], df["low"], df["close"], name="all")
        if isinstance(cdl, pd.DataFrame):
            df["cdl_net_bullish"] = (cdl > 0).sum(axis=1) - (cdl < 0).sum(axis=1)
        else:
            df["cdl_net_bullish"] = 0
    except Exception:
        df["cdl_net_bullish"] = 0

    return df


def _label(df: pd.DataFrame, lookahead: int = LOOKAHEAD_BARS) -> pd.Series:
    """1 if close rises within lookahead bars, 0 otherwise."""
    future_max = df["close"].shift(-lookahead).rolling(lookahead).max()
    return (future_max > df["close"]).astype(int)


def train(
    symbol: str = "BTCUSDT",
    limit: int = 1000,
    *,
    btc_trend_regime_only: bool = False,
) -> Path:
    try:
        import xgboost as xgb
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import classification_report
    except ImportError:
        print("ERROR: pip install xgboost scikit-learn", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching {limit} bars for {symbol} ...")
    df = _load_ohlcv_bybit(symbol, limit=limit)
    print(f"Computing indicators on {len(df)} bars ...")
    df = _compute_indicators(df)
    print("Merging 1h/4h features for btc_trend_regime ...")
    df = attach_btc_trend_htf_features(df, symbol=symbol)
    reg = _load_btc_trend_regime_module()
    df["btc_trend_regime"] = reg.btc_trend_long_series(df)
    cov = float(df["btc_trend_regime"].mean()) if len(df) else 0.0
    print(f"btc_trend_regime positive fraction: {cov:.3f}")
    if btc_trend_regime_only:
        before = len(df)
        df = df.loc[df["btc_trend_regime"] > 0].copy()
        print(f"--btc-trend-regime-only: {before} -> {len(df)} rows")

    for c in FEATURE_COLS:
        if c not in df.columns:
            df[c] = 0.0
        df[c] = df[c].replace([np.inf, -np.inf], np.nan).fillna(0.0)

    df["label"] = _label(df)
    df.dropna(subset=FEATURE_COLS + ["label"], inplace=True)
    if len(df) < 100:
        print(f"ERROR: only {len(df)} clean rows — need ≥100", file=sys.stderr)
        sys.exit(1)

    X = df[FEATURE_COLS].fillna(0)
    y = df["label"]

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)

    model = xgb.XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        eval_metric="logloss",
        random_state=42,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

    y_pred = model.predict(X_test)
    print(classification_report(y_test, y_pred, target_names=["down/flat", "up"]))

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model.save_model(str(MODEL_PATH))
    print(f"Model saved to {MODEL_PATH}")
    return MODEL_PATH


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train ML signal ensemble")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument(
        "--btc-trend-regime-only",
        action="store_true",
        help="Train only on bars where btc_trend_regime matches (see btc_trend_regime.py)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    train(symbol=args.symbol, limit=args.limit, btc_trend_regime_only=args.btc_trend_regime_only)
