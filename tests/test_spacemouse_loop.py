"""SpaceMouse mode in the driver: the actions on the dial's state, and the
input loop with the SpaceMouse (against a fake spacenavd) and the mouse
together, without hardware."""

import ast
import os
import select as real_select
import socket
import struct
import sys
import tempfile
import threading
import types
import unittest

from libevdev import EV_KEY, EV_REL, InputEvent, device

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import spacenav_client as proto  # noqa: E402
import test_driver_state  # noqa: E402
from config.schema import (  # noqa: E402
    MODIFIER_KEYS, DialPadConfig, Profile, SpaceMouseAction, SpaceMouseButton, SpaceMouseConfig,
    spacemouse_binds, spacemouse_buttons_for,
)
from dial_gesture import RotationTracker, scroll_acceleration_multiplier, slice_index  # noqa: E402
import threading as _threading  # noqa: E402
from fake_spacenavd import FakeSpacenavd  # noqa: E402
from mouse_dial import MouseDial, MousePointer  # noqa: E402
from spacemouse_dial import ButtonMachine, ListStepper, SpaceCap  # noqa: E402
from test_driver_state import last_state  # noqa: E402
from test_mouse_dial import FakeFile, FakeMouse  # noqa: E402

LOOP_FUNCTIONS = {
    "mouse_toggle_key", "set_mouse_grab", "close_mouse", "listen_input_sources", "turn_selected_function",
    "restore_spacenav_leftovers", "reconcile_spacemouse_keys",
}
LOOP_CLASSES = {"CenterGesture", "InputSource", "MouseSource", "SpaceMouseSource"}


def axis(index, value):
    return proto.pack_event(proto.UEV_RAWAXIS, index, value)


def button(index, pressed):
    return proto.pack_event(proto.UEV_RAWBUTTON, index, 1 if pressed else 0)


def load_driver(shortcuts, gui_config, restore_file):
    """The driver's state functions plus the input loop, around recorded effects."""
    driver, sent, queued = test_driver_state.load_driver_state(shortcuts)
    with open(os.path.join(ROOT, "dialpad.py"), encoding="utf-8") as source:
        tree = ast.parse(source.read())
    nodes = [
        node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in LOOP_FUNCTIONS)
        or (isinstance(node, ast.ClassDef) and node.name in LOOP_CLASSES)
    ]
    assert {node.name for node in nodes} == LOOP_FUNCTIONS | LOOP_CLASSES
    state = types.SimpleNamespace(autorun=False, clock=1000.0, steps=[], failures=[], calls=[], held=[], macros=[], rotations=[])
    noop = staticmethod(lambda *a, **k: None)

    def tick(seconds):
        """One pass of the loop: time moves on, the next scripted step runs."""
        state.clock += seconds
        if not state.steps:
            driver["stop_threads"] = True
            return False
        step = state.steps.pop(0)
        try:
            step()
        except AssertionError as error:
            state.failures.append(error)
        if state.autorun:
            # The action thread keeps up: every queued job has run before the next pass.
            while queued:
                queued.pop(0)()
        return True

    def select_(handles, _write, _error, timeout):
        return (list(handles) if tick(timeout) else []), [], []

    def toggle(current):
        state.calls.append("toggle")
        driver["dialpad"] = not current
        if driver["dialpad"]:
            driver["show_initial_function_state"]()
        else:
            driver["reset_center"]()

    driver.update(
        EV_KEY=EV_KEY, EV_REL=EV_REL, device=device, os=os,
        log=type("Log", (), {"info": noop, "debug": noop, "warning": noop, "error": noop, "exception": noop})(),
        time=lambda: state.clock, sleep=tick,
        select=types.SimpleNamespace(select=select_),
        gui_config=gui_config, SCHEMA_MODIFIER_KEYS=MODIFIER_KEYS, spacemouse_buttons_for=spacemouse_buttons_for, spacemouse_binds=spacemouse_binds,
        SPACENAV_RESTORE_FILE=restore_file, SPACENAV_UNUSED_BUTTON=63,
        SpacenavClient=proto.SpacenavClient, RestoreFile=proto.RestoreFile, # Never the real spacenavd of this computer: only the socket the test names.
        socket_candidates=lambda configured, _environ, _rc: [configured],
        enqueue_action=lambda job, required=False: queued.append(job),
        EVMASK_RAWAXIS=proto.EVMASK_RAWAXIS, EVMASK_RAWBUTTON=proto.EVMASK_RAWBUTTON,
        UEV_MOTION=proto.UEV_MOTION, UEV_PRESS=proto.UEV_PRESS, UEV_RELEASE=proto.UEV_RELEASE,
        UEV_RAWAXIS=proto.UEV_RAWAXIS, UEV_RAWBUTTON=proto.UEV_RAWBUTTON,
        MouseDial=MouseDial, MousePointer=MousePointer, RotationTracker=RotationTracker, SpaceCap=SpaceCap,
        ButtonMachine=ButtonMachine, ListStepper=ListStepper, slice_index=slice_index,
        scroll_acceleration_multiplier=scroll_acceleration_multiplier, SCROLL_TRIGGER_DEGREES=15,
        default_treshold=90, socket_send_progress_above_treshold=120, SUBMENU_STEP_DEGREES=30,
        angle_hysteresis=3, center_hold_time=0.5, last_event_time=0, treshold=None,
        MOUSE_RETRY_SECONDS=2, MOUSE_POLL_SECONDS=0.05,
        mouse_device=None, mouse_file=None, mouse_grabbed=False, mouse_lock=threading.Lock(),
        mouse_mode=False, spacemouse_mode=True, input_sources=[],
        open_mouse=lambda _code: False,
        show_pointer_enabled=lambda: True,
        toggle_top_right_icon=toggle,
        shortcut_has_scroll_action=lambda _shortcuts, _direction: False,
        run_rotation_action=lambda *args: state.rotations.append(args),
        run_function_center_action=lambda *args: state.calls.append(("center action", args[2])),
        run_center_action=lambda *args: state.calls.append(("single center", args[3])),
        execute_config_macro=lambda steps: state.macros.append(steps),
        set_keys_held=lambda keys, pressed, device=None: bool(keys) and state.held.append((tuple(key.name for key in keys), pressed)),
        process_pending_driver_updates=lambda gesture_idle=True: None,
        spacemouse_held_keys=set(), spacemouse_target_keys=[], spacemouse_applied_keys=[],
        spacemouse_key_lock=threading.Lock(), scroll_modifiers_held=[],
        stop_threads=False,
    )
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "dialpad.py", "exec"), driver)
    return driver, sent, queued, state


