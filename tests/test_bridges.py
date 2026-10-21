"""Application bridges (integrations/bridges.py) and the SolidCraft bridge
(integrations/solidcraft/) against a fake SolidCraft control channel
(tests/fake_solidcraft.py) and the real app link server, in private temporary
directories (never the user's runtime or data folders)."""

import ast
import json
import math
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import app_link  # noqa: E402
from fake_solidcraft import FakeSolidCraft  # noqa: E402
from integrations import bridge_base, bridges  # noqa: E402
from integrations import solidcraft  # noqa: E402
from integrations.applink import protocol  # noqa: E402
from integrations.solidcraft import catalog, context, control, plugin, runner  # noqa: E402

DESKTOP_ID = "ai.storyteller.solidcraft.desktop"


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def code_without_docstring(path):
    tree = ast.parse(Path(path).read_text())
    body = tree.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return ast.dump(ast.Module(body=body, type_ignores=[]))


class TwinsTest(unittest.TestCase):
    def test_app_link_client_is_the_blender_add_ons(self):
        # The add-on is zipped on its own, so it keeps a copy; the code must not drift.
        for name in ("protocol.py", "client.py"):
            with self.subTest(file=name):
                self.assertEqual(code_without_docstring(ROOT / "integrations" / "applink" / name),
                                 code_without_docstring(ROOT / "integrations" / "blender" / "turnpad" / name))

    def test_wire_limits_match_the_driver(self):
        self.assertEqual(protocol.PROTOCOL, app_link.PROTOCOL)
        self.assertEqual(protocol.MAX_LINE, app_link.MAX_LINE)
        self.assertEqual(protocol.MAX_COMMANDS, app_link.MAX_COMMANDS)


class TokenFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "control")
        os.mkdir(self.dir, 0o700)

    def tearDown(self):
        self.tmp.cleanup()

    def token_file(self, name, text="ab" * 32, mode=0o600):
        path = os.path.join(self.dir, name)
        with open(path, "w") as handle:
            handle.write(text + "\n")
        os.chmod(path, mode)
        return path

    def test_token_dirs(self):
        environ = {"HOME": "/home/u", "SOLIDCRAFT_CONTROL_DIR": "/run/x"}
        self.assertEqual(control.token_dirs(environ), [
            "/run/x",
            "/home/u/.local/share/solidcraft/control",
            "/home/u/.var/app/ai.storyteller.solidcraft/data/solidcraft/control",
            "/home/u/snap/solidcraft/current/.local/share/solidcraft/control",
        ])
        self.assertEqual(control.token_dirs({"HOME": "/h", "XDG_DATA_HOME": "/d"})[0], "/d/solidcraft/control")

    def test_only_private_token_files_of_this_user(self):
        old = self.token_file("7878.token")
        os.utime(old, (1000, 1000))
        self.token_file("7879.token")
        self.token_file("7880.token", mode=0o644)          # readable by others
        self.token_file("notes.txt")
        self.token_file("70000.token")                     # no such port
        os.symlink(old, os.path.join(self.dir, "7881.token"))
        found = control.find_instances([self.dir, os.path.join(self.tmp.name, "missing")])
        self.assertEqual([instance.port for instance in found], [7879, 7878], "newest first")
        self.assertEqual(control.find_instances([self.dir], uid=os.getuid() + 1), [])

    def test_read_token(self):
        self.assertEqual(control.read_token(self.token_file("1.token", "  " + "AB" * 32 + " ")), "AB" * 32)
        for name, text, mode in (("2.token", "not a token", 0o600), ("3.token", "ab" * 32, 0o640),
                                 ("4.token", "ab" * 3000, 0o600)):
            with self.subTest(name=name), self.assertRaises(control.ControlError):
                control.read_token(self.token_file(name, text, mode))
        os.symlink(self.token_file("5.token"), os.path.join(self.dir, "6.token"))
        with self.assertRaises(control.ControlError):
            control.read_token(os.path.join(self.dir, "6.token"))

    def test_port_owner(self):
        table = os.path.join(self.tmp.name, "tcp")
        with open(table, "w") as handle:
            handle.write("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid\n")
            handle.write("   0: 0100007F:1EC6 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000 0 1 1\n")
            handle.write("   1: 0100007F:1EC7 0100007F:9C40 01 00000000:00000000 00:00000000 00000000  1001 0 2 1\n")
        self.assertEqual(control.port_owner(7878, (table,)), 1000)
        self.assertIsNone(control.port_owner(7879, (table,)), "an established connection is not a listener")
        self.assertIsNone(control.port_owner(7878, (os.path.join(self.tmp.name, "none"),)))


class ControlClientTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = FakeSolidCraft(os.path.join(self.tmp.name, "control"))

    def tearDown(self):
        self.app.stop()
        self.tmp.cleanup()

    def test_token_on_the_first_request_only(self):
        client = control.ControlClient(self.app.port, self.app.token)
        client.open()
        self.assertEqual(client.call("ui.help")["version"], "0.18.1")
        self.assertEqual(client.call("ui.camera")["distance"], 200.0)
        with self.assertRaisesRegex(control.ControlError, "unknown view"):
            client.call("ui.view", {"view": "sideways"})
        self.assertTrue(client.connected, "an error of the app keeps the connection")
        client.close()

    def test_wrong_token_closes(self):
        client = control.ControlClient(self.app.port, "cd" * 32)
        client.open()
        with self.assertRaisesRegex(control.ConnectionLost, "unauthorized"):
            client.call("ui.help")
        self.assertFalse(client.connected)

    def test_no_reply_in_time_closes(self):
        client = control.ControlClient(self.app.port, self.app.token, timeout=0.2)
        client.open()
        client.call("ui.help")
        self.app.delay.set()
        with self.assertRaisesRegex(control.ConnectionLost, "no reply"):
            client.call("ui.inspect")
        self.assertFalse(client.connected)

    def test_nothing_listening(self):
        port = self.app.port
        self.app.stop()
        with self.assertRaises(control.ConnectionLost):
            control.ControlClient(port, self.app.token, timeout=0.5).open()


class CatalogTest(unittest.TestCase):
    def test_view_commands_first_then_the_toolbar(self):
        from fake_solidcraft import REGISTRY

        entries = catalog.wire_catalog(REGISTRY)
        ids = [entry["id"] for entry in entries]
        views = [command.id for command in catalog.VIEW_COMMANDS]
        self.assertEqual(ids[:len(views)], views)
        self.assertEqual(ids[len(views):], ["edit.undo", "solid.extrude", "solid.fillet", "sketch.line", "sheet.flange"])
        extrude = entries[ids.index("solid.extrude")]
        self.assertEqual(extrude, {"id": "solid.extrude", "title": "Extrude", "category": "Model › Create",
                                   "icon": "", "keys": "E", "modes": ["model"]})
        self.assertEqual(entries[ids.index("sheet.flange")]["modes"], ["sheet_metal"])
        self.assertEqual(entries[ids.index("edit.undo")]["category"], "Edit")
        # The driver keeps every entry as it is.
        self.assertEqual(app_link.clean_catalog(entries), entries)

    def test_cut_at_the_driver_limit(self):
        many = [{"id": f"solid.c{index}", "label": "C", "tab": "SOLID", "panel": "CREATE"} for index in range(900)]
        entries = catalog.wire_catalog(many)
        self.assertEqual(len(entries), protocol.MAX_COMMANDS)
        protocol.encode(protocol.commands_message(entries))   # fits on one line

    def test_garbage_registry(self):
        self.assertEqual(len(catalog.wire_catalog(None)), len(catalog.VIEW_COMMANDS))
        self.assertEqual(len(catalog.wire_catalog([1, "x", {"tab": "SOLID"}])), len(catalog.VIEW_COMMANDS) + 1)


