"""Tests for SygnifStrategy — verifies all entry/exit paths, indicators, and thresholds."""
import json
import os
import time

import numpy as np
import pandas as pd
import pytest


# ═══════════════════════════════════════════════════════════════════════
# TA Score
# ═══════════════════════════════════════════════════════════════════════

class TestTAScore:
    def test_neutral_returns_50(self, strategy, make_df):
        df = make_df(RSI_14=50.0, RSI_3=50.0, CMF_20=0.0)
        # EMA_9 == EMA_26 (both = close) → bearish branch (-7)
        score = strategy._calculate_ta_score_vectorized(df)
        assert 40 <= score.iloc[-1] <= 60

    def test_max_bullish(self, strategy, make_df):
        df = make_df(
            RSI_14=25.0, RSI_3=8.0,
            CMF_20=0.20,
            AROONU_14=85.0, AROOND_14=20.0,
            STOCHRSIk_14_14_3_3=15.0,
            RSI_14_1h=30.0, RSI_14_4h=35.0,
            btc_RSI_14_1h=65.0,
        )
        # Force EMA cross
        df["EMA_9"] = df["close"] + 1
        df["EMA_26"] = df["close"] - 1
        df.loc[df.index[0], "EMA_9"] = df["close"].iloc[0] - 1  # prev no cross
        # High volume
        df["volume"] = df["volume_sma_25"] * 2.0

        score = strategy._calculate_ta_score_vectorized(df)
        assert score.iloc[-1] >= 85

    def test_max_bearish(self, strategy, make_df):
        df = make_df(
            RSI_14=75.0, RSI_3=92.0,
            CMF_20=-0.20,
            AROONU_14=20.0, AROOND_14=85.0,
            STOCHRSIk_14_14_3_3=85.0,
            RSI_14_1h=75.0, RSI_14_4h=70.0,
            btc_RSI_14_1h=25.0,
        )
        df["EMA_9"] = df["close"] - 1
        df["EMA_26"] = df["close"] + 1
        df["volume"] = df["volume_sma_25"] * 2.0

        score = strategy._calculate_ta_score_vectorized(df)
        assert score.iloc[-1] <= 15

    def test_score_clipped_0_100(self, strategy, make_df):
        df = make_df(RSI_14=10.0, RSI_3=5.0)
        score = strategy._calculate_ta_score_vectorized(df)
        assert score.min() >= 0
        assert score.max() <= 100


# ═══════════════════════════════════════════════════════════════════════
# Global Protections
# ═══════════════════════════════════════════════════════════════════════

class TestGlobalProtections:
    def test_normal_conditions_allow_entry(self, strategy, make_df):
        df = make_df()
        prot = strategy._calc_global_protections(df)
        assert prot.iloc[-1] == True

    def test_crash_blocks_entry(self, strategy, make_df):
        df = make_df(
            RSI_3=1.0, RSI_3_15m=5.0, RSI_3_1h=8.0,
            RSI_14_1h=60.0,
        )
        prot = strategy._calc_global_protections(df)
        assert prot.iloc[-1] == False

    def test_btc_crash_blocks_entry(self, strategy, make_df):
        df = make_df(btc_RSI_3_1h=5.0, btc_RSI_14_4h=50.0)
        prot = strategy._calc_global_protections(df)
        assert prot.iloc[-1] == False

    def test_short_protections_normal_allow(self, strategy, make_df):
        df = make_df()
        prot = strategy._calc_global_protections_short(df)
        assert prot.iloc[-1] == True

    def test_pump_blocks_shorts(self, strategy, make_df):
        df = make_df(
            RSI_3=96.0, RSI_3_15m=90.0, RSI_3_1h=80.0,
            RSI_14_1h=45.0,
        )
        prot = strategy._calc_global_protections_short(df)
        assert prot.iloc[-1] == False


# ═══════════════════════════════════════════════════════════════════════
# Entry Paths
# ═══════════════════════════════════════════════════════════════════════

