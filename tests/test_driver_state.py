"""Menu and function state of the driver, without touchpad hardware.

``dialpad.py`` opens devices at import time, so the pure state functions are
extracted from its syntax tree and executed against recorded side effects.
"""

import ast
import dataclasses
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config.legacy_adapter import META_KEY, shortcuts_from_config  # noqa: E402
from config.presets import brightness, mute, tracks, vertical_scroll, volume, zoom_wheel  # noqa: E402
from config.schema import DialPadConfig, Profile, Segment  # noqa: E402

STATE_FUNCTIONS = {
    "reset_center",
    "profile_meta",
    "single_function_settings",
    "profile_float",
    "function_settings",
    "menu_colors",
    "overlay_look",
    "send_details",
    "send_function_details",
    "show_multifunction_menu",
    "group_settings",
    "is_group",
    "submenu_titles",
    "function_entry",
    "show_submenu",
    "open_submenu",
    "move_submenu_highlight",
    "leave_function",
    "enter_function",
    "show_single_function",
    "remembered_function",
    "show_initial_function_state",
    "return_to_multifunction_menu",
    "select_multifunction_segment",
    "function_has_center_action",
    "release_center",
    "show_menu_after_gesture",
    "queue_rotation_action",
    "rotate_selected_function",
    "rotate_menu_highlight",
    "highlight_menu_slice",
    "turn_selected_function",
    "rotate_dial",
    "shortcuts_for_modifiers",
    "modifier_names",
    "is_multifunction",
    "pad_to_minimum",
    "window_was_changed",
    "get_appropriate_app_name_and_shortcuts",
    "overlay_enabled",
}


def load_driver_state(shortcuts):
    with open(os.path.join(ROOT, "dialpad.py"), encoding="utf-8") as source:
        tree = ast.parse(source.read())
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in STATE_FUNCTIONS
    ]
    module = ast.Module(body=functions, type_ignores=[])
    sent, queued = [], []
    namespace = {
        "META_KEY": META_KEY,
        "GENERIC_DESKTOP_ID_WORDS": {"studio", "desktop", "app", "editor", "client", "player", "viewer"},
        "app_shortcuts": shortcuts,
        "app_specific_shortcuts": {},
        "app_name": None,
        "window_binary": None,
        "window_title": None,
        "xdg_session_type": "wayland",
        "multi_app_mode": None,
        "multi_app_mode_titles": None,
        "multi_app_mode_icons": None,
        "slices_minimum_count": 4,
        "socket_enabled": False,
        # The D-Bus overlay alone must receive the state.
        "dbus_service": object(),
        "dialpad": True,
        "active_modifiers": set(),
        "last_function_by_app": {},
        "submenu": None,
        "submenu_index": 0,
        "send_to_socket": sent.append,
        "enqueue_action": queued.append,
        "send_function_value": lambda *_args: None,
        "run_menu_action": lambda *_args: None,
        "get_current_value": lambda _entry: None,
        "stop_threads": False,
        "log": type("Log", (), {"info": staticmethod(lambda *a, **k: None),
                                "debug": staticmethod(lambda *a, **k: None)})(),
    }
    exec(compile(module, "dialpad.py", "exec"), namespace)
    return namespace, sent, queued


def last_state(sent):
    for payload in reversed(sent):
        if "title" in payload:
            return payload
    return None


def last_details(sent):
    merged = {}
    for payload in sent:
        merged.update(payload.get("details", {}))
    return merged


