"""DialPad emulation on touchpads without a DialPad: geometry, touchpad
detection, configuration and the driver's switching, without hardware."""

import ast
import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import layout_detect  # noqa: E402
from config.schema import DialPadConfig, EmulationConfig, _decoder, _encoder  # noqa: E402
from dial_gesture import EMULATED_CORNER_MM, emulated_dial  # noqa: E402

# Functions of dialpad.py that switch the dial; extracted like test_driver_state.
SWITCH_FUNCTIONS = {
    "send_value_to_touchpad",
    "activate_dialpad",
    "deactivate_dialpad",
    "set_emulated_dial_owns_touchpad",
    "on_dbus_start_calibration",
    "on_dbus_toggle",
    "toggle_top_right_icon",
}


class EmulatedGeometryTest(unittest.TestCase):
    def test_ring_is_centred_and_reaches_every_corner(self):
        dial = emulated_dial(0, 1200, 0, 800, 30)
        self.assertEqual((dial["circle_center_x"], dial["circle_center_y"]), (600, 400))
        radius = dial["circle_diameter"] / 2
        for x, y in ((0, 0), (1200, 0), (0, 800), (1200, 800)):
            self.assertLessEqual(math.hypot(x - 600, y - 400), radius + 1)
        self.assertEqual(dial["center_button_diameter"], 240)
        self.assertEqual(dial["x_scale"], 1.0)

    def test_offset_ranges(self):
        dial = emulated_dial(1472, 5772, 1408, 4806, 30)
        self.assertEqual((dial["circle_center_x"], dial["circle_center_y"]), (3622, 3107))

    def test_centre_size_is_limited(self):
        self.assertEqual(emulated_dial(0, 1000, 0, 1000, 1)["center_button_diameter"], 100)
        self.assertEqual(emulated_dial(0, 1000, 0, 1000, 95)["center_button_diameter"], 600)

    def test_corner_in_millimetres_with_resolution(self):
        dial = emulated_dial(0, 4000, 0, 2400, 30, resolution_x=40, resolution_y=40)
        self.assertEqual(dial["top_right_icon_width"], EMULATED_CORNER_MM * 40)
        self.assertEqual(dial["top_right_icon_height"], EMULATED_CORNER_MM * 40)

    def test_corner_without_resolution_stays_small(self):
        dial = emulated_dial(0, 1000, 0, 600, 30)
        self.assertGreater(dial["top_right_icon_width"], 0)
        self.assertLessEqual(dial["top_right_icon_width"], 1000 / 3)
        self.assertLessEqual(dial["top_right_icon_height"], 600 / 3)

    def test_axes_with_different_resolutions(self):
        # 100 mm x 60 mm with 20 and 40 units per millimetre.
        dial = emulated_dial(0, 2000, 0, 2400, 50, resolution_x=20, resolution_y=40)
        self.assertEqual(dial["x_scale"], 2.0)
        # The shorter side is the 60 mm height, in y units.
        self.assertEqual(dial["center_button_diameter"], 1200)


class TouchpadDetectionTest(unittest.TestCase):
    def test_touchpads_of_other_makes(self):
        for name in (
            'N: Name="SynPS/2 Synaptics TouchPad"',
            'N: Name="ELAN0712:00 04F3:30FD Touchpad"',
            'N: Name="Apple Inc. Magic Trackpad 2"',
        ):
            with self.subTest(name=name):
                self.assertTrue(layout_detect.is_touchpad(name))

    def test_other_devices(self):
        for name in ('N: Name="ELAN0712:00 04F3:30FD Mouse"', 'N: Name="AT Translated Set 2 keyboard"', None):
            with self.subTest(name=name):
                self.assertFalse(layout_detect.is_touchpad(name))


