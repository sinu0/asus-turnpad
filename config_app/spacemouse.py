"""SpaceMouse page helpers of the settings app, free of GTK so they are unit tested.

The page edits two maps of ``index (as text) -> SpaceMouseButton``: the global
one (``config.spacemouse.buttons``, empty = the driver's defaults) and the one
of a profile (``Profile.spacemouse_buttons``). A profile's entry replaces the
global entry of the same button as a whole, as the driver does.
"""

import copy
import os
import threading

from gi.repository import GLib

from config.labels import describe_macro, keys_to_text
from config.schema import (
    SPACEMOUSE_ACTION_TYPES,
    SPACEMOUSE_BUTTONS,
    SpaceMouseAction,
    SpaceMouseButton,
    default_spacemouse_buttons,
)
from spacenav_client import EVMASK_RAWBUTTON, UEV_PRESS, UEV_RAWBUTTON, SpacenavClient, socket_candidates

# Buttons of the SpacePilot Pro; raw indexes as spacenavd reports them. Some
# labels are guessed: correct them here.
DEFAULT_BUTTON_COUNT = 31
BUTTON_NAMES = {
    0: "Menu", 1: "Fit", 2: "Top", 3: "Left", 4: "Right", 5: "Front", 6: "Bottom", 7: "Back",
    8: "Roll ⟳", 9: "Roll ⟲", 10: "ISO 1", 11: "ISO 2",
    **{12 + number: str(number + 1) for number in range(10)},
    22: "Esc", 23: "Alt", 24: "Shift", 25: "Ctrl", 26: "Rotation lock", 27: "Pan/Zoom",
    28: "Dominant", 29: "+", 30: "−",
}

# The raw axes of the cap (spacenavd): 0 1 2 = X Y Z, 3 4 5 = RX RY RZ.
AXIS_LABELS = ("0 · X", "1 · Y", "2 · Z", "3 · RX", "4 · RY", "5 · RZ", "6", "7")

# (key, label) of the action dropdown, in this order. "none" is a pass-through
# to other applications; the three modifiers are one schema type.
ACTION_CHOICES = (
    ("none", "Pass through (nothing)"),
    ("macro", "Keys or macro"),
    ("key_hold", "Hold keys"),
    ("Ctrl", "Hold Ctrl"),
    ("Shift", "Hold Shift"),
    ("Alt", "Hold Alt"),
    ("dial", "Open the dial"),
    ("group", "Open a group"),
    ("function", "Go to a function"),
    ("select", "Select"),
    ("back", "Back"),
)
CHOICE_LABELS = dict(ACTION_CHOICES)
# A modifier cannot be held for a hold action: it is held while the button is down.
PRESS_ONLY = ("Ctrl", "Shift", "Alt")


def supported_choices():
    """ACTION_CHOICES the schema understands (an older schema lacks hold keys and pass through)."""
    return tuple(
        (key, label) for key, label in ACTION_CHOICES
        if key == "none" or ("modifier" if key in PRESS_ONLY else key) in SPACEMOUSE_ACTION_TYPES
    )


def button_name(index):
    return BUTTON_NAMES.get(index, f"Button {index}")


def button_count(buttons_maps=(), reported=None):
    """How many buttons to list: the device's, at least those with a binding."""
    count = reported if isinstance(reported, int) and 0 < reported <= SPACEMOUSE_BUTTONS else DEFAULT_BUTTON_COUNT
    for buttons in buttons_maps:
        for key in buttons:
            if str(key).isdigit():
                count = max(count, int(key) + 1)
    return min(count, SPACEMOUSE_BUTTONS)


def choice_of(action):
    """The dropdown key of an action; an unset one passes through."""
    if action is None:
        return "none"
    if action.type == "modifier":
        return action.modifier if action.modifier in PRESS_ONLY else "Ctrl"
    return action.type if action.type in CHOICE_LABELS else "none"


