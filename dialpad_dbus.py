#!/usr/bin/env python3
"""Session D-Bus interface for the DialPad driver."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import logging
import math
import threading
import time
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from config.identity import DBUS_INTERFACE, DBUS_NAME, DBUS_PATH


BUS_NAME = DBUS_NAME
OBJECT_PATH = DBUS_PATH
INTERFACE_NAME = DBUS_INTERFACE
_STARTUP_TIMEOUT_SECONDS = 2.0
_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
# org.freedesktop.DBus.RequestName: fail instead of waiting in the queue.
_NAME_FLAG_DO_NOT_QUEUE = 4
_NAME_REPLY_PRIMARY_OWNER = 1

# The wire contract (see the D-Bus table in AGENTS.md). Argument names and
# unnamed out arguments match what the dbus-python version exported.
INTERFACE_XML = f"""<node>
  <interface name="{INTERFACE_NAME}">
    <method name="CancelCalibration">
      <arg direction="out" type="b"/>
    </method>
    <method name="GetOverlayDetails">
      <arg direction="out" type="s"/>
    </method>
    <method name="SetActiveApplication">
      <arg direction="in" type="s" name="application"/>
    </method>
    <!-- The focused window: its application id and title (a KWin script or
         the Shell extension); SetActiveApplication is the same without a title. -->
    <method name="SetActiveWindow">
      <arg direction="in" type="s" name="application"/>
      <arg direction="in" type="s" name="title"/>
    </method>
    <!-- The commands the linked application (the Blender add-on) offers, as JSON:
         app, name, version, connected, context, commands[]. The argument is the
         link's application name or a desktop id. -->
    <method name="GetAppCommands">
      <arg direction="in" type="s" name="application"/>
      <arg direction="out" type="s"/>
    </method>
    <method name="GetStatus">
      <arg direction="out" type="a{{sv}}"/>
    </method>
    <method name="ReloadConfig">
      <arg direction="out" type="b"/>
    </method>
    <method name="StartCalibration">
      <arg direction="out" type="b"/>
    </method>
    <!-- The driver's state as JSON (what DriverState last carried). -->
    <method name="GetDriverState">
      <arg direction="out" type="s"/>
    </method>
    <!-- A dial command from another program (grammar: parse_control());
         true when it was understood, not when it changed anything. -->
    <method name="Control">
      <arg direction="in" type="s" name="command"/>
      <arg direction="out" type="b"/>
    </method>
    <!-- Switch the dial on or off, as holding the corner icon does. -->
    <method name="Toggle">
      <arg direction="out" type="b"/>
    </method>
    <signal name="OverlayState">
      <arg type="b" name="enabled"/>
      <arg type="s" name="title"/>
      <arg type="as" name="titles"/>
      <arg type="as" name="icons"/>
      <arg type="i" name="value"/>
      <arg type="d" name="value_angle_start"/>
      <arg type="s" name="unit"/>
      <arg type="b" name="center_pressed"/>
      <arg type="b" name="value_show_only_progress"/>
      <arg type="b" name="touching"/>
    </signal>
    <!-- Presentation details as JSON: profile, colours, hints, pointer. -->
    <signal name="OverlayDetails">
      <arg type="s" name="details_json"/>
    </signal>
    <!-- Profile, application, dial, group, function, held keys and the
         SpaceMouse connection as JSON; sent when the text changes. -->
    <signal name="DriverState">
      <arg type="s" name="state_json"/>
    </signal>
    <!-- Guided calibration state as JSON (step, message, error, result…). -->
    <signal name="CalibrationProgress">
      <arg type="s" name="state_json"/>
    </signal>
  </interface>
</node>"""

# What GetDriverState answers before the driver has published anything.
_EMPTY_DRIVER_STATE = {
    "profile": "",
    "app": "",
    "dial": False,
    "mode": "multi",
    "group": "",
    "function": "",
    "modifiers": [],
    "keys_held": [],
    "spacemouse": False,
    "spacemouse_device": "",
    "config_revision": "",
    # The app link: the focused application has a connected add-on, its
    # context ({mode, label, detail} or {}), the profile context in force,
    # a ready profile on offer (pack name) and the last notice ({id, text}).
    "app_link": False,
    "app_context": {},
    "context": "",
    "suggest": "",
    "notice": {},
}

NODE_INFO = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML)
INTERFACE_INFO = NODE_INFO.lookup_interface(INTERFACE_NAME)


def signal_signature(name: str) -> str:
    """The D-Bus signature of a signal's arguments, e.g. ``bsasasidsbbb``."""
    return "".join(arg.signature for arg in INTERFACE_INFO.lookup_signal(name).args)


