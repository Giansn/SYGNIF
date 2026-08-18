"""Tests for the engine core: vectors, RNG, and the fixed-timestep loop."""

from __future__ import annotations

import math
import unittest

from gamedev.engine.loop import Accumulator, GameLoop, ScriptedClock
from gamedev.engine.rng import Rng, seed_from_text
from gamedev.engine.vec import Vec2, clamp, lerp, sign


class TestVec2(unittest.TestCase):
    def test_arithmetic(self) -> None:
        a, b = Vec2(3, 4), Vec2(1, 2)
        self.assertEqual(a + b, Vec2(4, 6))
        self.assertEqual(a - b, Vec2(2, 2))
        self.assertEqual(a * 2, Vec2(6, 8))
        self.assertEqual(2 * a, Vec2(6, 8))
        self.assertEqual(a / 2, Vec2(1.5, 2.0))
        self.assertEqual(-a, Vec2(-3, -4))

    def test_length_and_normalize(self) -> None:
        v = Vec2(3, 4)
        self.assertAlmostEqual(v.length(), 5.0)
        self.assertAlmostEqual(v.length_squared(), 25.0)
        self.assertAlmostEqual(v.normalized().length(), 1.0)

    def test_normalizing_zero_returns_zero(self) -> None:
        # Stationary entities get their facing normalised constantly; raising
        # here would force a guard at every call site.
        self.assertEqual(Vec2(0, 0).normalized(), Vec2(0, 0))

    def test_dot_and_cross(self) -> None:
        self.assertAlmostEqual(Vec2(1, 0).dot(Vec2(0, 1)), 0.0)
        self.assertAlmostEqual(Vec2(1, 0).dot(Vec2(1, 0)), 1.0)
        # Cross sign identifies which side a point is on.
        self.assertGreater(Vec2(1, 0).cross(Vec2(0, 1)), 0.0)
        self.assertLess(Vec2(1, 0).cross(Vec2(0, -1)), 0.0)

    def test_reflect_is_a_bounce(self) -> None:
        # A ball travelling down-right hits a floor (normal pointing up).
        velocity = Vec2(3, -4)
        floor_normal = Vec2(0, 1)
        bounced = velocity.reflect(floor_normal)
        self.assertAlmostEqual(bounced.x, 3.0)  # tangential motion preserved
        self.assertAlmostEqual(bounced.y, 4.0)  # normal component flipped
        # Reflection preserves speed.
        self.assertAlmostEqual(bounced.length(), velocity.length())

    def test_clamped_is_terminal_velocity(self) -> None:
        self.assertAlmostEqual(Vec2(30, 40).clamped(5.0).length(), 5.0)
        # Already short enough: returned untouched.
        self.assertEqual(Vec2(1, 0).clamped(5.0), Vec2(1, 0))

    def test_rotation_round_trips(self) -> None:
        v = Vec2(1, 0).rotated(math.pi / 3)
        self.assertAlmostEqual(v.angle(), math.pi / 3)
        back = v.rotated(-math.pi / 3)
        self.assertAlmostEqual(back.x, 1.0)
        self.assertAlmostEqual(back.y, 0.0, places=9)

    def test_from_angle_matches_angle(self) -> None:
        for degrees in range(-180, 181, 15):
            radians = math.radians(degrees)
            self.assertAlmostEqual(Vec2.from_angle(radians).angle(), math.atan2(math.sin(radians), math.cos(radians)))

    def test_immutable_enough_to_share(self) -> None:
        # Two entities can hold the same Vec2 without aliasing bugs, because
        # every operation returns a new object.
        shared = Vec2(1, 1)
        moved = shared + Vec2(5, 0)
        self.assertEqual(shared, Vec2(1, 1))
        self.assertEqual(moved, Vec2(6, 1))

    def test_scalar_helpers(self) -> None:
        self.assertEqual(clamp(5, 0, 3), 3)
        self.assertEqual(clamp(-5, 0, 3), 0)
        self.assertEqual(clamp(2, 0, 3), 2)
        self.assertAlmostEqual(lerp(0, 10, 0.25), 2.5)
        self.assertEqual(sign(-3.2), -1.0)
        self.assertEqual(sign(0.0), 0.0)


