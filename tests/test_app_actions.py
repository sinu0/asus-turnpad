"""App commands in dial and button actions: the schema (APP macro step,
SpaceMouseAction "app"), the macro executor, and how the driver runs them
through the app link or falls back to the keys."""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app_link  # noqa: E402
from config.labels import describe_macro  # noqa: E402
from config.profile_share import ProfileTextError, profiles_from_text  # noqa: E402
from config.runtime import MacroExecutionError, MacroExecutor  # noqa: E402
from config.schema import (  # noqa: E402
    DialPadConfig, MacroStep, Profile, Segment, SpaceMouseAction, SpaceMouseButton, SpaceMouseConfig, StepType,
    _decoder, _encoder, spacemouse_macros,
)
from tests.test_contexts import FakeLink  # noqa: E402
from tests.test_driver_control import DriverCase  # noqa: E402


class SchemaTest(unittest.TestCase):
    def test_the_step_round_trips(self):
        step = MacroStep(type=StepType.APP, command="mesh.extrude_region_move", keys=["KEY_E"], title="Extrude")
        data = json.loads(json.dumps(_encoder(step)))
        self.assertEqual(data, {"type": "app", "keys": ["KEY_E"], "title": "Extrude", "command": "mesh.extrude_region_move"})
        self.assertEqual(_decoder(data, MacroStep), step)

    def test_the_action_round_trips_and_makes_an_app_step(self):
        action = SpaceMouseAction("app", command="view3d.view_selected", keys=["KEY_KPDOT"])
        button = SpaceMouseButton(press=action)
        config = DialPadConfig(spacemouse=SpaceMouseConfig(buttons={"1": button}))
        from config.schema import config_from_text
        again = config_from_text(json.dumps(_encoder(config))).spacemouse.buttons["1"].press
        self.assertEqual((again.type, again.command, again.keys), ("app", "view3d.view_selected", ["KEY_KPDOT"]))
        self.assertEqual(action.steps(), [MacroStep(type=StepType.APP, command="view3d.view_selected", keys=["KEY_KPDOT"])])
        self.assertEqual(SpaceMouseAction("app", command="a.b").steps()[0].keys, None)

    def test_an_app_action_without_a_command_is_dropped(self):
        config = config_from_text_with_buttons({"1": {"press": {"type": "app", "keys": ["KEY_E"]}},
                                                "2": {"press": {"type": "app", "command": "  "}},
                                                "3": {"press": {"type": "app", "command": "x" * 200}}})
        self.assertEqual(config.spacemouse.buttons, {})

    def test_the_keys_of_app_actions_reach_the_virtual_keyboard(self):
        config = DialPadConfig(profiles=[Profile(name="B", app_id="b.desktop", spacemouse_buttons={
            "1": SpaceMouseButton(press=SpaceMouseAction("app", command="a.b", keys=["KEY_E"]))})])
        steps = [step for macro in spacemouse_macros(config) for step in macro]
        self.assertIn(("a.b", ["KEY_E"]), [(step.command, step.keys) for step in steps if step.type is StepType.APP])

    def test_description_prefers_the_shortcut(self):
        self.assertEqual(describe_macro([MacroStep(type=StepType.APP, command="mesh.extrude", keys=["KEY_E"])]), "E")
        self.assertEqual(describe_macro([MacroStep(type=StepType.APP, command="mesh.extrude_region")]), "Extrude region")
        self.assertEqual(describe_macro([MacroStep(type=StepType.APP, command="x", title="Pull")]), "Pull")


def config_from_text_with_buttons(buttons):
    from config.schema import config_from_text
    config = DialPadConfig(spacemouse=SpaceMouseConfig())
    data = _encoder(config)
    data["spacemouse"]["buttons"] = buttons
    return config_from_text(json.dumps(data))


class ExecutorTest(unittest.TestCase):
    def executor(self, run_app=None):
        events = []
        record = lambda name: (lambda *args: events.append((name, args)))  # noqa: E731
        executor = MacroExecutor(
            send_keys=record("keys"), scroll=record("scroll"), media=record("media"), brightness=record("brightness"),
            volume=record("volume"), run_program=record("program"), run_shell=record("shell"), sleep=record("sleep"),
            run_app=record("app") if run_app else None,
        )
        return executor, events

    def test_the_command_goes_to_the_app_callback_with_the_fallback_keys(self):
        executor, events = self.executor(run_app=True)
        executor.execute([MacroStep(type=StepType.APP, command="a.b", keys=["KEY_E"]), MacroStep(type=StepType.APP, command="c.d")])
        self.assertEqual(events, [("app", ("a.b", ["KEY_E"])), ("app", ("c.d", []))])

    def test_without_a_callback_the_keys_are_pressed(self):
        executor, events = self.executor()
        executor.execute([MacroStep(type=StepType.APP, command="a.b", keys=["KEY_LEFTSHIFT", "KEY_E"]),
                          MacroStep(type=StepType.APP, command="c.d")])
        self.assertEqual(events, [("keys", (["KEY_LEFTSHIFT", "KEY_E"],))])

    def test_a_step_without_a_command_is_refused(self):
        executor, _events = self.executor(run_app=True)
        with self.assertRaises(MacroExecutionError):
            executor.execute([MacroStep(type=StepType.APP, keys=["KEY_E"])])