def new_action(choice, previous=None):
    """The action for a dropdown choice, keeping what fits from the previous one."""
    if choice == "none":
        return None
    if choice in PRESS_ONLY:
        return SpaceMouseAction("modifier", modifier=choice)
    if choice in ("macro", "key_hold"):
        keys = list(previous.keys) if previous is not None and previous.keys else None
        macro = list(previous.macro) if choice == "macro" and previous is not None and previous.macro else None
        return SpaceMouseAction(choice, keys=keys, macro=macro)
    if choice in ("group", "function"):
        same = previous is not None and previous.type == choice
        return SpaceMouseAction(
            choice,
            group=previous.group if same and choice == "group" else None,
            function=previous.function if same and choice == "function" else None,
            slot=previous.slot if same and choice == "function" else None,
        )
    return SpaceMouseAction(choice)


def is_valid(action):
    """Whether the schema keeps the action: a key_hold without keys is dropped on load."""
    return action is None or action.type != "key_hold" or bool(action.keys)


def describe_action(action):
    """A short text for a row: "Ctrl+Tab", "Hold Ctrl+Tab", "Group: Modeling", "Dial"."""
    if action is None or action.type == "none":
        return "Pass through"
    if action.type == "macro":
        if action.macro:
            text = describe_macro(action.macro) or "Macro"
            return text if len(action.macro) == 1 else f"{text} …"
        return keys_to_text(action.keys) if action.keys else "Keys not set"
    if action.type == "key_hold":
        return f"Hold {keys_to_text(action.keys)}" if action.keys else "Hold keys not set"
    if action.type == "modifier":
        return f"Hold {action.modifier or 'a modifier'}"
    if action.type == "dial":
        return "Dial"
    if action.type == "group":
        return f"Group: {action.group}" if action.group else "Group: first"
    if action.type == "function":
        if action.function:
            return f"Function: {action.function}"
        return f"Function: #{action.slot + 1}" if action.slot is not None else "Function: not set"
    if action.type == "select":
        return "Select"
    if action.type == "back":
        return "Back"
    return action.type


def describe_button(button):
    """"Press: Dial · Hold: Group: Modeling"; a lone press is just its text."""
    if button is None or (button.press is None and button.hold is None):
        return "Pass through"
    if button.hold is None:
        return describe_action(button.press)
    press = describe_action(button.press)
    return f"{press} · Hold: {describe_action(button.hold)}" if button.press else f"Hold: {describe_action(button.hold)}"


def global_buttons(config):
    """The global map as the driver reads it (the defaults while none is stored)."""
    return config.spacemouse.buttons or default_spacemouse_buttons()


def editable_global(config):
    """The global map for editing: the defaults are stored on the first edit so
    they stay when one button changes."""
    if not config.spacemouse.buttons:
        config.spacemouse.buttons = copy.deepcopy(default_spacemouse_buttons())
    return config.spacemouse.buttons


def binding(config, profile, index):
    """(button, source) the driver runs for ``index`` under ``profile``: source is
    "profile", "global" or None (nothing bound)."""
    key = str(index)
    if profile is not None and key in profile.spacemouse_buttons:
        return profile.spacemouse_buttons[key], "profile"
    button = global_buttons(config).get(key)
    return button, ("global" if button is not None else None)


def store(buttons, index, button):
    """Put ``button`` (a copy) in the map; one without actions passes through:
    an explicit "none" press keeps the entry, so a profile's override survives
    and an emptied global map does not bring the defaults back."""
    key = str(index)
    if button is None:
        buttons.pop(key, None)
        return
    if button.press is None and button.hold is None:
        button = SpaceMouseButton(press=SpaceMouseAction("none"))
    buttons[key] = SpaceMouseButton(press=button.press, hold=button.hold)


def store_global(buttons, index, button):
    """Like store(), but a global button without actions is simply left out
    (unless that empties the map: the defaults would come back)."""
    key = str(index)
    if button is not None and button.press is None and button.hold is None:
        buttons.pop(key, None)
        if not buttons:
            buttons[key] = SpaceMouseButton(press=SpaceMouseAction("none"))
        return
    store(buttons, index, button)


