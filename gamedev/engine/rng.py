"""A deterministic pseudo-random number generator (PCG32).

Games need randomness that is *reproducible*. A seed must produce the same
dungeon, the same loot rolls, and the same enemy patrol jitter on every run,
on every machine, forever — otherwise you cannot reproduce a bug report, you
cannot write a test over generated content, and you cannot ship a "share this
seed" feature.

Python's :mod:`random` is a fine Mersenne Twister, but its exact stream is an
implementation detail of the interpreter version, and it is a single global
by default. So this module implements PCG32 (O'Neill, 2014) directly: a
64-bit LCG whose output is permuted before being handed out. It is tiny, it
is fast enough, its stream is nailed down by this source file, and each
subsystem can hold its own independent instance.

The independent-instance point matters more than it sounds. If terrain
generation and loot rolls draw from one shared stream, adding a single extra
terrain call shifts every subsequent loot roll and the whole world changes.
Give each subsystem its own ``Rng`` and they stay decoupled.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence, TypeVar

__all__ = ["Rng"]

T = TypeVar("T")

_MASK64 = (1 << 64) - 1
_MASK32 = (1 << 32) - 1
_MULTIPLIER = 6364136223846793005
_DEFAULT_STREAM = 1442695040888963407


class Rng:
    """PCG32 — a permuted congruential generator.

    The LCG state advances with ``state = state * MULT + inc``. An LCG alone
    has famously weak low bits, so PCG never hands out the state directly: it
    xor-folds the high bits down and then rotates by an amount *also* taken
    from the state. That output permutation is what turns a mediocre generator
    into a good one without the cost of a big-state design.
    """

    __slots__ = ("_state", "_inc")

    def __init__(self, seed: int = 0, stream: int = _DEFAULT_STREAM) -> None:
        # ``stream`` selects one of 2^63 distinct sequences from the same
        # generator. Two Rng objects with the same seed but different streams
        # are independent, which is how you give each subsystem its own
        # randomness derived from one master world seed.
        self._inc = ((int(stream) << 1) | 1) & _MASK64
        self._state = 0
        self._next_uint32()
        self._state = (self._state + int(seed)) & _MASK64
        self._next_uint32()

    # -- core ------------------------------------------------------------

    def _next_uint32(self) -> int:
        old = self._state
        self._state = (old * _MULTIPLIER + self._inc) & _MASK64
        # Xor-fold the high bits into the middle, then rotate right by a
        # variable amount drawn from the top 5 bits of the old state.
        xorshifted = (((old >> 18) ^ old) >> 27) & _MASK32
        rot = (old >> 59) & 31
        return ((xorshifted >> rot) | (xorshifted << ((-rot) & 31))) & _MASK32

    def next_uint32(self) -> int:
        """A uniformly distributed 32-bit unsigned integer."""
        return self._next_uint32()

    # -- derived distributions -------------------------------------------

    def random(self) -> float:
        """A float in ``[0.0, 1.0)``.

        Built from 53 random bits — the exact size of a float64 mantissa — so
        every representable value in the range is reachable and none is more
        likely than another. Dividing a single 32-bit draw by 2**32 would only
        ever produce 4 billion distinct values with visible gaps.
        """
        high = self._next_uint32() >> 5  # 27 bits
        low = self._next_uint32() >> 6  # 26 bits
        return ((high << 26) | low) / float(1 << 53)

    def below(self, bound: int) -> int:
        """A uniform integer in ``[0, bound)`` with no modulo bias.

        The naive ``next_uint32() % bound`` is subtly wrong: unless ``bound``
        divides 2**32 exactly, the low residues get one extra chance each. For
        a d6 the skew is invisible; for a bound near 2**31 it approaches 2:1.
        The fix is rejection sampling — throw away draws that land in the
        short final block and roll again.
        """
        if bound <= 0:
            raise ValueError("bound must be positive")
        threshold = (-bound) % bound  # == 2**32 % bound, without the big int
        while True:
            candidate = self._next_uint32()
            if candidate >= threshold:
                return candidate % bound

    def randint(self, low: int, high: int) -> int:
        """A uniform integer in ``[low, high]``, both ends inclusive."""
        if high < low:
            raise ValueError("high must be >= low")
        return low + self.below(high - low + 1)

    def uniform(self, low: float, high: float) -> float:
        """A float in ``[low, high)``."""
        return low + (high - low) * self.random()

    def chance(self, probability: float) -> bool:
        """True with the given probability."""
        return self.random() < probability

    def gauss(self, mu: float = 0.0, sigma: float = 1.0) -> float:
        """A normally distributed float (Box-Muller transform).

        Useful for scatter that should cluster around a centre — weapon
        spread, crowd placement — where uniform noise looks unnaturally flat.
        """
        # 1 - random() keeps the argument of log strictly positive.
        u1 = 1.0 - self.random()
        u2 = self.random()
        return mu + sigma * math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)

    def choice(self, items: Sequence[T]) -> T:
        if not items:
            raise IndexError("cannot choose from an empty sequence")
        return items[self.below(len(items))]

    def weighted_choice(self, items: Sequence[T], weights: Sequence[float]) -> T:
        """Pick an item with probability proportional to its weight."""
        if len(items) != len(weights):
            raise ValueError("items and weights must be the same length")
        total = math.fsum(weights)
        if total <= 0.0:
            raise ValueError("weights must sum to a positive value")
        target = self.random() * total
        cumulative = 0.0
        for item, weight in zip(items, weights):
            cumulative += weight
            if target < cumulative:
                return item
        return items[-1]  # only reachable through float rounding

    def shuffle(self, items: list[T]) -> None:
        """Fisher-Yates shuffle, in place.

        Iterating downwards and swapping with an index in ``[0, i]`` is the
        correct form; the common bug is picking from the whole list each time,
        which produces n**n equally likely swap sequences over n! orderings
        and therefore a measurably biased result.
        """
        for i in range(len(items) - 1, 0, -1):
            j = self.below(i + 1)
            items[i], items[j] = items[j], items[i]

    def sample(self, population: Sequence[T], count: int) -> list[T]:
        """``count`` distinct items, in random order."""
        if count > len(population):
            raise ValueError("sample larger than population")
        pool = list(population)
        self.shuffle(pool)
        return pool[:count]

    # -- derivation ------------------------------------------------------

    def fork(self, stream: int) -> "Rng":
        """A new independent generator seeded from this one.

        The idiom is a single world seed forked per subsystem, so that
        ``world.fork(1)`` for terrain and ``world.fork(2)`` for loot stay
        stable no matter how many draws the other one makes.
        """
        return Rng(seed=self._next_uint32() | (self._next_uint32() << 32), stream=stream)

    def state(self) -> tuple[int, int]:
        """Snapshot, for save games and replay files."""
        return (self._state, self._inc)

    def restore(self, state: tuple[int, int]) -> None:
        self._state, self._inc = state

    def __repr__(self) -> str:
        return f"Rng(state=0x{self._state:016x}, inc=0x{self._inc:016x})"


def seed_from_text(text: str) -> int:
    """Turn a human-typed seed like ``"cavern"`` into a 64-bit integer.

    FNV-1a: small, dependency-free, and stable across runs — unlike
    :func:`hash`, which Python randomises per process for strings.
    """
    value = 0xCBF29CE484222325
    for byte in text.encode("utf-8"):
        value = ((value ^ byte) * 0x100000001B3) & _MASK64
    return value


__all__.append("seed_from_text")
