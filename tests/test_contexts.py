"""Profile contexts: variants of an application's profile by window title and by
the mode the linked application reports. Schema, legacy adapter, profile text
and the driver's selection (the state functions extracted from dialpad.py)."""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config.legacy_adapter import CONTEXTS_KEY, META_KEY, shortcuts_for_context, shortcuts_from_config  # noqa: E402
from config.presets import tracks, vertical_scroll, volume, zoom_wheel  # noqa: E402
from config.profile_share import PROFILE_FORMAT, ProfileTextError, profiles_from_text  # noqa: E402
from config.schema import (  # noqa: E402
    CONTEXT_TITLE_MAX, CONTEXTS_MAX, DialPadConfig, MacroStep, Profile, ProfileContext, Segment, SpaceMouseAction,
    SpaceMouseButton, StepType, _decoder, _encoder, config_from_text, context_title_error, matching_context,
    merge_spacemouse_layers, shell_commands, spacemouse_binds,
)
from tests.test_driver_control import DriverCase  # noqa: E402


def press(action_type, **values):
    return SpaceMouseButton(press=SpaceMouseAction(action_type, **values))


class MatchingTest(unittest.TestCase):
    def test_title_is_a_case_insensitive_text_search(self):
        context = ProfileContext("Docs", title=".pdf")
        self.assertTrue(context.matches("Manual.PDF - Viewer", ""))
        self.assertFalse(context.matches("Manual.png", ""))
        self.assertFalse(context.matches("Manualxpdf", ""))  # a dot is a dot
        self.assertFalse(context.matches(None, ""))

    def test_alternatives_are_separated_by_a_bar(self):
        context = ProfileContext("Either", title=" Cube | ,, |sphere||")
        self.assertTrue(context.matches("My CUBE.blend", ""))
        self.assertTrue(context.matches("a Sphere", ""))
        self.assertFalse(context.matches("Cone", ""))
        self.assertTrue(context.matches("a ,, b", ""))  # only empty parts are ignored

    def test_regular_expression_text_is_literal_and_fast(self):
        import time
        context = ProfileContext("Evil", title="(a|a)*b")
        started = time.monotonic()
        self.assertFalse(context.matches("a" * 30, ""))
        self.assertFalse(context.matches("a" * 499 + "b", ""))
        self.assertTrue(context.matches("see (a)*b here", ""))
        self.assertTrue(ProfileContext("Dot", title="^bash$").matches("x ^bash$ y", ""))
        self.assertFalse(ProfileContext("Dot", title="^bash$").matches("bash", ""))
        self.assertLess(time.monotonic() - started, 0.5)

    def test_modes_are_exact(self):
        context = ProfileContext("Edit", app_modes=["EDIT_MESH", "EDIT_CURVE"])
        self.assertTrue(context.matches("anything", "EDIT_CURVE"))
        self.assertFalse(context.matches("anything", "OBJECT"))
        self.assertFalse(context.matches("anything", None))
        self.assertFalse(context.matches("anything", "edit_mesh"))

    def test_every_given_condition_must_hold(self):
        context = ProfileContext("Both", title="cube", app_modes=["SCULPT"])
        self.assertTrue(context.matches("Cube.blend", "SCULPT"))
        self.assertFalse(context.matches("Sphere.blend", "SCULPT"))
        self.assertFalse(context.matches("Cube.blend", "OBJECT"))

    def test_a_context_without_conditions_never_matches(self):
        context = ProfileContext("Nothing")
        self.assertFalse(context.has_conditions())
        self.assertFalse(context.matches("anything", "OBJECT"))
        self.assertFalse(context.matches("", ""))

    def test_a_broken_pattern_never_matches(self):
        for pattern in (" | ", "", "x" * (CONTEXT_TITLE_MAX + 1)):
            with self.subTest(pattern=pattern[:20]):
                self.assertIsNotNone(context_title_error(pattern))
                self.assertFalse(ProfileContext("Broken", title=pattern).matches("aaaa", ""))

    def test_pattern_errors_are_explained(self):
        self.assertIsNone(context_title_error(r"^Untitled \d+$"))  # any text is fine, it is literal
        self.assertIsNone(context_title_error("[a-"))
        self.assertIn("no text", context_title_error(" | "))
        self.assertIn("longer than", context_title_error("a" * 201))
        self.assertIn("must be text", context_title_error(5))

    def test_the_first_match_wins_and_none_means_the_profile(self):
        first = ProfileContext("First", app_modes=["EDIT_MESH"])
        second = ProfileContext("Second", app_modes=["EDIT_MESH", "SCULPT"])
        self.assertIs(matching_context([first, second], "", "EDIT_MESH"), first)
        self.assertIs(matching_context([first, second], "", "SCULPT"), second)
        self.assertIsNone(matching_context([first, second], "", "OBJECT"))
        self.assertIsNone(matching_context([], "", "OBJECT"))

    def test_a_very_long_title_is_cut_before_matching(self):
        context = ProfileContext("Tail", title="needle")
        self.assertFalse(context.matches("x" * 600 + "needle", ""))
        self.assertTrue(context.matches("needle" + "x" * 600, ""))