class ProfileTextTest(unittest.TestCase):
    def parse(self, step=None, action=None):
        data = {"name": "B", "segments": [{"title": "Zoom", "macro_cw": [step] if step else []}]}
        if action:
            data["spacemouse_buttons"] = {"1": {"press": action}}
        return profiles_from_text(json.dumps(data))[0]

    def test_steps_and_actions_are_checked_and_keys_cleaned(self):
        profile = self.parse({"type": "app", "command": "view3d.view_selected", "keys": ["KEY_KPDOT"]},
                             {"type": "app", "command": "mesh.extrude", "keys": ["e"]})
        self.assertEqual(profile.segments[0].macro_cw[0].command, "view3d.view_selected")
        self.assertEqual(profile.spacemouse_buttons["1"].press.keys, ["KEY_E"])

    def test_missing_commands_and_unknown_keys_are_explained(self):
        for step, action, message in (
            ({"type": "app"}, None, "command id"),
            ({"type": "app", "command": "a.b", "keys": ["KEY_NOPE"]}, None, "unknown key"),
            (None, {"type": "app"}, "command id"),
            (None, {"type": "app", "command": 5}, "command id"),
        ):
            with self.subTest(step=step, action=action):
                with self.assertRaisesRegex(ProfileTextError, message):
                    self.parse(step, action)


class DriverAppCommandTest(DriverCase):
    def setUp(self):
        super().setUp()
        self.link = FakeLink()
        self.pressed = []
        self.driver["app_link"] = self.link
        self.driver["window_binary"] = "org.blender.blender.desktop"
        self.driver["resolve_evdev_key"] = lambda name: name
        self.driver["send_key_event"] = lambda keys, *a, **k: self.pressed.append(keys)

    def run_command(self, command="mesh.extrude", keys=None):
        return self.driver["run_app_command"](command, keys)

    def notice(self):
        return self.driver["driver_state"]()["notice"]

    def test_a_connected_add_on_gets_the_command_for_the_focused_window(self):
        self.assertTrue(self.run_command("mesh.extrude", ["KEY_E"]))
        self.assertEqual(self.link.runs, [("org.blender.blender.desktop", "mesh.extrude")])
        self.assertEqual(self.pressed, [])
        self.assertEqual(self.notice(), {})

    def test_without_a_client_the_fallback_keys_are_pressed(self):
        self.link.outcome = (app_link.NO_CLIENT, "")
        self.assertFalse(self.run_command("mesh.extrude", ["KEY_E"]))
        self.assertEqual(self.pressed, ["KEY_E"])
        self.assertFalse(self.run_command("mesh.extrude", ["KEY_LEFTSHIFT", "KEY_E"]))
        self.assertEqual(self.pressed[-1], ["KEY_LEFTSHIFT", "KEY_E"])
        self.assertEqual(self.notice(), {})  # the shortcut did the job

    def test_an_unknown_command_also_falls_back(self):
        self.link.outcome = (app_link.UNKNOWN_COMMAND, "blender")
        self.assertFalse(self.run_command("old.id", ["KEY_E"]))
        self.assertEqual(self.pressed, ["KEY_E"])

    def test_without_fallback_the_screens_are_told(self):
        self.link.outcome = (app_link.NO_CLIENT, "")
        self.assertFalse(self.run_command("mesh.extrude"))
        self.assertEqual(self.pressed, [])
        notice = self.notice()
        self.assertIn("add-on", notice["text"])
        self.assertIn("mesh.extrude", notice["text"])
        first = notice["id"]
        self.link.outcome = (app_link.UNKNOWN_COMMAND, "blender")
        self.run_command("nope.nope")
        self.assertIn("does not know", self.notice()["text"])
        self.assertGreater(self.notice()["id"], first)
        self.link.outcome = (app_link.BUSY, "blender")
        self.run_command("mesh.extrude")
        self.assertIn("busy", self.notice()["text"])

    def test_the_notice_expires(self):
        self.link.outcome = (app_link.NO_CLIENT, "")
        self.run_command("mesh.extrude")
        self.assertTrue(self.notice())
        self.driver["app_notice"]["until"] = 0.0
        self.assertEqual(self.notice(), {})

    def test_a_failed_command_is_reported_by_the_add_on(self):
        self.driver["on_app_link_result"]("blender", "mesh.extrude", False, "poll failed")
        self.assertIn("poll failed", self.notice()["text"])
        before = self.notice()
        self.driver["on_app_link_result"]("blender", "mesh.extrude", True, "")
        self.assertEqual(self.notice(), before)

    def test_no_link_at_all_is_the_same_as_no_client(self):
        self.driver["app_link"] = None
        self.assertFalse(self.run_command("mesh.extrude", ["KEY_E"]))
        self.assertEqual(self.pressed, ["KEY_E"])

    def test_the_spacemouse_app_action_runs_like_a_macro_on_the_action_thread(self):
        action = SpaceMouseAction("app", command="view3d.view_selected", keys=["KEY_KPDOT"])
        ran = []
        self.driver["execute_config_macro"] = lambda steps: ran.append(steps)
        self.driver["run_spacemouse_action"](action)
        self.assertEqual(len(self.queued), 1)
        self.queued.pop()()
        self.assertEqual(ran, [[MacroStep(type=StepType.APP, command="view3d.view_selected", keys=["KEY_KPDOT"])]])

    def test_get_app_commands_answers_json(self):
        answer = json.loads(self.driver["on_dbus_app_commands"]("blender"))
        self.assertEqual((answer["app"], answer["connected"]), ("blender", True))
        self.driver["app_link"] = None
        answer = json.loads(self.driver["on_dbus_app_commands"]("blender"))
        self.assertEqual((answer["connected"], answer["commands"]), (False, []))

    def test_driver_state_tells_whether_the_focused_application_is_linked(self):
        self.link.context = {"mode": "OBJECT", "label": "Object Mode", "detail": ""}
        state = self.driver["driver_state"]()
        self.assertTrue(state["app_link"])
        self.assertEqual(state["app_context"]["mode"], "OBJECT")
        self.driver["window_binary"] = "org.kde.krita.desktop"
        state = self.driver["driver_state"]()
        self.assertEqual((state["app_link"], state["app_context"]), (False, {}))
        json.dumps(state)


