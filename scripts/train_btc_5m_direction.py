#!/usr/bin/env python3
"""
Research-only: train a **logistic** model for **next 5m bar direction** (BTC spot).

**Reality check:** 5m moves are mostly noise; reported accuracy is often near a
coin flip. Use for **experiments**, not production alpha.

Does **not** place bets or orders — train / predict / print only.

Uses **NumPy only** (no sklearn required) so it runs in minimal environments.

After indicator compute, merges 1h/4h columns and adds ``btc_trend_regime`` (same rule as
``SYGNIF_PROFILE=btc_trend`` / ``user_data/strategies/btc_trend_regime.py``).

Usage:
  python3 scripts/train_btc_5m_direction.py train --limit 2000
  python3 scripts/train_btc_5m_direction.py train --limit 2000 --regime-filter
  python3 scripts/train_btc_5m_direction.py predict

Optional (better metrics / same model file):
  pip install scikit-learn joblib  # then uses HistGradientBoosting if available
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
MODEL_NPZ = ROOT / "user_data" / "ml_models" / "btc_5m_direction.npz"
MODEL_JSON = ROOT / "user_data" / "ml_models" / "btc_5m_direction_meta.json"


def _load_train_ml_ensemble():
    path = ROOT / "scripts" / "train_ml_ensemble.py"
    spec = importlib.util.spec_from_file_location("_tml", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _label_next_bar_up(df: pd.DataFrame) -> pd.Series:
    nxt = df["close"].shift(-1)
    return (nxt > df["close"]).astype(int)


def _sigmoid(z: np.ndarray) -> np.ndarray:
    z = np.clip(z, -500, 500)
    return 1.0 / (1.0 + np.exp(-z))


def _train_logistic_numpy(X: np.ndarray, y: np.ndarray, epochs: int = 800, lr: float = 0.15) -> np.ndarray:
    """Binary logistic regression with bias; X shape (n, f)."""
    n, f = X.shape
    w = np.zeros(f + 1)
    Xb = np.c_[np.ones(n), X]
    y = y.astype(np.float64).reshape(-1)
    for _ in range(epochs):
        p = _sigmoid(Xb @ w)
        grad = Xb.T @ (p - y) / n
        w -= lr * grad
    return w


def train_main(limit: int = 2000, *, regime_filter: bool = False) -> None:
    tml = _load_train_ml_ensemble()
    feature_cols = list(tml.FEATURE_COLS)

    print(f"Fetching {limit} x 5m bars BTCUSDT ...")
    df = tml._load_ohlcv_bybit("BTCUSDT", limit=limit)
    print("Computing indicators ...")
    df = tml._compute_indicators(df)
    print("Merging 1h/4h + btc_trend_regime (train_ml_ensemble.attach_btc_trend_htf_features) ...")
    df = tml.attach_btc_trend_htf_features(df, symbol="BTCUSDT")
    reg = tml._load_btc_trend_regime_module()
    df["btc_trend_regime"] = reg.btc_trend_long_series(df)
    if regime_filter:
        before = len(df)
        df = df.loc[df["btc_trend_regime"] > 0].copy().reset_index(drop=True)
        print(f"--regime-filter: {before} -> {len(df)} rows (btc_trend_regime only)")
    for c in feature_cols:
        if c not in df.columns:
            df[c] = 0.0
    df["label"] = _label_next_bar_up(df)
    df = df.dropna(subset=["label"]).reset_index(drop=True)
    for c in feature_cols:
        df[c] = df[c].replace([np.inf, -np.inf], np.nan).fillna(0.0)

    if len(df) < 300:
        print(f"ERROR: only {len(df)} clean rows — increase --limit", file=sys.stderr)
        sys.exit(1)

    X_raw = df[feature_cols].astype(np.float64).replace([np.inf, -np.inf], np.nan).fillna(0.0).values
    y = df["label"].values.astype(np.int32)

    mu = X_raw.mean(axis=0)
    sd = X_raw.std(axis=0) + 1e-8
    X = (X_raw - mu) / sd

    split = int(len(X) * 0.75)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    base = float(y_test.mean())
    print(f"Test positive rate (next bar up): {base:.3f} (always-predict-up accuracy ~{base:.3f})")

    # Prefer sklearn ensemble if installed
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.metrics import accuracy_score, classification_report

        clf = HistGradientBoostingClassifier(
            max_depth=4,
            learning_rate=0.06,
            max_iter=150,
            random_state=42,
        )
        clf.fit(X_train, y_train)
        y_pred = clf.predict(X_test)
        acc = accuracy_score(y_test, y_pred)
        print(f"Test accuracy (HGB): {acc:.4f}")
        print(classification_report(y_test, y_pred, target_names=["down_or_flat", "up"], zero_division=0))

        MODEL_NPZ.parent.mkdir(parents=True, exist_ok=True)
        import joblib

        joblib.dump(
            {"kind": "sklearn", "model": clf, "mu": mu, "sd": sd, "feature_cols": feature_cols},
            MODEL_NPZ.with_suffix(".joblib"),
        )
        meta = {"kind": "sklearn", "path": str(MODEL_NPZ.with_suffix(".joblib")), "feature_cols": feature_cols}
        MODEL_JSON.write_text(json.dumps(meta, indent=2) + "\n")
        print(f"Saved: {MODEL_NPZ.with_suffix('.joblib')}")
        return
    except ImportError:
        pass

    w = _train_logistic_numpy(X_train, y_train)
    Xb_test = np.c_[np.ones(len(X_test)), X_test]
    p_test = _sigmoid(Xb_test @ w)
    y_pred = (p_test >= 0.5).astype(int)
    acc = float((y_pred == y_test).mean())
    print(f"Test accuracy (logistic numpy): {acc:.4f}")

    MODEL_NPZ.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        MODEL_NPZ,
        w=w,
        mu=mu,
        sd=sd,
        feature_cols=np.array(feature_cols, dtype=object),
    )
    meta = {"kind": "numpy_logistic", "path": str(MODEL_NPZ), "feature_cols": feature_cols}
    MODEL_JSON.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"Saved: {MODEL_NPZ}")


def predict_main() -> None:
    tml = _load_train_ml_ensemble()
    feature_cols = list(tml.FEATURE_COLS)

    joblib_path = MODEL_NPZ.with_suffix(".joblib")
    if joblib_path.is_file():
        import joblib

        bundle = joblib.load(joblib_path)
        clf = bundle["model"]
        mu = bundle["mu"]
        sd = bundle["sd"]
        feature_cols = bundle["feature_cols"]
        df = tml._load_ohlcv_bybit("BTCUSDT", limit=500)
        df = tml._compute_indicators(df)
        df = tml.attach_btc_trend_htf_features(df, symbol="BTCUSDT")
        reg = tml._load_btc_trend_regime_module()
        df["btc_trend_regime"] = reg.btc_trend_long_series(df)
        for c in feature_cols:
            if c not in df.columns:
                df[c] = 0.0
        for c in feature_cols:
            df[c] = df[c].replace([np.inf, -np.inf], np.nan).fillna(0.0)
        row = df.iloc[-1]
        Xr = row[feature_cols].astype(np.float64).fillna(0.0).values.reshape(1, -1)
        X = (Xr - mu) / (sd + 1e-8)
        proba = clf.predict_proba(X)[0]
        p_up = float(proba[1]) if len(proba) > 1 else float(proba[0])
    elif MODEL_NPZ.is_file():
        data = np.load(MODEL_NPZ, allow_pickle=True)
        w = data["w"]
        mu = data["mu"]
        sd = data["sd"]
        cols = list(data["feature_cols"].tolist())
        df = tml._load_ohlcv_bybit("BTCUSDT", limit=500)
        df = tml._compute_indicators(df)
        df = tml.attach_btc_trend_htf_features(df, symbol="BTCUSDT")
        reg = tml._load_btc_trend_regime_module()
        df["btc_trend_regime"] = reg.btc_trend_long_series(df)
        for c in cols:
            if c not in df.columns:
                df[c] = 0.0
        for c in cols:
            df[c] = df[c].replace([np.inf, -np.inf], np.nan).fillna(0.0)
        df = df.dropna(how="all").reset_index(drop=True)
        if len(df) < 1:
            print("ERROR: insufficient rows", file=sys.stderr)
            sys.exit(1)
        row = df.iloc[-1]
        Xr = row[cols].astype(np.float64).fillna(0.0).values
        X = (Xr - mu) / (sd + 1e-8)
        Xb = np.concatenate([[1.0], X])
        p_up = float(_sigmoid(np.dot(Xb, w)))
    else:
        print("No trained model — run: python3 scripts/train_btc_5m_direction.py train", file=sys.stderr)
        sys.exit(1)

    print(f"Last bar UTC: {row.get('date', 'n/a')}")
    tr = row.get("btc_trend_regime", float("nan"))
    print(f"btc_trend_regime (1=yes): {float(tr):.0f}")
    print(f"P(next 5m close > current close) ≈ {p_up:.3f}")
    print("(Research only — not a trading signal.)")


def main() -> None:
    parser = argparse.ArgumentParser(description="BTC 5m direction — research model")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_train = sub.add_parser("train", help="Train and save model")
    p_train.add_argument("--limit", type=int, default=2000, help="Number of 5m bars")
    p_train.add_argument(
        "--regime-filter",
        action="store_true",
        help="Train only on bars where btc_trend_regime is true (see btc_trend_regime.py)",
    )
    sub.add_parser("predict", help="P(up) for latest bar")
    args = parser.parse_args()
    if args.cmd == "train":
        train_main(limit=args.limit, regime_filter=args.regime_filter)
    else:
        predict_main()


if __name__ == "__main__":
    main()