class SpaceMouseTestCase(unittest.TestCase):
    # Run every queued action before the next pass of the loop (the action thread keeps up).
    autorun = False

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.restore_path = os.path.join(self.directory.name, "spacenav-restore.json")

    def start_server(self, **kwargs):
        server = FakeSpacenavd(self.directory.name, **kwargs)
        self.addCleanup(server.stop)
        return server

    def config(self, server, **kwargs):
        config = DialPadConfig()
        config.spacemouse = SpaceMouseConfig(enabled=True, socket=server.path, **kwargs)
        config.profiles = [
            Profile(name="Default", segments=[test_driver_state.volume(), test_driver_state.vertical_scroll()]),
            Profile(name="Krita", app_id="org.kde.krita.desktop", segments=[
                test_driver_state.zoom_wheel(), test_driver_state.volume(), test_driver_state.tracks(),
            ]),
            Profile(
                name="Files", app_id="org.gnome.Nautilus.desktop",
                segments=[
                    test_driver_state.vertical_scroll(),
                    test_driver_state.Segment(
                        title="Display", color="#f4c95d",
                        items=[test_driver_state.brightness(), test_driver_state.mute(), test_driver_state.volume()],
                    ),
                ],
            ),
        ]
        return config

    def load(self, config, app="org.kde.krita.desktop"):
        from config.legacy_adapter import shortcuts_from_config
        self.driver, self.sent, self.queued, self.state = load_driver(
            shortcuts_from_config(config), config, self.restore_path,
        )
        self.state.autorun = self.autorun
        self.driver["window_was_changed"](app)
        self.driver["dialpad"] = False
        self.driver["reset_center"]()
        self.sent.clear()
        self.queued.clear()
        self.addCleanup(self.close_sources)

    def close_sources(self):
        for source in self.driver["input_sources"]:
            try:
                source.client.close()
            except AttributeError:
                pass

    def run_queue(self):
        while self.queued:
            self.queued.pop(0)()

    def run_steps(self, *steps):
        # Sources of an earlier run in the same test: their sockets are done with.
        for old in list(self.driver["input_sources"]):
            if hasattr(old, "client"):
                old.client.close()
        self.state.steps = list(steps)
        self.driver["stop_threads"] = False
        self.driver["listen_input_sources"]()
        if self.state.failures:
            raise self.state.failures[0]

    def source(self, name="spacemouse"):
        return next(source for source in self.driver["input_sources"] if source.name == name)

    def deliver(self, server, *events):
        """Send events and wait until they are readable on the client's socket."""
        server.send(b"".join(events))
        real_select.select([self.source().client.sock], [], [], 1.0)

    def pump(self, count=1):
        return [lambda: None] * count


class ActionTest(unittest.TestCase):
    """The actions on the dial's state (the driver's state functions)."""

    setUp = test_driver_state.DriverStateTest.setUp
    focus = test_driver_state.DriverStateTest.focus

    def prepare(self, app_id):
        self.toggled = []
        self.driver["gui_config"] = None
        self.driver["spacemouse_buttons_for"] = None

        def toggle(current):
            self.toggled.append(current)
            self.driver["dialpad"] = not current
            if self.driver["dialpad"]:
                self.driver["show_initial_function_state"]()

        self.driver.update(toggle_top_right_icon=toggle, execute_config_macro=lambda steps: self.macros.append(steps))
        self.macros = []
        self.focus(app_id)
        self.driver["dialpad"] = False
        self.driver["reset_center"]()
        self.sent.clear()
        self.queued.clear()

    def act(self, kind, **kwargs):
        self.driver["run_spacemouse_action"](SpaceMouseAction(kind, **kwargs))

    def test_dial_toggles(self):
        self.prepare("org.kde.krita.desktop")
        self.act("dial")
        self.assertTrue(self.driver["dialpad"])
        self.assertEqual(last_state(self.sent)["title"], "Menu")
        self.act("dial")
        self.assertFalse(self.driver["dialpad"])
        self.assertEqual(self.toggled, [False, True])

    def test_function_by_menu_place_switches_the_dial_on_and_enters_it(self):
        self.prepare("org.kde.krita.desktop")
        self.act("function", slot=1)
        self.assertTrue(self.driver["dialpad"])
        self.assertTrue(self.driver["center_activated"])
        self.assertEqual(self.driver["title"], "Volume")

    def test_function_by_name_also_inside_a_group(self):
        self.prepare("org.gnome.Nautilus.desktop")
        self.act("function", function="Mute")
        self.assertEqual((self.driver["submenu"], self.driver["title"]), ("Display", "Mute"))
        self.assertTrue(self.driver["center_activated"] or self.queued)  # a launcher-like function may just run
        self.act("back")

    def test_slot_with_a_group_opens_its_list(self):
        self.prepare("org.gnome.Nautilus.desktop")
        self.act("function", slot=1)
        self.assertEqual(self.driver["submenu"], "Display")
        self.assertFalse(self.driver["center_activated"])

    def test_group_action_opens_the_first_or_the_named_group(self):
        self.prepare("org.gnome.Nautilus.desktop")
        self.act("group")
        self.assertTrue(self.driver["dialpad"])
        self.assertEqual((self.driver["submenu"], self.driver["title"]), ("Display", "Brightness"))
        self.act("back")
        self.assertIsNone(self.driver["submenu"])
        self.act("group", group="Display")
        self.assertEqual(self.driver["submenu"], "Display")

    def test_unknown_targets_do_nothing(self):
        self.prepare("org.gnome.Nautilus.desktop")
        self.act("group", group="Nope")
        self.assertIsNone(self.driver["submenu"])
        self.act("function", function="Nope")
        self.assertFalse(self.driver["center_activated"])
        self.act("function", slot=40)
        self.assertFalse(self.driver["center_activated"])

    def test_group_without_groups(self):
        self.prepare("org.kde.krita.desktop")
        self.act("group")
        self.assertTrue(self.driver["dialpad"])
        self.assertIsNone(self.driver["submenu"])

    def test_select_taps_the_highlighted_entry_and_back_leaves(self):
        self.prepare("org.kde.krita.desktop")
        self.act("dial")
        self.assertFalse(self.driver["tap_center"]())  # nothing highlighted
        self.driver["highlight_menu_slice"](0)
        self.act("select")
        self.assertTrue(self.driver["center_activated"])
        self.act("back")
        self.assertFalse(self.driver["center_activated"])
        self.act("back")  # in the plain menu: nothing
        self.assertTrue(self.driver["dialpad"])

    def test_macro_runs_on_the_action_thread(self):
        self.prepare("org.kde.krita.desktop")
        self.act("macro", keys=["KEY_LEFTCTRL", "KEY_Z"])
        self.assertEqual(self.macros, [])
        self.run_queue_now()
        self.assertEqual([[step.keys for step in steps] for steps in self.macros], [[["KEY_LEFTCTRL", "KEY_Z"]]])
        self.act("macro")  # nothing to run
        self.assertEqual(self.queued, [])

    def run_queue_now(self):
        while self.queued:
            self.queued.pop(0)()

    def test_binding_lookup_profile_over_global(self):
        self.prepare("org.kde.krita.desktop")
        config = DialPadConfig()
        config.spacemouse.buttons = {"5": SpaceMouseButton(press=SpaceMouseAction("dial"))}
        self.driver["gui_config"] = config
        self.driver["spacemouse_buttons_for"] = spacemouse_buttons_for
        self.assertEqual(self.driver["spacemouse_binding"](5).press.type, "dial")
        self.assertIsNone(self.driver["spacemouse_binding"](6))
        self.driver["app_specific_shortcuts"]["_meta"]["spacemouse_buttons"] = {
            "5": SpaceMouseButton(press=SpaceMouseAction("back")),
        }
        self.assertEqual(self.driver["spacemouse_binding"](5).press.type, "back")
        self.assertEqual(self.driver["spacemouse_binding"](6), None)
        config.spacemouse.buttons = {}
        self.assertEqual(self.driver["spacemouse_binding"](17).press.type, "function")  # the default map


