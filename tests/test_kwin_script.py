"""The KWin script that reports the focused application on Plasma 6, with a
fake session bus (nothing is loaded into a real KWin)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from config.identity import DBUS_INTERFACE, DBUS_NAME, DBUS_PATH  # noqa: E402
from desktop import kwin_script  # noqa: E402
import test_driver_state  # noqa: E402


class FakeBus:
    def __init__(self, load_reply=(7,), fail=None):
        self.calls = []
        self.load_reply = load_reply
        self.fail = fail

    def call(self, service, path, interface, method, args):
        self.calls.append((service, path, interface, method, tuple(args)))
        if self.fail == method:
            raise OSError("no such method")
        return self.load_reply if method == "loadScript" else ()


class ScriptTextTest(unittest.TestCase):
    def test_script_calls_the_drivers_method_on_activation_and_once_at_load(self):
        text = kwin_script.script_text()
        self.assertIn('callDBus("%s", "%s", "%s", "SetActiveWindow", String(id), String(caption))' % (DBUS_NAME, DBUS_PATH, DBUS_INTERFACE), text)
        self.assertIn("workspace.windowActivated", text)
        self.assertIn("window.desktopFileName || window.resourceClass", text)
        self.assertIn("report(workspace.activeWindow", text)
        self.assertEqual(text.count("%"), 0)  # fully filled in

    def test_script_sends_the_caption_and_follows_it_on_the_active_window_only(self):
        text = kwin_script.script_text()
        self.assertIn("window.caption", text)
        # A change of the caption is reported for the window in focus ...
        self.assertIn("current.captionChanged.connect(captionChanged)", text)
        # ... and the previous window is let go before the next is followed.
        self.assertLess(text.index("current.captionChanged.disconnect(captionChanged)"),
                        text.index("current = window || null"))
        self.assertIn("function captionChanged() {\n    send(current);", text)

    def test_kde_is_read_from_the_desktop_list(self):
        self.assertTrue(kwin_script.is_kde({"XDG_CURRENT_DESKTOP": "KDE"}))
        self.assertTrue(kwin_script.is_kde({"XDG_CURRENT_DESKTOP": "ubuntu:KDE"}))
        self.assertFalse(kwin_script.is_kde({"XDG_CURRENT_DESKTOP": "GNOME"}))
        self.assertFalse(kwin_script.is_kde({}))

    def test_script_lives_in_the_runtime_dir(self):
        self.assertEqual(
            kwin_script.script_path({"XDG_RUNTIME_DIR": "/run/user/1000"}),
            Path("/run/user/1000/asus-turnpad/kwin-active-window.js"),
        )


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "asus-turnpad" / "kwin-active-window.js"

    def test_unload_old_then_load_then_start(self):
        bus = FakeBus()
        script = kwin_script.KWinScript(bus, self.path)
        self.assertEqual(script.load(), 7)
        self.assertEqual(
            [(call[3], call[4]) for call in bus.calls],
            [
                ("unloadScript", (kwin_script.PLUGIN_NAME,)),
                ("loadScript", (str(self.path), kwin_script.PLUGIN_NAME)),
                ("start", ()),
            ],
        )
        self.assertTrue(all(call[:3] == ("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting") for call in bus.calls))
        self.assertEqual(self.path.read_text(), kwin_script.script_text())

    def test_unload_removes_the_script_and_asks_kwin_once(self):
        bus = FakeBus()
        script = kwin_script.KWinScript(bus, self.path)
        script.load()
        bus.calls.clear()
        script.unload()
        script.unload()
        self.assertEqual([(call[3], call[4]) for call in bus.calls], [("unloadScript", (kwin_script.PLUGIN_NAME,))])
        self.assertFalse(self.path.exists())

    def test_a_refused_load_is_an_error(self):
        script = kwin_script.KWinScript(FakeBus(load_reply=(-1,)), self.path)
        with self.assertRaises(RuntimeError):
            script.load()
        with self.assertRaises(OSError):
            kwin_script.KWinScript(FakeBus(fail="loadScript"), self.path).load()

    def test_unload_failure_still_removes_the_file(self):
        bus = FakeBus()
        script = kwin_script.KWinScript(bus, self.path)
        script.load()
        bus.fail = "unloadScript"
        with self.assertRaises(OSError):
            script.unload()
        self.assertFalse(self.path.exists())


class SafetyTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "asus-turnpad" / "kwin-active-window.js"

    def test_start_failing_after_a_successful_load_unloads_the_script(self):
        bus = FakeBus(fail="start")
        script = kwin_script.KWinScript(bus, self.path)
        with self.assertRaises(OSError):
            script.load()
        self.assertEqual([call[3] for call in bus.calls], ["unloadScript", "loadScript", "start", "unloadScript"])
        self.assertFalse(self.path.exists())
        self.assertIsNone(script.script_id)

    def test_a_refused_load_removes_the_file(self):
        script = kwin_script.KWinScript(FakeBus(load_reply=(-1,)), self.path)
        with self.assertRaises(RuntimeError):
            script.load()
        self.assertFalse(self.path.exists())

    def test_no_runtime_dir_means_no_script(self):
        self.assertIsNone(kwin_script.script_path({}))
        with self.assertRaises(ValueError):
            kwin_script.KWinScript(FakeBus(), environ={})

    def test_the_directory_is_private_to_the_user(self):
        script = kwin_script.KWinScript(FakeBus(), self.path)
        script.load()
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        os.chmod(self.path.parent, 0o770)  # someone else could write the script now
        with self.assertRaises(PermissionError):
            kwin_script.KWinScript(FakeBus(), self.path).load()

    def test_a_symlinked_directory_is_refused(self):
        target = Path(self.directory.name) / "elsewhere"
        target.mkdir(mode=0o700)
        link = Path(self.directory.name) / "link"
        link.symlink_to(target)
        with self.assertRaises(PermissionError):
            kwin_script.KWinScript(FakeBus(), link / "kwin-active-window.js").load()


class DriverHookTest(unittest.TestCase):
    """start_kwin_script() and stop_kwin_script() of dialpad.py."""

    def hooks(self, session="wayland", desktop="KDE", runtime="/run/user/1", on_load=None):
        import ast
        with open(os.path.join(ROOT, "dialpad.py"), encoding="utf-8") as source:
            tree = ast.parse(source.read())
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("start_kwin_script", "stop_kwin_script")]
        events = []
        driver_ref = {}

        class Script:
            def __init__(self, _bus):
                pass

            def load(self):
                events.append("load")
                if on_load:
                    on_load(driver_ref["ns"])

            def unload(self):
                events.append("unload")

        fake_module = type("M", (), {
            "is_kde": staticmethod(lambda: desktop == "KDE"),
            "script_path": staticmethod(lambda: Path(runtime) if runtime else None),
            "KWinScript": Script, "GioBus": staticmethod(lambda: None),
        })
        noop = staticmethod(lambda *a, **k: None)
        namespace = {
            "kwin_script": fake_module, "xdg_session_type": session, "kde_script": None, "kde_script_closed": False,
            "log": type("Log", (), {"warning": noop, "info": noop, "debug": noop})(),
        }
        driver_ref["ns"] = namespace
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "dialpad.py", "exec"), namespace)
        return namespace, events

    def test_loaded_on_kde_wayland_only(self):
        for kwargs in ({}, {"session": "x11"}, {"desktop": "GNOME"}, {"runtime": ""}):
            with self.subTest(kwargs=kwargs):
                namespace, events = self.hooks(**kwargs)
                namespace["start_kwin_script"]()
                self.assertEqual(events, ["load"] if not kwargs else [])

    def test_stop_unloads(self):
        namespace, events = self.hooks()
        namespace["start_kwin_script"]()
        namespace["stop_kwin_script"]()
        self.assertEqual(events, ["load", "unload"])

    def test_a_driver_stopped_while_the_script_loads_unloads_it(self):
        namespace, events = self.hooks(on_load=lambda ns: ns["stop_kwin_script"]())
        namespace["start_kwin_script"]()
        self.assertEqual(events, ["load", "unload"])
        self.assertIsNone(namespace["kde_script"])

    def test_a_driver_already_stopped_does_not_load(self):
        namespace, events = self.hooks()
        namespace["stop_kwin_script"]()
        namespace["start_kwin_script"]()
        self.assertEqual(events, [])


class BlenderIdTest(unittest.TestCase):
    """SetActiveApplication gets the window's desktop file name as KWin knows it."""

    def test_both_forms_of_the_blender_id_select_the_blender_profile(self):
        from config.legacy_adapter import shortcuts_from_config
        from config.schema import DialPadConfig, Profile

        config = DialPadConfig(profiles=[
            Profile(name="Default", segments=[test_driver_state.volume(), test_driver_state.vertical_scroll()]),
            Profile(name="Blender", app_id="org.blender.Blender.desktop",
                    segments=[test_driver_state.zoom_wheel(), test_driver_state.volume()]),
        ])
        driver, _sent, _queued = test_driver_state.load_driver_state(shortcuts_from_config(config))
        for window_id in ("blender", "org.blender.blender", "org.blender.Blender", "org.blender.Blender.desktop"):
            with self.subTest(window_id=window_id):
                driver["app_name"] = None
                driver["window_was_changed"](window_id.lower())
                self.assertEqual(driver["app_name"], "org.blender.Blender.desktop")
        driver["window_was_changed"]("firefox")
        self.assertEqual(driver["app_name"], "none")


if __name__ == "__main__":
    unittest.main()
