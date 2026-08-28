"""Collision detection and response for axis-aligned boxes and circles.

Collision is where a game stops being a simulation of points and starts
feeling like a world with solid things in it, and it is where the naive
implementation fails in the most instructive ways.

The naive approach is *discrete*: move everything, then look for overlaps,
then push overlapping things apart. It has two failure modes that every 2D
game hits.

**Tunnelling.** A bullet moving 400 px per tick simply is not inside a 16 px
wall on any tick — it was in front on one tick and behind on the next. No
amount of overlap testing will find a collision that never existed at a
sampled instant. The fix is *continuous* detection: instead of asking "do
these overlap now", ask "over the course of this motion, at what fraction of
the step did they first touch". That is what :func:`swept_aabb` computes.

**Corner snagging.** Push-out resolution picks an axis to eject along. Walk
an entity along a floor made of separate tiles and, at a tile seam, the
smallest penetration is sometimes horizontal rather than vertical — so the
entity gets shoved sideways and appears to trip over a perfectly flat floor.
The fix is not a better push-out; it is to resolve the axes separately and in
a fixed order, which is what :func:`move_and_slide` does.

Everything here is axis-aligned. Rotated boxes need the separating axis
theorem, which is a genuinely bigger idea; AABBs plus circles cover the
overwhelming majority of 2D games and keep the maths exact.
"""

from __future__ import annotations

import math
from typing import Iterable, NamedTuple, Optional, Sequence

from gamedev.engine.vec import Vec2

__all__ = [
    "AABB",
    "Hit",
    "aabb_overlap",
    "minimum_translation",
    "swept_aabb",
    "sweep_against",
    "ray_vs_aabb",
    "circle_overlap",
    "circle_vs_aabb",
    "move_and_slide",
]


class AABB:
    """An axis-aligned bounding box, stored as a corner plus a size.

    Corner-plus-size rather than centre-plus-extent because tile grids,
    sprites and screen rectangles are all naturally expressed from their
    top-left, and converting at every call site is where sign errors breed.

    The y axis points **down**, matching the framebuffer: ``top`` is the
    smaller y. Mixing conventions between the physics and the renderer is one
    of the great time sinks of 2D game programming, so there is exactly one
    convention here and it is the screen's.
    """

    __slots__ = ("x", "y", "width", "height")

    def __init__(self, x: float, y: float, width: float, height: float) -> None:
        if width < 0.0 or height < 0.0:
            raise ValueError("AABB dimensions must be non-negative")
        self.x = float(x)
        self.y = float(y)
        self.width = float(width)
        self.height = float(height)

    # -- edges and corners ----------------------------------------------

    @property
    def left(self) -> float:
        return self.x

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def top(self) -> float:
        return self.y

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center(self) -> Vec2:
        return Vec2(self.x + self.width * 0.5, self.y + self.height * 0.5)

    @property
    def half(self) -> Vec2:
        return Vec2(self.width * 0.5, self.height * 0.5)

    @staticmethod
    def from_center(center: Vec2, width: float, height: float) -> "AABB":
        return AABB(center.x - width * 0.5, center.y - height * 0.5, width, height)

    # -- derived boxes ---------------------------------------------------

    def translated(self, delta: Vec2) -> "AABB":
        return AABB(self.x + delta.x, self.y + delta.y, self.width, self.height)

    def moved_to(self, position: Vec2) -> "AABB":
        return AABB(position.x, position.y, self.width, self.height)

    def expanded(self, amount: float) -> "AABB":
        """Grow by ``amount`` on every side (the Minkowski sum with a box)."""
        return AABB(self.x - amount, self.y - amount, self.width + 2 * amount, self.height + 2 * amount)

    def union(self, other: "AABB") -> "AABB":
        left = min(self.left, other.left)
        top = min(self.top, other.top)
        return AABB(left, top, max(self.right, other.right) - left, max(self.bottom, other.bottom) - top)

    def swept_bounds(self, velocity: Vec2) -> "AABB":
        """The box covering this box's entire path over one step.

        A cheap conservative test: if this does not overlap a candidate, no
        continuous test can possibly hit it, so it can be rejected outright.
        """
        return self.union(self.translated(velocity))

    # -- queries ---------------------------------------------------------

    def contains_point(self, point: Vec2) -> bool:
        return self.left <= point.x <= self.right and self.top <= point.y <= self.bottom

    def overlaps(self, other: "AABB") -> bool:
        return aabb_overlap(self, other)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AABB):
            return NotImplemented
        return (self.x, self.y, self.width, self.height) == (other.x, other.y, other.width, other.height)

    def __repr__(self) -> str:
        return f"AABB(x={self.x:g}, y={self.y:g}, w={self.width:g}, h={self.height:g})"


