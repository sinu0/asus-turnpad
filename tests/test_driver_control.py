"""The D-Bus Control commands and the DriverState snapshot of the driver, without hardware.

Like test_driver_state.py the state functions are extracted from
``dialpad.py``; the pieces around them (the toggle, the config, the input
sources) are small fakes.
"""

import json
import os
import sys
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config.legacy_adapter import shortcuts_from_config  # noqa: E402
from config.presets import tracks, vertical_scroll, volume, zoom_wheel  # noqa: E402
from config.schema import (  # noqa: E402
    MODIFIER_KEYS, DialPadConfig, Profile, Segment, SpaceMouseAction, SpaceMouseButton, SpaceMouseConfig,
    merge_spacemouse_buttons,
    spacemouse_buttons_for,
)
from spacemouse_dial import LayerState, MuteLease  # noqa: E402
from tests.test_driver_state import load_driver_state  # noqa: E402

try:
    from dialpad_dbus import parse_control
except ImportError:  # PyGObject missing
    parse_control = None


class Key:
    """An evdev key as the driver holds it: only the name matters here."""

    def __init__(self, name):
        self.name = name


class Source:
    name = "spacemouse"
    connected = True
    device_name = "SpacePilot PRO"

    def __init__(self):
        self.layers = LayerState()
        self.motion_shown = {"sensitivity": 1.0, "rotation": True, "translation": True}


@unittest.skipIf(parse_control is None, "PyGObject is not installed")
class DriverCase(unittest.TestCase):
    """The extracted driver with Krita's profile in focus and the dial off."""

    def setUp(self):
        buttons = {
            "0": SpaceMouseButton(press=SpaceMouseAction("dial")),
            "1": SpaceMouseButton(press=SpaceMouseAction("select")),
            "2": SpaceMouseButton(press=SpaceMouseAction("modifier", modifier="Ctrl")),
            "3": SpaceMouseButton(press=SpaceMouseAction("key_hold", keys=["KEY_Z"])),
            "4": SpaceMouseButton(press=SpaceMouseAction("none")),
            "5": SpaceMouseButton(hold=SpaceMouseAction("dial")),
            "6": SpaceMouseButton(press=SpaceMouseAction("function", slot=1)),
            "7": SpaceMouseButton(press=SpaceMouseAction("back")),
        }
        self.config = DialPadConfig(
            spacemouse=SpaceMouseConfig(buttons=buttons),
            profiles=[
                Profile(name="Default", segments=[volume(), vertical_scroll(), tracks()]),
                Profile(
                    name="Krita", app_id="org.kde.krita.desktop",
                    segments=[zoom_wheel(), volume(), Segment(title="Brush", items=[volume(), tracks()])],
                ),
            ],
        )
        self.driver, self.sent, self.queued = load_driver_state(shortcuts_from_config(self.config))
        self.clock = [5000.0]
        self.toggles = []
        self.states = []
        applied = []
        self.applied = applied

        def toggle(current):
            self.toggles.append(current)
            self.driver["dialpad"] = not current

        class Service:
            @staticmethod
            def emit_driver_state(state):
                # A snapshot is computed and handed over under one lock.
                states.append((state, publish_lock.locked()))

        states = self.states
        publish_lock = threading.Lock()
        self.driver.update(
            dialpad=False,
            toggle_top_right_icon=toggle,
            gui_config=self.config,
            spacemouse_buttons_for=spacemouse_buttons_for,
            merge_spacemouse_buttons=merge_spacemouse_buttons,
            SCHEMA_MODIFIER_KEYS=MODIFIER_KEYS,
            parse_control=parse_control,
            pending_controls=[],
            spacemouse_mute=MuteLease(),
            time=lambda: self.clock[0],
            PENDING_CONTROLS_MAX=4,
            driver_state_lock=threading.RLock(),
            driver_state_publish_lock=publish_lock,
            input_sources=[],
            spacemouse_applied_keys=applied,
            config_status={"revision": "abc123", "error": "", "pending": False},
            pending_active_application=None,
            pending_config_reload=False,
            pending_calibration=None,
            pending_gui_config=None,
            pending_window_title=None,
            pending_window_title_set=False,
            pushed_window_title=None,
            pending_context_refresh=False,
            window_binary=None,
            dbus_service=Service,
            log=type("Log", (), {
                "info": staticmethod(lambda *a, **k: None),
                "debug": staticmethod(lambda *a, **k: None),
                "exception": staticmethod(lambda *a, **k: None),
            })(),
        )
        self.driver["window_was_changed"]("org.kde.krita.desktop")

    def control(self, command):
        return self.driver["on_dbus_control"](command)

    def process(self, **kwargs):
        self.driver["process_pending_driver_updates"](**kwargs)