class TestRng(unittest.TestCase):
    # The canonical PCG32 demo output for seed=42, stream=54. Matching it
    # proves this is really PCG32 and not merely "some generator".
    REFERENCE = [0xA15C02B7, 0x7B47F409, 0xBA1D3330, 0x83D2F293, 0xBFA4784B, 0xCBED606E]

    def test_matches_reference_implementation(self) -> None:
        rng = Rng(seed=42, stream=54)
        self.assertEqual([rng.next_uint32() for _ in range(6)], self.REFERENCE)

    def test_same_seed_same_sequence(self) -> None:
        a, b = Rng(seed=1234), Rng(seed=1234)
        self.assertEqual([a.random() for _ in range(50)], [b.random() for _ in range(50)])

    def test_different_seeds_differ(self) -> None:
        a, b = Rng(seed=1), Rng(seed=2)
        self.assertNotEqual([a.random() for _ in range(20)], [b.random() for _ in range(20)])

    def test_streams_are_independent(self) -> None:
        # Same seed, different stream: distinct sequences. This is what lets
        # terrain and loot share a world seed without interfering.
        a, b = Rng(seed=7, stream=1), Rng(seed=7, stream=2)
        self.assertNotEqual([a.random() for _ in range(20)], [b.random() for _ in range(20)])

    def test_random_is_in_unit_interval(self) -> None:
        rng = Rng(seed=99)
        for _ in range(10_000):
            value = rng.random()
            self.assertGreaterEqual(value, 0.0)
            self.assertLess(value, 1.0)

    def test_below_stays_in_range(self) -> None:
        rng = Rng(seed=5)
        for bound in (1, 2, 6, 52, 1000):
            for _ in range(2_000):
                self.assertIn(rng.below(bound), range(bound))

    def test_below_is_close_to_uniform(self) -> None:
        # Rejection sampling should leave no visible skew across buckets.
        rng = Rng(seed=17)
        buckets = [0] * 6
        rolls = 60_000
        for _ in range(rolls):
            buckets[rng.below(6)] += 1
        expected = rolls / 6
        for count in buckets:
            self.assertLess(abs(count - expected) / expected, 0.05, f"skewed: {buckets}")

    def test_randint_is_inclusive_at_both_ends(self) -> None:
        rng = Rng(seed=3)
        seen = {rng.randint(1, 3) for _ in range(500)}
        self.assertEqual(seen, {1, 2, 3})

    def test_shuffle_is_a_permutation_and_reproducible(self) -> None:
        original = list(range(50))
        a, b = list(original), list(original)
        Rng(seed=8).shuffle(a)
        Rng(seed=8).shuffle(b)
        self.assertEqual(a, b)
        self.assertEqual(sorted(a), original)
        self.assertNotEqual(a, original)

    def test_shuffle_has_no_positional_bias(self) -> None:
        # A biased shuffle (picking from the whole list each step) shows up as
        # some element favouring some slot. Check element 0's landing spot is
        # spread evenly across a 4-slot list.
        rng = Rng(seed=21)
        landings = [0] * 4
        trials = 20_000
        for _ in range(trials):
            deck = [0, 1, 2, 3]
            rng.shuffle(deck)
            landings[deck.index(0)] += 1
        expected = trials / 4
        for count in landings:
            self.assertLess(abs(count - expected) / expected, 0.06, f"biased: {landings}")

    def test_weighted_choice_respects_weights(self) -> None:
        rng = Rng(seed=11)
        counts = {"common": 0, "rare": 0}
        for _ in range(20_000):
            counts[rng.weighted_choice(["common", "rare"], [9.0, 1.0])] += 1
        ratio = counts["rare"] / 20_000
        self.assertAlmostEqual(ratio, 0.1, delta=0.015)

    def test_state_round_trip_replays_exactly(self) -> None:
        rng = Rng(seed=404)
        [rng.random() for _ in range(10)]
        snapshot = rng.state()
        expected = [rng.random() for _ in range(10)]
        rng.restore(snapshot)
        self.assertEqual([rng.random() for _ in range(10)], expected)

    def test_fork_isolates_subsystems(self) -> None:
        # The point of forking: draws made by one subsystem must not shift
        # what another subsystem generates.
        terrain_a = Rng(seed=2024).fork(stream=1)
        loot_a = Rng(seed=2024).fork(stream=2)
        expected_loot = [loot_a.below(100) for _ in range(10)]

        # Now pretend terrain generation got more elaborate and draws more.
        terrain_b = Rng(seed=2024).fork(stream=1)
        [terrain_b.random() for _ in range(500)]
        loot_b = Rng(seed=2024).fork(stream=2)
        self.assertEqual([loot_b.below(100) for _ in range(10)], expected_loot)
        # And the two forks are genuinely different streams.
        self.assertNotEqual(terrain_a.below(1000), loot_a.below(1000))

    def test_seed_from_text_is_stable_across_processes(self) -> None:
        # Unlike hash(), which Python randomises per process.
        self.assertEqual(seed_from_text("cavern"), seed_from_text("cavern"))
        self.assertNotEqual(seed_from_text("cavern"), seed_from_text("caverm"))
        self.assertEqual(seed_from_text(""), 0xCBF29CE484222325)

    def test_gauss_has_expected_moments(self) -> None:
        rng = Rng(seed=1)
        samples = [rng.gauss(10.0, 2.0) for _ in range(20_000)]
        mean = sum(samples) / len(samples)
        variance = sum((s - mean) ** 2 for s in samples) / len(samples)
        self.assertAlmostEqual(mean, 10.0, delta=0.1)
        self.assertAlmostEqual(math.sqrt(variance), 2.0, delta=0.1)


