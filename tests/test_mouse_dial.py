"""Mouse dial: finding the mouse in /proc/bus/input/devices, the gesture
state machine, the configuration section and the driver's centre decision,
without hardware."""

import ast
import math
import os
import sys
import threading
import types
import unittest

from libevdev import EV_KEY, EV_REL, InputEvent, device

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from config.schema import DialPadConfig, MouseConfig, _decoder, _encoder  # noqa: E402
from dial_gesture import RotationTracker, slice_index, touch_angle  # noqa: E402
from mouse_dial import CLOCKWISE, COUNTERCLOCKWISE, MouseDial, MousePointer, find_mouse, has_bit  # noqa: E402
import test_driver_state  # noqa: E402
from test_driver_state import last_state  # noqa: E402

KEY_3 = 4
KEY_F13 = 183

DEVICES = '''I: Bus=0019 Vendor=0000 Product=0001 Version=0000
N: Name="Power Button"
P: Phys=PNP0C0C/button/input0
H: Handlers=kbd event0 
B: PROP=0
B: EV=3
B: KEY=10000000000000 0

I: Bus=0003 Vendor=046d Product=4085 Version=0111
N: Name="Logitech G604 Keyboard"
P: Phys=usb-0000:0b:00.3-2.4.4/input1:1
H: Handlers=sysrq kbd leds event1 
B: PROP=0
B: EV=120013
B: KEY=1000000000007 ff9f207ac14057ff febeffdfffefffff fffffffffffffffe
B: MSC=10
B: LED=1f

I: Bus=0003 Vendor=046d Product=4085 Version=0111
N: Name="Logitech G604 "
P: Phys=usb-0000:0b:00.3-2.4.4/input2:1
S: Sysfs=/devices/pci0000:00/0000:00:08.1/0000:0b:00.3/usb3/3-2/3-2.4/3-2.4.4/3-2.4.4:1.2/0003:046D:C539.001F/0003:046D:4085.0020/input/input78
U: Uniq=5b-a5-ef-1f
H: Handlers=sysrq kbd leds event2 mouse0 
B: PROP=0
B: EV=12001f
B: KEY=3f00733fff 0 0 483ffff17aff32d bfd4444600000000 ffff0001 130ff38b17d007 ffff7bfad9415fff ffbeffdfffefffff fffffffffffffffe
B: REL=1943
B: ABS=100000000
B: MSC=10
B: LED=1f

I: Bus=0003 Vendor=1234 Product=0001 Version=0111
N: Name="Plain Mouse"
H: Handlers=mouse1 event3 
B: PROP=0
B: EV=17
B: KEY=1f0000 0 0 0 0
B: REL=903
'''

# What the driver's own virtual device looks like: wheel and the key included.
OWN_DEVICE = '''I: Bus=0003 Vendor=0001 Product=0001 Version=0001
N: Name="Mouse DialPad"
H: Handlers=sysrq kbd event9 mouse2 
B: EV=7
B: KEY=ffffffffffffffff ffffffffffffffff ffffffffffffffff ffffffffffffffff
B: REL=1943
'''


class FindMouseTest(unittest.TestCase):
    def test_g604_with_the_side_button_key(self):
        self.assertEqual(find_mouse(DEVICES, "", KEY_3), "event2")
        self.assertEqual(find_mouse(DEVICES, "Logitech G604", KEY_3), "event2")
        self.assertEqual(find_mouse(DEVICES, "logitech", KEY_F13), "event2")

    def test_keyboard_without_a_wheel_is_skipped(self):
        # The G604's keyboard interface has the keys but no REL_WHEEL.
        self.assertEqual(find_mouse(DEVICES, "G604 Keyboard", KEY_F13), None)

    def test_key_the_mouse_does_not_have(self):
        self.assertEqual(find_mouse(DEVICES, "Plain Mouse", KEY_F13), None)
        self.assertEqual(find_mouse(DEVICES, "", 9999), None)

    def test_name_is_a_substring_in_any_case(self):
        self.assertEqual(find_mouse(DEVICES, "G604", KEY_3), "event2")
        self.assertEqual(find_mouse(DEVICES, "no such mouse", KEY_3), None)

    def test_any_mouse_with_wheel_and_key(self):
        # BTN_LEFT: both mice have it, the first one wins.
        self.assertEqual(find_mouse(DEVICES, "", 0x110), "event2")  # BTN_LEFT, first match
        self.assertEqual(find_mouse(DEVICES, "Plain", 0x110), "event3")

    def test_own_virtual_device_never_matches(self):
        self.assertEqual(find_mouse(OWN_DEVICE, "", KEY_F13), None)
        self.assertEqual(find_mouse(OWN_DEVICE + "\n" + DEVICES, "", KEY_F13), "event2")

    def test_no_devices(self):
        self.assertEqual(find_mouse("", "", KEY_F13), None)

    def test_bit_masks(self):
        # Most significant word first; 32-bit words work as well.
        self.assertTrue(has_bit("fffffffffffffffe", 4))
        self.assertFalse(has_bit("fffffffffffffffe", 0))
        self.assertTrue(has_bit("1 0", 64))
        self.assertFalse(has_bit("1 0", 0))
        self.assertTrue(has_bit("1 0", 32, word_bits=32))
        self.assertFalse(has_bit("1943", 7))
        self.assertTrue(has_bit("1943", 8))
        self.assertFalse(has_bit("zz", 1))
        self.assertFalse(has_bit("", 1))