class EmulationConfigTest(unittest.TestCase):
    def test_default_is_off(self):
        self.assertEqual(DialPadConfig().emulation, EmulationConfig(enabled=False, corner=True, center_size=30))

    def test_round_trip(self):
        config = DialPadConfig()
        config.emulation = EmulationConfig(enabled=True, corner=False, center_size=45)
        decoded = _decoder(_encoder(config), DialPadConfig)
        self.assertEqual(decoded.emulation, config.emulation)

    def test_old_files_and_bad_values(self):
        data = _encoder(DialPadConfig())
        del data["emulation"]
        self.assertEqual(_decoder(data, DialPadConfig).emulation, EmulationConfig())
        data["emulation"] = {"enabled": 1, "center_size": 500}
        self.assertEqual(_decoder(data, DialPadConfig).emulation, EmulationConfig(enabled=True, center_size=60))


class FakeTouchpad:
    def __init__(self):
        self.calls = []

    def grab(self):
        self.calls.append("grab")

    def ungrab(self):
        self.calls.append("ungrab")


def load_switching(emulation):
    with open(os.path.join(ROOT, "dialpad.py"), encoding="utf-8") as source:
        tree = ast.parse(source.read())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in SWITCH_FUNCTIONS]
    self_check = {node.name for node in functions}
    assert self_check == SWITCH_FUNCTIONS, SWITCH_FUNCTIONS - self_check
    hardware, queued, send_events = [], [], []
    noop = staticmethod(lambda *a, **k: None)
    namespace = {
        "emulation": emulation,
        "mouse_mode": False,
        "dialpad": False,
        "pending_calibration": None,
        "driver_state_lock": __import__("threading").RLock(),
        "d_t": FakeTouchpad(),
        "CONFIG_ENABLED": "enabled",
        "config_set": lambda *a, **k: None,
        "reset_center": lambda: None,
        "apply_backlight": lambda: None,
        "overlay_enabled": lambda: False,
        "send_to_socket": lambda payload: None,
        "show_initial_function_state": lambda: None,
        "send_value_to_touchpad_via_hidraw": lambda value: hardware.append(value) or True,
        "send_value_to_touchpad_via_i2c": lambda value: hardware.append(value) or True,
        "set_touchpad_prop_send_events": send_events.append,
        "enqueue_action": queued.append,
        "log": type("Log", (), {"info": noop, "debug": noop, "warning": noop})(),
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), "dialpad.py", "exec"), namespace)
    return namespace, hardware, queued, send_events


class DriverSwitchingTest(unittest.TestCase):
    def run_queue(self, queued):
        while queued:
            queued.pop(0)()

    def test_emulation_takes_the_touchpad_without_hardware_commands(self):
        driver, hardware, queued, send_events = load_switching(emulation=True)
        driver["activate_dialpad"]()
        self.run_queue(queued)
        self.assertTrue(driver["dialpad"])
        self.assertEqual(driver["d_t"].calls, ["grab"])
        self.assertEqual(send_events, [0])

        driver["deactivate_dialpad"]()
        self.run_queue(queued)
        self.assertFalse(driver["dialpad"])
        self.assertEqual(driver["d_t"].calls, ["grab", "ungrab"])
        self.assertEqual(send_events, [0, 1])
        self.assertEqual(hardware, [])

    def test_dialpad_hardware_is_switched_as_before(self):
        driver, hardware, queued, send_events = load_switching(emulation=False)
        driver["activate_dialpad"]()
        driver["deactivate_dialpad"]()
        self.run_queue(queued)
        self.assertEqual(hardware, ["0x60", "0x01", "0x61", "0x00"])
        self.assertEqual(driver["d_t"].calls, [])
        self.assertEqual(send_events, [])

    def test_toggle_over_dbus_runs_on_the_action_thread(self):
        driver, _hardware, queued, _send_events = load_switching(emulation=True)
        self.assertTrue(driver["on_dbus_toggle"]())
        self.assertFalse(driver["dialpad"])
        self.run_queue(queued)
        self.assertTrue(driver["dialpad"])
        driver["on_dbus_toggle"]()
        self.run_queue(queued)
        self.assertFalse(driver["dialpad"])

    def test_no_calibration_for_the_emulated_dial(self):
        driver, *_ = load_switching(emulation=True)
        self.assertFalse(driver["on_dbus_start_calibration"]())
        self.assertIsNone(driver["pending_calibration"])
        driver, *_ = load_switching(emulation=False)
        self.assertTrue(driver["on_dbus_start_calibration"]())


if __name__ == "__main__":
    unittest.main()
