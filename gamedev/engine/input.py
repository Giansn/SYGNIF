"""Input state with edge detection.

Games need three different questions answered about a button, and conflating
them is a rich source of bugs:

* *Is it held?* — walking, holding a trigger. Asked every tick.
* *Was it just pressed?* — jumping, opening a menu. Must fire exactly once.
* *Was it just released?* — variable-height jumps, charged attacks.

Only the first is directly observable from the hardware. The other two are
derived by comparing this tick's state against last tick's, which means
something has to own that history. That is what this class is.

Two traps it exists to avoid.

**Rolling the history per rendered frame instead of per simulation tick.**
With a fixed timestep, one frame can run several ticks. If the "previous"
snapshot only updates once per frame, ``just_pressed`` stays true for every
tick in that frame, and one tap on the jump button produces three jumps.

**Losing a tap shorter than one tick.** At 60 Hz a tick is 16 ms; a fast
player, or a mouse polling at 1000 Hz, can press and release inside that
window. Comparing snapshots alone shows the button down at neither end and
the input vanishes. So presses are *latched* as they arrive and cleared when
consumed, which guarantees a press is never silently dropped.
"""

from __future__ import annotations

from typing import Iterable

__all__ = ["InputState"]


class InputState:
    """Tracks which actions are held, and which changed on this tick.

    Actions are named strings rather than raw key codes so the simulation
    talks about ``"jump"`` rather than ``"space"``. That indirection is what
    makes rebinding, gamepads and replay files possible without touching game
    logic.
    """

    __slots__ = ("_held", "_previous", "_pressed_since_tick", "_released_since_tick", "_just_pressed", "_just_released")

    def __init__(self, actions: Iterable[str] = ()) -> None:
        self._held: set[str] = set()
        self._previous: set[str] = set()
        self._pressed_since_tick: set[str] = set()
        self._released_since_tick: set[str] = set()
        self._just_pressed: set[str] = set()
        self._just_released: set[str] = set()
        for action in actions:
            self._held.discard(action)

    # -- event side (called by the platform layer, any time) -------------

    def press(self, action: str) -> None:
        self._held.add(action)
        self._pressed_since_tick.add(action)

    def release(self, action: str) -> None:
        self._held.discard(action)
        self._released_since_tick.add(action)

    def set_held(self, action: str, down: bool) -> None:
        if down:
            self.press(action)
        else:
            self.release(action)

    def clear(self) -> None:
        """Drop everything — used when focus is lost.

        Without this, alt-tabbing away mid-stride leaves the movement key
        stuck down forever and the player walks into a wall on return.
        """
        self._held.clear()
        self._previous.clear()
        self._pressed_since_tick.clear()
        self._released_since_tick.clear()
        self._just_pressed.clear()
        self._just_released.clear()

    # -- simulation side (called once per tick) --------------------------

    def begin_tick(self) -> None:
        """Recompute the edges. Call exactly once per simulation tick."""
        # A press is an edge if the button went down since the last tick,
        # including the case where it also went back up again (a fast tap).
        self._just_pressed = (self._held - self._previous) | self._pressed_since_tick
        self._just_released = (self._previous - self._held) | (
            self._released_since_tick - self._held
        )
        self._previous = set(self._held)
        self._pressed_since_tick.clear()
        self._released_since_tick.clear()

    def is_held(self, action: str) -> bool:
        return action in self._held

    def just_pressed(self, action: str) -> bool:
        return action in self._just_pressed

    def just_released(self, action: str) -> bool:
        return action in self._just_released

    def axis(self, negative: str, positive: str) -> float:
        """A -1 / 0 / +1 axis from two opposed actions.

        Holding both yields zero rather than favouring one. Picking a winner
        instead produces the notorious bug where tapping left while running
        right makes the character sprint the wrong way.
        """
        return (1.0 if positive in self._held else 0.0) - (1.0 if negative in self._held else 0.0)

    def held_actions(self) -> frozenset[str]:
        return frozenset(self._held)

    def __repr__(self) -> str:
        return f"InputState(held={sorted(self._held)})"