class MouseDialTest(unittest.TestCase):
    def kinds(self, actions):
        return [action.kind for action in actions]

    def test_toggle_switches_and_ignores_repeats(self):
        mouse = MouseDial()
        self.assertFalse(mouse.active)
        self.assertEqual(self.kinds(mouse.toggle_key(1)), ["toggle"])
        self.assertTrue(mouse.active)
        self.assertTrue(mouse.toggle_down)
        self.assertEqual(mouse.toggle_key(2), [])
        self.assertTrue(mouse.active)
        self.assertEqual(mouse.toggle_key(0), [])
        self.assertFalse(mouse.toggle_down)
        self.assertEqual(self.kinds(mouse.toggle_key(1)), ["toggle"])
        self.assertFalse(mouse.active)

    def test_wheel_and_middle_do_nothing_while_off(self):
        mouse = MouseDial()
        self.assertEqual(mouse.wheel(-1), [])
        self.assertEqual(mouse.middle(1, 10.0), [])
        self.assertEqual(mouse.middle(0, 10.1), [])
        self.assertEqual(mouse.tick(20.0), [])

    def test_wheel_direction_one_step_per_notch(self):
        mouse = MouseDial()
        mouse.set_active(True)
        down = mouse.wheel(-1)
        self.assertEqual([(a.kind, a.direction) for a in down], [("rotate", CLOCKWISE)])
        up = mouse.wheel(1)
        self.assertEqual([(a.kind, a.direction) for a in up], [("rotate", COUNTERCLOCKWISE)])
        self.assertEqual([a.direction for a in mouse.wheel(-3)], [CLOCKWISE] * 3)
        self.assertEqual(mouse.wheel(0), [])

    def test_tap(self):
        mouse = MouseDial(hold_time=0.5)
        mouse.set_active(True)
        self.assertEqual(self.kinds(mouse.middle(1, 100.0)), ["center_press"])
        self.assertEqual(mouse.middle(2, 100.1), [])
        self.assertEqual(mouse.tick(100.2), [])
        (release,) = mouse.middle(0, 100.3)
        self.assertEqual(release.kind, "center_release")
        self.assertAlmostEqual(release.duration, 0.3)
        self.assertFalse(release.hold_fired)

    def test_hold_fires_once_then_release_says_so(self):
        mouse = MouseDial(hold_time=0.5)
        mouse.set_active(True)
        mouse.middle(1, 100.0)
        self.assertEqual(mouse.tick(100.49), [])
        self.assertEqual(self.kinds(mouse.tick(100.5)), ["center_hold"])
        self.assertEqual(mouse.tick(100.9), [])
        (release,) = mouse.middle(0, 101.0)
        self.assertTrue(release.hold_fired)
        self.assertAlmostEqual(release.duration, 1.0)
        # The next press starts clean.
        mouse.middle(1, 102.0)
        (release,) = mouse.middle(0, 102.1)
        self.assertFalse(release.hold_fired)

    def test_release_without_press_and_dial_switched_off_while_held(self):
        mouse = MouseDial()
        mouse.set_active(True)
        self.assertEqual(mouse.middle(0, 5.0), [])
        mouse.middle(1, 6.0)
        mouse.set_active(False)
        self.assertEqual(mouse.middle(0, 6.1), [])
        self.assertEqual(mouse.tick(10.0), [])
        mouse.set_active(True)
        self.assertEqual(mouse.tick(20.0), [])


class MouseButtonsTest(unittest.TestCase):
    def setUp(self):
        self.mouse = MouseDial(hold_time=0.5)
        self.mouse.set_active(True)

    def test_left_click_is_a_tap_on_the_centre_whatever_its_length(self):
        (press,) = self.mouse.left(1, 10.0)
        self.assertEqual(press.kind, "center_press")
        self.assertEqual(self.mouse.tick(12.0), [])  # no hold on the left button
        (release,) = self.mouse.left(0, 12.0)
        self.assertEqual((release.kind, release.duration, release.hold_fired), ("center_release", 0.0, False))
        self.assertFalse(self.mouse.center_held)

    def test_one_centre_button_at_a_time(self):
        self.mouse.middle(1, 10.0)
        self.assertEqual(self.mouse.left(1, 10.1), [])
        self.assertEqual(self.mouse.left(0, 10.2), [])
        self.assertTrue(self.mouse.center_held)
        self.assertEqual([a.kind for a in self.mouse.tick(10.6)], ["center_hold"])
        (release,) = self.mouse.middle(0, 11.0)
        self.assertTrue(release.hold_fired)

    def test_right_button_goes_back_on_the_press_only(self):
        self.assertEqual([a.kind for a in self.mouse.right(1)], ["back"])
        self.assertEqual(self.mouse.right(2), [])
        self.assertEqual(self.mouse.right(0), [])

    def test_buttons_do_nothing_while_the_dial_is_off(self):
        self.mouse.set_active(False)
        self.assertEqual(self.mouse.left(1, 1.0), [])
        self.assertEqual(self.mouse.left(0, 1.1), [])
        self.assertEqual(self.mouse.right(1), [])


