"""Tests for the software renderer and the PNG encoder.

The PNG tests decode the encoder's output with an independent minimal decoder
written here in the test. Encoding and decoding with the same helper would
only prove self-consistency; unfiltering the bytes by hand proves the file
really is a PNG that any decoder can read.
"""

from __future__ import annotations

import struct
import unittest
import zlib

from gamedev.render.font import GLYPH_HEIGHT, GLYPH_WIDTH, glyph, text_width, validate
from gamedev.render.png import encode_png
from gamedev.render.surface import PALETTE, Sprite, Surface

RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
BLACK = (0, 0, 0)


def decode_png(data: bytes) -> tuple[int, int, bytes]:
    """A minimal, independent PNG decoder: returns (width, height, rgb)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "bad signature"
    offset = 8
    width = height = 0
    idat = b""
    saw_end = False

    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        tag = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        (stored_crc,) = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])
        assert zlib.crc32(tag + payload) & 0xFFFFFFFF == stored_crc, f"CRC mismatch in {tag!r}"

        if tag == b"IHDR":
            width, height, depth, color_type, comp, filt, interlace = struct.unpack(">IIBBBBB", payload)
            assert (depth, color_type, comp, filt, interlace) == (8, 2, 0, 0, 0)
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            saw_end = True
        offset += 12 + length

    assert saw_end, "missing IEND"

    raw = zlib.decompress(idat)
    stride = width * 3
    out = bytearray()
    previous = bytearray(stride)
    position = 0
    for _ in range(height):
        filter_type = raw[position]
        position += 1
        line = bytearray(raw[position : position + stride])
        position += stride
        if filter_type == 0:
            pass
        elif filter_type == 1:  # Sub
            for i in range(stride):
                left = line[i - 3] if i >= 3 else 0
                line[i] = (line[i] + left) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        else:
            raise AssertionError(f"unexpected filter type {filter_type}")
        out.extend(line)
        previous = line
    return width, height, bytes(out)


class TestPng(unittest.TestCase):
    def test_round_trips_exactly(self) -> None:
        surface = Surface(23, 17, PALETTE["night"])
        surface.fill_rect(3, 4, 10, 6, RED)
        surface.line(0, 0, 22, 16, GREEN)
        surface.fill_circle(15, 12, 4, BLUE)

        width, height, pixels = decode_png(encode_png(23, 17, bytes(surface.pixels)))
        self.assertEqual((width, height), (23, 17))
        self.assertEqual(pixels, bytes(surface.pixels))

    def test_channel_order_is_rgb(self) -> None:
        # A single red pixel must decode as FF 00 00, not 00 00 FF.
        surface = Surface(1, 1, RED)
        _, _, pixels = decode_png(encode_png(1, 1, bytes(surface.pixels)))
        self.assertEqual(pixels, b"\xff\x00\x00")

    def test_all_filter_types_round_trip(self) -> None:
        # Content chosen so the adaptive heuristic picks different filters:
        # flat rows favour Up, horizontal gradients favour Sub.
        surface = Surface(64, 48, BLACK)
        for y in range(16):
            surface.fill_rect(0, y, 64, 1, PALETTE["teal"])  # flat -> Up
        for y in range(16, 32):
            for x in range(64):
                surface.set_pixel(x, y, (x * 4 % 256, 30, 200))  # ramp -> Sub
        for y in range(32, 48):
            for x in range(64):
                surface.set_pixel(x, y, ((x * 37 + y * 91) % 256, (x * 13) % 256, (y * 7) % 256))

        encoded = encode_png(64, 48, bytes(surface.pixels))
        _, _, pixels = decode_png(encoded)
        self.assertEqual(pixels, bytes(surface.pixels))

        # And confirm the encoder really did vary its filter choice.
        raw = zlib.decompress(b"".join(_idat_payloads(encoded)))
        stride = 64 * 3
        used = {raw[row * (stride + 1)] for row in range(48)}
        self.assertGreater(len(used), 1, f"expected mixed filters, saw {used}")

    def test_filtering_actually_compresses(self) -> None:
        # A horizontal gradient is near-incompressible raw and trivial after
        # the Sub filter. If this ratio collapses, filtering has broken.
        surface = Surface(256, 64)
        for y in range(64):
            for x in range(256):
                surface.set_pixel(x, y, (x, x, x))
        encoded = encode_png(256, 64, bytes(surface.pixels))
        self.assertLess(len(encoded), len(surface.pixels) // 20)

    def test_rejects_wrong_sized_buffer(self) -> None:
        with self.assertRaises(ValueError):
            encode_png(4, 4, b"\x00" * 10)


def _idat_payloads(data: bytes) -> list[bytes]:
    payloads = []
    offset = 8
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        tag = data[offset + 4 : offset + 8]
        if tag == b"IDAT":
            payloads.append(data[offset + 8 : offset + 8 + length])
        offset += 12 + length
    return payloads


class TestSurface(unittest.TestCase):
    def test_clear_and_get(self) -> None:
        surface = Surface(4, 3, RED)
        self.assertEqual(surface.get_pixel(0, 0), RED)
        self.assertEqual(surface.get_pixel(3, 2), RED)
        surface.clear(BLUE)
        self.assertEqual(surface.get_pixel(2, 1), BLUE)

    def test_set_pixel_out_of_bounds_is_ignored(self) -> None:
        surface = Surface(4, 4, BLACK)
        for x, y in ((-1, 0), (0, -1), (4, 0), (0, 4), (99, 99)):
            surface.set_pixel(x, y, RED)
        self.assertEqual(bytes(surface.pixels), bytes(BLACK) * 16)

    def test_fill_rect_clips_without_wrapping(self) -> None:
        """The bug this guards: a rect hanging off the left edge reappearing
        on the right one row up, because the flat buffer was indexed without
        a per-row bound."""
        surface = Surface(20, 2, BLACK)
        surface.fill_rect(-5, 0, 10, 1, RED)
        for x in range(20):
            expected = RED if x < 5 else BLACK
            self.assertEqual(surface.get_pixel(x, 0), expected, f"x={x}")
        for x in range(20):
            self.assertEqual(surface.get_pixel(x, 1), BLACK, f"row 1 x={x} was touched")

    def test_fill_rect_clips_on_every_edge(self) -> None:
        for x, y, w, h in ((-50, 0, 10, 10), (45, 0, 10, 10), (0, -50, 10, 10), (0, 45, 10, 10)):
            surface = Surface(10, 10, BLACK)
            surface.fill_rect(x, y, w, h, RED)  # entirely outside
            self.assertEqual(bytes(surface.pixels), bytes(BLACK) * 100, f"({x},{y}) leaked")

    def test_fill_rect_covers_exactly_its_extent(self) -> None:
        surface = Surface(10, 10, BLACK)
        surface.fill_rect(2, 3, 4, 5, GREEN)
        for y in range(10):
            for x in range(10):
                inside = 2 <= x < 6 and 3 <= y < 8
                self.assertEqual(surface.get_pixel(x, y), GREEN if inside else BLACK, f"({x},{y})")

    def test_zero_sized_rect_draws_nothing(self) -> None:
        surface = Surface(5, 5, BLACK)
        surface.fill_rect(1, 1, 0, 3, RED)
        surface.fill_rect(1, 1, 3, 0, RED)
        self.assertEqual(bytes(surface.pixels), bytes(BLACK) * 25)

    def test_line_hits_both_endpoints(self) -> None:
        for x1, y1 in ((19, 0), (0, 19), (19, 19), (0, 0), (19, 5), (5, 19)):
            surface = Surface(20, 20, BLACK)
            surface.line(0, 0, x1, y1, RED)
            self.assertEqual(surface.get_pixel(0, 0), RED, f"start missing for {(x1, y1)}")
            self.assertEqual(surface.get_pixel(x1, y1), RED, f"end missing for {(x1, y1)}")

    def test_line_is_gapless_on_every_slope(self) -> None:
        # Walk the long axis; every column (for shallow lines) or row (for
        # steep ones) must be touched. Gaps here are the classic sign of a
        # broken error term. The surface is sized so no line is clipped —
        # clipping would look exactly like a gap and mask a real bug.
        size = 48
        origin_x, origin_y = 24, 20
        for dx, dy in ((20, 3), (20, 19), (20, 20), (3, 20), (20, -7), (-20, 19), (1, 20)):
            surface = Surface(size, size, BLACK)
            end_x, end_y = origin_x + dx, origin_y + dy
            self.assertTrue(0 <= end_x < size and 0 <= end_y < size, "test line escapes the surface")
            surface.line(origin_x, origin_y, end_x, end_y, RED)

            if abs(dx) >= abs(dy):  # shallow: one pixel per column
                span = range(min(origin_x, end_x), max(origin_x, end_x) + 1)
                for x in span:
                    hits = [y for y in range(size) if surface.get_pixel(x, y) == RED]
                    self.assertTrue(hits, f"gap at x={x} for slope {(dx, dy)}")
            else:  # steep: one pixel per row
                span = range(min(origin_y, end_y), max(origin_y, end_y) + 1)
                for y in span:
                    hits = [x for x in range(size) if surface.get_pixel(x, y) == RED]
                    self.assertTrue(hits, f"gap at y={y} for slope {(dx, dy)}")

    def test_line_of_zero_length_is_one_pixel(self) -> None:
        surface = Surface(5, 5, BLACK)
        surface.line(2, 2, 2, 2, RED)
        self.assertEqual(surface.get_pixel(2, 2), RED)
        self.assertEqual(sum(1 for i in range(25) if surface.pixels[i * 3] == 255), 1)

    def test_line_clips_off_screen(self) -> None:
        surface = Surface(8, 8, BLACK)
        surface.line(-100, -100, 100, 100, RED)  # must not raise
        self.assertEqual(surface.get_pixel(0, 0), RED)
        self.assertEqual(surface.get_pixel(7, 7), RED)

    def test_fill_circle_is_symmetric_and_bounded(self) -> None:
        surface = Surface(41, 41, BLACK)
        surface.fill_circle(20, 20, 15, RED)
        self.assertEqual(surface.get_pixel(20, 20), RED)
        # Symmetry across both axes.
        for offset in range(1, 15):
            self.assertEqual(surface.get_pixel(20 + offset, 20), surface.get_pixel(20 - offset, 20))
            self.assertEqual(surface.get_pixel(20, 20 + offset), surface.get_pixel(20, 20 - offset))
        # Nothing outside the radius.
        self.assertEqual(surface.get_pixel(20 + 16, 20), BLACK)
        self.assertEqual(surface.get_pixel(20, 20 + 16), BLACK)

    def test_circle_outline_is_hollow(self) -> None:
        surface = Surface(41, 41, BLACK)
        surface.circle_outline(20, 20, 15, RED)
        self.assertEqual(surface.get_pixel(20, 20), BLACK)
        self.assertEqual(surface.get_pixel(20 + 15, 20), RED)

    def test_negative_radius_draws_nothing(self) -> None:
        surface = Surface(9, 9, BLACK)
        surface.fill_circle(4, 4, -3, RED)
        surface.circle_outline(4, 4, -3, RED)
        self.assertEqual(bytes(surface.pixels), bytes(BLACK) * 81)

    def test_blit_clips_at_edges(self) -> None:
        source = Surface(6, 6, GREEN)
        surface = Surface(10, 10, BLACK)
        surface.blit(source, -3, -3)
        surface.blit(source, 7, 7)
        self.assertEqual(surface.get_pixel(0, 0), GREEN)
        self.assertEqual(surface.get_pixel(2, 2), GREEN)
        self.assertEqual(surface.get_pixel(3, 3), BLACK)
        self.assertEqual(surface.get_pixel(9, 9), GREEN)

    def test_blit_fully_offscreen_is_a_noop(self) -> None:
        surface = Surface(10, 10, BLACK)
        surface.blit(Surface(4, 4, GREEN), 50, 50)
        self.assertEqual(bytes(surface.pixels), bytes(BLACK) * 100)

    def test_scaled_replicates_pixels(self) -> None:
        source = Surface(2, 2, BLACK)
        source.set_pixel(0, 0, RED)
        source.set_pixel(1, 1, GREEN)
        scaled = source.scaled(3)
        self.assertEqual((scaled.width, scaled.height), (6, 6))
        for y in range(3):
            for x in range(3):
                self.assertEqual(scaled.get_pixel(x, y), RED)
                self.assertEqual(scaled.get_pixel(x + 3, y + 3), GREEN)
        self.assertEqual(scaled.get_pixel(3, 0), BLACK)

    def test_copy_is_independent(self) -> None:
        original = Surface(4, 4, RED)
        clone = original.copy()
        clone.clear(BLUE)
        self.assertEqual(original.get_pixel(0, 0), RED)

    def test_rejects_degenerate_size(self) -> None:
        with self.assertRaises(ValueError):
            Surface(0, 5)
        with self.assertRaises(ValueError):
            Surface(5, -1)


class TestSprite(unittest.TestCase):
    ART = ["..R..", ".RGR.", "RGBGR", ".RGR.", "..R.."]
    COLORS = {"R": RED, "G": GREEN, "B": BLUE}

    def test_from_ascii_skips_transparent(self) -> None:
        sprite = Sprite.from_ascii(self.ART, self.COLORS)
        self.assertEqual((sprite.width, sprite.height), (5, 5))
        self.assertEqual(len(sprite.texels), 13)  # the diamond, not the 25 cells

    def test_transparent_pixels_are_not_drawn(self) -> None:
        surface = Surface(5, 5, BLACK)
        surface.draw_sprite(Sprite.from_ascii(self.ART, self.COLORS), 0, 0)
        self.assertEqual(surface.get_pixel(0, 0), BLACK)  # corner stays background
        self.assertEqual(surface.get_pixel(2, 2), BLUE)  # centre drawn

    def test_flip_mirrors_horizontally(self) -> None:
        sprite = Sprite.from_ascii(["RG...", ".....", "....."], self.COLORS)
        flipped = sprite.flipped_horizontally()
        surface = Surface(5, 3, BLACK)
        surface.draw_sprite(flipped, 0, 0)
        self.assertEqual(surface.get_pixel(4, 0), RED)
        self.assertEqual(surface.get_pixel(3, 0), GREEN)

    def test_tint_overrides_every_colour(self) -> None:
        surface = Surface(5, 5, BLACK)
        surface.draw_sprite(Sprite.from_ascii(self.ART, self.COLORS), 0, 0, tint=BLUE)
        self.assertEqual(surface.get_pixel(2, 0), BLUE)
        self.assertEqual(surface.get_pixel(2, 2), BLUE)

    def test_unknown_palette_character_is_an_error(self) -> None:
        with self.assertRaises(KeyError):
            Sprite.from_ascii(["X"], self.COLORS)

    def test_sprite_clips_at_surface_edge(self) -> None:
        surface = Surface(5, 5, BLACK)
        surface.draw_sprite(Sprite.from_ascii(self.ART, self.COLORS), -3, -3)  # must not raise
        surface.draw_sprite(Sprite.from_ascii(self.ART, self.COLORS), 4, 4)


class TestFont(unittest.TestCase):
    def test_every_glyph_is_well_formed(self) -> None:
        self.assertEqual(validate(), [])

    def test_glyph_lookup_is_case_insensitive(self) -> None:
        self.assertEqual(glyph("a"), glyph("A"))

    def test_unknown_character_returns_none_rather_than_raising(self) -> None:
        self.assertIsNone(glyph("é"))

    def test_unknown_character_leaves_a_gap_and_does_not_crash(self) -> None:
        surface = Surface(60, 10, BLACK)
        surface.text(0, 0, "AéB", (255, 255, 255))  # middle glyph missing
        self.assertNotEqual(surface.get_pixel(1, 0), BLACK)  # A drawn
        self.assertEqual(surface.get_pixel(7, 0), BLACK)  # gap where e-acute was

    def test_text_width_matches_drawn_extent(self) -> None:
        message = "HELLO 123"
        surface = Surface(120, 12, BLACK)
        surface.text(0, 0, message, (255, 255, 255))
        rightmost = max(x for x in range(120) for y in range(12) if surface.get_pixel(x, y) != BLACK)
        self.assertLess(rightmost, text_width(message))
        self.assertGreaterEqual(rightmost, text_width(message) - GLYPH_WIDTH)

    def test_text_width_of_empty_string_is_zero(self) -> None:
        self.assertEqual(text_width(""), 0)

    def test_scaled_text_is_proportional(self) -> None:
        self.assertEqual(text_width("ABC", scale=2), text_width("ABC", scale=1) * 2)

    def test_text_clips_off_screen(self) -> None:
        surface = Surface(20, GLYPH_HEIGHT, BLACK)
        surface.text(-10, 0, "LONG MESSAGE", (255, 255, 255))  # must not raise or wrap
        surface.text(15, 0, "LONG MESSAGE", (255, 255, 255))


if __name__ == "__main__":
    unittest.main(verbosity=2)