class SchemaTest(unittest.TestCase):
    def make_profile(self):
        return Profile(
            name="Blender", app_id="org.blender.Blender.desktop", segments=[volume()],
            contexts=[
                ProfileContext(
                    "Edit mode", title="edit", app_modes=["EDIT_MESH"], segments=[zoom_wheel(), tracks()],
                    spacemouse_buttons={"3": press("app", command="mesh.extrude_region_move", keys=["KEY_E"])},
                    spacemouse_layers={"Shift layer": {"4": press("dial")}},
                    screen={"start_page": "keys"},
                ),
                ProfileContext("Keep menu", app_modes=["SCULPT"]),
            ],
        )

    def test_round_trip(self):
        profile = self.make_profile()
        data = json.loads(json.dumps(_encoder(profile)))
        again = _decoder(data, Profile)
        self.assertEqual(_encoder(again), _encoder(profile))
        edit, keep = again.contexts
        self.assertEqual((edit.name, edit.title, edit.app_modes), ("Edit mode", "edit", ["EDIT_MESH"]))
        self.assertEqual([segment.title for segment in edit.segments], [zoom_wheel().title, tracks().title])
        self.assertIsNone(keep.segments)
        self.assertEqual(edit.spacemouse_buttons["3"].press.command, "mesh.extrude_region_move")
        self.assertIn("4", edit.spacemouse_layers["Shift layer"])
        self.assertEqual(edit.screen, {"start_page": "keys"})

    def test_unset_parts_are_not_written(self):
        data = _encoder(ProfileContext("Only modes", app_modes=["POSE"]))
        self.assertEqual(data, {"name": "Only modes", "app_modes": ["POSE"]})
        self.assertNotIn("contexts", _encoder(Profile(name="Plain")))

    def test_decoder_is_forgiving(self):
        profile = _decoder({
            "name": "P", "segments": [],
            "contexts": [
                {"name": "  "}, {"title": "no name"}, "text", None,
                {"name": "ok", "title": "t" * 400, "app_modes": ["A", 5, "", " B "], "segments": "no",
                 "spacemouse_buttons": "no", "screen": {"pages": ["bogus"]}},
            ],
        }, Profile)
        self.assertEqual(len(profile.contexts), 1)
        context = profile.contexts[0]
        self.assertEqual(len(context.title), CONTEXT_TITLE_MAX)
        self.assertEqual(context.app_modes, ["A", "B"])
        self.assertIsNone(context.segments)
        self.assertEqual(context.spacemouse_buttons, {})
        self.assertEqual(context.screen, {})

    def test_at_most_the_limit(self):
        data = {"name": "P", "segments": [], "contexts": [{"name": "c%d" % number, "title": "x"} for number in range(CONTEXTS_MAX + 5)]}
        self.assertEqual(len(_decoder(data, Profile).contexts), CONTEXTS_MAX)

    def test_a_config_file_carries_them(self):
        config = DialPadConfig(profiles=[self.make_profile()])
        again = config_from_text(json.dumps(_encoder(config)))
        self.assertEqual(again.profiles[0].contexts[0].name, "Edit mode")

    def test_programs_in_a_context_are_listed_for_the_import_check(self):
        profile = self.make_profile()
        shell = MacroStep(type=StepType.SHELL, script="rm -rf /tmp/x")
        profile.contexts[0].segments[0].macro_center = [shell]
        profile.contexts[0].spacemouse_buttons["5"] = SpaceMouseButton(press=SpaceMouseAction(
            "macro", macro=[MacroStep(type=StepType.SHELL, script="notify-send hi")]))
        found = shell_commands([profile])
        self.assertIn(("Blender › Edit mode", zoom_wheel().title, "rm -rf /tmp/x"), found)
        self.assertIn(("Blender › Edit mode", "SpaceMouse button 5", "notify-send hi"), found)


