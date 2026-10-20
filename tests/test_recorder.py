"""The macro recorder's conversion and keyboard detection (config_app/recorder.py):
key events become macro steps; no hardware, no GTK main loop."""

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import gi  # noqa: F401
    from gi.repository import GLib
except ImportError:  # pragma: no cover
    gi = None

from config.schema import StepType

if gi is not None:
    from config_app import recorder
    from config_app.recorder import Recording, convert, gtk_event, keyboard_nodes


def keys_of(steps):
    return [step.keys for step in steps]


@unittest.skipIf(gi is None, "needs PyGObject")
class ConvertTest(unittest.TestCase):
    def test_modifier_and_key_make_one_step(self):
        events = [
            ("KEY_LEFTCTRL", 1, 0.0), ("KEY_C", 1, 0.1), ("KEY_C", 0, 0.15), ("KEY_LEFTCTRL", 0, 0.2),
        ]
        steps = convert(events)
        self.assertEqual(keys_of(steps), [["KEY_LEFTCTRL", "KEY_C"]])
        self.assertIs(steps[0].type, StepType.KEYSTROKE)

    def test_modifiers_keep_the_order_they_were_pressed_in(self):
        events = [("KEY_LEFTSHIFT", 1, 0), ("KEY_LEFTCTRL", 1, 0.1), ("KEY_Z", 1, 0.2)]
        self.assertEqual(keys_of(convert(events)), [["KEY_LEFTSHIFT", "KEY_LEFTCTRL", "KEY_Z"]])

    def test_plain_keys_are_one_step_each(self):
        events = [("KEY_A", 1, 0), ("KEY_A", 0, 0.05), ("KEY_B", 1, 0.1), ("KEY_B", 0, 0.15)]
        self.assertEqual(keys_of(convert(events)), [["KEY_A"], ["KEY_B"]])

    def test_overlapping_keys_each_count(self):
        events = [("KEY_A", 1, 0), ("KEY_B", 1, 0.05), ("KEY_A", 0, 0.1), ("KEY_B", 0, 0.12)]
        self.assertEqual(keys_of(convert(events)), [["KEY_A"], ["KEY_B"]])

    def test_auto_repeat_is_ignored(self):
        events = [("KEY_A", 1, 0)] + [("KEY_A", 2, 0.5 + 0.03 * n) for n in range(20)] + [("KEY_A", 0, 1.2)]
        self.assertEqual(keys_of(convert(events)), [["KEY_A"]])

    def test_a_second_press_of_a_key_that_is_down_is_ignored(self):
        events = [("KEY_A", 1, 0), ("KEY_A", 1, 0.1), ("KEY_A", 0, 0.2)]
        self.assertEqual(keys_of(convert(events)), [["KEY_A"]])

    def test_modifier_only_presses_are_ignored(self):
        events = [("KEY_LEFTCTRL", 1, 0), ("KEY_LEFTCTRL", 0, 0.2), ("KEY_LEFTMETA", 1, 1), ("KEY_LEFTMETA", 0, 1.1)]
        self.assertEqual(convert(events), [])

    def test_a_released_modifier_is_not_carried_to_the_next_key(self):
        events = [("KEY_LEFTCTRL", 1, 0), ("KEY_C", 1, 0.1), ("KEY_C", 0, 0.2), ("KEY_LEFTCTRL", 0, 0.3),
                  ("KEY_V", 1, 0.4)]
        self.assertEqual(keys_of(convert(events)), [["KEY_LEFTCTRL", "KEY_C"], ["KEY_V"]])

    def test_escape_is_recorded_as_a_key(self):
        self.assertEqual(keys_of(convert([("KEY_ESC", 1, 0), ("KEY_ESC", 0, 0.1)])), [["KEY_ESC"]])

    def test_mouse_buttons_and_junk_are_ignored(self):
        events = [("BTN_LEFT", 1, 0), ("KEY_RESERVED", 1, 0), (None, 1, 0), ("KEY_A", 1, 0.1)]
        self.assertEqual(keys_of(convert(events)), [["KEY_A"]])

    def test_right_hand_modifiers_are_kept(self):
        events = [("KEY_RIGHTALT", 1, 0), ("KEY_E", 1, 0.1)]
        self.assertEqual(keys_of(convert(events)), [["KEY_RIGHTALT", "KEY_E"]])

    def test_pauses_are_dropped_by_default(self):
        events = [("KEY_A", 1, 0), ("KEY_A", 0, 0.1), ("KEY_B", 1, 5), ("KEY_B", 0, 5.1)]
        self.assertEqual([step.type for step in convert(events)], [StepType.KEYSTROKE] * 2)

    def test_long_pauses_become_wait_steps_when_kept(self):
        events = [("KEY_A", 1, 0), ("KEY_A", 0, 0.1), ("KEY_B", 1, 1.6), ("KEY_B", 0, 1.7)]
        steps = convert(events, keep_pauses=True)
        self.assertEqual([step.type for step in steps], [StepType.KEYSTROKE, StepType.DELAY, StepType.KEYSTROKE])
        self.assertEqual(steps[1].duration, 1.5)

    def test_short_pauses_stay_out_when_kept(self):
        events = [("KEY_A", 1, 0), ("KEY_A", 0, 0.1), ("KEY_B", 1, 0.3), ("KEY_B", 0, 0.4)]
        self.assertEqual(len(convert(events, keep_pauses=True)), 2)

    def test_the_pause_counts_from_the_release_not_the_press(self):
        # Held for 2 s, then the next key at once: no pause.
        events = [("KEY_A", 1, 0), ("KEY_A", 0, 2.0), ("KEY_B", 1, 2.1)]
        self.assertEqual(len(convert(events, keep_pauses=True)), 2)

    def test_no_wait_before_the_first_step_and_a_cap_on_the_longest(self):
        events = [("KEY_A", 1, 100), ("KEY_A", 0, 100.1), ("KEY_B", 1, 300)]
        steps = convert(events, keep_pauses=True)
        self.assertIs(steps[0].type, StepType.KEYSTROKE)
        self.assertEqual(steps[1].duration, 10.0)

    def test_recording_keeps_the_events_for_both_settings(self):
        recording = Recording()
        for name, value, when in [("KEY_A", 1, 0), ("KEY_A", 0, 0.1), ("KEY_B", 1, 2)]:
            recording.add(name, value, when)
        self.assertEqual(len(recording.steps()), 2)
        self.assertEqual(len(recording.steps(keep_pauses=True)), 3)
        self.assertGreaterEqual(recording.idle(), 0)

    def test_gtk_events_use_the_evdev_names(self):
        self.assertEqual(gtk_event(8 + 30, True, 1.0), ("KEY_A", 1, 1.0))
        self.assertEqual(gtk_event(8 + 30, False, 2.0), ("KEY_A", 0, 2.0))
        self.assertIsNone(gtk_event(0, True))