class DriverStateTest(unittest.TestCase):
    def setUp(self):
        config = DialPadConfig(profiles=[
            Profile(name="Default", segments=[volume(), vertical_scroll(), tracks()]),
            Profile(
                name="Krita",
                app_id="org.kde.krita.desktop",
                segments=[dataclasses.replace(zoom_wheel(), overlay={"visibility": "pill"}), volume()],
                overlay={"position": "custom", "custom_x": 0.9, "custom_y": 0.2},
            ),
            Profile(
                name="Files",
                app_id="org.gnome.Nautilus.desktop",
                segments=[
                    vertical_scroll(),
                    Segment(
                        title="Display", color="#f4c95d", overlay={"position": "top"},
                        items=[brightness(), Segment(title="Off", enabled=False), mute(), volume()],
                    ),
                    Segment(title="Empty", items=[]),
                ],
            ),
            Profile(
                name="Player",
                app_id="mpv.desktop",
                mode="single",
                single_function="Volume",
                segments=[Segment(title="Hidden", enabled=False), volume()],
            ),
        ])
        self.driver, self.sent, self.queued = load_driver_state(shortcuts_from_config(config))

    def focus(self, app_id, **kwargs):
        self.driver["window_was_changed"](app_id, **kwargs)

    def test_new_application_opens_its_menu_with_colours(self):
        self.focus("org.kde.krita.desktop")
        state = last_state(self.sent)
        self.assertEqual(state["title"], "Menu")
        self.assertEqual(state["titles"][:2], ["Zoom", "Volume"])
        details = last_details(self.sent)
        self.assertEqual(details["profile"], "Krita")
        self.assertEqual(details["colors"][:2], ["#e7b98d", "#6cb6ff"])

    def test_dial_look_comes_from_profile_then_function(self):
        self.focus("org.kde.krita.desktop")
        profile_look = {"position": "custom", "custom_x": 0.9, "custom_y": 0.2}
        self.assertEqual(last_details(self.sent)["look"], profile_look)
        self.driver["enter_function"]("Zoom")
        self.assertEqual(last_details(self.sent)["look"], {**profile_look, "visibility": "pill"})
        self.driver["enter_function"]("Volume")
        self.assertEqual(last_details(self.sent)["look"], profile_look)
        # Another application's profile has no overrides: the defaults apply.
        self.focus("firefox.desktop")
        self.assertEqual(last_details(self.sent)["look"], {})

    def test_last_function_is_remembered_per_application(self):
        self.focus("org.kde.krita.desktop")
        self.driver["enter_function"]("Zoom")
        self.focus("firefox.desktop")
        self.assertEqual(last_state(self.sent)["title"], "Menu")
        self.assertFalse(self.driver["center_activated"])
        self.focus("org.kde.krita.desktop")
        self.assertTrue(self.driver["center_activated"])
        self.assertEqual(self.driver["title"], "Zoom")
        self.assertEqual(last_details(self.sent)["hints"]["cw"], "Zoom in")

    def test_returning_to_menu_forgets_the_function(self):
        self.focus("org.kde.krita.desktop")
        self.driver["enter_function"]("Zoom")
        self.driver["return_to_multifunction_menu"]()
        self.focus("firefox.desktop")
        self.focus("org.kde.krita.desktop")
        self.assertFalse(self.driver["center_activated"])

    def test_function_of_previous_application_is_not_kept(self):
        self.focus("org.kde.krita.desktop")
        self.driver["enter_function"]("Zoom")
        self.driver["last_function_by_app"].clear()
        self.focus("firefox.desktop")
        self.assertFalse(self.driver["center_activated"])
        self.assertIsNone(self.driver["title"])

    def test_single_function_profile_binds_the_dial(self):
        self.focus("mpv.desktop")
        self.assertFalse(self.driver["multi_app_mode"])
        self.assertEqual(self.driver["title"], "Volume")
        self.assertEqual(last_state(self.sent)["titles"], [])
        self.assertEqual(last_details(self.sent)["mode"], "single")
        self.assertEqual(self.driver["function_settings"]("Volume")["treshold"], 30)

    def test_config_reload_keeps_the_selected_function(self):
        self.focus("org.kde.krita.desktop")
        self.driver["enter_function"]("Zoom")
        self.sent.clear()
        self.focus("org.kde.krita.desktop", force=True)
        self.assertTrue(self.driver["center_activated"])
        self.assertEqual(self.driver["title"], "Zoom")
        self.assertNotIn("Menu", [payload.get("title") for payload in self.sent])

    def test_selecting_a_sector_enters_the_function(self):
        self.focus("org.kde.krita.desktop")
        self.driver["title"] = "Zoom"
        self.assertTrue(self.driver["select_multifunction_segment"]())
        self.assertTrue(self.driver["center_activated"])
        self.assertTrue(self.driver["function_has_center_action"]("Zoom"))



