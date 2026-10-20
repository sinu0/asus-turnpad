"""The pure helpers of the settings app's SpaceMouse page (config_app/spacemouse.py)."""

import socket
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

try:
    import gi  # noqa: F401
    from gi.repository import GLib  # noqa: F401
except ImportError:  # pragma: no cover
    gi = None

from config.schema import (
    MacroStep,
    Profile,
    Segment,
    SpaceMouseAction,
    SpaceMouseButton,
    StepType,
    default_spacemouse_buttons,
    _default_config,
)

from spacemouse_devices import GROUPS, SPACEPILOT_PRO

if gi is not None:
    from config_app import spacemouse as sm
    from fake_spacenavd import FakeSpacenavd
    from spacenav_client import UEV_RAWBUTTON, pack_event


@unittest.skipIf(gi is None, "needs PyGObject")
class DescribeTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(sm.button_name(0), "Menu")
        self.assertEqual(sm.button_name(12), "Function key 1")
        self.assertEqual(sm.button_name(21), "Function key 5 (long)")
        self.assertEqual(sm.button_name(2), "Top view")
        self.assertEqual(sm.button_name(6), "Bottom view")
        self.assertEqual(sm.button_name(30), "Speed −")
        self.assertEqual(sm.button_name(40), "Button 40")
        self.assertEqual(sm.button_name(3, None), "Button 3")

    def test_model_follows_the_device_name(self):
        self.assertIs(sm.model_for_device(""), SPACEPILOT_PRO)  # no device known: the default map's
        self.assertIs(sm.model_for_device("3Dconnexion SpacePilot PRO"), SPACEPILOT_PRO)
        generic = sm.model_for_device("3Dconnexion SpaceNavigator", 15)
        self.assertEqual((generic.id, generic.button_count), ("generic", 15))

    def test_key_rows_group_the_physical_keys(self):
        rows = sm.key_rows(SPACEPILOT_PRO)
        self.assertEqual(len(rows), 21)
        self.assertEqual(len({index for row in rows for index in row.indexes}), 31)
        self.assertEqual([row.group for row in rows], sorted((row.group for row in rows), key=GROUPS.index))
        first = rows[0]
        self.assertEqual((first.group, first.title, first.indexes, first.dual), ("function", "Function key 1", (12, 17), True))
        self.assertEqual(sm.row_for_index(rows, 6).title, "Top view")
        self.assertFalse(sm.row_for_index(rows, 0).dual)
        self.assertIsNone(sm.row_for_index(rows, 40))

    def test_unknown_buttons_get_raw_rows(self):
        rows = sm.key_rows(SPACEPILOT_PRO, [{"40": SpaceMouseButton(press=SpaceMouseAction("dial")), "x": None}], {41})
        self.assertEqual([(row.group, row.title) for row in rows[-2:]], [("other", "Button 40"), ("other", "Button 41")])
        self.assertEqual(len(sm.key_rows(sm.model_for_device("Other mouse", 4))), 4)
        self.assertEqual(sm.group_title(SPACEPILOT_PRO, "view"), "View keys")
        self.assertEqual(sm.group_title(sm.model_for_device("Other mouse", 4), "system"), "Buttons")

    def test_actions_in_words(self):
        describe = sm.describe_action
        self.assertEqual(describe(None), "Pass through")
        self.assertEqual(describe(SpaceMouseAction("none")), "Pass through")
        self.assertEqual(describe(SpaceMouseAction("dial")), "Dial")
        self.assertEqual(describe(SpaceMouseAction("macro", keys=["KEY_LEFTCTRL", "KEY_TAB"])), "Ctrl+Tab")
        self.assertEqual(describe(SpaceMouseAction("macro")), "Keys not set")
        self.assertEqual(describe(SpaceMouseAction("key_hold", keys=["KEY_LEFTCTRL", "KEY_TAB"])), "Hold Ctrl+Tab")
        self.assertEqual(describe(SpaceMouseAction("modifier", modifier="Ctrl")), "Hold Ctrl")
        self.assertEqual(describe(SpaceMouseAction("group", group="Modeling")), "Group: Modeling")
        self.assertEqual(describe(SpaceMouseAction("group")), "Group: first")
        self.assertEqual(describe(SpaceMouseAction("function", function="Zoom")), "Function: Zoom")
        self.assertEqual(describe(SpaceMouseAction("function", slot=2)), "Function: #3")
        self.assertEqual(describe(SpaceMouseAction("select")), "Select")
        self.assertEqual(describe(SpaceMouseAction("back")), "Back")
        macro = [MacroStep(type=StepType.KEYSTROKE, keys=["KEY_TAB"]), MacroStep(type=StepType.KEYSTROKE, keys=["KEY_ESC"])]
        self.assertTrue(describe(SpaceMouseAction("macro", macro=macro)).endswith("…"))

    def test_button_with_hold(self):
        button = SpaceMouseButton(press=SpaceMouseAction("select"), hold=SpaceMouseAction("group", group="Edit"))
        self.assertEqual(sm.describe_button(button), "Select · Hold: Group: Edit")
        self.assertEqual(sm.describe_button(SpaceMouseButton(hold=SpaceMouseAction("back"))), "Hold: Back")
        self.assertEqual(sm.describe_button(None), "Pass through")

    def test_the_label_replaces_the_description(self):
        button = SpaceMouseButton(press=SpaceMouseAction("select"), label="Pick")
        self.assertEqual(sm.describe_button(button), "Pick")
        self.assertEqual(sm.describe_button(button, use_label=False), "Select")
        self.assertEqual(sm.describe_button(SpaceMouseButton(label="Front")), "Front")

    def test_every_default_has_a_choice(self):
        choices = {key for key, _label in sm.ACTION_CHOICES}
        for button in default_spacemouse_buttons().values():
            for action in (button.press, button.hold):
                if action is not None:
                    self.assertIn(sm.choice_of(action), choices)

    def test_choices_round_trip(self):
        for key, _label in sm.ACTION_CHOICES:
            action = sm.new_action(key)
            self.assertEqual(sm.choice_of(action), key)

    def test_changing_the_type_keeps_the_keys(self):
        macro = SpaceMouseAction("macro", keys=["KEY_LEFTCTRL", "KEY_TAB"])
        self.assertEqual(sm.new_action("key_hold", macro).keys, ["KEY_LEFTCTRL", "KEY_TAB"])
        self.assertIsNone(sm.new_action("dial", macro).keys)

    def test_hold_keys_without_keys_is_not_valid(self):
        self.assertFalse(sm.is_valid(SpaceMouseAction("key_hold")))
        self.assertTrue(sm.is_valid(SpaceMouseAction("key_hold", keys=["KEY_LEFTCTRL"])))
        self.assertTrue(sm.is_valid(SpaceMouseAction("macro")))
        self.assertTrue(sm.is_valid(None))

    def test_button_count(self):
        self.assertEqual(sm.button_count(), 31)
        self.assertEqual(sm.button_count([{"40": SpaceMouseButton(press=SpaceMouseAction("dial"))}]), 41)
        self.assertEqual(sm.button_count(reported=12), 12)


