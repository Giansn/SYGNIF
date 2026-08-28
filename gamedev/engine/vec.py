"""2D vector math.

Everything in a 2D game — position, velocity, acceleration, surface normals,
steering forces — is a pair of floats. Giving that pair a type buys three
things: readable call sites, one place to put the geometry helpers, and a
guarantee that ``a + b`` means component-wise addition rather than tuple
concatenation.

``Vec2`` is immutable. Mutable vectors are a classic source of aliasing bugs
(two entities accidentally sharing one position object, so moving one moves
both). Immutability costs an allocation per operation; in the hot inner loops
of this engine we drop down to raw floats where it matters and keep ``Vec2``
for everything else.
"""

from __future__ import annotations

import math
from typing import Iterator

__all__ = ["Vec2", "ZERO", "clamp", "lerp", "sign", "approx"]

EPSILON = 1e-9


class Vec2:
    """An immutable 2D vector of floats."""

    __slots__ = ("x", "y")

    def __init__(self, x: float = 0.0, y: float = 0.0) -> None:
        # object.__setattr__ is not needed because we simply never expose a
        # setter; __slots__ plus discipline is enough and keeps construction
        # cheap, which matters when a frame builds thousands of these.
        self.x = float(x)
        self.y = float(y)

    # -- construction ----------------------------------------------------

    @staticmethod
    def from_angle(radians: float, length: float = 1.0) -> "Vec2":
        """Unit vector at ``radians`` (measured counter-clockwise from +x)."""
        return Vec2(math.cos(radians) * length, math.sin(radians) * length)

    # -- arithmetic ------------------------------------------------------

    def __add__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x - other.x, self.y - other.y)

    def __mul__(self, scalar: float) -> "Vec2":
        return Vec2(self.x * scalar, self.y * scalar)

    __rmul__ = __mul__

    def __truediv__(self, scalar: float) -> "Vec2":
        return Vec2(self.x / scalar, self.y / scalar)

    def __neg__(self) -> "Vec2":
        return Vec2(-self.x, -self.y)

    # -- geometry --------------------------------------------------------

    def dot(self, other: "Vec2") -> float:
        return self.x * other.x + self.y * other.y

    def cross(self, other: "Vec2") -> float:
        """The z component of the 3D cross product.

        Sign tells you which side of ``self`` the vector ``other`` lies on,
        which is how you do "is this turn left or right" without trigonometry.
        """
        return self.x * other.y - self.y * other.x

    def length(self) -> float:
        return math.hypot(self.x, self.y)

    def length_squared(self) -> float:
        """Length without the square root.

        Comparing distances is the single most common vector operation in a
        game (nearest enemy, is-in-range, collision culling). ``sqrt`` is
        monotonic, so comparing squared lengths gives the same answer and
        skips the expensive call.
        """
        return self.x * self.x + self.y * self.y

    def normalized(self) -> "Vec2":
        """Unit vector in the same direction; zero vector maps to zero.

        Returning zero rather than raising is deliberate: normalising a zero
        velocity happens constantly (a stationary entity asked for its facing)
        and an exception there would force guard clauses at every call site.
        """
        length = self.length()
        if length < EPSILON:
            return Vec2(0.0, 0.0)
        return Vec2(self.x / length, self.y / length)

    def clamped(self, max_length: float) -> "Vec2":
        """Same direction, length capped at ``max_length`` (terminal velocity)."""
        length_sq = self.length_squared()
        if length_sq <= max_length * max_length or length_sq < EPSILON:
            return self
        return self.normalized() * max_length

    def perpendicular(self) -> "Vec2":
        """Rotated 90 degrees counter-clockwise. Used for surface tangents."""
        return Vec2(-self.y, self.x)

    def rotated(self, radians: float) -> "Vec2":
        cos_r, sin_r = math.cos(radians), math.sin(radians)
        return Vec2(self.x * cos_r - self.y * sin_r, self.x * sin_r + self.y * cos_r)

    def angle(self) -> float:
        """Angle in radians from +x, in ``(-pi, pi]``."""
        return math.atan2(self.y, self.x)

    def distance_to(self, other: "Vec2") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def distance_squared_to(self, other: "Vec2") -> float:
        dx, dy = self.x - other.x, self.y - other.y
        return dx * dx + dy * dy

    def lerp(self, other: "Vec2", t: float) -> "Vec2":
        """Linear interpolation. ``t=0`` gives self, ``t=1`` gives other."""
        return Vec2(self.x + (other.x - self.x) * t, self.y + (other.y - self.y) * t)

    def reflect(self, normal: "Vec2") -> "Vec2":
        """Mirror this vector about a surface with the given unit ``normal``.

        ``v - 2(v.n)n``. This is the whole of "ball bounces off wall": the
        component along the normal flips, the component along the surface is
        untouched.
        """
        return self - normal * (2.0 * self.dot(normal))

    # -- plumbing --------------------------------------------------------

    def as_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)

    def __iter__(self) -> Iterator[float]:
        yield self.x
        yield self.y

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Vec2):
            return NotImplemented
        return self.x == other.x and self.y == other.y

    def __hash__(self) -> int:
        return hash((self.x, self.y))

    def __repr__(self) -> str:
        return f"Vec2({self.x:g}, {self.y:g})"


ZERO = Vec2(0.0, 0.0)


# -- scalar helpers ------------------------------------------------------


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def sign(value: float) -> float:
    return 0.0 if value == 0.0 else math.copysign(1.0, value)


def approx(a: float, b: float, tolerance: float = 1e-6) -> bool:
    return abs(a - b) <= tolerance