def hand_circle_moves(radius, laps=1, clockwise=True, steps=72):
    """Moves (dx, dy) of a hand drawing circles of ``radius`` counts, as
    mouse motion with y down: clockwise on the desk is clockwise on the screen."""
    sign = -1 if clockwise else 1
    for _ in range(laps):
        for step in range(steps):
            start, end = 2 * math.pi * step / steps, 2 * math.pi * (step + 1) / steps
            yield sign * radius * (math.sin(end) - math.sin(start)), radius * (math.cos(end) - math.cos(start))


class MousePointerTest(unittest.TestCase):
    def circle(self, pointer, radius, laps=1, clockwise=True):
        """Angles after every move of the circle that already have one."""
        angles = []
        for dx, dy in hand_circle_moves(radius, laps, clockwise):
            pointer.move(dx, dy)
            if not pointer.in_dead_zone:
                angles.append(pointer.angle())
        return angles

    def turned(self, angles):
        return sum((b - a + 180) % 360 - 180 for a, b in zip(angles, angles[1:]))

    def test_no_angle_before_the_dead_zone_has_been_travelled(self):
        pointer = MousePointer(radius=300)  # 105 counts of travel
        self.assertTrue(pointer.in_dead_zone)
        pointer.move(45, 0)
        pointer.move(-45, 0)  # travel counts, not displacement
        pointer.move(0, 5)
        self.assertTrue(pointer.in_dead_zone)
        pointer.move(0, 10)
        self.assertFalse(pointer.in_dead_zone)

    def test_straight_strokes_give_the_touch_angle(self):
        for dx, dy, angle in ((0, -1, 0), (1, 0, 90), (0, 1, 180), (-1, 0, 270), (1, 1, 135)):
            with self.subTest(angle=angle):
                pointer = MousePointer(radius=300)
                for _ in range(20):
                    pointer.move(10 * dx, 10 * dy)
                self.assertAlmostEqual(pointer.angle(), angle)
                self.assertAlmostEqual(touch_angle(dx, dy), angle)
                self.assertFalse(pointer.jumped)

    def test_a_single_big_stroke_has_an_angle_at_once(self):
        pointer = MousePointer(radius=300)
        pointer.move(500, 0)
        self.assertFalse(pointer.in_dead_zone)
        self.assertAlmostEqual(pointer.angle(), 90)

    def test_clockwise_circles_turn_clockwise_once_per_lap_whatever_their_size(self):
        for radius in (75, 150, 300, 600):  # R/4 up to 2R
            with self.subTest(radius=radius):
                pointer = MousePointer(radius=300)
                self.circle(pointer, radius)  # settles
                lap = self.circle(pointer, radius, laps=2)
                self.assertAlmostEqual(self.turned(lap) / 2, 360, delta=15)
                self.assertTrue(all(((b - a + 180) % 360 - 180) > 0 for a, b in zip(lap, lap[1:])))
                self.assertFalse(pointer.in_dead_zone)

    def test_counterclockwise_circles_turn_back(self):
        pointer = MousePointer(radius=300)
        self.circle(pointer, 75, clockwise=False)
        lap = self.circle(pointer, 75, laps=2, clockwise=False)
        self.assertAlmostEqual(self.turned(lap) / 2, -360, delta=15)

    def test_there_is_always_an_angle_once_there_is_one(self):
        pointer = MousePointer(radius=300)
        pointer.move(0, -200)
        for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, 0)):
            pointer.move(dx, dy)
            self.assertFalse(pointer.in_dead_zone)
        for _ in range(100):
            pointer.move(0, 0)
        self.assertFalse(pointer.in_dead_zone)
        pointer.reset()
        self.assertTrue(pointer.in_dead_zone)

    def test_a_wiggle_keeps_the_angle(self):
        pointer = MousePointer(radius=300)
        for _ in range(20):
            pointer.move(0, -10)  # up
        self.assertAlmostEqual(pointer.angle(), 0)
        for _ in range(40):  # back and forth: the net displacement over the window is tiny
            pointer.move(8, 0)
            pointer.move(-8, 0)
            self.assertAlmostEqual(pointer.angle(), 0, places=0)
            self.assertFalse(pointer.jumped)

    def test_reversing_a_stroke_reports_a_jump(self):
        pointer = MousePointer(radius=300)
        for _ in range(20):
            pointer.move(10, 0)
        self.assertAlmostEqual(pointer.angle(), 90)
        jumps = []
        for _ in range(40):
            pointer.move(-10, 0)
            if pointer.jumped:
                jumps.append(pointer.angle())
        self.assertEqual(len(jumps), 1)
        self.assertAlmostEqual(jumps[0], 270)
        self.assertAlmostEqual(pointer.angle(), 270)

    def test_circling_never_jumps(self):
        pointer = MousePointer(radius=300)
        for dx, dy in hand_circle_moves(75, laps=3):
            pointer.move(dx, dy)
            self.assertFalse(pointer.jumped)

    def test_reset_starts_over(self):
        pointer = MousePointer(radius=300)
        pointer.move(0, -200)
        pointer.reset()
        self.assertTrue(pointer.in_dead_zone)
        self.assertFalse(pointer.jumped)
        pointer.move(0, 100)
        self.assertTrue(pointer.in_dead_zone)
        pointer.move(0, 10)
        self.assertAlmostEqual(pointer.angle(), 180)

    def test_the_trail_stays_short(self):
        pointer = MousePointer(radius=300)
        for _ in range(10000):
            pointer.move(3, 4)
        self.assertLess(len(pointer._trail), 50)


