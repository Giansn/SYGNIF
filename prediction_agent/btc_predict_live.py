#!/usr/bin/env python3
"""
In-process **live** retrain + predict for the Nautilus bar node (same feature stack as
``btc_predict_runner``, smaller estimators for sub-second to low-second fits on a sliding window).

- Seeds history from Bybit **public** linear 5m klines (no API keys).
- **15m context** (optional): ``fetch_linear_15m_klines`` + ``build_15m_chart_context`` for higher-timeframe
  bias and chart HTML (see ``run_live_fit`` in ``btc_asap_predict_core``).
- Each bar close: append OHLCV, ``add_ta_features``, windowed X/y, light RF + XGB + LogReg,
  then consensus + ``nautilus_enhanced_consensus`` (reads sidecar/bundle JSON from ``data_dir``).
- **Hivemind (Truthcoin):** when ``SYGNIF_PREDICT_HIVEMIND_FUSION`` is on (default: same as Swarm Truthcoin
  / hive flags — see ``_predict_hivemind_fusion_enabled``), fetches ``hivemind_explore_snapshot`` and
  ``vote_hivemind_from_explore``, writes ``predictions.hivemind``, and may bump **BULLISH → STRONG_BULLISH**
  when liveness vote is ``+1`` and the tree+logreg stack is already bullish (see ``apply_hivemind_to_enhanced_consensus``).
- Optionally writes ``btc_prediction_output.json`` for dashboards (same shape as runner).
- **Temporal split audit:** set ``SYGNIF_PREDICT_AUDIT_TIME_SPLIT=1`` to embed ``time_split_audit`` in the live JSON
  (chronological train vs holdout index, dates, short note on why this is **not** label lookahead for the final row).
"""

from __future__ import annotations

import json
import os
import sys
import time
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
from btc_predict_runner import apply_hivemind_to_enhanced_consensus
from btc_predict_runner import build_windowed_dataset
from btc_predict_runner import load_nautilus_research_hints
from btc_predict_runner import nautilus_enhanced_consensus
from btc_swing_failure import swing_failure_snapshot

BYBIT_KLINE = "https://api.bybit.com/v5/market/kline"

# Bybit v5 ``interval`` minutes: 1,3,5,15,30,60,120,240,360,720,D,W,M
_BYBIT_INTERVAL_MIN = {"1", "3", "5", "15", "30", "60", "120", "240", "360", "720"}


def fetch_linear_klines(
    symbol: str = "BTCUSDT",
    *,
    interval: str = "5",
    limit: int = 800,
    timeout_sec: float = 20.0,
) -> pd.DataFrame:
    """Public Bybit v5 klines (linear perpetual). ``limit`` max 1000."""
    iv = str(interval or "5").strip()
    if iv not in _BYBIT_INTERVAL_MIN:
        raise ValueError(f"unsupported Bybit kline interval minutes: {interval!r}")
    limit = max(10, min(int(limit), 1000))
    q = urllib.parse.urlencode(
        {"category": "linear", "symbol": symbol, "interval": iv, "limit": str(limit)}
    )
    url = f"{BYBIT_KLINE}?{q}"
    last_err: RuntimeError | None = None
    raw: dict = {}
    for attempt in range(3):
        req = urllib.request.Request(url, headers={"User-Agent": "sygnif-btc-live-predict/1"})
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                raw = json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_err = RuntimeError(f"Bybit kline HTTP/JSON failed: {exc}")
            time.sleep(1.0 + attempt)
            continue
        lst_try = (raw.get("result") or {}).get("list") or []
        if lst_try:
            lst = lst_try
            break
        rc = raw.get("retCode")
        rm = raw.get("retMsg")
        last_err = RuntimeError(f"Bybit kline: empty list (retCode={rc} retMsg={rm!r})")
        time.sleep(1.0 + attempt)
    else:
        raise last_err or RuntimeError("Bybit kline: empty list (unknown)")
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


def fetch_linear_5m_klines(
    symbol: str = "BTCUSDT",
    limit: int = 800,
    *,
    timeout_sec: float = 20.0,
) -> pd.DataFrame:
    """Public Bybit v5 **5m** linear klines (backward-compatible wrapper)."""
    return fetch_linear_klines(symbol, interval="5", limit=limit, timeout_sec=timeout_sec)


def fetch_linear_15m_klines(
    symbol: str = "BTCUSDT",
    limit: int = 200,
    *,
    timeout_sec: float = 20.0,
) -> pd.DataFrame:
    """Public Bybit v5 **15m** linear klines for higher-timeframe context and charts."""
    return fetch_linear_klines(symbol, interval="15", limit=limit, timeout_sec=timeout_sec)