class TestEntryPaths:
    def test_strong_ta_entry(self, strategy, make_df):
        """ta_score >= 55 should trigger strong_ta entry."""
        df = make_df(
            RSI_14=25.0, RSI_3=8.0,
            CMF_20=0.20,
            AROONU_14=85.0, AROOND_14=20.0,
            STOCHRSIk_14_14_3_3=15.0,
        )
        df["EMA_9"] = df["close"] + 1
        df["EMA_26"] = df["close"] - 1

        result = strategy.populate_entry_trend(df, {"pair": "BTC/USDT"})
        # At least one strong_ta entry
        strong = result[result["enter_tag"] == "strong_ta"]
        assert len(strong) > 0

    def test_no_entry_when_protections_fail(self, strategy, make_df):
        df = make_df(
            RSI_14=25.0, RSI_3=1.0,  # would be bullish but...
            RSI_3_15m=5.0, RSI_3_1h=8.0, RSI_14_1h=60.0,  # crash protection triggers
            protections_long_global=False,
        )
        result = strategy.populate_entry_trend(df, {"pair": "ETH/USDT"})
        assert result["enter_long"].sum() == 0

    def test_failure_swing_long_entry(self, strategy, make_df):
        df = make_df()
        # Set last candle as failure swing signal
        df.loc[df.index[-1], "fs_long"] = True
        df.loc[df.index[-1], "RSI_14"] = 55.0  # ta_score will be ~50+

        result = strategy.populate_entry_trend(df, {"pair": "SOL/USDT"})
        last = result.iloc[-1]
        assert last["enter_long"] == 1
        assert last["enter_tag"] in ("swing_failure", "claude_swing")

    def test_failure_swing_short_entry(self, strategy, make_df):
        strategy.can_short = True
        df = make_df()
        df.loc[df.index[-1], "fs_short"] = True
        # Low TA score for short confluence
        df.loc[df.index[-1], "RSI_14"] = 75.0
        df.loc[df.index[-1], "RSI_3"] = 92.0

        result = strategy.populate_entry_trend(df, {"pair": "ETH/USDT"})
        last = result.iloc[-1]
        assert last["enter_short"] == 1
        assert last["enter_tag"] in ("swing_failure_short", "claude_swing_short", "strong_ta_short")


# ═══════════════════════════════════════════════════════════════════════
# Exit RSI Tiers
# ═══════════════════════════════════════════════════════════════════════

class TestExitRSITiers:
    @pytest.mark.parametrize("profit,expected_min,expected_max", [
        (0.005, 10, 14),
        (0.015, 28, 32),
        (0.025, 30, 34),
        (0.035, 32, 36),
        (0.045, 34, 38),
        (0.055, 36, 40),
        (0.07, 38, 42),
        (0.09, 42, 46),
        (0.11, 46, 50),
        (0.15, 44, 48),
        (0.25, 42, 46),
    ])
    def test_long_rsi_threshold(self, strategy, profit, expected_min, expected_max):
        threshold = strategy._get_exit_rsi_threshold(profit, above_ema200=True)
        assert expected_min <= threshold <= expected_max

    def test_below_ema200_adds_offset(self, strategy):
        above = strategy._get_exit_rsi_threshold(0.05, above_ema200=True)
        below = strategy._get_exit_rsi_threshold(0.05, above_ema200=False)
        assert below > above

    @pytest.mark.parametrize("profit,expected_min,expected_max", [
        (0.005, 88, 92),
        (0.015, 70, 74),
        (0.025, 68, 72),
        (0.035, 66, 70),
        (0.045, 64, 68),
        (0.055, 62, 66),
        (0.07, 60, 64),
        (0.09, 56, 60),
        (0.11, 52, 56),
        (0.15, 54, 58),
        (0.25, 56, 60),
    ])
    def test_short_rsi_threshold(self, strategy, profit, expected_min, expected_max):
        threshold = strategy._get_short_exit_rsi_threshold(profit, below_ema200=True)
        assert expected_min <= threshold <= expected_max