class MouseConfigTest(unittest.TestCase):
    def test_default_is_off(self):
        self.assertEqual(DialPadConfig().mouse, MouseConfig(enabled=False, device="", toggle_key="KEY_F13"))

    def test_round_trip(self):
        config = DialPadConfig()
        config.mouse = MouseConfig(enabled=True, device="Logitech G604", toggle_key="KEY_3")
        decoded = _decoder(_encoder(config), DialPadConfig)
        self.assertEqual(decoded.mouse, config.mouse)

    def test_motion_settings(self):
        self.assertEqual((MouseConfig().motion, MouseConfig().motion_radius), (False, 300))
        config = DialPadConfig()
        config.mouse = MouseConfig(enabled=True, motion=True, motion_radius=450)
        self.assertEqual(_decoder(_encoder(config), DialPadConfig).mouse, config.mouse)
        data = _encoder(DialPadConfig())
        data["mouse"] = {"motion": 1, "motion_radius": 99999}
        self.assertEqual(_decoder(data, DialPadConfig).mouse.motion_radius, 2000)
        data["mouse"] = {"motion_radius": "junk"}
        self.assertEqual(_decoder(data, DialPadConfig).mouse.motion_radius, 300)
        data["mouse"] = {"motion_radius": 1}
        self.assertEqual(_decoder(data, DialPadConfig).mouse.motion_radius, 50)

    def test_old_files_without_the_section(self):
        data = _encoder(DialPadConfig())
        del data["mouse"]
        self.assertEqual(_decoder(data, DialPadConfig).mouse, MouseConfig())

    def test_key_names_are_normalised_and_bad_values_fall_back(self):
        data = _encoder(DialPadConfig())
        data["mouse"] = {"enabled": 1, "toggle_key": "f13", "device": "  G604 "}
        self.assertEqual(_decoder(data, DialPadConfig).mouse, MouseConfig(True, "G604", "KEY_F13"))
        data["mouse"] = {"toggle_key": "3"}
        self.assertEqual(_decoder(data, DialPadConfig).mouse.toggle_key, "KEY_3")
        data["mouse"] = {"toggle_key": "", "device": None}
        self.assertEqual(_decoder(data, DialPadConfig).mouse, MouseConfig())
        data["mouse"] = {"toggle_key": 5}
        self.assertEqual(_decoder(data, DialPadConfig).mouse.toggle_key, "KEY_F13")