def build_15m_chart_context(
    df15: pd.DataFrame,
    *,
    compare_close_5m: float | None = None,
    chart_tail: int = 64,
) -> dict[str, object]:
    """
    Summarise recent **15m** structure for JSON + HTML chart payloads.

    ``trend_bias`` is ``bull`` / ``bear`` / ``neutral`` from close vs SMA20 (+ SMA50 when available).
    """
    if df15 is None or df15.empty or "Close" not in df15.columns:
        return {"interval": "15", "ok": False, "detail": "empty_or_missing_close"}
    d = df15.sort_values("Date").reset_index(drop=True).copy()
    close = pd.to_numeric(d["Close"], errors="coerce")
    d["sma20"] = close.rolling(20, min_periods=10).mean()
    d["sma50"] = close.rolling(50, min_periods=20).mean()
    last = d.iloc[-1]
    lc = float(last["Close"] or 0.0)
    s20 = float(last["sma20"]) if pd.notna(last["sma20"]) else float("nan")
    s50 = float(last["sma50"]) if pd.notna(last["sma50"]) else float("nan")
    last_dt = last["Date"]
    if hasattr(last_dt, "strftime"):
        last_iso = pd.Timestamp(last_dt).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        last_iso = str(last_dt)

    eps = max(1e-9, lc * 0.0015)  # 0.15% band → neutral chop zone
    bias = "neutral"
    if lc > 0 and s20 == s20:
        above = lc > s20 + eps
        below = lc < s20 - eps
        if s50 == s50:
            if above and s20 >= s50:
                bias = "bull"
            elif below and s20 <= s50:
                bias = "bear"
        elif above:
            bias = "bull"
        elif below:
            bias = "bear"

    ret_4h = None
    if len(d) >= 17:
        prev = float(d["Close"].iloc[-17])
        if prev > 0:
            ret_4h = round((lc - prev) / prev * 100.0, 4)

    hi_24h = lo_24h = None
    if len(d) >= 96:
        tail96 = d.iloc[-96:]
        hi_24h = float(tail96["High"].max())
        lo_24h = float(tail96["Low"].min())

    cmp_pct: float | None = None
    if compare_close_5m is not None and compare_close_5m > 0 and lc > 0:
        cmp_pct = round((compare_close_5m - lc) / lc * 100.0, 4)

    tail = max(10, min(int(chart_tail), 200))
    sub = d.iloc[-tail:][["Date", "Open", "High", "Low", "Close", "sma20"]].copy()
    series: list[dict[str, object]] = []
    for _, r in sub.iterrows():
        ts = r["Date"]
        tss = pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ") if ts is not None else ""
        s20v = float(r["sma20"]) if pd.notna(r["sma20"]) else None
        series.append(
            {
                "t": tss,
                "o": round(float(r["Open"]), 2),
                "h": round(float(r["High"]), 2),
                "l": round(float(r["Low"]), 2),
                "c": round(float(r["Close"]), 2),
                "sma20": None if s20v is None else round(s20v, 2),
            }
        )

    return {
        "interval": "15",
        "ok": True,
        "last_candle_utc": last_iso,
        "bars": int(len(d)),
        "close": round(lc, 2),
        "sma20": None if s20 != s20 else round(s20, 2),
        "sma50": None if s50 != s50 else round(s50, 2),
        "trend_bias": bias,
        "ret_4h_pct": ret_4h,
        "range_24h_high": None if hi_24h is None else round(hi_24h, 2),
        "range_24h_low": None if lo_24h is None else round(lo_24h, 2),
        "close_5m_vs_15m_close_pct": cmp_pct,
        "chart_series": series,
    }


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
    # saga + L1 (same sparsity intent as elasticnet+l1_ratio=1); avoids sklearn 1.8+ penalty=elasticnet warnings
    return LogisticRegression(
        solver="saga",
        penalty="l1",
        l1_ratio=1.0,
        C=float(C),
        max_iter=2000,
        random_state=42,
    )


