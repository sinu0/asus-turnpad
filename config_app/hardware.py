"""What hardware this computer has, for the settings app: free of GTK so it is unit tested.

``detect()`` looks at /proc/bus/input/devices, sysfs (USB, DMI) and the
spacenavd socket and says which of the dial's modes can work here:
DialPad mode, Touchpad mode, Mouse-only mode, SpaceMouse mode. Every input of
``detect()`` can be replaced (texts, paths), so tests run without the hardware.
The setup assistant, the page visibility, the mouse picker and the key capture
of the Driver page build on it. Also here: the app-only preferences file.
"""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from config.labels import EVDEV_CODE_NAMES, format_keys
from config.schema import CONFIG_DIR
from layout_detect import TABLE, choose_layout, is_touchpad, touchpad_ids
from mouse_dial import LONG_BITS, REL_WHEEL, find_mouse, has_bit
from spacemouse_devices import model_for_usb, screens_present, spacemice_present
from spacenav_client import socket_candidates

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# The driver's own virtual devices are named "... DialPad"; never offered as a mouse.
OWN_DEVICE_WORDS = ("DialPad", "Turnpad")
# Touchpad names the driver takes for a DialPad (dialpad.py): the prefixes of
# ASUS's (and ELAN's) touchpads; the two product ids are other devices.
DIALPAD_PREFIXES = ("ASUE", "ELAN", "ASUP", "ASUF", "ASCE", "ASCF", "ASCP")
DIALPAD_EXCLUDED = ("9009", "9008")
REL_X, REL_Y = 0, 1

MODE_DIALPAD, MODE_TOUCHPAD, MODE_MOUSE = "dialpad", "touchpad", "mouse"
MAIN_MODES = (MODE_DIALPAD, MODE_TOUCHPAD, MODE_MOUSE)


# --- /proc/bus/input/devices -----------------------------------------------------------

@dataclass(frozen=True)
class InputDevice:
    name: str
    node: str
    vendor: str = ""
    product: str = ""
    key_mask: str = ""
    rel_mask: str = ""


def parse_input_devices(text):
    """The devices of /proc/bus/input/devices that have an event node."""
    devices = []
    for block in re.split(r"\n\s*\n", text or ""):
        name = vendor = product = handlers = key_mask = rel_mask = ""
        for line in block.splitlines():
            if line.startswith("N: Name="):
                name = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("I: "):
                found = re.search(r"Vendor=([0-9a-fA-F]+) Product=([0-9a-fA-F]+)", line)
                if found:
                    vendor, product = found.group(1).lower(), found.group(2).lower()
            elif line.startswith("H: Handlers="):
                handlers = line.split("=", 1)[1]
            elif line.startswith("B: KEY="):
                key_mask = line.split("=", 1)[1]
            elif line.startswith("B: REL="):
                rel_mask = line.split("=", 1)[1]
        node = re.search(r"\bevent\d+\b", handlers)
        if node and name:
            devices.append(InputDevice(name, node.group(0), vendor, product, key_mask, rel_mask))
    return devices


def _own_device(name):
    return any(word in name for word in OWN_DEVICE_WORDS)


# --- Mice ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Mouse:
    """A pointer with a wheel. ``name`` is the device's name as the kernel gives
    it (what ``mouse.device`` stores and the driver searches for), ``label``
    the same with the padding spaces collapsed; ``nodes`` its event nodes (a
    gaming mouse has several with one name)."""

    name: str
    label: str
    nodes: tuple = ()


def list_mice(text, word_bits=LONG_BITS):
    """Every pointer with a wheel in /proc/bus/input/devices (``text``): relative
    X and Y plus REL_WHEEL. Same names are one entry; the driver's own virtual
    devices are left out."""
    found = {}
    for device in parse_input_devices(text):
        if _own_device(device.name):
            continue
        if not all(has_bit(device.rel_mask, bit, word_bits) for bit in (REL_X, REL_Y, REL_WHEEL)):
            continue
        found.setdefault(device.name, []).append(device.node)
    return [Mouse(name, " ".join(name.split()), tuple(nodes)) for name, nodes in found.items()]


