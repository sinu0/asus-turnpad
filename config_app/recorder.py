"""Recording a macro from the keyboard: the settings app's "Record…" button.

The conversion is pure (``convert()``, unit tested): raw key events of
``(name, value, time)`` with value 1 = down, 0 = up, 2 = auto-repeat become the
macro steps the driver runs. ``keyboard_nodes()`` picks the event nodes of the
real keyboards out of /proc/bus/input/devices and ``KeyReader`` reads them
without grabbing (every key still reaches the desktop). Without access to
/dev/input the dialog records the keys GTK reports (``gtk_event()``).
"""

import os
import time

from config.labels import MODIFIER_KEYS, order_keys
from config.schema import MacroStep, StepType
from config_app import hardware as hw
from mouse_dial import LONG_BITS, has_bit

try:
    import libevdev
except ImportError:  # pragma: no cover - the driver needs it too
    libevdev = None

# A pause longer than this between two key presses becomes a Wait step.
PAUSE_THRESHOLD = 0.25
# The longest Wait the action editor offers.
PAUSE_MAX = 10.0
# Recording stops by itself after this long without a key.
IDLE_STOP_SECONDS = 10

# evdev codes of the 26 letters: a keyboard has all of them.
LETTER_CODES = tuple([*range(16, 26), *range(30, 39), *range(44, 51)])


def is_keyboard(device, word_bits=LONG_BITS):
    """Whether ``device`` (hardware.InputDevice) is a real keyboard: it has every
    letter key and is not one of the driver's own virtual devices."""
    if any(word in device.name for word in hw.OWN_DEVICE_WORDS):
        return False
    return all(has_bit(device.key_mask, code, word_bits) for code in LETTER_CODES)


def keyboard_nodes(devices_text, word_bits=LONG_BITS):
    """Event nodes ("event3") of the keyboards listed in /proc/bus/input/devices."""
    nodes = []
    for device in hw.parse_input_devices(devices_text):
        if is_keyboard(device, word_bits) and device.node not in nodes:
            nodes.append(device.node)
    return nodes


def convert(events, keep_pauses=False, pause_threshold=PAUSE_THRESHOLD):
    """Macro steps for key ``events`` of (evdev name, value, seconds).

    A key going down with modifiers held is one step [modifiers…, key]; the
    modifiers stay in the order they were pressed. Auto-repeat, a key that is
    already down, modifiers on their own and anything that is not a keyboard
    key (BTN_…) are ignored. With ``keep_pauses`` a gap longer than
    ``pause_threshold`` between the end of one key (its release) and the next
    press becomes a Wait step (at most PAUSE_MAX seconds).
    """
    steps = []
    down = set()
    modifiers = []
    last_key = None
    last_end = None
    for name, value, when in events:
        if not isinstance(name, str) or not name.startswith("KEY_") or name == "KEY_RESERVED":
            continue
        if value == 2:
            continue
        if value == 0:
            down.discard(name)
            if name in modifiers:
                modifiers.remove(name)
            if name == last_key:
                last_end = when
            continue
        if value != 1 or name in down:
            continue
        down.add(name)
        if name in MODIFIER_KEYS:
            modifiers.append(name)
            continue
        if keep_pauses and steps and last_end is not None and when - last_end > pause_threshold:
            steps.append(MacroStep(type=StepType.DELAY, duration=round(min(when - last_end, PAUSE_MAX), 2)))
        steps.append(MacroStep(type=StepType.KEYSTROKE, keys=order_keys([*modifiers, name])))
        last_key = name
        last_end = when
    return steps


def gtk_event(keycode, pressed, when=None):
    """(name, value, time) of a GTK key event, or None for a key evdev has no name for."""
    name = hw.gtk_key_name(keycode)
    if name is None:
        return None
    return name, 1 if pressed else 0, time.monotonic() if when is None else when


class Recording:
    """The events of one recording and the steps they make."""

    def __init__(self):
        self.events = []
        self.last_event = time.monotonic()

    def add(self, name, value, when=None):
        self.events.append((name, value, time.monotonic() if when is None else when))
        if value == 1:
            self.last_event = time.monotonic()

    def steps(self, keep_pauses=False):
        return convert(self.events, keep_pauses)

    def idle(self, now=None):
        """Seconds since the last key went down (or since the recording began)."""
        return (time.monotonic() if now is None else now) - self.last_event


def open_node(node):
    """(file, device) of "/dev/input/<node>" opened for reading without blocking."""
    handle = open("/dev/input/" + node, "rb")
    try:
        os.set_blocking(handle.fileno(), False)
        return handle, libevdev.Device(handle)
    except Exception:
        handle.close()
        raise


class KeyReader:
    """Reads every key event of the keyboard ``nodes`` in the GLib main loop and
    calls ``on_event(name, value, seconds)``. Never grabs a device. ``opener(node)``
    returns (file, device) as ``open_node`` does (tests give their own)."""

    def __init__(self, nodes, on_event, opener=None):
        self.nodes = list(nodes)
        self.on_event = on_event
        self.opener = opener
        self._open = []

    @property
    def count(self):
        return len(self._open)

    def start(self):
        """Open the nodes that can be read; returns how many."""
        from gi.repository import GLib

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

    def _readable(self, _fd, condition, entry):
        from gi.repository import GLib

        try:
            if condition & (GLib.IOCondition.HUP | GLib.IOCondition.ERR):
                raise OSError("the device went away")
            for event in entry["device"].events():
                if event.type.value != hw.EV_KEY:
                    continue
                name = hw.code_name(event.code.value)
                if name is None:
                    continue
                seconds = getattr(event, "sec", 0) + getattr(event, "usec", 0) / 1e6
                self.on_event(name, event.value, seconds or time.monotonic())
        except OSError:
            entry["watch"] = 0
            self._close(entry)
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _close(self, entry):
        from gi.repository import GLib

        if entry in self._open:
            self._open.remove(entry)
        if entry["watch"]:
            GLib.source_remove(entry["watch"])
            entry["watch"] = 0
        try:
            entry["handle"].close()
        except OSError:
            pass

    def stop(self):
        for entry in list(self._open):
            self._close(entry)