class Hit(NamedTuple):
    """The result of a collision query.

    ``time`` is the fraction of the attempted motion completed before contact,
    so ``position + velocity * hit.time`` is where the mover should stop.
    ``normal`` points out of the surface that was struck.
    """

    time: float
    normal: Vec2
    position: Vec2
    #: The box that was struck, when the query was against a collection.
    target: Optional[AABB] = None


# -- discrete tests ------------------------------------------------------


def aabb_overlap(a: AABB, b: AABB, touching_counts: bool = False) -> bool:
    """Do two boxes overlap?

    ``touching_counts`` decides whether exactly-flush edges collide. The
    default (no) is what you want for solids — an entity standing precisely on
    a floor is touching it every single frame, and if that registered as a
    collision the resolver would fight it forever.
    """
    if touching_counts:
        return a.left <= b.right and a.right >= b.left and a.top <= b.bottom and a.bottom >= b.top
    return a.left < b.right and a.right > b.left and a.top < b.bottom and a.bottom > b.top


def minimum_translation(a: AABB, b: AABB) -> Optional[Vec2]:
    """Smallest offset that moves ``a`` out of ``b``, or ``None`` if apart.

    Overlap depth is computed on both axes and the shallower one wins, because
    ejecting along the deep axis would teleport the entity across the obstacle.
    This is correct for a single box and subtly wrong for a grid of them; see
    :func:`move_and_slide` for why.
    """
    if not aabb_overlap(a, b):
        return None

    # Signed depth: positive means push in the positive direction.
    right_push = b.right - a.left
    left_push = b.left - a.right
    down_push = b.bottom - a.top
    up_push = b.top - a.bottom

    x_push = right_push if right_push < -left_push else left_push
    y_push = down_push if down_push < -up_push else up_push

    return Vec2(x_push, 0.0) if abs(x_push) < abs(y_push) else Vec2(0.0, y_push)


def circle_overlap(center_a: Vec2, radius_a: float, center_b: Vec2, radius_b: float) -> bool:
    """Circles collide when centres are closer than the summed radii.

    Compared squared to avoid the square root — this runs for every pair.
    """
    reach = radius_a + radius_b
    return center_a.distance_squared_to(center_b) < reach * reach


def circle_vs_aabb(center: Vec2, radius: float, box: AABB) -> bool:
    """Circle against box, via the closest point on the box.

    Clamping the centre into the box gives the nearest point on (or in) it;
    the circle hits exactly when that point is within the radius. This handles
    the corner case correctly, which the tempting "expand the box by the
    radius" shortcut does not — that version collides on the diagonal outside
    a corner where the real circle does not reach.
    """
    closest = Vec2(
        min(max(center.x, box.left), box.right),
        min(max(center.y, box.top), box.bottom),
    )
    return center.distance_squared_to(closest) < radius * radius


# -- continuous tests ----------------------------------------------------


