"""The helpers of the contexts editor in the settings app (config_app/contexts.py)."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.schema import (
    CONTEXT_NAME_MAX,
    CONTEXTS_MAX,
    DialPadConfig,
    Profile,
    ProfileContext,
    Segment,
    SpaceMouseAction,
    SpaceMouseButton,
)

try:
    import gi  # noqa: F401
except ImportError:  # pragma: no cover
    gi = None

from config_app import contexts as cx

if gi is not None:
    from config_app import spacemouse as sm


CATALOG = json.dumps({
    "app": "blender", "connected": True, "context": {"mode": "SCULPT", "label": "Sculpt Mode"},
    "commands": [
        {"id": "a", "modes": ["EDIT_MESH"]}, {"id": "b", "modes": ["EDIT_MESH", "OBJECT"]},
        {"id": "c"}, "junk", {"id": "d", "modes": "not a list"},
    ],
})


class ModeTest(unittest.TestCase):
    def test_friendly_labels(self):
        self.assertEqual(cx.mode_label("EDIT_MESH"), "Edit Mesh")
        self.assertEqual(cx.mode_label("OBJECT"), "Object Mode")
        self.assertEqual(cx.mode_label("SOME_NEW_MODE"), "Some New Mode")
        self.assertEqual(cx.mode_label(""), "")

    def test_modes_come_from_the_catalog_the_current_context_and_the_chosen(self):
        self.assertEqual(cx.known_modes(CATALOG, "POSE", ["PARTICLE", "EDIT_MESH"]),
                         ["EDIT_MESH", "OBJECT", "SCULPT", "POSE", "PARTICLE"])

    def test_no_catalog_still_keeps_what_is_chosen(self):
        self.assertEqual(cx.known_modes("not json", "", ["X"]), ["X"])
        self.assertEqual(cx.known_modes({}, "", []), [])
        self.assertEqual(cx.known_modes(None), [])

    def test_typed_modes_map_to_the_known_ids(self):
        known = ["EDIT_MESH", "SCULPT"]
        self.assertEqual(cx.normalize_mode("edit mesh", known), "EDIT_MESH")
        self.assertEqual(cx.normalize_mode(" sculpt ", known), "SCULPT")
        self.assertEqual(cx.normalize_mode("Layout", known), "Layout")
        self.assertEqual(cx.normalize_mode("", known), "")


class TitleCheckTest(unittest.TestCase):
    def test_states(self):
        self.assertEqual(cx.title_check("", "x")[0], "empty")
        self.assertEqual(cx.title_check("  ", "x")[0], "empty")
        self.assertEqual(cx.title_check(" | ", "x")[0], "invalid")
        self.assertEqual(cx.title_check("x" * 1000, "x")[0], "invalid")
        self.assertEqual(cx.title_check("cube", "")[0], "unchecked")
        self.assertEqual(cx.title_check(".blend", "Cube.blend - Blender")[0], "match")
        self.assertEqual(cx.title_check("CUBE", "my cube")[0], "match")
        self.assertEqual(cx.title_check("sphere", "my cube")[0], "nomatch")

    def test_alternatives_and_plain_text(self):
        self.assertEqual(cx.title_check("YouTube | Vimeo", "Cats - vimeo")[0], "match")
        self.assertEqual(cx.title_check("YouTube | Vimeo", "Dogs")[0], "nomatch")
        # Not a regular expression: the characters mean themselves.
        self.assertEqual(cx.title_check("(", "f(x)")[0], "match")
        self.assertEqual(cx.title_check("a.c", "abc")[0], "nomatch")

    def test_invalid_message_says_why(self):
        self.assertTrue(cx.title_check("|", "x")[1][0].isupper())


class EditingTest(unittest.TestCase):
    def setUp(self):
        self.profile = Profile(name="Blender", app_id="blender", segments=[Segment(title="Zoom"), Segment(title="Undo")])

    def test_new_contexts_get_unique_names_and_go_last(self):
        first = cx.new_context(self.profile)
        second = cx.new_context(self.profile)
        third = cx.new_context(self.profile, "Context")
        self.assertEqual([c.name for c in self.profile.contexts], ["Context", "Context 2", "Context 3"])
        self.assertIs(self.profile.contexts[0], first)
        self.assertIs(self.profile.contexts[1], second)
        self.assertIs(self.profile.contexts[2], third)

    def test_a_rename_may_keep_its_own_name(self):
        context = cx.new_context(self.profile, "Sculpt")
        self.assertEqual(cx.unique_name(self.profile, "Sculpt", ignore=context), "Sculpt")
        self.assertEqual(cx.unique_name(self.profile, "Sculpt"), "Sculpt 2")
        self.assertEqual(cx.unique_name(self.profile, "   "), "Context")

    def test_long_names_are_cut_and_numbering_still_fits(self):
        name = "x" * (CONTEXT_NAME_MAX + 20)
        cx.new_context(self.profile, name)
        second = cx.new_context(self.profile, name)
        self.assertLessEqual(len(second.name), CONTEXT_NAME_MAX)
        self.assertNotEqual(second.name, self.profile.contexts[0].name)

    def test_the_number_of_contexts_is_limited(self):
        for _ in range(CONTEXTS_MAX):
            cx.new_context(self.profile)
        self.assertFalse(cx.can_add(self.profile))

    def test_priority_order_changes_and_stops_at_the_ends(self):
        a, b, c = (cx.new_context(self.profile, name) for name in "abc")
        self.assertTrue(cx.move_context(self.profile, c, -1))
        self.assertEqual(self.profile.contexts, [a, c, b])
        self.assertFalse(cx.move_context(self.profile, a, -1))
        self.assertFalse(cx.move_context(self.profile, b, 1))

    def test_own_menu_starts_as_a_copy_and_goes_back_to_the_profiles(self):
        context = cx.new_context(self.profile)
        cx.set_own_menu(self.profile, context, True)
        self.assertEqual([s.title for s in context.segments], ["Zoom", "Undo"])
        self.assertIsNot(context.segments[0], self.profile.segments[0])
        context.segments.append(Segment(title="Extra"))
        cx.set_own_menu(self.profile, context, True)  # already on: nothing is lost
        self.assertEqual(len(context.segments), 3)
        self.assertEqual(len(self.profile.segments), 2)
        cx.set_own_menu(self.profile, context, False)
        self.assertIsNone(context.segments)

    def test_the_menu_view_edits_the_contexts_list_in_place(self):
        context = cx.new_context(self.profile)
        cx.set_own_menu(self.profile, context, True)
        view = cx.menu_view(self.profile, context)
        self.assertIs(view.segments, context.segments)
        view.segments.append(Segment(title="New"))
        self.assertEqual(context.segments[-1].title, "New")
        self.assertEqual(len(self.profile.segments), 2)
        self.assertEqual(view.contexts, [])
        self.assertEqual(view.app_id, "blender")

    def test_summaries(self):
        context = ProfileContext("Edit", title="cube | sphere", app_modes=["EDIT_MESH", "SCULPT"])
        self.assertEqual(cx.conditions_text(context), "title contains cube | sphere · Edit Mesh, Sculpt Mode")
        self.assertIn("never used", cx.conditions_text(ProfileContext("Empty")))
        context.segments = []
        context.spacemouse_buttons = {"3": SpaceMouseButton(press=SpaceMouseAction("none"))}
        context.screen = {"start_page": "dial"}
        text = cx.context_summary(context)
        self.assertIn("own menu (0)", text)
        self.assertIn("own buttons (1)", text)
        self.assertIn("screen starts on dial", text)


class ScopeTest(unittest.TestCase):
    def setUp(self):
        self.config = DialPadConfig()
        self.blender = Profile(name="Blender", app_id="blender")
        self.plain = Profile(name="Default")
        self.config.profiles = [self.plain, self.blender]
        self.sculpt = cx.new_context(self.blender, "Sculpt", app_modes=["SCULPT"])
        self.edit = cx.new_context(self.blender, "Edit", app_modes=["EDIT_MESH"])

    def test_entries_list_contexts_after_their_profile(self):
        labels = [label for label, _scope in cx.scope_entries(self.config)]
        self.assertEqual(labels, ["Default", "Blender  ·  app", "Blender › Sculpt", "Blender › Edit"])

    def test_scopes_compare_by_what_they_point_at(self):
        scope = cx.ContextScope(self.blender, self.sculpt)
        self.assertEqual(scope, cx.ContextScope(self.blender, self.sculpt))
        self.assertNotEqual(scope, cx.ContextScope(self.blender, self.edit))
        self.assertNotEqual(scope, self.blender)
        self.assertEqual(len({scope, cx.ContextScope(self.blender, self.sculpt)}), 1)

    def test_a_scope_acts_like_a_profile_for_the_button_list(self):
        scope = cx.ContextScope(self.blender, self.sculpt)
        self.assertEqual(scope.name, "Blender › Sculpt")
        self.assertEqual(scope.app_id, "blender")
        scope.spacemouse_buttons["5"] = SpaceMouseButton(press=SpaceMouseAction("none"))
        self.assertIn("5", self.sculpt.spacemouse_buttons)
        self.assertEqual(self.blender.spacemouse_buttons, {})
        scope.spacemouse_buttons = {}
        self.assertEqual(self.sculpt.spacemouse_buttons, {})
        # The menu pickers offer the context's own functions, else the profile's.
        self.blender.segments = [Segment(title="Zoom")]
        self.assertEqual([s.title for s in scope.segments], ["Zoom"])
        self.sculpt.segments = [Segment(title="Brush")]
        self.assertEqual([s.title for s in scope.segments], ["Brush"])

    def test_scope_alive(self):
        scope = cx.ContextScope(self.blender, self.sculpt)
        self.assertTrue(cx.scope_alive(self.config, None))
        self.assertTrue(cx.scope_alive(self.config, self.blender))
        self.assertTrue(cx.scope_alive(self.config, scope))
        self.blender.contexts.remove(self.sculpt)
        self.assertFalse(cx.scope_alive(self.config, scope))
        self.config.profiles.remove(self.blender)
        self.assertFalse(cx.scope_alive(self.config, self.blender))

    def test_button_maps_cover_contexts(self):
        self.sculpt.spacemouse_buttons["1"] = SpaceMouseButton(press=SpaceMouseAction("none"))
        maps = cx.button_maps(self.config)
        self.assertEqual(len(maps), 4)
        self.assertIn({"1": self.sculpt.spacemouse_buttons["1"]}, maps)

    @unittest.skipIf(gi is None, "needs PyGObject")
    def test_a_contexts_binding_lies_over_the_profiles_over_all_applications(self):
        scope = cx.ContextScope(self.blender, self.sculpt)
        global_button, source = sm.binding(self.config, None, 12)
        # Nothing of its own: the profile's binding (or the global one) shows.
        button, source = sm.binding(self.config, scope, 12)
        self.assertEqual((button, source), (global_button, "global" if global_button else None))
        self.blender.spacemouse_buttons["12"] = SpaceMouseButton(press=SpaceMouseAction("key", keys=["KEY_F"]))
        button, source = sm.binding(self.config, scope, 12)
        self.assertEqual(button.press.keys, ["KEY_F"])
        self.assertEqual(source, "global")
        self.sculpt.spacemouse_buttons["12"] = SpaceMouseButton(press=SpaceMouseAction("key", keys=["KEY_G"]))
        button, source = sm.binding(self.config, scope, 12)
        self.assertEqual((button.press.keys, source), (["KEY_G"], "profile"))
        # A label only keeps the profile's actions.
        self.sculpt.spacemouse_buttons["12"] = SpaceMouseButton(label="Frame")
        button, source = sm.binding(self.config, scope, 12)
        self.assertEqual((button.press.keys, button.label, source), (["KEY_F"], "Frame", "global"))


if __name__ == "__main__":
    unittest.main()
