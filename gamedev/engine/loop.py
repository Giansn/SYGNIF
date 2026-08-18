"""The game loop: a fixed simulation timestep with interpolated rendering.

This is the single most important structural decision in a game, and the one
most often got wrong. The naive loop is:

    while running:
        dt = time_since_last_frame()
        update(dt)
        render()

It works until it doesn't. Because ``dt`` varies with the machine, the frame
rate, and whatever else the OS is doing, the *simulation* varies too. Concrete
failures that fall out of a variable timestep:

* Physics stops being reproducible. Two runs of the same replay diverge, so
  recorded demos desync and networked clients disagree.
* Jump heights change with frame rate. Euler integration of gravity
  accumulates differently across one 32 ms step than across two 16 ms steps,
  so a player on a slow machine literally cannot make a jump.
* A long frame — a garbage collection pause, a texture load — moves an entity
  so far in one step that it passes clean through a wall.

The fix, the well-worn "fix your timestep" pattern, is to decouple the two
clocks. The simulation always advances in identical fixed-size ticks. Real
time is poured into an accumulator, and whole ticks are drained out of it. The
leftover fraction becomes ``alpha``, which rendering uses to interpolate
between the previous and current simulation states, so motion still looks
smooth on a display whose refresh has nothing to do with the tick rate.

The remaining trap is the spiral of death: if a tick takes longer to compute
than it represents, each frame requests more ticks than the last and the game
locks up. The guard is to clamp the incoming frame time, which trades a
temporary slow-motion simulation for staying responsive. That is the right
trade — a game running at half speed is playable, a frozen one is not.
"""

from __future__ import annotations

import time
from typing import Callable, Optional, Protocol

__all__ = ["Accumulator", "GameLoop", "LoopStats", "Simulation"]


class Simulation(Protocol):
    """What the loop needs from a game."""

    running: bool

    def update(self, dt: float) -> None:
        """Advance the simulation by exactly ``dt`` seconds."""

    def render(self, alpha: float) -> None:
        """Draw. ``alpha`` in ``[0, 1)`` blends previous -> current state."""


#: How close to a whole tick the accumulator must be before we call it whole.
#:
#: This tolerance is not cosmetic. Frame times are floats and essentially
#: never land exactly on tick boundaries: a display running at 30 Hz against a
#: 60 Hz tick should yield exactly 2 ticks per frame, but 30 frames of
#: ``1/30`` sum to 0.9999999999999999 seconds — 59.99999 ticks, which floors
#: to 59. The missing tick does not vanish quietly; it shows up as a
#: one-frame hitch every few seconds, the kind of stutter that gets reported
#: as "the game feels bad" and is miserable to track down.
#:
#: Snapping within a microsecond means a tick can run at most 1 us early,
#: which is 0.006% of a 60 Hz tick and imperceptible, and in exchange the
#: common integer-ratio refresh rates become exact.
SNAP_TOLERANCE = 1e-6


class Accumulator:
    """Converts elapsed wall-clock time into whole fixed-size ticks.

    Kept separate from the real-time driver so the tricky part can be tested
    without a clock: feed it numbers, assert on the tick counts.
    """

    __slots__ = ("dt", "max_frame_time", "_accumulated", "dropped_time")

    def __init__(self, dt: float = 1.0 / 60.0, max_frame_time: float = 0.25) -> None:
        if dt <= 0.0:
            raise ValueError("dt must be positive")
        if max_frame_time < dt:
            raise ValueError("max_frame_time must be at least one tick")
        self.dt = dt
        self.max_frame_time = max_frame_time
        self._accumulated = 0.0
        # How much real time the clamp has thrown away. Worth surfacing: a
        # non-zero value means the game could not keep up, which is a
        # performance bug, not a cosmetic one.
        self.dropped_time = 0.0

    def feed(self, frame_time: float) -> int:
        """Add elapsed real time; return how many ticks should now run."""
        if frame_time < 0.0:
            raise ValueError("frame_time must not be negative")
        if frame_time > self.max_frame_time:
            self.dropped_time += frame_time - self.max_frame_time
            frame_time = self.max_frame_time
        self._accumulated += frame_time
        ticks = int((self._accumulated + SNAP_TOLERANCE) / self.dt)
        self._accumulated -= ticks * self.dt
        # Snapping can leave the remainder a few ulps below zero. A negative
        # accumulator would make the render interpolation factor negative and
        # extrapolate backwards, so floor it.
        if self._accumulated < 0.0:
            self._accumulated = 0.0
        return ticks

    @property
    def alpha(self) -> float:
        """Fraction of a tick left over — the render interpolation factor."""
        return self._accumulated / self.dt

    @property
    def pending(self) -> float:
        return self._accumulated

    def reset(self) -> None:
        self._accumulated = 0.0


