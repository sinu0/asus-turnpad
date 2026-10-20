"""SpaceMouse page helpers of the settings app, free of GTK so they are unit tested.

The page edits two maps of ``index (as text) -> SpaceMouseButton``: the global
one (``config.spacemouse.buttons``, empty = the driver's defaults) and the one
of a profile (``Profile.spacemouse_buttons``). A profile's entry replaces the
global entry of the same button as a whole, as the driver does.
"""

import copy
import os
import threading
from dataclasses import dataclass

from gi.repository import GLib

from config.labels import describe_macro, keys_to_text
from config.schema import (
    SPACEMOUSE_ACTION_TYPES,
    SPACEMOUSE_BUTTONS,
    SpaceMouseAction,
    SpaceMouseButton,
    default_spacemouse_buttons,
    merge_spacemouse_buttons,
    spacemouse_buttons_for,
)
from spacemouse_devices import (
    GROUP_LABELS,
    GROUPS,
    SPACEPILOT_PRO,
    Key,
    Model,
    generic_model,
    key_for_index,
    model_for_name,
)
from spacenav_client import EVMASK_RAWBUTTON, UEV_PRESS, UEV_RAWBUTTON, SpacenavClient, socket_candidates

# Buttons listed when no device says how many it has (the SpacePilot Pro's).
DEFAULT_BUTTON_COUNT = 31
# Group of the buttons the model does not know (raw "Button N" rows).
OTHER_GROUP = "other"

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
    ("layer", "Switch layer"),
    ("motion", "Cap motion"),
    ("app", "App command"),
)
CHOICE_LABELS = dict(ACTION_CHOICES)
# Modes of a layer action (SpaceMouseAction.layer_mode): (key, label, one line of explanation).
LAYER_MODES = (
    ("hold", "Hold", "The layer is on while this button is held; other buttons can be pressed meanwhile"),
    ("latch", "Once", "The next press of another button uses the layer, then it ends"),
    ("toggle", "Toggle", "The layer stays on until this button is pressed again"),
)
LAYER_MODE_LABELS = {key: label for key, label, _text in LAYER_MODES}
# The cap-motion actions (SpaceMouseAction.motion): (key, label).
MOTION_CHOICES = (
    ("rotation", "Rotation on / off"),
    ("translation", "Pan and zoom on / off"),
    ("faster", "Faster"),
    ("slower", "Slower"),
    ("reset", "Reset"),
)
MOTION_LABELS = dict(MOTION_CHOICES)
# A modifier cannot be held for a hold action: it is held while the button is down.
PRESS_ONLY = ("Ctrl", "Shift", "Alt")


def supported_choices():
    """ACTION_CHOICES the schema understands (an older schema lacks hold keys and pass through)."""
    return tuple(
        (key, label) for key, label in ACTION_CHOICES
        if key == "none" or ("modifier" if key in PRESS_ONLY else key) in SPACEMOUSE_ACTION_TYPES
    )


def model_for_device(device_name, count=DEFAULT_BUTTON_COUNT):
    """The model the button list follows: the device's; the SpacePilot Pro (the
    one the default map is for) while no device name is known; a generic one,
    "Button N" per index, for a device of another make."""
    if not str(device_name or "").strip():
        return SPACEPILOT_PRO
    return model_for_name(device_name) or generic_model(count)


def button_name(index, model=SPACEPILOT_PRO):
    """"Top view", "Function key 1 (long)"; "Button N" when the model has no such key."""
    found = key_for_index(model, index) if model is not None else None
    if found is None:
        return f"Button {index}"
    key, is_long = found
    return (key.long_name or f"{key.name} (long)") if is_long else key.name


@dataclass(frozen=True)
class KeyRow:
    """One row of the button list: a physical key (``key``) with one raw index,
    or two when the device itself tells a short from a long press; a button the
    model does not know has no ``key``."""

    group: str
    title: str
    label: str
    indexes: tuple
    key: Key | None = None

    @property
    def dual(self):
        return len(self.indexes) == 2


def group_title(model, group):
    if group == OTHER_GROUP:
        return "Other buttons"
    return "Buttons" if model.id == "generic" else GROUP_LABELS.get(group, group.title())


def key_rows(model, buttons_maps=(), extra=()):
    """The rows of the button list, grouped in the order of GROUPS: the model's
    keys, then "Button N" rows for raw indexes outside the model that have a
    binding in ``buttons_maps`` or are in ``extra`` (a pressed one)."""
    rows = [
        KeyRow(key.group, key.name, key.label, (key.index,) if key.long_index is None else (key.index, key.long_index), key)
        for key in model.keys
    ]
    rows.sort(key=lambda row: GROUPS.index(row.group) if row.group in GROUPS else len(GROUPS))
    known = {index for row in rows for index in row.indexes}
    unknown = {int(key) for buttons in buttons_maps for key in buttons if str(key).isdigit()}
    unknown |= {index for index in extra if isinstance(index, int)}
    for index in sorted(unknown - known):
        if 0 <= index < SPACEMOUSE_BUTTONS:
            rows.append(KeyRow(OTHER_GROUP, f"Button {index}", str(index), (index,)))
    return rows


