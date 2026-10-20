"""Cairo surfaces for the SpacePilot Pro's panel and for pictures.

The panel is 240 x 320 RGB565 pixels with the landscape picture transposed:
the buffer index of the landscape pixel (x, y) is x * 240 + y. Pages paint in
landscape coordinates (320 x 240) on a context made here; the same painting
code draws a picture of any scale for the settings app and the README.
"""

from __future__ import annotations

import cairo

WIDTH, HEIGHT = 320, 240

# One bulk write per frame: this header, then the pixels. Verified on the
# device (see AGENTS.md "SpacePilot Pro screen"); the filler counts up.
HEADER = bytes([0x10, 0x0F, 0x00, 0x58, 0x02, 0x00, 0x00, 0x00,
                0x00, 0x00, 0x00, 0x3F, 0x01, 0xEF, 0x00]) + bytes((0x0F + i) & 0xFF for i in range(512 - 15))
FRAME_SIZE = len(HEADER) + WIDTH * HEIGHT * 2


def device_surface():
    """(surface, context): a portrait RGB565 surface; the context draws in
    landscape 320 x 240 coordinates (x and y swapped)."""
    surface = cairo.ImageSurface(cairo.FORMAT_RGB16_565, HEIGHT, WIDTH)
    cr = cairo.Context(surface)
    cr.set_matrix(cairo.Matrix(0, 1, 1, 0, 0, 0))
    return surface, cr


def frame_bytes(surface) -> bytes:
    """The bulk transfer for a device_surface(): header and pixels."""
    surface.flush()
    if surface.get_stride() != HEIGHT * 2:
        raise ValueError("unexpected stride %d" % surface.get_stride())
    return HEADER + bytes(surface.get_data())


def preview_surface(scale: float = 1):
    """(surface, context): an ARGB32 picture of the landscape screen at
    ``scale``; the context is scaled, so code paints in 320 x 240 as well."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, round(WIDTH * scale), round(HEIGHT * scale))
    cr = cairo.Context(surface)
    cr.scale(scale, scale)
    return surface, cr