class LoopTest(SpaceMouseTestCase):
    def default_start(self, **kwargs):
        server = self.start_server()
        self.server = server
        self.load(self.config(server, **kwargs))
        return server

    def test_connects_asks_for_raw_events_and_names_the_device(self):
        server = self.default_start()
        self.run_steps(*self.pump(2))
        source = self.source()
        self.assertTrue(source.connected)
        self.assertEqual(server.evmask, proto.EVMASK_RAWAXIS | proto.EVMASK_RAWBUTTON)
        self.assertEqual(source.device_name, "3Dconnexion SpacePilot PRO")  # spaces collapsed for display
        self.assertEqual(server.client_name, "Turnpad")
        self.assertEqual(server.sensitivity, 1.0)  # not muted while the dial is off

    def test_menu_button_tilt_release_selects_and_the_others_are_muted_meanwhile(self):
        server = self.default_start()
        self.run_steps(
            lambda: None,
            lambda: self.deliver(server, button(0, True)),
            lambda: self.deliver(server, axis(3, -300)),  # tilt away: the top sector
            lambda: None,
            lambda: (
                self.assertTrue(self.driver["dialpad"]),
                self.assertEqual(self.driver["title"], "Zoom"),
                self.assertEqual(server.sensitivity, 0.0),
                self.assertEqual(proto.RestoreFile(self.restore_path).load(), {"sensitivity": 1.0}),
                self.assertIn({"touching": True}, self.sent),
            ),
            lambda: self.deliver(server, button(0, False)),  # release: select
            lambda: None,
        )
        self.assertTrue(self.driver["center_activated"])
        self.assertEqual(self.driver["title"], "Zoom")
        self.assertEqual(server.sensitivity, 0.0)  # still on

    def test_menu_button_sticky_then_pressed_again_closes_and_unmutes(self):
        server = self.default_start()
        self.run_steps(
            lambda: self.deliver(server, button(0, True)),
            lambda: self.deliver(server, button(0, False)),
            lambda: self.assertTrue(self.driver["dialpad"]),
            lambda: self.deliver(server, button(0, True)),
            lambda: None,
            lambda: (
                self.assertFalse(self.driver["dialpad"]),
                self.assertEqual(server.sensitivity, 1.0),
                self.assertIsNone(proto.RestoreFile(self.restore_path).load()),
            ),
        )

    def test_tilt_highlights_sectors_and_a_twist_turns_the_selected_function(self):
        server = self.default_start()
        self.run_steps(
            lambda: self.deliver(server, button(0, True)),
            lambda: self.deliver(server, axis(5, 300)),  # right: 90 degrees, sector 1
            lambda: None,
            lambda: self.assertEqual(self.driver["title"], "Volume"),
            lambda: self.deliver(server, axis(5, 0)),
            lambda: self.deliver(server, button(0, False)),
            lambda: None,
            lambda: self.assertEqual((self.driver["center_activated"], self.driver["title"]), (True, "Volume")),
            lambda: self.deliver(server, axis(4, -400)),  # full twist: about 300 degrees a second
            *self.pump(12),
        )
        self.run_queue()
        directions = {args[1] for args in self.state.rotations}
        self.assertEqual(directions, {"clockwise"})
        self.assertGreaterEqual(len(self.state.rotations), 3)

    def test_twist_the_other_way_turns_back_and_stops_when_released(self):
        server = self.default_start()
        self.run_steps(
            lambda: self.deliver(server, button(0, True)),
            lambda: self.deliver(server, axis(5, 300)),
            lambda: self.deliver(server, axis(5, 0)),
            lambda: self.deliver(server, button(0, False)),
            lambda: self.deliver(server, axis(4, 400)),
            *self.pump(12),
            lambda: self.deliver(server, axis(4, 0)),
            *self.pump(3),
        )
        self.run_queue()
        count = len(self.state.rotations)
        self.assertGreaterEqual(count, 3)
        self.assertEqual({args[1] for args in self.state.rotations}, {"counterclockwise"})
        self.run_steps(*self.pump(5))
        self.run_queue()
        self.assertEqual(len(self.state.rotations), count)

    def test_twist_moves_the_highlight_in_the_menu_one_step_at_a_time(self):
        server = self.default_start()
        self.run_steps(
            lambda: self.deliver(server, button(0, True)),
            lambda: self.deliver(server, button(0, False)),
            lambda: self.deliver(server, axis(4, -400)),
            *self.pump(8),
        )
        self.assertIn(self.driver["title"], ("Zoom", "Volume", "Media"))
        self.assertFalse(self.driver["center_activated"])

    def test_list_tilt_steps_with_auto_repeat_and_the_group_button_releases_to_select(self):
        server = self.start_server()
        self.server = server
        self.load(self.config(server), app="org.gnome.Nautilus.desktop")
        self.run_steps(
            lambda: self.deliver(server, button(18, True)),
            *self.pump(10),  # the hold action (the first group) fires after 400 ms
            lambda: self.assertEqual(self.driver["submenu"], "Display"),
            lambda: self.deliver(server, axis(3, 300)),  # towards you: down the list
            lambda: None,
            lambda: self.assertEqual(self.driver["title"], "Mute"),
            *self.pump(6),
            lambda: self.assertEqual(self.driver["title"], "Volume"),  # repeated, stopped at the end
            lambda: self.deliver(server, axis(3, 0)),
            lambda: self.deliver(server, button(18, False)),
            lambda: None,
        )
        self.assertEqual((self.driver["submenu"], self.driver["title"]), ("Display", "Volume"))
        self.assertTrue(self.driver["center_activated"])

    def test_function_button_enters_the_function_at_once(self):
        server = self.default_start()
        self.run_steps(
            lambda: self.deliver(server, button(17, True)),
            lambda: self.deliver(server, button(17, False)),  # tapped: the press action
            lambda: None,
        )
        self.assertEqual((self.driver["dialpad"], self.driver["center_activated"], self.driver["title"]), (True, True, "Zoom"))

    def test_push_is_the_centre_button(self):
        server = self.default_start()
        self.run_steps(
            lambda: self.deliver(server, button(0, True)),
            lambda: self.deliver(server, axis(3, -300)),
            lambda: self.deliver(server, axis(3, 0)),
            lambda: self.deliver(server, button(0, False)),
            lambda: self.deliver(server, button(22, True)),  # Esc: back to the menu
            lambda: self.deliver(server, button(22, False)),
            lambda: self.assertFalse(self.driver["center_activated"]),
            lambda: self.deliver(server, axis(3, -300)),  # highlight again
            lambda: self.deliver(server, axis(3, 0)),
            lambda: self.deliver(server, axis(1, -300)),   # push
            lambda: self.deliver(server, axis(1, 0)),     # let go: a tap selects
            lambda: None,
        )
        self.assertTrue(self.driver["center_activated"])
        self.assertEqual(self.driver["title"], "Zoom")

    def test_hold_of_the_push_goes_back(self):
        server = self.default_start()
        self.run_steps(
            lambda: self.deliver(server, button(17, True)),
            lambda: self.deliver(server, button(17, False)),
            lambda: self.deliver(server, axis(1, -300)),
            *self.pump(14),  # past the centre hold time (0.5 s)
            lambda: self.deliver(server, axis(1, 0)),
        )
        self.assertFalse(self.driver["center_activated"])

    def test_modifier_buttons_hold_their_key_while_down(self):
        self.autorun = True
        server = self.default_start()
        self.run_steps(
            lambda: self.deliver(server, button(25, True)),
            lambda: None,
            lambda: self.deliver(server, button(25, False)),
            lambda: None,
        )
        self.run_queue()
        self.assertEqual(self.state.held, [(("KEY_LEFTCTRL",), True), (("KEY_LEFTCTRL",), False)])

    def test_unbound_buttons_do_nothing(self):
        server = self.default_start()
        self.run_steps(lambda: self.deliver(server, button(9, True)), lambda: self.deliver(server, button(9, False)))
        self.assertFalse(self.driver["dialpad"])

    def test_custom_buttons_and_a_profile_override(self):
        server = self.start_server()
        self.server = server
        config = self.config(server, buttons={
            "3": SpaceMouseButton(press=SpaceMouseAction("macro", keys=["KEY_F5"])),
            "4": SpaceMouseButton(press=SpaceMouseAction("macro", keys=["KEY_F6"])),
        })
        config.profiles[1].spacemouse_buttons = {"4": SpaceMouseButton(press=SpaceMouseAction("macro", keys=["KEY_F7"]))}
        self.load(config)
        self.run_steps(
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(4, True)),
            lambda: None,
        )
        self.run_queue()
        self.assertEqual([steps[0].keys for steps in self.state.macros], [["KEY_F5"], ["KEY_F7"]])

    def test_lost_connection_releases_everything_and_reconnects(self):
        self.autorun = True
        server = self.default_start()
        self.run_steps(
            lambda: self.deliver(server, button(25, True)),
            lambda: self.deliver(server, button(0, True)),
            lambda: self.deliver(server, button(0, False)),
            lambda: None,
            lambda: (self.assertEqual(server.sensitivity, 0.0), server.drop_client()),
            *self.pump(50),  # the loss, two seconds of waiting, the new connection
        )
        self.run_queue()
        self.assertIn((("KEY_LEFTCTRL",), False), self.state.held)
        source = self.source()
        self.assertEqual(server.connections, 2)  # found again after two seconds of search
        self.assertTrue(source.connected)
        self.assertEqual(server.evmask, 0x30)
        # The dial is still on: muted again. The new connection first gave back
        # what the lost one left (the saved sensitivity 1.0 is what it remembers).
        self.assertTrue(source.muted)
        self.assertEqual(source.saved_sensitivity, 1.0)
        self.assertEqual(server.sensitivity, 0.0)
        source.close()
        self.assertEqual(server.sensitivity, 1.0)
        self.assertIsNone(proto.RestoreFile(self.restore_path).load())

    def test_waits_for_spacenavd_and_connects_when_it_starts(self):
        server = self.default_start()
        self.driver["gui_config"].spacemouse.socket = os.path.join(self.directory.name, "later.sock")
        self.load(self.driver["gui_config"])
        self.run_steps(*self.pump(3))
        self.assertFalse(self.source().connected)
        self.assertTrue(self.driver["dialpad"] is False)

    def test_old_daemon_without_raw_events_uses_press_and_release(self):
        original = proto.HANDSHAKE_TIMEOUT, proto.HANDSHAKE_RETRY_TIMEOUT
        proto.HANDSHAKE_TIMEOUT = proto.HANDSHAKE_RETRY_TIMEOUT = 0.05
        self.addCleanup(lambda: (setattr(proto, "HANDSHAKE_TIMEOUT", original[0]), setattr(proto, "HANDSHAKE_RETRY_TIMEOUT", original[1])))
        server = self.start_server(version=None)
        self.server = server
        self.load(self.config(server))
        self.run_steps(
            lambda: None,
            lambda: self.deliver(server, proto.pack_event(proto.UEV_PRESS, 17)),
            lambda: self.deliver(server, proto.pack_event(proto.UEV_RELEASE, 17)),
            lambda: None,
        )
        self.assertEqual(self.source().client.proto, 0)
        self.assertEqual((self.driver["dialpad"], self.driver["title"]), (True, "Zoom"))
        self.assertFalse(self.source().muted)  # nothing to mute with

    def test_hidden_buttons_are_mapped_away_and_restored(self):
        server = self.default_start(hide_buttons=True)
        self.run_steps(*self.pump(2))
        self.assertEqual(server.button_map.get(0), 63)
        self.assertEqual(server.button_map.get(25), 63)
        saved = proto.RestoreFile(self.restore_path).load()
        self.assertEqual(saved["bnmap"]["0"], 0)
        self.source().close()
        self.assertEqual(server.button_map.get(0), 0)
        self.assertEqual(server.button_map.get(25), 25)
        self.assertIsNone(proto.RestoreFile(self.restore_path).load())

    def test_not_muting_when_asked_not_to(self):
        server = self.default_start(mute_others=False)
        self.run_steps(lambda: self.deliver(server, button(0, True)), *self.pump(2))
        self.assertTrue(self.driver["dialpad"])
        self.assertEqual(server.sensitivity, 1.0)

    def test_cleanup_gives_the_daemon_back(self):
        server = self.default_start()
        self.run_steps(lambda: self.deliver(server, button(0, True)), *self.pump(2))
        self.assertEqual(server.sensitivity, 0.0)
        self.source().close()
        self.assertEqual(server.sensitivity, 1.0)
        self.assertIsNone(proto.RestoreFile(self.restore_path).load())