class TestAccumulator(unittest.TestCase):
    def test_exact_tick_counts(self) -> None:
        acc = Accumulator(dt=1.0 / 60.0)
        self.assertEqual(acc.feed(1.0 / 60.0), 1)
        self.assertEqual(acc.feed(4.0 / 60.0), 4)
        self.assertEqual(acc.feed(0.0), 0)

    def test_partial_frames_accumulate_into_whole_ticks(self) -> None:
        # A 90 Hz frame is *shorter* than a 60 Hz tick, so the first one
        # cannot produce a tick at all — it banks 0.67 of one. Three such
        # frames are exactly two ticks' worth of time.
        acc = Accumulator(dt=1.0 / 60.0)
        self.assertEqual(acc.feed(1.0 / 90.0), 0)
        self.assertEqual(acc.feed(1.0 / 90.0), 1)
        self.assertEqual(acc.feed(1.0 / 90.0), 1)

    def test_half_rate_display_yields_exactly_two_ticks(self) -> None:
        # The case the snap tolerance exists for: 30 Hz frames against a
        # 60 Hz tick must be a steady 2 ticks each, never an occasional 1.
        acc = Accumulator(dt=1.0 / 60.0)
        self.assertEqual([acc.feed(1.0 / 30.0) for _ in range(600)], [2] * 600)

    def test_alpha_is_the_leftover_fraction(self) -> None:
        acc = Accumulator(dt=1.0 / 60.0)
        acc.feed(1.5 / 60.0)
        self.assertAlmostEqual(acc.alpha, 0.5, places=9)
        self.assertGreaterEqual(acc.alpha, 0.0)
        self.assertLess(acc.alpha, 1.0)

    def test_no_drift_over_many_frames(self) -> None:
        # Float error in the accumulator must not lose or invent ticks over a
        # long session; 10 minutes at 60 Hz should be exactly 36000 ticks.
        acc = Accumulator(dt=1.0 / 60.0)
        total = sum(acc.feed(1.0 / 60.0) for _ in range(36_000))
        self.assertEqual(total, 36_000)

    def test_irregular_frames_still_conserve_time(self) -> None:
        acc = Accumulator(dt=1.0 / 60.0)
        rng = Rng(seed=42)
        ticks = 0
        wall = 0.0
        for _ in range(5_000):
            frame = rng.uniform(0.001, 0.05)
            wall += frame
            ticks += acc.feed(frame)
        # Simulated time tracks real time to within the one partial tick that
        # is legitimately still sitting in the accumulator.
        self.assertLess(abs(ticks * (1.0 / 60.0) - wall), 1.0 / 60.0)

    def test_clamp_prevents_the_spiral_of_death(self) -> None:
        # A 10-second stall must not queue 600 ticks; that would take longer
        # to simulate than the stall itself and the game would never recover.
        acc = Accumulator(dt=1.0 / 60.0, max_frame_time=0.25)
        ticks = acc.feed(10.0)
        self.assertEqual(ticks, 15)  # 0.25s worth, not 10s worth
        self.assertAlmostEqual(acc.dropped_time, 9.75, places=6)

    def test_rejects_nonsense_configuration(self) -> None:
        with self.assertRaises(ValueError):
            Accumulator(dt=0.0)
        with self.assertRaises(ValueError):
            Accumulator(dt=1.0 / 60.0, max_frame_time=0.001)
        with self.assertRaises(ValueError):
            Accumulator().feed(-1.0)