class AdapterTest(unittest.TestCase):
    def shortcuts(self, profile, default=True):
        profiles = [Profile(name="Default", segments=[volume(), vertical_scroll()])] if default else []
        return shortcuts_from_config(DialPadConfig(profiles=[*profiles, profile]))

    def titles(self, shortcuts):
        return [key for key in shortcuts if key not in ("center", "clockwise", "counterclockwise") and not key.startswith("_")]

    def test_each_context_has_a_shortcut_set_of_its_own(self):
        profile = Profile(
            name="Blender", app_id="org.blender.Blender.desktop", segments=[volume(), vertical_scroll()],
            spacemouse_buttons={"1": press("dial"), "2": press("select")},
            contexts=[
                ProfileContext("Own menu", app_modes=["EDIT_MESH"], segments=[zoom_wheel(), tracks()],
                               spacemouse_buttons={"2": press("back"), "3": press("select")}),
                ProfileContext("Same menu", title="sculpt"),
            ],
        )
        shortcuts = self.shortcuts(profile)["org.blender.Blender.desktop"]
        own, same = shortcuts[CONTEXTS_KEY]
        self.assertEqual(self.titles(own["shortcuts"]), [zoom_wheel().title, tracks().title])
        self.assertEqual(self.titles(same["shortcuts"]), self.titles(shortcuts))
        self.assertIsNot(own["shortcuts"][META_KEY], shortcuts[META_KEY])
        self.assertIsNot(same["shortcuts"][META_KEY], shortcuts[META_KEY])
        # The context's buttons lie over the profile's, button by button.
        buttons = own["shortcuts"][META_KEY]["spacemouse_buttons"]
        self.assertEqual({key: button.press.type for key, button in buttons.items()},
                         {"1": "dial", "2": "back", "3": "select"})
        same_buttons = same["shortcuts"][META_KEY]["spacemouse_buttons"]
        self.assertEqual({key: button.press.type for key, button in same_buttons.items()}, {"1": "dial", "2": "select"})
        self.assertEqual(own["shortcuts"][META_KEY]["context"], "Own menu")
        self.assertNotIn("context", shortcuts[META_KEY])
        # The profile's own set is untouched.
        self.assertEqual({key: button.press.type for key, button in shortcuts[META_KEY]["spacemouse_buttons"].items()},
                         {"1": "dial", "2": "select"})

    def test_layers_screen_and_motion_follow_the_profile(self):
        profile = Profile(
            name="Blender", app_id="org.blender.Blender.desktop", segments=[volume()],
            spacemouse_layers={"Shift layer": {"1": press("dial")}, "Other": {"9": press("back")}},
            spacemouse_motion={"sensitivity": 2.0}, screen={"accent": "#112233"},
            contexts=[ProfileContext("Edit", app_modes=["EDIT_MESH"],
                                     spacemouse_layers={"Shift layer": {"2": press("select")}},
                                     screen={"start_page": "keys"})],
        )
        shortcuts = self.shortcuts(profile)["org.blender.Blender.desktop"]
        meta = shortcuts[CONTEXTS_KEY][0]["shortcuts"][META_KEY]
        self.assertEqual(sorted(meta["spacemouse_layers"]), ["Other", "Shift layer"])
        self.assertEqual(sorted(meta["spacemouse_layers"]["Shift layer"]), ["1", "2"])
        self.assertEqual(meta["screen"], {"accent": "#112233", "start_page": "keys"})
        self.assertEqual(meta["spacemouse_motion"], {"sensitivity": 2.0})
        # The profile's own layers were not changed by the merge.
        self.assertEqual(sorted(shortcuts[META_KEY]["spacemouse_layers"]["Shift layer"]), ["1"])

    def test_selection_by_title_and_mode(self):
        profile = Profile(
            name="Blender", app_id="org.blender.Blender.desktop", segments=[volume()],
            contexts=[ProfileContext("Edit", app_modes=["EDIT_MESH"]), ProfileContext("Docs", title="manual")],
        )
        shortcuts = self.shortcuts(profile)["org.blender.Blender.desktop"]
        self.assertEqual(shortcuts_for_context(shortcuts, "x", "EDIT_MESH")[0], "Edit")
        self.assertEqual(shortcuts_for_context(shortcuts, "The Manual", "OBJECT")[0], "Docs")
        self.assertEqual(shortcuts_for_context(shortcuts, "The Manual", "EDIT_MESH")[0], "Edit")  # first wins
        name, plain = shortcuts_for_context(shortcuts, "x", "OBJECT")
        self.assertEqual((name, plain is shortcuts), ("", True))
        self.assertEqual(shortcuts_for_context({"center": []}, "x", "OBJECT")[0], "")

    def test_contexts_without_conditions_are_left_out(self):
        profile = Profile(name="B", app_id="b.desktop", segments=[volume()], contexts=[ProfileContext("Nothing")])
        self.assertNotIn(CONTEXTS_KEY, self.shortcuts(profile)["b.desktop"])

    def test_the_default_profile_can_have_contexts(self):
        profile = Profile(name="Default", segments=[volume()], contexts=[ProfileContext("Terminal", title="bash")])
        shortcuts = shortcuts_from_config(DialPadConfig(profiles=[profile]))
        self.assertEqual(shortcuts_for_context(shortcuts["none"], "~ bash", "")[0], "Terminal")

    def test_a_profile_without_a_menu_but_with_contexts_keeps_the_default_menu(self):
        profile = Profile(
            name="Blender", app_id="org.blender.Blender.desktop",
            contexts=[ProfileContext("Edit", app_modes=["EDIT_MESH"], segments=[zoom_wheel()]),
                      ProfileContext("Other", app_modes=["POSE"], spacemouse_buttons={"1": press("dial")})],
        )
        shortcuts = self.shortcuts(profile)["org.blender.Blender.desktop"]
        self.assertEqual(self.titles(shortcuts), [volume().title, vertical_scroll().title])
        edit, other = shortcuts[CONTEXTS_KEY]
        self.assertEqual(self.titles(edit["shortcuts"]), [zoom_wheel().title])
        self.assertEqual(self.titles(other["shortcuts"]), [volume().title, vertical_scroll().title])
        # The default profile's set carries none of Blender's contexts.
        default = shortcuts_from_config(DialPadConfig(profiles=[
            Profile(name="Default", segments=[volume()]), profile]))["none"]
        self.assertNotIn(CONTEXTS_KEY, default)

    def test_a_context_menu_without_functions_keeps_the_profiles(self):
        profile = Profile(name="B", app_id="b.desktop", segments=[volume()],
                          contexts=[ProfileContext("Empty", title="x", segments=[])])
        context = self.shortcuts(profile)["b.desktop"][CONTEXTS_KEY][0]["shortcuts"]
        self.assertEqual(self.titles(context), [volume().title])


