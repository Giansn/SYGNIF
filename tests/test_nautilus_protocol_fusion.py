"""Tests for prediction_agent/nautilus_protocol_fusion.py."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PA = ROOT / "prediction_agent"
FA = ROOT / "finance_agent"
sys.path.insert(0, str(PA))
sys.path.insert(0, str(FA))

import nautilus_protocol_fusion as fus  # noqa: E402


def test_build_fusion_payload_votes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "finance_agent" / "btc_specialist" / "data"
    data.mkdir(parents=True)
    (data / "nautilus_strategy_signal.json").write_text(
        json.dumps(
            {
                "bias": "long",
                "close": 70000.0,
                "generated_utc": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    pred_dir = tmp_path / "prediction_agent"
    pred_dir.mkdir(parents=True)
    (pred_dir / "btc_prediction_output.json").write_text(
        json.dumps(
            {
                "generated_utc": "2026-01-01T01:00:00Z",
                "predictions": {"consensus": "BULLISH"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NAUTILUS_BTC_OHLCV_DIR", str(data))
    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE", "0")
    doc = fus.build_fusion_payload(tmp_path)
    assert doc["schema_version"] == 2
    assert doc["fusion"]["vote_nautilus"] == 1
    assert doc["fusion"]["vote_ml"] == 1
    assert doc["fusion"]["vote_btc_future"] == 0
    assert doc["fusion"]["vote_btc_future_raw"] == 0
    assert doc["fusion"]["btc_future_direction"] == "flat"
    assert doc["fusion"]["btc_future_detail"] == "off"
    assert doc["fusion"]["sum"] == 2
    assert doc["fusion"]["label"] == "strong_long"
    assert doc["nautilus_sidecar"]["bias"] == "long"


def test_build_fusion_payload_includes_usd_btc_macro_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "finance_agent" / "btc_specialist" / "data"
    data.mkdir(parents=True)
    (data / "nautilus_strategy_signal.json").write_text(
        json.dumps({"bias": "neutral", "generated_utc": "2026-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    (data / "btc_usd_index_correlation.json").write_text(
        json.dumps(
            {
                "generated_utc": "2026-01-02T00:00:00Z",
                "last_usd_index_return": 0.0005,
                "last_common_date": "2026-01-01",
                "pearson_correlation_daily_returns": {"pearson_last_20d": -0.15},
            }
        ),
        encoding="utf-8",
    )
    pred_dir = tmp_path / "prediction_agent"
    pred_dir.mkdir(parents=True)
    (pred_dir / "btc_prediction_output.json").write_text(
        json.dumps(
            {
                "generated_utc": "2026-01-01T01:00:00Z",
                "predictions": {"consensus": "MIXED"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NAUTILUS_BTC_OHLCV_DIR", str(data))
    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE", "0")
    monkeypatch.delenv("SYGNIF_PREDICT_USD_BTC_MACRO_OFF", raising=False)
    doc = fus.build_fusion_payload(tmp_path)
    umb = doc.get("usd_btc_macro")
    assert isinstance(umb, dict)
    assert umb.get("last_common_date") == "2026-01-01"
    assert umb.get("macro_source") == "snapshot_file"


def test_write_and_briefing_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "finance_agent" / "btc_specialist" / "data"
    data.mkdir(parents=True)
    (data / "nautilus_strategy_signal.json").write_text(
        json.dumps({"bias": "neutral", "generated_utc": "2026-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    pred_dir = tmp_path / "prediction_agent"
    pred_dir.mkdir(parents=True)
    (pred_dir / "btc_prediction_output.json").write_text(
        json.dumps(
            {
                "generated_utc": "2026-01-01T01:00:00Z",
                "predictions": {
                    "consensus": "MIXED",
                    "direction_logistic": {"label": "UP", "confidence": 97.0},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NAUTILUS_BTC_OHLCV_DIR", str(data))
    monkeypatch.setenv("SYGNIF_NAUTILUS_FUSION_PATH", str(pred_dir / "fused.json"))
    monkeypatch.setenv("SYGNIF_BRIEFING_INCLUDE_NAUTILUS_FUSION", "1")
    fus.write_fused_sidecar(tmp_path)
    path = pred_dir / "fused.json"
    assert path.is_file()
    line = fus.briefing_line_nautilus_fusion(max_chars=300, repo_root=tmp_path)
    assert "NAU_FUSE" in line
    assert "fuse=" in line
    assert "|bf=" in line


def test_fusion_sum_includes_btc_future_when_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "finance_agent" / "btc_specialist" / "data"
    data.mkdir(parents=True)
    (data / "nautilus_strategy_signal.json").write_text(
        json.dumps({"bias": "long", "generated_utc": "2026-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    pred_dir = tmp_path / "prediction_agent"
    pred_dir.mkdir(parents=True)
    (pred_dir / "btc_prediction_output.json").write_text(
        json.dumps({"predictions": {"consensus": "MIXED"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("NAUTILUS_BTC_OHLCV_DIR", str(data))
    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE", "1")

    def _fake_bf(_root: Path):
        return (-1, "posS", {"enabled": True, "ok": True, "profile": "btc_future"})

    monkeypatch.setattr(fus, "_btc_future_fusion_vote", _fake_bf)
    doc = fus.build_fusion_payload(tmp_path)
    assert doc["fusion"]["vote_nautilus"] == 1
    assert doc["fusion"]["vote_ml"] == 0
    assert doc["fusion"]["vote_btc_future"] == -1
    assert doc["fusion"]["vote_btc_future_raw"] == -1
    assert doc["fusion"]["sum"] == 0
    assert doc["fusion"]["label"] == "neutral"


def test_build_fusion_payload_adapts_flat_bf_from_ml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data = tmp_path / "finance_agent" / "btc_specialist" / "data"
    data.mkdir(parents=True)
    (data / "nautilus_strategy_signal.json").write_text(
        json.dumps({"bias": "neutral", "generated_utc": "2026-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    pred_dir = tmp_path / "prediction_agent"
    pred_dir.mkdir(parents=True)
    (pred_dir / "btc_prediction_output.json").write_text(
        json.dumps({"predictions": {"consensus": "BULLISH"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("NAUTILUS_BTC_OHLCV_DIR", str(data))
    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE", "1")
    monkeypatch.setenv("SYGNIF_FUSION_BTC_FUTURE_ADAPT_WHEN_FLAT", "1")

    def _fake_bf(_root: Path):
        return 0, "flat", {"enabled": True, "ok": True, "profile": "btc_future"}

    monkeypatch.setattr(fus, "_btc_future_fusion_vote", _fake_bf)
    doc = fus.build_fusion_payload(tmp_path)
    assert doc["fusion"]["vote_btc_future_raw"] == 0
    assert doc["fusion"]["vote_btc_future"] == 1
    assert doc["fusion"]["btc_future_detail"].startswith("flat→ml:")
    assert doc["fusion"]["btc_future_meta"].get("fusion_flat_adapted") is True
    assert doc["fusion"]["sum"] == 2
    assert doc["fusion"]["label"] == "strong_long"


def test_build_fusion_payload_override_beats_stale_disk_for_flat_bf_adapt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Stale on-disk ``btc_prediction_output.json`` must not zero out ML vote for flat-bf adapt."""
    data = tmp_path / "finance_agent" / "btc_specialist" / "data"
    data.mkdir(parents=True)
    (data / "nautilus_strategy_signal.json").write_text(
        json.dumps({"bias": "neutral", "generated_utc": "2026-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    pred_dir = tmp_path / "prediction_agent"
    pred_dir.mkdir(parents=True)
    (pred_dir / "btc_prediction_output.json").write_text(
        json.dumps({"predictions": {"consensus": "MIXED"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("NAUTILUS_BTC_OHLCV_DIR", str(data))
    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE", "1")
    monkeypatch.setenv("SYGNIF_FUSION_BTC_FUTURE_ADAPT_WHEN_FLAT", "1")

    def _fake_bf(_root: Path):
        return 0, "flat", {"enabled": True, "ok": True, "profile": "btc_future"}

    monkeypatch.setattr(fus, "_btc_future_fusion_vote", _fake_bf)
    live_out = {"predictions": {"consensus": "BULLISH"}}
    doc = fus.build_fusion_payload(tmp_path, btc_prediction_override=live_out)
    assert doc["fusion"]["vote_ml"] == 1
    assert doc["fusion"]["vote_btc_future_raw"] == 0
    assert doc["fusion"]["vote_btc_future"] == 1
    assert str(doc["fusion"]["btc_future_detail"]).startswith("flat→ml:")
    assert doc["btc_prediction"]["predictions"]["consensus"] == "BULLISH"


def test_build_fusion_payload_flat_bf_not_adapted_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = tmp_path / "finance_agent" / "btc_specialist" / "data"
    data.mkdir(parents=True)
    (data / "nautilus_strategy_signal.json").write_text(
        json.dumps({"bias": "neutral", "generated_utc": "2026-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    pred_dir = tmp_path / "prediction_agent"
    pred_dir.mkdir(parents=True)
    (pred_dir / "btc_prediction_output.json").write_text(
        json.dumps({"predictions": {"consensus": "BULLISH"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("NAUTILUS_BTC_OHLCV_DIR", str(data))
    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE", "1")
    monkeypatch.setenv("SYGNIF_FUSION_BTC_FUTURE_ADAPT_WHEN_FLAT", "0")

    def _fake_bf(_root: Path):
        return 0, "flat", {"enabled": True, "ok": True, "profile": "btc_future"}

    monkeypatch.setattr(fus, "_btc_future_fusion_vote", _fake_bf)
    doc = fus.build_fusion_payload(tmp_path)
    assert doc["fusion"]["vote_btc_future_raw"] == 0
    assert doc["fusion"]["vote_btc_future"] == 0
    assert doc["fusion"]["btc_future_detail"] == "flat"
    assert doc["fusion"]["btc_future_meta"].get("fusion_flat_adapted") is None


def test_record_protocol_tick_preserves_then_updates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "finance_agent" / "btc_specialist" / "data"
    data.mkdir(parents=True)
    (data / "nautilus_strategy_signal.json").write_text(
        json.dumps({"bias": "short", "generated_utc": "2026-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    pred_dir = tmp_path / "prediction_agent"
    pred_dir.mkdir(parents=True)
    (pred_dir / "btc_prediction_output.json").write_text(
        json.dumps({"generated_utc": "2026-01-01T01:00:00Z", "predictions": {"consensus": "BEARISH"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("NAUTILUS_BTC_OHLCV_DIR", str(data))
    monkeypatch.setenv("SYGNIF_NAUTILUS_FUSION_PATH", str(pred_dir / "fused.json"))
    fus.write_fused_sidecar(tmp_path)
    fus.record_protocol_tick(tmp_path, {"iter": 1, "target_side": "long"})
    raw = json.loads((pred_dir / "fused.json").read_text(encoding="utf-8"))
    assert raw["predict_protocol_loop"]["iter"] == 1
    assert raw["predict_protocol_loop"]["target_side"] == "long"
    fus.record_protocol_tick(tmp_path, {"iter": 2, "target_side": None})
    raw2 = json.loads((pred_dir / "fused.json").read_text(encoding="utf-8"))
    assert raw2["predict_protocol_loop"]["iter"] == 2


def test_briefing_line_empty_without_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYGNIF_BRIEFING_INCLUDE_NAUTILUS_FUSION", raising=False)
    assert fus.briefing_line_nautilus_fusion(max_chars=200, repo_root=tmp_path) == ""


def test_btc_future_fusion_vote_trade_calls_mainnet(monkeypatch: pytest.MonkeyPatch) -> None:
    import finance_agent.swarm_knowledge as skmod

    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE", "trade")
    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")
    monkeypatch.delenv("BYBIT_DEMO_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_DEMO_API_SECRET", raising=False)
    calls: list[str] = []

    def _fake(sym: str, *, cache_sec: float):
        calls.append(sym)
        return {"retCode": 0, "result": {"list": [{"size": "0.01", "side": "Buy"}]}}

    monkeypatch.setattr(skmod, "fetch_mainnet_linear_position_list", _fake)
    v, d, meta = fus._btc_future_fusion_vote(ROOT)
    assert calls == ["BTCUSDT"]
    assert v == 1
    assert meta.get("profile") == "trade"
    assert meta.get("ok") is True


def test_liquidation_tape_in_fusion_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time as time_mod

    data = tmp_path / "finance_agent" / "btc_specialist" / "data"
    data.mkdir(parents=True)
    (data / "nautilus_strategy_signal.json").write_text(
        json.dumps({"bias": "neutral", "generated_utc": "2026-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    pred_dir = tmp_path / "prediction_agent"
    pred_dir.mkdir(parents=True)
    (pred_dir / "btc_prediction_output.json").write_text(
        json.dumps({"predictions": {"consensus": "MIXED"}}),
        encoding="utf-8",
    )
    ud = tmp_path / "user_data"
    ud.mkdir(parents=True)
    now_ms = int(time_mod.time() * 1000)
    snap = {
        "symbol": "BTCUSDT",
        "liquidation_ingress_total": 3,
        "liquidations_recent": [
            {"T": now_ms, "s": "BTCUSDT", "S": "Buy", "v": "0.5", "p": "100000"},
            {"T": now_ms, "s": "BTCUSDT", "S": "Buy", "v": "0.5", "p": "100000"},
        ],
    }
    (ud / "bybit_ws_monitor_state.json").write_text(json.dumps(snap), encoding="utf-8")
    monkeypatch.setenv("NAUTILUS_BTC_OHLCV_DIR", str(data))
    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE", "0")
    monkeypatch.setenv("SYGNIF_PREDICT_LIQUIDATION_TAPE", "1")
    monkeypatch.setenv("SYGNIF_LIQUIDATION_TAPE_MIN_NOTIONAL_USDT", "1")
    monkeypatch.setenv("SYGNIF_LIQUIDATION_TAPE_RATIO", "1.5")
    doc = fus.build_fusion_payload(tmp_path)
    lt = doc.get("liquidation_tape")
    assert isinstance(lt, dict)
    assert lt.get("ok") is True
    assert lt.get("tape_pressure_vote") == -1
    assert lt.get("tape_label") == "long_liquidation_flush"