# ═══════════════════════════════════════════════════════════════════════
# Exit Paths
# ═══════════════════════════════════════════════════════════════════════

class TestExitPaths:
    def test_willr_overbought_exit(self, strategy, make_df, mock_trade):
        trade = mock_trade(enter_tag="strong_ta")
        df = make_df(WILLR_14=-3.0, RSI_14=50.0)

        strategy.dp = type("DP", (), {
            "get_analyzed_dataframe": lambda self, pair, tf: (df, None)
        })()

        result = strategy.custom_exit(
            "BTC/USDT", trade, None, 51000.0, 0.03, after_fill=False,
        )
        assert result == "exit_willr_overbought"

    def test_swing_failure_tp(self, strategy, make_df, mock_trade):
        trade = mock_trade(enter_tag="swing_failure", open_rate=100.0)
        last = pd.Series({"fs_tp_ema": 102.0, "fs_sl_pct": 0.03})

        result = strategy._exit_swing_failure(last, 103.0, trade, 0.03)
        assert result == "exit_sf_ema_tp"

    def test_swing_failure_sl(self, strategy, make_df, mock_trade):
        trade = mock_trade(enter_tag="swing_failure", open_rate=100.0)
        last = pd.Series({"fs_tp_ema": 102.0, "fs_sl_pct": 0.03})

        result = strategy._exit_swing_failure(last, 96.0, trade, -0.04)
        assert result == "exit_sf_vol_sl"

    def test_swing_failure_short_tp(self, strategy, mock_trade):
        trade = mock_trade(enter_tag="swing_failure_short", open_rate=100.0, is_short=True)
        last = pd.Series({"fs_tp_ema": 98.0, "fs_sl_pct": 0.03})

        result = strategy._exit_swing_failure(last, 97.0, trade, 0.03)
        assert result == "exit_sf_short_ema_tp"

    def test_swing_failure_short_sl(self, strategy, mock_trade):
        trade = mock_trade(enter_tag="swing_failure_short", open_rate=100.0, is_short=True)
        last = pd.Series({"fs_tp_ema": 98.0, "fs_sl_pct": 0.03})

        result = strategy._exit_swing_failure(last, 104.0, trade, -0.04)
        assert result == "exit_sf_short_vol_sl"

    def test_no_exit_when_neutral(self, strategy, make_df, mock_trade):
        trade = mock_trade(enter_tag="strong_ta")
        df = make_df(WILLR_14=-50.0, RSI_14=50.0)

        strategy.dp = type("DP", (), {
            "get_analyzed_dataframe": lambda self, pair, tf: (df, None)
        })()

        result = strategy.custom_exit(
            "BTC/USDT", trade, None, 50000.0, 0.001, after_fill=False,
        )
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# Leverage
# ═══════════════════════════════════════════════════════════════════════

class TestLeverage:
    def test_major_pair_5x(self, strategy, make_df):
        df = make_df(ATR_14=0.5)
        strategy.dp = type("DP", (), {
            "get_analyzed_dataframe": lambda self, pair, tf: (df, None)
        })()

        lev = strategy.leverage(
            "BTC/USDT", None, 50000.0, 5.0, 10.0, "strong_ta", "long",
        )
        assert lev == 5.0

    def test_alt_pair_3x(self, strategy, make_df):
        df = make_df(ATR_14=0.5)
        strategy.dp = type("DP", (), {
            "get_analyzed_dataframe": lambda self, pair, tf: (df, None)
        })()

        lev = strategy.leverage(
            "DOGE/USDT", None, 0.15, 3.0, 10.0, "strong_ta", "long",
        )
        assert lev == 3.0

    def test_high_atr_caps_leverage(self, strategy, make_df):
        df = make_df()
        # Set ATR to 3.5% of close
        df["ATR_14"] = df["close"] * 0.035
        strategy.dp = type("DP", (), {
            "get_analyzed_dataframe": lambda self, pair, tf: (df, None)
        })()

        lev = strategy.leverage(
            "BTC/USDT", None, 50000.0, 5.0, 10.0, "strong_ta", "long",
        )
        assert lev == 2.0

    def test_max_leverage_cap(self, strategy, make_df):
        df = make_df(ATR_14=0.5)
        strategy.dp = type("DP", (), {
            "get_analyzed_dataframe": lambda self, pair, tf: (df, None)
        })()

        lev = strategy.leverage(
            "BTC/USDT", None, 50000.0, 5.0, 3.0, "strong_ta", "long",
        )
        assert lev == 3.0  # capped by max_leverage