class _Projectile:
    """A body under gravity, integrated with semi-implicit Euler."""

    def __init__(self) -> None:
        self.position = Vec2(0.0, 0.0)
        self.velocity = Vec2(10.0, 20.0)
        self.running = True
        self.renders = 0

    def update(self, dt: float) -> None:
        self.velocity = self.velocity + Vec2(0.0, -9.81) * dt
        self.position = self.position + self.velocity * dt

    def render(self, alpha: float) -> None:
        self.renders += 1


class TestFixedTimestep(unittest.TestCase):
    def test_variable_timestep_changes_the_physics(self) -> None:
        """The bug the fixed timestep exists to prevent.

        The same one second of gravity, integrated at two frame rates, lands
        the projectile in two different places. In a real game that is the
        difference between clearing a jump and not.
        """
        coarse = _Projectile()
        for _ in range(15):
            coarse.update(1.0 / 15.0)

        fine = _Projectile()
        for _ in range(240):
            fine.update(1.0 / 240.0)

        self.assertAlmostEqual(coarse.position.x, fine.position.x, places=6)  # no accel in x
        # But the vertical position, which gravity acts on, disagrees.
        self.assertGreater(abs(coarse.position.y - fine.position.y), 0.1)

    def test_fixed_timestep_is_frame_rate_independent(self) -> None:
        """The fix: identical simulation from wildly different frame rates."""
        smooth = _Projectile()
        GameLoop(smooth, tick_rate=60.0, clock=ScriptedClock([1.0 / 60.0] * 61), sleep=None).run(max_frames=60)

        stuttery = _Projectile()
        GameLoop(stuttery, tick_rate=60.0, clock=ScriptedClock([4.0 / 60.0] * 16), sleep=None).run(max_frames=15)

        # Both consumed one second of wall time, so both ran 60 ticks and
        # arrived at bit-identical state despite one rendering 4x as often.
        self.assertEqual(smooth.position, stuttery.position)
        self.assertEqual(smooth.velocity, stuttery.velocity)
        self.assertEqual(smooth.renders, 60)
        self.assertEqual(stuttery.renders, 15)

    def test_headless_matches_realtime(self) -> None:
        realtime = _Projectile()
        GameLoop(realtime, tick_rate=60.0, clock=ScriptedClock([1.0 / 60.0] * 121), sleep=None).run(max_frames=120)

        headless = _Projectile()
        GameLoop(headless, tick_rate=60.0).run_headless(ticks=120)

        self.assertEqual(realtime.position, headless.position)

    def test_loop_stops_when_simulation_ends(self) -> None:
        class Suicidal(_Projectile):
            def update(self, dt: float) -> None:
                super().update(dt)
                if self.position.y < 0.0:
                    self.running = False

        sim = Suicidal()
        stats = GameLoop(sim, tick_rate=60.0).run_headless(ticks=100_000)
        self.assertFalse(sim.running)
        self.assertLess(stats.ticks, 100_000)
        self.assertLess(sim.position.y, 0.0)

    def test_stats_are_recorded(self) -> None:
        sim = _Projectile()
        stats = GameLoop(sim, tick_rate=60.0, clock=ScriptedClock([1.0 / 30.0] * 31), sleep=None).run(max_frames=30)
        self.assertEqual(stats.frames, 30)
        self.assertEqual(stats.ticks, 60)
        self.assertAlmostEqual(stats.ticks_per_frame, 2.0)
        self.assertAlmostEqual(stats.sim_time, 1.0, places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
