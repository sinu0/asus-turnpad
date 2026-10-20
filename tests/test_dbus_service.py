"""The D-Bus service on a private session bus, called like the extension and the app.

Every test starts its own dbus-daemon, so the user's running driver (which owns
the real bus name on the real session bus) is never seen or disturbed. The
service runs in a child process, like the driver does.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import gi

    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib

    from config.identity import DBUS_INTERFACE, DBUS_NAME, DBUS_PATH
except (ImportError, ValueError):
    Gio = None

DBUS_DAEMON = shutil.which("dbus-daemon")

# The child: the service with callbacks that make every call observable.
# ReloadConfig emits a state with details, then details only, then
# calibration progress. Like backlight.KeyboardBacklight it subscribes to a
# signal from the main thread before the service starts; GetStatus reports
# the thread that received it.
SERVICE = r"""
import json
import sys
import threading
from gi.repository import Gio
from dialpad_dbus import DialPadDBusService

active = []
controls = []
windows = []
received = {}

def on_ping(*_args):
    received["ping_thread"] = threading.current_thread().name

try:
    # Kept referenced, as KeyboardBacklight keeps its bus.
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    bus.signal_subscribe(
        None, "test.Backlight", "Ping", "/test", None, Gio.DBusSignalFlags.NONE, on_ping,
    )
except Exception:
    pass

def reload_config():
    service.emit_overlay_state({
        "enabled": True, "title": "Volume", "titles": ["A", "B"], "icons": ["x", "y"],
        "value": 2**40, "value_angle_start": 12.5, "unit": "%", "touching": True,
        "details": {"profile": "Krita", "colors": ["#fff"]},
    })
    service.emit_overlay_state({"details": {"pointer": 42.5}})
    service.emit_calibration_progress({"step": "outer", "point": [1, 2]})
    # The same state twice sends one signal, a change sends another.
    service.emit_driver_state({"profile": "Krita", "dial": True})
    service.emit_driver_state({"dial": True, "profile": "Krita"})
    service.emit_driver_state({"profile": "Krita", "dial": False})
    return True

