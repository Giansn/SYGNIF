"""Tile maps — the level representation almost every 2D game starts with.

A tile map is a grid of small integers. That sounds unambitious next to a list
of arbitrary polygons, and it is exactly why it wins: a grid gives you
constant-time lookup of "what is at this position", trivially cheap collision
broadphase, compact levels that can be authored as text, and a natural
coordinate system for pathfinding.

The one thing you must not do is iterate the whole map. A 200x60 level is
12 000 tiles; testing an entity against every one of them, sixty times a
second, per entity, is the difference between a game and a slideshow. Because
the map is a grid, the tiles an entity could possibly touch are found by
dividing its bounding box by the tile size — :meth:`TileMap.boxes_overlapping`
looks at the handful of tiles in that range and nothing else.
"""

from __future__ import annotations

from typing import Iterator, Sequence

from gamedev.engine.collision import AABB

__all__ = ["TileMap", "EMPTY", "SOLID", "ONEWAY", "HAZARD", "GOAL", "DEFAULT_LEGEND"]

EMPTY = 0
SOLID = 1
ONEWAY = 2  # collides only from above, so you can jump up through it
HAZARD = 3
GOAL = 4

DEFAULT_LEGEND = {
    ".": EMPTY,
    " ": EMPTY,
    "#": SOLID,
    "=": ONEWAY,
    "^": HAZARD,
    "G": GOAL,
}


class TileMap:
    """A rectangular grid of tile ids."""

    __slots__ = ("width", "height", "tile_size", "tiles", "solid_ids")

    def __init__(self, width: int, height: int, tile_size: int = 16, fill: int = EMPTY) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("map must have positive dimensions")
        self.width = width
        self.height = height
        self.tile_size = tile_size
        self.tiles = bytearray([fill]) * (width * height)
        self.solid_ids = {SOLID}

    # -- authoring -------------------------------------------------------

    @staticmethod
    def from_ascii(rows: Sequence[str], tile_size: int = 16, legend: dict[str, int] | None = None) -> "TileMap":
        """Build a level from ASCII art.

        Levels as text are readable in a diff, editable without a tool, and
        reviewable in a pull request. Every real game outgrows this and builds
        an editor, but starting here means the first level exists in minutes
        instead of after the editor does.
        """
        legend = legend or DEFAULT_LEGEND
        height = len(rows)
        width = max((len(row) for row in rows), default=0)
        if height == 0 or width == 0:
            raise ValueError("empty level")

        level = TileMap(width, height, tile_size)
        for y, row in enumerate(rows):
            for x, character in enumerate(row):
                if character not in legend:
                    raise KeyError(f"no legend entry for {character!r} at ({x}, {y})")
                level.tiles[y * width + x] = legend[character]
        return level

    def to_ascii(self, legend: dict[str, int] | None = None) -> list[str]:
        legend = legend or DEFAULT_LEGEND
        reverse = {value: key for key, value in legend.items() if key != " "}
        return [
            "".join(reverse.get(self.tiles[y * self.width + x], "?") for x in range(self.width))
            for y in range(self.height)
        ]

    # -- access ----------------------------------------------------------

    def in_bounds(self, tx: int, ty: int) -> bool:
        return 0 <= tx < self.width and 0 <= ty < self.height

    def get(self, tx: int, ty: int) -> int:
        """Tile at a grid coordinate.

        Out of bounds reads as ``SOLID`` on the sides and bottom but ``EMPTY``
        above the map. Treating the surround as solid means an entity can
        never leave the level through a wall, with no special-casing at the
        edges; leaving the sky open means a jump near the ceiling is not
        mysteriously blocked by nothing.
        """
        if not self.in_bounds(tx, ty):
            return EMPTY if ty < 0 else SOLID
        return self.tiles[ty * self.width + tx]

    def set(self, tx: int, ty: int, tile: int) -> None:
        if self.in_bounds(tx, ty):
            self.tiles[ty * self.width + tx] = tile

    def is_solid(self, tx: int, ty: int) -> bool:
        return self.get(tx, ty) in self.solid_ids

    def box_for(self, tx: int, ty: int) -> AABB:
        return AABB(tx * self.tile_size, ty * self.tile_size, self.tile_size, self.tile_size)

    # -- queries ---------------------------------------------------------

    def tile_range(self, box: AABB) -> tuple[int, int, int, int]:
        """The inclusive grid range a world-space box covers.

        The subtraction of one epsilon on the far edges matters: a box whose
        right edge sits exactly on a tile boundary does not overlap the tile
        beyond it, and including that column makes an entity collide with a
        wall it is merely flush against.
        """
        size = self.tile_size
        left = int(box.left // size)
        top = int(box.top // size)
        right = int((box.right - 1e-9) // size)
        bottom = int((box.bottom - 1e-9) // size)
        return left, top, right, bottom

    def tiles_overlapping(self, box: AABB) -> Iterator[tuple[int, int, int]]:
        """Yield ``(tx, ty, tile)`` for every cell the box touches."""
        left, top, right, bottom = self.tile_range(box)
        for ty in range(top, bottom + 1):
            for tx in range(left, right + 1):
                yield tx, ty, self.get(tx, ty)

    def boxes_overlapping(self, box: AABB, tile_ids: set[int] | None = None) -> list[AABB]:
        """Collision boxes for the matching tiles the box touches.

        This is the whole broadphase. It looks at the tiles under the entity
        and nothing else, so cost scales with the entity's size rather than
        with the size of the level.
        """
        wanted = tile_ids if tile_ids is not None else self.solid_ids
        return [
            self.box_for(tx, ty)
            for tx, ty, tile in self.tiles_overlapping(box)
            if tile in wanted
        ]

    def overlaps_any(self, box: AABB, tile_ids: set[int]) -> bool:
        return any(tile in tile_ids for _, _, tile in self.tiles_overlapping(box))

    def find(self, tile_id: int) -> list[tuple[int, int]]:
        return [
            (index % self.width, index // self.width)
            for index, tile in enumerate(self.tiles)
            if tile == tile_id
        ]

    # -- world/grid conversion -------------------------------------------

    def world_to_tile(self, x: float, y: float) -> tuple[int, int]:
        return int(x // self.tile_size), int(y // self.tile_size)

    def tile_to_world(self, tx: int, ty: int) -> tuple[float, float]:
        """Centre of a tile in world space."""
        return (tx + 0.5) * self.tile_size, (ty + 0.5) * self.tile_size

    @property
    def pixel_width(self) -> int:
        return self.width * self.tile_size

    @property
    def pixel_height(self) -> int:
        return self.height * self.tile_size

    def __repr__(self) -> str:
        return f"TileMap({self.width}x{self.height} @ {self.tile_size}px)"