def swept_aabb(mover: AABB, velocity: Vec2, target: AABB) -> Optional[Hit]:
    """When, during this motion, does ``mover`` first touch ``target``?

    The slab method. Treat the target as two slabs, one per axis. For each
    axis compute the time the mover enters that slab and the time it leaves.
    The boxes overlap during the window where *both* slabs are entered, so
    contact happens at ``max(entry_x, entry_y)`` and ends at
    ``min(exit_x, exit_y)``; if the start of the window is after its end,
    the mover passes by without touching.

    Returns ``None`` when there is no contact within this step. Otherwise the
    hit time is in ``[0, 1]`` as a fraction of ``velocity``.
    """
    # Entry/exit distances per axis, oriented by the direction of travel.
    if velocity.x > 0.0:
        x_entry_distance = target.left - mover.right
        x_exit_distance = target.right - mover.left
    else:
        x_entry_distance = target.right - mover.left
        x_exit_distance = target.left - mover.right

    if velocity.y > 0.0:
        y_entry_distance = target.top - mover.bottom
        y_exit_distance = target.bottom - mover.top
    else:
        y_entry_distance = target.bottom - mover.top
        y_exit_distance = target.top - mover.bottom

    # A zero-velocity axis never enters or leaves its slab. If the boxes
    # already overlap on that axis the constraint is satisfied for all time
    # (-inf, +inf); if they do not, it is never satisfied and no contact is
    # possible. Dividing by zero here instead is the classic source of a
    # NaN that silently poisons the whole physics step.
    if velocity.x == 0.0:
        if mover.right <= target.left or mover.left >= target.right:
            return None
        x_entry, x_exit = -math.inf, math.inf
    else:
        x_entry = x_entry_distance / velocity.x
        x_exit = x_exit_distance / velocity.x

    if velocity.y == 0.0:
        if mover.bottom <= target.top or mover.top >= target.bottom:
            return None
        y_entry, y_exit = -math.inf, math.inf
    else:
        y_entry = y_entry_distance / velocity.y
        y_exit = y_exit_distance / velocity.y

    entry_time = max(x_entry, y_entry)
    exit_time = min(x_exit, y_exit)

    # No overlap window, already past it, or contact happens beyond this step.
    if entry_time > exit_time or entry_time > 1.0 or exit_time <= 0.0:
        return None
    if x_entry > 1.0 or y_entry > 1.0:
        return None

    # Starting already overlapped: report an immediate hit rather than
    # pretending nothing is wrong, so the caller can push out.
    entry_time = max(entry_time, 0.0)

    # The axis that entered last is the face that was struck.
    if x_entry > y_entry:
        normal = Vec2(-1.0, 0.0) if velocity.x > 0.0 else Vec2(1.0, 0.0)
    else:
        normal = Vec2(0.0, -1.0) if velocity.y > 0.0 else Vec2(0.0, 1.0)

    contact = Vec2(mover.x + velocity.x * entry_time, mover.y + velocity.y * entry_time)
    return Hit(time=entry_time, normal=normal, position=contact, target=target)


def sweep_against(mover: AABB, velocity: Vec2, targets: Iterable[AABB]) -> Optional[Hit]:
    """Earliest contact between ``mover`` and any of ``targets``.

    Only the first hit matters: everything after it is on a trajectory the
    mover never actually takes.
    """
    earliest: Optional[Hit] = None
    for target in targets:
        hit = swept_aabb(mover, velocity, target)
        if hit is not None and (earliest is None or hit.time < earliest.time):
            earliest = hit
    return earliest


def ray_vs_aabb(origin: Vec2, direction: Vec2, box: AABB, max_distance: float = math.inf) -> Optional[Hit]:
    """Ray against box — the same slab method with a zero-sized mover.

    Used for line of sight, hitscan weapons and mouse picking.
    """
    hit = swept_aabb(AABB(origin.x, origin.y, 0.0, 0.0), direction, box)
    if hit is None:
        return None
    distance = hit.time * direction.length()
    if distance > max_distance:
        return None
    return hit


# -- response ------------------------------------------------------------


def move_and_slide(
    box: AABB,
    velocity: Vec2,
    obstacles: Sequence[AABB],
    max_iterations: int = 4,
    skin: float = 1e-4,
) -> tuple[Vec2, list[Hit]]:
    """Move as far as possible, sliding along whatever is struck.

    Stopping dead on contact makes a game feel like it is made of glue: brush
    a wall while running diagonally and you stop instead of continuing along
    it. Sliding keeps the component of motion parallel to the surface and
    discards only the component into it, which is what players expect.

    Iterating matters because sliding off one surface can push into another —
    the inside of a corner needs two resolutions in a single step. The
    iteration cap stops a wedge-shaped dead end from looping forever.

    ``skin`` leaves a hair of space at the contact point, measured **in world
    units, not as a fraction of the step**. Landing *exactly* on a surface
    means the next step starts flush against it, and floating point then
    decides at random whether that counts as touching, which shows up as an
    entity flickering between grounded and airborne. Expressing the gap as a
    fraction of the motion instead is a trap: it makes the standoff distance
    proportional to speed, so a bullet stops visibly short of the wall it hit
    while a slow walker is left touching it.

    Returns the movement actually performed and the hits encountered.
    """
    remaining = velocity
    total = Vec2(0.0, 0.0)
    hits: list[Hit] = []
    current = box

    for _ in range(max_iterations):
        speed = remaining.length()
        if speed == 0.0:
            break

        hit = sweep_against(current, remaining, obstacles)
        if hit is None:
            total = total + remaining
            current = current.translated(remaining)
            break

        # Back off by a fixed distance, converted into this step's time units.
        safe_time = max(0.0, hit.time - skin / speed)
        step = remaining * safe_time
        total = total + step
        current = current.translated(step)
        hits.append(hit)

        # Project the leftover motion onto the surface: drop the component
        # heading into it, keep the component running along it.
        leftover = remaining * (1.0 - safe_time)
        remaining = leftover - hit.normal * leftover.dot(hit.normal)

    return total, hits
