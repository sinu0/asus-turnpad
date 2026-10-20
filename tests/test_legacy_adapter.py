import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.legacy_adapter import shortcuts_from_config  # noqa: E402
from config.schema import (  # noqa: E402
    ConfigError,
    DialPadConfig,
    MacroStep,
    ModifierActions,
    Profile,
    Segment,
    StepType,
    _decoder,
    _default_config,
    _encoder,
    overlay_rule,
    config_from_text,
    shell_commands,
)


def volume_segment():
    return Segment(
        title="Volume",
        macro_cw=[MacroStep(type=StepType.SHELL, script="true")],
        macro_ccw=[MacroStep(type=StepType.SHELL, script="true")],
        value_command="echo 42",
        unit="%",
        treshold=45,
    )


class LegacyAdapterTest(unittest.TestCase):
    def test_value_command_and_unit_reach_the_driver(self):
        config = DialPadConfig(profiles=[Profile(name="Default", segments=[volume_segment()])])
        segment = shortcuts_from_config(config)["none"]["Volume"]
        self.assertEqual(segment["value"], "echo 42")
        self.assertEqual(segment["unit"], "%")
        self.assertEqual(segment["treshold"], 45)

    def test_segment_without_value_has_no_value_key(self):
        config = DialPadConfig(profiles=[Profile(name="Default", segments=[Segment(title="Mute")])])
        segment = shortcuts_from_config(config)["none"]["Mute"]
        self.assertNotIn("value", segment)
        self.assertNotIn("unit", segment)

    def test_empty_app_profile_falls_back(self):
        config = DialPadConfig(profiles=[
            Profile(name="Firefox", app_id="firefox.desktop"),
            Profile(name="Default", segments=[volume_segment()]),
        ])
        shortcuts = shortcuts_from_config(config)
        self.assertNotIn("firefox.desktop", shortcuts)
        self.assertIn("Volume", shortcuts["none"])

    def test_profile_with_only_screen_overrides_stays_active(self):
        config = DialPadConfig(profiles=[
            Profile(name="Default", segments=[volume_segment()]),
            Profile(name="Blender", app_id="blender.desktop", screen={"pages": ["keys", "clock"]}),
            Profile(name="Empty", app_id="empty.desktop"),
        ])
        shortcuts = shortcuts_from_config(config)
        self.assertEqual(shortcuts["blender.desktop"]["_meta"]["name"], "Blender")
        self.assertIn("Volume", shortcuts["blender.desktop"])  # the dial keeps the default menu
        self.assertEqual(shortcuts["none"]["_meta"]["name"], "Default")
        self.assertNotIn("empty.desktop", shortcuts)

    def test_default_config(self):
        shortcuts = shortcuts_from_config(_default_config())
        self.assertIn("none", shortcuts)

    def test_disabled_segments_are_hidden(self):
        config = DialPadConfig(profiles=[Profile(name="Default", segments=[
            volume_segment(), Segment(title="Off", enabled=False),
        ])])
        self.assertNotIn("Off", shortcuts_from_config(config)["none"])

    def test_profile_meta_and_hints(self):
        from config.legacy_adapter import META_KEY

        segment = volume_segment()
        segment.macro_cw[0].title = "Louder"
        segment.color = "#123456"
        config = DialPadConfig(profiles=[Profile(name="Default", sensitivity=2.0, segments=[segment])])
        profile = shortcuts_from_config(config)["none"]
        self.assertEqual(profile[META_KEY]["sensitivity"], 2.0)
        self.assertEqual(profile["Volume"]["hints"]["cw"], "Louder")
        self.assertEqual(profile["Volume"]["color"], "#123456")

    def test_single_mode_uses_flat_layout(self):
        from config.legacy_adapter import META_KEY

        config = DialPadConfig(profiles=[Profile(name="Default", mode="single", segments=[volume_segment()])])
        profile = shortcuts_from_config(config)["none"]
        self.assertNotIn("Volume", profile)
        self.assertTrue(profile["clockwise"])
        self.assertEqual(profile[META_KEY]["single"]["treshold"], 45)
        self.assertEqual(profile[META_KEY]["single"]["value"], "echo 42")


