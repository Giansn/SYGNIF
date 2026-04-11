"""Tests for SYGNIF_PROFILE=btc_trend regime helpers (no Freqtrade runtime)."""

import pandas as pd

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "user_data" / "strategies"))
import btc_trend_regime as btr  # noqa: E402


def test_btc_trend_long_row_true_when_all_conditions_met():
    row = pd.Series(
        {
            "RSI_14_1h": 55.0,
            "RSI_14_4h": 52.0,
            "ADX_14": 30.0,
            "close": 100.0,
            "EMA_200_1h": 95.0,
        }
    )
    assert btr.btc_trend_long_row(row) is True


def test_btc_trend_long_row_false_when_rsi_1h_low():
    row = pd.Series(
        {
            "RSI_14_1h": 45.0,
            "RSI_14_4h": 52.0,
            "ADX_14": 30.0,
            "close": 100.0,
            "EMA_200_1h": 95.0,
        }
    )
    assert btr.btc_trend_long_row(row) is False


def test_btc_trend_long_row_false_when_below_ema200_1h():
    row = pd.Series(
        {
            "RSI_14_1h": 55.0,
            "RSI_14_4h": 52.0,
            "ADX_14": 30.0,
            "close": 90.0,
            "EMA_200_1h": 95.0,
        }
    )
    assert btr.btc_trend_long_row(row) is False


def test_btc_trend_long_row_false_when_adx_weak():
    row = pd.Series(
        {
            "RSI_14_1h": 55.0,
            "RSI_14_4h": 52.0,
            "ADX_14": 20.0,
            "close": 100.0,
            "EMA_200_1h": 95.0,
        }
    )
    assert btr.btc_trend_long_row(row) is False


def test_btc_trend_long_series_vectorized():
    df = pd.DataFrame(
        {
            "RSI_14_1h": [55.0, 45.0],
            "RSI_14_4h": [52.0, 52.0],
            "ADX_14": [30.0, 30.0],
            "close": [100.0, 100.0],
            "EMA_200_1h": [95.0, 95.0],
        }
    )
    s = btr.btc_trend_long_series(df)
    assert s.iloc[0] == 1.0
    assert s.iloc[1] == 0.0


def test_is_btc_pair():
    assert btr.is_btc_pair("BTC/USDT") is True
    assert btr.is_btc_pair("BTC/USDT:USDT") is True
    assert btr.is_btc_pair("ETH/USDT") is False
