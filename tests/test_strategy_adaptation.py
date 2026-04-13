"""Tests for runtime strategy adaptation (JSON bounds)."""

import sys
from pathlib import Path

ud = Path(__file__).resolve().parent.parent / "user_data"
sys.path.insert(0, str(ud))
import strategy_adaptation as sa  # noqa: E402


class TestValidateOverrides:
    def test_clamps_slots(self):
        o = sa.validate_overrides({"max_slots_strong": 99, "max_slots_swing": 1})
        assert o["max_slots_strong"] == 10
        assert o["max_slots_swing"] == 2

    def test_unknown_key_dropped(self):
        o = sa.validate_overrides({"not_a_real_key": 1})
        assert o == {}

    def test_apply_resets_then_overrides(self):
        class S:
            max_slots_strong = 6

        s = S()
        sa.apply_defaults_and_overrides(s, {"max_slots_strong": 4})
        assert s.max_slots_strong == 4
        assert s.max_slots_strong_short == sa.DEFAULTS["max_slots_strong_short"]
        assert s.sentiment_threshold_buy == sa.DEFAULTS["sentiment_threshold_buy"]