def row_for_index(rows, index):
    return next((row for row in rows if index in row.indexes), None)


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


def new_action(choice, previous=None, layers=()):
    """The action for a dropdown choice, keeping what fits from the previous one;
    a new layer action takes the first of ``layers`` (none yet: it has no layer)."""
    if choice == "none":
        return None
    if choice == "layer":
        same = previous is not None and previous.type == "layer"
        return SpaceMouseAction(
            "layer", layer=previous.layer if same else (layers[0] if layers else None),
            layer_mode=(previous.layer_mode or "hold") if same else "hold",
        )
    if choice == "motion":
        same = previous is not None and previous.type == "motion"
        return SpaceMouseAction("motion", motion=previous.motion if same else "rotation")
    if choice == "app":
        same = previous is not None and previous.type == "app"
        keys = list(previous.keys) if previous is not None and previous.keys and previous.type in ("macro", "key_hold", "app") else None
        return SpaceMouseAction("app", command=previous.command if same else None, keys=keys)
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
    """Whether the schema keeps the action: a key_hold without keys, a layer
    without a name and an app command without an id are dropped on load."""
    if action is None:
        return True
    if action.type == "key_hold":
        return bool(action.keys)
    if action.type == "layer":
        return bool((action.layer or "").strip())
    if action.type == "app":
        return bool((action.command or "").strip())
    return True


def describe_action(action, titles=None):
    """A short text for a row: "Ctrl+Tab", "Hold Ctrl+Tab", "Group: Modeling", "Dial".
    ``titles`` (command id -> title) names app commands the application's list knows."""
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
    if action.type == "layer":
        if not action.layer:
            return "Layer: not set"
        mode = {"latch": "once", "toggle": "toggle"}.get(action.layer_mode or "hold", "hold")
        return f"Layer: {action.layer} ({mode})"
    if action.type == "motion":
        return f"Cap: {MOTION_LABELS.get(action.motion, action.motion or 'not set').lower()}"
    if action.type == "app":
        if not action.command:
            return "App command not set"
        text = f"App: {(titles or {}).get(action.command) or action.command}"
        return f"{text} ({keys_to_text(action.keys)})" if action.keys else text
    return action.type


def describe_button(button, use_label=True, titles=None):
    """"Press: Dial · Hold: Group: Modeling"; a lone press is just its text. The
    button's own label (the text for a device screen) replaces it."""
    if use_label and button is not None and button.label:
        return button.label
    if button is None or (button.press is None and button.hold is None):
        return "Pass through"
    if button.hold is None:
        return describe_action(button.press, titles)
    press = describe_action(button.press, titles)
    hold = describe_action(button.hold, titles)
    return f"{press} · Hold: {hold}" if button.press else f"Hold: {hold}"


def global_buttons(config):
    """The global map as the driver reads it (the defaults while none has an action;
    label-only entries, names for the screen, are laid over either)."""
    return spacemouse_buttons_for(config)


def editable_global(config):
    """The global map for editing: the defaults are stored on the first edit so
    they stay when one button changes."""
    stored = config.spacemouse.buttons
    if not any(button.press is not None or button.hold is not None for button in stored.values()):
        config.spacemouse.buttons = merge_spacemouse_buttons(copy.deepcopy(default_spacemouse_buttons()), stored)
    return config.spacemouse.buttons


def binding(config, profile, index):
    """(button, source) the driver runs for ``index`` under ``profile``: source is
    "profile", "global" or None (nothing bound)."""
    key = str(index)
    button = global_buttons(config).get(key)
    parent = getattr(profile, "parent", None)
    if parent is not None:
        # A profile's context (contexts.ContextScope): what it leaves alone is the
        # profile's binding, which in turn falls back to All applications.
        button, _source = binding(config, parent, index)
        if key not in profile.spacemouse_buttons:
            return button, ("global" if button is not None else None)
    if profile is not None and key in profile.spacemouse_buttons:
        own = profile.spacemouse_buttons[key]
        if own.press is None and own.hold is None and button is not None:
            # A label only: the actions of all applications stay.
            return SpaceMouseButton(press=button.press, hold=button.hold, label=own.label or button.label), "global"
        return own, "profile"
    return button, ("global" if button is not None else None)


def store(buttons, index, button):
    """Put ``button`` (a copy, its label too) in the map; one without actions passes through:
    an explicit "none" press keeps the entry, so a profile's override survives
    and an emptied global map does not bring the defaults back."""
    key = str(index)
    if button is None:
        buttons.pop(key, None)
        return
    press = button.press
    if press is None and button.hold is None:
        press = SpaceMouseAction("none")
    buttons[key] = SpaceMouseButton(press=press, hold=button.hold, label=button.label)


def store_global(buttons, index, button):
    """Like store(), but a global button without actions is simply left out
    (unless that empties the map: the defaults would come back)."""
    key = str(index)
    if button is not None and button.press is None and button.hold is None and not button.label:
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
