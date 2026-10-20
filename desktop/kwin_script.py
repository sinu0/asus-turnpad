"""Focused application on KDE Plasma, pushed by a KWin script.

Plasma 6 has no D-Bus call that answers "which window is active"
(``org.kde.KWin.activeWindow`` is gone), so the driver loads a tiny KWin
script that reports every activation, with the window's title (and each change of
that title), to the driver's own D-Bus method ``SetActiveWindow``, and once when
it is loaded. ``SetActiveApplication`` (the application only) stays for the GNOME
Shell extension and older scripts. The polling backends in ``active_window.py`` stay as
the fallback.

The pure parts (the script text, the call sequence over a ``bus`` with a
``call()`` method) are unit tested; ``GioBus`` is the real session bus.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from config.identity import DBUS_INTERFACE, DBUS_NAME, DBUS_PATH, SLUG

log = logging.getLogger(SLUG)

PLUGIN_NAME = "asus-turnpad-active-window"
KWIN = "org.kde.KWin"
SCRIPTING_PATH = "/Scripting"
SCRIPTING_INTERFACE = "org.kde.kwin.Scripting"

SCRIPT = """// Written by Turnpad: reports the active window's application and title to the driver.
var current = null;
function send(window) {
    var id = "";
    var caption = "";
    if (window) {
        id = window.desktopFileName || window.resourceClass || "";
        caption = window.caption || "";
    }
    callDBus(%(name)s, %(path)s, %(interface)s, "SetActiveWindow", String(id), String(caption));
}
function captionChanged() {
    send(current);
}
function report(window) {
    // The title of the window that has the focus now is the one to follow.
    if (current && current.captionChanged) {
        try {
            current.captionChanged.disconnect(captionChanged);
        } catch (error) {
        }
    }
    current = window || null;
    if (current && current.captionChanged) {
        current.captionChanged.connect(captionChanged);
    }
    send(current);
}
// Plasma 6 names; Plasma 5 had clientActivated and activeClient.
var activated = workspace.windowActivated || workspace.clientActivated;
if (activated) {
    activated.connect(report);
}
report(workspace.activeWindow || workspace.activeClient);
"""


def script_text(name: str = DBUS_NAME, path: str = DBUS_PATH, interface: str = DBUS_INTERFACE) -> str:
    """The KWin script, with the driver's D-Bus address as JavaScript strings."""
    import json

    return SCRIPT % {"name": json.dumps(name), "path": json.dumps(path), "interface": json.dumps(interface)}


def is_kde(environ=None) -> bool:
    """XDG_CURRENT_DESKTOP lists KDE (it is a colon-separated list)."""
    environ = os.environ if environ is None else environ
    return "KDE" in environ.get("XDG_CURRENT_DESKTOP", "").upper().split(":")


def script_path(environ=None) -> Path | None:
    """$XDG_RUNTIME_DIR/asus-turnpad/kwin-active-window.js; None without a runtime
    dir (the runtime dir is private to the user, a predictable place under /tmp
    would let another user plant the script KWin runs)."""
    environ = os.environ if environ is None else environ
    runtime = environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        return None
    return Path(runtime) / SLUG / "kwin-active-window.js"


def private_directory(directory: Path) -> None:
    """Create ``directory`` (0700) and refuse one that is not ours alone."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    status = directory.stat()
    if status.st_uid != os.getuid() or status.st_mode & 0o022 or directory.is_symlink():
        raise PermissionError(f"{directory} is not a private directory of this user")


class KWinScript:
    """Loads and unloads the script through ``bus.call(service, path, interface, method, args)``,
    which returns the reply's values as a tuple and raises OSError (or anything) on failure."""

    def __init__(self, bus, path: Path | None = None, environ=None):
        self.bus = bus
        self.path = Path(path) if path is not None else script_path(environ)
        if self.path is None:
            raise ValueError("no XDG_RUNTIME_DIR to keep the script in")
        self.script_id: int | None = None

    def _scripting(self, method: str, *args):
        return self.bus.call(KWIN, SCRIPTING_PATH, SCRIPTING_INTERFACE, method, args)

    def load(self) -> int:
        """Write the script, drop an older instance, load and start it; returns the script id."""
        private_directory(self.path.parent)
        self.path.write_text(script_text(), encoding="utf-8")
        # A previous driver may have been killed before it unloaded its script.
        self._scripting("unloadScript", PLUGIN_NAME)
        reply = self._scripting("loadScript", str(self.path), PLUGIN_NAME)
        self.script_id = int(reply[0]) if reply else -1
        if self.script_id < 0:
            self.script_id = None
            self._forget_file()
            raise RuntimeError("KWin did not load the script")
        try:
            self._scripting("start")
        except Exception:
            # Loaded but not running: do not leave it behind.
            self.unload()
            raise
        log.info("KWin script %s loaded (id %d)", PLUGIN_NAME, self.script_id)
        return self.script_id

    def unload(self) -> None:
        if self.script_id is None:
            return
        self.script_id = None
        try:
            self._scripting("unloadScript", PLUGIN_NAME)
        finally:
            self._forget_file()

    def _forget_file(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


class GioBus:
    """The session bus for ``KWinScript``, on a connection of its own."""

    def call(self, service, path, interface, method, args):
        from gi.repository import Gio, GLib

        from desktop.active_window import _session_connection

        signature = {"unloadScript": "(s)", "loadScript": "(ss)", "start": "()"}[method]
        reply = _session_connection().call_sync(
            service, path, interface, method, GLib.Variant(signature, tuple(args)), None,
            Gio.DBusCallFlags.NONE, 3000, None,
        )
        return tuple(reply.unpack())
