"""Pong — the first complete game on the engine.

Pong is the right first game because it is small enough to hold in your head
and still forces every core system to exist: a loop, input, collision,
response, scoring, and an opponent. It also contains one genuinely famous
design idea that is easy to miss.

**The deflection rule.** If the ball reflected off a paddle like a mirror, the
player would have no influence over where it goes — they could only survive,
never aim, and the rally would be pure reflex. The original arcade machine
instead sets the outgoing angle from *where on the paddle the ball struck*:
the edges throw it away steeply, the centre returns it flat. That single rule
turns a reaction test into a game with offence in it. It is implemented in
:meth:`PongGame._bounce_off_paddle`.

Two bugs this implementation guards against, both of which are traditional:

* **The vertical stall.** Nothing in the physics stops the ball from ending up
  travelling almost straight up and down, at which point it bounces between
  the walls forever and the rally never ends. The horizontal speed is
  therefore floored.
* **Tunnelling through a paddle.** The ball accelerates on every hit; a paddle
  is 4 pixels wide. Discrete collision loses the ball through the paddle
  within a few rallies, so the ball is moved with a swept test.
"""

from __future__ import annotations

import math
import os
from typing import Callable, Optional, Protocol

from gamedev.engine.collision import AABB, swept_aabb
from gamedev.engine.input import InputState
from gamedev.engine.rng import Rng
from gamedev.engine.vec import Vec2, clamp, sign
from gamedev.render.font import text_width
from gamedev.render.surface import PALETTE, Surface

__all__ = ["PongGame", "PaddleAI", "keyboard_controller", "Side"]

LEFT = "left"
RIGHT = "right"
Side = str

# Tuning. Speeds are in pixels per second, so they are independent of the
# tick rate -- the whole point of integrating with an explicit dt.
BALL_SIZE = 5.0
BALL_START_SPEED = 150.0
BALL_SPEEDUP = 1.06  # per paddle hit; rallies get harder

#: The ceiling on ball speed decides whether a rally can ever end.
#:
#: This was tuned by measurement, not taste. At 480 two competent opponents
#: lock into a stable loop: each aims at the predicted intercept, a centred
#: hit returns the ball flat down the middle, and the other paddle barely has
#: to move. A 600-second match produced a single 418-hit rally and no winner.
#: The escalation has to eventually exceed what a defender can physically
#: reach, otherwise "both players are good" means "the game never ends".
#:
#: Measured over 12 seeds of a five-point match between two equally skilled
#: AIs, which is the worst case for stalling:
#:
#:     cap    mean match   worst match   longest rally
#:     700       431 s        599 s          251 hits
#:     900       256 s        306 s           89 hits
#:    1100       212 s        258 s           48 hits
#:
#: 900 is the pick: rallies still build to something worth watching, while a
#: match is bounded at about five minutes even when neither side errs.
BALL_MAX_SPEED = 900.0
MAX_BOUNCE_ANGLE = math.radians(60.0)
MIN_HORIZONTAL_FRACTION = 0.35  # floor on |vx| / speed, prevents the stall

PADDLE_WIDTH = 4.0
PADDLE_HEIGHT = 34.0
PADDLE_SPEED = 200.0
PADDLE_INSET = 12.0

SERVE_DELAY = 0.9  # seconds of pause after a point


class Controller(Protocol):
    """Returns the desired paddle direction: -1 up, +1 down, 0 still."""

    def __call__(self, game: "PongGame", side: Side, dt: float) -> float: ...


class Paddle:
    __slots__ = ("side", "y", "height", "width", "x", "score")

    def __init__(self, side: Side, x: float, y: float) -> None:
        self.side = side
        self.x = x
        self.y = y
        self.width = PADDLE_WIDTH
        self.height = PADDLE_HEIGHT
        self.score = 0

    @property
    def box(self) -> AABB:
        return AABB(self.x, self.y, self.width, self.height)

    @property
    def center_y(self) -> float:
        return self.y + self.height * 0.5