class MouseDriverTest(unittest.TestCase):
    """What the mouse loop does with the actions, through the driver's state
    functions (the same ones the touch loop uses)."""

    setUp = test_driver_state.DriverStateTest.setUp
    focus = test_driver_state.DriverStateTest.focus

    def prepare(self, app_id):
        self.rotations, self.center_runs = [], []
        self.driver.update(
            center_hold_time=0.5,
            shortcut_has_scroll_action=lambda _shortcuts, direction: direction == "clockwise",
            run_rotation_action=lambda *args: self.rotations.append(args),
            run_function_center_action=lambda *args: self.center_runs.append(("function", args[2])),
            run_center_action=lambda *args: self.center_runs.append(("single", args[3])),
        )
        self.focus(app_id)
        self.sent.clear()

    def run_queue(self):
        while self.queued:
            self.queued.pop(0)()

    def test_wheel_steps_round_the_menu_and_wraps(self):
        self.prepare("org.kde.krita.desktop")  # Zoom, Volume and two empty sectors
        rotate = self.driver["rotate_dial"]
        self.assertIsNone(self.driver["title"])
        rotate("clockwise")
        self.assertEqual(self.driver["title"], "Zoom")
        self.assertEqual(last_state(self.sent)["titles"][:2], ["Zoom", "Volume"])
        self.assertEqual(last_state(self.sent)["title"], "Zoom")
        rotate("clockwise")
        self.assertEqual(self.driver["title"], "Volume")
        rotate("clockwise")
        self.assertEqual(self.driver["title"], "Zoom")
        rotate("counterclockwise")
        self.assertEqual(self.driver["title"], "Volume")
        self.assertFalse(self.driver["center_activated"])

    def test_first_step_back_starts_at_the_last_entry(self):
        self.prepare("org.kde.krita.desktop")
        self.driver["rotate_dial"]("counterclockwise")
        self.assertEqual(self.driver["title"], "Volume")

    def test_wheel_moves_the_highlight_of_an_open_list(self):
        self.prepare("org.gnome.Nautilus.desktop")
        self.driver["title"] = "Display"
        self.driver["select_multifunction_segment"]()
        self.assertEqual(self.driver["title"], "Brightness")
        self.driver["rotate_dial"]("clockwise")
        self.assertEqual(self.driver["title"], "Mute")
        self.driver["rotate_dial"]("clockwise")
        self.driver["rotate_dial"]("clockwise")
        self.assertEqual(self.driver["title"], "Volume")  # stops at the end
        self.driver["rotate_dial"]("counterclockwise")
        self.assertEqual(self.driver["title"], "Mute")

    def test_wheel_turns_the_selected_function(self):
        self.prepare("org.kde.krita.desktop")
        self.driver["enter_function"]("Zoom")
        self.queued.clear()
        self.driver["rotate_dial"]("clockwise")
        self.driver["rotate_dial"]("counterclockwise")
        self.run_queue()
        self.assertEqual([(args[1], args[4], args[5]) for args in self.rotations],
                         [("clockwise", "Zoom", True), ("counterclockwise", "Zoom", True)])
        # Scroll steps are multiplied by the profile's speed, the others are not.
        self.assertEqual([args[3] for args in self.rotations], [1.0, 1])

    def test_wheel_in_single_function_mode(self):
        self.prepare("mpv.desktop")
        self.assertEqual(self.driver["title"], "Volume")
        self.queued.clear()
        self.driver["rotate_dial"]("clockwise")
        self.run_queue()
        self.assertEqual([(args[1], args[4]) for args in self.rotations], [("clockwise", "Volume")])

    def test_sector_highlight_is_a_finger_slice(self):
        self.prepare("org.kde.krita.desktop")
        self.driver["highlight_menu_slice"](1)
        self.assertEqual(self.driver["title"], "Volume")
        self.assertEqual(last_state(self.sent)["title"], "Volume")
        self.driver["highlight_menu_slice"](3)  # an empty sector
        self.assertIsNone(self.driver["title"])
        self.driver["highlight_menu_slice"](9)
        self.assertIsNone(self.driver["title"])

    def test_angle_in_a_function_crosses_the_threshold_into_an_action(self):
        from dial_gesture import RotationTracker, scroll_acceleration_multiplier

        self.prepare("org.kde.krita.desktop")
        clock = [100.0]
        self.driver.update(
            time=lambda: clock[0], default_treshold=90, socket_send_progress_above_treshold=120,
            SCROLL_TRIGGER_DEGREES=15, scroll_acceleration_multiplier=scroll_acceleration_multiplier,
        )
        self.driver["enter_function"]("Zoom")
        self.queued.clear()
        rotation = RotationTracker(3, 30)
        self.driver["turn_selected_function"](rotation, 0.0)
        clock[0] += 1.0
        self.driver["turn_selected_function"](rotation, 40.0)
        self.run_queue()
        self.assertEqual([(args[1], args[4]) for args in self.rotations], [("clockwise", "Zoom")])

    def test_middle_button_tap_selects_in_the_menu(self):
        self.prepare("org.kde.krita.desktop")
        self.driver["title"] = "Zoom"
        self.driver["release_center"](0.1, False)
        self.assertTrue(self.driver["center_activated"])
        self.assertEqual(self.driver["title"], "Zoom")

    def test_middle_button_tap_in_a_function_runs_its_centre_action(self):
        self.prepare("org.kde.krita.desktop")
        self.driver["enter_function"]("Zoom")
        self.queued.clear()
        self.driver["release_center"](0.1, False)
        self.run_queue()
        self.assertEqual(self.center_runs, [("function", "Zoom")])
        self.assertTrue(self.driver["center_activated"])

    def test_long_middle_press_or_hold_goes_back(self):
        self.prepare("org.kde.krita.desktop")
        self.driver["enter_function"]("Zoom")
        self.driver["release_center"](0.9, False)
        self.assertFalse(self.driver["center_activated"])
        self.assertEqual(last_state(self.sent)["title"], "Menu")
        # The hold already went back: the release does nothing more.
        self.driver["enter_function"]("Zoom")
        self.driver["release_center"](0.9, True)
        self.assertTrue(self.driver["center_activated"])

    def test_single_function_centre(self):
        self.prepare("mpv.desktop")
        self.queued.clear()
        self.driver["release_center"](0.1, False)
        self.run_queue()
        self.assertEqual(self.center_runs, [("single", "Volume")])

    def test_menu_shows_again_after_the_gesture(self):
        self.prepare("org.kde.krita.desktop")
        self.driver["title"] = "Zoom"
        self.driver["show_menu_after_gesture"]()
        self.assertEqual(last_state(self.sent)["title"], "Menu")
        self.sent.clear()
        self.driver["enter_function"]("Zoom")
        self.sent.clear()
        self.driver["show_menu_after_gesture"]()
        self.assertEqual(self.sent, [])


MOUSE_FUNCTIONS = {
    "mouse_toggle_key", "set_mouse_grab", "close_mouse", "listen_input_sources", "go_back",
}
MOUSE_CLASSES = {"CenterGesture", "InputSource", "MouseSource"}


def rel(code, value):
    return InputEvent(code, value)


