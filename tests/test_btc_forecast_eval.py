"""btc_forecast_eval: pending append + next-bar resolution."""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
PA = ROOT / "prediction_agent"
sys.path.insert(0, str(PA))

import btc_forecast_eval as ev  # noqa: E402


def _sample_out(*, last_c: str, gen: str) -> dict:
    return {
        "last_candle_utc": last_c,
        "generated_utc": gen,
        "current_close": 100.0,
        "predictions": {
            "random_forest": {"next_mean": 101.0, "delta": 1.0},
            "xgboost": {"next_mean": 102.0, "delta": 2.0},
            "direction_logistic": {"label": "UP", "confidence": 60},
            "consensus": "BULLISH",
            "consensus_nautilus_enhanced": "BULLISH",
        },
    }


def test_first_row_after_picks_next_candle() -> None:
    df = pd.DataFrame(
        [
            {"Date": "2024-06-01T10:00:00+00:00", "Open": 1, "High": 2, "Low": 1, "Close": 1.5},
            {"Date": "2024-06-01T10:05:00+00:00", "Open": 1.5, "High": 3, "Low": 1, "Close": 2.5},
        ]
    )
    last = pd.Timestamp("2024-06-01T10:00:00+00:00")
    row = ev._first_row_after(df, last)
    assert row is not None
    assert float(row["Close"]) == pytest.approx(2.5)


def test_append_forecast_pending_writes_row(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SYGNIF_PREDICT_EVAL_LOG", "1")
    p = tmp_path / "pending.jsonl"
    monkeypatch.setenv("SYGNIF_PREDICT_EVAL_FORECAST_JSONL", str(p))
    out = _sample_out(last_c="2024-06-01T10:00:00Z", gen="2024-06-01T10:04:00Z")
    eid = ev.append_forecast_pending(out, symbol="BTCUSDT")
    assert eid
    assert p.is_file()
    row = json.loads(p.read_text(encoding="utf-8").strip())
    assert row["eval_id"] == eid
    assert row["symbol"] == "BTCUSDT"
    assert row["forecast"]["logreg_label"] == "UP"
    assert row["status"] == "pending"


def test_process_pending_resolves_and_dedupes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SYGNIF_PREDICT_EVAL_LOG", "1")
    monkeypatch.setenv("SYGNIF_PREDICT_EVAL_FORECAST_JSONL", str(tmp_path / "pending.jsonl"))
    monkeypatch.setenv("SYGNIF_PREDICT_EVAL_OUTCOMES_JSONL", str(tmp_path / "outcomes.jsonl"))
    out = _sample_out(last_c="2024-06-01T10:00:00Z", gen="2024-06-01T10:04:00Z")
    ev.append_forecast_pending(out, symbol="BTCUSDT")

    df = pd.DataFrame(
        [
            {"Date": "2024-06-01T10:00:00+00:00", "Open": 100, "High": 101, "Low": 99, "Close": 100.0},
            {"Date": "2024-06-01T10:05:00+00:00", "Open": 100, "High": 110, "Low": 99, "Close": 105.0},
        ]
    )
    monkeypatch.setattr(ev, "_due_ready", lambda *a, **k: True)
    monkeypatch.setattr(ev, "_fetch_linear_5m_df", lambda sym, limit=200: df)

    s1 = ev.process_pending_outcomes()
    assert s1["processed"] == 1
    assert s1["errors"] == 0
    oc = tmp_path / "outcomes.jsonl"
    o1 = json.loads(oc.read_text(encoding="utf-8").strip().splitlines()[0])
    assert o1["schema"] == "sygnif.btc_forecast_eval_outcome/v1"
    assert o1["actual_next_close"] == 105.0
    assert o1["realized_direction"] == 1
    assert o1["logreg_direction_hit"] is True

    s2 = ev.process_pending_outcomes()
    assert s2["processed"] == 0
    assert oc.read_text(encoding="utf-8").count("\n") == 1

    rep = ev.aggregate_report()
    assert rep["n"] == 1
    assert rep["logreg_direction_accuracy"] == pytest.approx(1.0)
