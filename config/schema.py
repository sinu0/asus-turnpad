from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, replace
from enum import Enum
from pathlib import Path

from config.identity import CONFIG_DIR_NAME

CONFIG_DIR = Path.home() / ".config" / CONFIG_DIR_NAME

CONFIG_FILE = CONFIG_DIR / "config.json"
DARKTABLE_APP_ID = "org.darktable.darktable.desktop"


PROFILE_MODES = ("multi", "single")
OVERLAY_POSITIONS = ("center", "lower", "bottom", "top", "custom", "pointer")
# Position "pointer": the dial's side of the pointer (the first one with
# room wins, the opposite side next) and its distance in logical pixels.
POINTER_SIDES = ("below-right", "below-left", "above-right", "above-left", "right", "left", "above", "below")
POINTER_GAP_MAX = 400
# full: always fully visible; fade: full on touch, then fades to
# fade_opacity while turning; pill: a small label instead of the dial.
OVERLAY_VISIBILITIES = ("full", "fade", "pill")
SCROLL_AXES = ("vertical", "horizontal")
# Keyboard modifiers that give a function other actions while held, with
# the evdev keys of both sides of the keyboard.
MODIFIER_KEYS = {
    "Ctrl": ("KEY_LEFTCTRL", "KEY_RIGHTCTRL"),
    "Shift": ("KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"),
    "Alt": ("KEY_LEFTALT", "KEY_RIGHTALT"),
}
MACRO_FIELDS = ("macro_cw", "macro_ccw", "macro_center")


class StepType(str, Enum):
    KEYSTROKE = "keystroke"
    SCROLL = "scroll"
    MEDIA = "media"
    BRIGHTNESS = "brightness"
    VOLUME = "volume"
    DELAY = "delay"
    COMMAND = "command"
    SHELL = "shell"


@dataclass
class TouchGeometry:
    width: int
    height: int
    circle_diameter: int
    center_button_diameter: int
    center_x: int
    center_y: int


@dataclass
class ActivationConfig:
    activation_time: float = 1.0
    coactivator_keys: list[str] = field(default_factory=list)


@dataclass
class GestureConfig:
    default_threshold: int = 90
    slices_minimum_count: int = 4
    hysteresis: int = 5
    disable_inactivity_time: int = 120
    # Holding the centre this long inside a function returns to the menu;
    # a shorter tap runs the function's own centre action.
    center_hold_time: float = 0.5


@dataclass
class BacklightConfig:
    # Brightness of the DialPad light while it is on: the keyboard
    # backlight's (keyboard light off = DialPad light off) or a fixed level
    # from 1 to 8; 0 keeps the light off.
    follow_keyboard: bool = True
    level: int = 4


@dataclass
class EmulationConfig:
    # Touchpads without a DialPad (no printed ring, no light): while the dial
    # is on, the whole touchpad is the dial and the pointer stands still.
    # The driver reads this at start; the settings app restarts it.
    enabled: bool = False
    # Holding the top-right corner switches the dial on and off (it can
    # always be switched with the D-Bus method Toggle, e.g. a shortcut).
    corner: bool = True
    # Centre button, percent of the touchpad's shorter side.
    center_size: int = 30


@dataclass
class MouseConfig:
    # A mouse drives the dial on a computer without a touchpad: the wheel
    # turns it, the middle button is the centre, and a key the mouse sends
    # (from its onboard memory or Piper/Solaar: F13, KEY_3) switches it on and
    # off. While the dial is on the driver grabs the mouse; the left button
    # selects, the right one goes back. Read at driver
    # start; the settings app restarts it.
    enabled: bool = False
    # Part of the input device's name; "" = the first device that has the
    # toggle key and a wheel.
    device: str = ""
    # evdev key or button name; "F13" is read as KEY_F13.
    toggle_key: str = "KEY_F13"
    # The direction the mouse moves is the dial's angle (move towards a
    # tile, circle to turn). Nothing happens until the hand has travelled 35 %
    # of motion_radius mouse counts; half of it is the stretch the direction
    # is measured over (smaller = more sensitive). Off: only the wheel and
    # the buttons.
    motion: bool = False
    motion_radius: int = 300