service = DialPadDBusService(
    on_active_application=active.append,
    on_reload_config=reload_config,
    on_start_calibration=lambda: True,
    status_provider=lambda: {
        "enabled": True, "application": active[-1] if active else "",
        "touchpad_max_x": 4010, "huge": 2**70, "ratio": 0.5, "nan": float("nan"), "none": None,
        "controls": "|".join(controls),
        "windows": "|".join(windows),
        **received,
    },
    on_cancel_calibration=lambda: False,
    on_control=lambda command: controls.append(command) or command != "bad",
    on_active_window=lambda app, title: windows.append(app + "/" + title),
    on_app_commands=lambda app: json.dumps({"app": app, "connected": False, "commands": []}),
)
print("ready" if service.start() else "failed", flush=True)
sys.stdin.read()
service.stop()
"""


@unittest.skipIf(Gio is None or DBUS_DAEMON is None, "needs PyGObject and dbus-daemon")
class DBusServiceTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.daemon = subprocess.Popen(
            [DBUS_DAEMON, "--session", "--nofork", "--print-address"],
            stdout=subprocess.PIPE, text=True,
        )
        self.address = self.daemon.stdout.readline().strip()
        self.assertTrue(self.address, "dbus-daemon printed no address")
        self.env = dict(
            os.environ,
            DBUS_SESSION_BUS_ADDRESS=self.address,
            HOME=self.home.name,
            PYTHONPATH=ROOT,
            # PyGObject's MainLoop.run() warns about asyncio on Python 3.14.
            PYTHONWARNINGS="ignore::DeprecationWarning",
        )
        self.child = None
        # Never Gio.bus_get_sync(): that would be the real session bus.
        self.connection = Gio.DBusConnection.new_for_address_sync(
            self.address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None,
        )
        self.context = GLib.MainContext.new()
        self.context.push_thread_default()
        self.signals = []

    def tearDown(self):
        self.connection.close_sync(None)
        self.context.pop_thread_default()
        if self.child is not None:
            self.child.stdin.close()
            try:
                self.child.wait(5)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait()
            self.child.stdout.close()
        self.daemon.terminate()
        self.daemon.wait(5)
        self.daemon.stdout.close()
        self.home.cleanup()

    def start_service(self):
        self.child = subprocess.Popen(
            [sys.executable, "-c", SERVICE], cwd=ROOT, env=self.env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        self.assertEqual(self.child.stdout.readline().strip(), "ready")

    def subscribe(self, sender):
        def on_signal(_connection, _sender, _path, _interface, name, parameters):
            self.signals.append((name, parameters.unpack()))

        for name in ("OverlayState", "OverlayDetails", "CalibrationProgress", "DriverState"):
            self.connection.signal_subscribe(
                sender, DBUS_INTERFACE, name, DBUS_PATH, None,
                Gio.DBusSignalFlags.NONE, on_signal,
            )
        # Let GDBus learn the owner of the name before anything is sent.
        self.pump(lambda: False, 0.2)

    def pump(self, done, timeout=5.0):
        deadline = time.monotonic() + timeout
        while not done() and time.monotonic() < deadline:
            self.context.iteration(False) or time.sleep(0.005)
        return done()

    def call(self, proxy, method, args=None):
        return proxy.call_sync(method, args, Gio.DBusCallFlags.NONE, 2000, None).unpack()

    def test_methods_and_signals(self):
        self.start_service()
        # Subscribed by name, as the extension and the settings app do.
        self.subscribe(DBUS_NAME)
        proxy = Gio.DBusProxy.new_sync(
            self.connection, Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES, None,
            DBUS_NAME, DBUS_PATH, DBUS_INTERFACE, None,
        )

        self.assertEqual(self.call(proxy, "SetActiveApplication", GLib.Variant("(s)", ("firefox",))), ())
        self.assertEqual(self.call(proxy, "StartCalibration"), (True,))
        self.assertEqual(self.call(proxy, "CancelCalibration"), (False,))
        details = json.loads(self.call(proxy, "GetOverlayDetails")[0])
        self.assertEqual(details["mode"], "multi")
        self.assertEqual(details["pointer"], -1)

        reply = proxy.call_sync("GetStatus", None, Gio.DBusCallFlags.NONE, 2000, None)
        self.assertEqual(reply.get_type_string(), "(a{sv})")
        status = reply.get_child_value(0)
        types = {
            status.get_child_value(index).get_child_value(0).get_string():
            status.get_child_value(index).get_child_value(1).get_variant().get_type_string()
            for index in range(status.n_children())
        }
        self.assertEqual(types, {
            "enabled": "b", "application": "s", "touchpad_max_x": "x", "huge": "x", "ratio": "d",
            "controls": "s", "windows": "s",
        })
        self.assertEqual(reply.unpack()[0]["application"], "firefox")
        self.assertEqual(reply.unpack()[0]["huge"], 2**63 - 1)

        with self.assertRaises(GLib.Error):
            self.call(proxy, "SetActiveApplication", GLib.Variant("(i)", (5,)))

        # SetActiveWindow hands the application and the title over; GetAppCommands answers JSON.
        self.assertEqual(self.call(proxy, "SetActiveWindow", GLib.Variant("(ss)", ("blender", "Cube - Blender"))), ())
        self.assertEqual(self.call(proxy, "GetStatus")[0]["windows"], "blender/Cube - Blender")
        self.assertEqual(
            json.loads(self.call(proxy, "GetAppCommands", GLib.Variant("(s)", ("blender",)))[0]),
            {"app": "blender", "connected": False, "commands": []},
        )
        with self.assertRaises(GLib.Error):
            self.call(proxy, "SetActiveWindow", GLib.Variant("(s)", ("blender",)))

        # Control hands the text to the driver and returns its answer.
        self.assertEqual(self.call(proxy, "Control", GLib.Variant("(s)", ("rotate:2",))), (True,))
        self.assertEqual(self.call(proxy, "Control", GLib.Variant("(s)", ("bad",))), (False,))
        with self.assertRaises(GLib.Error):
            self.call(proxy, "Control", GLib.Variant("(i)", (1,)))
        self.assertEqual(self.call(proxy, "GetStatus")[0]["controls"], "rotate:2|bad")

        # Before the first publish the state is the empty one.
        empty = json.loads(self.call(proxy, "GetDriverState")[0])
        self.assertEqual((empty["dial"], empty["profile"], empty["modifiers"]), (False, "", []))

        self.assertEqual(self.call(proxy, "ReloadConfig"), (True,))
        self.assertTrue(self.pump(lambda: any(name == "CalibrationProgress" for name, _ in self.signals)))
        self.pump(lambda: False, 0.2)

        # Details before the state, details-only sends no OverlayState, an
        # unchanged driver state sends no second DriverState.
        self.assertEqual([name for name, _ in self.signals], [
            "OverlayDetails", "OverlayState", "OverlayDetails", "CalibrationProgress",
            "DriverState", "DriverState",
        ])
        self.assertEqual(
            [json.loads(arguments[0])["dial"] for name, arguments in self.signals if name == "DriverState"],
            [True, False],
        )
        latest = json.loads(self.call(proxy, "GetDriverState")[0])
        self.assertEqual((latest["profile"], latest["dial"]), ("Krita", False))
        first = json.loads(self.signals[0][1][0])
        self.assertEqual((first["profile"], first["colors"]), ("Krita", ["#fff"]))
        self.assertEqual(self.signals[1][1], (
            True, "Volume", ["A", "B"], ["x", "y"], 2**31 - 1, 12.5, "%", False, False, True,
        ))
        self.assertEqual(json.loads(self.signals[2][1][0])["pointer"], 42.5)
        self.assertEqual(json.loads(self.signals[3][1][0]), {"step": "outer", "point": [1, 2]})

    def test_main_thread_subscriptions_run_in_the_service_loop(self):
        # backlight.KeyboardBacklight relies on this for UPower signals.
        self.start_service()
        proxy = Gio.DBusProxy.new_sync(
            self.connection, Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES, None,
            DBUS_NAME, DBUS_PATH, DBUS_INTERFACE, None,
        )
        self.connection.emit_signal(None, "/test", "test.Backlight", "Ping", None)
        deadline = time.monotonic() + 5
        status = {}
        while "ping_thread" not in status and time.monotonic() < deadline:
            time.sleep(0.02)
            status = self.call(proxy, "GetStatus")[0]
        self.assertEqual(status.get("ping_thread"), "dialpad-dbus")

    def test_second_instance_does_not_take_the_name(self):
        self.start_service()
        second = subprocess.run(
            [sys.executable, "-c", SERVICE], cwd=ROOT, env=self.env,
            input="", capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout.strip(), "failed")
        self.assertIn("already owned", second.stderr)

    def test_standalone_run_emits_one_invisible_state(self):
        # The name only exists for a moment, so match by path, not by name.
        self.subscribe(None)
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "dialpad_dbus.py")], cwd=ROOT, env=self.env,
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.pump(lambda: self.signals))
        self.pump(lambda: False, 0.2)
        self.assertEqual(self.signals, [
            ("OverlayState", (False, "", [], [], 0, 0.0, "", False, False, False)),
        ])

    def test_no_session_bus_is_not_fatal(self):
        env = dict(self.env, DBUS_SESSION_BUS_ADDRESS="unix:path=" + os.path.join(self.home.name, "none"))
        result = subprocess.run(
            [sys.executable, "-c", SERVICE], cwd=ROOT, env=env,
            input="", capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "failed")


if __name__ == "__main__":
    unittest.main()
