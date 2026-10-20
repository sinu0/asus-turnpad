"""3Dconnexion SpaceMouse models: the physical keys with the raw button
indexes spacenavd reports, and the screen of the SpacePilot Pro. Pure data
and sysfs reading, no hardware imports, so the settings app, the driver and
the screen service share one table.

Raw button index = evdev code - 256. Some keys are "dual-function": the device
itself tells a short press (its own index, press and release both at the
release) from a long one (about 1 s, another index, down while held).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Display order and titles of Key.group.
GROUPS = ("function", "modifier", "view", "motion", "system")
GROUP_LABELS = {
    "function": "Function keys",
    "modifier": "Modifier keys",
    "view": "View keys",
    "motion": "Motion keys",
    "system": "System keys",
}


@dataclass(frozen=True)
class Key:
    """A physical button: ``label`` as printed, ``name`` in full, ``index`` its
    (short press) raw index, ``long_index`` the one the device sends on its own
    after a long press."""

    id: str
    label: str
    name: str
    index: int
    long_index: int | None = None
    long_name: str = ""
    group: str = "system"


@dataclass(frozen=True)
class ScreenKey:
    """A key around the screen: ``bit`` in the 2-byte mask of its interrupt endpoint."""

    id: str
    label: str
    bit: int


@dataclass(frozen=True)
class Screen:
    width: int
    height: int
    # Rows from this one down are hidden by the bezel: keep content above it.
    safe_bottom: int
    usb_interface: int
    endpoint: int
    keys_endpoint: int
    keys: tuple[ScreenKey, ...]


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    usb_ids: tuple[tuple[int, int], ...]
    # Lower case, matched in the device name spacenavd reports.
    names: tuple[str, ...]
    button_count: int
    keys: tuple[Key, ...]
    screen: Screen | None = None


def _function_keys() -> tuple[Key, ...]:
    return tuple(
        Key(
            f"{number}", f"{number}", f"Function key {number}", 11 + number,
            long_index=16 + number, long_name=f"Function key {number} (long)", group="function",
        )
        for number in range(1, 6)
    )


# Verified on a SpacePilot Pro (046d:c629, spacenavd 1.3.1). The long indexes
# marked "inferred" were not all seen on the device: they follow Blender's NDOF
# button table (LEFT 3, BACK 7, ROLL_CCW 9, ISO2 11) and earlier sessions that
# saw 3, 6, 7, 9 and 11 pressed.
SPACEPILOT_PRO = Model(
    id="spacepilot-pro",
    name="SpacePilot Pro",
    usb_ids=((0x046D, 0xC629),),
    names=("spacepilot pro",),
    button_count=31,
    keys=(
        Key("esc", "Esc", "Escape", 22, group="modifier"),
        Key("shift", "Shift", "Shift", 24, group="modifier"),
        Key("ctrl", "Ctrl", "Ctrl", 25, group="modifier"),
        Key("alt", "Alt", "Alt", 23, group="modifier"),
        *_function_keys(),
        Key("menu", "Menu", "Menu", 0),
        Key("fit", "Fit", "Fit", 1, group="view"),
        Key("t", "T", "Top view", 2, 6, "Bottom view", "view"),
        Key("r", "R", "Right view", 4, 3, "Left view", "view"),  # long index inferred
        Key("f", "F", "Front view", 5, 7, "Back view", "view"),  # long index inferred
        Key("roll", "Roll", "Roll clockwise", 8, 9, "Roll counter-clockwise", "view"),  # long index inferred
        Key("iso", "ISO", "ISO view 1", 10, 11, "ISO view 2", "view"),  # long index inferred
        Key("rotation", "Rotation", "Rotation lock", 26, group="motion"),
        Key("panzoom", "Pan/Zoom", "Pan/Zoom", 27, group="motion"),
        Key("dominant", "Dom", "Dominant axis", 28, group="motion"),
        Key("plus", "+", "Speed +", 29, group="motion"),
        Key("minus", "−", "Speed −", 30, group="motion"),
    ),
    screen=Screen(
        width=320, height=240, safe_bottom=238, usb_interface=0, endpoint=0x02, keys_endpoint=0x81,
        keys=(
            ScreenKey("settings", "Settings", 0x0001),
            ScreenKey("back", "Back", 0x0002),
            ScreenKey("menu", "Menu", 0x0004),
            ScreenKey("ok", "OK", 0x0008),
            ScreenKey("right", "Right", 0x0010),
            ScreenKey("left", "Left", 0x0020),
            ScreenKey("down", "Down", 0x0040),
            ScreenKey("up", "Up", 0x0080),
            ScreenKey("light", "Light", 0x0200),
        ),
    ),
)

MODELS = (SPACEPILOT_PRO,)


def model_for_name(device_name: str) -> Model | None:
    """The model of the device name spacenavd reports ("3Dconnexion  SpacePilot PRO ")."""
    text = " ".join(str(device_name or "").split()).lower()
    if not text:
        return None
    for model in MODELS:
        if any(name in text for name in model.names):
            return model
    return None


def model_for_usb(vendor: int, product: int) -> Model | None:
    for model in MODELS:
        if (vendor, product) in model.usb_ids:
            return model
    return None


def generic_model(button_count: int) -> Model:
    """An unknown device: one "Button N" per index, no long presses, no screen."""
    count = max(0, int(button_count))
    return Model(
        id="generic", name="SpaceMouse", usb_ids=(), names=(), button_count=count,
        keys=tuple(Key(f"button{index}", str(index), f"Button {index}", index) for index in range(count)),
    )


def key_for_index(model: Model, index: int) -> tuple[Key, bool] | None:
    """(key, is_long) of a raw index; None when the model has no such button."""
    for key in model.keys:
        if key.index == index:
            return key, False
        if key.long_index is not None and key.long_index == index:
            return key, True
    return None


def index_label(model: Model | None, index: int) -> str:
    """"1", "1 · long", "T", "T · long"; "Button N" when the model does not know it."""
    found = key_for_index(model, index) if model is not None else None
    if found is None:
        return f"Button {index}"
    key, is_long = found
    return f"{key.label} · long" if is_long else key.label


@dataclass(frozen=True)
class PresentScreen:
    model: Model
    busnum: int
    devnum: int
    path: str


def _read_hex_or_int(path: str, base: int) -> int | None:
    try:
        with open(path, "r", encoding="ascii") as handle:
            return int(handle.read().strip(), base)
    except (OSError, ValueError):
        return None


def screens_present(root: str = "/sys/bus/usb/devices") -> list[PresentScreen]:
    """The USB devices under ``root`` (sysfs) whose model has a screen."""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    found = []
    for name in names:
        folder = os.path.join(root, name)
        vendor = _read_hex_or_int(os.path.join(folder, "idVendor"), 16)
        product = _read_hex_or_int(os.path.join(folder, "idProduct"), 16)
        if vendor is None or product is None:
            continue
        model = model_for_usb(vendor, product)
        if model is None or model.screen is None:
            continue
        busnum = _read_hex_or_int(os.path.join(folder, "busnum"), 10)
        devnum = _read_hex_or_int(os.path.join(folder, "devnum"), 10)
        if busnum is None or devnum is None:
            continue
        found.append(PresentScreen(model, busnum, devnum, folder))
    return found


# USB vendor 3Dconnexion since 2017 and Logitech's 3Dconnexion products before
# it: the products of vendor 046d that are SpaceMice, as spacenavd lists them.
SPACEMOUSE_VENDOR_3DX = 0x256F
SPACEMOUSE_VENDOR_LOGITECH = 0x046D
SPACEMOUSE_LOGITECH_PRODUCTS = frozenset({
    0xC603, 0xC605, 0xC606, 0xC621, 0xC623, 0xC625, 0xC626, 0xC627, 0xC628, 0xC629,
    0xC62B, 0xC62E, 0xC62F, 0xC631, 0xC632, 0xC633, 0xC635, 0xC636, 0xC640, 0xC652,
})


def is_spacemouse_usb(vendor: int, product: int) -> bool:
    """Whether a USB id is a 3Dconnexion SpaceMouse (a model this program knows
    or any product of vendor 256f)."""
    return (
        vendor == SPACEMOUSE_VENDOR_3DX
        or (vendor == SPACEMOUSE_VENDOR_LOGITECH and product in SPACEMOUSE_LOGITECH_PRODUCTS)
        or model_for_usb(vendor, product) is not None
    )


def spacemice_present(root: str = "/sys/bus/usb/devices") -> list[tuple[int, int, str]]:
    """(vendor, product, name) of the SpaceMice plugged in under ``root`` (sysfs);
    the name is the one the device reports, else its model's."""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    found = []
    for name in names:
        folder = os.path.join(root, name)
        vendor = _read_hex_or_int(os.path.join(folder, "idVendor"), 16)
        product = _read_hex_or_int(os.path.join(folder, "idProduct"), 16)
        if vendor is None or product is None or not is_spacemouse_usb(vendor, product):
            continue
        try:
            with open(os.path.join(folder, "product"), "r", encoding="utf-8", errors="replace") as handle:
                title = " ".join(handle.read().split())
        except OSError:
            title = ""
        model = model_for_usb(vendor, product)
        found.append((vendor, product, title or (model.name if model else "SpaceMouse")))
    return found