def device_matches(stored, name):
    """The driver's rule: ``stored`` is part of the device's name, any case; "" matches all."""
    return not stored or stored.lower() in name.lower()


def mouse_choices(mice, stored):
    """Entries for the mouse picker: ([label, ...], [value, ...], selected index).

    The first entry is "Automatic" (value ""). ``stored`` is the saved
    ``mouse.device``; it selects the first mouse it is part of the name of, and
    one that matches none stays selectable as "<value> (not connected)".
    """
    labels = ["Automatic (first mouse with this key and a wheel)"]
    values = [""]
    for mouse in mice:
        labels.append(mouse.label)
        values.append(mouse.name)
    stored = (stored or "").strip()
    selected = 0
    if stored:
        for index, value in enumerate(values[1:], 1):
            if device_matches(stored, value):
                selected = index
                break
        else:
            labels.append(f"{stored} (not connected)")
            values.append(stored)
            selected = len(values) - 1
    return labels, values, selected


def mouse_nodes(mice, stored):
    """Event nodes of the mice ``stored`` selects ("" = all of them)."""
    return [node for mouse in mice if device_matches(stored, mouse.name) for node in mouse.nodes]


# --- Keys and buttons ------------------------------------------------------------------------

BUTTON_NAMES = {
    0x110: "BTN_LEFT", 0x111: "BTN_RIGHT", 0x112: "BTN_MIDDLE", 0x113: "BTN_SIDE",
    0x114: "BTN_EXTRA", 0x115: "BTN_FORWARD", 0x116: "BTN_BACK", 0x117: "BTN_TASK",
}
BUTTON_LABELS = {
    "BTN_LEFT": "Left button", "BTN_RIGHT": "Right button", "BTN_MIDDLE": "Middle button",
    "BTN_SIDE": "Side button", "BTN_EXTRA": "Extra side button", "BTN_FORWARD": "Forward button",
    "BTN_BACK": "Back button", "BTN_TASK": "Task button",
}
# The left and right buttons are the dial's select and back while it is on.
REFUSED_BUTTONS = {
    "BTN_LEFT": "The left button selects while the dial is on: choose a side button, or a key the mouse sends",
    "BTN_RIGHT": "The right button goes back while the dial is on: choose a side button, or a key the mouse sends",
}
EV_KEY = 1

try:  # python-libevdev knows every code; the tables above cover the common ones
    import libevdev
except ImportError:  # pragma: no cover - the driver needs it, a test machine may not
    libevdev = None


def code_name(code):
    """The evdev name of a key or button code ("KEY_F13", "BTN_SIDE"), or None."""
    if libevdev is not None:
        try:
            name = libevdev.evbit(EV_KEY, int(code)).name
        except (AttributeError, ValueError, TypeError):
            name = None
        if name:
            return name
    return BUTTON_NAMES.get(code) or EVDEV_CODE_NAMES.get(code)


def code_for_name(name):
    """The evdev code of "KEY_F13"/"BTN_SIDE", or None."""
    if libevdev is not None:
        try:
            code = libevdev.evbit(name)
            if code is not None and code.type.value == EV_KEY:
                return code.value
        except (AttributeError, ValueError, TypeError):
            pass
    for table in (BUTTON_NAMES, EVDEV_CODE_NAMES):
        for code, known in table.items():
            if known == name:
                return code
    return None


def describe_key(name):
    """A key or button for people: "F13 · KEY_F13", "Side button · BTN_SIDE"."""
    if not name:
        return "Not set"
    label = BUTTON_LABELS.get(name)
    if label is None:
        label = format_keys([name]) if name.startswith("KEY_") else name.removeprefix("BTN_").replace("_", " ").title()
    return f"{label} · {name}"


def captured_key(name):
    """(name, hint) for something pressed while the toggle key is being chosen:
    the name when it can be the toggle key, else None and the reason."""
    if not name or not name.startswith(("KEY_", "BTN_")):
        return None, "That is not a key or a mouse button"
    if name in REFUSED_BUTTONS:
        return None, REFUSED_BUTTONS[name]
    return name, ""


