"""Tests for Pong.

Beyond "does it run", these check the properties that make it a *game*: the
ball cannot escape or tunnel, rallies terminate, and the deflection rule
actually gives the player control over the return angle.
"""

from __future__ import annotations

import math
import unittest

from gamedev.engine.loop import GameLoop
from gamedev.engine.vec import Vec2
from gamedev.games import pong
from gamedev.games.pong import (
    BALL_MAX_SPEED,
    BALL_SIZE,
    LEFT,
    MIN_HORIZONTAL_FRACTION,
    RIGHT,
    PaddleAI,
    PongGame,
    keyboard_controller,
)


def play(game: PongGame, seconds: float = 600.0) -> GameLoop:
    loop = GameLoop(game, tick_rate=60.0)
    loop.run_headless(ticks=int(60 * seconds))
    return loop


class TestMatchFlow(unittest.TestCase):
    def test_a_match_reaches_a_winner(self) -> None:
        game = PongGame(seed=2026, target_score=5)
        play(game)
        self.assertFalse(game.running)
        self.assertIn(game.winner, (LEFT, RIGHT))
        self.assertEqual(max(game.left.score, game.right.score), 5)

    def test_matches_terminate_across_skill_pairings(self) -> None:
        """The runaway-rally regression.

        With the ball speed capped too low, two competent opponents settle
        into a stable centred rally that never ends — a 600-second match once
        produced one 418-hit rally and no winner. Every pairing must finish.
        """
        pairings = [(0.62, 0.80), (0.55, 0.70), (0.80, 0.80), (0.9, 0.45)]
        for skill_left, skill_right in pairings:
            game = PongGame(
                seed=7,
                target_score=5,
                left=PaddleAI(skill=skill_left, seed=11),
                right=PaddleAI(skill=skill_right, seed=22),
            )
            play(game, seconds=600.0)
            self.assertIsNotNone(game.winner, f"no winner for skills {skill_left}/{skill_right}")

    def test_is_deterministic(self) -> None:
        results = []
        for _ in range(2):
            game = PongGame(seed=99, target_score=3)
            play(game)
            results.append((game.left.score, game.right.score, game.winner, game.elapsed))
        self.assertEqual(results[0], results[1])

    def test_different_seeds_diverge(self) -> None:
        outcomes = set()
        for seed in range(6):
            game = PongGame(seed=seed, target_score=3)
            play(game)
            outcomes.add((game.left.score, game.right.score, round(game.elapsed, 3)))
        self.assertGreater(len(outcomes), 1)

    def test_serve_goes_towards_the_conceder(self) -> None:
        game = PongGame(seed=5, target_score=9)
        game._award_point(game.left)  # right conceded
        game.serve_timer = 0.0
        game._serve()
        self.assertGreater(game.ball_velocity.x, 0.0, "should serve towards the right (conceding) side")

        game._award_point(game.right)
        game.serve_timer = 0.0
        game._serve()
        self.assertLess(game.ball_velocity.x, 0.0)

    def test_ball_is_hidden_and_still_during_the_serve_pause(self) -> None:
        game = PongGame(seed=3)
        self.assertGreater(game.serve_timer, 0.0)
        self.assertEqual(game.ball_velocity, Vec2(0, 0))
        game.update(1.0 / 60.0)
        self.assertEqual(game.ball_velocity, Vec2(0, 0))


