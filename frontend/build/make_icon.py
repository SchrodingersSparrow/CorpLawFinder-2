"""Generate the application icon (Stage 8) — standard library only.

Draws the app's mark — a green legal-paper page with a deep-green docket
spine, a crimson index tab, and ink rule lines — then packs it into
``icon.ico`` (256 px PNG frame + 64/48/32/16 px BMP frames, the layout
Windows and electron-builder expect) and ``icon.png`` (used by Linux/macOS
builds and the README).

Run from anywhere::

    python frontend/build/make_icon.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The design system's colours (renderer/styles/tokens.css).
PAPER = (238, 241, 234, 255)      # green legal paper
SPINE = (28, 75, 63, 255)         # deep docket green
INK = (34, 49, 63, 255)           # office ink
CRIMSON = (179, 55, 47, 255)      # the one colour that means "attend to me"
EDGE = (200, 207, 194, 255)       # hairline for definition on white


def draw_mark(size: int = 256) -> list[list[tuple[int, int, int, int]]]:
    """The icon as a pixel grid, drawn with plain rectangles."""
    px = [[PAPER for _ in range(size)] for _ in range(size)]

    def rect(x0: int, y0: int, x1: int, y1: int, colour) -> None:
        for y in range(max(0, y0), min(size, y1)):
            row = px[y]
            for x in range(max(0, x0), min(size, x1)):
                row[x] = colour

    s = size / 256.0  # everything below is designed on a 256 grid

    rect(0, 0, int(48 * s), size, SPINE)                    # docket spine
    rect(int(10 * s), int(30 * s), int(38 * s), int(76 * s), CRIMSON)  # tab

    line_x0, line_x1 = int(76 * s), int(224 * s)
    for i, width in enumerate((1.0, 1.0, 0.72, 1.0, 0.55)):  # ink rules
        y0 = int((78 + i * 30) * s)
        rect(line_x0, y0, int(line_x0 + (line_x1 - line_x0) * width),
             y0 + max(1, int(10 * s)), INK)

    edge = max(1, int(2 * s))                                # hairline border
    rect(0, 0, size, edge, EDGE)
    rect(0, size - edge, size, size, EDGE)
    rect(0, 0, edge, size, EDGE)
    rect(size - edge, 0, size, size, EDGE)
    return px


def downscale(px: list[list[tuple[int, int, int, int]]], target: int):
    """Box-filter downscale (source size must be a multiple of target)."""
    source = len(px)
    factor = source // target
    out = []
    for ty in range(target):
        row = []
        for tx in range(target):
            sums = [0, 0, 0, 0]
            for dy in range(factor):
                for dx in range(factor):
                    p = px[ty * factor + dy][tx * factor + dx]
                    for c in range(4):
                        sums[c] += p[c]
            n = factor * factor
            row.append(tuple(v // n for v in sums))
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Encoders
# ---------------------------------------------------------------------------


def encode_png(px) -> bytes:
    size = len(px)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload)))

    raw = b"".join(
        b"\x00" + b"".join(bytes(pixel) for pixel in row) for row in px
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def encode_ico_bmp(px) -> bytes:
    """One BMP frame for the ICO: 32-bit BGRA bottom-up + empty AND mask."""
    size = len(px)
    header = struct.pack(
        "<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0
    )
    xor_rows = b"".join(
        b"".join(bytes((p[2], p[1], p[0], p[3])) for p in px[y])
        for y in range(size - 1, -1, -1)
    )
    mask_row_bytes = ((size + 31) // 32) * 4
    and_mask = b"\x00" * (mask_row_bytes * size)
    return header + xor_rows + and_mask


def build_ico(frames: dict[int, bytes]) -> bytes:
    """Pack pre-encoded frames (size -> PNG or ICO-BMP bytes) into an .ico."""
    ordered = sorted(frames.items(), reverse=True)
    directory = struct.pack("<HHH", 0, 1, len(ordered))
    entries = b""
    blobs = b""
    offset = 6 + 16 * len(ordered)
    for size, blob in ordered:
        entries += struct.pack(
            "<BBBBHHII",
            size % 256, size % 256, 0, 0, 1, 32, len(blob), offset,
        )
        blobs += blob
        offset += len(blob)
    return directory + entries + blobs


def main() -> None:
    full = draw_mark(256)
    frames: dict[int, bytes] = {256: encode_png(full)}
    for size in (64, 48, 32, 16):
        # Box-filtering needs the source to divide evenly; the mark scales,
        # so draw 48 px from a 192 px original, everything else from 256.
        source = 256 if 256 % size == 0 else size * 4
        frames[size] = encode_ico_bmp(downscale(draw_mark(source), size))

    (HERE / "icon.ico").write_bytes(build_ico(frames))
    (HERE / "icon.png").write_bytes(encode_png(full))
    print(f"Wrote {HERE / 'icon.ico'} and icon.png")


if __name__ == "__main__":
    main()
