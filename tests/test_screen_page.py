"""The pure helpers of the settings app's Screen page (config_app/screen_page.py)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import gi  # noqa: F401
    from gi.repository import GLib  # noqa: F401
except ImportError:  # pragma: no cover
    gi = None

from config.schema import (
    SCREEN_PAGES,
    Profile,
    ScreenConfig,
    SpaceMouseAction,
    SpaceMouseButton,
    _default_config,
    spacemouse_buttons_for,
)
from spacemouse_devices import SPACEPILOT_PRO, PresentScreen

if gi is not None:
    from config_app import screen_page as sp
    from config_app import spacemouse as sm


@unittest.skipIf(gi is None, "needs PyGObject")
class ChoiceTests(unittest.TestCase):
    def test_values_round_trip(self):
        for choices in (sp.DIM_AFTER_CHOICES, sp.OFF_AFTER_CHOICES, sp.THEME_CHOICES, sp.TEXT_SIZE_CHOICES):
            for position, (value, _label) in enumerate(choices):
                self.assertEqual(sp.choice_index(choices, value), position)
                self.assertEqual(sp.choice_value(choices, position), value)

    def test_unknown_value_falls_back_to_the_first(self):
        self.assertEqual(sp.choice_index(sp.THEME_CHOICES, "neon"), 0)
        self.assertEqual(sp.choice_value(sp.THEME_CHOICES, 99), "skin")

    def test_choices_match_the_schema(self):
        from config.schema import SCREEN_TEXT_SIZES, SCREEN_THEMES

        self.assertEqual([value for value, _ in sp.THEME_CHOICES], list(SCREEN_THEMES))
        self.assertEqual([value for value, _ in sp.TEXT_SIZE_CHOICES], list(SCREEN_TEXT_SIZES))
        self.assertEqual(set(sp.PAGE_TITLES), set(SCREEN_PAGES))
        defaults = ScreenConfig()
        self.assertIn(defaults.dim_after_s, [value for value, _ in sp.DIM_AFTER_CHOICES])
        self.assertIn(defaults.off_after_s, [value for value, _ in sp.OFF_AFTER_CHOICES])

    def test_a_value_from_the_file_is_kept(self):
        choices = sp.choices_with(sp.DIM_AFTER_CHOICES, 45)
        self.assertEqual([value for value, _ in choices], [0, 30, 45, 60, 120, 300, 600, 1800])
        self.assertEqual(choices[2][1], "45 seconds")
        self.assertEqual(sp.choices_with(sp.DIM_AFTER_CHOICES, 60), sp.DIM_AFTER_CHOICES)
        self.assertEqual(sp.choices_with(sp.DIM_AFTER_CHOICES, 99999)[-1], (99999, "99999 seconds"))
        self.assertEqual(sp.choices_with(sp.THEME_CHOICES, "neon"), sp.THEME_CHOICES)

    def test_seconds_label(self):
        self.assertEqual(sp.seconds_label(0), "Never")
        self.assertEqual(sp.seconds_label(60), "1 minute")
        self.assertEqual(sp.seconds_label(300), "5 minutes")
        self.assertEqual(sp.seconds_label(3600), "1 hour")
        self.assertEqual(sp.seconds_label(7200), "2 hours")
        self.assertEqual(sp.seconds_label(90), "90 seconds")


@unittest.skipIf(gi is None, "needs PyGObject")
class PageTests(unittest.TestCase):
    def test_order_lists_hidden_pages_last(self):
        self.assertEqual(sp.page_order(["clock", "keys"]), ["clock", "keys", "dial", "device", "motion"])
        self.assertEqual(sp.page_order([]), list(SCREEN_PAGES))
        self.assertEqual(sp.page_order(["keys", "keys", "bogus"]), ["keys", "dial", "device", "motion", "clock"])

    def test_move(self):
        pages = ["keys", "dial", "clock"]
        self.assertEqual(sp.move_page(pages, "dial", -1), ["dial", "keys", "clock"])
        self.assertEqual(sp.move_page(pages, "dial", 1), ["keys", "clock", "dial"])
        self.assertEqual(sp.move_page(pages, "keys", -1), pages)  # at the start
        self.assertEqual(sp.move_page(pages, "clock", 1), pages)  # at the end
        self.assertEqual(sp.move_page(pages, "motion", 1), pages)  # hidden
        self.assertEqual(pages, ["keys", "dial", "clock"])  # the argument is not changed

    def test_toggle_keeps_one_page(self):
        self.assertEqual(sp.toggle_page(["keys", "dial"], "dial", False), ["keys"])
        self.assertEqual(sp.toggle_page(["keys"], "keys", False), ["keys"])
        self.assertEqual(sp.toggle_page(["keys"], "clock", True), ["keys", "clock"])
        self.assertEqual(sp.toggle_page(["keys"], "bogus", True), ["keys"])

    def test_a_page_comes_back_in_its_place(self):
        reference = ["keys", "dial", "device", "motion", "clock"]
        self.assertEqual(sp.toggle_page(["keys", "motion"], "dial", True, reference), ["keys", "dial", "motion"])
        self.assertEqual(sp.toggle_page(["keys", "dial"], "clock", True, reference), ["keys", "dial", "clock"])
        self.assertEqual(sp.toggle_page(["dial"], "keys", True, reference), ["keys", "dial"])

    def test_start_page_must_be_shown(self):
        self.assertEqual(sp.fix_start_page(["dial", "clock"], "clock"), "clock")
        self.assertEqual(sp.fix_start_page(["dial", "clock"], "keys"), "dial")
        self.assertEqual(sp.fix_start_page([], "keys"), "keys")

    def test_effective_pages_follow_the_profile(self):
        config = _default_config()
        config.screen.pages = ["keys", "clock"]
        config.screen.start_page = "clock"
        profile = Profile(name="Blender", app_id="blender")
        self.assertEqual(sp.effective_pages(config, None), ["keys", "clock"])
        self.assertEqual(sp.effective_pages(config, profile), ["keys", "clock"])
        self.assertEqual(sp.effective_start(config, profile), "clock")
        profile.screen = {"pages": ["dial", "motion"], "start_page": "motion"}
        self.assertEqual(sp.effective_pages(config, profile), ["dial", "motion"])
        self.assertEqual(sp.effective_start(config, profile), "motion")
        profile.screen = {"pages": ["dial"], "start_page": "clock"}  # not among its pages
        self.assertEqual(sp.effective_start(config, profile), "dial")


@unittest.skipIf(gi is None, "needs PyGObject")
class OverrideTests(unittest.TestCase):
    def test_set_and_remove(self):
        profile = Profile(name="Blender", app_id="blender")
        self.assertTrue(sp.set_override(profile, "start_page", "dial"))
        self.assertTrue(sp.set_override(profile, "pages", ["dial", "keys"]))
        self.assertTrue(sp.set_override(profile, "accent", "#33AA66"))
        self.assertEqual(profile.screen, {"start_page": "dial", "pages": ["dial", "keys"], "accent": "#33aa66"})
        self.assertTrue(sp.set_override(profile, "accent", None))
        self.assertTrue(sp.set_override(profile, "start_page", None))
        self.assertEqual(profile.screen, {"pages": ["dial", "keys"]})

    def test_invalid_values_are_refused(self):
        profile = Profile(name="Blender", app_id="blender", screen={"start_page": "clock"})
        self.assertFalse(sp.set_override(profile, "start_page", "bogus"))
        self.assertFalse(sp.set_override(profile, "accent", "red"))
        self.assertFalse(sp.set_override(profile, "pages", []))
        self.assertEqual(profile.screen, {"start_page": "clock"})

    def test_customised(self):
        config = _default_config()
        self.assertFalse(sp.settings_customised(config))
        config.screen.brightness = 40
        self.assertTrue(sp.settings_customised(config))
        config = _default_config()
        config.profiles[0].screen = {"start_page": "dial"}
        self.assertTrue(sp.settings_customised(config))


@unittest.skipIf(gi is None, "needs PyGObject")
class LabelTests(unittest.TestCase):
    def setUp(self):
        self.config = _default_config()
        self.profile = Profile(name="Blender", app_id="blender")
        self.config.profiles.append(self.profile)

    def test_function_keys(self):
        keys = sp.function_keys(SPACEPILOT_PRO)
        self.assertEqual([key.label for key in keys], ["1", "2", "3", "4", "5"])
        self.assertEqual([key.index for key in keys], [12, 13, 14, 15, 16])
        self.assertEqual([key.long_index for key in keys], [17, 18, 19, 20, 21])

    def test_global_label_keeps_the_default_actions(self):
        self.assertTrue(sp.set_label(self.config, None, 12, " Orbit "))
        self.assertEqual(self.config.spacemouse.buttons, {"12": SpaceMouseButton(label="Orbit")})
        effective = spacemouse_buttons_for(self.config)
        self.assertEqual(effective["12"].label, "Orbit")
        self.assertEqual(effective["12"].press, SpaceMouseAction("function", slot=0))
        self.assertEqual(effective["13"].press, SpaceMouseAction("function", slot=1))
        self.assertEqual(sp.label_of(self.config, None, 12), "Orbit")
        # The SpaceMouse page sees the same.
        self.assertEqual(sm.binding(self.config, None, 13)[0].press.slot, 1)

    def test_clearing_a_global_label_removes_the_entry(self):
        sp.set_label(self.config, None, 12, "Orbit")
        self.assertTrue(sp.set_label(self.config, None, 12, ""))
        self.assertEqual(self.config.spacemouse.buttons, {})
        self.assertFalse(sp.set_label(self.config, None, 12, ""))

    def test_label_in_an_edited_map_goes_into_the_entry(self):
        sm.store_global(sm.editable_global(self.config), 12, SpaceMouseButton(press=SpaceMouseAction("dial")))
        sp.set_label(self.config, None, 12, "Dial")
        self.assertEqual(self.config.spacemouse.buttons["12"], SpaceMouseButton(press=SpaceMouseAction("dial"), label="Dial"))
        sp.set_label(self.config, None, 12, "")
        self.assertEqual(self.config.spacemouse.buttons["12"], SpaceMouseButton(press=SpaceMouseAction("dial")))

    def test_profile_label_is_label_only_without_own_binding(self):
        self.assertTrue(sp.set_label(self.config, self.profile, 12, "Orbit"))
        self.assertEqual(self.profile.spacemouse_buttons, {"12": SpaceMouseButton(label="Orbit")})
        self.assertEqual(self.config.spacemouse.buttons, {})
        button, source = sm.binding(self.config, self.profile, 12)
        self.assertEqual((button.label, button.press), ("Orbit", SpaceMouseAction("function", slot=0)))
        self.assertEqual(sp.label_of(self.config, None, 12), "")  # the others are untouched
        self.assertTrue(sp.set_label(self.config, self.profile, 12, ""))
        self.assertEqual(self.profile.spacemouse_buttons, {})

    def test_profile_label_on_an_own_binding(self):
        own = SpaceMouseButton(press=SpaceMouseAction("select"))
        self.profile.spacemouse_buttons["12"] = own
        sp.set_label(self.config, self.profile, 12, "Pick")
        self.assertEqual(self.profile.spacemouse_buttons["12"], SpaceMouseButton(press=SpaceMouseAction("select"), label="Pick"))
        sp.set_label(self.config, self.profile, 12, "")
        self.assertEqual(self.profile.spacemouse_buttons["12"], SpaceMouseButton(press=SpaceMouseAction("select")))

    def test_a_profile_can_hide_the_global_label(self):
        sp.set_label(self.config, None, 12, "Orbit")
        self.assertEqual(sp.label_of(self.config, self.profile, 12), "Orbit")
        self.assertTrue(sp.set_label(self.config, self.profile, 12, ""))
        self.assertEqual(sp.label_of(self.config, self.profile, 12), "")
        self.assertEqual(sp.label_of(self.config, None, 12), "Orbit")
        self.assertEqual(sm.binding(self.config, self.profile, 12)[0].press, SpaceMouseAction("function", slot=0))

    def test_label_is_trimmed_and_capped(self):
        sp.set_label(self.config, None, 12, "x" * 100)
        self.assertEqual(len(self.config.spacemouse.buttons["12"].label), 40)

    def test_unchanged_label_changes_nothing(self):
        self.assertFalse(sp.set_label(self.config, None, 12, ""))
        sp.set_label(self.config, None, 12, "A")
        self.assertFalse(sp.set_label(self.config, None, 12, " A "))

    def test_automatic_text_is_the_description(self):
        self.assertEqual(sp.auto_text(self.config, None, 12), "Function: #1")
        sp.set_label(self.config, None, 12, "Orbit")
        self.assertEqual(sp.auto_text(self.config, None, 12), "Function: #1")  # not the label
        self.assertEqual(sm.describe_button(sm.binding(self.config, None, 12)[0]), "Orbit")


@unittest.skipIf(gi is None, "needs PyGObject")
class ScreenSyncTests(unittest.TestCase):
    def test_a_field_the_window_did_not_change_comes_from_the_file(self):
        sync = sp.ScreenSync()
        window, stored = ScreenConfig(brightness=40), ScreenConfig(brightness=100)
        adopted = sync.adopt(window, stored)
        self.assertEqual(adopted.brightness, 100)
        self.assertIsNot(adopted, stored)

    def test_a_field_the_window_changed_is_kept(self):
        sync = sp.ScreenSync()
        sync.mark("theme")
        window = ScreenConfig(brightness=40, theme="dark")
        stored = ScreenConfig(brightness=100, theme="light")
        adopted = sync.adopt(window, stored)
        self.assertEqual((adopted.brightness, adopted.theme), (100, "dark"))

    def test_nothing_to_adopt(self):
        sync = sp.ScreenSync()
        self.assertIsNone(sync.adopt(ScreenConfig(brightness=40), ScreenConfig(brightness=40)))
        sync.mark("brightness")
        self.assertIsNone(sync.adopt(ScreenConfig(brightness=40), ScreenConfig(brightness=100, theme="skin")),
                          "the only difference is a field the window changed")

    def test_clear_after_a_save(self):
        sync = sp.ScreenSync()
        sync.mark("brightness", "pages")
        sync.clear()
        self.assertEqual(sync.adopt(ScreenConfig(brightness=40), ScreenConfig(brightness=100)).brightness, 100)

    def test_lists_are_copied(self):
        sync = sp.ScreenSync()
        sync.mark("pages")
        window = ScreenConfig(pages=["clock"])
        adopted = sync.adopt(window, ScreenConfig(pages=["keys", "dial"], brightness=5))
        self.assertEqual(adopted.pages, ["clock"])
        adopted.pages.append("dial")
        self.assertEqual(window.pages, ["clock"])

    def test_unknown_field_is_refused(self):
        with self.assertRaises(ValueError):
            sp.ScreenSync().mark("bogus")

    def test_every_screen_field_is_known(self):
        self.assertEqual(set(sp.SCREEN_FIELDS), set(ScreenConfig.__dataclass_fields__))
        sp.ScreenSync().mark(*sp.SCREEN_FIELDS)


@unittest.skipIf(gi is None, "needs PyGObject")
class ReadScreenTests(unittest.TestCase):
    def test_reads_the_stored_section(self):
        import json
        import tempfile

        from config.schema import _encoder

        config = _default_config()
        config.screen.brightness = 100
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            self.assertIsNone(sp.read_screen(path))  # no file
            path.write_text("{ not json")
            self.assertIsNone(sp.read_screen(path))
            path.write_text(json.dumps(_encoder(config)))
            self.assertEqual(sp.read_screen(path).brightness, 100)

    def test_external_change_survives_a_pending_edit_of_another_field(self):
        """The case of the report: the service saved brightness 100 while the window
        (holding 40 and an unsaved theme change) was about to save."""
        sync = sp.ScreenSync()
        window = ScreenConfig(brightness=40)
        window.theme = "light"
        sync.mark("theme")
        stored = ScreenConfig(brightness=100)
        window = sync.adopt(window, stored) or window
        self.assertEqual((window.brightness, window.theme), (100, "light"))
        sync.clear()
        self.assertIsNone(sync.adopt(window, ScreenConfig(brightness=100, theme="light")))


@unittest.skipIf(gi is None, "needs PyGObject")
class StatusTests(unittest.TestCase):
    def test_service_state_text(self):
        self.assertEqual(sp.service_state_text("active\n"), "Running")
        self.assertEqual(sp.service_state_text("inactive\n"), "Not running")
        self.assertTrue(sp.service_state_text("failed").startswith("Failed"))
        self.assertEqual(sp.service_state_text(""), "Unknown")

    def test_connection_text(self):
        self.assertIn("No SpaceMouse", sp.connection_text([]))
        text = sp.connection_text([PresentScreen(SPACEPILOT_PRO, 1, 4, "/sys/x")])
        self.assertEqual(text, "SpacePilot Pro · USB bus 1, device 4")

    def test_pyusb_check_does_not_raise(self):
        self.assertIsInstance(sp.pyusb_missing(), bool)

    def test_screen_keys_help_covers_the_keys_of_the_model(self):
        text = " ".join(name for name, _ in sp.SCREEN_KEY_HELP).lower()
        for key in SPACEPILOT_PRO.screen.keys:
            self.assertIn(key.label.lower(), text)


if __name__ == "__main__":
    unittest.main()
