"""Configuration reloads, the virtual device and held keyboard keys, without hardware.

Like test_driver_state.py, the functions are taken from ``dialpad.py``'s
syntax tree (the module opens devices at import time) and run against fakes.
"""

import ast
import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config.legacy_adapter import shortcuts_from_config  # noqa: E402
from config.schema import (  # noqa: E402
    DialPadConfig,
    MacroStep,
    ModifierActions,
    Profile,
    Segment,
    StepType,
    _default_config,
    _encoder,
    config_from_text,
)

QUIET_LOG = SimpleNamespace(**{level: (lambda *a, **k: None) for level in ("info", "debug", "warning", "error", "exception")})


def driver_functions(names, namespace):
    """Run the named top-level functions of dialpad.py in namespace."""
    with open(os.path.join(ROOT, "dialpad.py"), encoding="utf-8") as source:
        tree = ast.parse(source.read())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    missing = set(names) - {node.name for node in functions}
    assert not missing, f"dialpad.py lost {missing}"
    namespace.setdefault("log", QUIET_LOG)
    exec(compile(ast.Module(body=functions, type_ignores=[]), "dialpad.py", "exec"), namespace)
    return namespace


class EventCode:
    """Stands in for a libevdev EventCode (hashable, with a name)."""

    def __init__(self, name):
        self.name = name


def key(name):
    return EventCode(name)


def volume_with_ctrl():
    return Segment(
        title="Volume",
        macro_cw=[MacroStep(type=StepType.VOLUME, direction="up", value=1, title="Louder")],
        macro_center=[MacroStep(type=StepType.MEDIA, keys=["KEY_MUTE"], title="Mute")],
        with_modifier={"Ctrl": ModifierActions(
            macro_center=[MacroStep(type=StepType.SHELL, script="pavucontrol", title="Mixer")],
        )},
    )


class HeldKeyTest(unittest.TestCase):
    """A function runs its variant while Ctrl, Shift or Alt is held."""

    def setUp(self):
        self.ran = []
        self.driver = driver_functions(
            {"modifier_names", "shortcuts_for_modifiers", "emulate_shortcuts", "function_has_center_action",
             "function_entry", "group_settings"},
            {
                "StepType": StepType,
                # The menu, no group's list open.
                "submenu": None,
                "multi_app_mode": True,
                "execute_config_macro": lambda steps, _multiplier: self.ran.append(steps[0].title),
                "send_key_event": lambda code, value=None: self.ran.append(code),
                "execute_command": self.ran.append,
                "active_modifiers": set(),
            },
        )
        config = DialPadConfig(profiles=[Profile(name="Default", segments=[volume_with_ctrl()])])
        self.volume = shortcuts_from_config(config)["none"]["Volume"]

    def tap(self, *held):
        self.driver["emulate_shortcuts"](self.volume, "center", 0, {key(name) for name in held})

    def test_held_key_picks_the_variant(self):
        self.tap()
        self.tap("KEY_LEFTCTRL")
        self.tap("KEY_RIGHTCTRL")
        self.assertEqual(self.ran, ["Mute", "Mixer", "Mixer"])

    def test_without_a_variant_the_usual_action_runs(self):
        self.tap("KEY_LEFTSHIFT")
        self.driver["emulate_shortcuts"](self.volume, "clockwise", 1, {key("KEY_LEFTCTRL")})
        self.assertEqual(self.ran, ["Mute", "Louder"])

    def test_layout_files_name_one_evdev_code(self):
        # Python layouts give "modifier" as a single libevdev code.
        layout = {"center": [
            {"key": "KEY_F14", "modifier": key("KEY_LEFTSHIFT")},
            {"key": "KEY_F13"},
        ]}
        self.driver["emulate_shortcuts"](layout, "center", 0, {key("KEY_LEFTSHIFT")})
        self.driver["emulate_shortcuts"](layout, "center", 0, set())
        self.assertEqual(self.ran, ["KEY_F14", "KEY_F13"])

    def test_tap_with_only_a_variant_goes_back_to_the_menu(self):
        self.driver["app_specific_shortcuts"] = {"Mixer": {"center": [self.volume["center"][1]]}}
        self.assertFalse(self.driver["function_has_center_action"]("Mixer"))
        self.driver["active_modifiers"] = {key("KEY_LEFTCTRL")}
        self.assertTrue(self.driver["function_has_center_action"]("Mixer"))


