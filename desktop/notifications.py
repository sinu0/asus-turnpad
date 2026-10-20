"""Desktop notifications with a button (org.freedesktop.Notifications).

``ActionNotifier.notify()`` sends a notification with actions and calls
``on_action(key)`` when the user presses one. The signals arrive in the
process's default GLib main context, which the driver's D-Bus service runs.
The connection is a ``Gio.DBusConnection`` (a fake one in tests): ``call_sync``,
``signal_subscribe`` and ``signal_unsubscribe``.
"""

from __future__ import annotations

import logging

from config.identity import SLUG

log = logging.getLogger(SLUG)

SERVICE = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"
INTERFACE = "org.freedesktop.Notifications"


def session_connection():
    """The session bus connection of ``desktop.active_window`` (not the shared one)."""
    from desktop.active_window import _session_connection

    return _session_connection()


class ActionNotifier:
    def __init__(self, connection_factory=session_connection, app_name: str = "Turnpad"):
        self._connection_factory = connection_factory
        self.app_name = app_name

    def notify(self, summary: str, body: str, actions, on_action, icon: str = "", timeout_ms: int = 20000) -> int:
        """Show a notification; ``actions`` is [(key, label), ...]. Returns its id.
        Raises when the notification service is not there."""
        from gi.repository import Gio, GLib

        connection = self._connection_factory()
        state = {"id": None, "subscriptions": []}

        def finish():
            for subscription in state["subscriptions"]:
                try:
                    connection.signal_unsubscribe(subscription)
                except Exception:
                    pass
            state["subscriptions"] = []

        def on_signal(_connection, _sender, _path, _interface, signal, parameters):
            values = parameters.unpack()
            if values[0] != state["id"]:
                return
            if signal == "ActionInvoked":
                try:
                    on_action(values[1])
                except Exception:
                    log.exception("Notification action failed")
            finish()

        for signal in ("ActionInvoked", "NotificationClosed"):
            state["subscriptions"].append(connection.signal_subscribe(
                SERVICE, INTERFACE, signal, PATH, None, Gio.DBusSignalFlags.NONE, on_signal,
            ))
        flat = [item for key, label in actions for item in (key, label)]
        try:
            reply = connection.call_sync(
                SERVICE, PATH, INTERFACE, "Notify",
                GLib.Variant("(susssasa{sv}i)", (self.app_name, 0, icon, summary, body, flat, {}, timeout_ms)),
                GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 3000, None,
            )
        except Exception:
            finish()
            raise
        state["id"] = reply.unpack()[0]
        # A server that never reports the end must not keep the subscriptions forever.
        GLib.timeout_add_seconds(max(5, timeout_ms // 1000) + 30, lambda: (finish(), GLib.SOURCE_REMOVE)[1])
        return state["id"]