DEVICES = """\
I: Bus=0011 Vendor=0001 Product=0001 Version=ab41
N: Name="AT Translated Set 2 keyboard"
H: Handlers=sysrq kbd event3 leds
B: PROP=0
B: EV=120013
B: KEY=402000000 3803078f800d001 feffffdfffefffff fffffffffffffffe

I: Bus=0003 Vendor=046d Product=c52b Version=0111
N: Name="Logitech USB Receiver Mouse"
H: Handlers=mouse2 event5
B: EV=17
B: KEY=ffff0000 0 0 0 0
B: REL=1943

I: Bus=0006 Vendor=0000 Product=0000 Version=0000
N: Name="Turnpad DialPad virtual keyboard"
H: Handlers=sysrq kbd event9
B: KEY=402000000 3803078f800d001 feffffdfffefffff fffffffffffffffe

I: Bus=0003 Vendor=046d Product=c629 Version=0111
N: Name="3Dconnexion SpacePilot Pro"
H: Handlers=event7
B: KEY=ffff0000 0 0 0 0

I: Bus=0003 Vendor=1234 Product=0001 Version=0001
N: Name="Power Button"
H: Handlers=kbd event0
B: KEY=10000000000000 0
"""


@unittest.skipIf(gi is None, "needs PyGObject")
class KeyboardTest(unittest.TestCase):
    def test_only_real_keyboards_are_read(self):
        # 64-bit words, as on x86-64.
        self.assertEqual(keyboard_nodes(DEVICES, word_bits=64), ["event3"])

    def test_nothing_listed_means_no_nodes(self):
        self.assertEqual(keyboard_nodes("", word_bits=64), [])


class FakeDevice:
    def __init__(self, events):
        self.pending = list(events)

    def events(self):
        pending, self.pending = self.pending, []
        return iter(pending)


def event(code, value, sec=10, usec=0, type_=1):
    return SimpleNamespace(type=SimpleNamespace(value=type_), code=SimpleNamespace(value=code),
                           value=value, sec=sec, usec=usec)


@unittest.skipIf(gi is None, "needs PyGObject")
class KeyReaderTest(unittest.TestCase):
    def setUp(self):
        self.pipes = []
        self.devices = {}

    def tearDown(self):
        for fd in self.pipes:
            try:
                os.close(fd)
            except OSError:
                pass

    def opener(self, node):
        if node == "event99":
            raise PermissionError("not readable")
        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        self.pipes.append(write_fd)
        return os.fdopen(read_fd, "rb"), self.devices.setdefault(node, FakeDevice([]))

    def pump(self, reader, device, events):
        device.pending = list(events)
        os.write(self.pipes[-1], b"x")
        context = GLib.MainContext.default()
        for _ in range(5):
            context.iteration(False)

    def test_reads_key_events_and_skips_the_rest(self):
        got = []
        reader = recorder.KeyReader(["event99", "event3"], lambda *args: got.append(args), self.opener)
        self.assertEqual(reader.start(), 1)
        device = self.devices["event3"]
        # KEY_A = 30 down, a relative event (type 2), KEY_A up half a second later.
        self.pump(reader, device, [event(30, 1, 10, 0), event(0, 5, type_=2), event(30, 0, 10, 500000)])
        reader.stop()
        self.assertEqual(got, [("KEY_A", 1, 10.0), ("KEY_A", 0, 10.5)])
        self.assertEqual(reader.count, 0)

    def test_a_device_that_fails_is_dropped(self):
        reader = recorder.KeyReader(["event3"], lambda *args: None, self.opener)
        reader.start()

        def broken():
            raise OSError("gone")

        self.devices["event3"].events = broken
        os.write(self.pipes[-1], b"x")
        context = GLib.MainContext.default()
        for _ in range(5):
            context.iteration(False)
        self.assertEqual(reader.count, 0)


if __name__ == "__main__":
    unittest.main()