@dataclass
class OverlayConfig:
    """Appearance of the GNOME Shell dial; read directly by the extension."""

    scale: float = 1.0
    position: str = "lower"
    hide_delay_ms: int = 1500
    show_hints: bool = True
    show_pointer: bool = True
    accent: str = "#e7b98d"
    # Built-in skin id (gnome/extension/skins.json) or the name of a file
    # in ~/.config/asus-turnpad/skins/ without ".json".
    skin: str = "glass"
    # With follow_system_style the dial uses skin_light while GNOME is in
    # light style and skin in dark style.
    skin_light: str = "frost"
    follow_system_style: bool = False
    # Functions whose rotation step is at least this many degrees show a
    # progress arc while turning instead of waiting silently.
    progress_above_degrees: int = 120
    # Centre of the dial for position "custom", as fractions of the screen.
    custom_x: float = 0.5
    custom_y: float = 0.5
    visibility: str = "full"
    # Percent the dial fades to with visibility "fade".
    fade_opacity: int = 35
    # With position "pointer": where the dial appears around the pointer
    # and how far from it (not multiplied by scale).
    pointer_side: str = "below-right"
    pointer_gap: int = 24


# Keys a profile or a function may override; OverlayConfig holds defaults.
OVERLAY_RULE_KEYS = ("position", "custom_x", "custom_y", "visibility", "fade_opacity", "pointer_side", "pointer_gap")


def overlay_rule(values) -> dict:
    """The valid overrides in values, e.g. a profile's or function's "overlay"."""
    if not isinstance(values, dict):
        return {}
    rule = {}
    if values.get("position") in OVERLAY_POSITIONS:
        rule["position"] = values["position"]
    for key in ("custom_x", "custom_y"):
        if isinstance(values.get(key), (int, float)) and not isinstance(values.get(key), bool):
            rule[key] = round(_clamp(values[key], 0.0, 1.0, 0.5), 3)
    if values.get("visibility") in OVERLAY_VISIBILITIES:
        rule["visibility"] = values["visibility"]
    if isinstance(values.get("fade_opacity"), (int, float)) and not isinstance(values.get("fade_opacity"), bool):
        rule["fade_opacity"] = int(_clamp(values["fade_opacity"], 0, 100, 35))
    if values.get("pointer_side") in POINTER_SIDES:
        rule["pointer_side"] = values["pointer_side"]
    if isinstance(values.get("pointer_gap"), (int, float)) and not isinstance(values.get("pointer_gap"), bool):
        rule["pointer_gap"] = int(_clamp(values["pointer_gap"], 0, POINTER_GAP_MAX, 24))
    return rule


@dataclass
class MacroStep:
    type: StepType
    keys: list[str] | None = None
    value: int | None = None
    direction: str | None = None
    duration: float | None = None
    program: str | None = None
    args: list[str] | None = None
    script: str | None = None
    title: str | None = None
    # Scroll only: keys held while the wheel turns (Ctrl gives zoom) and
    # the wheel axis.
    modifiers: list[str] | None = None
    axis: str | None = None


@dataclass
class ModifierActions:
    """What a function does while a keyboard modifier (MODIFIER_KEYS) is held."""

    macro_cw: list[MacroStep] = field(default_factory=list)
    macro_ccw: list[MacroStep] = field(default_factory=list)
    macro_center: list[MacroStep] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(getattr(self, name) for name in MACRO_FIELDS)


@dataclass
class Segment:
    title: str
    icon_url: str | None = None
    treshold: int | None = None
    macro_cw: list[MacroStep] = field(default_factory=list)
    macro_ccw: list[MacroStep] = field(default_factory=list)
    macro_center: list[MacroStep] = field(default_factory=list)
    value_command: str | None = None
    unit: str | None = None
    enabled: bool = True
    color: str | None = None
    # Overrides of the on-screen dial while this function is selected.
    overlay: dict = field(default_factory=dict)
    # Other actions while Ctrl, Shift or Alt is held, by MODIFIER_KEYS name.
    with_modifier: dict[str, ModifierActions] = field(default_factory=dict)
    # A group: choosing it opens these functions as a list beside the dial
    # (one level, items never hold items). A group has no actions itself.
    items: list[Segment] | None = None

    @property
    def is_group(self) -> bool:
        return self.items is not None

    def active_items(self) -> list[Segment]:
        return [item for item in self.items or [] if item.enabled]

    def macro_lists(self) -> list[list[MacroStep]]:
        """Every step list of the function, with the modifier variants."""
        holders = [self, *self.with_modifier.values()]
        return [getattr(holder, name) for holder in holders for name in MACRO_FIELDS]

    def turns(self) -> bool:
        """False for a launcher: nothing to turn, with or without a modifier."""
        holders = [self, *self.with_modifier.values()]
        return any(holder.macro_cw or holder.macro_ccw for holder in holders)


def all_functions(segments: list[Segment]):
    """(group or None, function) for every function, the items of groups too."""
    for segment in segments:
        if segment.is_group:
            for item in segment.items:
                yield segment, item
        else:
            yield None, segment