class ProfileTextTest(unittest.TestCase):
    def parse(self, **extra):
        data = {"name": "Blender", "app_id": "blender.desktop", "segments": [{"title": "Zoom", "macro_cw": []}], **extra}
        return profiles_from_text(json.dumps(data))[0]

    def test_the_format_text_documents_contexts(self):
        self.assertIn('"contexts"', PROFILE_FORMAT)
        self.assertIn("app_modes", PROFILE_FORMAT)
        self.assertIn('"type": "app"', PROFILE_FORMAT)

    def test_a_valid_context_is_read_and_cleaned(self):
        profile = self.parse(contexts=[{
            "name": "Edit", "title": "^cube", "app_modes": ["EDIT_MESH"],
            "segments": [{"title": "Move", "macro_cw": [{"type": "keystroke", "keys": ["Ctrl", "Z"]}]}],
            "spacemouse_buttons": {"3": {"press": {"type": "app", "command": "mesh.extrude", "keys": ["e"]}}},
        }])
        context = profile.contexts[0]
        self.assertEqual(context.segments[0].macro_cw[0].keys, ["KEY_LEFTCTRL", "KEY_Z"])
        self.assertEqual(context.spacemouse_buttons["3"].press.keys, ["KEY_E"])

    def test_problems_are_explained(self):
        cases = [
            ([{"name": "x", "title": " | "}], "no text"),
            ([{"name": "x", "title": "t" * 201}], "longer than"),
            ([{"name": "x"}], "never applies"),
            ([{"title": "x"}], "no \"name\""),
            ([{"name": "x", "app_modes": "EDIT_MESH"}], "app_modes"),
            ([{"name": "x", "title": "t", "segments": [{"macro_cw": []}]}], "title"),
            ([{"name": "x", "title": "t", "spacemouse_buttons": {"1": {"press": {"type": "bogus"}}}}], "bogus"),
            ([{"name": "x", "title": "t", "spacemouse_buttons": {"1": {"press": {"type": "macro", "keys": ["KEY_NOPE"]}}}}], "unknown key"),
            ("text", "list"),
        ]
        for contexts, message in cases:
            with self.subTest(contexts=str(contexts)[:50]):
                with self.assertRaisesRegex(ProfileTextError, message):
                    self.parse(contexts=contexts)

    def test_too_many_contexts(self):
        with self.assertRaisesRegex(ProfileTextError, "at most"):
            self.parse(contexts=[{"name": "c%d" % n, "title": "x"} for n in range(CONTEXTS_MAX + 1)])


