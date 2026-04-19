"""prediction_agent/swarm_btc_flow.py + swarm_btc_translate.py — constant pipeline, no orders."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture()
def flow_mod():
    sys.path.insert(0, str(REPO / "prediction_agent"))
    import swarm_btc_flow as m  # noqa: PLC0415

    return m


@pytest.fixture()
def tr_mod():
    sys.path.insert(0, str(REPO / "prediction_agent"))
    import swarm_btc_translate as m  # noqa: PLC0415

    return m


def test_synthesize_bull_no_conflict(flow_mod, tr_mod) -> None:
    from swarm_btc_flow_constants import K_BTC_DUMP_RISK_PCT
    from swarm_btc_flow_constants import K_BULL_BEAR
    from swarm_btc_flow_constants import K_CHANNEL_PROB_DOWN_PCT
    from swarm_btc_flow_constants import K_ORDER_SIGNAL
    from swarm_btc_flow_constants import K_SIDE
    from swarm_btc_flow_constants import K_SWARM_CONFLICT
    from swarm_btc_flow_constants import K_SWARM_LABEL

    vec = {
        K_SWARM_LABEL: "SWARM_BULL",
        K_SWARM_CONFLICT: False,
        K_CHANNEL_PROB_DOWN_PCT: 41.2,
    }
    synth = flow_mod.synthesize_swarm_btc_card(vec, repo=REPO, skip_price_fetch=True)
    assert synth[K_ORDER_SIGNAL] == "BUY"
    assert synth[K_SIDE] == "LONG"
    assert synth[K_BULL_BEAR] == "BULL"
    assert synth[K_BTC_DUMP_RISK_PCT] == pytest.approx(41.2)
    buf = io.StringIO()
    tr_mod.print_swarm_btc_card(synth, file=buf)
    text = buf.getvalue()
    assert "Order: BUY" in text
    assert "Long/Short: LONG" in text
    assert "BTC Dump risk %: 41.2" in text
    assert "Bull/Bear: BULL" in text


def test_synthesize_conflict_holds(flow_mod) -> None:
    from swarm_btc_flow_constants import K_ORDER_SIGNAL
    from swarm_btc_flow_constants import K_SIDE
    from swarm_btc_flow_constants import K_SWARM_CONFLICT
    from swarm_btc_flow_constants import K_SWARM_LABEL

    vec = {K_SWARM_LABEL: "SWARM_BULL", K_SWARM_CONFLICT: True}
    synth = flow_mod.synthesize_swarm_btc_card(vec, repo=REPO, skip_price_fetch=True)
    assert synth[K_ORDER_SIGNAL] == "HOLD"
    assert synth[K_SIDE] == "FLAT"


def test_translate_price_na(tr_mod) -> None:
    from swarm_btc_flow_constants import K_AMOUNT_BTC
    from swarm_btc_flow_constants import K_BTC_DUMP_RISK_PCT
    from swarm_btc_flow_constants import K_BTC_USD_PRICE
    from swarm_btc_flow_constants import K_BULL_BEAR
    from swarm_btc_flow_constants import K_LEVERAGE
    from swarm_btc_flow_constants import K_ORDER_SIGNAL
    from swarm_btc_flow_constants import K_PRICE_SYMBOL
    from swarm_btc_flow_constants import K_SIDE

    synth = {
        K_BTC_USD_PRICE: None,
        K_PRICE_SYMBOL: "BTCUSDT",
        K_ORDER_SIGNAL: "HOLD",
        K_AMOUNT_BTC: "signal only",
        K_LEVERAGE: 5,
        K_SIDE: "FLAT",
        K_BTC_DUMP_RISK_PCT: None,
        K_BULL_BEAR: "MIXED",
    }
    lines = tr_mod.format_swarm_btc_card_lines(synth)
    assert any("BTC/USD price: N/A" in ln for ln in lines)
    assert any("BTC Dump risk %: N/A" in ln for ln in lines)
