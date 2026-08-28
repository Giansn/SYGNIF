"""Tests for collision detection and response.

The headline test is :meth:`TestTunnelling.test_discrete_collision_misses_a_fast_mover`,
which reproduces the bug continuous detection exists to fix, followed by the
proof that the swept test catches it.

There is also a brute-force cross-check: for randomised box pairs, the
analytic swept result is compared against sampling the motion at ten thousand
points. Analytic geometry is easy to get subtly wrong and hard to eyeball, so
it is worth checking against a stupid method that is obviously right.
"""

from __future__ import annotations

import math
import unittest

from gamedev.engine.collision import (
    AABB,
    aabb_overlap,
    circle_overlap,
    circle_vs_aabb,
    minimum_translation,
    move_and_slide,
    ray_vs_aabb,
    sweep_against,
    swept_aabb,
)
from gamedev.engine.rng import Rng
from gamedev.engine.vec import Vec2


class TestAABB(unittest.TestCase):
    def test_edges_and_centre(self) -> None:
        box = AABB(10, 20, 30, 40)
        self.assertEqual((box.left, box.right), (10, 40))
        self.assertEqual((box.top, box.bottom), (20, 60))
        self.assertEqual(box.center, Vec2(25, 40))
        self.assertEqual(box.half, Vec2(15, 20))

    def test_from_center_round_trips(self) -> None:
        box = AABB.from_center(Vec2(100, 50), 20, 10)
        self.assertEqual(box.center, Vec2(100, 50))
        self.assertEqual((box.left, box.top), (90, 45))

    def test_translated_and_expanded(self) -> None:
        box = AABB(0, 0, 10, 10)
        self.assertEqual(box.translated(Vec2(5, -5)), AABB(5, -5, 10, 10))
        self.assertEqual(box.expanded(2), AABB(-2, -2, 14, 14))

    def test_union_covers_both(self) -> None:
        merged = AABB(0, 0, 10, 10).union(AABB(20, -5, 10, 10))
        self.assertEqual(merged, AABB(0, -5, 30, 15))

    def test_swept_bounds_covers_the_whole_path(self) -> None:
        bounds = AABB(0, 0, 4, 4).swept_bounds(Vec2(100, -20))
        self.assertEqual(bounds, AABB(0, -20, 104, 24))

    def test_contains_point_includes_the_boundary(self) -> None:
        box = AABB(0, 0, 10, 10)
        self.assertTrue(box.contains_point(Vec2(5, 5)))
        self.assertTrue(box.contains_point(Vec2(0, 0)))
        self.assertTrue(box.contains_point(Vec2(10, 10)))
        self.assertFalse(box.contains_point(Vec2(10.001, 5)))

    def test_negative_size_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AABB(0, 0, -1, 5)


class TestDiscreteOverlap(unittest.TestCase):
    def test_clear_overlap_and_separation(self) -> None:
        a = AABB(0, 0, 10, 10)
        self.assertTrue(aabb_overlap(a, AABB(5, 5, 10, 10)))
        self.assertFalse(aabb_overlap(a, AABB(20, 0, 10, 10)))
        self.assertFalse(aabb_overlap(a, AABB(0, 20, 10, 10)))

    def test_flush_edges_do_not_collide_by_default(self) -> None:
        # An entity standing exactly on a floor touches it every frame. If
        # that counted as a collision the resolver would fight it forever.
        floor = AABB(0, 10, 100, 10)
        standing = AABB(0, 0, 10, 10)  # bottom == 10 == floor.top
        self.assertFalse(aabb_overlap(standing, floor))
        self.assertTrue(aabb_overlap(standing, floor, touching_counts=True))

    def test_separation_on_one_axis_is_enough(self) -> None:
        # Boxes overlapping in x but not y must not collide.
        self.assertFalse(aabb_overlap(AABB(0, 0, 10, 10), AABB(5, 100, 10, 10)))