class CombinedLoopTest(SpaceMouseTestCase):
    """The mouse and the SpaceMouse in one select loop."""

    def test_both_sources_drive_the_same_dial(self):
        server = self.start_server()
        config = self.config(server)
        config.mouse.enabled = True
        self.load(config)
        mouse = FakeMouse(self.state.calls)
        self.driver.update(mouse_mode=True, mouse_device=mouse, mouse_file=FakeFile())

        def wheel(value):
            mouse.pending.append(InputEvent(EV_REL.REL_WHEEL, value))

        self.run_steps(
            lambda: self.deliver(server, button(0, True)),     # the SpaceMouse's Menu button
            lambda: self.deliver(server, axis(5, 300)),         # sector 1
            lambda: None,
            lambda: self.assertEqual(self.driver["title"], "Volume"),
            lambda: wheel(-1),                                  # the mouse wheel: next sector
            lambda: None,
            lambda: self.assertEqual(self.driver["title"], "Media"),
            lambda: self.deliver(server, button(0, False)),    # release: select
            lambda: None,
            lambda: self.assertTrue(self.driver["center_activated"]),
            lambda: mouse.pending.append(InputEvent(EV_KEY.BTN_RIGHT, 1)),  # the mouse's right button: back
            lambda: None,
        )
        self.assertFalse(self.driver["center_activated"])
        self.assertEqual([source.name for source in self.driver["input_sources"]], ["mouse", "spacemouse"])
        self.assertIn("grab", self.state.calls)  # the dial is on: the mouse is grabbed
        self.assertEqual(server.sensitivity, 0.0)

    def test_the_mouse_alone_still_works_while_spacenavd_is_missing_and_reconnects_later(self):
        server = self.start_server()
        config = self.config(server)
        config.spacemouse.socket = os.path.join(self.directory.name, "absent.sock")
        config.mouse.enabled = True
        self.load(config)
        mouse = FakeMouse(self.state.calls)
        self.driver.update(mouse_mode=True, mouse_device=mouse, mouse_file=FakeFile())
        self.driver["dialpad"] = True
        self.driver["show_initial_function_state"]()

        def wheel():
            mouse.pending.append(InputEvent(EV_REL.REL_WHEEL, -1))

        self.run_steps(wheel, lambda: None, *self.pump(3))
        self.assertEqual(self.driver["title"], "Zoom")
        self.assertFalse(self.source().connected)
        # spacenavd appears (its socket is where the config says)
        self.driver["gui_config"].spacemouse.socket = server.path
        self.source().settings.socket = server.path
        self.run_steps(*self.pump(60))  # about 3 s of polling
        self.assertTrue(self.source().connected)
        self.assertEqual(server.sensitivity, 0.0)  # the dial is on, so it is muted at once

    def test_the_mouse_going_away_does_not_stop_the_spacemouse(self):
        server = self.start_server()
        config = self.config(server)
        config.mouse.enabled = True
        self.load(config)
        mouse = FakeMouse(self.state.calls)
        self.driver.update(mouse_mode=True, mouse_device=mouse, mouse_file=FakeFile())

        def unplug():
            mouse.gone = True
            mouse.pending.append(InputEvent(EV_REL.REL_WHEEL, 1))

        self.run_steps(
            unplug,
            lambda: self.deliver(server, button(0, True)),
            lambda: None,
        )
        self.assertIsNone(self.driver["mouse_device"])
        self.assertTrue(self.driver["dialpad"])
        self.assertTrue(self.source().connected)