class ContextTest(unittest.TestCase):
    labels = {"sketch.line": "Line"}

    def test_tab_tool_and_dialog(self):
        self.assertEqual(context.context_of({"ui": {"tab": "SKETCH"}, "tool": "sketch.line"}, self.labels),
                         {"mode": "sketch", "label": "Sketch", "detail": "Line"})
        dialog = "Dialog { kind: SketchDimension(Dim { value: 3 }), inputs: [] }"
        self.assertEqual(context.context_of({"ui": {"tab": "SHEET METAL"}, "dialog": dialog}, self.labels),
                         {"mode": "sheet_metal", "label": "Sheet Metal", "detail": "Sketch Dimension"})
        self.assertEqual(context.context_of({"ui": {"tab": "RENDER"}}, self.labels)["mode"], "render")
        self.assertEqual(context.context_of({"ui": {}}, self.labels), {})
        self.assertEqual(context.context_of("nonsense", self.labels), {})

    def test_viewport(self):
        self.assertEqual(context.viewport_of({"viewport": [0, 80, 1200, 800]}), [0, 80, 1200, 800])
        self.assertIsNone(context.viewport_of({"viewport": None}))
        self.assertIsNone(context.viewport_of({"viewport": [0, "x", 1, 2]}))


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.camera = {"yaw": 0.0, "pitch": 1.5, "roll": 6.27, "distance": 100.0, "target": [0, 0, 0]}
        self.mode = "model"
        entries = catalog.wire_catalog([
            {"id": "solid.extrude", "label": "Extrude", "tab": "SOLID", "panel": "CREATE", "shortcut": "E"},
            {"id": "sketch.line", "label": "Line", "tab": "SKETCH", "panel": "CREATE", "shortcut": "L"},
            {"id": "sketch.ellipse", "label": "Ellipse", "tab": "SKETCH", "panel": "CREATE"},
            {"id": "sketch.finish", "label": "Finish Sketch", "tab": "SKETCH", "panel": "FINISH SKETCH"},
        ])
        self.runner = runner.Runner(self.call, entries, viewport=lambda: [0, 0, 1000, 500], mode=lambda: self.mode)

    def call(self, method, params=None):
        self.calls.append((method, params))
        if method == "ui.camera":
            return dict(self.camera)
        if method == "ui.view" and params["view"] == "nowhere":
            raise control.ControlError("unknown view")
        return {}

    def test_commands_start_like_the_toolbar(self):
        self.assertEqual(self.runner.run("solid.extrude"), (True, ""))
        self.assertEqual(self.calls, [("ui.start", {"command": "solid.extrude"})])

    def test_a_sketch_tool_outside_a_sketch_is_its_key(self):
        # SolidCraft opens Create Sketch for the key and starts the tool in the new sketch.
        self.runner.run("sketch.line")
        self.runner.run("sketch.ellipse")       # no key: the toolbar's way
        self.runner.run("sketch.finish")
        self.mode = "sketch"
        self.runner.run("sketch.line")
        self.assertEqual(self.calls, [("ui.key", {"key": "L"}), ("ui.start", {"command": "sketch.ellipse"}),
                                      ("ui.start", {"command": "sketch.finish"}),
                                      ("ui.start", {"command": "sketch.line"})])

    def test_ids_outside_the_catalog_never_reach_solidcraft(self):
        self.assertEqual(self.runner.run("doc.open"), (False, "not in the catalog"))
        self.assertEqual(self.calls, [])

    def test_camera_steps(self):
        step = math.radians(runner.ORBIT_STEP_DEGREES)
        expected = {
            "turnpad.orbit_left": {"yaw": step},
            "turnpad.orbit_right": {"yaw": math.tau - step},
            "turnpad.orbit_up": {"pitch": math.pi / 2 - 1e-3},       # clamped
            "turnpad.orbit_down": {"pitch": 1.5 - step},
            "turnpad.roll_cw": {"roll": (6.27 + math.radians(runner.ROLL_STEP_DEGREES)) % math.tau},
            "turnpad.zoom_in": {"distance": 100.0 * runner.ZOOM_STEP},
            "turnpad.zoom_out": {"distance": 100.0 / runner.ZOOM_STEP},
        }
        for command_id, change in expected.items():
            with self.subTest(command=command_id):
                self.calls.clear()
                self.assertEqual(self.runner.run(command_id), (True, ""))
                self.assertEqual(self.calls[0], ("ui.camera", {}))
                method, params = self.calls[1]
                self.assertEqual(method, "ui.camera")
                self.assertEqual(params.keys(), change.keys())
                for key, value in change.items():
                    self.assertAlmostEqual(params[key], value)

    def test_pan_uses_the_viewport(self):
        self.assertEqual(self.runner.run("turnpad.pan_left"), (True, ""))
        method, params = self.calls[-1]
        self.assertEqual(method, "engine.execute")
        self.assertEqual(params["command"], "view.navigate")
        self.assertEqual(params["params"]["motion"], "pan")
        self.assertEqual(params["params"]["viewport"], [1000.0, 500.0])
        self.assertEqual(params["params"]["delta"], [-500 * runner.PAN_STEP, 0])

    def test_quarter_rolls_animate(self):
        self.runner.run("turnpad.roll_quarter_ccw")
        self.assertEqual(self.calls[-1], ("engine.execute", {"command": "view.navigate", "params": {
            "camera": self.camera, "action": "counterclockwise"}}))

    def test_views_and_search(self):
        self.runner.run("turnpad.view_top")
        self.runner.run("turnpad.search")
        self.assertEqual(self.calls, [("ui.view", {"view": "top", "animate": True}), ("ui.palette", {})])

    def test_errors_come_back(self):
        self.runner._entries["turnpad.view_nowhere"] = {"id": "turnpad.view_nowhere", "keys": "", "modes": []}
        catalog.VIEW_BY_ID["turnpad.view_nowhere"] = catalog.ViewCommand("turnpad.view_nowhere", "x", "view", "nowhere")
        try:
            self.assertEqual(self.runner.run("turnpad.view_nowhere"), (False, "unknown view"))
        finally:
            del catalog.VIEW_BY_ID["turnpad.view_nowhere"]