def reset_global_button(config, index):
    """Back to the default of one button (nothing when it has none)."""
    buttons = editable_global(config)
    default = default_spacemouse_buttons().get(str(index))
    store_global(buttons, index, copy.deepcopy(default) if default is not None else SpaceMouseButton())


def group_titles(profiles):
    """Titles of the groups of ``profiles``, in order, without repeats."""
    seen = []
    for profile in profiles:
        for segment in profile.segments:
            if segment.is_group and segment.title not in seen:
                seen.append(segment.title)
    return seen


def function_titles(profiles):
    """Titles of the functions of ``profiles`` (menu entries and group items)."""
    seen = []
    for profile in profiles:
        for segment in profile.segments:
            for function in (segment.items or []) if segment.is_group else [segment]:
                if function.title not in seen:
                    seen.append(function.title)
    return seen


def socket_exists(configured=""):
    """Whether a spacenavd socket is there (the daemon is running)."""
    try:
        with open("/etc/spnavrc", "r", encoding="utf-8") as rc:
            spnavrc = rc.read()
    except OSError:
        spnavrc = ""
    return any(os.path.exists(path) for path in socket_candidates(configured, os.environ, spnavrc))


class ButtonProbe:
    """Tells which SpaceMouse button is pressed, by listening to spacenavd as a
    client of its own (raw buttons, whatever the daemon's mapping is). The
    connection is made in a thread (the handshake can take over a second); the
    watching runs in the GLib main loop. ``stop()`` closes the connection."""

    def __init__(self, socket_path, on_button):
        self.socket_path = socket_path
        self.on_button = on_button
        self.client = SpacenavClient()
        self.watch = 0
        self.connecting = False
        self.cancelled = False

    @property
    def running(self):
        return self.client.connected

    def start(self, on_done):
        """Connect in the background; ``on_done(ok)`` runs in the main loop
        (never after ``stop()``). ok is False when spacenavd cannot be reached."""
        self.connecting = True
        self.cancelled = False
        threading.Thread(target=self._connect, args=(on_done,), daemon=True).start()

    def _connect(self, on_done):
        ok = False
        try:
            with open("/etc/spnavrc", "r", encoding="utf-8") as rc:
                spnavrc = rc.read()
        except OSError:
            spnavrc = ""
        try:
            if self.client.connect(socket_candidates(self.socket_path, os.environ, spnavrc)):
                if self.client.proto >= 1:
                    self.client.send_name("Turnpad settings")
                    self.client.set_evmask(EVMASK_RAWBUTTON)
                ok = True
        except OSError:
            ok = False
        GLib.idle_add(self._connected, ok, on_done)

    def _connected(self, ok, on_done):
        self.connecting = False
        if self.cancelled or not ok:
            self.client.close()
            if not self.cancelled:
                on_done(False)
            return GLib.SOURCE_REMOVE
        # Protocol 0 daemons send the mapped buttons (UEV_PRESS) instead.
        self.watch = GLib.io_add_watch(
            self.client.fileno(), GLib.PRIORITY_DEFAULT, GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR,
            self._readable,
        )
        on_done(True)
        return GLib.SOURCE_REMOVE

    def _readable(self, _fd, condition):
        try:
            if condition & (GLib.IOCondition.HUP | GLib.IOCondition.ERR):
                raise ConnectionError("spacenavd went away")
            events = self.client.read_events()
        except OSError:
            self.watch = 0
            self.client.close()
            self.on_button(None)
            return GLib.SOURCE_REMOVE
        proto0 = self.client.proto < 1
        for event in events:
            if event.type == UEV_RAWBUTTON or (proto0 and event.type == UEV_PRESS):
                if event.type == UEV_PRESS or event.data[1]:
                    self.on_button(event.data[0])
        return GLib.SOURCE_CONTINUE

    def stop(self):
        self.cancelled = True
        if self.watch:
            GLib.source_remove(self.watch)
            self.watch = 0
        # While the thread connects it owns the socket; _connected() closes it.
        if not self.connecting:
            self.client.close()