class TestBallContainment(unittest.TestCase):
    def test_ball_never_leaves_the_field_vertically(self) -> None:
        game = PongGame(seed=17, target_score=9)
        loop = GameLoop(game, tick_rate=60.0)
        worst_top, worst_bottom = 0.0, 0.0
        for _ in range(60 * 400):
            if not game.running:
                break
            loop.run_headless(ticks=1)
            worst_top = min(worst_top, game.ball_position.y)
            worst_bottom = max(worst_bottom, game.ball_position.y + BALL_SIZE - game.height)
        # A small tolerance for the deliberate anti-restick nudge at contact.
        self.assertGreater(worst_top, -1.0, "ball escaped through the ceiling")
        self.assertLess(worst_bottom, 1.0, "ball escaped through the floor")

    def test_fast_ball_does_not_tunnel_through_a_paddle(self) -> None:
        """A 4px paddle against a ball moving 12px per tick."""
        game = PongGame(seed=1, target_score=9)
        game.serve_timer = 0.0
        paddle = game.right
        # Place the ball just left of the paddle, aimed straight at its middle.
        game.ball_position = Vec2(paddle.x - 12.0, paddle.center_y - BALL_SIZE * 0.5)
        game.ball_velocity = Vec2(BALL_MAX_SPEED, 0.0)
        self.assertGreater(BALL_MAX_SPEED / 60.0, paddle.width, "test is not actually a tunnelling risk")

        game.update(1.0 / 60.0)
        self.assertLess(game.ball_velocity.x, 0.0, "ball passed through the paddle instead of bouncing")
        self.assertLess(game.ball_position.x, paddle.x)

    def test_ball_leaving_a_paddle_is_not_retrapped(self) -> None:
        # A ball overlapping the paddle it just left must not immediately
        # re-collide, which would pin it inside the paddle forever.
        game = PongGame(seed=1, target_score=9)
        game.serve_timer = 0.0
        paddle = game.left
        game.ball_position = Vec2(paddle.x + 1.0, paddle.center_y)
        game.ball_velocity = Vec2(200.0, 0.0)  # moving away, to the right
        for _ in range(10):
            game.update(1.0 / 60.0)
        self.assertGreater(game.ball_position.x, paddle.x + paddle.width)
        self.assertGreater(game.ball_velocity.x, 0.0)

    def test_a_perfect_wall_is_never_beaten(self) -> None:
        """If the ball can be blocked, it must be. Any score here means the
        ball got through something solid."""
        game = PongGame(seed=4, target_score=9, left=PaddleAI(skill=1.0, reaction=0.0, error=0.0, seed=3))
        # Make the left paddle tall and fast enough to be an unbeatable wall.
        game.left.height = float(game.height)
        game.left.y = 0.0
        loop = GameLoop(game, tick_rate=60.0)
        for _ in range(60 * 120):
            loop.run_headless(ticks=1)
            game.left.y = 0.0  # pin it: it spans the whole field
        self.assertEqual(game.right.score, 0, "ball got past a full-height paddle")


