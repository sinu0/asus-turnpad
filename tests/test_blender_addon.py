"""The Blender add-on (integrations/blender/turnpad): the line protocol, the
catalog, the non-blocking client against a real Unix socket, and the runner,
context tracker and registration against a fake bpy. Blender itself is not
needed; the catalog's operator names were checked against Blender 5.2 by hand
(`blender -b --factory-startup --python ...`, see the add-on's README notes in
the task report)."""

import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "integrations", "blender"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import fake_bpy                                  # noqa: E402

BPY = fake_bpy.install()

from turnpad import catalog, client, context, protocol, runner   # noqa: E402
import turnpad                                                   # noqa: E402
from turnpad import service as service_module                    # noqa: E402


class ProtocolTests(unittest.TestCase):
    def test_encode_is_one_utf8_line(self):
        line = protocol.encode({"hello": {"name": "Zażółć"}})
        self.assertTrue(line.endswith(b"\n"))
        self.assertEqual(line.count(b"\n"), 1)
        self.assertEqual(json.loads(line.decode("utf-8")), {"hello": {"name": "Zażółć"}})

    def test_too_long_message_refused(self):
        with self.assertRaises(protocol.LineTooLong):
            protocol.encode({"x": "a" * protocol.MAX_LINE})

    def test_message_builders(self):
        hello = protocol.hello_message("blender", "Blender", "5.2.0", 7)["hello"]
        self.assertEqual(hello, {"app": "blender", "name": "Blender", "version": "5.2.0",
                                 "pid": 7, "protocol": 1})
        self.assertEqual(protocol.context_message("EDIT_MESH", "Edit Mesh", "Vertex"),
                         {"context": {"mode": "EDIT_MESH", "label": "Edit Mesh", "detail": "Vertex"}})
        self.assertEqual(protocol.result_message(7, True), {"result": {"seq": 7, "ok": True, "error": ""}})
        long = protocol.result_message(1, False, "x" * 5000)["result"]["error"]
        self.assertEqual(len(long), protocol.MAX_ERROR)

    def test_catalog_message_limit(self):
        protocol.commands_message([{}] * protocol.MAX_COMMANDS)
        with self.assertRaises(ValueError):
            protocol.commands_message([{}] * (protocol.MAX_COMMANDS + 1))

    def test_line_buffer_frames_partial_and_multiple_lines(self):
        buffer = protocol.LineBuffer()
        self.assertEqual(buffer.feed(b'{"run": {"id": "a", '), [])
        got = buffer.feed(b'"seq": 1}}\n{"hello": {"protocol": 1}}\n{"run"')
        self.assertEqual(got, [{"run": {"id": "a", "seq": 1}}, {"hello": {"protocol": 1}}])
        self.assertEqual(buffer.feed(b': {}}\n'), [{"run": {}}])

    def test_line_buffer_skips_garbage(self):
        buffer = protocol.LineBuffer()
        got = buffer.feed(b'\xff\xfe\n not json \n[1, 2]\n\n{"ok": 1}\n')
        self.assertEqual(got, [{"ok": 1}])
        self.assertEqual(buffer.skipped, 3)

    def test_line_buffer_overflow(self):
        buffer = protocol.LineBuffer()
        with self.assertRaises(protocol.LineTooLong):
            buffer.feed(b"a" * protocol.MAX_LINE)
        buffer = protocol.LineBuffer()
        with self.assertRaises(protocol.LineTooLong):
            buffer.feed(b"a" * protocol.MAX_LINE + b"\n")

    def test_parse_run(self):
        self.assertEqual(protocol.parse_run({"run": {"id": "view.frame_all", "seq": 3}}),
                         ("view.frame_all", 3))
        for bad in ({}, {"run": 1}, {"run": {"id": "", "seq": 1}}, {"run": {"id": "a"}},
                    {"run": {"id": "a", "seq": "1"}}, {"run": {"id": "a", "seq": True}},
                    {"run": {"id": 5, "seq": 1}}, {"hello": {}}, [], None):
            self.assertIsNone(protocol.parse_run(bad), bad)

    def test_default_socket_path(self):
        self.assertEqual(protocol.default_socket_path({"XDG_RUNTIME_DIR": "/run/user/1000"}),
                         "/run/user/1000/asus-turnpad-apps.sock")
        self.assertIsNone(protocol.default_socket_path({}))


