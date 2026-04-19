"""Swarm knowledge: deterministic fuse of sidecar votes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tests.env_utils import delenv_strict

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_swarm_pytorch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYGNIF_SWARM_PYTORCH", raising=False)
    monkeypatch.delenv("SYGNIF_SWARM_PT_WEIGHTS", raising=False)
    monkeypatch.delenv("SYGNIF_SWARM_BTC_FUTURE", raising=False)
    monkeypatch.delenv("SYGNIF_SWARM_TRUTHCOIN_DC", raising=False)
    monkeypatch.delenv("SYGNIF_SWARM_CORE_ENGINE", raising=False)
    monkeypatch.delenv("SYGNIF_SWARM_HIVEMIND_VOTE", raising=False)
    monkeypatch.delenv("SYGNIF_SWARM_FULL_ROOT_ACCESS", raising=False)
    monkeypatch.setenv("SYGNIF_SWARM_OPEN_TRADES", "0")


def test_open_trades_embedded_when_enabled(monkeypatch, tmp_path: Path) -> None:
    from finance_agent import swarm_knowledge as sk

    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_MAINNET")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_ACCOUNT")
    monkeypatch.setenv("SYGNIF_SWARM_OPEN_TRADES", "1")
    monkeypatch.setattr(
        sk,
        "build_open_trades_report",
        lambda: {"enabled": True, "source": "test", "open_n": 2, "trades": []},
    )

    (tmp_path / "p.json").write_text(json.dumps({"predictions": {"consensus": "MIXED"}}))
    (tmp_path / "t.json").write_text(json.dumps({"recognition": {"last_bar_probability_up_pct": 50.0}}))
    (tmp_path / "s.json").write_text(json.dumps({"bias": "neutral"}))
    (tmp_path / "ta.json").write_text(json.dumps({"ta_score": 50.0}))

    out = sk.compute_swarm(
        pred_path=tmp_path / "p.json",
        train_path=tmp_path / "t.json",
        sidecar_path=tmp_path / "s.json",
        ta_path=tmp_path / "ta.json",
    )
    assert out["open_trades"]["source"] == "test"
    assert out["open_trades"]["open_n"] == 2


def test_compute_swarm_struct(monkeypatch, tmp_path: Path) -> None:
    from finance_agent import swarm_knowledge as sk

    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_MAINNET")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_ACCOUNT")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_MODE")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_ADMIN")

    pred = {
        "predictions": {
            "consensus_nautilus_enhanced": "BULLISH",
            "consensus": "MIXED",
        }
    }
    train = {
        "recognition": {
            "last_bar_probability_up_pct": 60.0,
            "last_bar_probability_down_pct": 40.0,
        }
    }
    sidecar = {"bias": "long"}
    ta = {"ta_score": 40.0}

    (tmp_path / "p.json").write_text(json.dumps(pred), encoding="utf-8")
    (tmp_path / "t.json").write_text(json.dumps(train), encoding="utf-8")
    (tmp_path / "s.json").write_text(json.dumps(sidecar), encoding="utf-8")
    (tmp_path / "ta.json").write_text(json.dumps(ta), encoding="utf-8")

    out = sk.compute_swarm(
        pred_path=tmp_path / "p.json",
        train_path=tmp_path / "t.json",
        sidecar_path=tmp_path / "s.json",
        ta_path=tmp_path / "ta.json",
    )
    assert out["sources_n"] == 4
    assert out["sources"]["ml"]["vote"] == 1
    assert out["sources"]["ch"]["vote"] == 1
    assert out["sources"]["sc"]["vote"] == 1
    assert out["sources"]["ta"]["vote"] == -1
    assert out["swarm_mean"] == pytest.approx(0.5, abs=0.01)
    assert out["swarm_conflict"] is True
    assert out.get("swarm_engine") == "python"


def test_briefing_line_respects_env(monkeypatch) -> None:
    from finance_agent import swarm_knowledge as sk

    delenv_strict(monkeypatch, "SYGNIF_BRIEFING_INCLUDE_SWARM")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_MAINNET")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_ACCOUNT")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_MODE")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_ADMIN")
    assert sk.briefing_line_swarm() == ""
    monkeypatch.setenv("SYGNIF_BRIEFING_INCLUDE_SWARM", "1")
    line = sk.briefing_line_swarm(max_chars=200)
    assert line.startswith("BTC_SWARM|")


def test_vote_bybit_mainnet_from_row() -> None:
    from finance_agent import swarm_knowledge as sk

    v, d = sk.vote_bybit_mainnet_from_row(
        {"price24hPcnt": "0.005", "lastPrice": "97000", "fundingRate": "0.0001"},
        thr_pct=0.25,
    )
    assert v == 1
    assert "24h+0.50%" in d or "24h+0.5" in d

    v2, _ = sk.vote_bybit_mainnet_from_row(
        {"price24hPcnt": "-0.004", "lastPrice": "96000", "fundingRate": "0"},
        thr_pct=0.25,
    )
    assert v2 == -1

    v3, _ = sk.vote_bybit_mainnet_from_row(
        {"price24hPcnt": "0.001", "lastPrice": "96000", "fundingRate": "0"},
        thr_pct=0.25,
    )
    assert v3 == 0


def test_compute_swarm_includes_mn_when_enabled(monkeypatch, tmp_path: Path) -> None:
    from finance_agent import swarm_knowledge as sk

    sk._BYBIT_TICKER_CACHE.clear()
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_ACCOUNT")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_MODE")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_ADMIN")
    monkeypatch.setenv("SYGNIF_SWARM_BYBIT_MAINNET", "1")

    fake = {"price24hPcnt": "0.01", "lastPrice": "100000", "fundingRate": "0"}

    def _fake_fetch(**_kwargs):
        return fake

    monkeypatch.setattr(sk, "fetch_bybit_mainnet_ticker_row", _fake_fetch)

    (tmp_path / "p.json").write_text(json.dumps({"predictions": {"consensus_nautilus_enhanced": "BULLISH"}}))
    (tmp_path / "t.json").write_text(
        json.dumps({"recognition": {"last_bar_probability_up_pct": 50.0, "last_bar_probability_down_pct": 50.0}})
    )
    (tmp_path / "s.json").write_text(json.dumps({"bias": "neutral"}))
    (tmp_path / "ta.json").write_text(json.dumps({"ta_score": 50.0}))

    out = sk.compute_swarm(
        pred_path=tmp_path / "p.json",
        train_path=tmp_path / "t.json",
        sidecar_path=tmp_path / "s.json",
        ta_path=tmp_path / "ta.json",
    )
    assert out["sources_n"] == 5
    assert out["sources"]["mn"]["vote"] == 1
    assert out.get("bybit_mainnet", {}).get("enabled") is True


def test_vote_account_position_from_response() -> None:
    from finance_agent import swarm_knowledge as sk

    v, d = sk.vote_account_position_from_response(
        {"retCode": 0, "result": {"list": [{"size": "0.02", "side": "Buy"}]}}
    )
    assert v == 1 and d == "posL"

    v2, d2 = sk.vote_account_position_from_response(
        {"retCode": 0, "result": {"list": [{"size": "0", "side": "Buy"}]}}
    )
    assert v2 == 0 and d2 == "flat"


def test_compute_swarm_includes_bf_when_enabled(monkeypatch, tmp_path: Path) -> None:
    from finance_agent import swarm_knowledge as sk

    sk._BYBIT_DEMO_BF_CACHE.clear()
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_MAINNET")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_ACCOUNT")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_MODE")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_ADMIN")
    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE", "1")
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "dummy")
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "dummy")

    def _fake_demo_pos(_symbol: str, *, cache_sec: float):
        return {"retCode": 0, "result": {"list": [{"size": "0.01", "side": "Buy"}]}}

    monkeypatch.setattr(sk, "fetch_demo_linear_position_list", _fake_demo_pos)

    (tmp_path / "p.json").write_text(json.dumps({"predictions": {"consensus_nautilus_enhanced": "BULLISH"}}))
    (tmp_path / "t.json").write_text(
        json.dumps({"recognition": {"last_bar_probability_up_pct": 50.0, "last_bar_probability_down_pct": 50.0}})
    )
    (tmp_path / "s.json").write_text(json.dumps({"bias": "neutral"}))
    (tmp_path / "ta.json").write_text(json.dumps({"ta_score": 50.0}))

    out = sk.compute_swarm(
        pred_path=tmp_path / "p.json",
        train_path=tmp_path / "t.json",
        sidecar_path=tmp_path / "s.json",
        ta_path=tmp_path / "ta.json",
    )
    assert out["sources_n"] == 5
    assert out["sources"]["bf"]["vote"] == 1
    assert out.get("btc_future", {}).get("enabled") is True
    assert out["btc_future"].get("profile") == "btc_future"


def test_compute_swarm_bf_demo_api_key_hint_optional(monkeypatch, tmp_path: Path) -> None:
    from finance_agent import swarm_knowledge as sk

    sk._BYBIT_DEMO_BF_CACHE.clear()
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_MAINNET")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_ACCOUNT")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_MODE")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_ADMIN")
    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE", "1")
    monkeypatch.setenv("SYGNIF_SWARM_PRINT_DEMO_API_KEY_HINT", "1")
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "AbCdEfGhIjKlMnOp")
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "dummy")

    def _fake_demo_pos(_symbol: str, *, cache_sec: float):
        return {"retCode": 0, "result": {"list": [{"size": "0.01", "side": "Buy"}]}}

    monkeypatch.setattr(sk, "fetch_demo_linear_position_list", _fake_demo_pos)

    (tmp_path / "p.json").write_text(json.dumps({"predictions": {"consensus_nautilus_enhanced": "BULLISH"}}))
    (tmp_path / "t.json").write_text(
        json.dumps({"recognition": {"last_bar_probability_up_pct": 50.0, "last_bar_probability_down_pct": 50.0}})
    )
    (tmp_path / "s.json").write_text(json.dumps({"bias": "neutral"}))
    (tmp_path / "ta.json").write_text(json.dumps({"ta_score": 50.0}))

    out = sk.compute_swarm(
        pred_path=tmp_path / "p.json",
        train_path=tmp_path / "t.json",
        sidecar_path=tmp_path / "s.json",
        ta_path=tmp_path / "ta.json",
    )
    assert out["btc_future"]["demo_api_key_hint"] == "AbCd...MnOp"


def test_compute_swarm_includes_ac_when_enabled(monkeypatch, tmp_path: Path) -> None:
    from finance_agent import swarm_knowledge as sk

    sk._BYBIT_ACCOUNT_CACHE.clear()
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_MAINNET")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_MODE")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_ADMIN")
    monkeypatch.setenv("SYGNIF_SWARM_BYBIT_ACCOUNT", "1")
    monkeypatch.setenv("BYBIT_API_KEY", "dummy")
    monkeypatch.setenv("BYBIT_API_SECRET", "dummy")

    def _fake_fetch(_symbol: str, *, cache_sec: float):
        return {"retCode": 0, "result": {"list": [{"size": "0.01", "side": "Sell"}]}}

    monkeypatch.setattr(sk, "fetch_mainnet_linear_position_list", _fake_fetch)

    (tmp_path / "p.json").write_text(json.dumps({"predictions": {"consensus_nautilus_enhanced": "BULLISH"}}))
    (tmp_path / "t.json").write_text(
        json.dumps({"recognition": {"last_bar_probability_up_pct": 50.0, "last_bar_probability_down_pct": 50.0}})
    )
    (tmp_path / "s.json").write_text(json.dumps({"bias": "neutral"}))
    (tmp_path / "ta.json").write_text(json.dumps({"ta_score": 50.0}))

    out = sk.compute_swarm(
        pred_path=tmp_path / "p.json",
        train_path=tmp_path / "t.json",
        sidecar_path=tmp_path / "s.json",
        ta_path=tmp_path / "ta.json",
    )
    assert out["sources_n"] == 5
    assert out["sources"]["ac"]["vote"] == -1
    assert out.get("bybit_account", {}).get("enabled") is True


def test_wallet_usdt_band_label() -> None:
    from finance_agent import swarm_knowledge as sk

    assert sk.wallet_usdt_band_label(12_500.0, step=1000.0) == "~12k"
    assert sk.wallet_usdt_band_label(None, step=1000.0) == "?"
    assert sk.wallet_usdt_band_label(100.0, step=1000.0) == "~0"


def test_admin_tier_adds_wallet_meta(monkeypatch, tmp_path: Path) -> None:
    from finance_agent import swarm_knowledge as sk

    sk._BYBIT_ACCOUNT_CACHE.clear()
    sk._BYBIT_WALLET_CACHE.clear()
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_MAINNET")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_ACCOUNT")
    monkeypatch.setenv("SYGNIF_SWARM_BYBIT_MODE", "admin")
    monkeypatch.setenv("BYBIT_API_KEY", "dummy")
    monkeypatch.setenv("BYBIT_API_SECRET", "dummy")

    def _fake_pos(_symbol: str, *, cache_sec: float):
        return {"retCode": 0, "result": {"list": []}}

    def _fake_wallet(*, cache_sec: float):
        return {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "coin": [
                            {"coin": "USDT", "availableToWithdraw": "15000", "walletBalance": "15000"}
                        ]
                    }
                ]
            },
        }

    monkeypatch.setattr(sk, "fetch_mainnet_linear_position_list", _fake_pos)
    monkeypatch.setattr(sk, "fetch_mainnet_wallet_balance_usdt", _fake_wallet)

    (tmp_path / "p.json").write_text(json.dumps({"predictions": {"consensus_nautilus_enhanced": "BULLISH"}}))
    (tmp_path / "t.json").write_text(
        json.dumps({"recognition": {"last_bar_probability_up_pct": 50.0, "last_bar_probability_down_pct": 50.0}})
    )
    (tmp_path / "s.json").write_text(json.dumps({"bias": "neutral"}))
    (tmp_path / "ta.json").write_text(json.dumps({"ta_score": 50.0}))

    out = sk.compute_swarm(
        pred_path=tmp_path / "p.json",
        train_path=tmp_path / "t.json",
        sidecar_path=tmp_path / "s.json",
        ta_path=tmp_path / "ta.json",
    )
    assert out.get("bybit_wallet", {}).get("enabled") is True
    assert out["bybit_wallet"].get("usdt_available_briefing") == "~15k"
    assert "ac" in out["sources"]


def test_sygnif_swarm_btc_future_mode_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_agent import swarm_knowledge as sk

    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE", "")
    assert sk.sygnif_swarm_btc_future_mode() == "off"
    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE", "trade")
    assert sk.sygnif_swarm_btc_future_mode() == "trade"
    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE", "demo")
    assert sk.sygnif_swarm_btc_future_mode() == "demo"
    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE", "1")
    assert sk.sygnif_swarm_btc_future_mode() == "demo"


def test_compute_swarm_fuses_ac_into_trade_bf_single_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from finance_agent import swarm_knowledge as sk

    sk._BYBIT_ACCOUNT_CACHE.clear()
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_MAINNET")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_MODE")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_ADMIN")
    monkeypatch.delenv("BYBIT_DEMO_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_DEMO_API_SECRET", raising=False)
    monkeypatch.setenv("SYGNIF_SWARM_BYBIT_ACCOUNT", "1")
    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE", "trade")
    monkeypatch.setenv("BYBIT_API_KEY", "dummy")
    monkeypatch.setenv("BYBIT_API_SECRET", "dummy")
    calls: list[str] = []

    def _fake_mn(sym: str, *, cache_sec: float):
        calls.append(sym)
        return {"retCode": 0, "result": {"list": [{"size": "0.02", "side": "Sell"}]}}

    monkeypatch.setattr(sk, "fetch_mainnet_linear_position_list", _fake_mn)

    (tmp_path / "p.json").write_text(json.dumps({"predictions": {"consensus_nautilus_enhanced": "BULLISH"}}))
    (tmp_path / "t.json").write_text(
        json.dumps({"recognition": {"last_bar_probability_up_pct": 50.0, "last_bar_probability_down_pct": 50.0}})
    )
    (tmp_path / "s.json").write_text(json.dumps({"bias": "neutral"}))
    (tmp_path / "ta.json").write_text(json.dumps({"ta_score": 50.0}))

    out = sk.compute_swarm(
        pred_path=tmp_path / "p.json",
        train_path=tmp_path / "t.json",
        sidecar_path=tmp_path / "s.json",
        ta_path=tmp_path / "ta.json",
    )
    assert calls == ["BTCUSDT"]
    assert "ac" not in out["sources"]
    assert out["sources"]["bf"]["vote"] == -1
    assert out["bybit_account"].get("fused_with_btc_future_trade") is True
    assert out["sources_n"] == 5


def test_compute_swarm_bf_trade_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from finance_agent import swarm_knowledge as sk

    sk._BYBIT_ACCOUNT_CACHE.clear()
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_MAINNET")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_ACCOUNT")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_MODE")
    delenv_strict(monkeypatch, "SYGNIF_SWARM_BYBIT_ADMIN")
    monkeypatch.delenv("BYBIT_DEMO_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_DEMO_API_SECRET", raising=False)
    monkeypatch.setenv("SYGNIF_SWARM_BTC_FUTURE", "trade")
    monkeypatch.setenv("BYBIT_API_KEY", "dummy")
    monkeypatch.setenv("BYBIT_API_SECRET", "dummy")

    def _fake_mn(_symbol: str, *, cache_sec: float):
        return {"retCode": 0, "result": {"list": [{"size": "0.02", "side": "Sell"}]}}

    monkeypatch.setattr(sk, "fetch_mainnet_linear_position_list", _fake_mn)

    (tmp_path / "p.json").write_text(json.dumps({"predictions": {"consensus_nautilus_enhanced": "BULLISH"}}))
    (tmp_path / "t.json").write_text(
        json.dumps({"recognition": {"last_bar_probability_up_pct": 50.0, "last_bar_probability_down_pct": 50.0}})
    )
    (tmp_path / "s.json").write_text(json.dumps({"bias": "neutral"}))
    (tmp_path / "ta.json").write_text(json.dumps({"ta_score": 50.0}))

    out = sk.compute_swarm(
        pred_path=tmp_path / "p.json",
        train_path=tmp_path / "t.json",
        sidecar_path=tmp_path / "s.json",
        ta_path=tmp_path / "ta.json",
    )
    assert out["sources"]["bf"]["vote"] == -1
    assert out["btc_future"].get("profile") == "trade"
    assert out["btc_future"].get("mode") == "trade"
    assert out["btc_future"].get("mainnet") is True


def test_swarm_crypto_roundtrip(monkeypatch) -> None:
    pytest.importorskip("cryptography")
    from cryptography.fernet import Fernet

    from finance_agent import swarm_crypto as sc

    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("SYGNIF_SWARM_FERNET_KEY", key)
    payload = {"a": 1, "b": "x"}
    tok = sc.seal_swarm_dict(payload)
    env = sc.wrap_sealed_envelope(tok)
    assert sc.unwrap_sealed_envelope(env) == payload


def test_build_open_trades_report_bybit_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_agent import swarm_knowledge as sk

    delenv_strict(monkeypatch, "OVERSEER_BYBIT_HEDGE_MAINNET")
    delenv_strict(monkeypatch, "OVERSEER_HEDGE_LIVE_OK")
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "k")
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "s")

    def _fake_demo(sym: str, *, cache_sec: float):
        assert sym == "BTCUSDT"
        return {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "Buy",
                        "size": "0.1",
                        "avgPrice": "70000",
                        "markPrice": "70100",
                        "unrealisedPnl": "1.5",
                        "liqPrice": "60000",
                        "positionIdx": 0,
                    }
                ]
            },
        }

    monkeypatch.setattr(sk, "fetch_demo_linear_position_list", _fake_demo)
    r = sk.build_open_trades_report()
    assert r["source"] == "bybit"
    assert r["ok"] is True
    assert r["open_n"] == 1
    assert r["trades"][0]["pair"] == "BTC/USDT"
    assert r["trades"][0]["is_short"] is False


def test_build_open_trades_report_no_bybit_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_agent import swarm_knowledge as sk

    monkeypatch.delenv("BYBIT_DEMO_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_DEMO_API_SECRET", raising=False)
    monkeypatch.delenv("BYBIT_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_API_SECRET", raising=False)
    delenv_strict(monkeypatch, "OVERSEER_BYBIT_HEDGE_MAINNET")
    delenv_strict(monkeypatch, "OVERSEER_HEDGE_LIVE_OK")
    r = sk.build_open_trades_report()
    assert r["source"] == "bybit"
    assert r["ok"] is False
    assert r["reason"] == "no_bybit_signed_creds"
    assert r["open_n"] == 0


def test_freqtrade_open_trades_archive_import() -> None:
    from finance_agent.swarm_open_trades_freqtrade_archive import build_open_trades_report_freqtrade_legacy

    assert callable(build_open_trades_report_freqtrade_legacy)


def test_hivemind_vote_from_bybit_demo_position() -> None:
    from finance_agent import swarm_knowledge as sk

    assert sk.hivemind_vote_from_bybit_demo_position(None) == (0, "demo_flat")
    assert sk.hivemind_vote_from_bybit_demo_position({"retCode": 1}) == (0, "demo_flat")
    long_r = {"retCode": 0, "result": {"list": [{"side": "Buy", "size": "0.01"}]}}
    assert sk.hivemind_vote_from_bybit_demo_position(long_r) == (1, "demo_long")
    short_r = {"retCode": 0, "result": {"list": [{"side": "Sell", "size": "0.02"}]}}
    assert sk.hivemind_vote_from_bybit_demo_position(short_r) == (-1, "demo_short")


def test_post_linear_market_order_refused_without_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_agent import swarm_knowledge as sk

    monkeypatch.delenv("SYGNIF_SWARM_KNOWLEDGE_ORDER_ACK", raising=False)
    r = sk.post_linear_market_order(symbol="BTCUSDT", side="Buy", qty="0.001")
    assert r["retCode"] == -1
    assert "SYGNIF_SWARM_KNOWLEDGE_ORDER_ACK" in r["retMsg"]


def test_post_linear_market_order_delegates_with_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_agent import swarm_knowledge as sk

    monkeypatch.setenv("SYGNIF_SWARM_KNOWLEDGE_ORDER_ACK", "YES")
    td = str(REPO / "trade_overseer")
    if td not in sys.path:
        sys.path.insert(0, td)
    import bybit_linear_hedge as blh  # noqa: PLC0415

    def _fake(*args: object, **kwargs: object) -> dict:
        return {"retCode": 0, "retMsg": "OK", "fake": True, "args": args, "kwargs": kwargs}

    monkeypatch.setattr(blh, "create_market_order", _fake)
    r = sk.post_linear_market_order(symbol="BTCUSDT", side="Buy", qty="0.001", position_idx=0)
    assert r["retCode"] == 0
    assert r.get("fake") is True