class DroppingDaemon(FakeSpacenavd):
    """Answers the handshake and hangs up at once."""

    def handle(self, client):
        try:
            self.read_exact(client, 4)
            client.sendall(struct.pack("=i", proto.REQ_TAG | proto.REQ_CHANGE_PROTO | 1))
            client.shutdown(socket.SHUT_RDWR)
            client.close()
        except OSError:
            pass


class ReviewFixesTest(SpaceMouseTestCase):
    """Regression tests for the problems found in the review of stage 1."""

    def default_start(self, **kwargs):
        server = self.start_server()
        self.load(self.config(server, **kwargs))
        return server

    # 2. a daemon that goes away right after connecting must not stop the driver
    def test_daemon_hanging_up_after_the_handshake_does_not_stop_the_loop(self):
        server = DroppingDaemon(self.directory.name)
        self.addCleanup(server.stop)
        self.load(self.config(server))
        passes = []
        self.run_steps(*[lambda: passes.append(1) for _ in range(5)])
        self.assertEqual(len(passes), 5)
        self.assertFalse(self.source().connected)

    def test_an_os_error_while_searching_is_a_lost_source_not_a_dead_driver(self):
        server = self.default_start()
        calls = []

        class Boom(proto.SpacenavClient):
            def connect(self, _paths):
                calls.append(1)
                raise BrokenPipeError(32, "Broken pipe")

        self.driver["SpacenavClient"] = Boom
        passes = []
        self.run_steps(*[lambda: passes.append(1) for _ in range(5)])
        self.assertEqual(len(passes), 5)
        self.assertEqual(len(calls), 1)  # asked once, then waits two seconds

    # 3. a push that chose something clears the opener
    def test_push_selection_after_a_sticky_menu_leaves_no_opener(self):
        server = self.default_start()
        self.run_steps(
            lambda: self.deliver(server, button(0, True)),
            lambda: self.deliver(server, button(0, False)),  # sticky menu
            lambda: self.deliver(server, axis(3, -300)),     # highlight
            lambda: self.deliver(server, axis(3, 0)),
            lambda: self.deliver(server, axis(1, -300)),
            lambda: self.deliver(server, axis(1, 0)),        # push: enters the function
            lambda: None,
            lambda: (self.assertTrue(self.driver["center_activated"]), self.assertIsNone(self.source().buttons.opener)),
            lambda: self.deliver(server, button(0, True)),   # Menu again: closes the dial
            lambda: self.deliver(server, button(0, False)),
            lambda: None,
        )
        self.assertFalse(self.driver["dialpad"])

    def test_opener_held_push_and_release_selects_once(self):
        server = self.default_start()
        selected = []
        original = self.driver["tap_center"]
        self.driver["tap_center"] = lambda: selected.append(1) or original()
        self.run_steps(
            lambda: self.deliver(server, button(0, True)),
            lambda: self.deliver(server, axis(3, -300)),
            lambda: self.deliver(server, axis(3, 0)),
            lambda: self.deliver(server, axis(1, -300)),
            lambda: self.deliver(server, axis(1, 0)),        # the push selects
            lambda: self.deliver(server, button(0, False)),  # releasing the opener must not select again
            lambda: None,
        )
        self.assertEqual(selected, [])
        self.assertTrue(self.driver["center_activated"])

    def test_push_that_opens_a_group_keeps_the_opener(self):
        server = self.start_server()
        self.load(self.config(server), app="org.gnome.Nautilus.desktop")
        self.run_steps(
            lambda: self.deliver(server, button(0, True)),
            lambda: self.deliver(server, axis(3, 300)),  # down and right: sector 1, the group
            lambda: self.deliver(server, axis(5, 300)),
            lambda: self.deliver(server, axis(3, 0)),
            lambda: self.deliver(server, axis(5, 0)),
            lambda: None,
            lambda: self.assertEqual(self.driver["title"], "Display"),
            lambda: self.deliver(server, axis(1, -300)),
            lambda: self.deliver(server, axis(1, 0)),
            lambda: None,
            lambda: (
                self.assertEqual(self.driver["submenu"], "Display"),
                self.assertEqual(self.source().buttons.opener, 0),
            ),
        )

    # 4. modifier release order
    def test_lost_connection_releases_modifiers_through_the_action_queue(self):
        self.autorun = True
        server = self.default_start()
        self.run_steps(
            lambda: self.deliver(server, button(25, True)),
            lambda: None,
            lambda: server.drop_client(),
            lambda: None,
            lambda: None,
        )
        self.run_queue()
        self.assertEqual(self.state.held, [(("KEY_LEFTCTRL",), True), (("KEY_LEFTCTRL",), False)])
        self.assertEqual(self.driver["spacemouse_held_keys"], set())

    def test_scroll_steps_do_not_let_go_of_a_key_a_button_holds(self):
        with open(os.path.join(ROOT, "dialpad.py"), encoding="utf-8") as source:
            tree = ast.parse(source.read())
        names = {"release_scroll_modifiers", "hold_scroll_modifiers"}
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        released = []
        key, other = EV_KEY.KEY_LEFTCTRL, EV_KEY.KEY_LEFTSHIFT
        namespace = {
            "scroll_modifier_lock": _threading.Lock(), "scroll_modifiers_held": [], "scroll_modifier_generation": 0,
            "SCROLL_MODIFIER_RELEASE_SECONDS": 60, "threading": _threading,
            "set_keys_held": lambda keys, pressed: released.append((tuple(k.name for k in keys), pressed)),
            "spacemouse_held_keys": {key},
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "dialpad.py", "exec"), namespace)
        namespace["hold_scroll_modifiers"]([key, other])
        namespace["release_scroll_modifiers"]()
        self.assertEqual(released[-1], (("KEY_LEFTSHIFT",), False))  # the button's Ctrl stays down
        namespace["hold_scroll_modifiers"]([key])
        namespace["hold_scroll_modifiers"]([other])  # changes the held set: Ctrl is not released
        self.assertNotIn((("KEY_LEFTCTRL",), False), released)
        namespace["spacemouse_held_keys"].clear()
        namespace["release_scroll_modifiers"]()
        namespace["hold_scroll_modifiers"]([key])
        namespace["release_scroll_modifiers"]()
        self.assertIn((("KEY_LEFTCTRL",), False), released)

    # 5. a stale slice
    def test_tilting_to_the_same_sector_after_a_twist_or_the_dead_zone_highlights_again(self):
        server = self.default_start()
        seen = []
        note = lambda tag: (lambda: seen.append((tag, self.driver["title"])))
        self.run_steps(
            lambda: self.deliver(server, button(0, True)),
            lambda: self.deliver(server, button(0, False)),
            lambda: self.deliver(server, axis(3, -300)), lambda: None, note("tilt up"),
            lambda: self.deliver(server, axis(3, 0)), lambda: None,
            lambda: self.deliver(server, axis(3, -300)), lambda: None, note("up again"),  # after the dead zone
            lambda: self.deliver(server, axis(3, 0)),
            lambda: self.deliver(server, axis(4, -400)),
            *self.pump(14),
            lambda: self.deliver(server, axis(4, 0)), lambda: None, note("after twist"),
            lambda: self.deliver(server, axis(3, -300)), lambda: None, lambda: None, note("tilt up after twist"),
            lambda: self.deliver(server, axis(3, 0)),
        )
        self.assertEqual(dict(seen)["tilt up"], "Zoom")
        self.assertEqual(dict(seen)["up again"], "Zoom")
        self.assertNotEqual(dict(seen)["after twist"], "Zoom")
        self.assertEqual(dict(seen)["tilt up after twist"], "Zoom")

    # 6. a waiting opener counts as a gesture
    def test_opener_held_for_release_to_select_blocks_config_changes(self):
        server = self.default_start()
        gestures = []
        self.driver["process_pending_driver_updates"] = lambda gesture_idle=True: gestures.append(gesture_idle)
        self.run_steps(
            lambda: self.deliver(server, button(0, True)),
            lambda: self.deliver(server, axis(3, -300)),
            lambda: self.deliver(server, button(0, False)),
            lambda: None,
            lambda: self.deliver(server, button(25, True)),  # a held Ctrl is no gesture
            lambda: None,
            lambda: self.deliver(server, button(25, False)),
        )
        self.assertFalse(gestures[1])          # the opener is down
        self.assertTrue(gestures[3])           # let go
        self.assertTrue(gestures[5])           # only a modifier is held
        self.assertTrue(gestures[-1])

    # 7. leftovers and the hidden-button fallback
    def restore_namespace(self, server, socket_path=None):
        with open(os.path.join(ROOT, "dialpad.py"), encoding="utf-8") as source:
            tree = ast.parse(source.read())
        names = {"restore_spacenav_leftovers", "restore_spacenav_at_start"}
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        noop = staticmethod(lambda *a, **k: None)
        path = socket_path or server.path
        namespace = {
            "SPACENAV_RESTORE_FILE": self.restore_path, "RestoreFile": proto.RestoreFile,
            "SpacenavClient": proto.SpacenavClient, "os": os,
            "socket_candidates": lambda configured, _environ, _rc: [configured],
            "gui_config": types.SimpleNamespace(spacemouse=types.SimpleNamespace(socket=path)),
            "log": type("Log", (), {"info": noop, "debug": noop, "warning": noop})(),
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "dialpad.py", "exec"), namespace)
        return namespace

    def test_driver_start_gives_back_what_a_killed_run_left_even_with_the_mode_off(self):
        server = self.start_server()
        server.sensitivity = 0.0
        server.button_map = {0: 63}
        proto.RestoreFile(self.restore_path).save({"sensitivity": 0.7, "bnmap": {"0": 0}})
        self.restore_namespace(server)["restore_spacenav_at_start"]()
        self.assertAlmostEqual(server.sensitivity, 0.7, places=5)
        self.assertEqual(server.button_map[0], 0)
        self.assertIsNone(proto.RestoreFile(self.restore_path).load())

    def test_driver_start_keeps_the_file_when_spacenavd_is_not_there(self):
        server = self.start_server()
        proto.RestoreFile(self.restore_path).save({"sensitivity": 0.7})
        self.restore_namespace(server, os.path.join(self.directory.name, "nothing.sock"))["restore_spacenav_at_start"]()
        self.assertEqual(proto.RestoreFile(self.restore_path).load(), {"sensitivity": 0.7})

    def test_driver_start_without_a_file_does_not_connect(self):
        server = self.start_server()
        self.restore_namespace(server)["restore_spacenav_at_start"]()
        self.assertEqual(server.connections, 0)

    def test_a_button_already_hidden_by_an_earlier_run_is_given_back_as_itself(self):
        server = self.start_server()
        server.button_map = {0: 63, 17: 63, 22: 5}
        self.load(self.config(server, hide_buttons=True))
        self.run_steps(*self.pump(2))
        self.assertEqual(self.source().hidden[0], 0)
        self.assertEqual(self.source().hidden[17], 17)
        self.assertEqual(self.source().hidden[22], 5)
        self.source().close()
        self.assertEqual((server.button_map[0], server.button_map[17], server.button_map[22]), (0, 17, 5))


