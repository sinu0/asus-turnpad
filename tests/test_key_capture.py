"""The evdev listener of the toggle key capture (config_app/key_capture.py),
on pipes and a fake device: it reports the first key or button press, ignores
everything else and closes what it opened."""

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

if gi is not None:
    from config_app.key_capture import EvdevWatcher


def event(type_, code, value):
    return SimpleNamespace(type=SimpleNamespace(value=type_), code=SimpleNamespace(value=code), value=value)


class FakeDevice:
    def __init__(self, events):
        self.pending = list(events)
        self.fail = False

    def events(self):
        if self.fail:
            raise OSError("gone")
        pending, self.pending = self.pending, []
        return iter(pending)


@unittest.skipIf(gi is None, "needs PyGObject")
class EvdevWatcherTest(unittest.TestCase):
    def setUp(self):
        self.opened = {}
        self.pipes = []

    def opener(self, node):
        if node == "event99":
            raise PermissionError("not readable")
        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        self.pipes.append(write_fd)
        device = self.opened.setdefault(node, FakeDevice([]))
        return os.fdopen(read_fd, "rb"), device

    def tearDown(self):
        for fd in self.pipes:
            try:
                os.close(fd)
            except OSError:
                pass

    def run_loop(self, watcher, wake):
        keys = []
        watcher.on_key = keys.append
        opened = watcher.start()
        wake()
        context = GLib.MainContext.default()
        for _ in range(50):
            while context.iteration(False):
                pass
            if keys:
                break
        return opened, keys

    def wake_all(self):
        for fd in self.pipes:
            os.write(fd, b"x")

    def test_reports_the_first_press(self):
        self.opened["event2"] = FakeDevice([
            event(2, 8, -1),   # the wheel
            event(1, 275, 0),  # a release
            event(1, 275, 1),  # BTN_SIDE pressed
            event(1, 183, 1),
        ])
        watcher = EvdevWatcher(["event2"], None, opener=self.opener)
        opened, keys = self.run_loop(watcher, self.wake_all)
        self.assertEqual((opened, keys), (1, ["BTN_SIDE"]))
        self.assertEqual(watcher.count, 0)

    def test_unreadable_nodes_are_skipped(self):
        self.opened["event3"] = FakeDevice([event(1, 183, 1)])
        watcher = EvdevWatcher(["event99", "event3"], None, opener=self.opener)
        opened, keys = self.run_loop(watcher, self.wake_all)
        self.assertEqual((opened, keys), (1, ["KEY_F13"]))

    def test_nothing_opened_when_nothing_is_readable(self):
        watcher = EvdevWatcher(["event99"], None, opener=self.opener)
        self.assertEqual(watcher.start(), 0)

    def test_only_one_key_even_from_two_devices(self):
        self.opened["event2"] = FakeDevice([event(1, 4, 1)])
        self.opened["event3"] = FakeDevice([event(1, 183, 1)])
        watcher = EvdevWatcher(["event2", "event3"], None, opener=self.opener)
        _opened, keys = self.run_loop(watcher, self.wake_all)
        self.assertEqual(len(keys), 1)

    def test_a_device_that_goes_away_is_dropped(self):
        device = FakeDevice([])
        device.fail = True
        self.opened["event2"] = device
        watcher = EvdevWatcher(["event2"], None, opener=self.opener)
        watcher.on_key = lambda name: self.fail("no key expected")
        self.assertEqual(watcher.start(), 1)
        self.wake_all()
        context = GLib.MainContext.default()
        for _ in range(20):
            while context.iteration(False):
                pass
        self.assertEqual(watcher.count, 0)

    def test_stop_closes_everything(self):
        watcher = EvdevWatcher(["event2"], lambda name: None, opener=self.opener)
        watcher.start()
        handle = watcher._open[0]["handle"]
        watcher.stop()
        self.assertTrue(handle.closed)
        self.assertEqual(watcher.count, 0)
        watcher.stop()


if __name__ == "__main__":
    unittest.main()