class DriverContextTest(DriverCase):
    """The driver with a Blender profile that has contexts, Blender in focus."""

    BLENDER = "org.blender.blender.desktop"

    def setUp(self):
        super().setUp()
        self.blender = Profile(
            name="Blender", app_id="org.blender.Blender.desktop",
            segments=[zoom_wheel(), volume()],
            spacemouse_buttons={"1": press("dial")},
            contexts=[
                ProfileContext("Edit", app_modes=["EDIT_MESH"], segments=[tracks(), volume()],
                               spacemouse_buttons={"1": press("select")}),
                ProfileContext("Sculpt", app_modes=["SCULPT"], spacemouse_buttons={"2": press("back")}),
                ProfileContext("Manual", title="manual"),
            ],
        )
        self.config.profiles.append(self.blender)
        self.driver["gui_config"] = self.config
        self.driver["app_shortcuts"] = shortcuts_from_config(self.config)
        self.link = FakeLink()
        self.driver["app_link"] = self.link
        self.driver["window_was_changed"](self.BLENDER)

    def state(self):
        return self.driver["driver_state"]()

    def push(self, app, title):
        self.driver["on_dbus_active_window"](app, title)
        self.process()

    def test_no_condition_met_is_the_plain_profile(self):
        self.assertEqual(self.driver["app_name"], "org.blender.Blender.desktop")
        self.assertEqual(self.driver["context_name"], "")
        self.assertEqual(self.state()["context"], "")
        self.assertEqual(self.driver["multi_app_mode_titles"][:2], [zoom_wheel().title, volume().title])

    def test_the_mode_of_the_linked_application_selects_a_context(self):
        self.link.context = {"mode": "EDIT_MESH", "label": "Edit Mesh", "detail": "Vertex"}
        self.driver["on_app_link_change"]("context")
        self.assertTrue(self.driver["pending_context_refresh"])
        self.process()
        self.assertEqual(self.driver["context_name"], "Edit")
        self.assertEqual(self.state()["context"], "Edit")
        self.assertEqual(self.state()["app_context"]["label"], "Edit Mesh")
        self.assertEqual(self.driver["multi_app_mode_titles"][:2], [tracks().title, volume().title])
        self.assertEqual(self.driver["profile_meta"]()["context"], "Edit")
        # Back to object mode: the plain profile.
        self.link.context = {"mode": "OBJECT", "label": "Object Mode", "detail": ""}
        self.driver["on_app_link_change"]("context")
        self.process()
        self.assertEqual(self.driver["context_name"], "")
        self.assertEqual(self.driver["multi_app_mode_titles"][:2], [zoom_wheel().title, volume().title])
        self.assertNotIn("context", self.driver["profile_meta"]())

    def test_the_title_comes_with_a_push_and_can_change_inside_the_same_window(self):
        self.push(self.BLENDER, "User Manual - Blender")
        self.assertEqual(self.driver["context_name"], "Manual")
        self.push(self.BLENDER, "Cube.blend - Blender")
        self.assertEqual(self.driver["context_name"], "")
        self.assertEqual(self.driver["pushed_window_title"], "Cube.blend - Blender")

    def test_a_push_waits_for_a_finger_on_the_dial(self):
        self.driver["on_dbus_active_window"](self.BLENDER, "manual")
        self.process(gesture_idle=False)
        self.assertEqual(self.driver["context_name"], "")
        self.process()
        self.assertEqual(self.driver["context_name"], "Manual")

    def test_an_application_change_without_title_goes_back_to_the_polled_one(self):
        self.push(self.BLENDER, "manual")
        self.assertEqual(self.driver["context_name"], "Manual")
        self.driver["window_title"] = "something else"
        self.driver["on_dbus_active_application"](self.BLENDER)
        self.process()
        self.assertIsNone(self.driver["pushed_window_title"])
        self.assertEqual(self.driver["context_name"], "")

    def test_the_first_matching_context_wins(self):
        self.link.context = {"mode": "SCULPT", "label": "", "detail": ""}
        self.push(self.BLENDER, "Manual")
        self.assertEqual(self.driver["context_name"], "Sculpt")

    def test_other_applications_have_no_contexts(self):
        self.link.context = {"mode": "EDIT_MESH", "label": "", "detail": ""}
        self.push("org.kde.krita.desktop", "manual")
        self.assertEqual(self.driver["app_name"], "org.kde.krita.desktop")
        self.assertEqual(self.driver["context_name"], "")

    def test_the_context_buttons_are_the_ones_in_force(self):
        def types():
            return {key: button.press.type for key, button in self.driver["spacemouse_effective_buttons"]().items()
                    if key in ("1", "2")}

        self.assertEqual(types(), {"1": "dial", "2": "modifier"})
        plain_meta = self.driver["profile_meta"]()
        self.link.context = {"mode": "EDIT_MESH"}
        self.driver["on_app_link_change"]("context")
        self.process()
        self.assertEqual(types(), {"1": "select", "2": "modifier"})
        # D2 re-syncs hidden buttons and ends latched layers when this object changes.
        self.assertIsNot(self.driver["profile_meta"](), plain_meta)
        self.link.context = {"mode": "SCULPT"}
        self.driver["on_app_link_change"]("context")
        self.process()
        self.assertEqual(types(), {"1": "dial", "2": "back"})

    def test_a_context_with_the_same_menu_keeps_the_selected_function(self):
        self.driver["dialpad"] = True
        self.driver["title"] = zoom_wheel().title
        self.driver["select_multifunction_segment"]()
        self.assertTrue(self.driver["center_activated"])
        self.link.context = {"mode": "SCULPT"}
        self.driver["on_app_link_change"]("context")
        self.process()
        self.assertEqual(self.driver["context_name"], "Sculpt")
        self.assertTrue(self.driver["center_activated"])
        self.assertEqual(self.driver["title"], zoom_wheel().title)

    def test_a_context_with_another_menu_returns_to_it(self):
        self.driver["dialpad"] = True
        self.driver["title"] = zoom_wheel().title
        self.driver["select_multifunction_segment"]()
        self.link.context = {"mode": "EDIT_MESH"}
        self.driver["on_app_link_change"]("context")
        self.process()
        self.assertFalse(self.driver["center_activated"])

    def test_the_last_function_is_remembered_per_context(self):
        self.driver["dialpad"] = True
        self.driver["title"] = zoom_wheel().title
        self.driver["select_multifunction_segment"]()
        remembered = self.driver["last_function_by_app"]
        self.assertIn("org.blender.Blender.desktop", remembered)
        self.link.context = {"mode": "SCULPT"}
        self.driver["on_app_link_change"]("context")
        self.process()
        self.driver["title"] = volume().title
        self.driver["select_multifunction_segment"]()
        self.assertIn(("org.blender.Blender.desktop", "Sculpt"), remembered)
        self.assertEqual(remembered["org.blender.Blender.desktop"][1], zoom_wheel().title)

    def test_link_changes_are_published(self):
        self.driver["on_app_link_change"]("hello")
        self.assertTrue(self.states)