class HoldKeysLoopTest(SpaceMouseTestCase):
    """key_hold, none and hide_buttons for the active profile."""

    def start(self, global_buttons=None, krita=None, files=None, **kwargs):
        server = self.start_server()
        config = self.config(server, buttons=global_buttons or {}, **kwargs)
        config.profiles[1].spacemouse_buttons = krita or {}
        config.profiles[2].spacemouse_buttons = files or {}
        self.load(config)
        return server

    def test_key_hold_press_keeps_the_keys_down_while_the_button_is_down(self):
        self.autorun = True
        server = self.start({"3": SpaceMouseButton(press=SpaceMouseAction("key_hold", keys=["KEY_Z"])),
                             "4": SpaceMouseButton(press=SpaceMouseAction("key_hold", keys=["KEY_LEFTCTRL", "KEY_TAB"]))})
        self.run_steps(
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(4, True)),
            lambda: None,
            lambda: self.deliver(server, button(4, False)),
            lambda: self.deliver(server, button(3, False)),
            lambda: None,
        )
        self.run_queue()
        self.assertEqual(self.state.held, [
            (("KEY_Z",), True), (("KEY_LEFTCTRL", "KEY_TAB"), True),
            (("KEY_LEFTCTRL", "KEY_TAB"), False), (("KEY_Z",), False),
        ])
        self.assertEqual(self.driver["spacemouse_held_keys"], set())

    def test_key_hold_as_a_hold_action_goes_down_after_the_hold_time(self):
        self.autorun = True
        server = self.start({"3": SpaceMouseButton(press=SpaceMouseAction("macro", keys=["KEY_F5"]),
                                                   hold=SpaceMouseAction("key_hold", keys=["KEY_Z"]))})
        self.run_steps(
            lambda: self.deliver(server, button(3, True)),
            *self.pump(12),  # past 400 ms
            lambda: self.deliver(server, button(3, False)),
            lambda: None,
        )
        self.run_queue()
        self.assertEqual(self.state.held, [(("KEY_Z",), True), (("KEY_Z",), False)])
        self.assertEqual(self.state.macros, [])  # the hold replaced the press

    def test_a_key_two_buttons_hold_goes_up_with_the_last(self):
        self.autorun = True
        server = self.start({"3": SpaceMouseButton(press=SpaceMouseAction("key_hold", keys=["KEY_Z"])),
                             "4": SpaceMouseButton(press=SpaceMouseAction("key_hold", keys=["KEY_Z"]))})
        self.run_steps(
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(4, True)),
            lambda: self.deliver(server, button(3, False)),
            lambda: None,
            lambda: self.deliver(server, button(4, False)),
            lambda: None,
        )
        self.run_queue()
        self.assertEqual(self.state.held, [(("KEY_Z",), True), (("KEY_Z",), False)])

    def test_lost_connection_lets_go_of_key_hold_keys_through_the_queue(self):
        self.autorun = True
        server = self.start({"3": SpaceMouseButton(press=SpaceMouseAction("key_hold", keys=["KEY_Z"]))})
        self.run_steps(
            lambda: self.deliver(server, button(3, True)),
            lambda: None,
            lambda: server.drop_client(),
            lambda: None,
            lambda: None,
        )
        self.run_queue()
        self.assertEqual(self.state.held, [(("KEY_Z",), True), (("KEY_Z",), False)])

    def test_the_keys_are_known_to_the_virtual_keyboard(self):
        from config.schema import spacemouse_hold_keys
        server = self.start({"3": SpaceMouseButton(press=SpaceMouseAction("key_hold", keys=["KEY_Z", "KEY_TAB"]))})
        self.assertEqual(spacemouse_hold_keys(self.driver["gui_config"]), {"KEY_Z", "KEY_TAB"})

    def test_a_profile_none_beats_the_global_binding(self):
        server = self.start(krita={"22": SpaceMouseButton(press=SpaceMouseAction("none"))})
        self.run_steps(
            lambda: self.deliver(server, button(17, True)),   # enters the first function
            lambda: self.deliver(server, button(17, False)),
            lambda: self.deliver(server, button(22, True)),   # Esc is none here: no "back"
            lambda: self.deliver(server, button(22, False)),
            lambda: None,
        )
        self.assertTrue(self.driver["center_activated"])
        self.assertEqual(self.driver["spacemouse_binding"](22).press.type, "none")

    def test_hide_buttons_follows_the_profile_and_restores_everything_on_exit(self):
        files_bind = {"4": SpaceMouseButton(press=SpaceMouseAction("macro", keys=["KEY_F5"]))}
        krita_bind = {"3": SpaceMouseButton(press=SpaceMouseAction("macro", keys=["KEY_F6"])),
                      "22": SpaceMouseButton(press=SpaceMouseAction("none")),
                      "17": SpaceMouseButton(press=SpaceMouseAction("none"), hold=SpaceMouseAction("none"))}
        server = self.start(krita=krita_bind, files=files_bind, hide_buttons=True)
        default_bound = {0, 17, 18, 19, 20, 21, 22, 23, 24, 25}
        hidden = lambda: {b for b, m in server.button_map.items() if m == 63}
        self.run_steps(
            lambda: None,
            lambda: self.assertEqual(hidden(), (default_bound - {17, 22}) | {3}),  # Krita: none is not hidden
            lambda: self.driver["window_was_changed"]("org.gnome.Nautilus.desktop"),
            lambda: None,
            lambda: self.assertEqual(hidden(), default_bound | {4}),
            lambda: self.driver["window_was_changed"]("org.kde.krita.desktop"),
            lambda: None,
            lambda: self.assertEqual(hidden(), (default_bound - {17, 22}) | {3}),
            lambda: self.assertEqual(server.button_map.get(4, 4), 4),  # Nautilus' button given back
            lambda: self.assertEqual(proto.RestoreFile(self.restore_path).load()["bnmap"]["3"], 3),
        )
        self.source().close()
        self.assertEqual(hidden(), set())
        self.assertIsNone(proto.RestoreFile(self.restore_path).load())

    def test_hidden_buttons_still_arrive_as_raw_buttons(self):
        server = self.start(hide_buttons=True)
        self.run_steps(
            lambda: None,
            lambda: self.assertEqual(server.button_map.get(0), 63),
            lambda: self.deliver(server, button(0, True)),  # the raw event is what arrives
            lambda: None,
        )
        self.assertTrue(self.driver["dialpad"])

    def test_hide_buttons_off_hides_nothing_and_reload_follows_the_setting(self):
        server = self.start()
        self.run_steps(
            lambda: None,
            lambda: self.assertEqual(server.button_map, {}),
            lambda: self.driver["gui_config"].spacemouse.__setattr__("hide_buttons", True),
            lambda: self.driver.update(gui_config=__import__("copy").copy(self.driver["gui_config"])),
            lambda: None,
            lambda: self.assertIn(0, server.button_map),
        )