class BridgeTest(unittest.TestCase):
    """The whole way: driver (AppLink.run) -> bridge -> SolidCraft (fake)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        runtime = os.path.join(self.tmp.name, "run")
        os.mkdir(runtime, 0o700)
        self.results = []
        self.link = app_link.AppLink(os.path.join(runtime, "apps.sock"),
                                     on_result=lambda *result: self.results.append(result))
        self.assertTrue(self.link.start())
        self.environ = {"HOME": os.path.join(self.tmp.name, "home"),
                        "SOLIDCRAFT_CONTROL_DIR": os.path.join(self.tmp.name, "control")}
        self.app = FakeSolidCraft(self.environ["SOLIDCRAFT_CONTROL_DIR"])
        # The token folder is <tmp>/control, so <tmp> is this SolidCraft's data folder.
        self.data_root = self.tmp.name
        plugin.install(self.data_root)
        self.bridge = solidcraft.Bridge(self.link.path, environ=self.environ)
        # Quicker than in the driver, so switching the plugin is seen within the test.
        self.bridge.CONTEXT_S = 0.05
        self.bridge.REFUSE_S = 0.3
        self.host = bridges.BridgeHost(self.link.path, modules=())
        self.host._bridges = [("solidcraft", self.bridge)]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            self._stop.wait(self.host.tick_all())

    def tearDown(self):
        self._stop.set()
        self._thread.join(5)
        self.bridge.stop()
        self.app.stop()
        self.link.stop()
        self.tmp.cleanup()

    def linked(self):
        return self.link.commands(DESKTOP_ID)["connected"]

    def test_commands_reach_solidcraft(self):
        self.assertTrue(wait_for(self.linked))
        answer = self.link.commands(DESKTOP_ID)
        self.assertEqual((answer["app"], answer["name"], answer["version"]), ("solidcraft", "SolidCraft", "0.18.1"))
        self.assertIn("solid.extrude", [entry["id"] for entry in answer["commands"]])
        self.assertTrue(wait_for(lambda: self.link.context_for(DESKTOP_ID).get("mode") == "model"))

        self.assertEqual(self.link.run(DESKTOP_ID, "solid.extrude"), (app_link.SENT, "solidcraft"))
        self.assertTrue(wait_for(lambda: ("ui.start", {"command": "solid.extrude"}) in self.app.requests))
        self.assertTrue(wait_for(lambda: self.results))
        self.assertEqual(self.results[0][:3], ("solidcraft", "solid.extrude", True))

        before = self.app.camera["yaw"]
        self.link.run(DESKTOP_ID, "turnpad.orbit_left")
        self.assertTrue(wait_for(lambda: self.app.camera["yaw"] != before))
        self.assertAlmostEqual(self.app.camera["yaw"], before + math.radians(runner.ORBIT_STEP_DEGREES))

        # The driver never asks for what the catalog does not hold.
        self.assertEqual(self.link.run(DESKTOP_ID, "doc.open"), (app_link.UNKNOWN_COMMAND, "solidcraft"))

    def test_context_follows_the_tool(self):
        self.assertTrue(wait_for(self.linked))
        self.app.ui = {"tab": "SKETCH"}
        self.app.tool = "sketch.line"
        self.assertTrue(wait_for(lambda: self.link.context_for(DESKTOP_ID) ==
                                 {"mode": "sketch", "label": "Sketch", "detail": "Line"}))

    def test_solidcraft_quits_and_comes_back(self):
        self.assertTrue(wait_for(self.linked))
        self.app.stop()
        self.assertTrue(wait_for(lambda: not self.linked()))
        self.assertEqual(self.bridge.state, "waiting")
        self.app = FakeSolidCraft(self.environ["SOLIDCRAFT_CONTROL_DIR"], version="0.14.0")
        self.assertTrue(wait_for(lambda: self.linked() and self.link.commands(DESKTOP_ID)["version"] == "0.14.0",
                                 timeout=8))

    def test_the_plugin_is_the_switch(self):
        self.assertTrue(wait_for(self.linked))
        # Turned off in SolidCraft's Preferences › Plugins: the link ends.
        self.app.disabled_plugins = ["turnpad"]
        self.assertTrue(wait_for(lambda: not self.linked()))
        self.assertTrue(wait_for(lambda: solidcraft.OFF_IN_SOLIDCRAFT in self.bridge.detail))
        # On again: linked again.
        self.app.disabled_plugins = []
        self.assertTrue(wait_for(self.linked, timeout=8))
        # Removed from SolidCraft's plugins folder: the link ends and stays down.
        self.assertTrue(plugin.remove(self.data_root))
        self.assertTrue(wait_for(lambda: not self.linked()))
        self.assertTrue(wait_for(lambda: solidcraft.NOT_INSTALLED in self.bridge.detail))
        self.assertNotIn("ui.help", self.app.methods()[-3:], "without the plugin nothing is asked")

    def test_status_is_json(self):
        self.assertTrue(wait_for(self.linked))
        status = json.loads(json.dumps(self.host.status()))
        self.assertEqual(status["solidcraft"]["state"], "linked")
        self.assertEqual(status["solidcraft"]["port"], self.app.port)
        self.assertTrue(wait_for(lambda: self.host.status()["solidcraft"]["driver"]))


class FakeLink:
    """Stands in for the app link client: records what the bridge sends."""

    def __init__(self, path_provider, hello, catalog, on_run):
        self.hello, self.catalog, self.on_run = hello, catalog, on_run
        self.contexts = []
        self.ticks = 0
        self.closed = False
        self.connected = True

    def send_context(self, message):
        self.contexts.append(message)

    def tick(self):
        self.ticks += 1

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, name):
        self.version = "1.0"
        self.entries = [{"id": "app.do", "title": "Do", "category": "", "icon": "", "keys": "", "modes": []}]
        self.detail = name
        self.alive = True
        self.context = {"mode": "edit", "label": "Edit", "detail": ""}
        self.gone = False
        self.closed = False

    def run(self, command_id):
        return True, ""

    def poll(self):
        if self.gone:
            raise bridge_base.BridgeGone("gone")
        return dict(self.context)

    def close(self):
        self.closed = True


class BaseBridgeTest(unittest.TestCase):
    """integrations/bridge_base.py with a made-up application."""

    def make(self):
        now = [0.0]
        sessions = {}

        class Bridge(bridge_base.LinkedBridge):
            NAME = TITLE = LINK_APP = "demo"
            offered = ["stale", "good"]

            def candidates(self):
                return [(name, name) for name in self.offered]

            def connect(self, name):
                if name == "stale":
                    raise bridge_base.BridgeError("nobody listens")
                sessions[name] = FakeSession(name)
                return sessions[name]

        bridge = Bridge("/run/sock", clock=lambda: now[0], link_factory=FakeLink)
        return bridge, now, sessions

    def test_links_follows_and_lets_go(self):
        bridge, now, sessions = self.make()
        self.assertEqual(bridge.tick(), bridge.TICK_S)
        self.assertEqual(bridge.state, "linked")
        link = bridge._link
        self.assertEqual(link.hello["hello"]["app"], "demo")
        self.assertEqual(link.catalog, {"commands": sessions["good"].entries})
        self.assertEqual(link.contexts, [{"context": {"mode": "edit", "label": "Edit", "detail": ""}}])
        now[0] += bridge.CONTEXT_S
        bridge.tick()
        self.assertEqual(len(link.contexts), 1, "an unchanged context is not sent again")
        sessions["good"].gone = True
        now[0] += bridge.CONTEXT_S
        self.assertEqual(bridge.tick(), bridge.DISCOVER_S)
        self.assertTrue(link.closed and sessions["good"].closed)
        self.assertEqual((bridge.state, bridge.detail), ("waiting", "gone"))

    def test_a_candidate_that_led_nowhere_rests(self):
        bridge, now, _sessions = self.make()
        bridge.offered = ["stale"]
        self.assertEqual(bridge.tick(), bridge.DISCOVER_S)
        self.assertEqual(bridge.detail, "nobody listens")
        tried = []
        bridge.connect = lambda name: tried.append(name) or (_ for _ in ()).throw(bridge_base.BridgeError("x"))
        now[0] += bridge.DISCOVER_S
        bridge.tick()
        self.assertEqual(tried, [], "resting")
        now[0] += bridge.REFUSE_S
        bridge.tick()
        self.assertEqual(tried, ["stale"])

    def test_a_dead_session_after_a_command(self):
        bridge, now, sessions = self.make()
        bridge.tick()
        sessions["good"].alive = False
        now[0] += 0.01
        self.assertEqual(bridge.tick(), bridge.DISCOVER_S)
        self.assertFalse(bridge.linked)


class PluginTest(unittest.TestCase):
    """Turnpad as a SolidCraft plugin (integrations/solidcraft/plugin.py)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "solidcraft")

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_manifest_passes_solidcrafts_checks(self):
        # crates/ui-egui/src/plugins.rs: deny_unknown_fields, id = folder, lengths, characters.
        manifest = plugin.MANIFEST
        self.assertEqual(set(manifest), {"id", "name", "version", "description"})
        self.assertRegex(manifest["id"], r"\A[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\Z")
        self.assertTrue(0 < len(manifest["name"]) <= 80)
        self.assertRegex(manifest["version"], r"\A[A-Za-z0-9.+-]{1,32}\Z")
        self.assertLessEqual(len(manifest["description"]), 240)
        for value in manifest.values():
            self.assertFalse(any(ord(char) < 32 for char in value))

    def test_install_upgrade_remove(self):
        self.assertEqual(plugin.install_state(self.root), plugin.MISSING)
        folder = plugin.install(self.root)
        self.assertEqual(folder, os.path.join(self.root, "plugins", "turnpad"))
        self.assertEqual(plugin.install_state(self.root), plugin.INSTALLED)
        with open(os.path.join(folder, "plugin.json")) as handle:
            self.assertEqual(json.load(handle), plugin.MANIFEST)
        self.assertEqual(os.listdir(folder), ["plugin.json"], "no temporary file left")
        older = dict(plugin.MANIFEST, version="0.9.0")
        with open(os.path.join(folder, "plugin.json"), "w") as handle:
            json.dump(older, handle)
        self.assertEqual(plugin.install_state(self.root), plugin.OUTDATED)
        self.assertTrue(plugin.installed(self.root), "an older manifest still links")
        plugin.install(self.root)
        self.assertEqual(plugin.install_state(self.root), plugin.INSTALLED)
        self.assertTrue(plugin.remove(self.root))
        self.assertFalse(os.path.exists(folder))
        self.assertFalse(plugin.remove(self.root))

    def test_a_folder_that_is_not_ours_is_left_alone(self):
        folder = os.path.join(self.root, "plugins", "turnpad")
        os.makedirs(folder)
        with open(os.path.join(folder, "plugin.json"), "w") as handle:
            json.dump({"id": "turnpad", "name": "Someone else", "version": "1"}, handle)
        self.assertEqual(plugin.install_state(self.root), plugin.FOREIGN)
        with self.assertRaises(OSError):
            plugin.install(self.root)
        self.assertFalse(plugin.remove(self.root))
        self.assertTrue(os.path.exists(os.path.join(folder, "plugin.json")))

    def test_links_are_not_followed(self):
        elsewhere = os.path.join(self.tmp.name, "elsewhere")
        os.makedirs(elsewhere)
        os.makedirs(os.path.join(self.root, "plugins"))
        os.symlink(elsewhere, os.path.join(self.root, "plugins", "turnpad"))
        self.assertEqual(plugin.install_state(self.root), plugin.FOREIGN)
        with self.assertRaises(OSError):
            plugin.install(self.root)
        self.assertEqual(os.listdir(elsewhere), [])

    def test_which_data_folder_a_token_belongs_to(self):
        self.assertEqual(plugin.root_of_token("/h/.local/share/solidcraft/control/7878.token"),
                         "/h/.local/share/solidcraft")
        self.assertIsNone(plugin.root_of_token("/run/elsewhere/7878.token"))
        environ = {"HOME": self.tmp.name}
        native = plugin.data_roots(environ)[0]
        self.assertEqual(native, os.path.join(self.tmp.name, ".local", "share", "solidcraft"))
        token = os.path.join(native, "control", "7878.token")
        self.assertFalse(plugin.installed_for(token, environ))
        plugin.install(native)
        self.assertTrue(plugin.installed_for(token, environ))
        # A moved control folder: any data folder with the plugin.
        self.assertTrue(plugin.installed_for("/run/elsewhere/7878.token", environ))
        self.assertEqual([root.endswith("solidcraft") for root in plugin.data_roots(environ)], [True] * 3)

    def test_switch_in_solidcrafts_preferences(self):
        self.assertTrue(plugin.disabled_in({"disabled_plugins": ["other", "turnpad"]}))
        self.assertFalse(plugin.disabled_in({"disabled_plugins": ["other"]}))
        self.assertFalse(plugin.disabled_in({"theme": "dark"}), "a SolidCraft without a plugin page")
        self.assertFalse(plugin.disabled_in(None))