class TestMinimumTranslation(unittest.TestCase):
    def test_returns_none_when_apart(self) -> None:
        self.assertIsNone(minimum_translation(AABB(0, 0, 10, 10), AABB(50, 50, 10, 10)))

    def test_ejects_along_the_shallow_axis(self) -> None:
        # Deep in x, shallow in y -> must be pushed vertically.
        mover = AABB(0, 8, 20, 10)
        wall = AABB(0, 16, 20, 10)
        push = minimum_translation(mover, wall)
        assert push is not None
        self.assertEqual(push.x, 0.0)
        self.assertAlmostEqual(push.y, -2.0)  # up, out of the wall

    def test_ejects_left_or_right_correctly(self) -> None:
        wall = AABB(100, 0, 20, 100)
        from_left = minimum_translation(AABB(95, 40, 10, 10), wall)
        assert from_left is not None
        self.assertLess(from_left.x, 0.0)  # pushed back left
        from_right = minimum_translation(AABB(115, 40, 10, 10), wall)
        assert from_right is not None
        self.assertGreater(from_right.x, 0.0)

    def test_applying_the_translation_separates_the_boxes(self) -> None:
        rng = Rng(seed=7)
        for _ in range(500):
            a = AABB(rng.uniform(-20, 20), rng.uniform(-20, 20), rng.uniform(1, 15), rng.uniform(1, 15))
            b = AABB(rng.uniform(-20, 20), rng.uniform(-20, 20), rng.uniform(1, 15), rng.uniform(1, 15))
            push = minimum_translation(a, b)
            if push is None:
                continue
            self.assertFalse(aabb_overlap(a.translated(push), b), f"{a} vs {b} push {push}")


class TestTunnelling(unittest.TestCase):
    """The bug that motivates continuous collision detection."""

    WALL = AABB(500, 0, 8, 200)  # a thin wall
    BULLET_START = AABB(100, 100, 4, 4)
    BULLET_VELOCITY = Vec2(800, 0)  # far more than the wall is thick

    def test_discrete_collision_misses_a_fast_mover(self) -> None:
        # Before the step the bullet is well short of the wall; after it, well
        # past. It is never *inside* on any sampled instant, so an
        # overlap-based test reports no collision and the bullet passes
        # straight through a solid wall.
        before = self.BULLET_START
        after = before.translated(self.BULLET_VELOCITY)
        self.assertFalse(aabb_overlap(before, self.WALL))
        self.assertFalse(aabb_overlap(after, self.WALL))
        self.assertLess(before.right, self.WALL.left)
        self.assertGreater(after.left, self.WALL.right)

    def test_swept_collision_catches_it(self) -> None:
        hit = swept_aabb(self.BULLET_START, self.BULLET_VELOCITY, self.WALL)
        self.assertIsNotNone(hit)
        assert hit is not None
        # Contact when the bullet's right edge reaches the wall's left edge:
        # (500 - 104) / 800.
        self.assertAlmostEqual(hit.time, 396.0 / 800.0)
        self.assertEqual(hit.normal, Vec2(-1, 0))
        self.assertAlmostEqual(hit.position.x + self.BULLET_START.width, self.WALL.left)

    def test_smaller_steps_would_also_fix_it_but_do_not_scale(self) -> None:
        # Substepping finds the hit, which is why it is the usual first
        # instinct — but the step count needed scales with speed, and the
        # analytic sweep costs the same regardless.
        steps = 200
        step_velocity = self.BULLET_VELOCITY / steps
        box = self.BULLET_START
        found = False
        for _ in range(steps):
            box = box.translated(step_velocity)
            if aabb_overlap(box, self.WALL):
                found = True
                break
        self.assertTrue(found)