class ButtonsOnlyProfileTest(DriverCase):
    """A Blender profile with only motion and a context: the default profile's menu is
    shown, but its buttons are not Blender's."""

    BLENDER = "org.blender.blender.desktop"

    def setUp(self):
        super().setUp()
        default = self.config.profiles[0]
        default.spacemouse_buttons = {"1": press("macro", keys=["KEY_F5"]), "9": press("macro", keys=["KEY_F6"])}
        default.spacemouse_layers = {"Default layer": {"8": press("dial")}}
        default.screen = {"start_page": "clock"}
        self.config.profiles.append(Profile(
            name="Blender", app_id="org.blender.Blender.desktop", spacemouse_motion={"sensitivity": 2.0},
            contexts=[ProfileContext("Edit", app_modes=["EDIT_MESH"])],
        ))
        self.driver["gui_config"] = self.config
        self.driver["spacemouse_binds"] = spacemouse_binds
        self.driver["merge_spacemouse_layers"] = merge_spacemouse_layers
        self.driver["app_shortcuts"] = shortcuts_from_config(self.config)
        self.link = FakeLink()
        self.driver["app_link"] = self.link
        self.driver["window_was_changed"]("none")

    def in_blender(self, mode):
        self.link.context = {"mode": mode}
        self.driver["window_was_changed"](self.BLENDER)
        self.process()

    def test_the_default_profiles_buttons_do_not_leak(self):
        self.driver["window_was_changed"]("none")
        self.assertEqual(self.driver["spacemouse_effective_buttons"]()["1"].press.type, "macro")
        self.assertIn(9, self.driver["spacemouse_bound_buttons"]())
        for mode in ("OBJECT", "EDIT_MESH"):  # without and with the context matching
            with self.subTest(mode=mode):
                self.in_blender(mode)
                self.assertEqual(self.driver["context_name"], "Edit" if mode == "EDIT_MESH" else "")
                buttons = self.driver["spacemouse_effective_buttons"]()
                self.assertEqual(buttons["1"].press.type, "select")  # the global map's
                self.assertNotIn("9", buttons)
                self.assertNotIn(9, self.driver["spacemouse_bound_buttons"]())
                meta = self.driver["profile_meta"]()
                self.assertEqual(meta["name"], "Blender")
                self.assertEqual(meta["spacemouse_motion"], {"sensitivity": 2.0})
                for key in ("spacemouse_buttons", "spacemouse_layers", "screen"):
                    self.assertNotIn(key, meta)
        self.assertEqual(self.driver["spacemouse_effective_buttons"]("Default layer"), self.driver["spacemouse_effective_buttons"]())


class FakeLink:
    """What dialpad.py asks of the app link server."""

    def __init__(self):
        self.context = {}
        self.runs = []
        self.outcome = ("sent", "blender")

    def context_for(self, window_id):
        return dict(self.context) if "blender" in str(window_id) else {}

    def connected(self, window_id):
        return "blender" in str(window_id)

    def run(self, window_id, command):
        self.runs.append((window_id, command))
        return self.outcome

    def commands(self, app):
        return {"app": app, "connected": True, "commands": []}


if __name__ == "__main__":
    unittest.main()