class SubmenuTest(unittest.TestCase):
    """A group opens a list of its functions beside the dial."""

    setUp = DriverStateTest.setUp
    focus = DriverStateTest.focus

    def open_group(self):
        self.focus("org.gnome.Nautilus.desktop")
        self.driver["title"] = "Display"
        self.assertTrue(self.driver["select_multifunction_segment"]())

    def test_group_is_a_menu_sector_and_an_empty_group_is_not(self):
        self.focus("org.gnome.Nautilus.desktop")
        self.assertEqual(last_state(self.sent)["titles"][:2], ["Scroll", "Display"])
        self.assertNotIn("Empty", last_state(self.sent)["titles"])

    def test_choosing_a_group_opens_its_list(self):
        self.open_group()
        self.assertFalse(self.driver["center_activated"])
        self.assertEqual(self.driver["submenu"], "Display")
        # The highlighted function is the title; the menu stays the menu.
        state = last_state(self.sent)
        self.assertEqual(state["title"], "Brightness")
        self.assertEqual(state["titles"][:2], ["Scroll", "Display"])
        submenu = last_details(self.sent)["submenu"]
        self.assertEqual(submenu["title"], "Display")
        self.assertEqual(submenu["index"], 1)
        self.assertEqual(submenu["items"], ["Brightness", "Mute", "Volume"])
        self.assertEqual(submenu["selected"], 0)
        self.assertEqual(len(submenu["colors"]), 3)
        self.assertEqual(last_details(self.sent)["look"], {"position": "top"})

    def test_turning_moves_the_highlight_and_stops_at_the_ends(self):
        self.open_group()
        move = self.driver["move_submenu_highlight"]
        self.assertFalse(move("counterclockwise"))
        self.assertTrue(move("clockwise"))
        self.assertTrue(move("clockwise"))
        self.assertFalse(move("clockwise"))
        self.assertEqual(self.driver["title"], "Volume")
        self.assertEqual(last_details(self.sent)["submenu"]["selected"], 2)

    def test_tap_chooses_a_function_and_hold_goes_back_one_level(self):
        self.open_group()
        self.driver["move_submenu_highlight"]("clockwise")
        self.driver["move_submenu_highlight"]("clockwise")
        self.assertTrue(self.driver["select_multifunction_segment"]())
        self.assertTrue(self.driver["center_activated"])
        self.assertEqual(self.driver["title"], "Volume")
        self.assertEqual(self.driver["function_settings"]("Volume")["treshold"], 30)
        self.assertTrue(self.driver["function_has_center_action"]("Volume"))
        details = last_details(self.sent)
        self.assertIsNone(details["submenu"])
        self.assertEqual(details["parent"], "Display")
        self.assertEqual(details["look"], {"position": "top"})

        self.driver["leave_function"]()
        self.assertFalse(self.driver["center_activated"])
        self.assertEqual(self.driver["submenu"], "Display")
        self.assertEqual(last_details(self.sent)["submenu"]["selected"], 2)

        self.driver["leave_function"]()
        self.assertIsNone(self.driver["submenu"])
        self.assertEqual(last_state(self.sent)["title"], "Menu")
        self.assertIsNone(last_details(self.sent)["submenu"])

    def test_launcher_in_a_list_runs_and_keeps_the_list(self):
        self.open_group()
        self.driver["move_submenu_highlight"]("clockwise")
        self.queued.clear()
        self.assertTrue(self.driver["select_multifunction_segment"]())
        self.assertEqual(len(self.queued), 1)
        self.assertFalse(self.driver["center_activated"])
        self.assertEqual(self.driver["submenu"], "Display")
        self.assertEqual(self.driver["title"], "Mute")

    def test_function_of_a_group_is_remembered(self):
        self.open_group()
        self.driver["select_multifunction_segment"]()
        self.assertEqual(self.driver["last_function_by_app"]["org.gnome.Nautilus.desktop"], ("Display", "Brightness"))
        self.focus("firefox.desktop")
        self.assertIsNone(self.driver["submenu"])
        self.focus("org.gnome.Nautilus.desktop")
        self.assertTrue(self.driver["center_activated"])
        self.assertEqual(self.driver["submenu"], "Display")
        self.assertEqual(self.driver["title"], "Brightness")

    def test_config_reload_keeps_the_open_list(self):
        self.open_group()
        self.driver["move_submenu_highlight"]("clockwise")
        self.focus("org.gnome.Nautilus.desktop", force=True)
        self.assertEqual(self.driver["submenu"], "Display")
        self.assertEqual(self.driver["title"], "Mute")


if __name__ == "__main__":
    unittest.main()