class ReloadTest(unittest.TestCase):
    """ReloadConfig checks the file at once and applies it between gestures."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.file = Path(directory.name) / "config.json"
        self.focused = []
        self.broken_settings = None
        self.driver = driver_functions(
            {
                "on_dbus_reload_config", "process_pending_driver_updates", "read_gui_config",
                "set_config_status", "load_gui_shortcuts", "apply_gui_config",
            },
            {
                "hashlib": hashlib,
                "driver_state_lock": threading.RLock(),
                "pending_active_application": None,
                "pending_config_reload": False,
                "pending_gui_config": None,
                "pending_window_title": None,
                "pending_window_title_set": False,
                "pushed_window_title": None,
                "pending_context_refresh": False,
                "pending_calibration": None,
                "pending_controls": [],
                "apply_control": lambda command: None,
                "publish_driver_state": lambda: None,
                "config_status": {"revision": "", "error": "", "pending": False},
                "CONFIG_APP_AVAILABLE": True,
                "CONFIG_FILE": self.file,
                "config_from_text": config_from_text,
                "shortcuts_from_config": shortcuts_from_config,
                "legacy_app_shortcuts": {"none": {"center": []}},
                "gui_config": None,
                "app_shortcuts": None,
                "window_binary": "org.gnome.Nautilus.desktop",
                "apply_gui_settings": self.apply_gui_settings,
                "enable_config_macro_capabilities": lambda reset_udev=False: None,
                "window_was_changed": lambda app, force=False: self.focused.append((app, force)),
            },
        )

    def apply_gui_settings(self):
        if self.driver["gui_config"] is not None and self.driver["gui_config"] is self.broken_settings:
            raise ValueError("geometry out of range")

    def write(self, profile_name):
        config = _default_config()
        config.profiles[0].name = profile_name
        text = json.dumps(_encoder(config))
        self.file.write_text(text)
        return hashlib.sha256(text.encode()).hexdigest()[:12]

    def running(self):
        config = self.driver["gui_config"]
        return config.profiles[0].name if config else None

    def test_waits_for_the_finger_to_lift(self):
        revision = self.write("New")
        self.assertTrue(self.driver["on_dbus_reload_config"]())
        self.assertTrue(self.driver["config_status"]["pending"])
        self.driver["process_pending_driver_updates"](gesture_idle=False)
        self.assertIsNone(self.running())
        self.assertEqual(self.focused, [])

        self.driver["process_pending_driver_updates"]()
        self.assertEqual(self.running(), "New")
        self.assertEqual(self.driver["config_status"], {"revision": revision, "error": "", "pending": False})
        self.assertEqual(self.focused, [("org.gnome.Nautilus.desktop", True)])

    def test_focus_change_during_a_gesture_waits_too(self):
        self.driver["pending_active_application"] = "org.kde.krita.desktop"
        self.driver["process_pending_driver_updates"](gesture_idle=False)
        self.assertEqual(self.focused, [])
        self.driver["process_pending_driver_updates"]()
        self.assertEqual(self.focused, [("org.kde.krita.desktop", False)])

    def test_unusable_file_keeps_the_running_configuration(self):
        revision = self.write("Good")
        self.driver["load_gui_shortcuts"]()
        self.file.write_text('{"version": 2,')
        self.assertFalse(self.driver["on_dbus_reload_config"]())
        self.driver["process_pending_driver_updates"]()
        self.assertEqual(self.running(), "Good")
        status = self.driver["config_status"]
        self.assertEqual(status["revision"], revision)
        self.assertIn("not valid JSON", status["error"])

        # The next good file clears the error.
        self.write("Fixed")
        self.assertTrue(self.driver["on_dbus_reload_config"]())
        self.assertEqual(self.driver["config_status"]["error"], "")

    def test_configuration_that_fails_to_apply_is_rolled_back(self):
        revision = self.write("Good")
        self.driver["load_gui_shortcuts"]()
        good_shortcuts = self.driver["app_shortcuts"]
        self.write("Bad")
        self.assertTrue(self.driver["on_dbus_reload_config"]())
        self.broken_settings = self.driver["pending_gui_config"][0]
        self.driver["process_pending_driver_updates"]()
        self.assertEqual(self.running(), "Good")
        self.assertIs(self.driver["app_shortcuts"], good_shortcuts)
        self.assertEqual(self.driver["config_status"],
                         {"revision": revision, "error": "geometry out of range", "pending": False})
        self.assertEqual(self.focused, [])

    def test_unusable_file_at_start_uses_the_layout(self):
        self.file.write_text("[]")
        self.assertFalse(self.driver["load_gui_shortcuts"]())
        self.assertEqual(self.driver["app_shortcuts"], {"none": {"center": []}})
        self.assertIn("no JSON object", self.driver["config_status"]["error"])


class FakeUinput:
    def __init__(self):
        self._uinput_device = "handle"
        self.destroyed = []

    def _uinput_destroy(self, handle):
        self.destroyed.append(handle)


class FakeDevice:
    def __init__(self, name):
        self.devnode = self.syspath = name
        self._uinput = FakeUinput()
        self.events = []

    def send_events(self, events):
        if self._uinput is None:
            raise RuntimeError("sent to a removed device")
        self.events.extend(events)


class KeyCodes:
    """EV_KEY: every KEY_ name is a code."""

    def __getattr__(self, name):
        if not name.startswith("KEY_"):
            raise AttributeError(name)
        return name[4:]


class VirtualDeviceTest(unittest.TestCase):
    """New keys replace the virtual device without leaving keys or devices behind."""

    def setUp(self):
        self.old, self.new = FakeDevice("old"), FakeDevice("new")
        self.driver = driver_functions(
            {"set_keys_held", "reset_udev_device", "close_virtual_device", "enable_config_macro_capabilities"},
            {
                "dev": SimpleNamespace(create_uinput_device=lambda: self.new),
                "udev": self.old,
                "sleep": lambda _seconds: None,
                "uinput_lock": threading.RLock(),
                "uinput_rebuild_lock": threading.Lock(),
                "scroll_modifier_lock": threading.Lock(),
                "scroll_modifiers_held": ["KEY_LEFTCTRL"],
                "spacemouse_applied_keys": [],
                "InputEvent": lambda code, value: (code, value),
                "EV_SYN": SimpleNamespace(SYN_REPORT="SYN"),
                "EV_KEY": KeyCodes(),
                "EV_REL": SimpleNamespace(),
                "MacroStep": MacroStep,
                "StepType": StepType,
                "enabled_evdev_keys": [],
                "enable_key": lambda *_args, **_kwargs: None,
                "modifiers": set(),
            },
        )

    def test_held_zoom_key_is_released_on_the_old_device(self):
        self.driver["reset_udev_device"]()
        self.assertEqual(self.old.events, [("KEY_LEFTCTRL", 0), ("SYN", 0)])
        self.assertIs(self.driver["udev"], self.new)
        self.assertEqual(self.driver["scroll_modifiers_held"], [])

    def test_keys_held_by_spacemouse_buttons_go_down_on_the_new_device(self):
        self.driver["spacemouse_applied_keys"].extend(["KEY_Z"])
        self.driver["reset_udev_device"]()
        self.assertIn(("KEY_Z", 1), self.new.events)
        self.assertNotIn(("KEY_Z", 1), self.old.events)

    def test_old_device_is_removed_at_once(self):
        uinput = self.old._uinput
        self.driver["reset_udev_device"]()
        self.assertEqual(uinput.destroyed, ["handle"])
        self.assertIsNone(self.old._uinput)
        self.driver["set_keys_held"](["KEY_A"], True)
        self.assertEqual(self.new.events, [("KEY_A", 1), ("SYN", 0)])

    def test_keys_of_variants_are_followed_on_the_keyboard(self):
        config = DialPadConfig(profiles=[Profile(name="Default", segments=[volume_with_ctrl()])])
        self.driver["app_shortcuts"] = shortcuts_from_config(config)
        self.driver["enable_config_macro_capabilities"]()
        self.assertEqual(self.driver["modifiers"], {"LEFTCTRL", "RIGHTCTRL"})


if __name__ == "__main__":
    unittest.main()
