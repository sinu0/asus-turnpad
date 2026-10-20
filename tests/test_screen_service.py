"""screen/service.py: the pure helpers (config save, start checks)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import schema
from config.schema import (
    MacroStep, Profile, ProfileContext, Segment, SpaceMouseAction, SpaceMouseButton, StepType, _default_config, _encoder,
)
from spacemouse_devices import SPACEPILOT_PRO
from screen import service
from screen.service import MuteHeartbeat


class TemporaryConfig(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name)
        patches = [mock.patch.object(schema, "CONFIG_DIR", path), mock.patch.object(schema, "CONFIG_FILE", path / "config.json")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.file = path / "config.json"

    def write(self, config):
        self.file.write_text(json.dumps(_encoder(config), indent=2))


class SaveTests(TemporaryConfig):
    def test_only_the_screen_section_changes(self):
        config = _default_config()
        config.profiles.append(Profile(name="X", app_id="x.desktop",
                                       spacemouse_buttons={"12": SpaceMouseButton(press=SpaceMouseAction("back"))}))
        config.overlay.accent = "#123456"
        self.write(config)
        before = json.loads(self.file.read_text())
        self.assertTrue(service.save_screen_fields({"brightness": 40, "theme": "light", "pages": ["clock", "keys"]}))
        after = json.loads(self.file.read_text())
        self.assertEqual(after["screen"]["brightness"], 40)
        self.assertEqual(after["screen"]["theme"], "light")
        self.assertEqual(after["screen"]["pages"], ["clock", "keys"])
        for section in before:
            if section != "screen":
                self.assertEqual(after[section], before[section], section)
        self.assertEqual({k: v for k, v in after["screen"].items() if k not in ("brightness", "theme", "pages")},
                         {k: v for k, v in before["screen"].items() if k not in ("brightness", "theme", "pages")})
        self.assertFalse(self.file.with_suffix(".json.tmp").exists())

    def test_unknown_fields_are_ignored(self):
        self.write(_default_config())
        before = self.file.read_text()
        self.assertFalse(service.save_screen_fields({"bogus": 1}))
        self.assertEqual(self.file.read_text(), before)

    def test_broken_file_is_not_overwritten(self):
        self.file.write_text("{not json")
        self.assertFalse(service.save_screen_fields({"brightness": 10}))
        self.assertEqual(self.file.read_text(), "{not json")

    def test_merge_screen_fields(self):
        config = _default_config()
        self.assertEqual(service.merge_screen_fields(config, {"theme": "dark", "nope": 1}), ["theme"])
        self.assertEqual(config.screen.theme, "dark")


class ReadAndChecksTests(TemporaryConfig):
    def test_read_config(self):
        self.assertEqual(service.read_config().screen.brightness, 70)  # no file: defaults, nothing written
        self.assertFalse(self.file.exists())
        self.file.write_text("nonsense")
        self.assertIsNone(service.read_config())

    def test_start_checks(self):
        config = _default_config()
        self.assertEqual(service.start_checks(config, [object()]), None)
        self.assertEqual(service.start_checks(config, [])[0], 3)
        config.screen.enabled = False
        self.assertEqual(service.start_checks(config, [object()])[0], 3)
        self.assertIsNone(service.start_checks(None, [object()]))

    def test_parse_json_object(self):
        self.assertEqual(service.parse_json_object('{"a": 1}'), {"a": 1})
        self.assertIsNone(service.parse_json_object("[1]"))
        self.assertIsNone(service.parse_json_object("x"))
        self.assertIsNone(service.parse_json_object(None))


class HighScoreTests(TemporaryConfig):
    def test_game_file_is_next_to_the_config(self):
        self.assertEqual(service.game_file(), Path(self.directory.name) / "screen-game.json")

    def test_missing_file_is_zero(self):
        self.assertEqual(service.read_high_score(service.game_file()), 0)

    def test_write_then_read(self):
        path = service.game_file()
        self.assertTrue(service.write_high_score(path, 1280))
        self.assertEqual(service.read_high_score(path), 1280)
        self.assertEqual(json.loads(path.read_text()), {"high_score": 1280})
        self.assertTrue(service.write_high_score(path, 2000))
        self.assertEqual(service.read_high_score(path), 2000)
        self.assertEqual(sorted(item.name for item in path.parent.iterdir()), ["screen-game.json"])  # no temporary file left

    def test_the_directory_is_made(self):
        path = Path(self.directory.name) / "new" / "dir" / "screen-game.json"
        self.assertTrue(service.write_high_score(path, 5))
        self.assertEqual(service.read_high_score(path), 5)

    def test_corrupt_or_odd_files_are_zero(self):
        path = service.game_file()
        for text in ("", "{not json", "[1, 2]", "null", '{"high_score": "9"}', '{"high_score": -4}', '{"high_score": 1.5}',
                     '{"high_score": true}', '{}', "\x00\xff"):
            path.write_text(text)
            self.assertEqual(service.read_high_score(path), 0, text)

    def test_unwritable_place_is_not_fatal(self):
        blocker = Path(self.directory.name) / "file"
        blocker.write_text("x")
        self.assertFalse(service.write_high_score(blocker / "inside" / "screen-game.json", 5))

    def test_a_failed_write_leaves_the_old_score(self):
        path = service.game_file()
        service.write_high_score(path, 70)
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            self.assertFalse(service.write_high_score(path, 99))
        self.assertEqual(service.read_high_score(path), 70)


class MuteHeartbeatTests(unittest.TestCase):
    def test_nothing_while_not_playing(self):
        beat = MuteHeartbeat()
        for now in (0, 1, 50):
            self.assertIsNone(beat.update(False, now))

    def test_starts_at_once_and_renews_every_second(self):
        beat = MuteHeartbeat()
        self.assertEqual(beat.update(True, 100.0), "mute:3000")
        self.assertIsNone(beat.update(True, 100.033))
        self.assertIsNone(beat.update(True, 100.99))
        self.assertEqual(beat.update(True, 101.0), "mute:3000")
        self.assertIsNone(beat.update(True, 101.5))
        self.assertEqual(beat.update(True, 102.6), "mute:3000")  # a late call renews once

    def test_stopping_sends_a_single_zero(self):
        beat = MuteHeartbeat()
        beat.update(True, 10.0)
        self.assertEqual(beat.update(False, 10.5), "mute:0")
        self.assertIsNone(beat.update(False, 10.6))
        self.assertIsNone(beat.update(False, 99))

    def test_pause_and_play_again(self):
        beat = MuteHeartbeat()
        beat.update(True, 1.0)
        self.assertEqual(beat.update(False, 1.2), "mute:0")
        self.assertEqual(beat.update(True, 1.3), "mute:3000")  # at once, not after a second

    def test_the_renewal_is_well_inside_the_lease(self):
        self.assertLess(service.MUTE_RENEW_S * 2, service.MUTE_LEASE_MS / 1000)
        self.assertLessEqual(service.MUTE_LEASE_MS, 5000)  # the driver's maximum
        self.assertEqual(service.MUTE_LEASE_MS, MuteHeartbeat().lease_ms)

    def test_every_command_is_one_the_driver_understands(self):
        try:
            from dialpad_dbus import parse_control
        except ImportError:  # PyGObject missing
            self.skipTest("PyGObject is not installed")
        beat = MuteHeartbeat()
        for command in (beat.update(True, 0.0), beat.update(False, 1.0)):
            self.assertIsNotNone(parse_control(command), command)


class FakeDevice:
    """Enough of ScreenDevice for the service's model; nothing is drawn."""

    screen = SPACEPILOT_PRO.screen
    closed = False

    def send(self, frame):
        pass

    def set_brightness(self, level):
        pass


