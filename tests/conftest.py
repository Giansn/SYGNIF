"""Shared fixtures for Sygnif tests."""
import sys
import os
import json
import types
import tempfile

import pytest
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Stub out heavy imports that aren't available outside Docker
# ---------------------------------------------------------------------------

# freqtrade stubs
_ft_strategy = types.ModuleType("freqtrade.strategy")
_ft_strategy.IStrategy = type("IStrategy", (), {
    "dp": None,
    "config": {},
    "INTERFACE_VERSION": 3,
})
_ft_strategy.merge_informative_pair = lambda df, info, tf_base, tf_info, ffill=True: df

_ft_persistence = types.ModuleType("freqtrade.persistence")
_ft_persistence.Trade = type("Trade", (), {"get_trades_proxy": staticmethod(lambda is_open=True: [])})

_ft = types.ModuleType("freqtrade")
sys.modules["freqtrade"] = _ft
sys.modules["freqtrade.strategy"] = _ft_strategy
sys.modules["freqtrade.persistence"] = _ft_persistence

# talib stub — must have __spec__ set for pandas_ta find_spec() check
import importlib.machinery
_talib = types.ModuleType("talib")
_talib.__spec__ = importlib.machinery.ModuleSpec("talib", None)
_talib_abstract = types.ModuleType("talib.abstract")
_talib_abstract.__spec__ = importlib.machinery.ModuleSpec("talib.abstract", None)

def _bbands(close, timeperiod=20, nbdevup=2.0, nbdevdn=2.0, matype=0):
    mid = close.rolling(timeperiod).mean()
    std = close.rolling(timeperiod).std()
    return mid + nbdevup * std, mid, mid - nbdevdn * std

_talib_abstract.BBANDS = _bbands
_talib.abstract = _talib_abstract
sys.modules["talib"] = _talib
sys.modules["talib.abstract"] = _talib_abstract

# feedparser stub
_feedparser = types.ModuleType("feedparser")
_feedparser.parse = lambda url: types.SimpleNamespace(entries=[])
sys.modules["feedparser"] = _feedparser

# cursor_cloud_completion (repo module; optional outside Docker)
_cc = types.ModuleType("cursor_cloud_completion")
_cc.cursor_cloud_completion = lambda *args, **kwargs: ""
sys.modules["cursor_cloud_completion"] = _cc

# Now we can import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def strategy():
    """Create a SygnifStrategy instance with mocked dependencies."""
    from SygnifStrategy import SygnifStrategy

    s = object.__new__(SygnifStrategy)
    # Set class-level defaults manually (IStrategy.__init__ not available)
    s.config = {"trading_mode": "spot", "dry_run": True}
    s.timeframe = "5m"
    s.info_timeframes = ["15m", "1h", "4h", "1d"]
    s.stoploss = -0.20
    s.stop_threshold_doom_spot = 0.20
    s.stop_threshold_doom_futures = 0.20
    s.stop_threshold_doom = 0.20
    s.stop_threshold_normal = 0.10
    s.stop_threshold_futures = 0.10
    s.futures_mode_leverage = 3.0
    s.futures_mode_leverage_majors = 5.0
    s.major_pairs = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
    s.sentiment_threshold_buy = 55.0
    s._doom_cooldown = {}
    s.doom_cooldown_secs = 14400
    s._doom_cooldown_path = os.path.join(tempfile.mkdtemp(), "doom_cooldown.json")
    s.max_slots_sentiment = 6
    s.max_slots_swing = 4
    s._swing_tags = {"swing_failure", "claude_swing", "swing_failure_short", "claude_swing_short"}
    s.can_short = False
    s.dp = None

    # Mock Claude sentiment
    from SygnifStrategy import SygnifSentiment
    s.claude = SygnifSentiment()
    s.claude.api_key = ""  # disable real API calls

    return s


