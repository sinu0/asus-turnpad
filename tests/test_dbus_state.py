import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dialpad_dbus import DialPadDBusService, parse_control
except ImportError:  # PyGObject missing
    DialPadDBusService = None
    parse_control = None


@unittest.skipIf(DialPadDBusService is None, "PyGObject is not installed")
class OverlayStateTest(unittest.TestCase):
    def setUp(self):
        # Never started, so updates only touch the in-memory state.
        self.service = DialPadDBusService(
            on_active_application=lambda app: None,
            on_reload_config=lambda: True,
            on_start_calibration=lambda: False,
            status_provider=dict,
        )

    def state(self):
        return self.service._overlay_state

    def test_signal_matches_declared_signature(self):
        from gi.repository import GLib
        from dialpad_dbus import signal_signature

        signature = signal_signature("OverlayState")
        self.assertEqual(signature, "bsasasidsbbb")
        values = DialPadDBusService._normalize_overlay_payload(self.state())
        variant = GLib.Variant("(%s)" % signature, values)
        self.assertEqual(variant.get_type_string(), "(bsasasidsbbb)")

    def test_interface_matches_contract(self):
        from dialpad_dbus import INTERFACE_INFO

        signals = {
            signal.name: "".join(arg.signature for arg in signal.args)
            for signal in INTERFACE_INFO.signals
        }
        self.assertEqual(signals, {
            "OverlayState": "bsasasidsbbb",
            "OverlayDetails": "s",
            "CalibrationProgress": "s",
            "DriverState": "s",
        })
        methods = {
            method.name: (
                "".join(arg.signature for arg in method.in_args),
                "".join(arg.signature for arg in method.out_args),
            )
            for method in INTERFACE_INFO.methods
        }
        self.assertEqual(methods, {
            "SetActiveApplication": ("s", ""),
            "GetStatus": ("", "a{sv}"),
            "GetOverlayDetails": ("", "s"),
            "ReloadConfig": ("", "b"),
            "StartCalibration": ("", "b"),
            "CancelCalibration": ("", "b"),
            "Toggle": ("", "b"),
            "GetDriverState": ("", "s"),
            "Control": ("s", "b"),
            "SetActiveWindow": ("ss", ""),
            "GetAppCommands": ("s", "s"),
        })

    def test_status_keeps_variant_types(self):
        self.service._status_provider = lambda: {
            "enabled": True,
            "layout": "proartp16",
            "touchpad_max_x": 4010,
            "huge": 2**70,
            "ratio": 0.5,
            "nan": float("nan"),
            "none": None,
        }
        status = self.service._get_status()
        self.assertEqual(
            {key: value.get_type_string() for key, value in status.items()},
            {"enabled": "b", "layout": "s", "touchpad_max_x": "x", "huge": "x", "ratio": "d"},
        )
        self.assertEqual(status["huge"].unpack(), 2**63 - 1)

    def test_progress_mode_is_not_sticky(self):
        self.service.emit_overlay_state({"enabled": True})
        self.service.emit_overlay_state({"title": "Edit", "value": 40, "value_show_only_progress": True})
        self.assertTrue(self.state()["value_show_only_progress"])
        self.service.emit_overlay_state({"titles": [], "icons": [], "title": "Volume", "value": 0, "unit": ""})
        self.assertFalse(self.state()["value_show_only_progress"])

    def test_partial_update_keeps_progress_mode(self):
        self.service.emit_overlay_state({"title": "Edit", "value": 40, "value_show_only_progress": True})
        self.service.emit_overlay_state({"touching": True})
        self.assertTrue(self.state()["value_show_only_progress"])
        self.assertTrue(self.state()["touching"])

    def test_disable_resets_transient_state(self):
        self.service.emit_overlay_state({"enabled": True, "title": "Volume", "value": 5, "touching": True})
        self.service.emit_overlay_state({"input": "center", "value": 1})
        self.service.emit_overlay_state({"enabled": False})
        state = self.state()
        self.assertEqual(state["title"], "")
        self.assertFalse(state["center_pressed"])
        self.assertFalse(state["touching"])

    def test_values_are_coerced(self):
        values = DialPadDBusService._normalize_overlay_payload({
            "value": "42",
            "title": None,
            "titles": ["A", None],
            "touching": 1,
        })
        self.assertEqual(values[4], 42)
        self.assertEqual(str(values[1]), "")
        self.assertEqual([str(item) for item in values[2]], ["A", ""])
        self.assertTrue(bool(values[9]))

    def test_details_merge_and_stay_out_of_state(self):
        self.service.emit_overlay_state({"details": {"profile": "Krita", "colors": ["#fff"]}})
        self.service.emit_overlay_state({"details": {"pointer": 42.5}})
        details = self.service._details
        self.assertEqual(details["profile"], "Krita")
        self.assertEqual(details["colors"], ["#fff"])
        self.assertEqual(details["pointer"], 42.5)
        self.assertEqual(self.state()["title"], "")

    def test_details_json_round_trip(self):
        import json

        self.service.emit_overlay_state({"details": {"hints": {"cw": "Zoom in"}}})
        self.assertEqual(json.loads(self.service._details_json())["hints"], {"cw": "Zoom in"})

    def test_submenu_details_open_and_close(self):
        import json

        submenu = {"title": "Brush", "index": 1, "items": ["Size", "Opacity"], "icons": ["", ""],
                   "colors": ["#ffad66", "#f4c95d"], "selected": 1}
        self.service.emit_overlay_state({"details": {"submenu": submenu, "parent": ""},
                                         "titles": ["Zoom", "Brush"], "title": "Opacity"})
        self.assertEqual(json.loads(self.service._details_json())["submenu"], submenu)
        # Choosing a function closes the list; its group stays as the parent.
        self.service.emit_overlay_state({"details": {"submenu": None, "parent": "Brush"}})
        details = json.loads(self.service._details_json())
        self.assertIsNone(details["submenu"])
        self.assertEqual(details["parent"], "Brush")

    def test_driver_state_signal_only_when_it_changes(self):
        service = self.service
        service._started = True
        service._connection = object()
        with mock.patch("dialpad_dbus.GLib.idle_add") as idle_add:
            self.assertTrue(service.emit_driver_state({"dial": True, "profile": "Krita"}))
            # Same content, other key order: nothing to tell.
            self.assertFalse(service.emit_driver_state({"profile": "Krita", "dial": True}))
            self.assertTrue(service.emit_driver_state({"profile": "Krita", "dial": False}))
            self.assertEqual(idle_add.call_count, 2)
        self.assertEqual(json.loads(service._driver_state_json), {"profile": "Krita", "dial": False})

    def test_get_driver_state_is_a_live_snapshot_when_the_driver_provides_one(self):
        service = self.service
        service.emit_driver_state({"dial": True})
        # No provider: the last published text.
        self.assertEqual(json.loads(service._driver_state_text()), {"dial": True})
        live = {"dial": False, "profile": "Krita"}
        service._driver_state_provider = lambda: live
        self.assertEqual(json.loads(service._driver_state_text()), live)
        live["profile"] = "Blender"
        self.assertEqual(json.loads(service._driver_state_text())["profile"], "Blender")
        # What the signal last told the subscribers is not changed by a read.
        self.assertEqual(json.loads(service._driver_state_json), {"dial": True})

        def broken():
            raise RuntimeError("driver is busy")

        service._driver_state_provider = broken
        with self.assertLogs("dialpad_dbus", level="ERROR"):
            self.assertEqual(json.loads(service._driver_state_text()), {"dial": True})

    def test_driver_state_before_start_is_kept_without_a_signal(self):
        with mock.patch("dialpad_dbus.GLib.idle_add") as idle_add:
            self.assertFalse(self.service.emit_driver_state({"dial": True}))
            idle_add.assert_not_called()
        self.assertEqual(json.loads(self.service._driver_state_json), {"dial": True})

    def test_driver_state_starts_with_every_key(self):
        state = json.loads(self.service._driver_state_json)
        self.assertEqual(sorted(state), sorted([
            "profile", "app", "dial", "mode", "group", "function", "modifiers", "keys_held",
            "spacemouse", "spacemouse_device", "config_revision",
            "app_link", "app_context", "context", "suggest", "notice",
        ]))

    def test_unserialisable_driver_state_is_dropped(self):
        before = self.service._driver_state_json
        self.assertFalse(self.service.emit_driver_state({"bad": object()}))
        self.assertEqual(self.service._driver_state_json, before)


