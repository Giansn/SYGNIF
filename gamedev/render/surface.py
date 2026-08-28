"""A software framebuffer — the drawing primitives, written from scratch.

A ``Surface`` is width x height x 3 bytes of RGB in a flat :class:`bytearray`,
which is exactly what a GPU texture or an SDL surface is underneath. Doing it
by hand makes the cost model visible in a way that a graphics API hides: a
filled rectangle is a handful of slice assignments, a line is one pass over
its longer axis, and a naive per-pixel Python loop over a full screen is
roughly a hundred times slower than either.

Two rules run through all of it:

* **Clip, never crash.** Games draw off the edge of the screen constantly —
  an entity walking out of view, an explosion near a corner. Every primitive
  clips to the surface bounds rather than raising or wrapping around, because
  wrapping is the bug where a sprite leaving the left edge reappears on the
  right one row down.
* **Integers at the boundary.** Positions are floats in the simulation and
  pixels are integers on the screen; the conversion happens here, once, at
  the drawing call, rather than being smeared through the game logic.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from gamedev.render.png import write_png

__all__ = ["Surface", "Sprite", "Color", "PALETTE"]

Color = tuple[int, int, int]

#: A small fixed palette. Constraining colour choice up front is the oldest
#: trick for making programmer art look deliberate rather than accidental.
PALETTE: dict[str, Color] = {
    "black": (13, 14, 20),
    "night": (26, 28, 44),
    "slate": (51, 60, 87),
    "steel": (86, 108, 134),
    "mist": (150, 162, 179),
    "white": (243, 246, 250),
    "red": (222, 62, 71),
    "orange": (240, 138, 60),
    "yellow": (250, 209, 96),
    "lime": (140, 200, 80),
    "green": (60, 163, 112),
    "teal": (54, 170, 168),
    "cyan": (99, 199, 216),
    "blue": (66, 122, 205),
    "indigo": (86, 84, 168),
    "violet": (140, 100, 190),
    "pink": (222, 118, 168),
    "brown": (128, 86, 63),
}


class Surface:
    """A mutable RGB framebuffer."""

    __slots__ = ("width", "height", "pixels", "_stride")

    def __init__(self, width: int, height: int, fill: Color = (0, 0, 0)) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("surface must have positive dimensions")
        self.width = width
        self.height = height
        self._stride = width * 3
        self.pixels = bytearray(bytes(fill) * (width * height))

    # -- whole-surface ---------------------------------------------------

    def clear(self, color: Color = (0, 0, 0)) -> None:
        """Reset every pixel.

        Slice-assigning a pre-multiplied bytes object pushes the loop into C;
        the equivalent Python loop over 76 800 pixels is ~200x slower and
        would dominate the frame on its own.
        """
        self.pixels[:] = bytes(color) * (self.width * self.height)

    def copy(self) -> "Surface":
        clone = Surface(self.width, self.height)
        clone.pixels[:] = self.pixels
        return clone

    # -- points ----------------------------------------------------------

    def set_pixel(self, x: int, y: int, color: Color) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            offset = y * self._stride + x * 3
            self.pixels[offset : offset + 3] = bytes(color)

    def get_pixel(self, x: int, y: int) -> Color:
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise IndexError(f"({x}, {y}) is outside {self.width}x{self.height}")
        offset = y * self._stride + x * 3
        return (self.pixels[offset], self.pixels[offset + 1], self.pixels[offset + 2])

    # -- rectangles ------------------------------------------------------

    def fill_rect(self, x: float, y: float, width: float, height: float, color: Color) -> None:
        """Filled axis-aligned rectangle, clipped to the surface."""
        left = max(0, int(x))
        top = max(0, int(y))
        right = min(self.width, int(x + width))
        bottom = min(self.height, int(y + height))
        if left >= right or top >= bottom:
            return  # entirely off-screen

        row = bytes(color) * (right - left)
        start = left * 3
        for py in range(top, bottom):
            offset = py * self._stride + start
            self.pixels[offset : offset + len(row)] = row

    def rect_outline(self, x: float, y: float, width: float, height: float, color: Color, thickness: int = 1) -> None:
        thickness = max(1, thickness)
        self.fill_rect(x, y, width, thickness, color)
        self.fill_rect(x, y + height - thickness, width, thickness, color)
        self.fill_rect(x, y, thickness, height, color)
        self.fill_rect(x + width - thickness, y, thickness, height, color)

    # -- lines -----------------------------------------------------------

    def line(self, x0: float, y0: float, x1: float, y1: float, color: Color) -> None:
        """Bresenham's line algorithm.

        The idea is to step one pixel at a time along whichever axis the line
        is longer on, carrying an integer error term that says when to step on
        the other axis too. No division, no floating point, no gaps — which is
        why it has outlived every processor it was designed for.
        """
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        step_x = 1 if x0 < x1 else -1
        step_y = 1 if y0 < y1 else -1
        error = dx + dy

        while True:
            self.set_pixel(x0, y0, color)
            if x0 == x1 and y0 == y1:
                return
            doubled = 2 * error
            # Comparing against dy/dx rather than halving keeps it integer.
            if doubled >= dy:
                if x0 == x1:
                    return
                error += dy
                x0 += step_x
            if doubled <= dx:
                if y0 == y1:
                    return
                error += dx
                y0 += step_y

    def polyline(self, points: Sequence[tuple[float, float]], color: Color, closed: bool = False) -> None:
        if len(points) < 2:
            return
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            self.line(x0, y0, x1, y1, color)
        if closed:
            self.line(points[-1][0], points[-1][1], points[0][0], points[0][1], color)

    # -- circles ---------------------------------------------------------

    def circle_outline(self, cx: float, cy: float, radius: float, color: Color) -> None:
        """Midpoint circle algorithm — Bresenham's idea applied to a circle.

        Only one octant is walked; the other seven are free by symmetry.
        """
        cx, cy, radius = int(cx), int(cy), int(radius)
        if radius < 0:
            return
        x, y = radius, 0
        error = 1 - radius
        while x >= y:
            for px, py in (
                (cx + x, cy + y), (cx + y, cy + x), (cx - y, cy + x), (cx - x, cy + y),
                (cx - x, cy - y), (cx - y, cy - x), (cx + y, cy - x), (cx + x, cy - y),
            ):
                self.set_pixel(px, py, color)
            y += 1
            if error < 0:
                error += 2 * y + 1
            else:
                x -= 1
                error += 2 * (y - x) + 1

    def fill_circle(self, cx: float, cy: float, radius: float, color: Color) -> None:
        """Filled circle, drawn as horizontal spans.

        Spans rather than per-pixel tests: one ``fill_rect`` per row turns an
        O(r^2) Python loop into O(r) slice assignments.
        """
        cx, cy = int(cx), int(cy)
        radius_int = int(radius)
        if radius_int < 0:
            return
        r_squared = radius * radius
        for dy in range(-radius_int, radius_int + 1):
            span = int((r_squared - dy * dy) ** 0.5) if r_squared >= dy * dy else 0
            self.fill_rect(cx - span, cy + dy, 2 * span + 1, 1, color)

    # -- composition -----------------------------------------------------

    def blit(self, source: "Surface", x: int, y: int) -> None:
        """Copy another surface in, clipped, row by row."""
        left = max(0, x)
        top = max(0, y)
        right = min(self.width, x + source.width)
        bottom = min(self.height, y + source.height)
        if left >= right or top >= bottom:
            return
        span = (right - left) * 3
        for py in range(top, bottom):
            src_offset = (py - y) * source._stride + (left - x) * 3
            dst_offset = py * self._stride + left * 3
            self.pixels[dst_offset : dst_offset + span] = source.pixels[src_offset : src_offset + span]

    def draw_sprite(self, sprite: "Sprite", x: int, y: int, tint: Color | None = None) -> None:
        """Draw a sprite, skipping its transparent pixels."""
        for dx, dy, color in sprite.texels:
            self.set_pixel(x + dx, y + dy, tint or color)

    # -- text ------------------------------------------------------------

    def text(self, x: int, y: int, message: str, color: Color, scale: int = 1, spacing: int = 1) -> int:
        """Draw text with the built-in bitmap font. Returns the width drawn."""
        from gamedev.render.font import GLYPH_HEIGHT, GLYPH_WIDTH, glyph

        cursor = x
        advance = (GLYPH_WIDTH + spacing) * scale
        for character in message.upper():
            rows = glyph(character)
            if rows is not None:
                for row_index, row in enumerate(rows):
                    for column, bit in enumerate(row):
                        if bit == "#":
                            if scale == 1:
                                self.set_pixel(cursor + column, y + row_index, color)
                            else:
                                self.fill_rect(
                                    cursor + column * scale, y + row_index * scale, scale, scale, color
                                )
            cursor += advance
        _ = GLYPH_HEIGHT
        return cursor - x

    # -- output ----------------------------------------------------------

    def save(self, path: str) -> int:
        return write_png(path, self.width, self.height, bytes(self.pixels))

    def scaled(self, factor: int) -> "Surface":
        """Nearest-neighbour upscale.

        Low-resolution art must be scaled with nearest-neighbour, never a
        smooth filter: interpolation is exactly what destroys the crisp pixel
        edges that make the art read.
        """
        if factor < 1:
            raise ValueError("scale factor must be >= 1")
        if factor == 1:
            return self.copy()
        out = Surface(self.width * factor, self.height * factor)
        for y in range(self.height):
            source_row = self.pixels[y * self._stride : (y + 1) * self._stride]
            # Widen the row once, then repeat it `factor` times.
            wide = bytearray()
            for x in range(self.width):
                wide.extend(source_row[x * 3 : x * 3 + 3] * factor)
            for repeat in range(factor):
                target = (y * factor + repeat) * out._stride
                out.pixels[target : target + len(wide)] = wide
        return out

    def __repr__(self) -> str:
        return f"Surface({self.width}x{self.height})"


class Sprite:
    """A small bitmap with binary transparency, authored as ASCII art.

    Keeping art in source rather than in image files means the whole project
    stays diffable and needs no asset pipeline. It scales badly past a certain
    size, which is precisely why real games have an asset pipeline — but at
    this scale it is the right trade.
    """

    __slots__ = ("width", "height", "texels")

    def __init__(self, width: int, height: int, texels: Iterable[tuple[int, int, Color]]) -> None:
        self.width = width
        self.height = height
        self.texels = list(texels)

    @staticmethod
    def from_ascii(rows: Sequence[str], palette: dict[str, Color], transparent: str = ".") -> "Sprite":
        """Build a sprite from ASCII art plus a character -> colour mapping."""
        height = len(rows)
        width = max((len(row) for row in rows), default=0)
        texels: list[tuple[int, int, Color]] = []
        for y, row in enumerate(rows):
            for x, character in enumerate(row):
                if character == transparent or character == " ":
                    continue
                if character not in palette:
                    raise KeyError(f"no palette entry for {character!r}")
                texels.append((x, y, palette[character]))
        return Sprite(width, height, texels)

    def flipped_horizontally(self) -> "Sprite":
        return Sprite(
            self.width,
            self.height,
            [(self.width - 1 - x, y, color) for x, y, color in self.texels],
        )

    def __repr__(self) -> str:
        return f"Sprite({self.width}x{self.height}, {len(self.texels)} texels)"