@unittest.skipIf(gi is None, "needs PyGObject")
class MapTests(unittest.TestCase):
    def setUp(self):
        self.config = _default_config()
        self.profile = self.config.profiles[0]

    def test_global_defaults_are_kept_on_the_first_edit(self):
        self.assertEqual(self.config.spacemouse.buttons, {})
        buttons = sm.editable_global(self.config)
        self.assertEqual(set(buttons), set(default_spacemouse_buttons()))
        sm.store_global(buttons, 3, SpaceMouseButton(press=SpaceMouseAction("select")))
        self.assertIn("0", self.config.spacemouse.buttons)
        self.assertEqual(self.config.spacemouse.buttons["3"].press.type, "select")

    def test_profile_replaces_the_global_button(self):
        button, source = sm.binding(self.config, self.profile, 0)
        self.assertEqual((button.press.type, source), ("dial", "global"))
        sm.store(self.profile.spacemouse_buttons, 0, SpaceMouseButton(press=SpaceMouseAction("back")))
        button, source = sm.binding(self.config, self.profile, 0)
        self.assertEqual((button.press.type, button.hold, source), ("back", None, "profile"))
        self.assertEqual(sm.binding(self.config, self.profile, 5), (None, None))
        self.assertEqual(sm.binding(self.config, None, 0)[1], "global")

    def test_the_label_is_stored_with_the_button(self):
        buttons = sm.editable_global(self.config)
        sm.store_global(buttons, 12, SpaceMouseButton(press=SpaceMouseAction("select"), label="Pick"))
        self.assertEqual(self.config.spacemouse.buttons["12"].label, "Pick")
        sm.store(self.profile.spacemouse_buttons, 12, SpaceMouseButton(label="Only a label"))
        stored = self.profile.spacemouse_buttons["12"]
        self.assertEqual((stored.press.type, stored.label), ("none", "Only a label"))
        sm.store_global(buttons, 12, SpaceMouseButton())
        self.assertNotIn("12", self.config.spacemouse.buttons)

    def test_a_button_without_actions_passes_through(self):
        sm.store(self.profile.spacemouse_buttons, 0, SpaceMouseButton())
        self.assertEqual(self.profile.spacemouse_buttons["0"].press.type, "none")

    def test_emptying_the_global_map_keeps_it_non_empty(self):
        buttons = sm.editable_global(self.config)
        for key in list(buttons):
            sm.store_global(buttons, int(key), SpaceMouseButton())
        self.assertTrue(self.config.spacemouse.buttons)

    def test_reset_restores_the_default(self):
        buttons = sm.editable_global(self.config)
        sm.store_global(buttons, 0, SpaceMouseButton(press=SpaceMouseAction("back")))
        sm.reset_global_button(self.config, 0)
        self.assertEqual(self.config.spacemouse.buttons["0"].press.type, "dial")
        sm.reset_global_button(self.config, 4)
        self.assertNotIn("4", self.config.spacemouse.buttons)

    def test_group_and_function_titles(self):
        group = Segment(title="Modeling", items=[Segment(title="Extrude"), Segment(title="Fillet")])
        profile = Profile(name="CAD", segments=[Segment(title="Zoom"), group])
        self.assertEqual(sm.group_titles([profile]), ["Modeling"])
        self.assertEqual(sm.function_titles([profile]), ["Zoom", "Extrude", "Fillet"])


