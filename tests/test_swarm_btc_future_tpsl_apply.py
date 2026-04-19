"""finance_agent/swarm_btc_future_tpsl_apply.py (no live Bybit)."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FA = ROOT / "finance_agent"
if str(FA) not in sys.path:
    sys.path.insert(0, str(FA))

import swarm_btc_future_tpsl_apply as tpsl  # noqa: E402


def test_consensus_mid_prefers_rf_xgb_mean() -> None:
    pred = {
        "current_close": 70000.0,
        "predictions": {
            "random_forest": {"next_mean": 70100.0},
            "xgboost": {"next_mean": 69900.0},
        },
    }
    mid, close = tpsl.consensus_mid_and_close(pred)
    assert close == 70000.0
    assert mid == 70000.0


def test_compute_tpsl_long_uses_mid_when_above_avg() -> None:
    c = tpsl.compute_tpsl_strings(
        side="Buy",
        avg=70000.0,
        mid=70500.0,
        mark=69900.0,
        tp_pct=0.5,
        sl_pct=0.35,
        trail_usd=100.0,
        trail_frac=0.25,
    )
    assert c["take_profit"] == "70500.00"
    assert float(c["stop_loss"]) < 70000.0
    assert "trailing_stop" in c


def test_compute_tpsl_short_uses_mid_when_below_avg() -> None:
    c = tpsl.compute_tpsl_strings(
        side="Sell",
        avg=70000.0,
        mid=69500.0,
        mark=69600.0,
        tp_pct=0.5,
        sl_pct=0.35,
        trail_usd=0.0,
        trail_frac=0.25,
    )
    assert c["take_profit"] == "69500.00"
    assert float(c["stop_loss"]) > 70000.0


def test_compute_tpsl_long_reanchors_sl_after_mark_clamp_vs_liq() -> None:
    """Regression: Bybit mark clamp must not leave a long SL below the liquidation buffer."""
    c = tpsl.compute_tpsl_strings(
        side="Buy",
        avg=73922.9,
        mid=73000.0,
        mark=73933.0,
        tp_pct=0.5,
        sl_pct=30.0,
        trail_usd=0.0,
        trail_frac=0.0,
        liq_price=72771.0,
        liq_buffer_bps=20.0,
        liq_anchor_enabled=True,
    )
    slf = float(c["stop_loss"])
    floor = 72771.0 * (1.0 + 20.0 / 10000.0)
    assert slf >= floor - 0.05
    assert slf < 73933.0


def test_compute_tpsl_long_liq_anchor_raises_sl_above_liq_floor() -> None:
    """Wide %-SL below liq floor must be raised toward liquidation buffer."""
    c = tpsl.compute_tpsl_strings(
        side="Buy",
        avg=100_000.0,
        mid=99_000.0,
        mark=99_500.0,
        tp_pct=0.5,
        sl_pct=5.0,
        trail_usd=0.0,
        trail_frac=0.25,
        liq_price=98_000.0,
        liq_buffer_bps=100.0,
        liq_anchor_enabled=True,
    )
    floor = 98_000.0 * 1.01
    assert float(c["stop_loss"]) >= floor - 1.0
    assert (c.get("liq_anchor_meta") or {}).get("action") == "raised_sl_above_liq_floor"


def test_compute_tpsl_short_liq_anchor_caps_sl_below_liq_ceiling() -> None:
    c = tpsl.compute_tpsl_strings(
        side="Sell",
        avg=100_000.0,
        mid=101_000.0,
        mark=99_500.0,
        tp_pct=0.5,
        sl_pct=8.0,
        trail_usd=0.0,
        trail_frac=0.25,
        liq_price=102_000.0,
        liq_buffer_bps=50.0,
        liq_anchor_enabled=True,
    )
    cap = 102_000.0 * (1.0 - 50.0 / 10_000.0)
    assert float(c["stop_loss"]) <= cap + 1.0
    assert (c.get("liq_anchor_meta") or {}).get("action") == "capped_sl_below_liq_ceiling"


def test_compute_tpsl_short_clamps_tp_below_mark() -> None:
    """Bybit rejects Sell TP above mark — mid can sit above mark while short is in profit."""
    c = tpsl.compute_tpsl_strings(
        side="Sell",
        avg=74532.1,
        mid=74521.9,
        mark=74360.8,
        tp_pct=0.5,
        sl_pct=0.35,
        trail_usd=150.0,
        trail_frac=0.25,
    )
    assert float(c["take_profit"]) < 74360.8
    assert "clamped_lt_mark" in c["tp_note"]


def test_base_tpsl_reward_risk_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_TPSL_PROFILE", "reward_risk")
    b_tp, b_sl, b_tr, b_tf = tpsl.base_tpsl_from_profile()
    assert b_tp == 0.55 and b_sl == 0.20 and b_tr == 120.0 and b_tf == 0.15


def test_channel_adjust_tightens_sl_when_uncertain_short(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_TPSL_CHANNEL_ADJUST", "1")
    monkeypatch.setenv("SYGNIF_PREDICTION_AGENT_DIR", str(tmp_path))
    (tmp_path / "training_channel_output.json").write_text(
        json.dumps(
            {
                "recognition": {
                    "last_bar_probability_up_pct": 70.0,
                    "last_bar_probability_down_pct": 30.0,
                }
            }
        ),
        encoding="utf-8",
    )
    tp, sl, tr, meta = tpsl.channel_adjust_tpsl("Sell", 0.5, 0.35, 150.0)
    assert meta.get("enabled") is True
    assert sl < 0.35
    assert "uncertain_short" in (meta.get("note") or "")


def test_apply_skipped_when_auto_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL", raising=False)
    r = tpsl.apply_btc_future_tpsl(dry_run=False)
    assert r["skipped"] == "SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL_off"


def test_apply_skipped_on_swarm_conflict(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL", "1")
    monkeypatch.setenv("SYGNIF_SWARM_TPSL_SKIP_ON_SWARM_CONFLICT", "1")
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "k")
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "s")
    monkeypatch.setenv("SYGNIF_PREDICTION_AGENT_DIR", str(tmp_path))

    (tmp_path / "btc_prediction_output.json").write_text(json.dumps({"current_close": 1.0, "predictions": {}}))
    (tmp_path / "swarm_knowledge_output.json").write_text(
        json.dumps({"swarm_conflict": True, "swarm_mean": 0.0}),
        encoding="utf-8",
    )

    r = tpsl.apply_btc_future_tpsl(dry_run=True)
    assert r["skipped"] == "swarm_conflict"


def test_apply_dry_run_computes_detail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL", "1")
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "k")
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "s")
    monkeypatch.setenv("SYGNIF_PREDICTION_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("SYGNIF_SWARM_TPSL_SKIP_ON_SWARM_CONFLICT", "0")

    (tmp_path / "btc_prediction_output.json").write_text(
        json.dumps(
            {
                "current_close": 70000.0,
                "predictions": {
                    "random_forest": {"next_mean": 69500.0},
                    "xgboost": {"next_mean": 69500.0},
                },
            }
        ),
        encoding="utf-8",
    )

    def _fake_list(_sym: str):
        return {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "size": "0.01",
                        "side": "Sell",
                        "avgPrice": "70000",
                        "markPrice": "69900",
                        "liqPrice": "71000",
                        "positionIdx": 2,
                    }
                ]
            },
        }

    td = ROOT / "trade_overseer"
    sys.path.insert(0, str(td))
    import bybit_linear_hedge as blh  # noqa: E402

    monkeypatch.setattr(blh, "position_list", _fake_list)

    r = tpsl.apply_btc_future_tpsl(dry_run=True)
    assert r["ok"] is True
    assert r["skipped"] == "dry_run"
    assert r["detail"].get("take_profit") == "69500.00"
    assert r["detail"].get("positionIdx") == 2
    assert r["detail"].get("liq_price_venue") == 71000.0
    assert isinstance(r["detail"].get("liq_anchor"), dict)

    mem = tmp_path / "swarm_sl_liquidation_memory.jsonl"
    assert mem.is_file()
    last = mem.read_text(encoding="utf-8").strip().splitlines()[-1]
    rec = json.loads(last)
    assert rec.get("stop_loss") == r["detail"].get("stop_loss")
    assert rec.get("liq_price_venue") == 71000.0


def test_sl_memory_off_skips_jsonl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL", "1")
    monkeypatch.setenv("SYGNIF_SWARM_SL_MEMORY", "0")
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "k")
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "s")
    monkeypatch.setenv("SYGNIF_PREDICTION_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("SYGNIF_SWARM_TPSL_SKIP_ON_SWARM_CONFLICT", "0")
    (tmp_path / "btc_prediction_output.json").write_text(
        json.dumps(
            {
                "current_close": 70000.0,
                "predictions": {
                    "random_forest": {"next_mean": 69500.0},
                    "xgboost": {"next_mean": 69500.0},
                },
            }
        ),
        encoding="utf-8",
    )

    def _fake_list(_sym: str):
        return {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "size": "0.01",
                        "side": "Sell",
                        "avgPrice": "70000",
                        "markPrice": "69900",
                        "positionIdx": 2,
                    }
                ]
            },
        }

    td = ROOT / "trade_overseer"
    sys.path.insert(0, str(td))
    import bybit_linear_hedge as blh  # noqa: E402

    monkeypatch.setattr(blh, "position_list", _fake_list)
    tpsl.apply_btc_future_tpsl(dry_run=True)
    assert not (tmp_path / "swarm_sl_liquidation_memory.jsonl").exists()
