"""Tests for session ORB helper (BTC/ETH on 5m-style bars)."""

import pandas as pd

from user_data.strategies import market_sessions_orb as mso


def _make_df_5m_session():
    # Two UTC days, 5m bars: simple rising then breakout in "us" session
    idx = pd.date_range("2026-01-02 13:00", periods=20, freq="5min", tz="UTC")
    base = 100.0
    highs = [base + i * 0.1 for i in range(20)]
    lows = [h - 0.05 for h in highs]
    closes = [h - 0.02 for h in highs]
    # Bar 6: close jumps above ORB high of first 6 bars (first 30m range)
    closes[6] = highs[0] + 1.0
    highs[6] = max(highs[6], closes[6])
    df = pd.DataFrame(
        {
            "date": idx,
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1.0] * 20,
        }
    )
    return df


def test_is_orb_pair_futures_suffix():
    assert mso.is_orb_pair("BTC/USDT:USDT")
    assert mso.is_orb_pair("ETH/USDT")
    assert not mso.is_orb_pair("SOL/USDT:USDT")


def test_attach_orb_columns_non_orb_noop():
    df = _make_df_5m_session()
    out = mso.attach_orb_columns(df.copy(), metadata_pair="SOL/USDT")
    assert (out["orb_break_long"] == False).all()


def test_attach_orb_columns_btc_sets_session_and_breakout():
    df = _make_df_5m_session()
    out = mso.attach_orb_columns(
        df.copy(),
        metadata_pair="BTC/USDT",
        timeframe_minutes=5,
        orb_minutes=30,
        min_range_pct=0.01,
    )
    assert "orb_session" in out.columns
    # ORB formed after first 6 bars of segment; synthetic close clears orb_high vs prior close
    assert bool(out.iloc[6]["orb_formed"])
    assert out.iloc[6]["orb_session"] == "us"
    assert bool(out.iloc[6]["orb_break_long"])
