"""The SpaceMouse screen's settings: the `screen` section, a profile's `screen`
overrides and the buttons' screen labels."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.profile_share import PROFILE_FORMAT, ProfileTextError, profile_json, profiles_from_text
from config.schema import (
    SCREEN_PAGES,
    DialPadConfig,
    Profile,
    ScreenConfig,
    SpaceMouseAction,
    SpaceMouseButton,
    _decoder,
    _default_config,
    _encoder,
    default_spacemouse_buttons,
    screen_rule,
)


def decode_screen(values):
    data = _encoder(DialPadConfig(geometry=_default_config().geometry))
    data["screen"] = values
    return _decoder(data, DialPadConfig).screen


class ScreenConfigTests(unittest.TestCase):
    def test_defaults_and_round_trip(self):
        config = _default_config()
        self.assertEqual(config.screen, ScreenConfig())
        self.assertEqual(config.screen.pages, list(SCREEN_PAGES))
        config.screen = ScreenConfig(
            enabled=False, brightness=40, dim_after_s=60, dim_brightness=5, off_after_s=900,
            wake_on_motion=False, pages=["dial", "keys"], start_page="dial", follow_dial=False,
            press_feedback=False, theme="contrast", text_size="large", show_long=False, clock_24h=False,
        )
        decoded = _decoder(json.loads(json.dumps(_encoder(config))), DialPadConfig)
        self.assertEqual(decoded.screen, config.screen)

    def test_an_old_file_without_the_section_gets_the_defaults(self):
        data = _encoder(_default_config())
        del data["screen"]
        self.assertEqual(_decoder(data, DialPadConfig).screen, ScreenConfig())

    def test_values_are_clamped_and_repaired(self):
        screen = decode_screen({
            "brightness": 500, "dim_after_s": -4, "dim_brightness": "x", "off_after_s": 10 ** 9,
            "pages": ["clock", "bogus", "clock", 3, "keys"], "start_page": "dial",
            "theme": "neon", "text_size": "huge", "enabled": 0,
        })
        self.assertEqual((screen.brightness, screen.dim_after_s, screen.dim_brightness), (100, 0, 10))
        self.assertEqual(screen.off_after_s, 86400)
        self.assertEqual(screen.pages, ["clock", "keys"])
        self.assertEqual(screen.start_page, "clock")  # "dial" is not shown: the first page
        self.assertEqual((screen.theme, screen.text_size, screen.enabled), ("skin", "normal", False))

    def test_no_usable_page_brings_the_defaults_back(self):
        for pages in ([], ["nope"], "keys", None):
            self.assertEqual(decode_screen({"pages": pages}).pages, list(SCREEN_PAGES))

    def test_a_section_that_is_not_an_object_is_ignored(self):
        data = _encoder(_default_config())
        data["screen"] = "junk"
        self.assertEqual(_decoder(data, DialPadConfig).screen, ScreenConfig())


class ProfileScreenTests(unittest.TestCase):
    def test_rule_keeps_only_valid_overrides(self):
        self.assertEqual(screen_rule(None), {})
        self.assertEqual(screen_rule({"pages": [], "start_page": "x", "accent": "red"}), {})
        self.assertEqual(
            screen_rule({"pages": ["dial", "dial", "keys", "x"], "start_page": "keys", "accent": " #AABBCC ", "other": 1}),
            {"pages": ["dial", "keys"], "start_page": "keys", "accent": "#aabbcc"},
        )

    def test_round_trip_and_empty_is_omitted(self):
        config = _default_config()
        profile = config.profiles[0]
        self.assertNotIn("screen", _encoder(profile))
        profile.screen = {"start_page": "dial", "accent": "#112233"}
        decoded = _decoder(_encoder(config), DialPadConfig)
        self.assertEqual(decoded.profiles[0].screen, profile.screen)


class ButtonLabelTests(unittest.TestCase):
    def test_label_round_trip_and_cap(self):
        config = _default_config()
        config.spacemouse.buttons = {
            "12": SpaceMouseButton(press=SpaceMouseAction("dial"), label="Dial"),
            "13": SpaceMouseButton(press=SpaceMouseAction("back")),
        }
        data = _encoder(config)
        self.assertEqual(data["spacemouse"]["buttons"]["12"]["label"], "Dial")
        self.assertNotIn("label", data["spacemouse"]["buttons"]["13"])
        self.assertEqual(_decoder(data, DialPadConfig).spacemouse.buttons, config.spacemouse.buttons)
        data["spacemouse"]["buttons"]["12"]["label"] = "  " + "x" * 80 + "  "
        self.assertEqual(_decoder(data, DialPadConfig).spacemouse.buttons["12"].label, "x" * 40)
        data["spacemouse"]["buttons"]["12"]["label"] = 7
        self.assertEqual(_decoder(data, DialPadConfig).spacemouse.buttons["12"].label, "")

    def test_a_label_alone_keeps_the_entry(self):
        config = _default_config()
        config.spacemouse.buttons = {"5": SpaceMouseButton(label="Front")}
        decoded = _decoder(_encoder(config), DialPadConfig)
        self.assertEqual(decoded.spacemouse.buttons["5"].label, "Front")

    def test_default_map_is_the_dual_function_keys(self):
        buttons = default_spacemouse_buttons()
        self.assertEqual({key for key in buttons if 12 <= int(key) <= 21}, {str(index) for index in range(12, 22)})
        self.assertEqual([buttons[str(12 + n)].press.slot for n in range(5)], [0, 1, 2, 3, 4])
        self.assertEqual([buttons[str(17 + n)].press.slot for n in range(5)], [5, 6, 7, 8, 9])
        self.assertTrue(all(button.label == "" for button in buttons.values()))


class ProfileTextTests(unittest.TestCase):
    base = {"name": "Shared", "segments": [{"title": "Zoom", "macro_cw": [{"type": "keystroke", "keys": ["KEY_EQUAL"]}]}]}

    def test_format_documents_label_and_screen(self):
        self.assertIn('"label"', PROFILE_FORMAT)
        self.assertIn('"screen"', PROFILE_FORMAT)
        self.assertIn("12-16", PROFILE_FORMAT)

    def test_label_and_screen_are_read(self):
        text = json.dumps({
            **self.base,
            "spacemouse_buttons": {"12": {"press": {"type": "dial"}, "label": "Dial"}},
            "screen": {"pages": ["dial", "keys"], "start_page": "dial", "accent": "#336699"},
        })
        (profile,) = profiles_from_text(text)
        self.assertEqual(profile.spacemouse_buttons["12"].label, "Dial")
        self.assertEqual(profile.screen, {"pages": ["dial", "keys"], "start_page": "dial", "accent": "#336699"})
        again = profiles_from_text(profile_json(profile))[0]
        self.assertEqual((again.spacemouse_buttons, again.screen), (profile.spacemouse_buttons, profile.screen))

    def test_bad_screen_is_explained(self):
        for screen in ("keys", {"pages": ["keys", "nope"]}, {"pages": "keys"}, {"start_page": "nope"}, {"accent": "red"}):
            with self.assertRaises(ProfileTextError, msg=screen):
                profiles_from_text(json.dumps({**self.base, "screen": screen}))

    def test_bad_label_is_explained(self):
        with self.assertRaises(ProfileTextError):
            profiles_from_text(json.dumps({**self.base, "spacemouse_buttons": {"3": {"press": {"type": "dial"}, "label": [1]}}}))


if __name__ == "__main__":
    unittest.main()