class PongGame:
    """A full game of Pong, renderer-agnostic and deterministic."""

    def __init__(
        self,
        width: int = 320,
        height: int = 200,
        target_score: int = 5,
        seed: int = 1,
        left: Optional[Controller] = None,
        right: Optional[Controller] = None,
    ) -> None:
        self.width = width
        self.height = height
        self.target_score = target_score
        self.rng = Rng(seed=seed)
        self.input = InputState()

        self.left = Paddle(LEFT, PADDLE_INSET, height * 0.5 - PADDLE_HEIGHT * 0.5)
        self.right = Paddle(RIGHT, width - PADDLE_INSET - PADDLE_WIDTH, height * 0.5 - PADDLE_HEIGHT * 0.5)

        self.left_controller: Controller = left or PaddleAI(seed=seed * 7 + 1)
        self.right_controller: Controller = right or PaddleAI(seed=seed * 7 + 2)

        self.ball_position = Vec2(width * 0.5, height * 0.5)
        self.ball_velocity = Vec2(0.0, 0.0)
        self.serve_timer = SERVE_DELAY
        self.rally_hits = 0
        self.longest_rally = 0
        self.running = True
        self.winner: Optional[Side] = None
        self.elapsed = 0.0
        self._serve_towards = LEFT if self.rng.chance(0.5) else RIGHT

    # -- geometry --------------------------------------------------------

    @property
    def ball_box(self) -> AABB:
        return AABB(self.ball_position.x, self.ball_position.y, BALL_SIZE, BALL_SIZE)

    @property
    def ball_center(self) -> Vec2:
        return Vec2(self.ball_position.x + BALL_SIZE * 0.5, self.ball_position.y + BALL_SIZE * 0.5)

    def paddle(self, side: Side) -> Paddle:
        return self.left if side == LEFT else self.right

    # -- simulation ------------------------------------------------------

    def update(self, dt: float) -> None:
        self.input.begin_tick()
        self.elapsed += dt

        self._update_paddle(self.left, self.left_controller(self, LEFT, dt), dt)
        self._update_paddle(self.right, self.right_controller(self, RIGHT, dt), dt)

        if self.serve_timer > 0.0:
            self.serve_timer -= dt
            if self.serve_timer <= 0.0:
                self._serve()
            return

        self._move_ball(dt)

    def render(self, alpha: float) -> None:
        """No-op. Rendering is pulled by whoever owns a surface, not pushed."""

    def _update_paddle(self, paddle: Paddle, direction: float, dt: float) -> None:
        paddle.y = clamp(
            paddle.y + clamp(direction, -1.0, 1.0) * PADDLE_SPEED * dt,
            0.0,
            self.height - paddle.height,
        )

    def _serve(self) -> None:
        self.ball_position = Vec2(self.width * 0.5 - BALL_SIZE * 0.5, self.height * 0.5 - BALL_SIZE * 0.5)
        # Serve towards whoever just conceded, which is the convention and
        # also stops a strong AI from farming an instant re-score.
        direction = -1.0 if self._serve_towards == LEFT else 1.0
        angle = self.rng.uniform(-0.35, 0.35)
        self.ball_velocity = Vec2(math.cos(angle) * direction, math.sin(angle)) * BALL_START_SPEED
        self.rally_hits = 0

    def _move_ball(self, dt: float) -> None:
        """Move the ball, bouncing off whatever it meets.

        This is a bounce loop rather than the engine's slide loop: on contact
        the velocity is *reflected* instead of projected onto the surface, and
        the leftover time is spent travelling in the new direction. Without
        spending the leftover time, a ball hitting a wall early in a tick
        would lose the rest of that tick's motion and visibly stutter.
        """
        remaining = dt
        for _ in range(4):  # generous; two bounces in one tick is already rare
            if remaining <= 0.0:
                return

            displacement = self.ball_velocity * remaining
            box = self.ball_box

            walls = [
                AABB(-64.0, -64.0, self.width + 128.0, 64.0),  # ceiling
                AABB(-64.0, float(self.height), self.width + 128.0, 64.0),  # floor
            ]
            hit = None
            hit_kind = ""
            for wall in walls:
                candidate = swept_aabb(box, displacement, wall)
                if candidate is not None and (hit is None or candidate.time < hit.time):
                    hit, hit_kind = candidate, "wall"

            for paddle in (self.left, self.right):
                # Only test the paddle the ball is actually heading towards;
                # a ball leaving a paddle overlaps it for a frame and would
                # otherwise immediately re-collide and be trapped inside it.
                approaching = (paddle is self.left and self.ball_velocity.x < 0.0) or (
                    paddle is self.right and self.ball_velocity.x > 0.0
                )
                if not approaching:
                    continue
                candidate = swept_aabb(box, displacement, paddle.box)
                if candidate is not None and (hit is None or candidate.time < hit.time):
                    hit, hit_kind = candidate, paddle.side

            if hit is None:
                self.ball_position = self.ball_position + displacement
                break

            self.ball_position = self.ball_position + displacement * hit.time
            remaining *= 1.0 - hit.time

            if hit_kind == "wall":
                self.ball_velocity = Vec2(self.ball_velocity.x, -self.ball_velocity.y)
                # Nudge clear of the surface so the next sweep does not start
                # flush against it and immediately re-report contact.
                self.ball_position = self.ball_position + hit.normal * 0.01
            else:
                self._bounce_off_paddle(self.paddle(hit_kind))
                self.ball_position = self.ball_position + hit.normal * 0.01

        self._check_scoring()

    def _bounce_off_paddle(self, paddle: Paddle) -> None:
        """The deflection rule that makes Pong a game rather than a reflex test.

        Where the ball strikes the paddle sets the outgoing angle. Hitting
        with the paddle's edge sends it away steeply; hitting flat in the
        middle returns it along the horizontal. The player is therefore
        aiming, not merely intercepting.
        """
        half_height = paddle.height * 0.5
        # A degenerate paddle has no "where on it" to measure, so it returns
        # the ball flat rather than dividing by zero. Guarding here keeps a
        # bad entity from taking the whole simulation down with a NaN.
        offset = clamp((self.ball_center.y - paddle.center_y) / half_height, -1.0, 1.0) if half_height > 0.0 else 0.0
        angle = offset * MAX_BOUNCE_ANGLE

        speed = min(self.ball_velocity.length() * BALL_SPEEDUP, BALL_MAX_SPEED)
        direction = 1.0 if paddle.side == LEFT else -1.0

        velocity = Vec2(math.cos(angle) * direction, math.sin(angle)) * speed

        # Floor the horizontal component. Without this the ball can end up
        # travelling almost vertically and ping between the walls forever,
        # with neither player able to reach it or end the rally.
        if abs(velocity.x) < speed * MIN_HORIZONTAL_FRACTION:
            vx = speed * MIN_HORIZONTAL_FRACTION * direction
            vy = math.sqrt(max(0.0, speed * speed - vx * vx)) * (sign(velocity.y) or 1.0)
            velocity = Vec2(vx, vy)

        self.ball_velocity = velocity
        self.rally_hits += 1
        self.longest_rally = max(self.longest_rally, self.rally_hits)

    def _check_scoring(self) -> None:
        if self.ball_position.x + BALL_SIZE < 0.0:
            self._award_point(self.right)
        elif self.ball_position.x > self.width:
            self._award_point(self.left)

    def _award_point(self, scorer: Paddle) -> None:
        scorer.score += 1
        self._serve_towards = LEFT if scorer.side == RIGHT else RIGHT
        self.ball_velocity = Vec2(0.0, 0.0)
        self.ball_position = Vec2(self.width * 0.5 - BALL_SIZE * 0.5, self.height * 0.5 - BALL_SIZE * 0.5)
        self.serve_timer = SERVE_DELAY
        if scorer.score >= self.target_score:
            self.winner = scorer.side
            self.running = False

    # -- presentation ----------------------------------------------------

    def draw(self, surface: Surface) -> None:
        surface.clear(PALETTE["black"])

        # Dashed centre line.
        for y in range(4, self.height - 4, 12):
            surface.fill_rect(self.width // 2 - 1, y, 2, 7, PALETTE["slate"])

        surface.fill_rect(self.left.x, self.left.y, self.left.width, self.left.height, PALETTE["cyan"])
        surface.fill_rect(self.right.x, self.right.y, self.right.width, self.right.height, PALETTE["orange"])

        if self.serve_timer <= 0.0:
            surface.fill_rect(self.ball_position.x, self.ball_position.y, BALL_SIZE, BALL_SIZE, PALETTE["white"])

        # Score, mirrored either side of the centre line.
        left_text = str(self.left.score)
        surface.text(self.width // 2 - 20 - text_width(left_text, scale=3), 10, left_text, PALETTE["cyan"], scale=3)
        surface.text(self.width // 2 + 20, 10, str(self.right.score), PALETTE["orange"], scale=3)

        if self.winner:
            banner = f"{self.winner.upper()} WINS"
            surface.text((self.width - text_width(banner, scale=2)) // 2, self.height // 2 - 8, banner,
                         PALETTE["yellow"], scale=2)
        elif self.rally_hits >= 4:
            note = f"RALLY {self.rally_hits}"
            surface.text((self.width - text_width(note)) // 2, self.height - 12, note, PALETTE["steel"])


class PaddleAI:
    """An opponent that is beatable on purpose.

    A paddle that simply matches the ball's y every tick is unbeatable and
    therefore worthless: the rally only ends when the *player* errs, so the
    game has no arc. Three knobs make it feel like an opponent instead.

    * ``skill`` blends between chasing the ball's current position (weak,
      because it is always reacting late) and chasing the predicted intercept
      (strong). Prediction is done by simulating the ball forward through its
      wall bounces, which is cheap for a straight-line ball.
    * ``reaction`` is how long it waits before re-aiming, so it commits to a
      slightly stale target the way a person does.
    * ``error`` is a per-rally aim offset, so its mistakes are consistent
      within a rally rather than jittering frame to frame — jitter reads as a
      broken robot, a steady misjudgement reads as a missed read.
    """

    def __init__(self, skill: float = 0.75, reaction: float = 0.09, error: float = 9.0, seed: int = 0) -> None:
        self.skill = clamp(skill, 0.0, 1.0)
        self.reaction = reaction
        self.error = error
        self.rng = Rng(seed=seed)
        self._timer = 0.0
        self._target = 0.0
        self._aim_error = 0.0
        self._last_rally = -1

    def __call__(self, game: "PongGame", side: Side, dt: float) -> float:
        paddle = game.paddle(side)

        if game.rally_hits != self._last_rally:
            self._last_rally = game.rally_hits
            self._aim_error = self.rng.uniform(-self.error, self.error)

        self._timer -= dt
        if self._timer <= 0.0:
            self._timer = self.reaction
            approaching = (side == LEFT and game.ball_velocity.x < 0.0) or (
                side == RIGHT and game.ball_velocity.x > 0.0
            )
            if approaching:
                predicted = self._predict_intercept(game, paddle)
                reactive = game.ball_center.y
                self._target = reactive + (predicted - reactive) * self.skill + self._aim_error
            else:
                # Idle back towards the middle while the ball is away. Real
                # players recentre; a paddle frozen at the last intercept
                # looks asleep.
                self._target = game.height * 0.5

        delta = self._target - paddle.center_y
        # A dead zone stops the paddle vibrating around its target, which is
        # the giveaway that a bang-bang controller is driving it.
        if abs(delta) < 3.0:
            return 0.0
        return sign(delta)

    def _predict_intercept(self, game: "PongGame", paddle: Paddle) -> float:
        """Where the ball will cross this paddle's plane, including bounces.

        The ball travels in straight lines between wall bounces, so instead of
        stepping the simulation we reflect the *destination*: unfold the
        vertical motion into a straight line and fold it back with a triangle
        wave. Exact, and independent of the tick rate.
        """
        velocity = game.ball_velocity
        if velocity.x == 0.0:
            return game.ball_center.y

        # The plane the ball's *centre* is on at the moment of contact. The
        # ball touches the paddle when its leading edge arrives, which is half
        # a ball-width earlier than when its centre reaches the paddle face.
        if paddle.side == LEFT:
            plane = paddle.x + paddle.width + BALL_SIZE * 0.5
        else:
            plane = paddle.x - BALL_SIZE * 0.5

        travel_time = (plane - game.ball_center.x) / velocity.x
        if travel_time < 0.0:
            return game.ball_center.y

        unbounded = game.ball_center.y + velocity.y * travel_time

        # Fold with a triangle wave, reflecting at both walls. The span is the
        # range available to the ball's *centre*, not the field height: the
        # ball is a box and bounces when its edge meets the wall, so its
        # centre only ever reaches half a ball-width from either side.
        # Folding over the full height instead puts every bounce half a ball
        # out of place, and the error compounds with each one.
        low = BALL_SIZE * 0.5
        high = game.height - BALL_SIZE * 0.5
        span = high - low
        if span <= 0.0:
            return game.ball_center.y

        folded = math.fmod(unbounded - low, 2.0 * span)
        if folded < 0.0:
            folded += 2.0 * span
        return low + (folded if folded <= span else 2.0 * span - folded)


def keyboard_controller(up_action: str, down_action: str) -> Controller:
    """A controller driven by the game's :class:`InputState`."""

    def controller(game: "PongGame", side: Side, dt: float) -> float:
        return game.input.axis(up_action, down_action)

    return controller


# -- offline demo --------------------------------------------------------


def main() -> None:
    """Play a full AI-vs-AI match headlessly and save frames to look at."""
    from gamedev.engine.loop import GameLoop

    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")
    os.makedirs(out_dir, exist_ok=True)

    game = PongGame(
        seed=2026,
        target_score=5,
        left=PaddleAI(skill=0.62, reaction=0.11, error=11.0, seed=11),
        right=PaddleAI(skill=0.80, reaction=0.07, error=7.0, seed=22),
    )
    surface = Surface(game.width, game.height)
    loop = GameLoop(game, tick_rate=60.0)

    captured = 0
    frames: list[Surface] = []
    # Ten simulated minutes is a generous ceiling; a five-point match between
    # these two settles in about four.
    while game.running and loop.stats.ticks < 60 * 600:
        loop.run_headless(ticks=1)
        # Grab a frame mid-rally, when there is something to see.
        if game.serve_timer <= 0.0 and loop.stats.ticks % 47 == 0 and captured < 4:
            game.draw(surface)
            frames.append(surface.copy())
            captured += 1

    game.draw(surface)
    final = surface.scaled(3)
    final.save(os.path.join(out_dir, "pong_final.png"))

    # A contact sheet of the rally frames, stacked vertically.
    if frames:
        sheet = Surface(game.width, game.height * len(frames) + 2 * (len(frames) - 1), PALETTE["slate"])
        for index, frame in enumerate(frames):
            sheet.blit(frame, 0, index * (game.height + 2))
        sheet.scaled(2).save(os.path.join(out_dir, "pong_rally.png"))

    print(
        f"final {game.left.score}-{game.right.score}, winner={game.winner}, "
        f"ticks={loop.stats.ticks}, sim_time={loop.stats.sim_time:.1f}s, "
        f"longest_rally={game.longest_rally}"
    )
    print(f"wrote pong_final.png and pong_rally.png to {out_dir}")


if __name__ == "__main__":
    main()
