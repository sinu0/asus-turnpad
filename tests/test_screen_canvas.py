"""screen/canvas.py: the panel's transposed RGB565 buffer."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from screen import canvas


class CanvasTests(unittest.TestCase):
    def test_pixel_lands_at_transposed_index(self):
        surface, cr = canvas.device_surface()
        cr.set_source_rgb(0, 0, 0)
        cr.paint()
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(10, 20, 1, 1)
        cr.fill()
        surface.flush()
        data = bytes(surface.get_data())
        self.assertEqual(surface.get_stride(), 480)
        index = (10 * 240 + 20) * 2
        self.assertEqual(data[index:index + 2], b"\xff\xff")
        self.assertEqual(sum(1 for value in data if value), 2)

    def test_frame_has_header_and_pixels(self):
        surface, _cr = canvas.device_surface()
        frame = canvas.frame_bytes(surface)
        self.assertEqual(len(frame), 512 + 320 * 240 * 2)
        self.assertEqual(frame[:15].hex(), "100f005802000000000000" "3f01ef00")
        self.assertEqual(frame[15], 0x0F)
        self.assertEqual(frame[16], 0x10)
        self.assertEqual(frame[15 + 241], 0x0F + 241 & 0xFF)

    def test_preview_surface_scales(self):
        surface, cr = canvas.preview_surface(2)
        self.assertEqual((surface.get_width(), surface.get_height()), (640, 480))
        cr.set_source_rgb(1, 0, 0)
        cr.rectangle(0, 0, 1, 1)
        cr.fill()
        surface.flush()
        stride = surface.get_stride()
        data = bytes(surface.get_data())
        self.assertEqual(data[1 * stride + 1 * 4 + 2], 255)  # red at (1, 1)


if __name__ == "__main__":
    unittest.main()