class SecondReviewTest(SpaceMouseTestCase):
    """Regression tests for the second review of stage 1."""

    def start(self, **kwargs):
        server = self.start_server()
        self.load(self.config(server, **kwargs))
        return server

    # 1. holding Menu, pressing Ctrl, tilting, releasing both: selects
    def test_menu_held_with_ctrl_pressed_still_selects_on_release(self):
        server = self.start()
        self.run_steps(
            lambda: self.deliver(server, button(0, True)),
            lambda: self.deliver(server, button(25, True)),   # Ctrl
            lambda: self.deliver(server, axis(3, -300)),      # tilt: highlights Zoom
            lambda: self.deliver(server, axis(3, 0)),
            lambda: self.deliver(server, button(25, False)),
            lambda: self.deliver(server, button(0, False)),
            lambda: None,
        )
        self.assertTrue(self.driver["center_activated"])
        self.assertEqual(self.driver["title"], "Zoom")

    # 2. press and release jobs that run late, twice or in the wrong order
    def test_key_jobs_running_out_of_order_never_leave_a_key_stuck(self):
        server = self.start(buttons={"3": SpaceMouseButton(press=SpaceMouseAction("key_hold", keys=["KEY_Z"]))})
        self.run_steps(
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(3, False)),
            lambda: None,
        )
        jobs = list(self.queued)
        self.assertGreaterEqual(len(jobs), 2)
        self.queued.clear()
        for job in reversed(jobs):  # the overflow thread of a full queue ran the release first
            job()
        for job in jobs:
            job()
        pressed = 0
        for keys, down in self.state.held:
            pressed += len(keys) if down else -len(keys)
        self.assertEqual(pressed, 0)
        self.assertEqual(self.driver["spacemouse_applied_keys"], [])

    def test_a_key_held_by_a_scroll_step_is_not_released_by_a_button(self):
        server = self.start()
        self.driver["scroll_modifiers_held"].append(EV_KEY.KEY_LEFTCTRL)
        self.autorun = True
        self.state.autorun = True
        self.run_steps(
            lambda: self.deliver(server, button(25, True)),
            lambda: None,
            lambda: self.deliver(server, button(25, False)),
            lambda: None,
        )
        self.assertNotIn((("KEY_LEFTCTRL",), False), self.state.held)
        self.assertEqual(self.driver["spacemouse_applied_keys"], [])

    # 3. the restore file knows the originals before the mapping changes
    def test_the_original_mapping_is_saved_before_a_button_is_hidden(self):
        server = self.start_server()
        server.button_map = {0: 5}
        seen = []
        server.on_set_bnmap = lambda button_, mapped: seen.append(
            (button_, mapped, (proto.RestoreFile(self.restore_path).load() or {}).get("bnmap", {}).get(str(button_))))
        self.load(self.config(server, hide_buttons=True))
        self.run_steps(*self.pump(2))
        hide = [entry for entry in seen if entry[0] == 0 and entry[1] == 63]
        self.assertEqual(hide, [(0, 63, 5)])

    def test_buttons_that_cannot_be_given_back_stay_in_the_restore_file(self):
        server = self.start_server()
        self.load(self.config(server, hide_buttons=True))
        self.run_steps(*self.pump(2))
        server.refuse_bnmap_values = set(range(0, 63))  # only 63 is accepted now
        self.source().close()
        saved = proto.RestoreFile(self.restore_path).load()
        self.assertTrue(saved and saved["bnmap"]["0"] == 0 and "17" in saved["bnmap"])

    # 8. a button that is down keeps its mapping until it is up
    def test_mapping_of_a_button_that_is_down_waits_for_its_release(self):
        server = self.start_server()
        config = self.config(server, hide_buttons=True)
        config.profiles[2].spacemouse_buttons = {"4": SpaceMouseButton(press=SpaceMouseAction("macro", keys=["KEY_F5"]))}
        self.load(config, app="org.gnome.Nautilus.desktop")
        self.run_steps(
            lambda: None,
            lambda: self.assertEqual(server.button_map.get(4), 63),
            lambda: self.deliver(server, button(4, True)),
            lambda: self.driver["window_was_changed"]("org.kde.krita.desktop"),  # button 4 is unbound there
            lambda: None,
            lambda: self.assertEqual(server.button_map.get(4), 63),   # still down: kept
            lambda: self.deliver(server, button(4, False)),
            lambda: None,
            lambda: None,
            lambda: self.assertEqual(server.button_map.get(4, 4), 4),  # given back after the release
        )

    def test_a_button_pressed_in_the_new_profile_waits_before_it_is_hidden(self):
        server = self.start_server()
        config = self.config(server, hide_buttons=True)
        config.profiles[1].spacemouse_buttons = {"4": SpaceMouseButton(press=SpaceMouseAction("macro", keys=["KEY_F6"]))}
        self.load(config, app="org.gnome.Nautilus.desktop")
        self.run_steps(
            lambda: None,
            lambda: self.assertNotEqual(server.button_map.get(4), 63),
            lambda: self.deliver(server, button(4, True)),
            lambda: self.driver["window_was_changed"]("org.kde.krita.desktop"),
            lambda: None,
            lambda: self.assertNotEqual(server.button_map.get(4), 63),
            lambda: self.deliver(server, button(4, False)),
            lambda: None,
            lambda: None,
            lambda: self.assertEqual(server.button_map.get(4), 63),
        )


if __name__ == "__main__":
    unittest.main()
