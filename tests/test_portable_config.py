"""Settings that travel between computers: what is portable and what stays
with the machine (config/schema.py), the settings file, and setup_done."""

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.schema import (  # noqa: E402
    MACHINE_SECTIONS,
    PORTABLE_SECTIONS,
    SETTINGS_FORMAT,
    SETTINGS_MARKER,
    SPACEMOUSE_MACHINE_FIELDS,
    SPACEMOUSE_PORTABLE_FIELDS,
    ConfigError,
    DialPadConfig,
    MacroStep,
    Profile,
    Segment,
    SpaceMouseAction,
    SpaceMouseButton,
    StepType,
    _default_config,
    _encoder,
    config_from_text,
    describe_changes,
    merge_portable,
    portable_config,
    settings_document,
    settings_from_text,
)


def other_machine():
    """A configuration as another computer might have it: every machine setting
    and every portable one differs from the defaults."""
    config = _default_config()
    config.setup_done = True
    config.device_name = "asusvivobook16x"
    config.geometry.circle_diameter = 1234
    config.activation.activation_time = 2.5
    config.activation.coactivator_keys = ["KEY_LEFTSHIFT"]
    config.backlight.level = 7
    config.emulation.enabled = True
    config.mouse.enabled = True
    config.mouse.device = "Razer"
    config.mouse.toggle_key = "BTN_SIDE"
    config.gesture.hysteresis = 9
    config.overlay.skin = "midnight"
    config.overlay.scale = 1.3
    config.screen.brightness = 33
    config.screen.pages = ["clock", "keys"]
    spacemouse = config.spacemouse
    spacemouse.enabled = True
    spacemouse.socket = "/run/other.sock"
    spacemouse.axis_max = 777
    spacemouse.tilt_axes = [1, 2]
    spacemouse.tilt_invert = [True, True]
    spacemouse.twist_axis = 5
    spacemouse.twist_invert = False
    spacemouse.push_axis = 2
    spacemouse.push_invert = False
    spacemouse.hold_ms = 650
    spacemouse.twist_speed = 90
    spacemouse.mute_others = False
    spacemouse.hide_buttons = True
    spacemouse.buttons = {"13": SpaceMouseButton(press=SpaceMouseAction("dial"))}
    config.profiles.append(Profile(
        name="Blender", app_id="org.blender.Blender.desktop",
        segments=[Segment(title="Zoom", macro_cw=[MacroStep(type=StepType.SHELL, script="echo hi")])],
        spacemouse_buttons={"3": SpaceMouseButton(press=SpaceMouseAction("select"))},
        screen={"pages": ["dial"]}, overlay={"visibility": "fade"},
    ))
    return config


class SplitTest(unittest.TestCase):
    def test_every_field_is_machine_or_portable(self):
        names = set(_encoder(_default_config()))
        self.assertEqual(names, set(MACHINE_SECTIONS) | set(PORTABLE_SECTIONS) | {"spacemouse"})

    def test_spacemouse_fields_are_split_completely(self):
        fields = set(_encoder(_default_config())["spacemouse"])
        self.assertEqual(fields, set(SPACEMOUSE_MACHINE_FIELDS) | set(SPACEMOUSE_PORTABLE_FIELDS))
        self.assertFalse(set(SPACEMOUSE_MACHINE_FIELDS) & set(SPACEMOUSE_PORTABLE_FIELDS))

    def test_portable_config_holds_no_machine_setting(self):
        data = portable_config(other_machine())
        self.assertEqual(set(data), set(PORTABLE_SECTIONS) | {"spacemouse"})
        for name in MACHINE_SECTIONS:
            self.assertNotIn(name, data)
        for name in SPACEMOUSE_MACHINE_FIELDS:
            self.assertNotIn(name, data["spacemouse"])
        self.assertEqual(data["spacemouse"]["hold_ms"], 650)

    def test_document_is_json_with_the_marker(self):
        document = settings_document(other_machine())
        self.assertEqual(document[SETTINGS_MARKER], SETTINGS_FORMAT)
        self.assertEqual(json.loads(json.dumps(document)), document)