_OVERLAY_STATE_TYPE = "(" + signal_signature("OverlayState") + ")"
# Methods whose reply is the boolean result of a driver callback.
_BOOL_METHODS = {
    "CancelCalibration": "_on_cancel_calibration",
    "ReloadConfig": "_on_reload_config",
    "StartCalibration": "_on_start_calibration",
    "Toggle": "_on_toggle",
}

LOG = logging.getLogger(__name__)

CONTROL_ROTATE_MAX = 10
CONTROL_MAX_LENGTH = 200
CONTROL_BUTTON_MAX = 1023
CONTROL_MUTE_MAX = 5000
_CONTROL_PLAIN = ("toggle", "dial-on", "dial-off", "select", "back", "group")


def parse_control(text: Any) -> tuple[str, Any] | None:
    """Read a Control command into ``(verb, argument)``; None when it is not one.

    ``toggle``, ``dial-on``, ``dial-off``, ``select``, ``back``, ``group`` (the
    first group; argument None), ``group=TITLE``, ``rotate:N`` (N from -10 to
    10 except 0, clockwise when positive), ``function:N`` (menu slot from 0,
    verb ``function``), ``function=TITLE`` (verb ``function-title``),
    ``button:INDEX`` (a SpaceMouse button's press action, INDEX 0 to 1023),
    ``mute:MS`` (silence the other spacenavd clients for MS milliseconds, 0 to
    5000, 0 ends it; a lease, not a queued command).
    Verbs are lower case, surrounding blanks are ignored.
    """
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text or len(text) > CONTROL_MAX_LENGTH:
        return None
    if text in _CONTROL_PLAIN:
        return (text, None)
    # The first ':' or '=' separates; a title may hold either.
    name = text.split(":", 1)[0].split("=", 1)[0]
    separator = text[len(name):len(name) + 1]
    value = text[len(name) + 1:].strip()
    if separator == ":":
        # int() would also take "1_0", "٣" and "+3"; digits and a sign only.
        digits = value[1:] if value[:1] in "+-" else value
        if name not in ("rotate", "function", "button", "mute") or not (digits.isascii() and digits.isdigit()):
            return None
        number = int(value)
        if name == "rotate":
            if number == 0 or abs(number) > CONTROL_ROTATE_MAX:
                return None
        elif number < 0 or (name == "button" and number > CONTROL_BUTTON_MAX) or (
            name == "mute" and number > CONTROL_MUTE_MAX
        ):
            return None
        return (name, number)
    if separator == "=" and value:
        if name == "function":
            return ("function-title", value)
        if name == "group":
            return ("group", value)
    return None