@unittest.skipIf(gi is None, "needs PyGObject")
class SocketTests(unittest.TestCase):
    def test_socket_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "spnav.sock")
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(path)
            try:
                self.assertTrue(sm.socket_exists(path))
            finally:
                server.close()


def iterate(until, timeout=3.0):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not until() and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.002)
    return until()


@unittest.skipIf(gi is None, "needs PyGObject")
class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        # Only the fake's socket, never a real spacenavd of this machine.
        patcher = mock.patch.object(sm, "socket_candidates", lambda configured, environ, spnavrc: [configured])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_connects_without_blocking_and_reports_buttons(self):
        daemon = FakeSpacenavd(self.directory.name, first_reply_delay=0.2)
        self.addCleanup(daemon.stop)
        pressed, done = [], []
        probe = sm.ButtonProbe(daemon.path, pressed.append)
        started = time.monotonic()
        probe.start(done.append)
        self.assertLess(time.monotonic() - started, 0.1)  # the handshake runs in a thread
        self.assertTrue(iterate(lambda: done))
        self.assertEqual(done, [True])
        self.assertTrue(daemon.wait_for(lambda: daemon.evmask == 0x20))
        daemon.send(pack_event(UEV_RAWBUTTON, 7, 1))
        daemon.send(pack_event(UEV_RAWBUTTON, 8, 0))  # a release
        daemon.send(pack_event(UEV_RAWBUTTON, 9, 1))
        self.assertTrue(iterate(lambda: len(pressed) >= 2))
        self.assertEqual(pressed, [7, 9])
        probe.stop()
        self.assertFalse(probe.running)

    def test_lost_connection_is_reported(self):
        daemon = FakeSpacenavd(self.directory.name)
        self.addCleanup(daemon.stop)
        pressed, done = [], []
        probe = sm.ButtonProbe(daemon.path, pressed.append)
        probe.start(done.append)
        self.assertTrue(iterate(lambda: done))
        daemon.drop_client()
        self.assertTrue(iterate(lambda: pressed))
        self.assertEqual(pressed, [None])
        probe.stop()

    def test_unreachable_daemon(self):
        done = []
        probe = sm.ButtonProbe(str(Path(self.directory.name) / "nothing"), lambda _index: None)
        probe.start(done.append)
        self.assertTrue(iterate(lambda: done))
        self.assertFalse(done[0] and probe.running)

    def test_stopped_before_connected_stays_quiet(self):
        daemon = FakeSpacenavd(self.directory.name, first_reply_delay=0.2)
        self.addCleanup(daemon.stop)
        done = []
        probe = sm.ButtonProbe(daemon.path, lambda _index: None)
        probe.start(done.append)
        probe.stop()
        iterate(lambda: False, timeout=0.6)
        self.assertEqual(done, [])
        self.assertFalse(probe.running)


if __name__ == "__main__":
    unittest.main()