class TestDeflectionRule(unittest.TestCase):
    """The rule that turns Pong from a reflex test into a game."""

    def _return_angle(self, offset_fraction: float) -> float:
        game = PongGame(seed=1, target_score=9)
        game.serve_timer = 0.0
        paddle = game.left
        game.ball_position = Vec2(
            paddle.x + paddle.width,
            paddle.center_y + offset_fraction * paddle.height * 0.5 - BALL_SIZE * 0.5,
        )
        game.ball_velocity = Vec2(-200.0, 0.0)
        game._bounce_off_paddle(paddle)
        return game.ball_velocity.angle()

    def test_edge_hits_are_steeper_than_centre_hits(self) -> None:
        centre = abs(self._return_angle(0.0))
        middle = abs(self._return_angle(0.5))
        edge = abs(self._return_angle(1.0))
        self.assertLess(centre, middle)
        self.assertLess(middle, edge)
        self.assertAlmostEqual(centre, 0.0, places=6)  # dead centre returns flat

    def test_hit_direction_follows_the_side_of_the_paddle_struck(self) -> None:
        self.assertGreater(self._return_angle(0.8), 0.0)  # low on the paddle -> downwards
        self.assertLess(self._return_angle(-0.8), 0.0)  # high on the paddle -> upwards

    def test_bounce_reverses_direction_and_speeds_up(self) -> None:
        game = PongGame(seed=1, target_score=9)
        game.serve_timer = 0.0
        game.ball_position = Vec2(game.left.x + game.left.width, game.left.center_y)
        game.ball_velocity = Vec2(-200.0, 0.0)
        before = game.ball_velocity.length()
        game._bounce_off_paddle(game.left)
        self.assertGreater(game.ball_velocity.x, 0.0)
        self.assertGreater(game.ball_velocity.length(), before)

    def test_speed_is_capped(self) -> None:
        game = PongGame(seed=1, target_score=9)
        game.serve_timer = 0.0
        game.ball_position = Vec2(game.left.x + game.left.width, game.left.center_y)
        game.ball_velocity = Vec2(-BALL_MAX_SPEED, 0.0)
        for _ in range(20):
            game._bounce_off_paddle(game.left)
            game.ball_velocity = Vec2(-abs(game.ball_velocity.x), game.ball_velocity.y)
        self.assertLessEqual(game.ball_velocity.length(), BALL_MAX_SPEED + 1e-6)

    def test_vertical_stall_is_prevented(self) -> None:
        """Without a floor on horizontal speed the ball can end up bouncing
        between ceiling and floor forever, unreachable by either player."""
        for offset in (-1.0, -0.99, 0.99, 1.0):
            game = PongGame(seed=1, target_score=9)
            game.serve_timer = 0.0
            paddle = game.left
            game.ball_position = Vec2(
                paddle.x + paddle.width,
                paddle.center_y + offset * paddle.height * 0.5 - BALL_SIZE * 0.5,
            )
            game.ball_velocity = Vec2(-300.0, 0.0)
            game._bounce_off_paddle(paddle)
            speed = game.ball_velocity.length()
            self.assertGreaterEqual(
                abs(game.ball_velocity.x) / speed,
                MIN_HORIZONTAL_FRACTION - 1e-9,
                f"ball too vertical at offset {offset}",
            )
            self.assertAlmostEqual(speed, game.ball_velocity.length())

    def test_speed_is_preserved_by_the_stall_correction(self) -> None:
        # Redirecting the ball must not silently change how fast it is going.
        game = PongGame(seed=1, target_score=9)
        game.serve_timer = 0.0
        paddle = game.left
        game.ball_position = Vec2(paddle.x + paddle.width, paddle.center_y + paddle.height * 0.5 - BALL_SIZE * 0.5)
        game.ball_velocity = Vec2(-300.0, 0.0)
        expected = min(300.0 * pong.BALL_SPEEDUP, BALL_MAX_SPEED)
        game._bounce_off_paddle(paddle)
        self.assertAlmostEqual(game.ball_velocity.length(), expected, places=6)