class SchemaRoundTripTest(unittest.TestCase):
    def test_new_fields_survive_save_and_load(self):
        from config.schema import OverlayConfig, _decoder, _encoder

        step = MacroStep(type=StepType.SCROLL, direction="clockwise", value=60,
                         axis="horizontal", modifiers=["KEY_LEFTCTRL"])
        config = DialPadConfig(
            overlay=OverlayConfig(scale=1.2, position="bottom"),
            profiles=[Profile(name="P", mode="single", single_function="S", scroll_speed=1.5,
                              remember_last=False,
                              segments=[Segment(title="S", color="#abcdef", enabled=False, macro_cw=[step])])],
        )
        loaded = _decoder(_encoder(config), DialPadConfig)
        self.assertEqual(loaded, config)

    def test_old_files_get_defaults_and_bad_values_are_clamped(self):
        from config.schema import _decoder

        loaded = _decoder({
            "geometry": {"width": 1, "height": 1, "circle_diameter": 1, "center_button_diameter": 1,
                         "center_x": 1, "center_y": 1},
            "overlay": {"scale": 9, "position": "nowhere"},
            "profiles": [{"name": "Old", "mode": "weird", "sensitivity": "x",
                          "segments": [{"title": "T", "unknown": 1}]}],
        }, DialPadConfig)
        self.assertEqual(loaded.overlay.scale, 1.6)
        self.assertEqual(loaded.overlay.position, "lower")
        self.assertEqual(loaded.profiles[0].mode, "multi")
        self.assertEqual(loaded.profiles[0].sensitivity, 1.0)
        self.assertTrue(loaded.profiles[0].segments[0].enabled)


class PresetsTest(unittest.TestCase):
    def test_library_presets_are_valid_and_labelled(self):
        from config.labels import describe_macro
        from config.presets import APP_PACKS, FUNCTION_LIBRARY

        segments = [preset.factory() for preset in FUNCTION_LIBRARY]
        for pack in APP_PACKS:
            segments.extend(pack.factory())
        # A group has no actions; its functions have.
        groups = [segment for segment in segments if segment.is_group]
        self.assertTrue(all(group.items for group in groups))
        segments = [item for segment in segments for item in (segment.items if segment.is_group else [segment])]
        for segment in segments:
            self.assertTrue(segment.macro_cw or segment.macro_center, segment.title)
            for step in segment.macro_cw + segment.macro_ccw:
                self.assertTrue(describe_macro([step]), segment.title)

    def test_packs_match_desktop_ids(self):
        from config.presets import pack_for_app, profile_for_app

        self.assertEqual(pack_for_app("org.kde.krita.desktop").name, "Krita")
        self.assertEqual(pack_for_app("firefox.desktop").name, "Browser")
        self.assertIsNone(pack_for_app("org.gnome.Calculator.desktop"))
        self.assertTrue(profile_for_app("darktable", "org.darktable.darktable.desktop").segments)
        self.assertEqual(pack_for_app("com.obsproject.Studio.desktop").name, "OBS Studio")

    def test_pack_titles_are_unique(self):
        from config.presets import APP_PACKS

        for pack in APP_PACKS:
            titles = [segment.title for segment in pack.factory()]
            self.assertEqual(len(titles), len(set(titles)), pack.name)