def key_event_name(event_type, code, value):
    """The name of a key or button that was just pressed (not released, not
    repeated), from an evdev event; None for anything else."""
    if event_type != EV_KEY or value != 1:
        return None
    return code_name(code)


def gtk_key_name(hardware_keycode):
    """The evdev name of a GTK key press: hardware keycode - 8 is the evdev code."""
    code = int(hardware_keycode) - 8
    if code <= 0:
        return None
    name = code_name(code)
    return name if name and name.startswith("KEY_") else None


GTK_BUTTONS = {2: "BTN_MIDDLE", 8: "BTN_SIDE", 9: "BTN_EXTRA"}


def gtk_button_name(button):
    """(name, hint) of a GTK button press: middle, side and extra buttons are
    taken, a plain left or right click is refused with a hint."""
    if button in (1, 3):
        return None, REFUSED_BUTTONS["BTN_LEFT" if button == 1 else "BTN_RIGHT"]
    name = GTK_BUTTONS.get(button)
    return (name, "") if name else (None, f"Button {button} is not one the mouse driver knows")


def key_available(devices_text, device, key_name):
    """Whether the driver would find a mouse for ``device`` ("" = any) that
    reports ``key_name`` and has a wheel; None when the key is unknown."""
    code = code_for_name(key_name)
    if code is None:
        return None
    return find_mouse(devices_text, device, code) is not None


# --- Devices -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Detected:
    """What was found; see ``detect()``."""

    product: str = ""
    vendor: str = ""
    # The touchpad the driver takes for a DialPad ("" = none), the layout the
    # table lists for this laptop ("" = unlisted).
    dialpad: str = ""
    layout: str = ""
    touchpads: tuple = ()
    mice: tuple = ()
    # The path of spacenavd's socket when one exists ("" = none).
    spacenav_socket: str = ""
    # (vendor, product, name) of the SpaceMice on USB.
    spacemice: tuple = ()
    screens: tuple = ()


@dataclass(frozen=True)
class Finding:
    """One line of the detection summary. ``kind``: dialpad, touchpad, mouse,
    spacemouse, screen."""

    kind: str
    title: str
    detail: str
    found: bool


def dialpad_touchpad(devices):
    """The name of the first touchpad the driver takes for a DialPad, or ""."""
    for device in devices:
        if (
            device.name.startswith(DIALPAD_PREFIXES)
            and "Touchpad" in device.name
            and not any(word in device.name for word in DIALPAD_EXCLUDED)
            and not _own_device(device.name)
        ):
            return device.name
    return ""


def _read(path):
    try:
        return Path(path).read_text(errors="ignore").strip()
    except OSError:
        return ""


def input_devices_text():
    """/proc/bus/input/devices ("" when unreadable)."""
    return _read("/proc/bus/input/devices")


def _table_rows(path=TABLE):
    import csv
    try:
        with open(path, newline="") as table:
            return list(csv.DictReader(table))
    except OSError:
        return []


