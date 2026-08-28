"""A PNG encoder built on nothing but :mod:`zlib` and :mod:`struct`.

Writing an image format by hand is not busywork here — it is what makes the
rest of this project verifiable. Without it, "the collision response works"
would be an assertion about numbers. With it, I can render the frame and look
at the picture.

PNG is a good format to implement from scratch because it is genuinely simple
underneath: a magic signature, then a sequence of length-prefixed, CRC-checked
chunks. Only three chunks are required.

* ``IHDR`` — width, height, bit depth, colour type.
* ``IDAT`` — the pixels, zlib-compressed.
* ``IEND`` — a zero-length terminator.

The one part that is not obvious is *filtering*. Before compression, every
scanline is prefixed with a filter byte saying how that row was transformed.
Filters do not compress anything themselves; they rearrange the data so that
zlib's job becomes easy. A horizontal gradient looks like noise to a
dictionary compressor, but the difference between each pixel and the one to
its left is a long run of the same small number, which compresses to almost
nothing. Choosing the filter per scanline is where most of a PNG encoder's
output size is won or lost.
"""

from __future__ import annotations

import struct
import zlib
from typing import BinaryIO

__all__ = ["write_png", "encode_png"]

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CHANNELS = 3  # RGB, PNG colour type 2


def _chunk(tag: bytes, data: bytes) -> bytes:
    """Length, type, payload, CRC — the PNG chunk envelope."""
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def _filter_scanlines(pixels: bytes, width: int, height: int) -> bytearray:
    """Prefix each scanline with the filter that should compress best.

    The heuristic is the one from the PNG specification: pick the filter whose
    output has the smallest sum of absolute (signed) byte values, on the
    reasoning that bytes clustered near zero have low entropy. It is cheap and
    close enough to optimal that nobody has found it worth replacing.

    Only ``None`` (0), ``Sub`` (1) and ``Up`` (2) are implemented. Sub encodes
    horizontal similarity, Up encodes vertical similarity, and between them
    they cover flat fills, gradients and tile grids — which is essentially all
    of the output of a 2D game. Average and Paeth would add maybe a few
    percent on photographic content this encoder will never see.
    """
    stride = width * _CHANNELS
    out = bytearray()
    previous = bytearray(stride)

    for y in range(height):
        line = pixels[y * stride : (y + 1) * stride]

        # Filter 1 (Sub): difference from the pixel to the left.
        sub = bytearray(stride)
        for i in range(stride):
            left = line[i - _CHANNELS] if i >= _CHANNELS else 0
            sub[i] = (line[i] - left) & 0xFF

        # Filter 2 (Up): difference from the pixel above.
        up = bytearray(stride)
        for i in range(stride):
            up[i] = (line[i] - previous[i]) & 0xFF

        best_tag, best_data, best_score = 0, line, _score(line)
        for tag, data in ((1, sub), (2, up)):
            score = _score(data)
            if score < best_score:
                best_tag, best_data, best_score = tag, data, score

        out.append(best_tag)
        out.extend(best_data)
        previous = line

    return out


def _score(data: bytes) -> int:
    """Sum of absolute signed byte values — the PNG filter heuristic."""
    return sum(byte if byte < 128 else 256 - byte for byte in data)


def encode_png(width: int, height: int, pixels: bytes, compression: int = 6) -> bytes:
    """Encode raw RGB bytes (row-major, 3 bytes per pixel) as a PNG file."""
    expected = width * height * _CHANNELS
    if len(pixels) != expected:
        raise ValueError(f"expected {expected} bytes of RGB data, got {len(pixels)}")

    header = struct.pack(
        ">IIBBBBB",
        width,
        height,
        8,  # bits per channel
        2,  # colour type 2 = truecolour RGB
        0,  # compression method: deflate (the only one defined)
        0,  # filter method: adaptive per-scanline (the only one defined)
        0,  # interlace: none
    )
    filtered = _filter_scanlines(pixels, width, height)
    return (
        _SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(filtered), compression))
        + _chunk(b"IEND", b"")
    )


def write_png(path: str, width: int, height: int, pixels: bytes, compression: int = 6) -> int:
    """Write a PNG to disk. Returns the number of bytes written."""
    data = encode_png(width, height, pixels, compression)
    with open(path, "wb") as handle:
        handle.write(data)
    return len(data)


def write_png_to(stream: BinaryIO, width: int, height: int, pixels: bytes) -> int:
    data = encode_png(width, height, pixels)
    stream.write(data)
    return len(data)
