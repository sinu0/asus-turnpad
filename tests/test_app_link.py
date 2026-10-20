"""The app link server (app_link.py) against a fake client on a real Unix
socket in a private temporary directory (never the user's runtime directory)."""

import json
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app_link  # noqa: E402
from app_link import AppLink, LineBuffer, LineTooLong, app_matches  # noqa: E402


def wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


class FakeClient:
    """What the Blender add-on does on the wire, by hand."""

    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(3.0)
        self.sock.connect(path)
        self.buffer = LineBuffer()
        self.received = []

    def send(self, message):
        self.sock.sendall(json.dumps(message).encode() + b"\n")

    def send_raw(self, data):
        self.sock.sendall(data)

    def hello(self, app="blender", name="Blender", version="5.2.0", pid=4242, protocol=1):
        self.send({"hello": {"app": app, "name": name, "version": version, "pid": pid, "protocol": protocol}})

    def read(self, count=1, timeout=3.0):
        """Wait until ``count`` more messages arrived; return all received so far."""
        deadline = time.monotonic() + timeout
        start = len(self.received)
        self.sock.settimeout(0.05)
        while len(self.received) - start < count and time.monotonic() < deadline:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                break
            self.received.extend(self.buffer.feed(chunk))
        return self.received

    def closed(self, timeout=3.0):
        """True when the server ended the connection."""
        deadline = time.monotonic() + timeout
        self.sock.settimeout(0.05)
        while time.monotonic() < deadline:
            try:
                if not self.sock.recv(65536):
                    return True
            except socket.timeout:
                continue
            except OSError:
                return True
        return False

    def close(self):
        self.sock.close()


def entry(identifier, title=None, **extra):
    return {"id": identifier, "title": title or identifier, "category": "Test", "icon": "", "keys": "", "modes": [], **extra}


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "asus-turnpad-apps.sock")
        self.changes = []
        self.results = []
        self.link = AppLink(
            self.path, on_change=self.changes.append,
            on_result=lambda *args: self.results.append(args),
        )
        self.assertTrue(self.link.start())
        self.addCleanup(self.link.stop)
        self.clients = []

    def client(self, hello=True, **kwargs):
        client = FakeClient(self.path)
        self.clients.append(client)
        self.addCleanup(client.close)
        if hello:
            client.hello(**kwargs)
            self.assertTrue(wait_for(lambda: any(info["app"] == kwargs.get("app", "blender") for info in self.link.clients())))
        return client


class SocketTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "asus-turnpad-apps.sock")

    def test_the_socket_is_for_the_user_alone(self):
        link = AppLink(self.path)
        self.assertTrue(link.start())
        self.addCleanup(link.stop)
        info = os.stat(self.path)
        self.assertTrue(stat.S_ISSOCK(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)

    def test_a_directory_others_can_enter_is_refused(self):
        os.chmod(self.directory.name, 0o755)
        link = AppLink(self.path)
        with self.assertLogs("asus-turnpad", level="WARNING"):
            self.assertFalse(link.start())
        self.assertFalse(os.path.exists(self.path))

    def test_a_symlinked_directory_is_refused(self):
        real = os.path.join(self.directory.name, "real")
        os.mkdir(real, 0o700)
        link_dir = os.path.join(self.directory.name, "link")
        os.symlink(real, link_dir)
        link = AppLink(os.path.join(link_dir, "asus-turnpad-apps.sock"))
        with self.assertLogs("asus-turnpad", level="WARNING"):
            self.assertFalse(link.start())

    def test_no_runtime_directory_means_no_link(self):
        link = AppLink(None)
        link.path = None
        self.assertFalse(link.start())

    def test_a_stale_socket_file_is_replaced(self):
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(self.path)
        stale.close()  # nobody listens: connect() is refused
        link = AppLink(self.path)
        self.assertTrue(link.start())
        self.addCleanup(link.stop)
        client = FakeClient(self.path)
        client.close()

    def test_a_live_socket_of_another_driver_is_left_alone(self):
        first = AppLink(self.path)
        self.assertTrue(first.start())
        self.addCleanup(first.stop)
        second = AppLink(self.path)
        with self.assertLogs("asus-turnpad", level="WARNING"):
            self.assertFalse(second.start())
        # The first one still serves.
        client = FakeClient(self.path)
        client.hello()
        self.assertTrue(wait_for(lambda: bool(first.clients())))
        client.close()

    def test_something_that_is_not_a_socket_is_not_removed(self):
        Path(self.path).write_text("precious")
        link = AppLink(self.path)
        with self.assertLogs("asus-turnpad", level="WARNING"):
            self.assertFalse(link.start())
        self.assertEqual(Path(self.path).read_text(), "precious")

    def test_stop_removes_the_socket(self):
        link = AppLink(self.path)
        link.start()
        link.stop()
        self.assertFalse(os.path.exists(self.path))


class HelloAndCatalogTest(ServerCase):
    def test_hello_is_answered_with_the_protocol(self):
        client = self.client()
        self.assertEqual(client.read(1), [{"hello": {"protocol": app_link.PROTOCOL}}])
        self.assertEqual(self.link.clients()[0]["name"], "Blender")
        self.assertIn("hello", self.changes)

    def test_nothing_counts_before_hello(self):
        client = self.client(hello=False)
        client.send({"commands": [entry("view.frame_selected")]})
        client.send({"context": {"mode": "OBJECT"}})
        client.hello()
        self.assertTrue(wait_for(lambda: bool(self.link.clients())))
        self.assertEqual(self.link.commands("blender")["commands"], [])
        self.assertEqual(self.link.context_for("blender"), {})

    def test_a_bad_hello_is_ignored(self):
        client = self.client(hello=False)
        for bad in ({"app": ""}, {"app": "Bad Name!"}, {"app": 5}, "text", None):
            client.send({"hello": bad})
        client.send({"commands": [entry("a")]})
        time.sleep(0.1)
        self.assertEqual(self.link.clients(), [])

    def test_the_catalog_is_cleaned(self):
        client = self.client()
        client.send({"commands": [
            entry("view.frame_selected", "Frame selected", keys="NUMPAD_PERIOD", modes=["OBJECT", "EDIT_MESH"]),
            entry("bad id with spaces"), entry(""), {"title": "no id"}, "text", entry("x" * 129),
            entry("view.frame_selected", "Frame selected again"),
            entry("mesh.extrude", "T" * 500),
        ]})
        self.assertTrue(wait_for(lambda: len(self.link.commands("blender")["commands"]) == 2))
        commands = {c["id"]: c for c in self.link.commands("blender")["commands"]}
        self.assertEqual(commands["view.frame_selected"]["title"], "Frame selected again")  # the later entry wins
        self.assertEqual(len(commands["mesh.extrude"]["title"]), 100)
        self.assertIn("catalog", self.changes)

    def test_a_catalog_beyond_the_limit_is_ignored_and_the_old_one_stays(self):
        client = self.client()
        client.send({"commands": [entry("a.one")]})
        self.assertTrue(wait_for(lambda: len(self.link.commands("blender")["commands"]) == 1))
        with self.assertLogs("asus-turnpad", level="WARNING"):
            client.send({"commands": [entry("c.%d" % number) for number in range(app_link.MAX_COMMANDS + 1)]})
            client.send({"context": {"mode": "OBJECT"}})  # processed after the big catalog
            self.assertTrue(wait_for(lambda: self.link.context_for("blender") != {}))
        self.assertEqual([c["id"] for c in self.link.commands("blender")["commands"]], ["a.one"])

    def test_the_limit_itself_is_accepted(self):
        client = self.client()
        client.send({"commands": [entry("c.%d" % number) for number in range(app_link.MAX_COMMANDS)]})
        self.assertTrue(wait_for(lambda: len(self.link.commands("blender")["commands"]) == app_link.MAX_COMMANDS))

    def test_context_is_kept_and_reported_on_change_only(self):
        client = self.client()
        client.send({"context": {"mode": "EDIT_MESH", "label": "Edit Mesh", "detail": "Vertex"}})
        self.assertTrue(wait_for(lambda: self.link.context_for("org.blender.Blender.desktop") != {}))
        self.assertEqual(self.link.context_for("blender"), {"mode": "EDIT_MESH", "label": "Edit Mesh", "detail": "Vertex"})
        client.send({"context": {"mode": "EDIT_MESH", "label": "Edit Mesh", "detail": "Vertex"}})
        client.send({"context": {"mode": "OBJECT", "label": "Object Mode", "detail": ""}})
        self.assertTrue(wait_for(lambda: self.link.context_for("blender").get("mode") == "OBJECT"))
        self.assertEqual(self.changes.count("context"), 2)

    def test_a_context_without_a_mode_is_no_context(self):
        client = self.client()
        client.send({"context": {"label": "x"}})
        client.send({"context": "x"})
        client.send({"context": {"mode": "OBJECT"}})
        self.assertTrue(wait_for(lambda: self.link.context_for("blender") != {}))
        self.assertEqual(self.link.context_for("blender"), {"mode": "OBJECT", "label": "", "detail": ""})

    def test_garbage_lines_are_skipped(self):
        client = self.client()
        client.send_raw(b"not json\n\n[1,2]\n\xff\xfe\n")
        client.send({"commands": [entry("a.one")]})
        self.assertTrue(wait_for(lambda: len(self.link.commands("blender")["commands"]) == 1))
        self.assertEqual(len(self.link.clients()), 1)


class LimitsTest(ServerCase):
    def test_a_line_beyond_the_limit_ends_the_connection(self):
        client = self.client()
        with self.assertLogs("asus-turnpad", level="WARNING"):
            client.send_raw(b'{"context": {"mode": "' + b"A" * app_link.MAX_LINE + b'"}}\n')
            self.assertTrue(client.closed())
        self.assertTrue(wait_for(lambda: self.link.clients() == []))

    def test_an_endless_line_ends_the_connection(self):
        client = self.client(hello=False)
        with self.assertLogs("asus-turnpad", level="WARNING"):
            client.send_raw(b"x" * (app_link.MAX_LINE + 10))
            self.assertTrue(client.closed())

    def test_a_connection_without_hello_is_dropped_after_the_timeout(self):
        now = [0.0]
        link = AppLink(os.path.join(self.directory.name, "other.sock"), clock=lambda: now[0])
        self.assertTrue(link.start())
        self.addCleanup(link.stop)
        client = FakeClient(link.path)
        self.addCleanup(client.close)
        time.sleep(0.1)
        now[0] = app_link.HELLO_TIMEOUT_S + 1
        self.assertTrue(client.closed())

    def test_too_many_connections_are_refused(self):
        sockets = []
        for _ in range(app_link.MAX_CLIENTS + 3):
            sockets.append(FakeClient(self.path))
            time.sleep(0.005)
        self.addCleanup(lambda: [item.close() for item in sockets])
        self.assertTrue(all(item.closed(1.0) for item in sockets[-3:]))


class RunTest(ServerCase):
    def setUp(self):
        super().setUp()
        self.blender = self.client()
        self.blender.send({"commands": [entry("view.frame_selected"), entry("mesh.extrude_region_move")]})
        self.assertTrue(wait_for(lambda: len(self.link.commands("blender")["commands"]) == 2))
        self.blender.read(1)  # the hello answer

    def test_a_known_command_is_sent_with_a_sequence_number(self):
        self.assertEqual(self.link.run("org.blender.Blender.desktop", "view.frame_selected"), (app_link.SENT, "blender"))
        messages = self.blender.read(1)
        run = messages[-1]["run"]
        self.assertEqual(run["id"], "view.frame_selected")
        self.assertIsInstance(run["seq"], int)

    def test_an_unknown_command_is_refused_and_nothing_is_sent(self):
        self.assertEqual(self.link.run("blender", "wm.quit_blender"), (app_link.UNKNOWN_COMMAND, "blender"))
        self.assertEqual(self.link.run("blender", ""), (app_link.UNKNOWN_COMMAND, "blender"))
        self.assertEqual(self.link.run("blender", None), (app_link.UNKNOWN_COMMAND, "blender"))
        self.blender.read(1, timeout=0.3)
        self.assertEqual(len(self.blender.received), 1)  # only the hello answer

    def test_a_command_of_another_applications_catalog_is_unknown(self):
        freecad = self.client(app="freecad", name="FreeCAD")
        freecad.send({"commands": [entry("Std_ViewFitAll")]})
        self.assertTrue(wait_for(lambda: self.link.commands("freecad")["commands"]))
        self.assertEqual(self.link.run("blender", "Std_ViewFitAll")[0], app_link.UNKNOWN_COMMAND)
        self.assertEqual(self.link.run("org.freecad.FreeCAD.desktop", "Std_ViewFitAll")[0], app_link.SENT)
        self.assertEqual(self.link.run("org.freecad.FreeCAD.desktop", "view.frame_selected")[0], app_link.UNKNOWN_COMMAND)

    def test_nobody_linked_for_the_focused_window(self):
        self.assertEqual(self.link.run("firefox", "view.frame_selected"), (app_link.NO_CLIENT, ""))
        self.assertEqual(self.link.run(None, "view.frame_selected"), (app_link.NO_CLIENT, ""))
        self.assertEqual(self.link.run("", "view.frame_selected"), (app_link.NO_CLIENT, ""))

    def test_the_most_recently_active_client_gets_the_request(self):
        second = self.client(name="Blender 2", pid=99)
        second.send({"commands": [entry("view.frame_selected")]})
        self.assertTrue(wait_for(lambda: self.link.commands("blender")["name"] == "Blender 2"))
        self.assertEqual(self.link.run("blender", "view.frame_selected")[0], app_link.SENT)
        second.read(2, timeout=3)
        self.assertEqual(second.received[-1]["run"]["id"], "view.frame_selected")
        # The first one speaks again (a mode change): it is the active one now.
        self.blender.send({"context": {"mode": "SCULPT"}})
        self.assertTrue(wait_for(lambda: self.link.context_for("blender").get("mode") == "SCULPT"))
        self.assertEqual(self.link.commands("blender")["version"], "5.2.0")
        count = len(self.blender.received)
        self.link.run("blender", "view.frame_selected")
        self.blender.read(1)
        self.assertGreater(len(self.blender.received), count)

    def test_results_are_matched_to_their_requests(self):
        self.link.run("blender", "mesh.extrude_region_move")
        seq = self.blender.read(1)[-1]["run"]["seq"]
        self.blender.send({"result": {"seq": seq, "ok": False, "error": "poll failed"}})
        self.assertTrue(wait_for(lambda: bool(self.results)))
        self.assertEqual(self.results, [("blender", "mesh.extrude_region_move", False, "poll failed")])
        # A result for a request nobody made, or twice, is ignored.
        self.blender.send({"result": {"seq": seq, "ok": True, "error": ""}})
        self.blender.send({"result": {"seq": 987654, "ok": True, "error": ""}})
        self.blender.send({"result": {"seq": "x", "ok": True}})
        self.blender.send({"context": {"mode": "OBJECT"}})
        self.assertTrue(wait_for(lambda: self.link.context_for("blender") != {}))
        self.assertEqual(len(self.results), 1)

    def test_a_success_is_reported_too(self):
        self.link.run("blender", "view.frame_selected")
        seq = self.blender.read(1)[-1]["run"]["seq"]
        self.blender.send({"result": {"seq": seq, "ok": True, "error": ""}})
        self.assertTrue(wait_for(lambda: bool(self.results)))
        self.assertEqual(self.results[0][2:], (True, ""))

    def test_disconnecting_removes_the_client_but_remembers_the_catalog(self):
        self.blender.close()
        self.assertTrue(wait_for(lambda: self.link.clients() == []))
        self.assertIn("gone", self.changes)
        self.assertEqual(self.link.run("blender", "view.frame_selected"), (app_link.NO_CLIENT, ""))
        remembered = self.link.commands("blender")
        self.assertFalse(remembered["connected"])
        self.assertEqual(len(remembered["commands"]), 2)
        self.assertEqual(self.link.context_for("blender"), {})
        self.assertFalse(self.link.connected("blender"))

    def test_commands_json_for_a_desktop_id_and_for_unknown_apps(self):
        by_id = self.link.commands("org.blender.Blender.desktop")
        self.assertEqual((by_id["app"], by_id["connected"], by_id["name"]), ("blender", True, "Blender"))
        self.assertEqual(
            self.link.commands("krita"),
            {"app": "krita", "name": "", "version": "", "connected": False, "context": {}, "commands": []},
        )
        json.dumps(by_id)

    def test_a_flood_of_requests_never_blocks_the_caller(self):
        # The client does not read: requests pile up, then are refused as busy.
        flood = self.client(app="kicad", name="KiCad")
        flood.send({"commands": [entry("a.one")]})
        self.assertTrue(wait_for(lambda: self.link.commands("kicad")["commands"]))
        started = time.monotonic()
        outcomes = {self.link.run("kicad", "a.one")[0] for _ in range(20000)}
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertIn(app_link.SENT, outcomes)
        self.assertTrue(outcomes <= {app_link.SENT, app_link.BUSY})

    def test_requests_from_several_threads(self):
        errors = []

        def worker():
            try:
                for _ in range(50):
                    self.link.run("blender", "view.frame_selected")
            except Exception as error:  # pragma: no cover
                errors.append(error)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.blender.read(200, timeout=5)
        seqs = [m["run"]["seq"] for m in self.blender.received if "run" in m]
        self.assertEqual(len(seqs), len(set(seqs)))


class MatchingTest(unittest.TestCase):
    def test_application_names_and_ids(self):
        for window in ("blender", "Blender", "org.blender.Blender", "org.blender.Blender.desktop",
                       "/usr/bin/blender", "/opt/blender-4.2/blender", "blender.desktop"):
            with self.subTest(window=window):
                self.assertTrue(app_matches("blender", window))
        for window in ("firefox", "org.blenderfoundation.Other", "blenderbot", "", None, "org.kde.krita.desktop"):
            with self.subTest(window=window):
                self.assertFalse(app_matches("blender", window))
        self.assertTrue(app_matches("freecad", "org.freecad.FreeCAD.desktop"))
        self.assertTrue(app_matches("kicad", "org.kicad.kicad.desktop"))
        self.assertFalse(app_matches("", "blender"))


class LineBufferTest(unittest.TestCase):
    def test_messages_split_across_chunks(self):
        buffer = LineBuffer()
        self.assertEqual(buffer.feed(b'{"a": 1}\n{"b"'), [{"a": 1}])
        self.assertEqual(buffer.feed(b': 2}\n'), [{"b": 2}])

    def test_the_limit_is_in_bytes_with_the_newline(self):
        buffer = LineBuffer()
        padding = app_link.MAX_LINE - len(b'{"a":""}\n')
        self.assertEqual(len(buffer.feed(b'{"a":"' + b"x" * padding + b'"}\n')), 1)
        with self.assertRaises(LineTooLong):
            LineBuffer().feed(b'{"a":"' + b"x" * (padding + 1) + b'"}\n')

    def test_many_short_lines_in_one_chunk_are_cut_in_linear_time(self):
        buffer = LineBuffer()
        count = 200000
        started = time.monotonic()
        got = buffer.feed(b'{"a":1}\n' * count)
        self.assertEqual(len(got), count)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(buffer.feed(b""), [])

    def test_bytes_arriving_one_by_one_and_a_tail_after_complete_lines(self):
        buffer = LineBuffer()
        got = []
        for byte in b'{"a":1}\n{"b":2}\n{"c":':
            got += buffer.feed(bytes([byte]))
        self.assertEqual(got, [{"a": 1}, {"b": 2}])
        self.assertEqual(buffer.feed(b'3}\n'), [{"c": 3}])
        self.assertEqual(buffer.feed(b'{"d":4}\n{"e"'), [{"d": 4}])
        self.assertEqual(buffer.feed(b':5}\n'), [{"e": 5}])

    def test_an_unterminated_tail_after_lines_still_hits_the_limit(self):
        with self.assertRaises(LineTooLong):
            LineBuffer().feed(b'{"a":1}\n' + b"x" * app_link.MAX_LINE)


if __name__ == "__main__":
    unittest.main()