class TestSweptAABB(unittest.TestCase):
    TARGET = AABB(100, 100, 50, 50)

    def test_normal_for_each_face(self) -> None:
        cases = [
            (AABB(0, 110, 10, 10), Vec2(200, 0), Vec2(-1, 0)),  # moving right, hits left face
            (AABB(200, 110, 10, 10), Vec2(-200, 0), Vec2(1, 0)),  # moving left, hits right face
            (AABB(110, 0, 10, 10), Vec2(0, 200), Vec2(0, -1)),  # moving down, hits top face
            (AABB(110, 200, 10, 10), Vec2(0, -200), Vec2(0, 1)),  # moving up, hits bottom face
        ]
        for mover, velocity, expected in cases:
            hit = swept_aabb(mover, velocity, self.TARGET)
            self.assertIsNotNone(hit, f"no hit for {velocity}")
            assert hit is not None
            self.assertEqual(hit.normal, expected, f"wrong normal for velocity {velocity}")
            self.assertGreaterEqual(hit.time, 0.0)
            self.assertLessEqual(hit.time, 1.0)

    def test_no_hit_when_motion_falls_short(self) -> None:
        # Stops 10 units before the wall.
        self.assertIsNone(swept_aabb(AABB(0, 110, 10, 10), Vec2(80, 0), self.TARGET))

    def test_no_hit_when_passing_alongside(self) -> None:
        # Travels past on a parallel track that never overlaps in y.
        self.assertIsNone(swept_aabb(AABB(0, 300, 10, 10), Vec2(400, 0), self.TARGET))

    def test_no_hit_when_moving_away(self) -> None:
        self.assertIsNone(swept_aabb(AABB(0, 110, 10, 10), Vec2(-200, 0), self.TARGET))

    def test_zero_velocity_never_collides(self) -> None:
        # Not moving cannot begin a collision; and crucially this must not
        # divide by zero and poison the result with NaN.
        self.assertIsNone(swept_aabb(AABB(0, 0, 10, 10), Vec2(0, 0), self.TARGET))

    def test_axis_aligned_motion_does_not_produce_nan(self) -> None:
        hit = swept_aabb(AABB(110, 0, 10, 10), Vec2(0, 500), self.TARGET)
        assert hit is not None
        self.assertFalse(math.isnan(hit.time))
        self.assertFalse(math.isnan(hit.position.x) or math.isnan(hit.position.y))

    def test_diagonal_motion_picks_the_face_entered_last(self) -> None:
        # Approaching the top-left corner mostly from the left: the x slab is
        # entered later, so it is the left face that is struck.
        hit = swept_aabb(AABB(0, 90, 10, 10), Vec2(200, 40), self.TARGET)
        assert hit is not None
        self.assertEqual(hit.normal, Vec2(-1, 0))

    def test_already_overlapping_reports_immediate_contact(self) -> None:
        hit = swept_aabb(AABB(110, 110, 10, 10), Vec2(10, 0), self.TARGET)
        assert hit is not None
        self.assertEqual(hit.time, 0.0)

    def test_matches_brute_force_sampling(self) -> None:
        """Cross-check the analytic sweep against dense sampling."""
        rng = Rng(seed=1234)
        substeps = 10_000
        checked = 0
        grazing = 0
        for _ in range(400):
            mover = AABB(rng.uniform(-100, 100), rng.uniform(-100, 100), rng.uniform(2, 20), rng.uniform(2, 20))
            target = AABB(rng.uniform(-100, 100), rng.uniform(-100, 100), rng.uniform(2, 40), rng.uniform(2, 40))
            velocity = Vec2(rng.uniform(-150, 150), rng.uniform(-150, 150))
            if aabb_overlap(mover, target):
                continue  # start state already invalid; covered by its own test

            brute_time = None
            for step in range(substeps + 1):
                t = step / substeps
                if aabb_overlap(mover.translated(velocity * t), target):
                    brute_time = t
                    break

            hit = swept_aabb(mover, velocity, target)
            if brute_time is None:
                # Sampling found nothing. The analytic test is exact, so it
                # may legitimately catch a grazing contact whose overlap
                # window is thinner than one substep -- but such a contact
                # must be shallow. A deep overlap that sampling missed would
                # mean the analytic test is inventing collisions.
                if hit is not None:
                    grazing += 1
                    probe = mover.translated(velocity * min(1.0, hit.time + 0.5 / substeps))
                    overlap_x = min(probe.right, target.right) - max(probe.left, target.left)
                    overlap_y = min(probe.bottom, target.bottom) - max(probe.top, target.top)
                    self.assertLess(
                        min(overlap_x, overlap_y),
                        0.5,
                        f"deep overlap ({overlap_x:.3f}, {overlap_y:.3f}) that sampling missed entirely",
                    )
                continue

            self.assertIsNotNone(hit, f"sampling hit at {brute_time} but sweep found nothing")
            assert hit is not None
            checked += 1
            # The analytic answer is exact; sampling can only overshoot, and
            # by at most one substep.
            self.assertLessEqual(hit.time, brute_time + 1e-9)
            self.assertGreater(hit.time, brute_time - (1.0 / substeps) - 1e-9)
        self.assertGreater(checked, 20, "test data produced too few collisions to be meaningful")
        # Grazing contacts are real but should be a rounding-error minority;
        # a flood of them would mean the analytic test is over-reporting.
        self.assertLess(grazing, checked, f"{grazing} grazing vs {checked} agreed hits")


class TestRay(unittest.TestCase):
    BOX = AABB(50, 50, 20, 20)

    def test_ray_hits_and_reports_the_face(self) -> None:
        hit = ray_vs_aabb(Vec2(0, 60), Vec2(100, 0), self.BOX)
        assert hit is not None
        self.assertEqual(hit.normal, Vec2(-1, 0))
        self.assertAlmostEqual(hit.position.x, 50.0)

    def test_ray_misses(self) -> None:
        self.assertIsNone(ray_vs_aabb(Vec2(0, 0), Vec2(100, 0), self.BOX))

    def test_max_distance_truncates(self) -> None:
        self.assertIsNone(ray_vs_aabb(Vec2(0, 60), Vec2(100, 0), self.BOX, max_distance=10))
        self.assertIsNotNone(ray_vs_aabb(Vec2(0, 60), Vec2(100, 0), self.BOX, max_distance=60))