class DialLookTest(unittest.TestCase):
    """Profile and function overrides of where and how the dial shows."""

    def config(self):
        zoom = Segment(title="Zoom", macro_cw=[MacroStep(type=StepType.SHELL, script="true")],
                       overlay={"visibility": "pill"})
        return DialPadConfig(profiles=[
            Profile(name="Default", segments=[zoom], overlay={"position": "custom", "custom_x": 0.9, "custom_y": 0.1}),
            Profile(name="Player", app_id="mpv.desktop", mode="single", segments=[volume_segment()],
                    overlay={"visibility": "fade", "fade_opacity": 20}),
        ])

    def test_overrides_reach_the_driver(self):
        shortcuts = shortcuts_from_config(self.config())
        menu = shortcuts["none"]
        self.assertEqual(menu["_meta"]["overlay"], {"position": "custom", "custom_x": 0.9, "custom_y": 0.1})
        self.assertEqual(menu["Zoom"]["overlay"], {"visibility": "pill"})
        self.assertEqual(shortcuts["mpv.desktop"]["_meta"]["overlay"], {"visibility": "fade", "fade_opacity": 20})
        self.assertNotIn("overlay", shortcuts["mpv.desktop"]["_meta"]["single"])

    def test_saved_and_read_back_and_invalid_values_dropped(self):
        data = _encoder(self.config())
        self.assertNotIn("overlay", data["profiles"][1]["segments"][0])
        data["profiles"][0]["overlay"].update({"visibility": "loud", "custom_y": 4, "fade_opacity": "x"})
        loaded = _decoder(data, DialPadConfig)
        self.assertEqual(loaded.profiles[0].overlay, {"position": "custom", "custom_x": 0.9, "custom_y": 1.0})
        self.assertEqual(loaded.profiles[0].segments[0].overlay, {"visibility": "pill"})

    def test_defaults_are_validated(self):
        data = _encoder(_default_config())
        data["overlay"].update({"position": "custom", "custom_x": -2, "visibility": "nope", "fade_opacity": 250})
        overlay = _decoder(data, DialPadConfig).overlay
        self.assertEqual((overlay.position, overlay.custom_x, overlay.visibility, overlay.fade_opacity),
                         ("custom", 0.0, "full", 100))

    def test_near_the_pointer(self):
        data = _encoder(_default_config())
        self.assertEqual((data["overlay"]["pointer_side"], data["overlay"]["pointer_gap"]), ("below-right", 24))
        data["overlay"].update({"position": "pointer", "pointer_side": "above", "pointer_gap": 1000})
        overlay = _decoder(data, DialPadConfig).overlay
        self.assertEqual((overlay.position, overlay.pointer_side, overlay.pointer_gap), ("pointer", "above", 400))
        data["overlay"].update({"pointer_side": "sideways", "pointer_gap": "far"})
        overlay = _decoder(data, DialPadConfig).overlay
        self.assertEqual((overlay.pointer_side, overlay.pointer_gap), ("below-right", 24))
        rule = {"position": "pointer", "pointer_side": "left", "pointer_gap": 12.7}
        self.assertEqual(overlay_rule(rule), {"position": "pointer", "pointer_side": "left", "pointer_gap": 12})
        self.assertEqual(overlay_rule({"pointer_side": ["left"], "pointer_gap": True}), {})
        self.assertEqual(overlay_rule({"pointer_gap": -5}), {"pointer_gap": 0})



class GroupTest(unittest.TestCase):
    """A group: a menu sector that opens a list of more functions."""

    def config(self):
        group = Segment(title="Display", color="#f4c95d", overlay={"position": "top"}, items=[
            volume_segment(),
            Segment(title="Off", enabled=False),
            Segment(title="Mute", macro_center=[MacroStep(type=StepType.SHELL, script="mute")]),
        ])
        return DialPadConfig(profiles=[
            Profile(name="Default", segments=[group, Segment(title="Empty", items=[]), Segment(title="Zoom")]),
            Profile(name="Player", app_id="mpv.desktop", mode="single", segments=[group, volume_segment()]),
        ])

    def test_group_reaches_the_driver_with_its_enabled_functions(self):
        menu = shortcuts_from_config(self.config())["none"]
        group = menu["Display"]
        self.assertEqual(list(group["items"]), ["Volume", "Mute"])
        self.assertEqual(group["icon"], "view-list-bullet-symbolic")
        self.assertEqual((group["clockwise"], group["counterclockwise"], group["center"]), ([], [], []))
        self.assertEqual(group["overlay"], {"position": "top"})
        self.assertEqual(group["items"]["Volume"]["value"], "echo 42")
        self.assertTrue(group["items"]["Mute"]["center"])
        # A group without functions to open is left out.
        self.assertNotIn("Empty", menu)
        self.assertIn("Zoom", menu)

    def test_single_mode_binds_a_function_not_a_group(self):
        single = shortcuts_from_config(self.config())["mpv.desktop"]["_meta"]["single"]
        self.assertEqual(single["title"], "Volume")

    def test_saved_and_read_back_one_level_deep(self):
        data = _encoder(self.config())
        group = data["profiles"][0]["segments"][0]
        self.assertEqual([item["title"] for item in group["items"]], ["Volume", "Off", "Mute"])
        self.assertNotIn("items", data["profiles"][0]["segments"][2])
        group["items"][0]["items"] = [{"title": "Too deep"}]
        loaded = _decoder(data, DialPadConfig)
        display = loaded.profiles[0].segments[0]
        self.assertTrue(display.is_group)
        self.assertIsNone(display.items[0].items)
        self.assertEqual([item.title for item in display.active_items()], ["Volume", "Mute"])
        self.assertTrue(loaded.profiles[0].segments[1].is_group)
        self.assertFalse(loaded.profiles[0].segments[2].is_group)


