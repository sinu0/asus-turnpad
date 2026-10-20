"""screen/content.py: faces of the keys, profile and settings resolution."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.schema import (
    DialPadConfig, MacroStep, Profile, ProfileContext, Segment, SpaceMouseAction, SpaceMouseButton, StepType,
    _default_config,
)
from screen import content


def keystroke(*keys, title=None):
    return MacroStep(type=StepType.KEYSTROKE, keys=list(keys), title=title)


def make_config():
    config = _default_config()
    blender = Profile(
        name="Blender", app_id="org.blender.Blender.desktop",
        segments=[
            Segment("Zoom", macro_cw=[keystroke("KEY_EQUAL")], color="#112233"),
            Segment("Tools", items=[Segment("Move", macro_cw=[keystroke("KEY_G")])]),
            Segment("Hidden", enabled=False),
        ],
        screen={"pages": ["clock", "keys"], "start_page": "clock", "accent": "#ff0000"},
    )
    config.profiles.append(blender)
    return config, blender


class ProfileTests(unittest.TestCase):
    def test_default_and_unknown_names(self):
        config, blender = make_config()
        self.assertIs(content.find_profile(config, "Blender"), blender)
        self.assertEqual(content.find_profile(config, "").name, "Default")
        self.assertEqual(content.find_profile(config, "Nope").name, "Default")
        self.assertEqual(content.find_profile(DialPadConfig(), "").name, "Default")

    def test_effective_screen_overrides(self):
        config, blender = make_config()
        plain = content.effective_screen(config, content.find_profile(config, ""))
        self.assertEqual(plain.pages, ["keys", "dial", "device", "motion", "clock"])
        self.assertEqual(plain.accent, "")
        effective = content.effective_screen(config, blender)
        self.assertEqual(effective.pages, ["clock", "keys"])
        self.assertEqual(effective.start_page, "clock")
        self.assertEqual(effective.accent, "#ff0000")
        self.assertEqual(config.screen.pages, ["keys", "dial", "device", "motion", "clock"])

    def test_start_page_outside_pages_falls_back(self):
        config, blender = make_config()
        blender.screen = {"pages": ["dial", "clock"]}
        self.assertEqual(content.effective_screen(config, blender).start_page, "dial")


class FaceTests(unittest.TestCase):
    def entries(self, config, profile):
        return {entry.key.id: entry for entry in content.key_entries(config, profile)}

    def test_function_slots_follow_the_menu(self):
        config, blender = make_config()
        entries = self.entries(config, blender)
        self.assertEqual(entries["1"].short.title, "Zoom")
        self.assertEqual(entries["1"].short.color, "#112233")
        self.assertEqual(entries["1"].short.kind, "function")
        self.assertEqual(entries["1"].short.detail, "=")
        self.assertEqual(entries["2"].short.title, "Tools")
        self.assertEqual(entries["2"].short.kind, "group")
        self.assertEqual(entries["3"].short.kind, "unbound")  # disabled entries are not slots
        # long presses are slots 5..9: nothing there
        self.assertEqual(entries["1"].long.kind, "unbound")

    def test_defaults_for_other_buttons(self):
        config, blender = make_config()
        entries = self.entries(config, blender)
        self.assertEqual(entries["menu"].short.kind, "dial")
        self.assertEqual(entries["esc"].short.kind, "back")
        self.assertEqual(entries["ctrl"].short.title, "Ctrl")
        self.assertEqual(entries["ctrl"].short.kind, "modifier")
        self.assertEqual(entries["fit"].short.kind, "unbound")
        self.assertIsNone(entries["fit"].long)

    def test_label_wins_and_macro_detail(self):
        config, blender = make_config()
        blender.spacemouse_buttons = {
            "12": SpaceMouseButton(press=SpaceMouseAction("macro", keys=["KEY_LEFTSHIFT", "KEY_A"]), label="Add"),
            "17": SpaceMouseButton(press=SpaceMouseAction("key_hold", keys=["KEY_Z"])),
            "13": SpaceMouseButton(press=SpaceMouseAction("macro", keys=["KEY_TAB"])),
            "1": SpaceMouseButton(press=SpaceMouseAction("none")),
        }
        entries = self.entries(config, blender)
        self.assertEqual((entries["1"].short.title, entries["1"].short.detail), ("Add", "Shift+A"))
        self.assertEqual(entries["1"].long.title, "Hold Z")
        self.assertEqual(entries["1"].long.kind, "hold")
        self.assertEqual(entries["2"].short.kind, "macro")
        self.assertEqual(entries["fit"].short.kind, "pass")
        self.assertEqual(entries["fit"].short.title, "App's own")
        self.assertFalse(entries["fit"].short.bound)

    def test_label_only_entry_names_the_binding_below(self):
        config, blender = make_config()
        blender.spacemouse_buttons = {"12": SpaceMouseButton(label="Zoom!")}
        face = self.entries(config, blender)["1"].short
        self.assertEqual(face.title, "Zoom!")
        self.assertEqual(face.kind, "function")

    def test_function_by_name_and_group_and_misc_kinds(self):
        config, blender = make_config()
        blender.spacemouse_buttons = {
            "12": SpaceMouseButton(press=SpaceMouseAction("function", function="Move")),
            "13": SpaceMouseButton(press=SpaceMouseAction("group", group="Tools")),
            "14": SpaceMouseButton(press=SpaceMouseAction("select")),
            "15": SpaceMouseButton(press=SpaceMouseAction("modifier", modifier="Alt")),
            "16": SpaceMouseButton(press=SpaceMouseAction("function", function="Ghost")),
        }
        entries = self.entries(config, blender)
        self.assertEqual(entries["1"].short.title, "Move")
        self.assertEqual(entries["2"].short.kind, "group")
        self.assertEqual(entries["3"].short.kind, "select")
        self.assertEqual(entries["4"].short.kind, "modifier")
        self.assertEqual(entries["5"].short.title, "Ghost")

    def test_plain_key_hold_action_is_its_long_face(self):
        config, blender = make_config()
        blender.spacemouse_buttons = {
            "1": SpaceMouseButton(press=SpaceMouseAction("macro", keys=["KEY_HOME"]),
                                  hold=SpaceMouseAction("key_hold", keys=["KEY_Z"])),
        }
        entry = self.entries(config, blender)["fit"]
        self.assertEqual(entry.long.kind, "hold")

    def test_face_buttons_and_lookup(self):
        config, blender = make_config()
        entries = content.key_entries(config, blender)
        self.assertEqual(content.face_buttons(entries), [12, 17, 13, 18, 14, 19, 15, 20, 16, 21])
        self.assertEqual(content.face_buttons(entries, show_long=False), [12, 13, 14, 15, 16])
        entry, face = content.entry_for_index(entries, 17)
        self.assertEqual(entry.key.id, "1")
        self.assertIs(face, entry.long)
        self.assertIsNone(content.entry_for_index(entries, 99))
        groups = [title for title, _ in content.grouped_entries(entries)]
        self.assertEqual(groups, ["Modifier keys", "View keys", "Motion keys", "System keys"])

    def test_single_profile_slots_are_unbound(self):
        config, blender = make_config()
        blender.mode = "single"
        self.assertEqual(self.entries(config, blender)["1"].short.kind, "unbound")

    def test_buttons_only_profile_uses_default_menu(self):
        config, _ = make_config()
        bare = Profile(name="Bare", app_id="x.desktop", spacemouse_buttons={"0": SpaceMouseButton(label="Dial")})
        config.profiles.append(bare)
        entries = self.entries(config, bare)
        self.assertEqual(entries["1"].short.title, "Volume")
        self.assertEqual(entries["menu"].short.title, "Dial")


def macro(*keys, label=""):
    return SpaceMouseButton(press=SpaceMouseAction("macro", keys=list(keys)), label=label)


def layer_press(name, mode="hold"):
    return SpaceMouseButton(press=SpaceMouseAction("layer", layer=name, layer_mode=mode))


def motion_press(which):
    return SpaceMouseButton(press=SpaceMouseAction("motion", motion=which))


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.config, self.blender = make_config()
        self.blender.spacemouse_buttons = {"12": macro("KEY_A", label="Plain")}
        self.blender.contexts = [ProfileContext(
            "Edit Mesh", app_modes=["EDIT_MESH"],
            segments=[Segment("Extrude", macro_cw=[keystroke("KEY_E")])],
            spacemouse_buttons={"13": macro("KEY_X", label="Cut")},
            screen={"accent": "#00ff00", "pages": ["device", "keys"]},
        )]

    def test_context_changes_menu_buttons_and_screen(self):
        resolved = content.resolve(self.config, {"profile": "Blender", "context": "Edit Mesh"})
        self.assertEqual(resolved.context.name, "Edit Mesh")
        self.assertEqual(resolved.profile.name, "Blender")
        entries = {entry.key.id: entry for entry in resolved.entries}
        self.assertEqual(entries["1"].short.title, "Plain")  # the profile's button stays
        self.assertEqual(entries["2"].short.title, "Cut")
        self.assertEqual(entries["3"].short.kind, "unbound")  # slot 2 of a menu with one entry
        self.assertEqual(resolved.settings.accent, "#00ff00")
        self.assertEqual(resolved.settings.pages, ["device", "keys"])
        # nothing of this leaks into the profile itself
        self.assertEqual(self.blender.screen["accent"], "#ff0000")
        self.assertEqual(len(self.blender.segments), 3)

    def test_context_without_segments_keeps_the_profiles_menu(self):
        self.blender.contexts[0].segments = None
        resolved = content.resolve(self.config, {"profile": "Blender", "context": "Edit Mesh"})
        faces = {entry.key.id: entry.short.title for entry in resolved.entries}
        self.assertEqual(faces["1"], "Plain")
        self.assertEqual(faces["2"], "Cut")
        self.assertEqual(content.effective_profile(self.blender, self.blender.contexts[0]).segments, self.blender.segments)

    def test_unknown_or_empty_context_is_the_plain_profile(self):
        for name in ("", "Nope", None):
            resolved = content.resolve(self.config, {"profile": "Blender", "context": name})
            self.assertIsNone(resolved.context)
            self.assertIs(resolved.profile, self.blender)
        self.assertEqual(content.find_context(self.blender, "Edit Mesh").name, "Edit Mesh")

    def test_context_layers_merge_over_the_profiles(self):
        self.blender.spacemouse_layers = {"Fn": {"12": macro("KEY_F1", label="P1"), "13": macro("KEY_F2", label="P2")}}
        self.blender.contexts[0].spacemouse_layers = {"Fn": {"13": macro("KEY_F3", label="C2")}}
        resolved = content.resolve(self.config, {"profile": "Blender", "context": "Edit Mesh", "layer": "Fn"})
        faces = {entry.key.id: entry.short for entry in resolved.entries}
        self.assertEqual((faces["1"].title, faces["1"].inherited), ("P1", False))
        self.assertEqual((faces["2"].title, faces["2"].inherited), ("C2", False))

    def test_resolution_key_follows_what_changes_faces(self):
        base = {"profile": "Blender"}
        keys = {
            content.resolution_key(self.config, base),
            content.resolution_key(self.config, {**base, "context": "Edit Mesh"}),
            content.resolution_key(self.config, {**base, "layer": "Fn"}),
            content.resolution_key(self.config, {**base, "motion": {"sensitivity": 1.5, "rotation": True, "translation": True}}),
            content.resolution_key(self.config, {**base, "app": "blender"}),
            content.resolution_key(self.config, base, 1),
        }
        self.assertEqual(len(keys), 6)
        # volatile parts of the state do not
        self.assertEqual(content.resolution_key(self.config, base),
                         content.resolution_key(self.config, {**base, "dial": True, "modifiers": ["Ctrl"], "suggest": "x"}))


class LayerFaceTests(unittest.TestCase):
    def setUp(self):
        self.config, self.blender = make_config()
        self.config.spacemouse.layers = {"Fn": {"12": macro("KEY_F1", label="Global one"), "14": macro("KEY_F4")}}
        self.blender.spacemouse_layers = {"Fn": {"14": macro("KEY_F5", label="Own")}}
        self.blender.spacemouse_buttons = {"13": layer_press("Fn", "latch")}

    def faces(self, layer):
        entries = content.key_entries(self.config, self.blender, layer=layer)
        return {entry.key.id: entry for entry in entries}

    def test_layer_faces_lie_over_the_base_map(self):
        faces = self.faces("Fn")
        self.assertEqual(faces["1"].short.title, "Global one")
        self.assertFalse(faces["1"].short.inherited)
        self.assertEqual(faces["3"].short.title, "Own")  # the profile's layer entry wins
        self.assertFalse(faces["3"].short.inherited)
        # not in the layer: the base face, dimmed
        self.assertEqual(faces["menu"].short.kind, "dial")
        self.assertTrue(faces["menu"].short.inherited)
        self.assertTrue(faces["2"].short.inherited)
        self.assertTrue(faces["1"].long.inherited)  # slot 5 of the base menu

    def test_no_layer_nothing_is_inherited(self):
        for entry in content.key_entries(self.config, self.blender):
            self.assertFalse(entry.short.inherited)
            self.assertFalse(entry.long is not None and entry.long.inherited)

    def test_unknown_layer_dims_everything(self):
        faces = self.faces("Ghost")
        self.assertTrue(all(entry.short.inherited for entry in faces.values() if entry.short.bound))

    def test_layer_action_face(self):
        for mode, word in (("hold", "hold"), ("latch", "once"), ("toggle", "toggle")):
            self.blender.spacemouse_buttons = {"13": layer_press("Fn", mode)}
            face = self.faces("")["2"].short
            self.assertEqual((face.title, face.detail, face.kind), ("Layer: Fn", word, "layer"))
            self.assertTrue(face.bound)
        self.blender.spacemouse_buttons = {"13": SpaceMouseButton(
            press=SpaceMouseAction("layer", layer="Fn"), label="Fn")}
        face = self.faces("")["2"].short
        self.assertEqual((face.title, face.detail), ("Fn", "Fn · hold"))

    def test_label_only_layer_entry_renames_the_binding_below(self):
        self.blender.spacemouse_layers = {"Fn": {"12": SpaceMouseButton(label="Renamed")}}
        face = self.faces("Fn")["1"].short
        self.assertEqual((face.title, face.kind, face.inherited), ("Renamed", "macro", False))  # global binding, new name


class MotionFaceTests(unittest.TestCase):
    def setUp(self):
        self.config, self.blender = make_config()
        self.blender.spacemouse_buttons = {
            "12": motion_press("rotation"), "13": motion_press("translation"), "14": motion_press("faster"),
            "15": motion_press("slower"), "16": motion_press("reset"),
        }

    def faces(self, motion):
        return {entry.key.id: entry.short for entry in content.key_entries(self.config, self.blender, motion=motion)}

    def test_toggles_show_their_state(self):
        on = self.faces({"sensitivity": 1.5, "rotation": True, "translation": False})
        self.assertEqual((on["1"].title, on["1"].detail), ("Rotation", "on"))
        self.assertEqual((on["2"].title, on["2"].detail), ("Pan/Zoom", "off"))
        self.assertEqual(on["2"].color, "")  # off = dimmed
        self.assertEqual(on["3"].detail, "×1.5")
        self.assertEqual((on["4"].title, on["4"].detail), ("Slower", "×1.5"))
        self.assertEqual(on["5"].title, "Reset motion")
        self.assertTrue(all(on[number].kind == "motion" for number in "12345"))

    def test_without_a_state(self):
        faces = self.faces(None)
        self.assertEqual(faces["1"].detail, "on / off")
        self.assertEqual(faces["3"].detail, "")

    def test_motion_state_reads_the_driver(self):
        self.assertIsNone(content.motion_state({}))
        self.assertIsNone(content.motion_state({"motion": "x"}))
        self.assertEqual(content.motion_state({"motion": {"sensitivity": "bad", "rotation": 0}}),
                         {"sensitivity": 1.0, "rotation": False, "translation": True})


class AppFaceTests(unittest.TestCase):
    def setUp(self):
        self.config, self.blender = make_config()
        self.catalog = content.catalog_index({"commands": [
            {"id": "mesh.extrude_region_move", "title": "Extrude", "category": "Modeling", "keys": "E"},
            {"id": "view.frame_selected", "title": "Frame selected", "keys": "Numpad ."},
            {"id": 5}, "junk",
        ]})

    def face(self, action, label="", commands="catalog"):
        self.blender.spacemouse_buttons = {"12": SpaceMouseButton(press=action, label=label)}
        entries = content.key_entries(self.config, self.blender, commands=self.catalog if commands == "catalog" else commands)
        return {entry.key.id: entry.short for entry in entries}["1"]

    def test_title_from_the_catalog_detail_from_fallback_keys(self):
        face = self.face(SpaceMouseAction("app", command="mesh.extrude_region_move", keys=["KEY_E"]))
        self.assertEqual((face.title, face.detail, face.kind), ("Extrude", "E", "app"))
        self.assertTrue(face.bound)

    def test_detail_falls_back_to_the_catalogs_keys(self):
        face = self.face(SpaceMouseAction("app", command="view.frame_selected"))
        self.assertEqual((face.title, face.detail), ("Frame selected", "Numpad ."))

    def test_unknown_command_or_no_catalog_is_prettified(self):
        action = SpaceMouseAction("app", command="mesh.loopcut_slide", keys=["KEY_LEFTCTRL", "KEY_R"])
        for commands in (None, {}):
            face = self.face(action, commands=commands)
            self.assertEqual(face.title, "Loopcut slide")
            self.assertEqual(face.detail, "Ctrl+R")
        self.assertEqual(content.prettify_command("plain"), "Plain")
        self.assertEqual(content.prettify_command(""), "Command")

    def test_label_wins(self):
        face = self.face(SpaceMouseAction("app", command="mesh.extrude_region_move"), label="Push out")
        self.assertEqual((face.title, face.detail), ("Push out", "E"))

    def test_catalog_index_accepts_json_and_garbage(self):
        self.assertEqual(set(self.catalog), {"mesh.extrude_region_move", "view.frame_selected"})
        self.assertEqual(content.catalog_index('{"commands": [{"id": "a"}]}'), {"a": {"id": "a"}})
        for junk in ("", "not json", None, 5, {"commands": 3}):
            self.assertEqual(content.catalog_index(junk), {})


class ChipAndNoticeTests(unittest.TestCase):
    def test_chips_in_order(self):
        driver = {"layer": "Fn", "context": "Edit Mesh", "app_context": {"label": "Sculpt"},
                  "motion": {"sensitivity": 1.5, "rotation": False, "translation": False}}
        self.assertEqual([(chip.text, chip.kind) for chip in content.bar_chips(driver)], [
            ("Fn", "layer"), ("Edit Mesh", "context"), ("Sculpt", "mode"),
            ("ROT off", "motion"), ("PAN off", "motion"), ("×1.5", "motion"),
        ])

    def test_default_state_has_no_chips(self):
        self.assertEqual(content.bar_chips({}), [])
        self.assertEqual(content.bar_chips({"motion": {"sensitivity": 1.0, "rotation": True, "translation": True}}), [])

    def test_mode_label_alone_and_not_twice(self):
        self.assertEqual([c.text for c in content.bar_chips({"app_context": {"label": "Edit Mesh"}})], ["Edit Mesh"])
        driver = {"context": "Edit Mesh", "app_context": {"label": "Edit Mesh"}}
        self.assertEqual([c.text for c in content.bar_chips(driver)], ["Edit Mesh"])

    def test_speed_text(self):
        self.assertEqual(content.format_speed(1.5), "×1.5")
        self.assertEqual(content.format_speed(2.0), "×2")
        self.assertEqual(content.format_speed(0.25), "×0.25")
        self.assertEqual(content.format_speed(0.1 + 0.2), "×0.3")

    BASE = {"profile": "Blender", "app": "blender", "context": "",
            "motion": {"sensitivity": 1.0, "rotation": True, "translation": True}, "suggest": "", "notice": {}}

    def state(self, **changes):
        state = {**self.BASE, "motion": dict(self.BASE["motion"])}
        state.update(changes)
        return state

    def test_motion_changes(self):
        old = self.state()
        cases = [
            ({"rotation": False}, "Rotation off"),
            ({"translation": False}, "Pan/Zoom off"),
            ({"sensitivity": 1.5}, "Speed ×1.5"),
            ({"rotation": False, "sensitivity": 2.0}, "Rotation off · Speed ×2"),
        ]
        for motion, title in cases:
            with self.subTest(motion=motion):
                new = self.state(motion={**old["motion"], **motion})
                notice = content.change_notice(old, new)
                self.assertEqual((notice.title, notice.kind), (title, "motion"))
        back = content.change_notice(self.state(motion={**old["motion"], "rotation": False}), old)
        self.assertEqual(back.title, "Rotation on")
        self.assertIsNone(content.change_notice(old, self.state()))

    def test_no_motion_toast_when_the_application_changed(self):
        old = self.state()
        for changes in ({"profile": "Other"}, {"app": "krita"}, {"context": "Edit"}):
            new = self.state(motion={"sensitivity": 2.0, "rotation": False, "translation": True}, **changes)
            self.assertIsNone(content.change_notice(old, new), changes)

    def test_suggest_appears_once(self):
        old = self.state()
        shown = self.state(suggest="FreeCAD")
        self.assertEqual(content.change_notice(old, shown).title, "Ready profile for FreeCAD — check the notification")
        self.assertIsNone(content.change_notice(shown, self.state(suggest="FreeCAD", dial=True)))
        self.assertIsNone(content.change_notice(shown, old))
        self.assertEqual(content.change_notice(shown, self.state(suggest="KiCad")).title,
                         "Ready profile for KiCad — check the notification")

    def test_notice_by_id(self):
        old = self.state(notice={"id": 3, "text": "Old"})
        self.assertIsNone(content.change_notice(old, self.state(notice={"id": 3, "text": "Old"})))
        new = content.change_notice(old, self.state(notice={"id": 4, "text": "Add-on not connected"}))
        self.assertEqual((new.title, new.kind), ("Add-on not connected", "notice"))
        self.assertIsNone(content.change_notice(old, self.state(notice={})))
        # a notice wins over a suggestion that arrives with it
        both = self.state(notice={"id": 5, "text": "Hi"}, suggest="KiCad")
        self.assertEqual(content.change_notice(old, both).title, "Hi")

    def test_first_state_and_lost_driver_say_nothing(self):
        self.assertIsNone(content.change_notice({}, self.state(suggest="KiCad", notice={"id": 1, "text": "x"})))
        self.assertIsNone(content.change_notice(self.state(), {}))
        self.assertIsNone(content.change_notice(None, None))


if __name__ == "__main__":
    unittest.main()