@unittest.skipIf(parse_control is None, "PyGObject is not installed")
class ParseControlTest(unittest.TestCase):
    def test_plain_commands(self):
        for command in ("toggle", "dial-on", "dial-off", "select", "back", "group"):
            self.assertEqual(parse_control(command), (command, None))
        self.assertEqual(parse_control("  toggle \n"), ("toggle", None))

    def test_numbers(self):
        self.assertEqual(parse_control("rotate:3"), ("rotate", 3))
        self.assertEqual(parse_control("rotate:-10"), ("rotate", -10))
        self.assertEqual(parse_control("rotate:+1"), ("rotate", 1))
        self.assertEqual(parse_control("function:0"), ("function", 0))
        self.assertEqual(parse_control("function: 4"), ("function", 4))
        self.assertEqual(parse_control("button:22"), ("button", 22))
        self.assertEqual(parse_control("mute:3000"), ("mute", 3000))
        self.assertEqual(parse_control("mute: 0"), ("mute", 0))
        self.assertEqual(parse_control("mute:5000"), ("mute", 5000))

    def test_titles(self):
        self.assertEqual(parse_control("function=Zoom"), ("function-title", "Zoom"))
        self.assertEqual(parse_control("group=Brush size"), ("group", "Brush size"))
        # Only the first ':' or '=' separates; the title may hold either.
        self.assertEqual(parse_control("function=Zoom=2:1"), ("function-title", "Zoom=2:1"))

    def test_rejected(self):
        for command in (
            "", "   ", "Toggle", "dial", "rotate", "rotate:", "rotate:0", "rotate:11", "rotate:-11",
            "rotate:1.5", "rotate:one", "rotate:1_0", "rotate:\u0663", "function:-1", "function:",
            "function", "function=", "group=", "button:-1", "button:1024", "button", "button=3",
            "toggle:1", "back=x", "select:1", "mute", "mute:", "mute:5001", "mute:-1", "mute:1.5", "mute=3000", "mute:3_0", "x" * 201, "function=" + "x" * 200,
        ):
            self.assertIsNone(parse_control(command), command)
        for value in (None, 3, b"toggle", ["toggle"]):
            self.assertIsNone(parse_control(value))


if __name__ == "__main__":
    unittest.main()