class LoopStats:
    """What actually happened, for the record."""

    __slots__ = ("frames", "ticks", "sim_time", "wall_time", "dropped_time")

    def __init__(self) -> None:
        self.frames = 0
        self.ticks = 0
        self.sim_time = 0.0
        self.wall_time = 0.0
        self.dropped_time = 0.0

    @property
    def ticks_per_frame(self) -> float:
        return self.ticks / self.frames if self.frames else 0.0

    def __repr__(self) -> str:
        return (
            f"LoopStats(frames={self.frames}, ticks={self.ticks}, "
            f"sim_time={self.sim_time:.3f}s, wall_time={self.wall_time:.3f}s, "
            f"dropped={self.dropped_time:.3f}s)"
        )


class GameLoop:
    """Drives a :class:`Simulation` with a fixed timestep.

    The clock is injected, which is what makes the loop testable: pass a fake
    clock and a run is deterministic and instant, with no sleeping and no
    dependence on how fast the host machine happens to be.
    """

    def __init__(
        self,
        sim: Simulation,
        tick_rate: float = 60.0,
        max_frame_time: float = 0.25,
        clock: Callable[[], float] = time.perf_counter,
        sleep: Optional[Callable[[float], None]] = time.sleep,
        frame_cap: Optional[float] = None,
    ) -> None:
        self.sim = sim
        self.accumulator = Accumulator(dt=1.0 / tick_rate, max_frame_time=max_frame_time)
        self.clock = clock
        self.sleep = sleep
        # Without a cap, a loop with a cheap update spins the CPU at thousands
        # of frames per second producing frames nobody will ever see.
        self.frame_cap = frame_cap
        self.stats = LoopStats()

    def run(self, max_frames: Optional[int] = None, max_sim_time: Optional[float] = None) -> LoopStats:
        """Run until the simulation stops or a limit is hit.

        The limits exist so the same loop can drive an interactive game and a
        headless test that must terminate.
        """
        dt = self.accumulator.dt
        previous = self.clock()
        started = previous
        min_frame = 1.0 / self.frame_cap if self.frame_cap else 0.0

        while self.sim.running:
            if max_frames is not None and self.stats.frames >= max_frames:
                break
            if max_sim_time is not None and self.stats.sim_time >= max_sim_time:
                break

            now = self.clock()
            frame_time = now - previous
            previous = now

            for _ in range(self.accumulator.feed(frame_time)):
                self.sim.update(dt)
                self.stats.ticks += 1
                self.stats.sim_time += dt
                # An update may end the game (player died, window closed).
                # Checking here rather than only at the top of the frame
                # avoids running further ticks of a simulation that is over.
                if not self.sim.running:
                    break

            self.sim.render(self.accumulator.alpha)
            self.stats.frames += 1

            if min_frame and self.sleep is not None:
                spare = min_frame - (self.clock() - now)
                if spare > 0.0:
                    self.sleep(spare)

        self.stats.wall_time = self.clock() - started
        self.stats.dropped_time = self.accumulator.dropped_time
        return self.stats

    def run_headless(self, ticks: int, render_every: Optional[int] = None) -> LoopStats:
        """Run exactly ``ticks`` simulation steps with no clock at all.

        Real time is the one input a test cannot control, so for tests, replay
        verification and offline frame capture we bypass it entirely. Because
        the timestep is fixed, this produces byte-identical results to a
        realtime run that happened to keep up — which is precisely the payoff
        of separating simulation time from wall time.
        """
        dt = self.accumulator.dt
        for tick in range(ticks):
            if not self.sim.running:
                break
            self.sim.update(dt)
            self.stats.ticks += 1
            self.stats.sim_time += dt
            if render_every and (tick + 1) % render_every == 0:
                self.sim.render(0.0)
                self.stats.frames += 1
        return self.stats


class ScriptedClock:
    """A clock that reads its elapsed times from a list.

    Lets a test say "now deliver a 300 ms frame" and assert on what the loop
    does about it, with no sleeping and no flakiness.
    """

    __slots__ = ("now", "_durations", "_index", "_default")

    def __init__(self, durations: list[float], default: float = 1.0 / 60.0) -> None:
        self.now = 0.0
        self._durations = durations
        self._index = 0
        self._default = default

    def __call__(self) -> float:
        """Return the current time, then advance by the next scripted step."""
        value = self.now
        if self._index < len(self._durations):
            self.now += self._durations[self._index]
            self._index += 1
        else:
            self.now += self._default
        return value


__all__.append("ScriptedClock")