class TestPaddleAI(unittest.TestCase):
    def test_prediction_folds_wall_bounces(self) -> None:
        game = PongGame(seed=1, target_score=9)
        game.serve_timer = 0.0
        ai = PaddleAI(skill=1.0, reaction=0.0, error=0.0, seed=1)
        # Aimed steeply down-right; it must bounce off the floor before
        # reaching the right paddle, so the intercept is not the naive
        # straight-line extrapolation.
        game.ball_position = Vec2(40.0, game.height - 20.0)
        game.ball_velocity = Vec2(200.0, 200.0)
        predicted = ai._predict_intercept(game, game.right)
        self.assertGreaterEqual(predicted, 0.0)
        self.assertLessEqual(predicted, game.height)

    def test_prediction_matches_simulation(self) -> None:
        """Cross-check the closed-form fold against step-by-step reflection.

        The closed form unfolds the ball's vertical path into a straight line
        and folds it back with a triangle wave. That is easy to get subtly
        wrong -- the span must be the range available to the ball's *centre*,
        not the field height, and the arrival plane is half a ball-width in
        front of the paddle face, because contact is made by the leading edge.
        Both mistakes are invisible on a direct shot and only show up after a
        bounce or two, so this walks the ball in tiny steps and reflects it by
        hand as an independent reference.
        """
        ai = PaddleAI(skill=1.0, reaction=0.0, error=0.0, seed=1)

        for start_y, velocity in (
            (30.0, Vec2(220.0, 260.0)),  # several floor/ceiling bounces
            (100.0, Vec2(300.0, 0.0)),  # flat, no bounces at all
            (180.0, Vec2(150.0, -240.0)),  # upwards first
            (20.0, Vec2(90.0, 310.0)),  # slow across, many bounces
        ):
            game = PongGame(seed=1, target_score=9)
            game.serve_timer = 0.0
            game.ball_position = Vec2(60.0, start_y)
            game.ball_velocity = velocity
            predicted = ai._predict_intercept(game, game.right)

            # Independent reference: advance the centre in small steps and
            # mirror it about the wall whenever the ball's edge crosses one.
            low = BALL_SIZE * 0.5
            high = game.height - BALL_SIZE * 0.5
            plane = game.right.x - low
            position = game.ball_center
            step = velocity * (1.0 / 20_000.0)
            for _ in range(400_000):
                if position.x >= plane:
                    break
                position = position + step
                if position.y < low:
                    position = Vec2(position.x, 2.0 * low - position.y)
                    step = Vec2(step.x, -step.y)
                elif position.y > high:
                    position = Vec2(position.x, 2.0 * high - position.y)
                    step = Vec2(step.x, -step.y)

            self.assertAlmostEqual(
                predicted, position.y, delta=0.5, msg=f"start_y={start_y} velocity={velocity}"
            )

    def test_ai_recentres_when_the_ball_is_moving_away(self) -> None:
        game = PongGame(seed=1, target_score=9)
        game.serve_timer = 0.0
        ai = PaddleAI(skill=1.0, reaction=0.0, error=0.0, seed=1)
        game.ball_velocity = Vec2(300.0, 0.0)  # heading right, away from left
        ai(game, LEFT, 1.0)
        self.assertAlmostEqual(ai._target, game.height * 0.5)

    def test_ai_has_a_dead_zone(self) -> None:
        game = PongGame(seed=1, target_score=9)
        ai = PaddleAI(skill=1.0, reaction=10.0, error=0.0, seed=1)
        ai._target = game.left.center_y + 1.0  # within the dead zone
        self.assertEqual(ai(game, LEFT, 0.0), 0.0)
        ai._target = game.left.center_y + 40.0
        self.assertEqual(ai(game, LEFT, 0.0), 1.0)

    def test_weak_ai_loses_to_strong_ai(self) -> None:
        game = PongGame(
            seed=31,
            target_score=5,
            left=PaddleAI(skill=0.15, reaction=0.3, error=30.0, seed=1),
            right=PaddleAI(skill=0.95, reaction=0.04, error=2.0, seed=2),
        )
        play(game, seconds=600.0)
        self.assertEqual(game.winner, RIGHT)


class TestPlayerControl(unittest.TestCase):
    def test_keyboard_controller_moves_the_paddle(self) -> None:
        game = PongGame(seed=1, target_score=9, left=keyboard_controller("up", "down"))
        start = game.left.y

        game.input.press("down")
        for _ in range(30):
            game.update(1.0 / 60.0)
        self.assertGreater(game.left.y, start)

        game.input.release("down")
        game.input.press("up")
        middle = game.left.y
        for _ in range(30):
            game.update(1.0 / 60.0)
        self.assertLess(game.left.y, middle)

    def test_paddle_is_clamped_to_the_field(self) -> None:
        game = PongGame(seed=1, target_score=9, left=keyboard_controller("up", "down"))
        game.input.press("up")
        for _ in range(600):
            game.update(1.0 / 60.0)
        self.assertGreaterEqual(game.left.y, 0.0)

        game.input.release("up")
        game.input.press("down")
        for _ in range(600):
            game.update(1.0 / 60.0)
        self.assertLessEqual(game.left.y + game.left.height, game.height)

    def test_holding_both_directions_stands_still(self) -> None:
        game = PongGame(seed=1, target_score=9, left=keyboard_controller("up", "down"))
        game.input.press("up")
        game.input.press("down")
        start = game.left.y
        for _ in range(60):
            game.update(1.0 / 60.0)
        self.assertAlmostEqual(game.left.y, start)


class TestRendering(unittest.TestCase):
    def test_draw_produces_a_frame_without_error(self) -> None:
        from gamedev.render.surface import Surface

        game = PongGame(seed=1, target_score=2)
        surface = Surface(game.width, game.height)
        game.draw(surface)
        # Something was actually drawn (the centre line at minimum).
        self.assertTrue(any(surface.pixels), "frame is entirely blank")

    def test_draw_handles_the_win_banner(self) -> None:
        from gamedev.render.surface import Surface

        game = PongGame(seed=1, target_score=1)
        game.winner = LEFT
        surface = Surface(game.width, game.height)
        game.draw(surface)  # must not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