class DialPadDBusService:
    """Owns the DialPad session service and its private GLib main-loop thread."""

    def __init__(
        self,
        on_active_application: Callable[[str], Any],
        on_reload_config: Callable[[], Any],
        on_start_calibration: Callable[[], Any],
        status_provider: Callable[[], Mapping[str, Any]],
        on_cancel_calibration: Callable[[], Any] | None = None,
        on_toggle: Callable[[], Any] | None = None,
        on_control: Callable[[str], Any] | None = None,
        driver_state_provider: Callable[[], Mapping[str, Any]] | None = None,
        on_active_window: Callable[[str, str], Any] | None = None,
        on_app_commands: Callable[[str], str] | None = None,
    ) -> None:
        self._on_active_window = on_active_window
        self._on_app_commands = on_app_commands
        self._driver_state_provider = driver_state_provider
        self._on_cancel_calibration = on_cancel_calibration
        self._on_control = on_control
        self._on_toggle = on_toggle
        self._on_active_application = on_active_application
        self._on_reload_config = on_reload_config
        self._on_start_calibration = on_start_calibration
        self._status_provider = status_provider
        self._lock = threading.RLock()
        self._startup_complete = threading.Event()
        self._stop_requested = False
        self._started = False
        self._thread: threading.Thread | None = None
        self._main_loop: GLib.MainLoop | None = None
        # Own connection, not the shared Gio.bus_get_sync() one: that one
        # exits the process when the session bus goes away.
        self._connection: Gio.DBusConnection | None = None
        self._overlay_state: dict[str, Any] = {
            "enabled": False,
            "title": "",
            "titles": [],
            "icons": [],
            "value": 0,
            "value_angle_start": 0.0,
            "unit": "",
            "center_pressed": False,
            "value_show_only_progress": False,
            "touching": False,
        }
        # The last DriverState text: the signal is sent only when it changes.
        self._driver_state_json = json.dumps(_EMPTY_DRIVER_STATE, sort_keys=True)
        # Extra presentation data kept outside the fixed OverlayState
        # signature so that older Shell extensions keep working.
        self._details: dict[str, Any] = {
            "profile": "",
            "mode": "multi",
            "colors": [],
            "color": "",
            "hints": {},
            "hold_returns": True,
            "pointer": -1,
            # Profile and function overrides of the dial's position and
            # visibility (see config.schema.OVERLAY_RULE_KEYS).
            "look": {},
            # The selected function's icon; the group it was chosen from.
            "icon": "",
            "parent": "",
            # A group's list beside the dial while it is open: title, index
            # (its menu sector), items, icons, colors, selected.
            "submenu": None,
        }

    def _details_json(self) -> str:
        with self._lock:
            return json.dumps(self._details)

    def start(self) -> bool:
        """Start the D-Bus service without making a session bus mandatory."""
        with self._lock:
            if self._started:
                return True
            if self._thread is not None:
                LOG.warning("DialPad D-Bus service is already starting")
                return False
            if self._stop_requested:
                return False

            self._startup_complete = threading.Event()
            thread = threading.Thread(
                target=self._run_main_loop,
                name="dialpad-dbus",
                daemon=True,
            )
            self._thread = thread

        thread.start()
        if not self._startup_complete.wait(_STARTUP_TIMEOUT_SECONDS):
            LOG.error("Timed out starting the DialPad D-Bus service")
            self.stop()
            return False

        with self._lock:
            return self._started

    def stop(self) -> None:
        """Stop the D-Bus loop and release the session name when possible."""
        with self._lock:
            self._stop_requested = True
            main_loop = self._main_loop
            thread = self._thread

        if main_loop is not None:
            try:
                main_loop.quit()
            except Exception:
                LOG.exception("Failed to stop the DialPad D-Bus main loop")

        if thread is not None and thread is not threading.current_thread():
            thread.join(_STARTUP_TIMEOUT_SECONDS)
            if thread.is_alive():
                LOG.warning("DialPad D-Bus thread did not stop before the timeout")

    def emit_overlay_state(self, payload: dict) -> None:
        """Merge a partial state update and schedule a full OverlayState signal."""
        data: Mapping[str, Any] = payload if isinstance(payload, Mapping) else {}
        with self._lock:
            state = self._overlay_state
            was_enabled = state["enabled"]

            details = data.get("details")
            details_json = None
            if isinstance(details, Mapping):
                self._details.update(
                    (str(key), value) for key, value in details.items()
                )
                try:
                    details_json = json.dumps(self._details)
                except (TypeError, ValueError):
                    LOG.warning("Dropping non-serialisable overlay details")
                    details_json = None
            # A details-only update (for example the finger pointer) must not
            # re-send the whole overlay state.
            details_only = set(data.keys()) <= {"details", "ts"}

            if "enabled" in data:
                state["enabled"] = self._as_bool(data["enabled"])
            if "title" in data:
                state["title"] = self._as_text(data["title"])
            if "titles" in data:
                state["titles"] = self._as_text_list(data["titles"])
            if "icons" in data:
                state["icons"] = self._as_text_list(data["icons"])
            if "value" in data:
                state["value"] = self._as_int32(data["value"])
            if "value_angle_start" in data:
                state["value_angle_start"] = self._as_float(data["value_angle_start"])
            if "unit" in data:
                state["unit"] = self._as_text(data["unit"])
            if "center_pressed" in data:
                state["center_pressed"] = self._as_bool(data["center_pressed"])
            if "value_show_only_progress" in data:
                state["value_show_only_progress"] = self._as_bool(
                    data["value_show_only_progress"]
                )
            if data.get("input") == "center":
                state["center_pressed"] = self._as_bool(data.get("value"))
            if "touching" in data:
                state["touching"] = self._as_bool(data["touching"])
            # Progress-only mode belongs to the update that asked for it;
            # a menu or function change must not inherit it.
            if (
                ("title" in data or "titles" in data)
                and "value_show_only_progress" not in data
            ):
                state["value_show_only_progress"] = False

            if was_enabled and not state["enabled"]:
                state.update(
                    title="",
                    value=0,
                    value_angle_start=0.0,
                    unit="",
                    center_pressed=False,
                    value_show_only_progress=False,
                    touching=False,
                )

            values = None if details_only else self._normalize_overlay_payload(state)
            connection = self._connection if self._started else None

        if connection is None or (values is None and details_json is None):
            return

        try:
            GLib.idle_add(self._emit_overlay_state, connection, values, details_json)
        except Exception:
            LOG.exception("Failed to schedule the DialPad overlay state signal")

    def emit_driver_state(self, state: Mapping[str, Any]) -> bool:
        """Remember the driver state and schedule a DriverState signal when it
        differs from the last one. True when a signal was scheduled. Cheap, and
        never waits for the bus: the emit runs in the GLib thread."""
        try:
            text = json.dumps(dict(state), sort_keys=True)
        except (TypeError, ValueError):
            LOG.warning("Dropping non-serialisable driver state")
            return False
        with self._lock:
            if text == self._driver_state_json:
                return False
            self._driver_state_json = text
            connection = self._connection if self._started else None
        if connection is None:
            return False

        def emit() -> bool:
            if self._is_current(connection):
                try:
                    self._emit_signal(connection, "DriverState", GLib.Variant("(s)", (text,)))
                except Exception:
                    LOG.exception("Failed to emit the driver state signal")
            return GLib.SOURCE_REMOVE

        try:
            GLib.idle_add(emit)
        except Exception:
            LOG.exception("Failed to schedule the driver state signal")
            return False
        return True

    def _driver_state_text(self) -> str:
        """The answer to GetDriverState: a snapshot taken now, else the last
        published one. Not remembered: only emit_driver_state decides what the
        signal has told the subscribers."""
        if self._driver_state_provider is not None:
            try:
                return json.dumps(dict(self._driver_state_provider()), sort_keys=True)
            except Exception:
                LOG.exception("Failed to read the live driver state")
        with self._lock:
            return self._driver_state_json

    def emit_calibration_progress(self, state: Mapping[str, Any]) -> None:
        with self._lock:
            connection = self._connection if self._started else None
        if connection is None:
            return
        try:
            payload = json.dumps(dict(state))
        except (TypeError, ValueError):
            LOG.warning("Dropping non-serialisable calibration state")
            return

        def emit() -> bool:
            if self._is_current(connection):
                try:
                    self._emit_signal(connection, "CalibrationProgress", GLib.Variant("(s)", (payload,)))
                except Exception:
                    LOG.exception("Failed to emit the calibration progress signal")
            return GLib.SOURCE_REMOVE

        GLib.idle_add(emit)

    def _run_main_loop(self) -> None:
        connection: Gio.DBusConnection | None = None
        registration_id = 0
        main_loop: GLib.MainLoop | None = None

        try:
            # The loop runs the process's default main context, as the
            # dbus-python version did: backlight.KeyboardBacklight subscribes
            # to UPower from the main thread and its signals arrive here.
            # Connection, object and emits below all use that context too.
            address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
            connection = Gio.DBusConnection.new_for_address_sync(
                address,
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None,
                None,
            )
            registration_id = connection.register_object(
                OBJECT_PATH, INTERFACE_INFO, self._on_method_call, None, None
            )
            reply = connection.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "RequestName",
                GLib.Variant("(su)", (BUS_NAME, _NAME_FLAG_DO_NOT_QUEUE)),
                GLib.VariantType("(u)"),
                Gio.DBusCallFlags.NONE,
                int(_STARTUP_TIMEOUT_SECONDS * 1000),
                None,
            )
            if reply.unpack()[0] != _NAME_REPLY_PRIMARY_OWNER:
                raise RuntimeError("the DialPad D-Bus name is already owned")
            main_loop = GLib.MainLoop()
            # Without this the service would keep "running" on a dead bus and
            # log a failure for every overlay update.
            connection.connect("closed", self._on_connection_closed, main_loop)

            with self._lock:
                should_run = not self._stop_requested
                if should_run:
                    self._connection = connection
                    self._main_loop = main_loop
                    self._started = True
                self._startup_complete.set()

            if should_run:
                main_loop.run()
        except Exception:
            LOG.exception("DialPad D-Bus service is unavailable")
        finally:
            with self._lock:
                if self._thread is threading.current_thread():
                    self._started = False
                    self._main_loop = None
                    self._connection = None

            if connection is not None:
                if registration_id:
                    try:
                        connection.unregister_object(registration_id)
                    except Exception:
                        LOG.exception("Failed to remove the DialPad D-Bus object")
                if not connection.is_closed():
                    # Closing the connection also releases the name; flush
                    # first so that signals already sent still arrive.
                    try:
                        connection.flush_sync(None)
                        connection.close_sync(None)
                    except Exception:
                        LOG.exception("Failed to close the DialPad D-Bus connection")

            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None
                self._startup_complete.set()

    @staticmethod
    def _on_connection_closed(
        _connection: Gio.DBusConnection,
        remote_peer_vanished: bool,
        error: GLib.Error | None,
        main_loop: GLib.MainLoop,
    ) -> None:
        if remote_peer_vanished:
            LOG.warning("Lost the session D-Bus connection: %s", error.message if error else "closed")
        main_loop.quit()

    def _is_current(self, connection: Gio.DBusConnection) -> bool:
        with self._lock:
            return self._started and self._connection is connection

    @staticmethod
    def _emit_signal(connection: Gio.DBusConnection, name: str, parameters: GLib.Variant) -> None:
        connection.emit_signal(None, OBJECT_PATH, INTERFACE_NAME, name, parameters)

    def _emit_overlay_state(
        self,
        connection: Gio.DBusConnection,
        values: tuple[Any, ...] | None,
        details_json: str | None = None,
    ) -> bool:
        if not self._is_current(connection):
            return GLib.SOURCE_REMOVE

        try:
            # Details first: the overlay repaints on OverlayState and must
            # already know the colours and hints of the new state.
            if details_json is not None:
                self._emit_signal(connection, "OverlayDetails", GLib.Variant("(s)", (details_json,)))
            if values is not None:
                self._emit_signal(connection, "OverlayState", GLib.Variant(_OVERLAY_STATE_TYPE, values))
        except Exception:
            LOG.exception("Failed to emit the DialPad overlay state signal")
        return GLib.SOURCE_REMOVE

    def _on_method_call(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _object_path: str,
        _interface_name: str,
        method_name: str,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        # Gio has already checked the method name and argument types against
        # INTERFACE_INFO and answered bad calls itself.
        try:
            if method_name == "SetActiveApplication":
                self._set_active_application(str(parameters.unpack()[0]))
                reply = None
            elif method_name == "SetActiveWindow":
                application, window_title = parameters.unpack()
                self._set_active_window(str(application), str(window_title))
                reply = None
            elif method_name == "GetAppCommands":
                reply = GLib.Variant("(s)", (self._app_commands(str(parameters.unpack()[0])),))
            elif method_name == "GetStatus":
                reply = GLib.Variant("(a{sv})", (self._get_status(),))
            elif method_name == "GetOverlayDetails":
                reply = GLib.Variant("(s)", (self._details_json(),))
            elif method_name == "GetDriverState":
                reply = GLib.Variant("(s)", (self._driver_state_text(),))
            elif method_name == "Control":
                reply = GLib.Variant("(b)", (self._control(str(parameters.unpack()[0])),))
            elif method_name in _BOOL_METHODS:
                callback = getattr(self, _BOOL_METHODS[method_name])
                reply = GLib.Variant("(b)", (self._call_bool_callback(callback, method_name),))
            else:
                invocation.return_dbus_error(
                    "org.freedesktop.DBus.Error.UnknownMethod",
                    f"Unknown method: {method_name}",
                )
                return
        except Exception as error:
            LOG.exception("%s failed", method_name)
            invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(error))
            return
        invocation.return_value(reply)

    def _set_active_application(self, application: str) -> None:
        callback = self._on_active_application
        if callback is None:
            return
        try:
            callback(application)
        except Exception:
            LOG.exception("SetActiveApplication callback failed")

    def _set_active_window(self, application: str, title: str) -> None:
        callback = self._on_active_window
        if callback is None:
            return
        try:
            callback(application, title)
        except Exception:
            LOG.exception("SetActiveWindow callback failed")

    def _app_commands(self, application: str) -> str:
        """The JSON catalog of a linked application; without a link server an empty one."""
        callback = self._on_app_commands
        if callback is not None:
            try:
                return str(callback(application))
            except Exception:
                LOG.exception("GetAppCommands callback failed")
        return json.dumps({"app": application, "name": "", "version": "", "connected": False,
                           "context": {}, "commands": []})

    def _control(self, command: str) -> bool:
        callback = self._on_control
        if callback is None:
            return False
        try:
            return bool(callback(command))
        except Exception:
            LOG.exception("Control callback failed")
            return False

    def _call_bool_callback(self, callback: Callable[[], Any] | None, name: str) -> bool:
        if callback is None:
            return False
        try:
            return bool(callback())
        except Exception:
            LOG.exception("%s callback failed", name)
            return False

    def _get_status(self) -> dict[str, GLib.Variant]:
        """The status as a{sv}: b, s, x (int64, clamped) and d; other values are left out."""
        provider = self._status_provider
        if provider is None:
            return {}

        try:
            status = provider()
        except Exception:
            LOG.exception("GetStatus provider failed")
            return {}

        if not isinstance(status, Mapping):
            LOG.warning("GetStatus provider returned a non-mapping value")
            return {}

        result: dict[str, GLib.Variant] = {}
        for key, value in status.items():
            try:
                key_text = str(key)
            except Exception:
                LOG.warning("GetStatus provider returned a non-stringable key")
                continue

            if isinstance(value, bool):
                result[key_text] = GLib.Variant("b", value)
            elif isinstance(value, str):
                result[key_text] = GLib.Variant("s", value)
            elif isinstance(value, int):
                result[key_text] = GLib.Variant("x", max(_INT64_MIN, min(value, _INT64_MAX)))
            elif isinstance(value, float) and math.isfinite(value):
                result[key_text] = GLib.Variant("d", value)

        return result

    @staticmethod
    def _normalize_overlay_payload(payload: dict) -> tuple[Any, ...]:
        """The OverlayState arguments in signature order (``bsasasidsbbb``)."""
        data: Mapping[str, Any] = payload if isinstance(payload, Mapping) else {}
        return (
            DialPadDBusService._as_bool(data.get("enabled", False)),
            DialPadDBusService._as_text(data.get("title", "")),
            DialPadDBusService._as_text_list(data.get("titles", [])),
            DialPadDBusService._as_text_list(data.get("icons", [])),
            DialPadDBusService._as_int32(data.get("value", 0)),
            DialPadDBusService._as_float(data.get("value_angle_start", 0.0)),
            DialPadDBusService._as_text(data.get("unit", "")),
            DialPadDBusService._as_bool(data.get("center_pressed", False)),
            DialPadDBusService._as_bool(data.get("value_show_only_progress", False)),
            DialPadDBusService._as_bool(data.get("touching", False)),
        )

    @staticmethod
    def _as_bool(value: Any) -> bool:
        return bool(value)

    @staticmethod
    def _as_text(value: Any) -> str:
        if value is None:
            return ""
        try:
            return str(value)
        except Exception:
            return ""

    @staticmethod
    def _as_text_list(value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)):
            return []
        return [DialPadDBusService._as_text(item) for item in value]

    @staticmethod
    def _as_int32(value: Any) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            return 0
        return max(_INT32_MIN, min(number, _INT32_MAX))

    @staticmethod
    def _as_float(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        return number if math.isfinite(number) else 0.0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    service = DialPadDBusService(
        on_active_application=lambda _application: None,
        on_reload_config=lambda: True,
        on_start_calibration=lambda: True,
        status_provider=lambda: {"running": True},
    )
    try:
        if service.start():
            service.emit_overlay_state({"enabled": False})
            time.sleep(0.05)
    finally:
        service.stop()