class TestCircles(unittest.TestCase):
    def test_circle_overlap(self) -> None:
        self.assertTrue(circle_overlap(Vec2(0, 0), 5, Vec2(8, 0), 5))
        self.assertFalse(circle_overlap(Vec2(0, 0), 5, Vec2(11, 0), 5))
        # Exactly touching does not count, matching the AABB convention.
        self.assertFalse(circle_overlap(Vec2(0, 0), 5, Vec2(10, 0), 5))

    def test_circle_vs_box_faces_and_interior(self) -> None:
        box = AABB(0, 0, 10, 10)
        self.assertTrue(circle_vs_aabb(Vec2(5, 5), 1, box))  # inside
        self.assertTrue(circle_vs_aabb(Vec2(-2, 5), 3, box))  # through the left face
        self.assertFalse(circle_vs_aabb(Vec2(-5, 5), 3, box))

    def test_corner_case_the_naive_shortcut_gets_wrong(self) -> None:
        """Expanding the box by the radius is the tempting shortcut and it is
        wrong outside corners: it makes a square where the reachable region is
        rounded."""
        box = AABB(0, 0, 10, 10)
        center, radius = Vec2(-3, -3), 4.0
        # The nearest point of the box is its corner, at distance sqrt(18)
        # which is 4.24 -- outside the radius, so no collision.
        self.assertAlmostEqual(center.distance_to(Vec2(0, 0)), math.sqrt(18))
        self.assertFalse(circle_vs_aabb(center, radius, box))
        # The naive expanded-box test would have said yes.
        self.assertTrue(box.expanded(radius).contains_point(center))


class TestMoveAndSlide(unittest.TestCase):
    def test_unobstructed_motion_completes(self) -> None:
        moved, hits = move_and_slide(AABB(0, 0, 10, 10), Vec2(50, 30), [])
        self.assertEqual(hits, [])
        self.assertAlmostEqual(moved.x, 50.0)
        self.assertAlmostEqual(moved.y, 30.0)

    def test_slides_along_a_wall_instead_of_stopping(self) -> None:
        # Running diagonally into a vertical wall: horizontal motion is
        # cancelled, vertical motion continues. Stopping dead here is what
        # makes a game feel like it is made of glue.
        wall = AABB(100, -100, 20, 400)
        moved, hits = move_and_slide(AABB(50, 0, 10, 10), Vec2(80, 60), [wall])
        self.assertTrue(hits)
        self.assertAlmostEqual(moved.x, 40.0, places=3)  # stopped at the wall
        self.assertAlmostEqual(moved.y, 60.0, places=3)  # full vertical travel

    def test_does_not_enter_the_obstacle(self) -> None:
        wall = AABB(100, -100, 20, 400)
        box = AABB(50, 0, 10, 10)
        moved, _ = move_and_slide(box, Vec2(500, 0), [wall])
        self.assertFalse(aabb_overlap(box.translated(moved), wall))

    def test_inside_corner_needs_two_resolutions(self) -> None:
        # Moving diagonally into the inside of a corner: sliding off the wall
        # pushes into the floor, so a single resolution pass is not enough.
        wall = AABB(100, 0, 20, 200)
        floor = AABB(0, 100, 200, 20)
        box = AABB(50, 50, 10, 10)
        moved, hits = move_and_slide(box, Vec2(200, 200), [wall, floor])
        self.assertGreaterEqual(len(hits), 2)
        final = box.translated(moved)
        self.assertFalse(aabb_overlap(final, wall))
        self.assertFalse(aabb_overlap(final, floor))

    def test_fast_mover_does_not_tunnel(self) -> None:
        wall = AABB(500, 0, 8, 200)
        bullet = AABB(100, 100, 4, 4)
        moved, hits = move_and_slide(bullet, Vec2(5000, 0), [wall])
        self.assertTrue(hits)
        self.assertLess(bullet.translated(moved).right, wall.left + 1e-3)

    def test_dead_end_terminates(self) -> None:
        # Wedged between two walls; must not loop forever.
        walls = [AABB(100, 0, 20, 100), AABB(0, 100, 200, 20), AABB(-20, 0, 20, 100)]
        moved, _ = move_and_slide(AABB(50, 50, 10, 10), Vec2(300, 300), walls)
        self.assertTrue(math.isfinite(moved.x) and math.isfinite(moved.y))

    def test_sweep_against_returns_the_earliest_hit(self) -> None:
        near = AABB(200, 0, 10, 100)
        far = AABB(400, 0, 10, 100)
        hit = sweep_against(AABB(0, 40, 10, 10), Vec2(600, 0), [far, near])
        assert hit is not None
        self.assertIs(hit.target, near)


if __name__ == "__main__":
    unittest.main(verbosity=2)