class EndToEndTest(DriverCase):
    """The driver's run_app_command and context selection over a real socket with a fake add-on."""

    def setUp(self):
        super().setUp()
        import tempfile
        from tests.test_app_link import FakeClient, entry, wait_for

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.link = app_link.AppLink(
            os.path.join(self.directory.name, "asus-turnpad-apps.sock"),
            on_change=self.driver["on_app_link_change"], on_result=self.driver["on_app_link_result"],
        )
        self.assertTrue(self.link.start())
        self.addCleanup(self.link.stop)
        self.client = FakeClient(self.link.path)
        self.addCleanup(self.client.close)
        self.client.hello()
        self.client.send({"commands": [entry("view3d.view_selected", keys="KP_DOT")]})
        self.client.send({"context": {"mode": "EDIT_MESH", "label": "Edit Mesh", "detail": "Vertex"}})
        self.assertTrue(wait_for(lambda: self.link.context_for("blender") != {}))
        self.driver.update(app_link=self.link, window_binary="org.blender.blender.desktop",
                           resolve_evdev_key=lambda name: name, send_key_event=lambda keys, *a, **k: self.pressed.append(keys))
        self.pressed = []

    def test_command_known_to_the_add_on_goes_over_the_socket_and_its_failure_comes_back(self):
        self.assertTrue(self.driver["run_app_command"]("view3d.view_selected", ["KEY_KPDOT"]))
        messages = self.client.read(2)
        run = messages[-1]["run"]
        self.assertEqual(run["id"], "view3d.view_selected")
        self.client.send({"result": {"seq": run["seq"], "ok": False, "error": "no 3D view"}})
        from tests.test_app_link import wait_for
        self.assertTrue(wait_for(lambda: self.driver["driver_state"]()["notice"] != {}))
        self.assertIn("no 3D view", self.driver["driver_state"]()["notice"]["text"])

    def test_a_command_the_add_on_never_announced_uses_the_shortcut(self):
        self.assertFalse(self.driver["run_app_command"]("wm.quit_blender", ["KEY_Q"]))
        self.assertEqual(self.pressed, ["KEY_Q"])
        self.client.read(1, timeout=0.3)
        self.assertFalse([m for m in self.client.received if "run" in m])

    def test_the_state_follows_the_add_on(self):
        state = self.driver["driver_state"]()
        self.assertTrue(state["app_link"])
        self.assertEqual(state["app_context"]["detail"], "Vertex")
        self.client.close()
        from tests.test_app_link import wait_for
        self.assertTrue(wait_for(lambda: not self.driver["driver_state"]()["app_link"]))
        self.assertTrue(self.driver["pending_context_refresh"])


if __name__ == "__main__":
    unittest.main()