class CatalogTests(unittest.TestCase):
    def test_table_is_consistent(self):
        self.assertEqual(catalog.validate(), [])

    def test_size_and_categories(self):
        self.assertGreaterEqual(len(catalog.CATALOG), 70)
        self.assertLessEqual(len(catalog.CATALOG), protocol.MAX_COMMANDS)
        categories = {command.category for command in catalog.CATALOG}
        self.assertEqual(categories, {"View", "Modes", "Modeling", "Objects", "Transform",
                                      "Sculpt", "Animation", "UI"})

    def test_ids_are_stable_names(self):
        for command in catalog.CATALOG:
            self.assertRegex(command.id, r"^[a-z0-9_]+\.[a-z0-9_]+$")
        for expected in ("view.frame_selected", "view.front", "mesh.extrude", "mesh.loop_cut",
                         "object.duplicate", "transform.pie_snap", "sculpt.brush_size",
                         "anim.play", "ui.sidebar", "ui.undo", "ui.render", "mode.pie",
                         "view.pie_shading", "mode.toggle_edit"):
            self.assertIn(expected, catalog.BY_ID)

    def test_modes_are_known_context_modes(self):
        for command in catalog.CATALOG:
            for mode in command.modes:
                self.assertIn(mode, context.MODE_LABELS, command.id)

    def test_wire_entries(self):
        wire = catalog.wire_catalog()
        self.assertEqual(len(wire), len(catalog.CATALOG))
        for entry in wire:
            self.assertEqual(set(entry), {"id", "title", "category", "icon", "keys", "modes"})
            self.assertIsInstance(entry["modes"], list)
            self.assertNotIn("target", entry)
            self.assertNotIn("props", entry)
        line = protocol.encode(protocol.commands_message(wire))
        self.assertLess(len(line), protocol.MAX_LINE // 2)

    def test_props_are_plain_json(self):
        for command in catalog.CATALOG:
            json.dumps(command.props)

    def test_titles_unique_per_mode_set(self):
        seen = {}
        for command in catalog.CATALOG:
            key = (command.title, command.modes)
            self.assertNotIn(key, seen, "%s and %s" % (command.id, seen.get(key)))
            seen[key] = command.id


class FakeDriver:
    """The driver's end: a listening Unix socket in a private directory."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="tp-blender-")
        os.chmod(self.dir, 0o700)
        self.path = os.path.join(self.dir, "asus-turnpad-apps.sock")
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(self.path)
        self.server.listen(2)
        self.server.setblocking(False)
        self.conn = None
        self.buffer = protocol.LineBuffer()
        self.received = []

    def accept(self):
        try:
            self.conn, _ = self.server.accept()
            self.conn.setblocking(False)
            return True
        except BlockingIOError:
            return False

    def pump(self):
        if self.conn is None:
            return
        try:
            data = self.conn.recv(65536)
        except BlockingIOError:
            return
        self.received.extend(self.buffer.feed(data))

    def send(self, message):
        self.conn.sendall(protocol.encode(message))

    def send_raw(self, data):
        self.conn.sendall(data)

    def drop(self):
        self.conn.close()
        self.conn = None
        self.buffer = protocol.LineBuffer()

    def close(self):
        for sock in (self.conn, self.server):
            if sock is not None:
                sock.close()
        shutil.rmtree(self.dir, ignore_errors=True)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def wait_for(condition, *steps, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for step in steps:
            step()
        if condition():
            return True
        time.sleep(0.005)
    return False


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.driver = FakeDriver()
        self.addCleanup(self.driver.close)
        self.clock = Clock()
        self.runs = []
        self.answer = (True, "")
        self.client = client.AppLinkClient(
            lambda: self.driver.path,
            protocol.hello_message("blender", "Blender", "5.2.2", 1),
            protocol.commands_message([{"id": "view.frame_all"}]),
            self._on_run, clock=self.clock)
        self.addCleanup(self.client.close)

    def _on_run(self, command):
        self.runs.append(command)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def steps(self):
        self.client.tick()
        self.driver.accept()
        self.driver.pump()

    def kinds(self):
        return [next(iter(message)) for message in self.driver.received]

    def test_connect_sends_hello_catalog_and_context(self):
        self.client.send_context(protocol.context_message("OBJECT", "Object Mode", ""))
        self.assertFalse(self.client.connected)
        self.assertTrue(wait_for(lambda: self.kinds() == ["hello", "commands", "context"], self.steps))
        self.assertTrue(self.client.connected)
        self.assertEqual(self.driver.received[0]["hello"]["app"], "blender")
        self.assertEqual(self.driver.received[2]["context"]["mode"], "OBJECT")

    def test_context_sent_after_connect(self):
        self.assertTrue(wait_for(lambda: self.client.connected, self.steps))
        self.client.send_context(protocol.context_message("SCULPT", "Sculpt Mode", ""))
        self.assertTrue(wait_for(lambda: "context" in self.kinds(), self.steps))

    def test_run_request_gets_a_result(self):
        self.assertTrue(wait_for(lambda: self.client.connected, self.steps))
        self.driver.send({"hello": {"protocol": 1}})
        self.driver.send({"run": {"id": "view.frame_all", "seq": 7}})
        self.assertTrue(wait_for(lambda: "result" in self.kinds(), self.steps))
        self.assertEqual(self.runs, ["view.frame_all"])
        self.assertEqual(self.driver.received[-1], {"result": {"seq": 7, "ok": True, "error": ""}})
        self.assertEqual(self.client.driver_protocol, 1)

    def test_failed_and_raising_commands_report_errors(self):
        self.assertTrue(wait_for(lambda: self.client.connected, self.steps))
        self.answer = (False, "no 3D view")
        self.driver.send({"run": {"id": "a.b", "seq": 1}})
        self.assertTrue(wait_for(lambda: "result" in self.kinds(), self.steps))
        self.assertEqual(self.driver.received[-1]["result"], {"seq": 1, "ok": False, "error": "no 3D view"})
        self.answer = RuntimeError("boom")
        self.driver.send({"run": {"id": "a.b", "seq": 2}})
        self.assertTrue(wait_for(lambda: len([k for k in self.kinds() if k == "result"]) == 2, self.steps))
        last = self.driver.received[-1]["result"]
        self.assertEqual((last["seq"], last["ok"]), (2, False))
        self.assertIn("boom", last["error"])

    def test_malformed_requests_are_ignored(self):
        self.assertTrue(wait_for(lambda: self.client.connected, self.steps))
        self.driver.send_raw(b'{"run": {"id": "a.b"}}\nnot json\n{"run": {"id": "x.y", "seq": 4}}\n')
        self.assertTrue(wait_for(lambda: "result" in self.kinds(), self.steps))
        self.assertEqual(self.runs, ["x.y"])

    def test_reconnects_every_two_seconds(self):
        self.assertTrue(wait_for(lambda: self.client.connected, self.steps))
        self.driver.drop()
        self.assertTrue(wait_for(lambda: not self.client.connected, self.client.tick))
        self.assertIn("closed", self.client.status())
        self.driver.received.clear()
        for _ in range(5):
            self.client.tick()
        self.assertFalse(self.client.connected, "no reconnect before the delay")
        self.clock.now += client.RECONNECT_S + 0.1
        self.assertTrue(wait_for(lambda: self.kinds()[:2] == ["hello", "commands"], self.steps))
        self.assertTrue(self.client.connected)

    def test_no_driver_is_a_status_not_an_error(self):
        os.unlink(self.driver.path)
        self.client.tick()
        self.assertFalse(self.client.connected)
        self.assertIn("Waiting", self.client.status())
        os.symlink(os.path.join(self.driver.dir, "nothing"), self.driver.path)
        self.clock.now += 3
        self.client.tick()
        self.assertFalse(self.client.connected)

    def test_refused_connection(self):
        self.driver.server.close()      # socket file stays, nobody listens
        self.client.tick()
        self.assertFalse(self.client.connected)
        self.assertIn("Cannot reach", self.client.status())

    def test_no_socket_path(self):
        c = client.AppLinkClient(lambda: None, {}, {}, self._on_run, clock=self.clock)
        c.tick()
        self.assertFalse(c.connected)
        self.assertIn("XDG_RUNTIME_DIR", c.status())

    def test_overlong_line_from_driver_drops_the_link(self):
        self.assertTrue(wait_for(lambda: self.client.connected, self.steps))
        self.driver.send_raw(b"a" * (protocol.MAX_LINE + 10))
        self.assertTrue(wait_for(lambda: not self.client.connected, self.client.tick))
        self.assertIn("too long", self.client.status())

    def test_queue_is_bounded_while_the_driver_does_not_read(self):
        self.assertTrue(wait_for(lambda: self.client.connected, self.steps))
        for index in range(client.MAX_QUEUE * 3):
            self.client.send_context(protocol.context_message("M%d" % index, "x", ""))
        self.assertLessEqual(len(self.client._outgoing), client.MAX_QUEUE)

    def test_socket_of_another_user_is_not_used(self):
        real_uid = os.getuid
        client.os.getuid = lambda: real_uid() + 1
        try:
            self.client.tick()
        finally:
            client.os.getuid = real_uid
        self.assertFalse(self.client.connected)
        self.assertIn("another user", self.client.status())


def window_with(*areas):
    window = fake_bpy.Window(*areas)
    BPY.windows[:] = [window]
    return window


class RunnerTests(unittest.TestCase):
    def setUp(self):
        BPY.ops.calls.clear()
        BPY.ops.results.clear()
        BPY.ops.errors.clear()
        BPY.ops.overrides_seen.clear()
        BPY.windows[:] = []
        BPY.context.mode = "OBJECT"
        BPY.app.background = False

    def test_unknown_command(self):
        self.assertEqual(runner.run("os.system"), (False, "unknown command"))
        self.assertEqual(BPY.ops.calls, [])

    def test_uses_the_largest_3d_view_and_its_window_region(self):
        small, big = fake_bpy.Area("VIEW_3D", 100, 100), fake_bpy.Area("VIEW_3D", 800, 600)
        window = window_with(small, fake_bpy.Area("PROPERTIES", 2000, 2000), big)
        self.assertEqual(runner.run("view.frame_all"), (True, ""))
        override = BPY.ops.overrides_seen[-1]
        self.assertIs(override["area"], big)
        self.assertIs(override["window"], window)
        self.assertIs(override["screen"], window.screen)
        self.assertEqual(override["region"].type, "WINDOW")

    def test_call_op_pie_and_menu_use_their_execution_context(self):
        window_with(fake_bpy.Area("VIEW_3D"))
        runner.run("view.front")
        runner.run("transform.grab")
        runner.run("view.pie_shading")
        runner.run("object.add_menu")
        self.assertEqual(BPY.ops.calls, [
            ("view3d.view_axis", "EXEC_DEFAULT", {"type": "FRONT"}),
            ("transform.translate", "INVOKE_DEFAULT", {}),
            ("wm.call_menu_pie", "INVOKE_DEFAULT", {"name": "VIEW3D_MT_shading_pie"}),
            ("wm.call_menu", "INVOKE_DEFAULT", {"name": "VIEW3D_MT_add"}),
        ])

    def test_properties_come_from_the_catalog(self):
        window_with(fake_bpy.Area("VIEW_3D"))
        runner.run("mode.toggle_edit")
        self.assertEqual(BPY.ops.calls[-1],
                         ("object.mode_set", "EXEC_DEFAULT", {"mode": "EDIT", "toggle": True}))

    def test_no_window_fails_gracefully(self):
        self.assertEqual(runner.run("view.frame_all"), (False, "no 3D view"))
        self.assertEqual(runner.run("anim.play"), (False, "no Blender window"))
        self.assertEqual(BPY.ops.calls, [])

    def test_window_without_3d_view(self):
        window_with(fake_bpy.Area("PROPERTIES"))
        self.assertEqual(runner.run("view.frame_all"), (False, "no 3D view"))
        self.assertEqual(runner.run("anim.play"), (True, ""))       # any area will do
        self.assertEqual(BPY.ops.overrides_seen[-1]["area"].type, "PROPERTIES")

    def test_data_operators_run_bare_without_a_window(self):
        self.assertEqual(runner.run("object.select_all"), (True, ""))
        self.assertEqual(BPY.ops.overrides_seen[-1], {})

    def test_any_prefers_a_3d_view(self):
        window_with(fake_bpy.Area("PROPERTIES", 5000, 5000), fake_bpy.Area("VIEW_3D", 10, 10))
        runner.run("anim.play")
        self.assertEqual(BPY.ops.overrides_seen[-1]["area"].type, "VIEW_3D")

    def test_mode_is_enforced(self):
        window_with(fake_bpy.Area("VIEW_3D"))
        ok, error = runner.run("sculpt.mask_invert")
        self.assertFalse(ok)
        self.assertIn("Sculpt Mode", error)
        self.assertEqual(BPY.ops.calls, [])
        BPY.context.mode = "SCULPT"
        self.assertEqual(runner.run("sculpt.mask_invert"), (True, ""))

    def test_menus_refused_in_a_background_session(self):
        window_with(fake_bpy.Area("VIEW_3D"))
        BPY.app.background = True
        ok, error = runner.run("view.pie_view")
        self.assertFalse(ok)
        self.assertIn("window", error)
        self.assertEqual(BPY.ops.calls, [])

    def test_operator_results(self):
        window_with(fake_bpy.Area("VIEW_3D"))
        BPY.ops.results["transform.translate"] = {"RUNNING_MODAL"}
        self.assertEqual(runner.run("transform.grab"), (True, ""))
        BPY.ops.results["view3d.view_all"] = {"CANCELLED"}
        ok, error = runner.run("view.frame_all")
        self.assertFalse(ok)
        self.assertIn("cancelled", error)
        BPY.ops.results["view3d.view_all"] = {"PASS_THROUGH"}
        self.assertFalse(runner.run("view.frame_all")[0])
        # UI properties are not undoable: wm.context_toggle says CANCELLED though it worked
        BPY.ops.results["wm.context_toggle"] = {"CANCELLED"}
        self.assertEqual(runner.run("ui.sidebar"), (True, ""))

    def test_exceptions_become_errors(self):
        window_with(fake_bpy.Area("VIEW_3D"))
        BPY.ops.errors["view3d.view_all"] = RuntimeError(
            "Operator bpy.ops.view3d.view_all.poll() failed, context is incorrect")
        ok, error = runner.run("view.frame_all")
        self.assertFalse(ok)
        self.assertIn("not available here", error)
        BPY.ops.errors["view3d.view_all"] = ValueError("x" * 1000)
        self.assertLessEqual(len(runner.run("view.frame_all")[1]), protocol.MAX_ERROR)


class ContextTests(unittest.TestCase):
    def setUp(self):
        BPY.windows[:] = []
        BPY.context.mode = "OBJECT"
        BPY.context.tool_settings.mesh_select_mode = (True, False, False)

    def test_labels(self):
        self.assertEqual(context.label_for("EDIT_MESH"), "Edit Mesh")
        self.assertEqual(context.label_for("SOMETHING_NEW"), "Something New")

    def test_select_mode_detail(self):
        self.assertEqual(context.select_mode_detail((True, False, False)), "Vertex")
        self.assertEqual(context.select_mode_detail((False, True, True)), "Edge, Face")
        self.assertEqual(context.select_mode_detail((False, False, False)), "")

    def test_read_edit_mesh_and_tool(self):
        BPY.context.mode = "EDIT_MESH"
        BPY.context.tool_settings.mesh_select_mode = (False, False, True)
        self.assertEqual(context.read(), {"mode": "EDIT_MESH", "label": "Edit Mesh", "detail": "Face"})
        BPY.context.mode = "SCULPT"
        window_with(fake_bpy.Area("VIEW_3D"))
        self.assertEqual(context.read(), {"mode": "SCULPT", "label": "Sculpt Mode",
                                          "detail": "builtin.move"})

    def test_read_survives_a_failing_tool_lookup(self):
        class Broken:
            def __getattr__(self, name):
                raise RuntimeError(name)
        BPY.context.mode = "EDIT_MESH"
        saved = BPY.context.tool_settings
        BPY.context.tool_settings = Broken()
        try:
            self.assertEqual(context.read()["detail"], "")
        finally:
            BPY.context.tool_settings = saved

    def test_tracker_reports_changes_only(self):
        values = [{"mode": "OBJECT", "label": "Object Mode", "detail": ""}] * 2 + [
            {"mode": "SCULPT", "label": "Sculpt Mode", "detail": ""}, {}]
        tracker = context.ContextTracker(lambda: values.pop(0))
        self.assertEqual(tracker.poll()["mode"], "OBJECT")
        self.assertIsNone(tracker.poll())
        self.assertEqual(tracker.poll()["mode"], "SCULPT")
        self.assertIsNone(tracker.poll())               # unreadable: nothing sent

    def test_tracker_reset_resends(self):
        tracker = context.ContextTracker(lambda: {"mode": "OBJECT", "label": "x", "detail": ""})
        self.assertIsNotNone(tracker.poll())
        self.assertIsNone(tracker.poll())
        tracker.reset()
        self.assertIsNotNone(tracker.poll())


class ServiceTests(unittest.TestCase):
    def setUp(self):
        BPY.ops.calls.clear()
        BPY.ops.errors.clear()
        BPY.ops.results.clear()
        BPY.context.mode = "OBJECT"
        window_with(fake_bpy.Area("VIEW_3D"))
        self.driver = FakeDriver()
        self.addCleanup(self.driver.close)
        self.settings = [True, self.driver.path]
        self.service = service_module.Service(lambda: tuple(self.settings))
        self.addCleanup(self.service.stop)

    def steps(self):
        self.service.tick()
        self.driver.accept()
        self.driver.pump()

    def kinds(self):
        return [next(iter(message)) for message in self.driver.received]

    def test_hello_catalog_context_then_run(self):
        self.assertTrue(wait_for(lambda: self.kinds()[:3] == ["hello", "commands", "context"], self.steps))
        self.assertEqual(len(self.driver.received[1]["commands"]), len(catalog.CATALOG))
        self.driver.send({"run": {"id": "view.frame_selected", "seq": 9}})
        self.assertTrue(wait_for(lambda: "result" in self.kinds(), self.steps))
        self.assertEqual(self.driver.received[-1]["result"], {"seq": 9, "ok": True, "error": ""})
        self.assertEqual(BPY.ops.calls[-1][0], "view3d.view_selected")

    def test_mode_change_is_sent_once(self):
        self.assertTrue(wait_for(lambda: "context" in self.kinds(), self.steps))
        BPY.context.mode = "SCULPT"
        self.assertTrue(wait_for(lambda: self.kinds().count("context") == 2, self.steps))
        for _ in range(service_module.CONTEXT_EVERY * 5):
            self.steps()
        self.assertEqual(self.kinds().count("context"), 2)
        self.assertEqual(self.driver.received[-1]["context"]["mode"], "SCULPT")

    def test_disabled_service_does_not_connect(self):
        self.settings[0] = False
        for _ in range(20):
            self.steps()
        self.assertIsNone(self.driver.conn)
        self.assertEqual(self.service.tick(), 0.5)

    def test_file_load_resends_the_context(self):
        self.assertTrue(wait_for(lambda: "context" in self.kinds(), self.steps))
        self.service.file_loaded()
        self.assertTrue(wait_for(lambda: self.kinds().count("context") == 2, self.steps))

    def test_socket_path_override_and_default(self):
        self.assertEqual(self.service._socket_path(), self.driver.path)
        self.settings[1] = ""
        saved = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["XDG_RUNTIME_DIR"] = "/run/user/42"
        try:
            self.assertEqual(self.service._socket_path(), "/run/user/42/asus-turnpad-apps.sock")
        finally:
            if saved is None:
                del os.environ["XDG_RUNTIME_DIR"]
            else:
                os.environ["XDG_RUNTIME_DIR"] = saved


class RegistrationTests(unittest.TestCase):
    def tearDown(self):
        turnpad.unregister()

    def test_register_and_unregister_leave_nothing_behind(self):
        turnpad.register()
        self.assertTrue(BPY.app.timers.is_registered(turnpad._tick))
        self.assertIn(turnpad._on_load_post, BPY.app.handlers.load_post)
        self.assertIn(turnpad.TurnpadPreferences, BPY.registered)
        self.assertIsNotNone(turnpad._state["service"])
        turnpad.register()                               # twice is harmless
        turnpad.unregister()
        self.assertFalse(BPY.app.timers.is_registered(turnpad._tick))
        self.assertNotIn(turnpad._on_load_post, BPY.app.handlers.load_post)
        self.assertNotIn(turnpad.TurnpadPreferences, BPY.registered)
        self.assertIsNone(turnpad._state["service"])
        turnpad.unregister()                             # and so is a second unregister

    def test_timer_never_raises(self):
        class Broken:
            def tick(self):
                raise RuntimeError("bug")

            def file_loaded(self):
                raise RuntimeError("bug")

            def stop(self):
                raise RuntimeError("bug")
        turnpad._state["service"] = Broken()
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(turnpad._tick(), 1.0)
            turnpad._on_load_post()
            turnpad.unregister()                         # logs, does not raise
        self.assertIn("Turnpad: timer failed", printed.getvalue())

    def test_timer_stops_without_service(self):
        turnpad._state["service"] = None
        self.assertIsNone(turnpad._tick())

    def test_settings_default_without_preferences(self):
        self.assertEqual(turnpad._settings(), (True, ""))


@unittest.skipUnless(shutil.which("blender"), "Blender is not installed")
class RealBlenderTests(unittest.TestCase):
    def test_catalog_matches_this_blender(self):
        done = subprocess.run(
            ["blender", "-b", "--factory-startup", "--python",
             os.path.join(ROOT, "tests", "blender_catalog_check.py")],
            capture_output=True, text=True, timeout=180)
        lines = [line for line in done.stdout.splitlines() if line.startswith(("MISSING", "CHECKED"))]
        self.assertEqual([line for line in lines if line.startswith("MISSING")], [], done.stdout[-2000:])
        self.assertIn("CHECKED %d" % len(catalog.CATALOG), lines)


class ManifestTests(unittest.TestCase):
    def test_manifest(self):
        path = os.path.join(ROOT, "integrations", "blender", "turnpad", "blender_manifest.toml")
        with open(path, "rb") as stream:
            try:
                import tomllib
            except ImportError:                          # pragma: no cover
                self.skipTest("no tomllib")
            manifest = tomllib.load(stream)
        self.assertEqual(manifest["schema_version"], "1.0.0")
        self.assertEqual(manifest["id"], "turnpad")
        self.assertEqual(manifest["type"], "add-on")
        self.assertEqual(manifest["blender_version_min"], "4.2.0")
        self.assertEqual(manifest["license"], ["SPDX:GPL-3.0-or-later"])
        self.assertIn("files", manifest["permissions"])
        for reason in manifest["permissions"].values():
            self.assertLessEqual(len(reason), 64)            # the extension validator's limit
        self.assertLessEqual(len(manifest["tagline"]), 64)


if __name__ == "__main__":
    unittest.main()
