"""Function library and application packs: basic and advanced variants."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.presets import (  # noqa: E402
    APP_PACKS,
    BRIGHTNESS_QUERY,
    FUNCTION_LIBRARY,
    KBD_LIGHT_QUERY,
    LIBRARY_CATEGORIES,
    VOLUME_QUERY,
    pack_for_app,
    profile_for_app,
    profile_for_pack,
    variant_summary,
)
from config.profile_share import profile_json, profiles_from_text  # noqa: E402
from config.schema import (  # noqa: E402
    SPACEMOUSE_LABEL_MAX,
    DialPadConfig,
    _decoder,
    _encoder,
    all_functions,
    overlay_rule,
    shell_commands,
)

try:
    import libevdev
except ImportError:  # the driver's library; not needed for the rest
    libevdev = None

VALUE_QUERIES = {VOLUME_QUERY, BRIGHTNESS_QUERY, KBD_LIGHT_QUERY}


def variants():
    for pack in APP_PACKS:
        for advanced in (False, True):
            yield pack, advanced, profile_for_pack(pack, advanced)


def functions(profile):
    return [segment for _group, segment in all_functions(profile.segments)]


class VariantsTest(unittest.TestCase):
    def test_every_pack_has_both_variants(self):
        for pack in APP_PACKS:
            with self.subTest(pack=pack.name):
                self.assertIsNotNone(pack.advanced)
                basic = pack.segments()
                advanced = pack.segments(True)
                self.assertLessEqual(len(basic), 6, "a basic menu stays short")
                self.assertTrue(all(not segment.is_group for segment in basic), "basic menus have no groups")
                self.assertLessEqual(len(advanced), 8, "sectors stay large enough to hit")
                count = len([item for _group, item in all_functions(advanced)])
                self.assertGreater(count, len(basic), "the advanced variant offers more")

    def test_groups_are_one_level_and_titles_unique(self):
        for pack, advanced, profile in variants():
            with self.subTest(pack=pack.name, advanced=advanced):
                titles = [segment.title for segment in profile.segments]
                self.assertEqual(len(titles), len(set(titles)))
                for segment in profile.segments:
                    if not segment.is_group:
                        continue
                    items = [item.title for item in segment.items]
                    self.assertGreaterEqual(len(items), 2, segment.title)
                    self.assertEqual(len(items), len(set(items)), segment.title)
                    self.assertNotIn(segment.title, items, "a group and its function share no name")
                    self.assertTrue(all(not item.is_group for item in segment.items))

    def test_every_function_does_something(self):
        for pack, advanced, profile in variants():
            for segment in functions(profile):
                with self.subTest(pack=pack.name, advanced=advanced, function=segment.title):
                    self.assertTrue(segment.macro_cw or segment.macro_center)
                    self.assertEqual(bool(segment.macro_cw), bool(segment.macro_ccw), "both turning directions")

    def test_dial_looks_are_valid(self):
        for pack, advanced, profile in variants():
            with self.subTest(pack=pack.name, advanced=advanced):
                self.assertEqual(overlay_rule(profile.overlay), profile.overlay)
                for segment in functions(profile):
                    self.assertEqual(overlay_rule(segment.overlay), segment.overlay, segment.title)
        looks = [segment.overlay for _pack, advanced, profile in variants() if advanced
                 for segment in functions(profile) if segment.overlay]
        self.assertTrue(looks, "advanced packs keep the dial out of the way somewhere")

    def test_only_value_queries_run_commands(self):
        for pack, advanced, profile in variants():
            with self.subTest(pack=pack.name, advanced=advanced):
                commands = {command for _profile, _title, command in shell_commands([profile])}
                self.assertLessEqual(commands, VALUE_QUERIES)

    @unittest.skipIf(libevdev is None, "python-libevdev is not installed")
    def test_keys_are_evdev_names(self):
        names = {code.name for code in libevdev.EV_KEY.codes}
        for pack, advanced, profile in variants():
            for segment in functions(profile):
                for steps in (segment.macro_cw, segment.macro_ccw, segment.macro_center):
                    for step in steps:
                        for key in (step.keys or []) + (step.modifiers or []):
                            with self.subTest(pack=pack.name, function=segment.title, key=key):
                                self.assertIn(key, names)

    def test_round_trips(self):
        for pack, advanced, profile in variants():
            with self.subTest(pack=pack.name, advanced=advanced):
                config = DialPadConfig(profiles=[profile])
                self.assertEqual(_encoder(_decoder(_encoder(config), DialPadConfig)), _encoder(config))
                (copy,) = profiles_from_text(profile_json(profile))
                self.assertEqual(_encoder(copy), _encoder(profile))

    def test_summaries(self):
        blender = pack_for_app("org.blender.Blender.desktop")
        self.assertEqual(variant_summary(blender), f"{len(blender.segments())} functions")
        self.assertIn("group", variant_summary(blender, True))

    def test_timeline_editors_show_their_menu_at_the_top(self):
        for app_id in ("org.kde.kdenlive.desktop", "com.blackmagicdesign.resolve.desktop"):
            pack = pack_for_app(app_id)
            self.assertEqual(profile_for_pack(pack, True).overlay, {"position": "top"})
            self.assertEqual(profile_for_pack(pack).overlay, {})


# Dual-function keys of the SpacePilot Pro (spacemouse_devices.py): the device tells a
# short from a long press, so they never carry a hold action.
DUAL_KEYS = set(range(2, 22))
SHORT_LABEL = 20


def spacemouse_actions(button):
    return [action for action in (button.press, button.hold) if action is not None]


class SpaceMouseButtonsTest(unittest.TestCase):
    def packs_with_buttons(self):
        for pack in APP_PACKS:
            for advanced in (False, True):
                buttons = pack.spacemouse_buttons(advanced)
                if buttons:
                    yield pack, advanced, buttons

    def test_creator_packs_have_buttons(self):
        for name in ("GIMP", "Krita", "Inkscape", "darktable", "Blender", "FreeCAD", "KiCad",
                     "Kdenlive", "DaVinci Resolve"):
            pack = next(pack for pack in APP_PACKS if pack.name == name)
            with self.subTest(pack=name):
                buttons = pack.spacemouse_buttons()
                for index in range(12, 22):
                    self.assertIn(str(index), buttons, "five function keys, short and long")
                    self.assertTrue(buttons[str(index)].press, str(index))

    def test_profiles_carry_the_buttons(self):
        for pack, advanced, buttons in self.packs_with_buttons():
            with self.subTest(pack=pack.name, advanced=advanced):
                profile = profile_for_pack(pack, advanced)
                self.assertEqual(profile.spacemouse_buttons, buttons)
        blender = profile_for_app("Blender", "org.blender.Blender.desktop", True)
        self.assertEqual(blender.spacemouse_buttons["12"].label, "Edit mode")
        self.assertEqual(profile_for_app("Calculator", "org.gnome.Calculator.desktop").spacemouse_buttons, {})

    def test_every_call_builds_a_fresh_map(self):
        pack = pack_for_app("blender")
        first = pack.spacemouse_buttons()
        first["12"].label = "changed"
        self.assertEqual(pack.spacemouse_buttons()["12"].label, "Edit mode")

    def test_indexes_labels_and_hold_actions(self):
        for pack, advanced, buttons in self.packs_with_buttons():
            with self.subTest(pack=pack.name, advanced=advanced):
                for key, button in buttons.items():
                    index = int(key)
                    self.assertTrue(0 < index <= 30, key)
                    self.assertNotIn(index, {26, 27, 28}, "motion keys stay with the global map")
                    self.assertIsNone(button.hold, f"{key}: dual-function keys have no hold action"
                                      if index in DUAL_KEYS else key)
                    bound = [action for action in spacemouse_actions(button) if action.type != "none"]
                    if bound:
                        self.assertTrue(button.label, f"{pack.name} {key} needs a label")
                        self.assertLessEqual(len(button.label), SHORT_LABEL)
                        self.assertLessEqual(len(button.label), SPACEMOUSE_LABEL_MAX)
                    for action in bound:
                        self.assertIn(action.type, ("macro", "key_hold"))
                        self.assertTrue(action.keys or action.macro, key)

    def test_function_keys_only_on_their_indexes(self):
        # The five function keys (12-16 short, 17-21 long) are all set; Menu (0) stays the dial
        # and Esc, Alt, Shift, Ctrl (22-25) stay with the global map except where passed through.
        for pack, advanced, buttons in self.packs_with_buttons():
            with self.subTest(pack=pack.name, advanced=advanced):
                self.assertNotIn("0", buttons)
                for index in (22, 23, 24, 25, 29, 30):
                    if str(index) in buttons:
                        self.assertEqual(pack.name, "Blender")
                        self.assertEqual(buttons[str(index)].press.type, "none")

    @unittest.skipIf(libevdev is None, "python-libevdev is not installed")
    def test_button_keys_are_evdev_names(self):
        names = {code.name for code in libevdev.EV_KEY.codes}
        for pack, advanced, buttons in self.packs_with_buttons():
            for key, button in buttons.items():
                for action in spacemouse_actions(button):
                    steps = action.macro or []
                    used = list(action.keys or []) + [name for step in steps for name in step.keys or []]
                    for name in used:
                        with self.subTest(pack=pack.name, button=key, key=name):
                            self.assertIn(name, names)

    def test_blender_passes_its_native_keys_through(self):
        buttons = pack_for_app("blender").spacemouse_buttons()
        for index in (*range(1, 10), 22, 23, 24, 25, 29, 30):
            self.assertEqual(buttons[str(index)].press.type, "none", str(index))
        # Pie menus are held, the rest pressed; ISO 1 / 2 are unbound in Blender.
        self.assertEqual(buttons["17"].press.type, "key_hold")
        self.assertEqual(buttons["20"].press.keys, ["KEY_Z"])
        self.assertEqual(buttons["10"].press.keys, ["KEY_KP0"])
        self.assertEqual(buttons["11"].press.keys, ["KEY_LEFTCTRL", "KEY_LEFTALT", "KEY_KP0"])

    def test_freecad_view_keys_press_its_shortcuts(self):
        # FreeCAD's own 3Dconnexion map is empty until loaded in Customize, so the
        # view keys press its view shortcuts: 1 front, 2 top, 3 right, 4 rear, 5 bottom, 6 left.
        buttons = pack_for_app("freecad").spacemouse_buttons()
        expected = {"2": "KEY_2", "3": "KEY_6", "4": "KEY_3", "5": "KEY_1", "6": "KEY_5", "7": "KEY_4", "10": "KEY_0"}
        for index, key in expected.items():
            self.assertEqual(buttons[index].press.keys, [key], index)
        fit = buttons["1"].press
        self.assertEqual([step.keys for step in fit.macro], [["KEY_V"], ["KEY_F"]], "V, F is two presses")

    def test_sequences_are_one_press_each(self):
        line = pack_for_app("freecad").spacemouse_buttons()["12"].press
        self.assertEqual([step.keys for step in line.macro], [["KEY_G"], ["KEY_L"]])

    def test_variants_without_a_map_of_their_own_share_it(self):
        for pack in APP_PACKS:
            if pack.spacemouse and not pack.spacemouse_advanced:
                self.assertEqual(pack.spacemouse_buttons(True), pack.spacemouse_buttons(False))

    def test_round_trips(self):
        for pack, advanced, _buttons in self.packs_with_buttons():
            with self.subTest(pack=pack.name, advanced=advanced):
                profile = profile_for_pack(pack, advanced)
                (copy,) = profiles_from_text(profile_json(profile))
                self.assertEqual(copy.spacemouse_buttons, profile.spacemouse_buttons)
                config = DialPadConfig(profiles=[profile])
                again = _decoder(_encoder(config), DialPadConfig)
                self.assertEqual(again.profiles[0].spacemouse_buttons, profile.spacemouse_buttons)


class PackMatchTest(unittest.TestCase):
    def test_desktop_ids(self):
        expected = {
            "org.gimp.GIMP.desktop": "GIMP",
            "org.kde.krita.desktop": "Krita",
            "org.blender.Blender.desktop": "Blender",
            "org.kde.kdenlive.desktop": "Kdenlive",
            "com.blackmagicdesign.resolve.desktop": "DaVinci Resolve",
            "org.darktable.darktable.desktop": "darktable",
            "org.audacityteam.Audacity.desktop": "Audacity",
            "com.obsproject.Studio.desktop": "OBS Studio",
            "firefox.desktop": "Browser",
            "org.mozilla.firefox.desktop": "Browser",
            "code.desktop": "Code editor",
            "codium.desktop": "Code editor",
            "org.gnome.Nautilus.desktop": "Files",
            "org.kde.dolphin.desktop": "Files",
            "vlc.desktop": "Video player",
            "io.mpv.Mpv.desktop": "Video player",
            "libreoffice-calc.desktop": "LibreOffice",
            "org.freecad.FreeCAD.desktop": "FreeCAD",
            "org.kicad.kicad.desktop": "KiCad",
            "org.kicad.pcbnew.desktop": "KiCad",
            "eeschema": "KiCad",
        }
        for app_id, name in expected.items():
            with self.subTest(app_id=app_id):
                self.assertEqual(pack_for_app(app_id).name, name)
        for app_id in ("org.gnome.Calculator.desktop", "org.gnome.Console.desktop", "org.gnome.Settings.desktop"):
            with self.subTest(app_id=app_id):
                self.assertIsNone(pack_for_app(app_id))

    def test_profile_for_app(self):
        basic = profile_for_app("Blender", "org.blender.Blender.desktop")
        advanced = profile_for_app("Blender", "org.blender.Blender.desktop", True)
        self.assertEqual(basic.name, "Blender")
        self.assertTrue(any(segment.is_group for segment in advanced.segments))
        self.assertFalse(profile_for_app("Calculator", "org.gnome.Calculator.desktop").segments)


class LibraryTest(unittest.TestCase):
    def test_presets_build_in_known_categories(self):
        titles = [preset.title for preset in FUNCTION_LIBRARY]
        self.assertEqual(len(titles), len(set(titles)))
        for preset in FUNCTION_LIBRARY:
            with self.subTest(preset=preset.title):
                segment = preset.factory()
                self.assertIn(preset.category, LIBRARY_CATEGORIES)
                self.assertEqual(segment.title, preset.title)
                self.assertEqual(segment.is_group, preset.category == "Groups")

    def test_ready_groups(self):
        groups = [preset.factory() for preset in FUNCTION_LIBRARY if preset.category == "Groups"]
        self.assertGreaterEqual(len(groups), 5)
        for group in groups:
            with self.subTest(group=group.title):
                titles = [item.title for item in group.items]
                self.assertGreaterEqual(len(titles), 3)
                self.assertEqual(len(titles), len(set(titles)))


if __name__ == "__main__":
    unittest.main()