def detect(
    devices_text=None,
    usb_root="/sys/bus/usb/devices",
    dmi_root="/sys/devices/virtual/dmi/id",
    environ=None,
    spnavrc_text=None,
    socket_exists=os.path.exists,
    table_rows=None,
    layouts=None,
    status=None,
    word_bits=LONG_BITS,
):
    """Find the dial's hardware. Every source can be given (``devices_text`` =
    /proc/bus/input/devices, ``usb_root``/``dmi_root`` = sysfs folders,
    ``environ``/``spnavrc_text`` for the spacenavd socket, ``socket_exists``,
    ``table_rows`` = laptop_touchpad_dialpad_layouts.csv as dicts, ``layouts``
    = the layout names that exist); the default is this computer.
    ``status`` is the driver's GetStatus(): a touchpad it runs in DialPad mode
    is a DialPad whatever the name table says.
    """
    if devices_text is None:
        devices_text = input_devices_text()
    environ = os.environ if environ is None else environ
    if spnavrc_text is None:
        spnavrc_text = _read("/etc/spnavrc")
    if table_rows is None:
        table_rows = _table_rows()
    if layouts is None:
        layouts = {path.stem for path in (PROJECT_ROOT / "layouts").glob("*.py") if not path.stem.startswith("_")}
    product = _read(os.path.join(dmi_root, "product_name"))
    vendor = _read(os.path.join(dmi_root, "sys_vendor"))

    devices = parse_input_devices(devices_text)
    layout = choose_layout(table_rows, product, touchpad_ids(devices_text), layouts) or ""
    candidate = dialpad_touchpad(devices)
    dialpad = ""
    if candidate and (layout or "asus" in vendor.lower()):
        dialpad = candidate
    status = status or {}
    if status.get("touchpad") and not status.get("emulation") and not status.get("mouse"):
        dialpad = dialpad or str(status["touchpad"])

    touchpads = []
    for device in devices:
        if is_touchpad(device.name) and not _own_device(device.name) and device.name != dialpad:
            if device.name not in touchpads:
                touchpads.append(device.name)

    socket = ""
    for path in socket_candidates("", environ, spnavrc_text):
        if socket_exists(path):
            socket = path
            break

    return Detected(
        product=product,
        vendor=vendor,
        dialpad=dialpad,
        layout=layout,
        touchpads=tuple(touchpads),
        mice=tuple(list_mice(devices_text, word_bits)),
        spacenav_socket=socket,
        spacemice=tuple(spacemice_present(usb_root)),
        screens=tuple(screen.model.name for screen in screens_present(usb_root)),
    )


def findings(found):
    """The detection summary: one Finding per kind of device, in display order."""
    result = []
    if found.dialpad:
        detail = found.dialpad + (f" · layout {found.layout}" if found.layout else " · not in the list of known laptops: measure it with Calibration")
        result.append(Finding("dialpad", "DialPad", detail, True))
    else:
        result.append(Finding("dialpad", "DialPad", "No touchpad with a printed dial found", False))
    if found.touchpads:
        result.append(Finding("touchpad", "Touchpad", ", ".join(found.touchpads), True))
    else:
        result.append(Finding("touchpad", "Touchpad", "No other touchpad found", False))
    if found.mice:
        names = ", ".join(mouse.label for mouse in found.mice[:3])
        more = f" and {len(found.mice) - 3} more" if len(found.mice) > 3 else ""
        result.append(Finding("mouse", "Mouse with a wheel", names + more, True))
    else:
        result.append(Finding("mouse", "Mouse with a wheel", "No mouse found", False))
    if found.spacemice:
        names = ", ".join(name for _vendor, _product, name in found.spacemice)
        state = "spacenavd is running" if found.spacenav_socket else "spacenavd is not running: install and start it"
        result.append(Finding("spacemouse", "SpaceMouse", f"{names} · {state}", True))
    elif found.spacenav_socket:
        result.append(Finding("spacemouse", "SpaceMouse", "spacenavd is running, no SpaceMouse seen on USB", True))
    else:
        result.append(Finding("spacemouse", "SpaceMouse", "No SpaceMouse found", False))
    if found.screens:
        result.append(Finding("screen", "SpaceMouse screen", ", ".join(found.screens), True))
    return result


@dataclass(frozen=True)
class Suggestion:
    """The modes to switch on: ``main`` one of MAIN_MODES or "" (nothing fits:
    keep what is set), ``spacemouse`` whether to use a SpaceMouse; ``notes``
    (``mode_notes()``) explain choices that need it."""

    main: str
    spacemouse: bool
    notes: tuple = ()


def suggest_modes(found):
    """DialPad mode when a DialPad is there, else Touchpad mode for a touchpad,
    else Mouse-only mode for a mouse with a wheel; SpaceMouse mode when one is
    connected (the driver ignores it next to a touchpad, so then it is a note)."""
    if found.dialpad:
        main = MODE_DIALPAD
    elif found.touchpads:
        main = MODE_TOUCHPAD
    elif found.mice:
        main = MODE_MOUSE
    else:
        main = ""
    spacemouse = bool(found.spacemice) and main not in (MODE_DIALPAD, MODE_TOUCHPAD)
    return Suggestion(main, spacemouse, mode_notes(found, main, spacemouse))


