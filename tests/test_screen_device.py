"""screen/device.py: the brightness mapping onto the device's raw range (no hardware)."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from screen import device
from spacemouse_devices import SPACEPILOT_PRO, PresentScreen


class FakeHandle:
    def __init__(self):
        self.transfers = []

    def ctrl_transfer(self, *args):
        self.transfers.append(args)


class BrightnessTests(unittest.TestCase):
    def test_percent_maps_onto_the_raw_range(self):
        self.assertEqual(device.RAW_MAX, 200)
        self.assertEqual(device.raw_brightness(0), 0)
        self.assertEqual(device.raw_brightness(100), 200)
        self.assertEqual(device.raw_brightness(50), 100)
        self.assertEqual(device.raw_brightness(70), 140)
        self.assertEqual(device.raw_brightness(1), 2)

    def test_out_of_range_is_clamped(self):
        self.assertEqual(device.raw_brightness(-5), 0)
        self.assertEqual(device.raw_brightness(500), 200)

    def test_monotonic(self):
        raw = [device.raw_brightness(level) for level in range(101)]
        self.assertEqual(raw, sorted(raw))
        self.assertTrue(all(value <= 255 for value in raw))

    def test_set_brightness_sends_the_raw_value(self):
        handle = FakeHandle()
        present = PresentScreen(model=SPACEPILOT_PRO, busnum=1, devnum=2, path="")
        screen = device.ScreenDevice(handle, present, SimpleNamespace(core=SimpleNamespace(USBError=OSError)))
        screen.set_brightness(100)
        screen.set_brightness(0)
        screen.set_brightness(40)
        payloads = [bytes(call[4]) for call in handle.transfers]
        self.assertEqual([payload[0] for payload in payloads], [200, 0, 80])
        self.assertTrue(all(payload[1:] == device.BRIGHTNESS_TAIL for payload in payloads))
        self.assertEqual(handle.transfers[0][:4], (0x41, device.BRIGHTNESS_REQUEST, 0, 0))


if __name__ == "__main__":
    unittest.main()