class FakeMouse:
    """Stands for the libevdev Device of the mouse."""

    name = "Fake Mouse"

    def __init__(self, calls):
        self.calls = calls
        self.pending = []
        self.gone = False

    def grab(self):
        self.calls.append("grab")

    def ungrab(self):
        self.calls.append("ungrab")

    def events(self):
        if self.gone:
            raise OSError(19, "No such device")
        pending, self.pending = self.pending, []
        yield from pending

    def sync(self):
        return iter(())


class FakeFile:
    def close(self):
        pass


def key(code, value):
    return InputEvent(code, value)


class MouseLoopTest(unittest.TestCase):
    """listen_input_sources() with a scripted select(): each step feeds events,
    the loop runs one pass, and the step's checks look at the result."""

    def setUp(self):
        with open(os.path.join(ROOT, "dialpad.py"), encoding="utf-8") as source:
            tree = ast.parse(source.read())
        functions = [
            node for node in tree.body
            if (isinstance(node, ast.FunctionDef) and node.name in MOUSE_FUNCTIONS)
            or (isinstance(node, ast.ClassDef) and node.name in MOUSE_CLASSES)
        ]
        self.assertEqual({node.name for node in functions}, MOUSE_FUNCTIONS | MOUSE_CLASSES)
        self.calls, self.sent, self.steps, self.failures = [], [], [], []
        self.clock = 1000.0
        self.mouse = FakeMouse(self.calls)
        noop = staticmethod(lambda *a, **k: None)
        namespace = {
            "EV_KEY": EV_KEY, "EV_REL": EV_REL, "device": device,
            "MouseDial": MouseDial, "time": lambda: self.clock, "sleep": self.sleep,
            "select": types.SimpleNamespace(select=self.select),
            "log": type("Log", (), {"info": noop, "debug": noop, "warning": noop, "error": noop, "exception": noop})(),
            "gui_config": types.SimpleNamespace(mouse=MouseConfig()),
            "MousePointer": MousePointer, "RotationTracker": RotationTracker, "slice_index": slice_index,
            "SUBMENU_STEP_DEGREES": 30, "slices_minimum_count": 4, "angle_hysteresis": 3,
            "multi_app_mode_titles": ["Zoom", "Volume", None, None],
            "profile_float": lambda _key: 1.0,
            "show_pointer_enabled": lambda: True,
            "send_details": lambda **details: self.sent.append({"details": details}),
            "highlight_menu_slice": lambda index: self.calls.append(("slice", index)),
            "move_submenu_highlight": lambda direction: self.calls.append(("list", direction)),
            "turn_selected_function": self.turn_selected_function,
            "mouse_lock": threading.Lock(),
            "mouse_device": self.mouse, "mouse_file": FakeFile(), "mouse_grabbed": False,
            "MOUSE_RETRY_SECONDS": 2, "MOUSE_POLL_SECONDS": 0.05,
            "stop_threads": False, "last_event_time": 0,
            "dialpad": False, "title": None, "center_activated": False, "treshold": None,
            "multi_app_mode": True, "submenu": None, "center_hold_time": 0.5,
            "overlay_enabled": lambda: True,
            "send_to_socket": self.sent.append,
            "process_pending_driver_updates": lambda gesture_idle=True: None,
            "toggle_top_right_icon": self.toggle,
            "rotate_dial": lambda direction: self.calls.append(("rotate", direction)),
            "release_center": lambda duration, hold_fired: self.calls.append(("release", hold_fired)),
            "leave_function": lambda: self.calls.append("leave"),
            "show_menu_after_gesture": lambda: self.calls.append("menu"),
            "open_mouse": lambda _code: False,
            "mouse_mode": True, "spacemouse_mode": False, "input_sources": [],
        }
        exec(compile(ast.Module(body=functions, type_ignores=[]), "dialpad.py", "exec"), namespace)
        self.driver = namespace

    def turn_selected_function(self, rotation, angle):
        """Like the driver's: the tracker counts the turn; records whether it started afresh."""
        fresh = rotation.last_angle is None
        triggered = rotation.update(angle, self.clock, 30)
        self.calls.append(("turn", round(angle), fresh, triggered[0] if triggered else None))

    def toggle(self, current):
        self.calls.append("toggle")
        self.driver["dialpad"] = not current

    def sleep(self, seconds):
        self.clock += seconds

    def select(self, _read, _write, _error, timeout):
        self.clock += timeout
        if not self.steps:
            self.driver["stop_threads"] = True
            return [], [], []
        step = self.steps.pop(0)
        try:
            step()
        except AssertionError as error:
            # The loop would log and swallow it: raise it from run_steps().
            self.failures.append(error)
        return ([self.driver["mouse_file"]] if self.mouse.pending else []), [], []

    def run_steps(self, *steps):
        self.steps = list(steps)
        self.driver["listen_input_sources"]()
        if self.failures:
            raise self.failures[0]

    def feed(self, *events):
        return lambda: self.mouse.pending.extend(events)

    def test_toggle_key_switches_and_grabs_after_its_release(self):
        self.run_steps(
            self.feed(key(EV_KEY.KEY_F13, 1)),
            # Held: switched on, but not grabbed yet (the desktop saw the press).
            lambda: self.assertEqual(self.calls, ["toggle"]),
            self.feed(key(EV_KEY.KEY_F13, 2)),
            self.feed(key(EV_KEY.KEY_F13, 0)),
            lambda: self.assertEqual(self.calls, ["toggle", "grab"]),
            self.feed(EV_REL_WHEEL(-1), key(EV_KEY.BTN_MIDDLE, 1)),
            self.feed(key(EV_KEY.BTN_MIDDLE, 0)),
        )
        self.assertEqual(self.calls[2:], [("rotate", "clockwise")])  # no title: the middle button does nothing
        self.assertIn({"touching": True}, self.sent)

    def test_events_are_ignored_while_the_dial_is_off(self):
        self.run_steps(self.feed(EV_REL_WHEEL(1), key(EV_KEY.BTN_MIDDLE, 1), key(EV_KEY.BTN_MIDDLE, 0)))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.sent, [])

    def test_middle_button_is_the_centre(self):
        self.driver["dialpad"] = True
        self.run_steps(
            lambda: self.driver.update(title="Zoom"),
            self.feed(key(EV_KEY.BTN_MIDDLE, 1)),
            lambda: self.assertEqual(self.sent[-1], {"input": "center", "value": 1, "title": "Zoom"}),
            self.feed(key(EV_KEY.BTN_MIDDLE, 0)),
        )
        self.assertEqual(self.calls, ["grab", ("release", False), "menu"])
        self.assertEqual(self.sent[-1], {"input": "center", "value": 0, "title": "Zoom"})

    def test_holding_the_middle_button_goes_back_once(self):
        self.driver["dialpad"] = True
        self.driver["center_hold_time"] = 0.0
        self.run_steps(
            lambda: self.driver.update(title="Zoom", center_activated=True),
            self.feed(key(EV_KEY.BTN_MIDDLE, 1)),
            lambda: None,
            lambda: None,
            self.feed(key(EV_KEY.BTN_MIDDLE, 0)),
        )
        self.assertEqual(self.calls.count("leave"), 1)
        self.assertIn(("release", True), self.calls)

    def test_dial_switched_over_dbus_grabs_and_releases_the_mouse(self):
        self.run_steps(
            lambda: self.driver.update(dialpad=True),
            lambda: self.assertEqual(self.calls, ["grab"]),
            lambda: self.driver.update(dialpad=False),
        )
        self.assertEqual(self.calls, ["grab", "ungrab"])
        self.assertEqual([p for p in self.sent if "touching" in p], [{"touching": True}, {"touching": False}])

    def test_toggle_off_with_the_key_releases_after_the_key(self):
        self.driver["dialpad"] = True
        self.driver["mouse_grabbed"] = True
        self.run_steps(
            self.feed(key(EV_KEY.KEY_F13, 1)),
            lambda: self.assertEqual(self.calls, ["toggle"]),
            self.feed(key(EV_KEY.KEY_F13, 0)),
        )
        self.assertEqual(self.calls, ["toggle", "ungrab"])

    def test_lost_mouse_is_closed_released_and_found_again(self):
        again = FakeMouse(self.calls)

        def open_again(_code):
            self.driver.update(mouse_device=again, mouse_file=FakeFile())
            return True

        self.driver["open_mouse"] = open_again
        self.driver["dialpad"] = True
        self.driver["mouse_grabbed"] = True

        def unplug():
            self.mouse.gone = True
            self.mouse.pending = [key(EV_KEY.BTN_MIDDLE, 0)]

        self.run_steps(
            unplug,
            lambda: None,
        )
        self.assertEqual(self.calls[0], "ungrab")
        # Searching again every two seconds finds it.
        self.assertIs(self.driver["mouse_device"], again)
        self.assertGreaterEqual(self.clock, 1000.0 + 2)

    def test_left_click_selects_and_right_click_goes_back(self):
        self.driver["dialpad"] = True
        self.run_steps(
            lambda: self.driver.update(title="Zoom", center_activated=True, submenu=None),
            self.feed(key(EV_KEY.BTN_LEFT, 1)),
            lambda: self.assertEqual(self.sent[-1], {"input": "center", "value": 1, "title": "Zoom"}),
            self.feed(key(EV_KEY.BTN_LEFT, 0)),
            self.feed(key(EV_KEY.BTN_RIGHT, 1), key(EV_KEY.BTN_RIGHT, 0)),
        )
        self.assertEqual(self.calls, ["grab", ("release", False), "menu", "leave"])

    def test_right_click_in_the_plain_menu_does_nothing(self):
        self.driver["dialpad"] = True
        self.run_steps(self.feed(key(EV_KEY.BTN_RIGHT, 1)))
        self.assertNotIn("leave", self.calls)

    def test_right_click_in_a_list_goes_back(self):
        self.driver["dialpad"] = True
        self.run_steps(
            lambda: self.driver.update(submenu="Display"),
            self.feed(key(EV_KEY.BTN_RIGHT, 1)),
        )
        self.assertIn("leave", self.calls)

    def motion(self, dx, dy):
        return self.feed(rel(EV_REL.REL_X, dx), rel(EV_REL.REL_Y, dy))

    def test_motion_is_ignored_unless_switched_on(self):
        self.driver["dialpad"] = True
        self.run_steps(self.motion(0, -250))
        self.assertEqual([c for c in self.calls if c != "grab"], [])

    def circle_steps(self, radius, laps, clockwise=True):
        return [self.motion(dx, dy) for dx, dy in hand_circle_moves(radius, laps, clockwise, steps=36)]

    def turns(self):
        return [c for c in self.calls if isinstance(c, tuple) and c[0] == "turn"]

    def test_motion_highlights_the_sector_the_hand_moves_towards(self):
        self.driver["gui_config"].mouse.motion = True
        self.driver["dialpad"] = True
        self.run_steps(
            self.motion(10, -10),   # too little travel: nothing
            self.motion(0, -150),   # straight up: sector 0
            self.motion(5, 0),      # same sector: no repeat
            self.motion(300, 0),    # to the right: sector 1 of 4 (angle 90..180)
        )
        self.assertEqual([c for c in self.calls if c != "grab"], [("slice", 0), ("slice", 1)])
        pointers = [p["details"]["pointer"] for p in self.sent if "pointer" in p.get("details", {})]
        self.assertTrue(pointers and pointers[0] >= 0)

    def test_nothing_happens_until_the_hand_has_travelled(self):
        self.driver["gui_config"].mouse.motion = True
        self.driver["gui_config"].mouse.motion_radius = 100  # 35 counts of travel
        self.driver["dialpad"] = True
        self.run_steps(self.motion(10, 10), self.motion(-5, 5))
        self.assertEqual([c for c in self.calls if c != "grab"], [])
        self.assertEqual([p for p in self.sent if "details" in p], [])

    def test_circling_turns_a_list_clockwise_and_back(self):
        self.driver["gui_config"].mouse.motion = True
        self.driver["dialpad"] = True
        self.run_steps(lambda: self.driver.update(submenu="Display"), *self.circle_steps(100, 2))
        moves = [c[1] for c in self.calls if isinstance(c, tuple) and c[0] == "list"]
        self.assertGreaterEqual(len(moves), 10)
        self.assertEqual(set(moves), {"clockwise"})

    def test_circling_turns_the_selected_function_once_per_lap(self):
        self.driver["gui_config"].mouse.motion = True
        self.driver["dialpad"] = True
        self.run_steps(
            lambda: self.driver.update(title="Zoom", center_activated=True, submenu=None),
            *self.circle_steps(100, 3),
        )
        turns = self.turns()
        self.assertEqual({t[3] for t in turns if t[3]}, {"clockwise"})
        # Thirty degrees per action: about 3 laps of 360 degrees (the first
        # quarter of a lap goes to finding a heading).
        self.assertAlmostEqual(len([t for t in turns if t[3]]), 3 * 360 / 30, delta=8)
        self.assertEqual([t[2] for t in turns].count(True), 1)  # only the first call starts afresh

    def test_circling_the_other_way_turns_back(self):
        self.driver["gui_config"].mouse.motion = True
        self.driver["dialpad"] = True
        self.run_steps(
            lambda: self.driver.update(title="Zoom", center_activated=True, submenu=None),
            *self.circle_steps(100, 2, clockwise=False),
        )
        self.assertEqual({t[3] for t in self.turns() if t[3]}, {"counterclockwise"})

    def test_reversing_a_stroke_starts_the_tracker_again_and_fires_nothing(self):
        self.driver["gui_config"].mouse.motion = True
        self.driver["dialpad"] = True
        self.run_steps(
            lambda: self.driver.update(title="Zoom", center_activated=True, submenu=None),
            *[self.motion(20, 0) for _ in range(10)],
            *[self.motion(-20, 0) for _ in range(15)],
        )
        turns = self.turns()
        self.assertEqual([t[3] for t in turns if t[3]], [])
        self.assertEqual([t[2] for t in turns].count(True), 2)  # the first call and the reversal
        self.assertEqual(turns[-1][1], 270)

    def test_the_heading_starts_again_after_entering_a_function(self):
        self.driver["gui_config"].mouse.motion = True
        self.driver["dialpad"] = True
        self.run_steps(
            self.motion(0, -250),
            lambda: self.driver.update(title="Zoom", center_activated=True),
            self.motion(0, 20),  # too little travel since: no angle
        )
        self.assertEqual(self.turns(), [])

    def test_unknown_toggle_key_falls_back_to_f13(self):
        self.driver["gui_config"].mouse.toggle_key = "KEY_NO_SUCH_KEY"
        self.assertEqual(self.driver["mouse_toggle_key"](), EV_KEY.KEY_F13)
        self.driver["gui_config"].mouse.toggle_key = "KEY_3"
        self.assertEqual(self.driver["mouse_toggle_key"](), EV_KEY.KEY_3)
        self.driver["gui_config"].mouse.toggle_key = "BTN_LEFT"
        self.assertEqual(self.driver["mouse_toggle_key"](), EV_KEY.BTN_LEFT)


def EV_REL_WHEEL(value):
    return InputEvent(EV_REL.REL_WHEEL, value)


if __name__ == "__main__":
    unittest.main()