class HostTest(unittest.TestCase):
    def test_a_failing_bridge_rests_and_the_others_go_on(self):
        now = [100.0]

        class Broken:
            stopped = 0

            def tick(self):
                raise RuntimeError("bug")

            def stop(self):
                Broken.stopped += 1

            def status(self):
                return {"state": "broken"}

        class Fine:
            ticks = 0

            def tick(self):
                Fine.ticks += 1
                return 0.5

            def stop(self):
                pass

            def status(self):
                return {"state": "fine"}

        host = bridges.BridgeHost("/nowhere", modules=(), clock=lambda: now[0])
        host._bridges = [("broken", Broken()), ("fine", Fine())]
        with self.assertLogs("asus-turnpad", "ERROR"):
            self.assertEqual(host.tick_all(), 0.5)
        self.assertEqual((Broken.stopped, Fine.ticks), (1, 1))
        host.tick_all()
        self.assertEqual(Broken.stopped, 1, "resting")
        now[0] += bridges.FAILURE_REST_S + 1
        with self.assertLogs("asus-turnpad", "ERROR"):
            host.tick_all()
        self.assertEqual(Broken.stopped, 2)
        self.assertEqual(host.status(), {"broken": {"state": "broken"}, "fine": {"state": "fine"}})

    def test_bridges_listed_import(self):
        for name in bridges.BRIDGES:
            with self.subTest(module=name):
                module = __import__(name, fromlist=["create"])
                self.assertTrue(callable(module.create))
                self.assertTrue(module.NAME)

    def test_no_socket_no_thread(self):
        self.assertFalse(bridges.BridgeHost(None).start())


if __name__ == "__main__":
    unittest.main()
