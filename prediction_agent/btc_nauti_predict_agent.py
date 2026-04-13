#!/usr/bin/env python3
"""
**Nauti-BTC-predict agent** — dataset journal, realized-accuracy resolution, light tuning.

- **Journal** (append-only JSONL): one row per prediction run, linked to the last closed bar.
- **Resolve**: when newer OHLCV exists, fill the *next* bar outcome and mark consensus / LogReg correct.
- **Report**: rolling accuracy on resolved rows (honest \"realtime\" track record on your file cadence).
- **Tune**: grid over window × ``C``; optional ``--calibrate``. **Walkforward**: rolling-origin mean test accuracy.

90% directional accuracy on BTC next-bar is **not generally achievable** with stable edge; use this to **measure** and **iterate**, not as a guaranteed target.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.metrics import accuracy_score
from sklearn.preprocessing import StandardScaler

# Reuse feature pipeline from the runner (same DATA_DIR layout).
from btc_predict_runner import (
    DATA_DIR,
    add_ta_features,
    build_windowed_dataset,
    load_bybit_ohlcv,
    _make_direction_model,
)

SCRIPT_DIR = Path(__file__).resolve().parent
JOURNAL_PATH = SCRIPT_DIR / "btc_nauti_prediction_journal.jsonl"
REPORT_PATH = SCRIPT_DIR / "btc_nauti_accuracy_report.json"


def _ohlcv_path(timeframe: str) -> Path:
    name = "btc_1h_ohlcv.json" if timeframe == "1h" else "btc_daily_90d.json"
    return Path(DATA_DIR) / name


def _load_ohlcv_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return raw if isinstance(raw, list) else []


def _bar_ts_ms(utc_str: str) -> int | None:
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def append_journal_from_output(output_path: Path, *, skip_duplicate_bar: bool = True) -> dict | None:
    """Append one journal row from ``btc_prediction_output.json``. Returns the row or None."""
    if not output_path.is_file():
        return None
    with open(output_path, encoding="utf-8") as f:
        out = json.load(f)
    if not isinstance(out, dict):
        return None

    pred_bar_utc = out.get("last_candle_utc")
    if not pred_bar_utc:
        return None
    row = {
        "id": hashlib.sha256(
            f"{pred_bar_utc}|{out.get('generated_utc')}|{out.get('predictions', {}).get('consensus')}".encode()
        ).hexdigest()[:20],
        "recorded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "open",
        "timeframe": out.get("timeframe", "1h"),
        "window_size": out.get("window_size"),
        "pred_bar_utc": pred_bar_utc,
        "pred_bar_close": out.get("current_close"),
        "predictions": out.get("predictions"),
        "nautilus_research": out.get("nautilus_research"),
        "backtest_metrics_snapshot": out.get("backtest_metrics"),
        "actual_next_close": None,
        "actual_next_bar_utc": None,
        "actual_up": None,
        "consensus_correct": None,
        "logreg_correct": None,
    }

    if skip_duplicate_bar and JOURNAL_PATH.is_file():
        tail = JOURNAL_PATH.read_text(encoding="utf-8").strip().splitlines()[-5:]
        for line in tail:
            try:
                prev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if prev.get("pred_bar_utc") == pred_bar_utc and prev.get("status") == "open":
                return None

    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(JOURNAL_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def _next_candle_after(rows: list[dict], pred_ts_ms: int) -> dict | None:
    """Return first candle strictly after ``pred_ts_ms`` (Bybit bar open time)."""
    for c in rows:
        if not isinstance(c, dict) or "t" not in c:
            continue
        t = int(c["t"])
        if t > pred_ts_ms:
            return c
    return None


def resolve_journal(timeframe: str = "1h") -> tuple[int, int]:
    """
    Update open rows when the next OHLCV bar exists. Returns (resolved_now, still_open).
    Rewrites JSONL (read all → apply → write all) for simplicity at modest file sizes.
    """
    path_oh = _ohlcv_path(timeframe)
    rows_json = _load_ohlcv_rows(path_oh)
    if not JOURNAL_PATH.is_file():
        return 0, 0

    lines = JOURNAL_PATH.read_text(encoding="utf-8").strip().splitlines()
    entries: list[dict] = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    resolved_now = 0
    for e in entries:
        if e.get("status") != "open":
            continue
        if e.get("timeframe") != timeframe:
            continue
        pred_ts = _bar_ts_ms(e.get("pred_bar_utc", ""))
        if pred_ts is None:
            continue
        nxt = _next_candle_after(rows_json, pred_ts)
        if nxt is None:
            continue
        pred_close = float(e.get("pred_bar_close") or 0)
        next_close = float(nxt["c"])
        up = next_close > pred_close
        preds = e.get("predictions") or {}
        enh = (preds.get("consensus_nautilus_enhanced") or preds.get("consensus") or "").upper()
        log_label = (preds.get("direction_logistic") or {}).get("label", "")
        log_up = log_label.upper() == "UP"

        e["actual_next_close"] = round(next_close, 2)
        e["actual_next_bar_utc"] = datetime.fromtimestamp(
            int(nxt["t"]) / 1000, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        e["actual_up"] = up
        if enh in ("BULLISH", "STRONG_BULLISH"):
            e["consensus_correct"] = bool(up)
        elif enh in ("BEARISH", "STRONG_BEARISH"):
            e["consensus_correct"] = bool(not up)
        else:
            e["consensus_correct"] = None
        e["logreg_correct"] = log_up == up
        e["status"] = "resolved"
        e["resolved_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        resolved_now += 1

    JOURNAL_PATH.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in entries) + ("\n" if entries else ""),
        encoding="utf-8",
    )
    still_open = sum(1 for x in entries if x.get("status") == "open")
    return resolved_now, still_open


def build_accuracy_report() -> dict:
    if not JOURNAL_PATH.is_file():
        return {"error": "no_journal", "resolved": 0}

    entries = []
    for line in JOURNAL_PATH.read_text(encoding="utf-8").strip().splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    resolved = [e for e in entries if e.get("status") == "resolved"]
    open_n = sum(1 for e in entries if e.get("status") == "open")

    def rate(key: str) -> float | None:
        vals = [e[key] for e in resolved if e.get(key) is not None]
        if not vals:
            return None
        return round(100.0 * sum(1 for v in vals if v) / len(vals), 2)

    report = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_rows": len(entries),
        "resolved": len(resolved),
        "open": open_n,
        "consensus_direction_accuracy_pct": rate("consensus_correct"),
        "logreg_direction_accuracy_pct": rate("logreg_correct"),
        "last_20_consensus_pct": None,
        "last_20_logreg_pct": None,
        "target_note": "90% next-bar direction is unrealistic for BTC; use report to track drift.",
    }
    last20 = resolved[-20:]
    if last20:
        c_ok = [e["consensus_correct"] for e in last20 if e.get("consensus_correct") is not None]
        l_ok = [e["logreg_correct"] for e in last20 if e.get("logreg_correct") is not None]
        if c_ok:
            report["last_20_consensus_pct"] = round(100.0 * sum(c_ok) / len(c_ok), 2)
        if l_ok:
            report["last_20_logreg_pct"] = round(100.0 * sum(l_ok) / len(l_ok), 2)

    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def walkforward_direction(
    timeframe: str = "1h",
    window: int = 5,
    C: float = 0.25,
    calibrate: bool = False,
    n_train: int = 450,
    n_test: int = 60,
    step: int = 30,
) -> dict:
    """
    Rolling origin evaluation: train direction model on ``n_train`` samples, test on ``n_test``, advance by ``step``.
    """
    from sklearn.preprocessing import StandardScaler

    data_file = str(_ohlcv_path(timeframe))
    df = load_bybit_ohlcv(data_file)
    df = add_ta_features(df)
    feature_cols = [c for c in df.columns if c not in ("Date", "Mean")]
    X, y, _dates = build_windowed_dataset(df, feature_cols, "Mean", window)
    if len(X) < n_train + n_test + 10:
        return {"error": "insufficient_samples", "need": n_train + n_test + 10, "have": len(X)}

    accuracies: list[float] = []
    folds = 0
    i = n_train
    while i + n_test <= len(X):
        X_tr = X[i - n_train : i]
        y_tr = y[i - n_train : i]
        X_te = X[i : i + n_test]
        y_te = y[i : i + n_test]
        y_tr_d = (np.diff(np.concatenate([[y_tr[0]], y_tr])) > 0).astype(int)[1:]
        y_te_d = (np.diff(np.concatenate([[y_te[0]], y_te])) > 0).astype(int)[1:]
        xtr, xte = X_tr[1:], X_te[1:]
        if len(xtr) < 30 or len(xte) < 5:
            i += step
            continue
        sc = StandardScaler()
        xtr_s = sc.fit_transform(xtr)
        xte_s = sc.transform(xte)
        model = _make_direction_model(calibrate=calibrate, C=C)
        model.fit(xtr_s, y_tr_d)
        pred = model.predict(xte_s)
        accuracies.append(float(accuracy_score(y_te_d, pred)))
        folds += 1
        i += step

    out = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timeframe": timeframe,
        "window": window,
        "C": C,
        "calibrate": calibrate,
        "n_train": n_train,
        "n_test": n_test,
        "step": step,
        "folds": folds,
        "mean_test_direction_accuracy_pct": round(100.0 * float(np.mean(accuracies)), 2) if accuracies else None,
        "std_pct": round(100.0 * float(np.std(accuracies)), 2) if len(accuracies) > 1 else None,
        "min_pct": round(100.0 * float(np.min(accuracies)), 2) if accuracies else None,
        "max_pct": round(100.0 * float(np.max(accuracies)), 2) if accuracies else None,
    }
    wf_path = SCRIPT_DIR / "btc_nauti_walkforward_result.json"
    wf_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out


def tune_direction_model(
    timeframe: str = "1h",
    test_ratio: float = 0.2,
    windows: list[int] | None = None,
    cs: list[float] | None = None,
    calibrate: bool = False,
) -> dict:
    """Grid search window × LogReg C on direction accuracy (same features as runner)."""
    windows = windows or [3, 5, 7, 10, 12]
    cs = cs or [0.1, 0.25, 0.5, 1.0, 2.0, 4.0]
    data_file = str(_ohlcv_path(timeframe))
    df = load_bybit_ohlcv(data_file)
    df = add_ta_features(df)
    feature_cols = [c for c in df.columns if c not in ("Date", "Mean")]
    target_col = "Mean"

    best = {"accuracy": -1.0, "window": None, "C": None}
    details = []

    for w in windows:
        X, y, _dates = build_windowed_dataset(df, feature_cols, target_col, w)
        if len(X) < 30:
            continue
        split_idx = int(len(X) * (1 - test_ratio))
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]
        y_train_dir = (np.diff(np.concatenate([[y_train[0]], y_train])) > 0).astype(int)[1:]
        y_test_dir = (np.diff(np.concatenate([[y_test[0]], y_test])) > 0).astype(int)[1:]
        X_tr, X_te = X_train[1:], X_test[1:]
        if len(X_te) < 5:
            continue
        scaler = StandardScaler()
        X_tr_s = scaler.fit_transform(X_tr)
        X_te_s = scaler.transform(X_te)
        for C in cs:
            model = _make_direction_model(calibrate=calibrate, C=C)
            try:
                model.fit(X_tr_s, y_train_dir)
                pred = model.predict(X_te_s)
                acc = accuracy_score(y_test_dir, pred)
            except Exception as exc:  # noqa: BLE001
                details.append(
                    {"window": w, "C": C, "test_dir_accuracy": None, "error": str(exc)[:120]}
                )
                continue
            details.append({"window": w, "C": C, "test_dir_accuracy": round(acc * 100, 2)})
            if acc > best["accuracy"]:
                best = {"accuracy": acc, "window": w, "C": C}

    out = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timeframe": timeframe,
        "test_ratio": test_ratio,
        "calibrate": calibrate,
        "best": {
            "test_direction_accuracy_pct": round(best["accuracy"] * 100, 2),
            "window": best["window"],
            "C": best["C"],
        },
        "grid_size": len(details),
        "top_5": sorted(
            [d for d in details if d.get("test_dir_accuracy") is not None],
            key=lambda x: -x["test_dir_accuracy"],
        )[:5],
    }
    tune_path = SCRIPT_DIR / "btc_nauti_tune_result.json"
    tune_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Nauti-BTC-predict: journal, resolve, report, tune")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_j = sub.add_parser("journal-append", help="Append last btc_prediction_output.json to JSONL journal")
    p_j.add_argument(
        "--output-json",
        type=Path,
        default=SCRIPT_DIR / "btc_prediction_output.json",
    )
    p_j.add_argument("--force-duplicate-bar", action="store_true")

    p_r = sub.add_parser("resolve", help="Resolve open journal rows using latest OHLCV")
    p_r.add_argument("--timeframe", choices=["1h", "daily"], default="1h")

    sub.add_parser("report", help="Write btc_nauti_accuracy_report.json and print summary")

    p_t = sub.add_parser("tune", help="Grid-search window and LogReg C (direction accuracy)")
    p_t.add_argument("--timeframe", choices=["1h", "daily"], default="1h")
    p_t.add_argument("--test-ratio", type=float, default=0.2)
    p_t.add_argument(
        "--calibrate",
        action="store_true",
        help="Use isotonic calibration inside each grid cell (slower)",
    )

    p_w = sub.add_parser(
        "walkforward",
        help="Rolling-origin mean test accuracy (more realistic than single holdout)",
    )
    p_w.add_argument("--timeframe", choices=["1h", "daily"], default="1h")
    p_w.add_argument("--window", type=int, default=5)
    p_w.add_argument("--dir-C", type=float, default=0.25)
    p_w.add_argument("--calibrate", action="store_true")
    p_w.add_argument("--n-train", type=int, default=450)
    p_w.add_argument("--n-test", type=int, default=60)
    p_w.add_argument("--step", type=int, default=30)

    args = ap.parse_args()
    if args.cmd == "journal-append":
        row = append_journal_from_output(
            args.output_json,
            skip_duplicate_bar=not args.force_duplicate_bar,
        )
        if row is None:
            print("No row appended (missing output, or duplicate open bar).")
            return 1
        print(json.dumps({"appended": row["id"], "pred_bar_utc": row["pred_bar_utc"]}))
        return 0
    if args.cmd == "resolve":
        n_ok, n_open = resolve_journal(timeframe=args.timeframe)
        print(json.dumps({"resolved_now": n_ok, "still_open": n_open}))
        return 0
    if args.cmd == "report":
        r = build_accuracy_report()
        print(json.dumps(r, indent=2))
        return 0
    if args.cmd == "tune":
        r = tune_direction_model(
            timeframe=args.timeframe,
            test_ratio=args.test_ratio,
            calibrate=args.calibrate,
        )
        print(json.dumps(r, indent=2))
        return 0
    if args.cmd == "walkforward":
        r = walkforward_direction(
            timeframe=args.timeframe,
            window=args.window,
            C=args.dir_C,
            calibrate=args.calibrate,
            n_train=args.n_train,
            n_test=args.n_test,
            step=args.step,
        )
        print(json.dumps(r, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