def _predict_hivemind_fusion_enabled() -> bool:
    raw = os.environ.get("SYGNIF_PREDICT_HIVEMIND_FUSION", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    for k in ("SYGNIF_SWARM_TRUTHCOIN_DC", "SYGNIF_SWARM_HIVEMIND_VOTE"):
        if os.environ.get(k, "").strip().lower() in ("1", "true", "yes", "on"):
            return True
    return (os.environ.get("SYGNIF_SWARM_CORE_ENGINE") or "").strip().lower() == "hivemind"


def _hivemind_snapshot_for_predict() -> tuple[bool, dict, int, str]:
    """
    Load Truthcoin hivemind explore + vote for the live prediction tick.

    Returns ``(import_ok, explore_doc, hm_vote, hm_detail)``. When ``import_ok`` is False, skip fusion.
    """
    try:
        from truthcoin_dc_swarm_bridge import hivemind_explore_snapshot
        from truthcoin_hivemind_swarm_core import vote_hivemind_from_explore
    except ImportError:
        try:
            from finance_agent.truthcoin_dc_swarm_bridge import hivemind_explore_snapshot
            from finance_agent.truthcoin_hivemind_swarm_core import vote_hivemind_from_explore
        except ImportError:
            return False, {}, 0, "finance_agent_truthcoin_import_failed"
    doc = hivemind_explore_snapshot()
    if not isinstance(doc, dict):
        doc = {}
    v, detail = vote_hivemind_from_explore(doc)
    return True, doc, int(v), str(detail)


def _hivemind_explore_brief(doc: dict) -> dict[str, object]:
    if not isinstance(doc, dict):
        return {}
    return {
        "ok": doc.get("ok"),
        "detail": str(doc.get("detail") or "")[:400],
        "slots_voting_n": doc.get("slots_voting_n"),
        "markets_trading_n": doc.get("markets_trading_n"),
        "cli": doc.get("cli"),
    }


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
    linear_symbol: str | None = None,
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

    hive_pred: dict[str, object] = {"fusion_enabled": _predict_hivemind_fusion_enabled()}
    if hive_pred["fusion_enabled"]:
        imp_ok, explore_doc, hm_vote, hm_detail = _hivemind_snapshot_for_predict()
        hive_pred["import_ok"] = imp_ok
        hive_pred["explore"] = _hivemind_explore_brief(explore_doc)
        hive_pred["vote"] = hm_vote
        hive_pred["vote_detail"] = hm_detail[:500]
        if imp_ok:
            enhanced, n_consensus_meta = apply_hivemind_to_enhanced_consensus(
                enhanced,
                consensus,
                consensus_up,
                logreg_up,
                hm_vote,
                n_consensus_meta,
            )
        else:
            n_consensus_meta = {
                **n_consensus_meta,
                "hivemind_vote": 0,
                "hivemind_prediction_note": hm_detail[:200],
            }
    else:
        hive_pred["import_ok"] = None
        hive_pred["explore"] = {}
        hive_pred["vote"] = 0
        hive_pred["vote_detail"] = "fusion_disabled"

    sf_snap = swing_failure_snapshot(df)
    swing_block: dict[str, object] = dict(sf_snap) if isinstance(sf_snap, dict) else {"ok": False}

    sym_g = (linear_symbol or os.environ.get("SYGNIF_PREDICT_GUIDELINE_SYMBOL") or "BTCUSDT").strip()
    try:
        from btc_strategy_guidelines import compute_strategy_guidelines  # noqa: PLC0415

        strategy_guidelines = compute_strategy_guidelines(df, linear_symbol=sym_g)
    except Exception as exc:  # noqa: BLE001
        strategy_guidelines = {"ok": False, "detail": f"guidelines:{str(exc)[:200]}"}

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
            "hivemind": hive_pred,
            "swing_failure": swing_block,
        },
        "backtest_metrics": {
            "random_forest": {k: round(float(v), 4) for k, v in rf_metrics.items()},
            "xgboost": {k: round(float(v), 4) for k, v in xgb_metrics.items()},
            "direction_logistic": {
                "Accuracy": round(float(np.mean(dir_preds == y_test_dir) * 100.0), 2),
            },
        },
        "strategy_guidelines": strategy_guidelines,
    }

    if (os.environ.get("SYGNIF_PREDICT_AUDIT_TIME_SPLIT") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        try:
            ds0 = str(pd.Timestamp(dates[0], tz="UTC")) if len(dates) else ""
            ds_split = str(pd.Timestamp(dates[split_idx], tz="UTC")) if split_idx < len(dates) else ""
            ds_last = str(pd.Timestamp(dates[-1], tz="UTC")) if len(dates) else ""
        except Exception:
            ds0 = ds_split = ds_last = ""
        out["time_split_audit"] = {
            "n_windowed_samples": len(X),
            "split_idx_first_test_row": split_idx,
            "test_ratio_param": float(test_ratio),
            "holdout_frac_approx": round((len(X) - split_idx) / max(len(X), 1), 5),
            "first_window_date_utc": ds0,
            "first_test_window_date_utc": ds_split,
            "last_window_date_utc": ds_last,
            "leakage_note": (
                "Chronological index split: metrics on X[split_idx:]; final RF/XGB refit uses X_train=X[:split_idx] "
                "only, then predicts last_window (most recent bar). Holdout labels are not used as inputs for that row."
            ),
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