# ═══════════════════════════════════════════════════════════════════════
# Custom Stoploss
# ═══════════════════════════════════════════════════════════════════════

class TestCustomStoploss:
    def test_spot_stoploss(self, strategy, mock_trade):
        strategy.config = {"trading_mode": "spot"}
        trade = mock_trade(leverage=1.0)

        sl = strategy.custom_stoploss(
            "BTC/USDT", trade, None, 50000.0, -0.05, after_fill=False,
        )
        assert sl == pytest.approx(-0.20)

    def test_futures_stoploss_divided_by_leverage(self, strategy, mock_trade):
        strategy.config = {"trading_mode": "futures"}
        trade = mock_trade(leverage=5.0)

        sl = strategy.custom_stoploss(
            "BTC/USDT", trade, None, 50000.0, -0.02, after_fill=False,
        )
        assert sl == pytest.approx(-0.04)  # -0.20 / 5


# ═══════════════════════════════════════════════════════════════════════
# Doom Cooldown
# ═══════════════════════════════════════════════════════════════════════

class TestDoomCooldown:
    def test_blocks_entry_within_cooldown(self, strategy):
        strategy._doom_cooldown["BTC/USDT"] = time.time()

        result = strategy.confirm_trade_entry(
            "BTC/USDT", "limit", 0.001, 50000, "GTC", None, "strong_ta", "buy",
        )
        assert result == False

    def test_allows_entry_after_cooldown(self, strategy):
        strategy._doom_cooldown["BTC/USDT"] = time.time() - 20000  # >4h ago

        result = strategy.confirm_trade_entry(
            "BTC/USDT", "limit", 0.001, 50000, "GTC", None, "strong_ta", "buy",
        )
        assert result == True

    def test_allows_entry_different_pair(self, strategy):
        strategy._doom_cooldown["BTC/USDT"] = time.time()

        result = strategy.confirm_trade_entry(
            "ETH/USDT", "limit", 0.01, 3000, "GTC", None, "strong_ta", "buy",
        )
        assert result == True

    def test_persistence_save_load(self, strategy):
        strategy._doom_cooldown["SOL/USDT"] = time.time()
        strategy._save_doom_cooldown()

        assert os.path.exists(strategy._doom_cooldown_path)

        strategy._doom_cooldown = {}
        strategy._load_doom_cooldown()
        assert "SOL/USDT" in strategy._doom_cooldown

    def test_persistence_skips_expired(self, strategy):
        strategy._doom_cooldown["OLD/USDT"] = time.time() - 20000
        strategy._save_doom_cooldown()

        strategy._doom_cooldown = {}
        strategy._load_doom_cooldown()
        assert "OLD/USDT" not in strategy._doom_cooldown


# ═══════════════════════════════════════════════════════════════════════
# Sentiment
# ═══════════════════════════════════════════════════════════════════════

class TestSentiment:
    def test_cache_returns_cached(self, strategy):
        import time as t
        strategy.claude._cache["BTC"] = (t.time(), 10.0)
        result = strategy.claude._get_cached("BTC")
        assert result == 10.0

    def test_cache_expired(self, strategy):
        import time as t
        strategy.claude._cache["BTC"] = (t.time() - 1000, 10.0)
        result = strategy.claude._get_cached("BTC")
        assert result is None

    def test_no_api_key_returns_zero(self, strategy):
        strategy.claude.api_key = ""
        result = strategy.claude.analyze_sentiment("BTC", 50000.0, 50.0, [])
        assert result == 0.0

    def test_daily_limit_returns_zero(self, strategy):
        strategy.claude.api_key = "test-key"
        strategy.claude.daily_calls = 50
        result = strategy.claude.analyze_sentiment("BTC", 50000.0, 50.0, [])
        assert result == 0.0

    def test_daily_counter_resets(self, strategy):
        from datetime import date, timedelta
        strategy.claude.daily_calls = 50
        strategy.claude._last_reset = date.today() - timedelta(days=1)
        strategy.claude._reset_daily_counter()
        assert strategy.claude.daily_calls == 0