@pytest.fixture
def make_df():
    """Factory to create a mock DataFrame with required columns."""
    def _make(rows=200, **overrides):
        np.random.seed(42)
        close = 100 + np.cumsum(np.random.randn(rows) * 0.5)
        high = close + np.abs(np.random.randn(rows) * 0.3)
        low = close - np.abs(np.random.randn(rows) * 0.3)
        open_ = close + np.random.randn(rows) * 0.1
        volume = np.random.randint(1000, 100000, rows).astype(float)

        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=rows, freq="5min"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })

        # Add commonly expected columns with defaults
        defaults = {
            "RSI_3": 50.0, "RSI_4": 50.0, "RSI_14": 50.0, "RSI_20": 50.0,
            "RSI_3_change_pct": 0.0, "RSI_14_change_pct": 0.0,
            "EMA_3": close, "EMA_9": close, "EMA_12": close,
            "EMA_16": close, "EMA_20": close, "EMA_26": close,
            "EMA_50": close, "EMA_100": close, "EMA_120": close,
            "EMA_200": close,
            "SMA_9": close, "SMA_16": close, "SMA_21": close,
            "SMA_30": close, "SMA_200": close,
            "BBL_20_2.0": close - 2, "BBM_20_2.0": close,
            "BBU_20_2.0": close + 2, "BBB_20_2.0": 4.0, "BBP_20_2.0": 0.5,
            "BBL_40_2.0": close - 3, "BBM_40_2.0": close,
            "BBU_40_2.0": close + 3, "BBB_40_2.0": 6.0, "BBP_40_2.0": 0.5,
            "MFI_14": 50.0, "CMF_20": 0.0,
            "WILLR_14": -50.0, "WILLR_480": -50.0,
            "AROONU_14": 50.0, "AROOND_14": 50.0,
            "STOCHRSIk_14_14_3_3": 50.0, "STOCHRSId_14_14_3_3": 50.0,
            "KST_10_15_20_30_10_10_10_15": 0.0, "KSTs_9": 0.0,
            "CCI_20": 0.0, "ROC_2": 0.0, "ROC_9": 0.0,
            "OBV": 0.0, "OBV_change_pct": 0.0,
            "change_pct": 0.0, "close_delta": 0.0,
            "close_max_6": close, "close_max_12": close, "close_max_48": close,
            "close_min_6": close, "close_min_12": close, "close_min_48": close,
            "volume_sma_25": 50000.0, "ATR_14": 1.0,
            "num_empty_288": 0.0,
            # Informative TF columns
            "RSI_3_5m": 50.0, "RSI_14_5m": 50.0,
            "RSI_3_15m": 50.0, "RSI_14_15m": 50.0,
            "RSI_3_1h": 50.0, "RSI_14_1h": 50.0,
            "RSI_3_4h": 50.0, "RSI_14_4h": 50.0,
            "RSI_14_1d": 50.0,
            "ROC_9_4h": 0.0, "ROC_9_1d": 0.0,
            "AROONU_14_15m": 50.0, "AROONU_14_4h": 50.0,
            "CMF_20_1h": 0.0,
            # BTC columns
            "btc_RSI_3_1h": 50.0, "btc_RSI_14_1h": 50.0,
            "btc_RSI_14_4h": 50.0,
            # Failure swing
            "sf_resistance": high.max(),
            "sf_support": low.min(),
            "sf_resistance_stable": True,
            "sf_support_stable": True,
            "sf_volatility": 0.06,
            "sf_vol_filter": True,
            "sf_long": False, "sf_short": False,
            "sf_sl_pct": 0.03, "sf_tp_ema": close * 1.02,
            # Protections
            "protections_long_global": True,
            "protections_short_global": True,
        }

        for col, val in defaults.items():
            if col not in overrides:
                df[col] = val

        for col, val in overrides.items():
            df[col] = val

        return df

    return _make


@pytest.fixture
def mock_trade():
    """Factory for creating mock Trade objects."""
    def _make(**kwargs):
        defaults = {
            "pair": "BTC/USDT",
            "open_rate": 50000.0,
            "is_short": False,
            "leverage": 1.0,
            "enter_tag": "strong_ta",
            "stake_amount": 10.0,
        }
        defaults.update(kwargs)

        class MockTrade:
            def __init__(self, **kw):
                for k, v in kw.items():
                    setattr(self, k, v)
                self.entry_side = "buy"

            def select_filled_orders(self, side):
                return [{"order_id": "1"}]

        return MockTrade(**defaults)

    return _make