@dataclass
class Profile:
    name: str
    app_id: str | None = None
    segments: list[Segment] = field(default_factory=list)
    # ASUS Dial offers a single-function mode (the dial always controls one
    # function) and a multi-function mode with the radial menu.
    mode: str = "multi"
    single_function: str | None = None
    sensitivity: float = 1.0
    scroll_speed: float = 1.0
    remember_last: bool = True
    # Overrides of the on-screen dial for this profile (and its menu).
    overlay: dict = field(default_factory=dict)

    def active_segments(self) -> list[Segment]:
        return [segment for segment in self.segments if segment.enabled]

    def single_segment(self) -> Segment | None:
        # The dial can be bound to a function, not to a group.
        segments = [segment for segment in self.active_segments() if not segment.is_group]
        for segment in segments:
            if segment.title == self.single_function:
                return segment
        return segments[0] if segments else None


@dataclass
class DialPadConfig:
    version: int = 2
    device_name: str = ""
    geometry: TouchGeometry = field(default_factory=lambda: TouchGeometry(
        width=250,
        height=250,
        circle_diameter=919,
        center_button_diameter=364,
        center_x=586,
        center_y=573,
    ))
    activation: ActivationConfig = field(default_factory=ActivationConfig)
    gesture: GestureConfig = field(default_factory=GestureConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    backlight: BacklightConfig = field(default_factory=BacklightConfig)
    emulation: EmulationConfig = field(default_factory=EmulationConfig)
    mouse: MouseConfig = field(default_factory=MouseConfig)
    profiles: list[Profile] = field(default_factory=list)


def shell_commands(profiles: list[Profile]) -> list[tuple[str, str, str]]:
    """(profile, function, command) for everything that runs a program or
    shell command, so imported profiles can be checked before use."""
    found = []
    for profile in profiles:
        for group, segment in all_functions(profile.segments):
            title = f"{group.title} › {segment.title}" if group else segment.title
            for steps in segment.macro_lists():
                for step in steps:
                    if step.type is StepType.SHELL and step.script:
                        found.append((profile.name, title, step.script))
                    elif step.type is StepType.COMMAND and step.program:
                        found.append((profile.name, title, " ".join([step.program, *(step.args or [])])))
            if segment.value_command:
                found.append((profile.name, title, segment.value_command))
    return found


def _default_segments() -> list[Segment]:
    from config.presets import default_segments

    return default_segments()


def darktable_colour_labels_segment() -> Segment:
    """Optional Darktable colour-label control for the profile editor."""
    return Segment(
        title="Colour label",
        macro_cw=[
            MacroStep(type=StepType.KEYSTROKE, keys=["KEY_F3"], title="Green label"),
        ],
        macro_ccw=[
            MacroStep(type=StepType.KEYSTROKE, keys=["KEY_F1"], title="Red label"),
        ],
        macro_center=[
            MacroStep(type=StepType.KEYSTROKE, keys=["KEY_F2"], title="Yellow label"),
        ],
    )


def darktable_profile() -> Profile:
    """Return the darktable profile from the pre-programmed packs."""
    from config.presets import darktable_profile_segments

    return Profile(name="Darktable", app_id=DARKTABLE_APP_ID, segments=darktable_profile_segments())


def _default_config() -> DialPadConfig:
    return DialPadConfig(
        device_name="",
        profiles=[
            Profile(
                name="Default",
                app_id=None,
                segments=_default_segments(),
            ),
            darktable_profile(),
        ],
    )


def _clamp(value, minimum, maximum, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(number, maximum))


def normalize_key_name(value, default):
    """An evdev key name as typed ("f13", "KEY_3") in its KEY_ form."""
    if not isinstance(value, str) or not value.strip():
        return default
    name = value.strip().upper().replace(" ", "_")
    # Mouse buttons (BTN_SIDE, BTN_EXTRA) can switch the dial as well.
    return name if name.startswith(("KEY_", "BTN_")) else f"KEY_{name}"


def _encoder(obj):
    if isinstance(obj, DialPadConfig):
        return {
            "version": obj.version,
            "device_name": obj.device_name,
            "geometry": _encoder(obj.geometry),
            "activation": _encoder(obj.activation),
            "gesture": _encoder(obj.gesture),
            "overlay": _encoder(obj.overlay),
            "backlight": _encoder(obj.backlight),
            "emulation": _encoder(obj.emulation),
            "mouse": _encoder(obj.mouse),
            "profiles": [_encoder(p) for p in obj.profiles],
        }
    if isinstance(obj, (TouchGeometry, ActivationConfig, GestureConfig, OverlayConfig, BacklightConfig, EmulationConfig, MouseConfig)):
        return {item.name: getattr(obj, item.name) for item in fields(obj)}
    if isinstance(obj, Profile):
        result: dict = {
            "name": obj.name,
            "segments": [_encoder(s) for s in obj.segments],
        }
        if obj.app_id is not None:
            result["app_id"] = obj.app_id
        result["mode"] = obj.mode
        if obj.single_function:
            result["single_function"] = obj.single_function
        result["sensitivity"] = obj.sensitivity
        result["scroll_speed"] = obj.scroll_speed
        result["remember_last"] = obj.remember_last
        if obj.overlay:
            result["overlay"] = dict(obj.overlay)
        return result
    if isinstance(obj, Segment):
        result: dict = {
            "title": obj.title,
            "macro_cw": [_encoder(m) for m in obj.macro_cw],
            "macro_ccw": [_encoder(m) for m in obj.macro_ccw],
        }
        if obj.icon_url is not None:
            result["icon_url"] = obj.icon_url
        if obj.treshold is not None:
            result["treshold"] = obj.treshold
        if obj.macro_center:
            result["macro_center"] = [_encoder(m) for m in obj.macro_center]
        if obj.value_command is not None:
            result["value_command"] = obj.value_command
        if obj.unit is not None:
            result["unit"] = obj.unit
        if not obj.enabled:
            result["enabled"] = False
        if obj.color:
            result["color"] = obj.color
        if obj.overlay:
            result["overlay"] = dict(obj.overlay)
        variants = {
            name: {
                field_name: [_encoder(m) for m in getattr(actions, field_name)]
                for field_name in MACRO_FIELDS
                if getattr(actions, field_name)
            }
            for name, actions in obj.with_modifier.items()
            if name in MODIFIER_KEYS and not actions.is_empty()
        }
        if variants:
            result["with_modifier"] = {name: variants[name] for name in MODIFIER_KEYS if name in variants}
        if obj.is_group:
            result["items"] = [_encoder(item) for item in obj.items]
        return result
    if isinstance(obj, MacroStep):
        result: dict = {"type": obj.type.value}
        for attr in (
            "keys", "value", "direction", "duration", "program", "args",
            "script", "title", "modifiers", "axis",
        ):
            val = getattr(obj, attr)
            if val is not None:
                result[attr] = val
        return result
    raise TypeError(f"Unserializable object: {type(obj)}")


def _known(d: dict, cls: type) -> dict:
    names = {item.name for item in fields(cls)}
    return {key: value for key, value in d.items() if key in names}


def _decoder(d: dict, cls: type):
    if cls is DialPadConfig:
        return DialPadConfig(
            version=d.get("version", 2),
            device_name=d.get("device_name", ""),
            geometry=_decoder(d["geometry"], TouchGeometry),
            activation=_decoder(d.get("activation", {}), ActivationConfig),
            gesture=_decoder(d.get("gesture", {}), GestureConfig),
            overlay=_decoder(d.get("overlay", {}), OverlayConfig),
            backlight=_decoder(d.get("backlight", {}), BacklightConfig),
            emulation=_decoder(d.get("emulation", {}), EmulationConfig),
            mouse=_decoder(d.get("mouse", {}), MouseConfig),
            profiles=[_decoder(p, Profile) for p in d.get("profiles", [])],
        )
    if cls is TouchGeometry:
        return TouchGeometry(**_known(d, TouchGeometry))
    if cls is ActivationConfig:
        return ActivationConfig(**_known(d, ActivationConfig))
    if cls is GestureConfig:
        return GestureConfig(**_known(d, GestureConfig))
    if cls is OverlayConfig:
        overlay = OverlayConfig(**_known(d, OverlayConfig))
        overlay.scale = _clamp(overlay.scale, 0.6, 1.6, 1.0)
        if overlay.position not in OVERLAY_POSITIONS:
            overlay.position = "lower"
        overlay.custom_x = round(_clamp(overlay.custom_x, 0.0, 1.0, 0.5), 3)
        overlay.custom_y = round(_clamp(overlay.custom_y, 0.0, 1.0, 0.5), 3)
        if overlay.visibility not in OVERLAY_VISIBILITIES:
            overlay.visibility = "full"
        overlay.fade_opacity = int(_clamp(overlay.fade_opacity, 0, 100, 35))
        if overlay.pointer_side not in POINTER_SIDES:
            overlay.pointer_side = "below-right"
        overlay.pointer_gap = int(_clamp(overlay.pointer_gap, 0, POINTER_GAP_MAX, 24))
        if not isinstance(overlay.skin, str) or not overlay.skin.strip():
            overlay.skin = "glass"
        if not isinstance(overlay.skin_light, str) or not overlay.skin_light.strip():
            overlay.skin_light = "frost"
        overlay.follow_system_style = bool(overlay.follow_system_style)
        return overlay
    if cls is BacklightConfig:
        return BacklightConfig(
            follow_keyboard=bool(d.get("follow_keyboard", True)),
            level=int(_clamp(d.get("level", 4), 0, 8, 4)),
        )
    if cls is EmulationConfig:
        return EmulationConfig(
            enabled=bool(d.get("enabled", False)),
            corner=bool(d.get("corner", True)),
            center_size=int(_clamp(d.get("center_size", 30), 10, 60, 30)),
        )
    if cls is MouseConfig:
        device = d.get("device", "")
        return MouseConfig(
            enabled=bool(d.get("enabled", False)),
            device=device.strip() if isinstance(device, str) else "",
            toggle_key=normalize_key_name(d.get("toggle_key", "KEY_F13"), "KEY_F13"),
            motion=bool(d.get("motion", False)),
            motion_radius=int(_clamp(d.get("motion_radius", 300), 50, 2000, 300)),
        )
    if cls is Profile:
        mode = d.get("mode", "multi")
        return Profile(
            name=d["name"],
            app_id=d.get("app_id"),
            segments=[_decoder(s, Segment) for s in d.get("segments", [])],
            mode=mode if mode in PROFILE_MODES else "multi",
            single_function=d.get("single_function"),
            sensitivity=_clamp(d.get("sensitivity", 1.0), 0.25, 4.0, 1.0),
            scroll_speed=_clamp(d.get("scroll_speed", 1.0), 0.25, 4.0, 1.0),
            remember_last=bool(d.get("remember_last", True)),
            overlay=overlay_rule(d.get("overlay")),
        )
    if cls is Segment:
        items = d.get("items")
        return Segment(
            title=d["title"],
            icon_url=d.get("icon_url"),
            treshold=d.get("treshold"),
            macro_cw=[_decoder(m, MacroStep) for m in d.get("macro_cw", [])],
            macro_ccw=[_decoder(m, MacroStep) for m in d.get("macro_ccw", [])],
            macro_center=[_decoder(m, MacroStep) for m in d.get("macro_center", [])],
            value_command=d.get("value_command"),
            unit=d.get("unit"),
            enabled=bool(d.get("enabled", True)),
            color=d.get("color"),
            overlay=overlay_rule(d.get("overlay")),
            with_modifier=_modifier_variants(d.get("with_modifier")),
            # One level only: the items of an item are dropped.
            items=[
                replace(_decoder(item, Segment), items=None) for item in items if isinstance(item, dict)
            ] if isinstance(items, list) else None,
        )
    if cls is MacroStep:
        values = _known(d, MacroStep)
        values["type"] = StepType(d["type"])
        return MacroStep(**values)
    raise TypeError(f"Unknown class: {cls}")


def _modifier_variants(values) -> dict[str, ModifierActions]:
    if not isinstance(values, dict):
        return {}
    variants = {}
    for name in MODIFIER_KEYS:
        actions = values.get(name)
        if not isinstance(actions, dict):
            continue
        variant = ModifierActions(**{
            field_name: [_decoder(m, MacroStep) for m in actions.get(field_name) or []]
            for field_name in MACRO_FIELDS
        })
        if not variant.is_empty():
            variants[name] = variant
    return variants


class ConfigError(ValueError):
    """config.json cannot be used; the message says why."""


def config_from_text(text: str) -> DialPadConfig:
    """The configuration in a config.json document; raises ConfigError."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ConfigError(f"not valid JSON: {error.msg} at line {error.lineno}, column {error.colno}") from None
    if not isinstance(data, dict):
        raise ConfigError("the file holds no JSON object")
    try:
        return _decoder(data, DialPadConfig)
    except KeyError as error:
        raise ConfigError(f"missing {error.args[0]!r}") from None
    except (TypeError, ValueError, AttributeError) as error:
        raise ConfigError(f"unreadable value: {error}") from None


def load_config() -> DialPadConfig:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if not CONFIG_FILE.exists():
        cfg = _default_config()
        save_config(cfg)
        return cfg

    return config_from_text(CONFIG_FILE.read_text())


def save_config(cfg: DialPadConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Write atomically: the driver and the Shell extension watch this file
    # and must never read a half-written document.
    temporary = CONFIG_FILE.with_suffix(".json.tmp")
    with open(temporary, "w") as f:
        json.dump(_encoder(cfg), f, indent=2)
        f.write("\n")
    temporary.replace(CONFIG_FILE)