# ═══════════════════════════════════════════════════════════════════════
# Slot Caps
# ═══════════════════════════════════════════════════════════════════════

class TestSlotCaps:
    def _make_open_trades(self, tags):
        """Create mock open trades with given enter_tags."""
        trades = []
        for tag in tags:
            t = type("T", (), {"enter_tag": tag, "pair": "X/USDT"})()
            trades.append(t)
        return trades

    def test_swing_cap_blocks_when_full(self, strategy):
        from freqtrade.persistence import Trade
        trades = self._make_open_trades(["swing_failure", "claude_swing", "swing_failure", "claude_swing"])
        Trade.get_trades_proxy = staticmethod(lambda is_open=True: trades)
        result = strategy.confirm_trade_entry(
            "NEW/USDT", "limit", 10, 1.0, "GTC", None, "swing_failure", "long")
        assert result is False

    def test_swing_cap_allows_under_limit(self, strategy):
        from freqtrade.persistence import Trade
        trades = self._make_open_trades(["swing_failure", "claude_swing"])
        Trade.get_trades_proxy = staticmethod(lambda is_open=True: trades)
        result = strategy.confirm_trade_entry(
            "NEW/USDT", "limit", 10, 1.0, "GTC", None, "claude_swing", "long")
        assert result is True

    def test_strong_ta_cap_blocks_when_full(self, strategy):
        from freqtrade.persistence import Trade
        trades = self._make_open_trades(["strong_ta"] * 6)
        Trade.get_trades_proxy = staticmethod(lambda is_open=True: trades)
        result = strategy.confirm_trade_entry(
            "NEW/USDT", "limit", 10, 1.0, "GTC", None, "strong_ta", "long")
        assert result is False

    def test_strong_ta_cap_allows_under_limit(self, strategy):
        from freqtrade.persistence import Trade
        trades = self._make_open_trades(["strong_ta"] * 4)
        Trade.get_trades_proxy = staticmethod(lambda is_open=True: trades)
        result = strategy.confirm_trade_entry(
            "NEW/USDT", "limit", 10, 1.0, "GTC", None, "strong_ta", "long")
        assert result is True

    def test_strong_full_still_allows_swing(self, strategy):
        from freqtrade.persistence import Trade
        trades = self._make_open_trades(["strong_ta"] * 6)
        Trade.get_trades_proxy = staticmethod(lambda is_open=True: trades)
        result = strategy.confirm_trade_entry(
            "NEW/USDT", "limit", 10, 1.0, "GTC", None, "swing_failure", "long")
        assert result is True

    def test_swing_full_still_allows_strong(self, strategy):
        from freqtrade.persistence import Trade
        trades = self._make_open_trades(["swing_failure"] * 4)
        Trade.get_trades_proxy = staticmethod(lambda is_open=True: trades)
        result = strategy.confirm_trade_entry(
            "NEW/USDT", "limit", 10, 1.0, "GTC", None, "strong_ta", "long")
        assert result is True


# ═══════════════════════════════════════════════════════════════════════
# Info Timeframes config
# ═══════════════════════════════════════════════════════════════════════

class TestConfig:
    def test_5m_not_in_info_timeframes(self, strategy):
        """5m is the base timeframe — not needed as informative."""
        assert "5m" not in strategy.info_timeframes

    def test_all_timeframes_present(self, strategy):
        for tf in ["15m", "1h", "4h", "1d"]:
            assert tf in strategy.info_timeframes