class ModifierVariantTest(unittest.TestCase):
    """Other actions of a function while Ctrl, Shift or Alt is held."""

    def segment(self):
        return Segment(
            title="Volume",
            macro_cw=[MacroStep(type=StepType.VOLUME, direction="up", value=1)],
            macro_center=[MacroStep(type=StepType.MEDIA, keys=["KEY_MUTE"])],
            with_modifier={
                "Ctrl": ModifierActions(macro_center=[MacroStep(type=StepType.SHELL, script="pavucontrol")]),
                "Alt": ModifierActions(),
            },
        )

    def test_driver_gets_variants_after_the_plain_entries(self):
        config = DialPadConfig(profiles=[Profile(name="Default", segments=[self.segment()])])
        volume = shortcuts_from_config(config)["none"]["Volume"]
        self.assertEqual([entry.get("modifier") for entry in volume["center"]],
                         [None, ["KEY_LEFTCTRL", "KEY_RIGHTCTRL"]])
        self.assertEqual(volume["center"][1]["macro"][0].script, "pavucontrol")
        # Nothing with Ctrl for turning: the plain entry stays alone.
        self.assertEqual(len(volume["clockwise"]), 1)

    def test_saved_without_empty_variants_and_read_back(self):
        data = _encoder(self.segment())
        self.assertEqual(list(data["with_modifier"]), ["Ctrl"])
        self.assertEqual(list(data["with_modifier"]["Ctrl"]), ["macro_center"])
        data["with_modifier"]["Hyper"] = {"macro_cw": [{"type": "shell", "script": "x"}]}
        loaded = _decoder(data, Segment)
        self.assertEqual(list(loaded.with_modifier), ["Ctrl"])
        self.assertEqual(loaded.with_modifier["Ctrl"].macro_center[0].script, "pavucontrol")
        self.assertNotIn("with_modifier", _encoder(Segment(title="Plain")))

    def test_imported_commands_include_variants(self):
        commands = shell_commands([Profile(name="Default", segments=[self.segment()])])
        self.assertIn(("Default", "Volume", "pavucontrol"), commands)

    def test_a_function_turning_only_with_a_key_is_no_launcher(self):
        segment = Segment(title="Brush", with_modifier={
            "Shift": ModifierActions(macro_cw=[MacroStep(type=StepType.KEYSTROKE, keys=["KEY_RIGHTBRACE"])]),
        })
        self.assertTrue(segment.turns())
        self.assertFalse(Segment(title="Launcher").turns())


class ConfigTextTest(unittest.TestCase):
    """config.json the driver cannot use is refused with a readable reason."""

    def test_reads_a_saved_configuration(self):
        text = json.dumps(_encoder(_default_config()))
        self.assertEqual(config_from_text(text).profiles[0].name, "Default")

    def test_explains_unusable_files(self):
        valid = _encoder(_default_config())
        del valid["geometry"]
        cases = {
            '{"version": 2,': "not valid JSON",
            "[1, 2]": "no JSON object",
            json.dumps(valid): "missing 'geometry'",
        }
        for text, message in cases.items():
            with self.subTest(text=text[:30]):
                with self.assertRaises(ConfigError) as caught:
                    config_from_text(text)
                self.assertIn(message, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
