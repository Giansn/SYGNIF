#!/usr/bin/env python3
"""
Train a lightweight **next-day BTC direction** model using:
  - **BTC behaviour:** TA features from `btc_predict_runner.add_ta_features` (Yahoo OHLCV).
  - **Fundamental / macro proxies (not company fundamentals):** SPY, VIX, TLT, GLD daily
    levels and returns — overlap with BTC only from first Yahoo BTC day (~2014).

Input: `btc_macro_yfinance_daily.json` from `pull_btc_macro_history.py`.
Output: `btc_macro_train_output.json` (metrics + sparse coef snapshot). Research only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss
from sklearn.preprocessing import StandardScaler

REPO_ROOT = Path(__file__).resolve().parents[1]
PA = Path(os.environ.get("PREDICTION_AGENT_DIR", REPO_ROOT / "prediction_agent"))
sys.path.insert(0, str(PA))

from btc_predict_runner import add_ta_features  # noqa: E402

DEFAULT_IN = REPO_ROOT / "finance_agent" / "btc_specialist" / "data" / "btc_macro_yfinance_daily.json"
DEFAULT_OUT = PA / "btc_macro_train_output.json"

# Macro columns merged into the TA frame (ETF / index **proxies**, not earnings statements).
MACRO_EXTRA = (
    "spy_ret_1",
    "spy_ret_5",
    "vix_lvl",
    "vix_z_20",
    "tlt_ret_5",
    "gld_ret_5",
    "btc_spy_roll_corr_20",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_daily_json(path: Path) -> pd.DataFrame:
    with open(path, encoding="utf-8") as f:
        blob = json.load(f)
    rows = blob.get("daily") or []
    if not rows:
        raise SystemExit(f"No daily[] in {path}")
    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    df = df.sort_values("Date").reset_index(drop=True)
    return df


def prepare_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str], np.ndarray]:
    """Build OHLCV + macro engineering, then TA; return rows ready for sklearn."""
    if "btc_close" not in df.columns:
        raise SystemExit("Missing column btc_close")

    df = df.dropna(subset=["btc_close"]).reset_index(drop=True)
    if df.empty:
        raise SystemExit("No rows with non-null btc_close (run pull_btc_macro_history.py first).")

    # Equities do not print Sat/Sun rows; crypto does — forward-fill last US close onto BTC weekends.
    for c in ("spy_close", "vix_close", "tlt_close", "gld_close"):
        if c in df.columns:
            df[c] = df[c].ffill()

    work = pd.DataFrame({"Date": df["Date"]})
    if "btc_open" in df.columns and df["btc_open"].notna().any():
        work["Open"] = df["btc_open"]
        work["High"] = df["btc_high"]
        work["Low"] = df["btc_low"]
        work["Close"] = df["btc_close"]
        work["Volume"] = df["btc_volume"].fillna(0.0) if "btc_volume" in df.columns else 0.0
    else:
        work["Close"] = df["btc_close"]
        work["Open"] = work["Close"].shift(1).fillna(work["Close"])
        work["High"] = work["Close"]
        work["Low"] = work["Close"]
        work["Volume"] = 0.0

    work["Mean"] = (work["High"] + work["Low"]) / 2.0

    # TA path drops NaN on **all** columns — keep OHLCV+Mean only for `add_ta_features`,
    # then merge macro/fundamental **proxies** (must not be passed into add_ta_features).
    ohlc = work[["Date", "Open", "High", "Low", "Close", "Volume", "Mean"]].copy()
    ta = add_ta_features(ohlc)

    macro = pd.DataFrame({"Date": df["Date"]})
    for col in ("spy_close", "vix_close", "tlt_close", "gld_close"):
        macro[col] = df[col] if col in df.columns else np.nan
    macro["spy_ret_1"] = macro["spy_close"].pct_change()
    macro["spy_ret_5"] = macro["spy_close"].pct_change(5)
    macro["vix_lvl"] = macro["vix_close"]
    vix_mu = macro["vix_close"].rolling(20).mean()
    vix_sd = macro["vix_close"].rolling(20).std()
    macro["vix_z_20"] = (macro["vix_close"] - vix_mu) / vix_sd.replace(0, np.nan)
    macro["tlt_ret_5"] = macro["tlt_close"].pct_change(5)
    macro["gld_ret_5"] = macro["gld_close"].pct_change(5)
    br = df["btc_close"].pct_change()
    sr = macro["spy_close"].pct_change()
    macro["btc_spy_roll_corr_20"] = br.rolling(20).corr(sr)

    macro_keep = macro[["Date"] + list(MACRO_EXTRA)]
    work = ta.merge(macro_keep, on="Date", how="inner")

    work["y_dir"] = (work["Close"].shift(-1) > work["Close"]).astype(int)
    work = work.iloc[:-1].copy()

    exclude = {
        "Date",
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
        "Mean",
        "y_dir",
        "spy_close",
        "vix_close",
        "tlt_close",
        "gld_close",
    }
    ta_cols = [c for c in work.columns if c not in exclude and c not in MACRO_EXTRA]
    feature_cols = ta_cols + list(MACRO_EXTRA)

    work = work.dropna(subset=feature_cols + ["y_dir"]).reset_index(drop=True)
    y = work["y_dir"].to_numpy(dtype=int)
    return work, feature_cols, y


def main() -> None:
    ap = argparse.ArgumentParser(description="Train BTC direction + macro proxy model.")
    ap.add_argument("--in", dest="in_path", default=str(DEFAULT_IN), help="btc_macro_yfinance_daily.json")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="Output JSON path.")
    ap.add_argument("--test-ratio", type=float, default=0.2, help="Holdout tail fraction.")
    args = ap.parse_args()

    df = load_daily_json(Path(args.in_path))
    work, feature_cols, y = prepare_frame(df)
    if len(work) < 200:
        raise SystemExit(f"Too few rows after cleaning: {len(work)}")

    split = int(len(work) * (1 - args.test_ratio))
    X_train = work.loc[: split - 1, feature_cols].to_numpy()
    y_train = y[:split]
    X_test = work.loc[split:, feature_cols].to_numpy()
    y_test = y[split:]

    scaler = StandardScaler()
    X_tr = scaler.fit_transform(X_train)
    X_te = scaler.transform(X_test)

    clf = LogisticRegression(max_iter=800, random_state=42, solver="liblinear")
    clf.fit(X_tr, y_train)
    proba = clf.predict_proba(X_te)[:, 1]
    pred = (proba >= 0.5).astype(int)

    coef_pairs = sorted(
        zip(feature_cols, clf.coef_.ravel()),
        key=lambda t: abs(t[1]),
        reverse=True,
    )[:24]

    out = {
        "generated_utc": _utc_now(),
        "input": str(Path(args.in_path).resolve()),
        "rows_total": int(len(work)),
        "holdout_rows": int(len(y_test)),
        "test_ratio": args.test_ratio,
        "model": "sklearn_logistic_liblinear_next_day_direction",
        "features": {
            "ta_columns_count": len([c for c in feature_cols if c not in MACRO_EXTRA]),
            "macro_proxy_columns": list(MACRO_EXTRA),
        },
        "metrics": {
            "holdout_accuracy": round(float(accuracy_score(y_test, pred)), 4),
            "holdout_brier": round(float(brier_score_loss(y_test, proba)), 4),
            "last_holdout_p_up_pct": round(float(proba[-1] * 100), 2),
        },
        "top_coef": [{"feature": n, "coef": round(float(v), 6)} for n, v in coef_pairs],
        "disclaimer": (
            "Macro columns are ETF/index proxies (risk-on/off, vol), not token on-chain fundamentals; "
            "Yahoo BTC is not Bybit execution; no live trading claim."
        ),
    }

    out_path = Path(os.environ.get("BTC_MACRO_TRAIN_OUT", args.out))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"[macro_train] wrote {out_path}", flush=True)


if __name__ == "__main__":
    main()