class MergeTest(unittest.TestCase):
    def test_import_replaces_the_portable_parts_and_keeps_the_machine(self):
        local, exported = _default_config(), other_machine()
        local.geometry.center_x = 42
        local.mouse.device = "Local mouse"
        merged = merge_portable(local, json.loads(json.dumps(settings_document(exported))))
        self.assertEqual(portable_config(merged), portable_config(exported))
        before = _encoder(local)
        after = _encoder(merged)
        for name in MACHINE_SECTIONS:
            self.assertEqual(after[name], before[name], name)
        for name in SPACEMOUSE_MACHINE_FIELDS:
            self.assertEqual(after["spacemouse"][name], before["spacemouse"][name], name)

    def test_the_local_configuration_is_not_changed(self):
        local = _default_config()
        before = copy.deepcopy(_encoder(local))
        merge_portable(local, other_machine())
        self.assertEqual(_encoder(local), before)

    def test_merge_accepts_a_whole_configuration(self):
        merged = merge_portable(_default_config(), other_machine())
        self.assertEqual(merged.spacemouse.hold_ms, 650)
        self.assertFalse(merged.spacemouse.enabled)
        self.assertEqual(merged.profiles[-1].name, "Blender")

    def test_missing_sections_stay_local(self):
        local = other_machine()
        merged = merge_portable(local, {"gesture": {"hysteresis": 2}})
        self.assertEqual(merged.gesture.hysteresis, 2)
        self.assertEqual(merged.profiles, local.profiles)
        self.assertEqual(merged.overlay, local.overlay)

    def test_round_trip_export_import_is_stable(self):
        config = other_machine()
        merged = merge_portable(_default_config(), settings_document(config))
        again = merge_portable(_default_config(), settings_document(merged))
        self.assertEqual(portable_config(again), portable_config(config))

    def test_values_are_checked_by_the_decoder(self):
        merged = merge_portable(_default_config(), {"spacemouse": {"hold_ms": 99999}, "overlay": {"scale": 99}})
        self.assertEqual(merged.spacemouse.hold_ms, 3000)
        self.assertEqual(merged.overlay.scale, 1.6)

    def test_garbage_raises_config_error(self):
        for bad in ({"profiles": [{"segments": []}]}, {"profiles": "x"}, {"spacemouse": {"buttons": 5}, "gesture": 3}):
            with self.assertRaises(ConfigError):
                merge_portable(_default_config(), bad)

    def test_imported_data_is_not_aliased(self):
        data = settings_document(other_machine())
        merged = merge_portable(_default_config(), data)
        data["profiles"][0]["name"] = "changed afterwards"
        self.assertNotEqual(merged.profiles[0].name, "changed afterwards")


class SettingsTextTest(unittest.TestCase):
    def test_reads_an_exported_file(self):
        text = json.dumps(settings_document(other_machine()))
        data = settings_from_text(text)
        self.assertEqual(set(data), set(PORTABLE_SECTIONS) | {"spacemouse"})

    def test_reads_a_whole_config_json(self):
        config = other_machine()
        data = settings_from_text(json.dumps(_encoder(config)))
        self.assertEqual(data, portable_config(config))

    def test_refuses_other_documents(self):
        for text, word in (
            ("{", "valid JSON"),
            ("[1]", "JSON object"),
            ('{"dialpad_profiles": 1, "profiles": []}', "Apps & Profiles"),
            ('{"something": 1}', "not a Turnpad settings file"),
            (json.dumps({SETTINGS_MARKER: SETTINGS_FORMAT + 1}), "newer version"),
            (json.dumps({SETTINGS_MARKER: "one"}), "unknown settings format"),
        ):
            with self.assertRaises(ConfigError) as caught:
                settings_from_text(text)
            self.assertIn(word, str(caught.exception))


class DescribeTest(unittest.TestCase):
    def test_no_difference(self):
        config = _default_config()
        self.assertEqual(describe_changes(config, merge_portable(config, config)), [])

    def test_lists_what_changes(self):
        local = _default_config()
        merged = merge_portable(local, other_machine())
        lines = describe_changes(local, merged)
        self.assertIn("Profiles: 1 new (Blender)", lines)
        for title in ("Dial look on the screen: changed", "Gesture feel: changed",
                      "SpaceMouse screen: changed", "SpaceMouse buttons and behaviour: changed"):
            self.assertIn(title, lines)

    def test_replaced_and_removed_profiles(self):
        local = other_machine()
        imported = _default_config()
        imported.profiles[0].sensitivity = 2.0
        lines = describe_changes(local, merge_portable(local, imported))
        profiles = next(line for line in lines if line.startswith("Profiles"))
        self.assertIn("1 replaced (Default)", profiles)
        self.assertIn("1 removed (Blender)", profiles)


class SetupDoneTest(unittest.TestCase):
    def test_new_configurations_start_without_it(self):
        self.assertFalse(_default_config().setup_done)
        self.assertFalse(DialPadConfig().setup_done)

    def test_an_older_file_counts_as_done(self):
        data = _encoder(_default_config())
        del data["setup_done"]
        self.assertTrue(config_from_text(json.dumps(data)).setup_done)

    def test_the_key_round_trips(self):
        for value in (True, False):
            config = _default_config()
            config.setup_done = value
            self.assertEqual(config_from_text(json.dumps(_encoder(config))).setup_done, value)


if __name__ == "__main__":
    unittest.main()
