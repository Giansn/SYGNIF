"""Tests for notification_handler.py — webhook message formatting."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from notification_handler import (
    format_entry_msg,
    format_exit_msg,
    format_status_msg,
    map_exit_reason,
    _fallback_review,
    _calc_duration,
    fmt_coin,
    fmt_price,
)


# ═══════════════════════════════════════════════════════════════════════
# Entry Messages
# ═══════════════════════════════════════════════════════════════════════

class TestEntryMessages:
    def test_spot_order_placed(self):
        msg = {
            "type": "entry",
            "pair": "BTC/USDT",
            "trade_id": 1,
            "open_rate": 50000.0,
            "stake_amount": 10.0,
            "quote_currency": "USDT",
            "enter_tag": "strong_ta",
            "leverage": 1,
            "direction": "Long",
            "is_short": False,
            "trading_mode": "spot",
        }
        text = format_entry_msg(msg)
        assert "Order Placed" in text
        assert "#1" in text
        assert "BTC/USDT" in text
        assert "strong_ta" in text
        assert "Direction" not in text  # no direction for spot

    def test_futures_order_placed(self):
        msg = {
            "type": "entry",
            "pair": "ETH/USDT",
            "trade_id": 2,
            "open_rate": 3000.0,
            "stake_amount": 10.0,
            "quote_currency": "USDT",
            "enter_tag": "fa_s8",
            "leverage": 3,
            "direction": "Long",
            "is_short": False,
            "trading_mode": "futures",
        }
        text = format_entry_msg(msg)
        assert "Order Placed" in text
        assert "Direction" in text
        assert "LONG" in text
        assert "3x" in text

    def test_spot_order_filled(self):
        msg = {
            "type": "entry_fill",
            "pair": "SOL/USDT",
            "trade_id": 3,
            "open_rate": 150.0,
            "stake_amount": 10.0,
            "quote_currency": "USDT",
            "enter_tag": "strong_ta",
            "leverage": 1,
            "direction": "Long",
            "is_short": False,
            "trading_mode": "spot",
        }
        text = format_entry_msg(msg)
        assert "Filled" in text
        assert "TP targets" in text
        assert "SL:" in text
        assert "Expected win" in text
        assert "Possible loss" in text

    def test_swing_failure_uses_tighter_targets(self):
        msg = {
            "type": "entry_fill",
            "pair": "BTC/USDT",
            "trade_id": 4,
            "open_rate": 50000.0,
            "stake_amount": 10.0,
            "quote_currency": "USDT",
            "enter_tag": "swing_failure",
            "leverage": 1,
            "direction": "Long",
            "is_short": False,
            "trading_mode": "spot",
        }
        text = format_entry_msg(msg)
        # Swing failure uses 4% SL, not 10%
        assert "-4%" in text

    def test_futures_short_filled(self):
        msg = {
            "type": "entry_fill",
            "pair": "ETH/USDT",
            "trade_id": 5,
            "open_rate": 3000.0,
            "stake_amount": 10.0,
            "quote_currency": "USDT",
            "enter_tag": "fa_swing_short",
            "leverage": 3,
            "direction": "Short",
            "is_short": True,
            "trading_mode": "futures",
        }
        text = format_entry_msg(msg)
        assert "SHORT" in text
        assert "3x" in text


# ═══════════════════════════════════════════════════════════════════════
# Exit Messages
# ═══════════════════════════════════════════════════════════════════════

class TestExitMessages:
    def test_profitable_exit(self):
        msg = {
            "type": "exit_fill",
            "pair": "BTC/USDT",
            "trade_id": 1,
            "profit_ratio": 0.05,
            "profit_amount": 5.0,
            "open_rate": 50000.0,
            "close_rate": 52500.0,
            "quote_currency": "USDT",
            "exit_reason": "exit_profit_rsi_5.0%",
            "enter_tag": "strong_ta",
            "leverage": 1,
            "direction": "Long",
            "is_short": False,
            "open_date": "2024-01-01T10:00:00+00:00",
            "close_date": "2024-01-01T12:30:00+00:00",
            "trading_mode": "spot",
        }
        text = format_exit_msg(msg)
        assert "Closed" in text
        assert "+5.00%" in text
        assert "2h30m" in text
        assert "Review" in text

    def test_loss_exit(self):
        msg = {
            "type": "exit_fill",
            "pair": "ETH/USDT",
            "trade_id": 2,
            "profit_ratio": -0.10,
            "profit_amount": -1.0,
            "open_rate": 3000.0,
            "close_rate": 2700.0,
            "quote_currency": "USDT",
            "exit_reason": "exit_doom_stoploss",
            "enter_tag": "strong_ta",
            "leverage": 1,
            "direction": "Long",
            "is_short": False,
            "open_date": "2024-01-01T10:00:00+00:00",
            "close_date": "2024-01-01T10:45:00+00:00",
            "trading_mode": "spot",
        }
        text = format_exit_msg(msg)
        assert "Closed" in text
        assert "-10.00%" in text
        assert "Max loss threshold" in text

    def test_non_fill_returns_none(self):
        msg = {"type": "exit", "pair": "BTC/USDT"}
        assert format_exit_msg(msg) is None


# ═══════════════════════════════════════════════════════════════════════
# Exit Reason Mapping
# ═══════════════════════════════════════════════════════════════════════

class TestExitReasonMapping:
    def test_all_known_reasons(self):
        cases = {
            "exit_sf_ema_tp": "EMA target hit",
            "exit_sf_vol_sl": "volatility stop",
            "exit_momentum_fade": "Momentum fade",
            "exit_overbought": "Overbought",
            "exit_oversold": "Oversold",
            "exit_trail_0.05": "Trailing",
            "exit_bounce": "Bounce",
            "exit_secure_profit": "Profit secured",
            "exit_rsi3_spike": "RSI momentum",
            "exit_extreme_rsi": "Extreme RSI",
            "exit_multi_tf": "Multi-TF",
            "exit_bb_stretch": "Bollinger",
            "exit_willr_overbought": "Williams %R",
            "exit_profit_rsi_5%": "RSI profit",
            "exit_doom_stoploss": "Max loss",
            "exit_stoploss_conditional": "Conditional",
            "exit_hard_sl": "Hard stoploss",
        }
        for reason, expected_fragment in cases.items():
            mapped = map_exit_reason(reason)
            assert expected_fragment in mapped, f"{reason} -> {mapped} missing '{expected_fragment}'"

    def test_unknown_reason_passthrough(self):
        assert map_exit_reason("some_custom_exit") == "some_custom_exit"

    def test_empty_reason(self):
        assert map_exit_reason("") == "unknown"
        assert map_exit_reason(None) == "unknown"


# ═══════════════════════════════════════════════════════════════════════
# Fallback Review
# ═══════════════════════════════════════════════════════════════════════

class TestFallbackReview:
    def test_profit_review(self):
        msg = {"profit_ratio": 0.05, "exit_reason": "profit_rsi"}
        review = _fallback_review(msg, "RSI profit lock")
        assert "clean exit" in review

    def test_doom_review(self):
        msg = {"profit_ratio": -0.2, "exit_reason": "exit_doom_stoploss"}
        review = _fallback_review(msg, "Max loss threshold")
        assert "max loss" in review

    def test_loss_review(self):
        msg = {"profit_ratio": -0.05, "exit_reason": "exit_stoploss_conditional"}
        review = _fallback_review(msg, "Conditional stoploss")
        assert "against thesis" in review


# ═══════════════════════════════════════════════════════════════════════
# Duration + Formatting
# ═══════════════════════════════════════════════════════════════════════

class TestFormatting:
    def test_duration_hours(self):
        assert _calc_duration(
            "2024-01-01T10:00:00+00:00",
            "2024-01-01T12:30:00+00:00",
        ) == "2h30m"

    def test_duration_minutes(self):
        assert _calc_duration(
            "2024-01-01T10:00:00+00:00",
            "2024-01-01T10:45:00+00:00",
        ) == "45m"

    def test_duration_seconds(self):
        assert _calc_duration(
            "2024-01-01T10:00:00+00:00",
            "2024-01-01T10:00:30+00:00",
        ) == "30s"

    def test_duration_invalid(self):
        assert _calc_duration("bad", "data") == "--"

    def test_fmt_coin(self):
        assert "10.0000 USDT" == fmt_coin(10.0, "USDT")

    def test_fmt_price_high(self):
        assert "50000.00 USDT" == fmt_price(50000.0, "USDT")

    def test_fmt_price_low(self):
        result = fmt_price(0.0001, "USDT")
        assert "0.000100" in result

    def test_status_running(self):
        msg = {"status": "running"}
        text = format_status_msg(msg)
        assert "System up" in text

    def test_status_stopped(self):
        msg = {"status": "process died"}
        text = format_status_msg(msg)
        assert "System down" in text

    def test_status_other_suppressed(self):
        msg = {"status": "some_other"}
        assert format_status_msg(msg) is None
