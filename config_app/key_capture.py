"""Listening for the next key or mouse button press on a mouse's evdev nodes.

The toggle key capture of the Driver page uses it to see the key exactly as the
driver does (a side button, a G-key that sends KEY_3). It only reads: the
devices are never grabbed, so the desktop keeps getting everything. Needs
python-libevdev and read access to /dev/input (the ``input`` group); without
them ``start()`` opens nothing and the dialog falls back to the keys and
buttons GTK reports.
"""

import os

from gi.repository import GLib

from config_app.hardware import key_event_name

try:
    import libevdev
except ImportError:  # pragma: no cover - the driver needs it too
    libevdev = None


def open_node(node):
    """(file, device) of "/dev/input/<node>" opened for reading without blocking."""
    handle = open("/dev/input/" + node, "rb")
    try:
        os.set_blocking(handle.fileno(), False)
        return handle, libevdev.Device(handle)
    except Exception:
        handle.close()
        raise


class EvdevWatcher:
    """Calls ``on_key(name)`` in the main loop for the first key or button
    pressed on any of the ``nodes``. ``opener(node)`` returns (file, device);
    the device has ``events()`` yielding evdev events (tests give their own;
    the default is looked up when ``start()`` runs)."""

    def __init__(self, nodes, on_key, opener=None):
        self.nodes = list(nodes)
        self.on_key = on_key
        self.opener = opener
        self._open = []
        self._done = False

    def start(self):
        """Open the nodes that can be read; returns how many."""
        opener = self.opener or open_node
        if libevdev is None and opener is open_node:
            return 0
        for node in self.nodes:
            try:
                handle, device = opener(node)
            except (OSError, ValueError, AttributeError):
                continue
            entry = {"handle": handle, "device": device, "watch": 0}
            entry["watch"] = GLib.io_add_watch(
                handle.fileno(), GLib.PRIORITY_DEFAULT,
                GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR,
                self._readable, entry,
            )
            self._open.append(entry)
        return len(self._open)

    @property
    def count(self):
        return len(self._open)

    def _readable(self, _fd, condition, entry):
        try:
            if condition & (GLib.IOCondition.HUP | GLib.IOCondition.ERR):
                raise OSError("the device went away")
            for event in entry["device"].events():
                name = key_event_name(event.type.value, event.code.value, event.value)
                if name is not None and not self._done:
                    self._done = True
                    # This source ends by returning SOURCE_REMOVE.
                    entry["watch"] = 0
                    self.stop()
                    self.on_key(name)
                    return GLib.SOURCE_REMOVE
        except OSError:
            self._close(entry, remove_watch=False)
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _close(self, entry, remove_watch=True):
        if entry in self._open:
            self._open.remove(entry)
        if remove_watch and entry["watch"]:
            GLib.source_remove(entry["watch"])
        entry["watch"] = 0
        try:
            entry["handle"].close()
        except OSError:
            pass

    def stop(self):
        for entry in list(self._open):
            self._close(entry)
