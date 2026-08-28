"""Render a test card exercising every drawing primitive.

Run it, then look at the PNG. A rendering bug — an off-by-one in a clip, a
swapped colour channel, a line algorithm that leaves gaps on steep slopes —
is instantly obvious in a picture and nearly invisible in a unit test.

    python3 -m gamedev.render.testcard
"""

from __future__ import annotations

import math
import os

from gamedev.render.font import GLYPHS, text_width
from gamedev.render.surface import PALETTE, Sprite, Surface

SHIP = Sprite.from_ascii(
    [
        "....W....",
        "...WWW...",
        "..WWCWW..",
        "..WWCWW..",
        ".WWWCWWW.",
        "WWW.C.WWW",
        "R.W.C.W.R",
        ".R.....R.",
        "..R...R..",
    ],
    {"W": PALETTE["mist"], "C": PALETTE["cyan"], "R": PALETTE["red"]},
)


def render() -> Surface:
    surface = Surface(400, 300, PALETTE["black"])

    # -- header ----------------------------------------------------------
    surface.fill_rect(0, 0, 400, 20, PALETTE["night"])
    surface.text(6, 7, "SOFTWARE RENDERER TEST CARD", PALETTE["white"])

    # -- palette swatches ------------------------------------------------
    surface.text(6, 28, "PALETTE", PALETTE["mist"])
    for index, (name, color) in enumerate(PALETTE.items()):
        x = 6 + (index % 9) * 43
        y = 40 + (index // 9) * 26
        surface.fill_rect(x, y, 40, 16, color)
        surface.rect_outline(x, y, 40, 16, PALETTE["slate"])
        surface.text(x + 2, y + 18, name[:5], PALETTE["steel"])

    # -- rectangles, clipping at every edge ------------------------------
    surface.text(6, 100, "RECTS + CLIP", PALETTE["mist"])
    surface.fill_rect(6, 112, 60, 40, PALETTE["blue"])
    surface.rect_outline(6, 112, 60, 40, PALETTE["cyan"], thickness=2)
    # Deliberately hanging off three edges: must clip, not wrap or crash.
    surface.fill_rect(-20, 112, 40, 40, PALETTE["indigo"])
    surface.fill_rect(376, 112, 40, 40, PALETTE["indigo"])
    surface.fill_rect(340, -10, 30, 30, PALETTE["violet"])

    # -- lines at every slope --------------------------------------------
    surface.text(110, 100, "BRESENHAM FAN", PALETTE["mist"])
    cx, cy = 140, 145
    for step in range(16):
        angle = step * math.tau / 16
        surface.line(cx, cy, cx + math.cos(angle) * 34, cy + math.sin(angle) * 34, PALETTE["lime"])
    surface.circle_outline(cx, cy, 34, PALETTE["slate"])

    # -- circles ---------------------------------------------------------
    surface.text(200, 100, "CIRCLES", PALETTE["mist"])
    surface.fill_circle(230, 145, 28, PALETTE["teal"])
    surface.circle_outline(230, 145, 28, PALETTE["white"])
    surface.fill_circle(300, 145, 16, PALETTE["orange"])
    surface.circle_outline(300, 145, 24, PALETTE["yellow"])
    # Clipped against the right edge.
    surface.fill_circle(396, 145, 20, PALETTE["pink"])

    # -- gradient, to catch channel-order mistakes -----------------------
    surface.text(6, 170, "GRADIENT (R->G->B)", PALETTE["mist"])
    for x in range(388):
        t = x / 387.0
        if t < 0.5:
            u = t * 2.0
            color = (int(255 * (1 - u)), int(255 * u), 0)
        else:
            u = (t - 0.5) * 2.0
            color = (0, int(255 * (1 - u)), int(255 * u))
        surface.fill_rect(6 + x, 182, 1, 14, color)

    # -- sprite blitting with transparency -------------------------------
    # Drawn before the text block so the panel cannot paint over it.
    surface.fill_rect(298, 200, 96, 58, PALETTE["night"])
    surface.text(302, 204, "SPRITE", PALETTE["mist"])
    surface.draw_sprite(SHIP, 306, 216)
    surface.draw_sprite(SHIP.flipped_horizontally(), 334, 216)
    surface.draw_sprite(SHIP, 362, 216, tint=PALETTE["yellow"])

    # -- text at several scales ------------------------------------------
    surface.text(6, 204, "TEXT SCALE 1 - 0123456789", PALETTE["white"])
    surface.text(6, 216, "SCALE 2 !?%", PALETTE["yellow"], scale=2)
    surface.text(6, 236, "SC 3", PALETTE["red"], scale=3)

    # Right-aligned against x=290, proving text_width agrees with what is
    # actually drawn: the glyphs must end exactly on that edge.
    label = "RIGHT ALIGNED"
    surface.text(290 - text_width(label), 248, label, PALETTE["green"])
    surface.fill_rect(291, 246, 1, 11, PALETTE["slate"])

    # -- footer ----------------------------------------------------------
    surface.fill_rect(0, 264, 400, 36, PALETTE["night"])
    surface.text(6, 270, f"GLYPHS: {len(GLYPHS)}   ZERO DEPENDENCIES", PALETTE["steel"])
    surface.text(6, 284, "PNG WRITTEN WITH ZLIB + STRUCT ONLY", PALETTE["steel"])
    return surface


def main() -> None:
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")
    os.makedirs(out_dir, exist_ok=True)
    surface = render()
    path = os.path.join(out_dir, "testcard.png")
    size = surface.save(path)
    print(f"wrote {path} ({surface.width}x{surface.height}, {size} bytes)")

    scaled = surface.scaled(2)
    path2 = os.path.join(out_dir, "testcard_2x.png")
    size2 = scaled.save(path2)
    print(f"wrote {path2} ({scaled.width}x{scaled.height}, {size2} bytes)")


if __name__ == "__main__":
    main()
