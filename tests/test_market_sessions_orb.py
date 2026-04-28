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
    assert not out["orb_break_long"].any()


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
    # SFP overlay columns are present by default and the upside breakout is not an SFP-short
    for col in (
        "orb_break_short",
        "orb_atr",
        "orb_sfp_long",
        "orb_sfp_short",
        "orb_failed_long",
        "orb_failed_short",
    ):
        assert col in out.columns, f"missing {col}"
    assert not bool(out.iloc[6]["orb_sfp_short"])


def _make_df_5m_sfp_long():
    # 30 bars in the "us" session: first 6 form ORB at high≈100.5 / low≈100.0,
    # then a sweep bar wicks below orb_low and closes back inside (bullish SFP).
    idx = pd.date_range("2026-01-02 13:00", periods=30, freq="5min", tz="UTC")
    n = len(idx)
    highs = [100.5] * n
    lows = [100.0] * n
    closes = [100.3] * n
    # Make ORB form cleanly at [100.0, 100.5] in the first 6 bars.
    for i in range(6):
        highs[i] = 100.5
        lows[i] = 100.0
        closes[i] = 100.3
    # Bar 10: sweep below orb_low with deep wick, close back above. ATR is small
    # because prior bars are flat, so any meaningful penetration easily clears
    # the ATR-scaled overshoot threshold.
    sfp_idx = 10
    lows[sfp_idx] = 99.0          # 1.0 below orb_low (well above 0.10 ATR after warm-up)
    highs[sfp_idx] = 100.4
    closes[sfp_idx] = 100.25      # closes back above orb_low (100.0)
    df = pd.DataFrame(
        {
            "date": idx,
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1.0] * n,
        }
    )
    return df, sfp_idx


def test_orb_sfp_long_fires_on_session_low_sweep():
    df, i = _make_df_5m_sfp_long()
    out = mso.attach_orb_columns(
        df.copy(),
        metadata_pair="BTC/USDT",
        timeframe_minutes=5,
        orb_minutes=30,
        min_range_pct=0.01,
    )
    assert bool(out.iloc[i]["orb_formed"])
    assert bool(out.iloc[i]["orb_sfp_long"])
    assert not bool(out.iloc[i]["orb_sfp_short"])


def test_orb_failed_long_after_break_reverts():
    # ORB high ~100.5; bar 7 closes above (orb_break_long); bar 8 closes back
    # below orb_high → orb_failed_long should fire on bar 8.
    idx = pd.date_range("2026-01-02 13:00", periods=20, freq="5min", tz="UTC")
    n = len(idx)
    highs = [100.5] * n
    lows = [100.0] * n
    closes = [100.3] * n
    # Bar 7: clean upside break
    highs[7] = 101.0
    closes[7] = 100.9
    # Bar 8: revert back inside the range
    highs[8] = 100.6
    lows[8] = 100.1
    closes[8] = 100.2
    df = pd.DataFrame(
        {
            "date": idx,
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1.0] * n,
        }
    )
    out = mso.attach_orb_columns(
        df.copy(),
        metadata_pair="BTC/USDT",
        timeframe_minutes=5,
        orb_minutes=30,
        min_range_pct=0.01,
    )
    assert bool(out.iloc[7]["orb_break_long"])
    assert bool(out.iloc[8]["orb_failed_long"])


def test_swing_failure_check_disabled_omits_overlay():
    df = _make_df_5m_session()
    out = mso.attach_orb_columns(
        df.copy(),
        metadata_pair="BTC/USDT",
        timeframe_minutes=5,
        orb_minutes=30,
        min_range_pct=0.01,
        swing_failure_check=False,
    )
    # core columns still present; overlay columns omitted
    assert "orb_break_long" in out.columns
    assert "orb_break_short" in out.columns
    for col in ("orb_sfp_long", "orb_sfp_short", "orb_failed_long", "orb_failed_short", "orb_atr"):
        assert col not in out.columns, f"unexpected overlay column {col}"