def mode_notes(found, main, spacemouse):
    """What to know about a choice of modes (``main`` as in MAIN_MODES, ``spacemouse``
    on or off), for the setup assistant."""
    notes = []
    if main in (MODE_DIALPAD, MODE_TOUCHPAD):
        if spacemouse:
            notes.append("The driver ignores a SpaceMouse next to a touchpad: use Mouse-only mode to use both.")
        elif found.spacemice:
            notes.append(
                "A SpaceMouse is connected, but the driver ignores it next to a touchpad: "
                "switch it on yourself to use it without one."
            )
    if main == MODE_MOUSE and not found.mice:
        notes.append("No mouse with a wheel was found: plug one in, or pick another mode.")
    return tuple(notes)


def current_main_mode(config):
    """The main mode a configuration selects (Mouse-only wins over Touchpad)."""
    if config.mouse.enabled:
        return MODE_MOUSE
    if config.emulation.enabled:
        return MODE_TOUCHPAD
    return MODE_DIALPAD


def apply_modes(config, main, spacemouse):
    """Switch the modes of ``config``; returns whether anything changed (the
    driver reads them at start). ``main`` "" leaves the main mode alone."""
    before = (config.mouse.enabled, config.emulation.enabled, config.spacemouse.enabled)
    if main in MAIN_MODES:
        config.mouse.enabled = main == MODE_MOUSE
        config.emulation.enabled = main == MODE_TOUCHPAD
    config.spacemouse.enabled = bool(spacemouse)
    return before != (config.mouse.enabled, config.emulation.enabled, config.spacemouse.enabled)


# --- Which pages are useful here -----------------------------------------------------------

def calibration_available(config, found, status=None):
    """Calibration measures the DialPad's ring: only when DialPad mode is
    possible, i.e. not in Touchpad, Mouse-only mode (the driver's report wins
    over the configuration while it runs)."""
    status = status or {}
    mouse = bool(status["mouse"]) if "mouse" in status else config.mouse.enabled
    emulation = bool(status["emulation"]) if "emulation" in status else config.emulation.enabled
    if mouse or emulation:
        return False
    return bool(status.get("touchpad")) or bool(found.dialpad)


def spacemouse_available(config, found):
    """The SpaceMouse page is useful when the mode is on, spacenavd runs or a
    SpaceMouse is plugged in."""
    return bool(config.spacemouse.enabled or found.spacenav_socket or found.spacemice)


def page_visibility(config, found, status=None, show_all=False):
    """{page name: shown} for the pages that depend on the hardware."""
    return {
        "calibration": show_all or calibration_available(config, found, status),
        "spacemouse": show_all or spacemouse_available(config, found),
    }


def two_openers(config):
    """Mouse-only mode and SpaceMouse mode are both on: a mouse key and the
    SpaceMouse both open the dial."""
    return bool(config.mouse.enabled and config.spacemouse.enabled)


TWO_OPENERS_HINT = "Both a mouse key and the SpaceMouse open the dial — switch one off if you only use one"


# --- App-only preferences -------------------------------------------------------------------------

APP_SETTINGS_FILE = CONFIG_DIR / "app.json"
APP_SETTINGS_DEFAULTS = {"show_all_pages": False}


def load_app_settings(path=None):
    """The settings app's own preferences (``app.json`` next to config.json).
    They are not the driver's: it never reads the file. Unreadable = defaults."""
    settings = dict(APP_SETTINGS_DEFAULTS)
    try:
        data = json.loads(Path(path or APP_SETTINGS_FILE).read_text())
    except (OSError, ValueError):
        return settings
    if isinstance(data, dict):
        for key, default in APP_SETTINGS_DEFAULTS.items():
            if isinstance(data.get(key), type(default)):
                settings[key] = data[key]
    return settings


def save_app_settings(settings, path=None):
    """Write the preferences; returns False when the file cannot be written."""
    path = Path(path or APP_SETTINGS_FILE)
    data = {key: settings.get(key, default) for key, default in APP_SETTINGS_DEFAULTS.items()}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n")
        temporary.replace(path)
    except OSError:
        return False
    return True