class ControlTest(DriverCase):

    def test_understood_commands_are_queued_not_run(self):
        for command in ("toggle", "rotate:1", "function:0", "group", "button:1", "function=Zoom"):
            self.assertTrue(self.control(command), command)
        self.assertEqual(len(self.driver["pending_controls"]), 4)  # the oldest were dropped
        self.assertEqual(self.toggles, [])

    def test_mute_is_a_lease_granted_at_once_not_a_queued_command(self):
        lease = self.driver["spacemouse_mute"]
        self.assertTrue(self.control("mute:3000"))
        self.assertEqual(self.driver["pending_controls"], [])  # no waiting for a gesture or the loop
        self.assertTrue(lease.active(self.clock[0]))
        self.assertTrue(lease.active(self.clock[0] + 2.9))
        self.assertFalse(lease.active(self.clock[0] + 3.1))
        self.assertTrue(self.control("mute:3000"))
        self.assertTrue(self.control("mute:0"))
        self.assertFalse(lease.active(self.clock[0]))
        self.assertEqual(self.toggles, [])

    def test_mute_out_of_range_is_refused_and_changes_nothing(self):
        lease = self.driver["spacemouse_mute"]
        for command in ("mute", "mute:5001", "mute:-1", "mute=1000", "mute:x"):
            self.assertFalse(self.control(command), command)
        self.assertFalse(lease.active(self.clock[0]))

    def test_mute_does_not_push_out_the_commands_waiting(self):
        for _ in range(4):
            self.control("rotate:1")
        self.control("mute:1000")
        self.assertEqual(len(self.driver["pending_controls"]), 4)

    def test_unknown_commands_are_refused(self):
        for command in ("", "dance", "rotate:99", "function:-1", None):
            self.assertFalse(self.control(command), command)
        self.assertEqual(self.driver["pending_controls"], [])

    def test_button_needs_a_press_action_a_command_can_run(self):
        self.assertTrue(self.control("button:0"))
        self.assertTrue(self.control("button:6"))
        # Modifiers and held keys are the loop's business, none leaves the button
        # alone, a hold-only button or an unbound one has no press.
        for index in (2, 3, 4, 5, 40):
            self.assertFalse(self.control("button:%d" % index), index)

    def test_commands_wait_for_the_loop_and_for_an_idle_gesture(self):
        self.control("dial-on")
        self.process(gesture_idle=False)
        self.assertEqual(self.toggles, [])
        self.assertEqual(len(self.driver["pending_controls"]), 1)
        self.process()
        self.assertEqual(self.toggles, [False])
        self.assertEqual(self.driver["pending_controls"], [])

    def test_dial_on_off_and_toggle(self):
        self.control("dial-off")
        self.control("dial-on")
        self.control("dial-on")
        self.control("toggle")
        self.control("dial-off")
        self.process()
        # off (already off: nothing), on, on (already on: nothing), toggle off, off (nothing)
        self.assertEqual(self.toggles, [False, True])
        self.assertFalse(self.driver["dialpad"])

    def test_rotate_walks_the_menu_and_needs_the_dial_on(self):
        self.driver["show_initial_function_state"]()
        before = self.driver["title"]
        self.control("rotate:1")
        self.process()
        self.assertEqual(self.driver["title"], before)
        self.driver["dialpad"] = True
        self.control("rotate:2")
        self.process()
        first = self.driver["title"]
        self.control("rotate:-1")
        self.process()
        titles = [entry for entry in self.driver["multi_app_mode_titles"] if entry]
        self.assertEqual((first, self.driver["title"]), (titles[1], titles[0]))

    def test_function_slot_switches_the_dial_on_and_enters_the_function(self):
        self.control("function:1")
        self.process()
        self.assertEqual(self.toggles, [False])
        self.assertTrue(self.driver["center_activated"])
        self.assertEqual(self.driver["title"], "Volume")

    def test_function_by_title_and_back(self):
        self.driver["dialpad"] = True
        self.control("function=Zoom")
        self.process()
        self.assertEqual((self.driver["center_activated"], self.driver["title"]), (True, "Zoom"))
        self.control("back")
        self.process()
        self.assertFalse(self.driver["center_activated"])

    def test_group_opens_the_first_list_or_a_named_one(self):
        self.control("group=Brush")
        self.process()
        self.assertEqual(self.driver["submenu"], "Brush")
        self.assertEqual(self.toggles, [False])
        self.control("back")
        self.control("group")
        self.process()
        self.assertEqual(self.driver["submenu"], "Brush")

    def test_select_taps_the_highlighted_function(self):
        self.driver["dialpad"] = True
        self.driver["show_initial_function_state"]()
        self.driver["title"] = "Zoom"
        self.control("select")
        self.process()
        self.assertTrue(self.driver["center_activated"])

    def test_button_runs_the_press_action(self):
        self.control("button:6")
        self.process()
        self.assertEqual(self.driver["title"], "Volume")
        self.assertTrue(self.driver["center_activated"])

    def test_profile_binding_replaces_the_global_one(self):
        shortcuts = self.driver["app_specific_shortcuts"]
        shortcuts[self.driver["META_KEY"]]["spacemouse_buttons"] = {"1": SpaceMouseButton(press=SpaceMouseAction("none"))}
        self.assertFalse(self.control("button:1"))

    def test_a_failing_command_does_not_stop_the_others(self):
        calls = []

        def broken(_current):
            calls.append("broken")
            raise RuntimeError("no touchpad")

        self.driver["toggle_top_right_icon"] = broken
        self.control("toggle")
        self.control("rotate:1")
        self.process()
        self.assertEqual(calls, ["broken"])
        self.assertEqual(self.driver["pending_controls"], [])