class DriverFollowing(unittest.TestCase):
    """The service's model follows DriverState: context, layer, motion, the
    application's commands and the toasts of changes (no bus, no loop)."""

    def setUp(self):
        from gi.repository import GLib

        config = _default_config()
        profile = Profile(
            name="Blender", app_id="org.blender.Blender.desktop",
            segments=[Segment("Zoom", macro_cw=[MacroStep(type=StepType.KEYSTROKE, keys=["KEY_EQUAL"])])],
            spacemouse_buttons={
                "12": SpaceMouseButton(press=SpaceMouseAction("app", command="mesh.extrude_region_move", keys=["KEY_E"])),
                "13": SpaceMouseButton(press=SpaceMouseAction("layer", layer="Fn")),
            },
            spacemouse_layers={"Fn": {"12": SpaceMouseButton(press=SpaceMouseAction("macro", keys=["KEY_F1"]), label="Fn one")}},
            contexts=[ProfileContext("Edit Mesh", app_modes=["EDIT_MESH"], screen={"accent": "#00ff00"})],
        )
        config.profiles.append(profile)
        patch = mock.patch.object(GLib, "timeout_add", return_value=1)
        patch.start()
        self.addCleanup(patch.stop)
        self.service = service.ScreenService(config, FakeDevice())
        self.service.ui.settings.press_feedback = False

    def feed(self, **state):
        base = {"profile": "Blender", "app": "blender", "dial": False, "motion": {
            "sensitivity": 1.0, "rotation": True, "translation": True}, "layer": "", "context": "", "suggest": "",
            "notice": {}}
        base.update(state)
        self.service._merge_driver(json.dumps(base))

    def face(self, number="1"):
        return {entry.key.id: entry for entry in self.service.entries}[number].short

    def toast(self):
        view = self.service.ui.view(self.service._now())
        return view.toast.title if view.toast else None

    def test_layer_and_context_change_the_faces_and_settings(self):
        self.feed()
        self.assertEqual(self.face().title, "Extrude region move")
        self.assertEqual(self.service.settings.accent, "")
        self.feed(layer="Fn")
        self.assertEqual(self.face().title, "Fn one")
        self.feed(context="Edit Mesh")
        self.assertEqual(self.service.settings.accent, "#00ff00")
        self.assertEqual(self.service.resolved.context.name, "Edit Mesh")
        self.feed()
        self.assertEqual(self.service.settings.accent, "")

    def test_catalog_names_the_app_faces(self):
        self.feed()
        before = self.service.catalog_version
        self.service._catalog_arrived("blender", json.dumps({"name": "Blender", "commands": [
            {"id": "mesh.extrude_region_move", "title": "Extrude", "keys": "E"}]}))
        self.assertEqual(self.service.catalog_version, before + 1)
        self.assertEqual(self.face().title, "Extrude")
        self.assertEqual(self.service._catalog()["name"], "Blender")
        scene = self.service.build_scene()
        self.assertEqual(scene.app_name, "Blender")
        # another application has no catalog of its own
        self.feed(app="krita")
        self.assertEqual(self.face().title, "Extrude region move")

    def test_motion_change_toasts_but_not_the_first_state_or_an_app_switch(self):
        self.feed()
        self.assertIsNone(self.toast())
        self.feed(motion={"sensitivity": 1.5, "rotation": False, "translation": True})
        self.assertEqual(self.toast(), "Rotation off · Speed ×1.5")
        self.service.ui.toast = None
        self.feed(app="krita", motion={"sensitivity": 1.0, "rotation": True, "translation": True})
        self.assertIsNone(self.toast())

    def test_suggestion_and_notice_toasts(self):
        self.feed()
        self.feed(suggest="FreeCAD")
        self.assertEqual(self.toast(), "Ready profile for FreeCAD — check the notification")
        self.service.ui.toast = None
        self.feed(suggest="FreeCAD")  # the same suggestion again: no second toast
        self.assertIsNone(self.toast())
        self.feed(notice={"id": 1, "text": "Blender add-on connected"})
        self.assertEqual(self.toast(), "Blender add-on connected")
        self.service.ui.toast = None
        self.feed(notice={"id": 1, "text": "Blender add-on connected"})
        self.assertIsNone(self.toast())
        self.feed(notice={"id": 2, "text": "Again"})
        self.assertEqual(self.toast(), "Again")

    def test_driver_gone_clears_without_a_toast(self):
        self.feed(layer="Fn")
        self.service._driver_gone()
        self.assertEqual(self.service.resolved.layer, "")
        self.assertIsNone(self.toast())


if __name__ == "__main__":
    unittest.main()
