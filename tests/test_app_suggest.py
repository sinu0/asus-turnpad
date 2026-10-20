"""The one-time offer of a ready profile: which applications get it (pure),
the notification with a button (fake bus) and the driver's hooks."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app_suggest  # noqa: E402
import launch  # noqa: E402
from app_suggest import Suggester  # noqa: E402
from config.presets import pack_for_app  # noqa: E402
from config.schema import Profile  # noqa: E402
from tests.test_driver_control import DriverCase  # noqa: E402

try:
    from gi.repository import GLib
    from desktop.notifications import ActionNotifier
except (ImportError, ValueError):
    ActionNotifier = None


class SuggesterTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config" / "suggested.json"

    def test_an_application_with_a_pack_and_no_profile_is_offered_once(self):
        suggester = Suggester(self.path)
        first = suggester.consider("org.blender.Blender.desktop", has_profile=False)
        self.assertEqual((first.pack, first.app_id), ("Blender", "org.blender.Blender.desktop"))  # own case
        self.assertEqual(first.window_id, "org.blender.blender.desktop")
        self.assertIsNone(suggester.consider("org.blender.Blender.desktop", False))
        self.assertIsNone(suggester.consider("ORG.BLENDER.BLENDER.DESKTOP", False))
        # Another application still gets its own offer.
        self.assertEqual(suggester.consider("org.freecad.FreeCAD.desktop", False).pack, "FreeCAD")

    def test_an_id_that_looks_like_an_option_is_never_offered(self):
        suggester = Suggester(self.path)
        for window in ("--help.desktop", "-org.blender.Blender.desktop", " --x.desktop"):
            self.assertIsNone(suggester.consider(window, False))
        self.assertFalse(suggester.offered("--help.desktop"))

    def test_nothing_for_applications_without_a_pack_or_with_a_profile(self):
        suggester = Suggester(self.path)
        self.assertIsNone(suggester.consider("org.gnome.Calculator.desktop", False))
        self.assertIsNone(suggester.consider("", False))
        self.assertIsNone(suggester.consider(None, False))
        self.assertIsNone(suggester.consider("org.blender.Blender.desktop", True))
        # A profile now does not use up the offer for later.
        self.assertIsNotNone(suggester.consider("org.blender.Blender.desktop", False))

    def test_a_binary_path_gets_the_packs_desktop_id(self):
        suggestion = Suggester(self.path).consider("/usr/bin/blender", False)
        self.assertEqual((suggestion.pack, suggestion.app_id), ("Blender", pack_for_app("blender").app_id))
        self.assertEqual(suggestion.window_id, "/usr/bin/blender")

    def test_what_was_offered_is_remembered_across_runs(self):
        Suggester(self.path).consider("org.blender.Blender.desktop", False)
        self.assertEqual(json.loads(self.path.read_text()), {"offered": ["org.blender.blender.desktop"]})
        again = Suggester(self.path)
        self.assertTrue(again.offered("org.blender.Blender.desktop"))
        self.assertIsNone(again.consider("org.blender.Blender.desktop", False))

    def test_an_unusable_file_means_nothing_was_offered(self):
        self.path.parent.mkdir(parents=True)
        for text in ("not json", "[]", '{"offered": "x"}', '{"offered": [1, null, ""]}'):
            self.path.write_text(text)
            self.assertEqual(app_suggest.load(self.path), set(), text)

    def test_the_offer_is_written_before_it_is_returned(self):
        suggester = Suggester(self.path)
        seen = []
        original = app_suggest.save

        def spy(path, offered):
            seen.append(set(offered))
            return original(path, offered)

        app_suggest.save = spy
        try:
            suggester.consider("org.blender.Blender.desktop", False)
        finally:
            app_suggest.save = original
        self.assertEqual(seen, [{"org.blender.blender.desktop"}])

    def test_an_unwritable_place_does_not_raise(self):
        blocked = Path(self.directory.name) / "file"
        blocked.write_text("x")
        with self.assertLogs("asus-turnpad", level="WARNING"):
            suggestion = Suggester(blocked / "suggested.json").consider("org.blender.Blender.desktop", False)
        self.assertIsNotNone(suggestion)  # offered now, perhaps again after a restart

    def test_the_file_is_not_left_half_written(self):
        Suggester(self.path).consider("org.blender.Blender.desktop", False)
        self.assertEqual([item.name for item in self.path.parent.iterdir()], ["suggested.json"])


class FakeConnection:
    """The few Gio.DBusConnection methods ActionNotifier uses."""

    def __init__(self, fail=False):
        self.calls = []
        self.handlers = {}
        self.fail = fail
        self.next_id = 41

    def signal_subscribe(self, sender, interface, signal, path, arg0, flags, callback):
        token = len(self.handlers) + 1
        self.handlers[token] = (signal, callback)
        return token

    def signal_unsubscribe(self, token):
        self.handlers.pop(token, None)

    def call_sync(self, service, path, interface, method, parameters, reply_type, flags, timeout, cancellable):
        if self.fail:
            raise OSError("no notification service")
        self.calls.append((service, path, interface, method, parameters.unpack()))
        self.next_id += 1
        return GLib.Variant("(u)", (self.next_id,))

    def emit(self, signal, *values):
        variant = GLib.Variant("(us)" if signal == "ActionInvoked" else "(uu)", values)
        for name, callback in list(self.handlers.values()):
            if name == signal:
                callback(self, "", "", "", signal, variant)


@unittest.skipIf(ActionNotifier is None, "PyGObject is not installed")
class NotificationTest(unittest.TestCase):
    def notify(self, connection, **kwargs):
        actions = []
        notifier = ActionNotifier(lambda: connection, app_name="Turnpad")
        number = notifier.notify("Ready profile", "Body", [("create", "Create profile")], actions.append, **kwargs)
        return number, actions

    def test_the_notification_has_the_button(self):
        connection = FakeConnection()
        number, _actions = self.notify(connection, icon="icon-name", timeout_ms=15000)
        self.assertEqual(number, 42)
        service, path, interface, method, args = connection.calls[0]
        self.assertEqual((service, path, interface, method), (
            "org.freedesktop.Notifications", "/org/freedesktop/Notifications", "org.freedesktop.Notifications", "Notify"))
        self.assertEqual(args, ("Turnpad", 0, "icon-name", "Ready profile", "Body", ["create", "Create profile"], {}, 15000))

    def test_pressing_the_button_calls_back_once_and_lets_go_of_the_signals(self):
        connection = FakeConnection()
        _number, actions = self.notify(connection)
        connection.emit("ActionInvoked", 7, "create")  # another notification's button
        self.assertEqual(actions, [])
        connection.emit("ActionInvoked", 42, "create")
        self.assertEqual(actions, ["create"])
        self.assertEqual(connection.handlers, {})
        connection.emit("ActionInvoked", 42, "create")
        self.assertEqual(actions, ["create"])

    def test_closing_without_the_button_calls_nothing(self):
        connection = FakeConnection()
        _number, actions = self.notify(connection)
        connection.emit("NotificationClosed", 42, 2)
        self.assertEqual((actions, connection.handlers), ([], {}))

    def test_no_service_raises_and_leaves_no_subscription(self):
        connection = FakeConnection(fail=True)
        with self.assertRaises(OSError):
            self.notify(connection)
        self.assertEqual(connection.handlers, {})


class FakeThreading:
    """Records threads instead of starting them."""

    started = []

    class Thread:
        def __init__(self, target=None, args=(), name="", daemon=False):
            self.target, self.args, self.name = target, args, name

        def start(self):
            FakeThreading.started.append(self)


class DriverSuggestionTest(DriverCase):
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        FakeThreading.started = []
        self.driver["threading"] = FakeThreading
        self.driver["suggester"] = Suggester(Path(self.directory.name) / "suggested.json")

    def state(self):
        return self.driver["driver_state"]()

    def test_focusing_an_application_with_a_pack_offers_the_profile_once(self):
        self.driver["window_was_changed"]("org.freecad.freecad.desktop")
        self.assertEqual(self.state()["suggest"], "FreeCAD")
        self.assertEqual([thread.name for thread in FakeThreading.started], ["dialpad-offer"])
        suggestion = FakeThreading.started[0].args[0]
        self.assertEqual(suggestion.app_id, "org.freecad.freecad.desktop")
        # Coming back to it, or a configuration reload, offers nothing new.
        self.driver["window_was_changed"]("org.kde.krita.desktop")
        self.driver["window_was_changed"]("org.freecad.freecad.desktop")
        self.driver["window_was_changed"]("org.freecad.freecad.desktop", force=True)
        self.assertEqual(len(FakeThreading.started), 1)

    def test_an_empty_profile_of_the_application_also_prevents_the_offer(self):
        # Without functions or buttons the shortcut sets leave the profile out ("none" stays in force).
        self.config.profiles.append(Profile(name="FreeCAD", app_id="org.freecad.FreeCAD.desktop"))
        self.driver["window_was_changed"]("org.freecad.freecad.desktop")
        self.assertEqual(FakeThreading.started, [])
        self.assertEqual(self.state()["suggest"], "")
        self.assertTrue(self.driver["profile_exists_for_window"]("org.freecad.freecad.desktop"))
        self.assertFalse(self.driver["profile_exists_for_window"]("org.blender.blender.desktop"))

    def test_the_offer_shows_for_a_while_only(self):
        self.driver["window_was_changed"]("org.freecad.freecad.desktop")
        self.assertEqual(self.state()["suggest"], "FreeCAD")
        self.driver["suggestion_shown"] = ("FreeCAD", 0.0)
        self.assertEqual(self.state()["suggest"], "")

    def test_applications_with_a_profile_or_without_a_pack_get_no_offer(self):
        for window in ("org.kde.krita.desktop", "org.gnome.Calculator.desktop"):
            self.driver["window_was_changed"](window)
        self.assertEqual(FakeThreading.started, [])
        self.assertEqual(self.state()["suggest"], "")

    def test_a_reload_while_focused_offers_nothing(self):
        self.driver["window_was_changed"]("org.freecad.freecad.desktop", force=True)
        self.assertEqual(FakeThreading.started, [])

    def test_no_configuration_no_offer(self):
        self.driver["gui_config"] = None
        self.driver["window_was_changed"]("org.freecad.freecad.desktop")
        self.assertEqual(FakeThreading.started, [])

    def test_the_button_starts_the_settings_app_with_the_application(self):
        suggestion = app_suggest.Suggestion("FreeCAD", "org.freecad.FreeCAD.desktop", "freecad")
        self.driver["open_add_profile"](suggestion)
        thread = FakeThreading.started[0]
        argv = thread.args[0]
        self.assertEqual(argv[1:], [os.path.join(ROOT, "config_app", "main.py"), "--add-profile=org.freecad.FreeCAD.desktop"])
        self.assertIs(thread.target, launch.run)  # its own scope, off the touch loop

    def test_the_offer_notification_runs_the_action_for_the_create_button(self):
        sent = []

        class Notifier:
            def notify(self, summary, body, actions, on_action, icon=""):
                sent.append((summary, actions, icon))
                on_action("create")
                on_action("other")

        opened = []
        self.driver["notifications"] = type("N", (), {"ActionNotifier": Notifier})
        self.driver["open_add_profile"] = opened.append
        suggestion = app_suggest.Suggestion("FreeCAD", "org.freecad.FreeCAD.desktop", "freecad")
        self.driver["send_profile_offer"](suggestion)
        self.assertEqual(sent, [("Ready profile for FreeCAD", [("create", "Create profile")], "io.github.sinu0.AsusTurnpad")])
        self.assertEqual(opened, [suggestion])

    def test_a_missing_notification_service_is_not_fatal(self):
        class Broken:
            def notify(self, *args, **kwargs):
                raise OSError("no service")

        self.driver["notifications"] = type("N", (), {"ActionNotifier": Broken})
        self.driver["send_profile_offer"](app_suggest.Suggestion("FreeCAD", "x.desktop", "x"))


if __name__ == "__main__":
    unittest.main()