class DriverStateTest(DriverCase):
    def snapshot(self):
        return self.driver["driver_state"]()

    def test_keys_and_idle_values(self):
        self.assertEqual(self.snapshot(), {
            "profile": "Krita", "app": "org.kde.krita.desktop", "dial": False, "mode": "multi", "group": "", "function": "",
            "modifiers": [], "keys_held": [], "spacemouse": False, "spacemouse_device": "",
            "layer": "", "motion": {"sensitivity": 1.0, "rotation": True, "translation": True},
            "config_revision": "abc123",
            "app_link": False, "app_context": {}, "context": "", "suggest": "", "notice": {},
        })
        json.dumps(self.snapshot())

    def test_follows_dial_group_and_function(self):
        self.driver["dialpad"] = True
        self.driver["open_submenu"]("Brush")
        state = self.snapshot()
        self.assertEqual((state["app"], state["dial"], state["group"], state["function"]),
                         ("org.kde.krita.desktop", True, "Brush", ""))
        self.driver["select_multifunction_segment"]()
        state = self.snapshot()
        self.assertEqual((state["group"], state["function"]), ("Brush", "Volume"))

    def test_modifiers_are_named_and_other_keys_listed(self):
        self.driver["spacemouse_applied_keys"].extend([Key("KEY_LEFTCTRL"), Key("KEY_Z"), Key("KEY_RIGHTALT")])
        state = self.snapshot()
        # Both sides of a modifier count only through the key the button holds.
        self.assertEqual(state["modifiers"], ["Ctrl"])
        self.assertEqual(state["keys_held"], ["KEY_Z", "KEY_RIGHTALT"])

    def test_spacemouse_connection(self):
        source = Source()
        self.driver["input_sources"].append(source)
        self.assertEqual(
            (self.snapshot()["spacemouse"], self.snapshot()["spacemouse_device"]), (True, "SpacePilot PRO"),
        )
        source.connected = False
        self.assertFalse(self.snapshot()["spacemouse"])

    def test_layer_and_motion_of_the_spacemouse(self):
        source = Source()
        self.driver["input_sources"].append(source)
        source.layers.activate(7, "Shift layer", "toggle")
        source.motion_shown = {"sensitivity": 1.5, "rotation": False, "translation": True}
        state = self.snapshot()
        self.assertEqual(state["layer"], "Shift layer")
        self.assertEqual(state["motion"], {"sensitivity": 1.5, "rotation": False, "translation": True})
        json.dumps(state)
        source.connected = False
        self.assertEqual(self.snapshot()["layer"], "")

    def test_publish_hands_the_snapshot_to_the_service(self):
        self.driver["publish_driver_state"]()
        self.assertEqual(self.states, [(self.snapshot(), True)])  # handed over under the publish lock
        self.assertFalse(self.driver["driver_state_publish_lock"].locked())
        self.driver["dbus_service"] = None
        self.driver["publish_driver_state"]()
        self.assertEqual(len(self.states), 1)

    def test_an_older_snapshot_is_never_emitted_after_a_newer_one(self):
        computed, release, order = [], threading.Event(), []
        counter = iter(range(10))

        def slow_state():
            number = next(counter)
            computed.append(number)
            if number == 0:
                release.wait(5)  # the first publisher stalls after computing its snapshot
            return {"n": number}

        self.driver["driver_state"] = slow_state
        self.states.clear()
        first = threading.Thread(target=self.driver["publish_driver_state"])
        first.start()
        while not computed:
            time.sleep(0.005)
        second = threading.Thread(target=self.driver["publish_driver_state"])
        second.start()
        time.sleep(0.1)
        self.assertEqual(computed, [0])  # the second one waits for the lock before it computes
        release.set()
        first.join(5)
        second.join(5)
        self.assertEqual([state["n"] for state, _locked in self.states], [0, 1])

    def test_processing_updates_publishes(self):
        self.states.clear()
        self.process()
        self.assertEqual(len(self.states), 1)


if __name__ == "__main__":
    unittest.main()
