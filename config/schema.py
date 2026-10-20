from __future__ import annotations

import copy
import json
import re
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
    # A command of the application in focus, run through the app link (the
    # Blender add-on); ``keys`` are the shortcut used when no add-on is connected.
    APP = "app"


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
class SpaceMouseConfig:
    # A 3Dconnexion SpaceMouse (through spacenavd) drives the dial: its buttons
    # open the dial or a group's list, tilt picks, twist turns, a push on the
    # cap is the centre button. Read at driver start. Axis numbers are the
    # device's raw axes: 0 1 2 = X Y Z, 3 4 5 = RX RY RZ.
    enabled: bool = False
    # spacenavd's socket; "" = $SPNAV_SOCKET, /etc/spnavrc, the runtime dir.
    socket: str = ""
    # A button held this long (ms) runs its hold action.
    hold_ms: int = 400
    # Full deflection of an axis in raw units.
    axis_max: int = 400
    # The tilt axes [horizontal, vertical] (the raw axis that grows when the cap
    # tilts right, and the one that grows when it tilts towards you) and
    # whether each is inverted. The defaults are a SpacePilot Pro's raw axes as
    # spacenavd reports them: tilt right = axis 5 +, tilt towards you = axis 3 +,
    # twist clockwise = axis 4 - (inverted), push down = axis 1 - (inverted).
    tilt_axes: list[int] = field(default_factory=lambda: [5, 3])
    tilt_invert: list[bool] = field(default_factory=lambda: [False, False])
    twist_axis: int = 4
    twist_invert: bool = True
    # Degrees per second at full twist.
    twist_speed: int = 360
    push_axis: int = 1
    push_invert: bool = True
    # Silence the other spacenavd clients (CAD programs) while the dial is on.
    mute_others: bool = True
    # Hide the buttons Turnpad uses from the other clients (experimental).
    hide_buttons: bool = False
    # Button index (as text) -> its actions; empty = default_spacemouse_buttons().
    buttons: dict[str, SpaceMouseButton] = field(default_factory=dict)
    # Button layers: name -> button map laid over the base map while the layer
    # is active (SpaceMouseAction "layer"). Profiles add to them per layer.
    layers: dict[str, dict[str, SpaceMouseButton]] = field(default_factory=dict)
    # How the cap's motion reaches other programs (spacenavd's sensitivity,
    # axis sensitivities and inverted axes, see motion_rule()); {} = leave
    # spacenavd as it is. Profiles override it per key.
    motion: dict = field(default_factory=dict)


SCREEN_PAGES = ("keys", "dial", "device", "motion", "clock")
SCREEN_THEMES = ("skin", "dark", "light", "contrast")
SCREEN_TEXT_SIZES = ("normal", "large")


@dataclass
class ScreenConfig:
    # The screen of a SpaceMouse that has one (SpacePilot Pro) shows Turnpad:
    # the buttons' actions, the live dial, the device, the cap's motion, a
    # clock. Read by the screen service (asus-turnpad-screen.service).
    enabled: bool = True
    # Percent 0-100 (the device maps it onto its raw range, screen/device.py).
    brightness: int = 70
    # Idle seconds before dimming to dim_brightness / before the screen goes
    # dark; 0 = never. Pressing a button wakes it.
    dim_after_s: int = 300
    dim_brightness: int = 10
    off_after_s: int = 0
    wake_on_motion: bool = True
    # Shown pages in order (SCREEN_PAGES) and the first one.
    pages: list[str] = field(default_factory=lambda: list(SCREEN_PAGES))
    start_page: str = "keys"
    # The dial page shows while the dial is on, then the previous page again.
    follow_dial: bool = True
    # A pressed button's action is shown for a moment.
    press_feedback: bool = True
    theme: str = "skin"
    text_size: str = "normal"
    # Long-press labels on the keys page.
    show_long: bool = True
    clock_24h: bool = True


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


def screen_pages(values) -> list[str]:
    """The known pages of a list, in order and without repeats; [] when none is left."""
    if not isinstance(values, (list, tuple)):
        return []
    pages: list[str] = []
    for page in values:
        if page in SCREEN_PAGES and page not in pages:
            pages.append(page)
    return pages


def screen_rule(values) -> dict:
    """The valid overrides in values, a profile's "screen": start_page, pages, accent."""
    if not isinstance(values, dict):
        return {}
    rule = {}
    pages = screen_pages(values.get("pages"))
    if pages:
        rule["pages"] = pages
    if values.get("start_page") in SCREEN_PAGES:
        rule["start_page"] = values["start_page"]
    accent = values.get("accent")
    if isinstance(accent, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", accent.strip()):
        rule["accent"] = accent.strip().lower()
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
    # App step only: the id of a command in the application's catalog
    # (the app link); ``keys`` is the fallback shortcut.
    command: str | None = None


@dataclass
class ModifierActions:
    """What a function does while a keyboard modifier (MODIFIER_KEYS) is held."""

    macro_cw: list[MacroStep] = field(default_factory=list)
    macro_ccw: list[MacroStep] = field(default_factory=list)
    macro_center: list[MacroStep] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(getattr(self, name) for name in MACRO_FIELDS)


SPACEMOUSE_ACTION_TYPES = ("macro", "modifier", "key_hold", "none", "dial", "group", "function", "select", "back")
SPACEMOUSE_ACTION_TYPES += ("layer", "motion")
SPACEMOUSE_ACTION_TYPES += ("app",)
APP_COMMAND_MAX = 128
SPACEMOUSE_LAYER_MODES = ("hold", "latch", "toggle")
SPACEMOUSE_MOTION_ACTIONS = ("rotation", "translation", "faster", "slower", "reset")
SPACEMOUSE_LAYER_NAME_MAX = 40
SPACEMOUSE_LAYERS_MAX = 16
SPACEMOUSE_BUTTONS = 64


@dataclass
class SpaceMouseAction:
    """What a SpaceMouse button does.

    macro: ``macro`` (steps) or ``keys`` (one key chord) run once;
    modifier: Ctrl, Shift or Alt are held while the button is down (press only);
    key_hold: ``keys`` (any evdev keys) are held while the button is down, from
    the press, or from the hold time when it is the hold action (a Blender pie
    menu: hold Z); none: do nothing in Turnpad and leave the button to other
    programs, to override a global binding in a profile;
    dial: switch the dial on or off; group: open a group's list (``group``,
    else the first one) of the current profile; function: enter a function of
    the profile's dial, by name (``function``) or menu position (``slot``,
    0 = the first entry; a group opens its list); select: tap the centre;
    back: leave the function or list;
    layer: ``layer`` (a name in ``layers``) takes over the other buttons: hold =
    while this button is down, latch = for the next press of another button
    (pressing this one again cancels), toggle = until this button is pressed
    again (or the application/profile changes); motion: ``rotation`` and
    ``translation`` switch that part of the cap's motion off and on for other
    programs, ``faster``/``slower`` step their sensitivity, ``reset`` undoes
    the session's changes (all per application, not saved).
    """

    type: str
    keys: list[str] | None = None
    macro: list[MacroStep] | None = None
    modifier: str | None = None
    group: str | None = None
    function: str | None = None
    slot: int | None = None
    # layer: switch the button layer ``layer`` on (see ``layer_mode``); motion:
    # a session toggle of the cap (one of SPACEMOUSE_MOTION_ACTIONS).
    layer: str | None = None
    layer_mode: str | None = None
    motion: str | None = None
    # app: the id of a command in the focused application's catalog (the app
    # link, e.g. the Blender add-on); ``keys`` are the fallback shortcut, used
    # when the application is not linked.
    command: str | None = None

    def steps(self) -> list[MacroStep]:
        """The macro to run: the steps, or the key chord as one keystroke."""
        if self.type == "app":
            if not self.command:
                return []
            return [MacroStep(type=StepType.APP, command=self.command, keys=list(self.keys) if self.keys else None)]
        if self.macro:
            return list(self.macro)
        if self.keys:
            return [MacroStep(type=StepType.KEYSTROKE, keys=list(self.keys))]
        return []


SPACEMOUSE_LABEL_MAX = 40


@dataclass
class SpaceMouseButton:
    press: SpaceMouseAction | None = None
    hold: SpaceMouseAction | None = None
    # Text for a device screen (and the settings app) instead of the automatic
    # description of the action; "" = automatic.
    label: str = ""


def spacemouse_binds(button: SpaceMouseButton | None) -> bool:
    """Whether Turnpad acts on this button: a press or hold that is not ``none``."""
    return button is not None and any(
        action is not None and action.type != "none" for action in (button.press, button.hold)
    )


def default_spacemouse_buttons() -> dict[str, SpaceMouseButton]:
    """The map of a 3Dconnexion SpacePilot Pro when ``buttons`` is empty: Menu
    (0) switches the dial; the five function keys are dual-function keys (the
    device tells a short press, indexes 12..16, from a long one, 17..21): the
    short press enters the first five entries of the profile's menu, the long
    press the next five; Esc (22) goes back; Alt, Shift and Ctrl (23, 24, 25)
    are held down; Rotation (26) and Pan/Zoom (27) switch that part of the cap's
    motion off and on, + (29) and − (30) step its sensitivity (spacenavd, for
    other programs). The rest is left to other applications."""
    buttons = {"0": SpaceMouseButton(press=SpaceMouseAction("dial"))}
    for number in range(5):
        buttons[str(12 + number)] = SpaceMouseButton(press=SpaceMouseAction("function", slot=number))
        buttons[str(17 + number)] = SpaceMouseButton(press=SpaceMouseAction("function", slot=5 + number))
    buttons["22"] = SpaceMouseButton(press=SpaceMouseAction("back"))
    for index, name in ((23, "Alt"), (24, "Shift"), (25, "Ctrl")):
        buttons[str(index)] = SpaceMouseButton(press=SpaceMouseAction("modifier", modifier=name))
    # Rotation, Pan/Zoom, + and −: the cap's motion for other programs.
    for index, motion in ((26, "rotation"), (27, "translation"), (29, "faster"), (30, "slower")):
        buttons[str(index)] = SpaceMouseButton(press=SpaceMouseAction("motion", motion=motion))
    return buttons


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


CONTEXT_TITLE_MAX = 200
CONTEXT_NAME_MAX = 60
CONTEXTS_MAX = 32
CONTEXT_MODE_MAX = 60
CONTEXT_WINDOW_TITLE_MAX = 500


def context_title_alternatives(title) -> list[str]:
    """The ``|``-separated alternatives of a title condition: stripped, case-folded,
    empty ones dropped."""
    return [part.strip().casefold() for part in str(title or "").split("|") if part.strip()]


def context_title_matches(title, window_title) -> bool:
    """Plain text, no regular expression (a pattern like ``(a|a)*b`` would freeze the
    driver): the window title's first characters contain any alternative of ``title``."""
    haystack = str(window_title or "")[:CONTEXT_WINDOW_TITLE_MAX].casefold()
    return any(part in haystack for part in context_title_alternatives(title))


def context_title_error(title) -> str | None:
    """Why ``title`` cannot be a context's window-title condition (plain text of at
    most CONTEXT_TITLE_MAX characters, alternatives separated by ``|``, at least one
    not empty), None when it can."""
    if not isinstance(title, str):
        return "the title must be text"
    if len(title) > CONTEXT_TITLE_MAX:
        return f"the title is longer than {CONTEXT_TITLE_MAX} characters"
    if not context_title_alternatives(title):
        return "the title has no text to look for"
    return None


@dataclass
class ProfileContext:
    """A variant of an application's profile for a window title and/or a mode of
    the application (the app link's ``context.mode``): the first context of a
    profile whose conditions all hold is in force, otherwise the profile itself.
    A context without conditions never matches. What a context leaves unset
    (``segments`` None, empty maps) is the profile's."""

    name: str
    # Plain text, case-insensitive: the window's title contains any of the
    # ``|``-separated alternatives (no regular expression).
    title: str = ""
    # Modes of the linked application (Blender: EDIT_MESH, SCULPT...).
    app_modes: list[str] = field(default_factory=list)
    # Its own dial menu; None keeps the profile's.
    segments: list[Segment] | None = None
    # Laid over the profile's maps (merge_spacemouse_buttons / merge_spacemouse_layers).
    spacemouse_buttons: dict[str, SpaceMouseButton] = field(default_factory=dict)
    spacemouse_layers: dict[str, dict[str, SpaceMouseButton]] = field(default_factory=dict)
    # Laid over the profile's screen overrides (screen_rule()).
    screen: dict = field(default_factory=dict)

    def has_conditions(self) -> bool:
        return bool(self.title or self.app_modes)

    def matches(self, window_title, app_mode) -> bool:
        if not self.has_conditions():
            return False
        if self.app_modes and (app_mode or "") not in self.app_modes:
            return False
        if self.title:
            if not context_title_matches(self.title, window_title):
                return False
        return True


def matching_context(contexts: list[ProfileContext], window_title, app_mode) -> ProfileContext | None:
    """The first context whose conditions hold, None for the plain profile."""
    for context in contexts or []:
        if context.matches(window_title, app_mode):
            return context
    return None


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
    # SpaceMouse buttons of this application; they replace the global
    # binding of the same button (SpaceMouseConfig.buttons).
    spacemouse_buttons: dict[str, SpaceMouseButton] = field(default_factory=dict)
    # Button layers of this application, merged per layer over the global ones.
    spacemouse_layers: dict[str, dict[str, SpaceMouseButton]] = field(default_factory=dict)
    # Cap motion of this application (overrides SpaceMouseConfig.motion per key).
    spacemouse_motion: dict = field(default_factory=dict)
    # Overrides of the SpaceMouse screen for this application (screen_rule()).
    screen: dict = field(default_factory=dict)
    # Variants for window titles / modes of the application, first match wins.
    contexts: list[ProfileContext] = field(default_factory=list)

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
    spacemouse: SpaceMouseConfig = field(default_factory=SpaceMouseConfig)
    screen: ScreenConfig = field(default_factory=ScreenConfig)
    profiles: list[Profile] = field(default_factory=list)
    # The setup assistant of the settings app has run (or was dismissed). A
    # new configuration starts with False; a config.json without the key
    # (written before the assistant existed) counts as done.
    setup_done: bool = False


def _steps_commands(steps) -> list[str]:
    commands = []
    for step in steps or []:
        if step.type is StepType.SHELL and step.script:
            commands.append(step.script)
        elif step.type is StepType.COMMAND and step.program:
            commands.append(" ".join([step.program, *(step.args or [])]))
    return commands


def _spacemouse_commands(
    owner: str, buttons: dict[str, SpaceMouseButton], prefix: str = "",
) -> list[tuple[str, str, str]]:
    found = []
    for index, button in sorted(buttons.items(), key=lambda item: int(item[0])):
        for kind, action in (("", button.press), (" (hold)", button.hold)):
            if action is not None and action.type == "macro":
                for command in _steps_commands(action.macro):
                    found.append((owner, f"SpaceMouse {prefix}button {index}{kind}", command))
    return found


def shell_commands(
    profiles: list[Profile], spacemouse_buttons: dict[str, SpaceMouseButton] | None = None,
    global_layers: dict[str, dict[str, SpaceMouseButton]] | None = None,
) -> list[tuple[str, str, str]]:
    """(profile, function, command) for everything that runs a program or
    shell command, so imported profiles can be checked before use. That
    includes the SpaceMouse buttons of the profiles and, given, the global
    ``spacemouse_buttons``."""
    found = []
    if spacemouse_buttons:
        found.extend(_spacemouse_commands("SpaceMouse", spacemouse_buttons))
    if global_layers:
        for layer, layer_buttons in global_layers.items():
            found.extend(_spacemouse_commands("SpaceMouse", layer_buttons, f"{layer} layer, "))
    for profile in profiles:
        found.extend(_spacemouse_commands(profile.name, profile.spacemouse_buttons))
        for layer, layer_buttons in (getattr(profile, "spacemouse_layers", None) or {}).items():
            found.extend(_spacemouse_commands(profile.name, layer_buttons, f"{layer} layer, "))
        owners = [(profile.name, profile.segments)]
        for context in profile.contexts:
            # A context's own buttons and menu run commands just like the profile's.
            owner = f"{profile.name} › {context.name}"
            found.extend(_spacemouse_commands(owner, context.spacemouse_buttons))
            for layer, layer_buttons in context.spacemouse_layers.items():
                found.extend(_spacemouse_commands(owner, layer_buttons, f"{layer} layer, "))
            if context.segments is not None:
                owners.append((owner, context.segments))
        for owner, segments in owners:
            for group, segment in all_functions(segments):
                title = f"{group.title} › {segment.title}" if group else segment.title
                for steps in segment.macro_lists():
                    for step in steps:
                        if step.type is StepType.SHELL and step.script:
                            found.append((owner, title, step.script))
                        elif step.type is StepType.COMMAND and step.program:
                            found.append((owner, title, " ".join([step.program, *(step.args or [])])))
                if segment.value_command:
                    found.append((owner, title, segment.value_command))
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
            "spacemouse": _encoder(obj.spacemouse),
            "screen": _encoder(obj.screen),
            "profiles": [_encoder(p) for p in obj.profiles],
            "setup_done": obj.setup_done,
        }
    if isinstance(obj, (TouchGeometry, ActivationConfig, GestureConfig, OverlayConfig, BacklightConfig, EmulationConfig, MouseConfig, ScreenConfig)):
        return {item.name: getattr(obj, item.name) for item in fields(obj)}
    if isinstance(obj, SpaceMouseConfig):
        result = {item.name: getattr(obj, item.name) for item in fields(obj) if item.name not in ("buttons", "layers", "motion")}
        result["buttons"] = _spacemouse_buttons_dict(obj.buttons)
        result["layers"] = _spacemouse_layers_dict(obj.layers)
        result["motion"] = dict(obj.motion)
        return result
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
        if obj.spacemouse_buttons:
            result["spacemouse_buttons"] = _spacemouse_buttons_dict(obj.spacemouse_buttons)
        if obj.spacemouse_layers:
            result["spacemouse_layers"] = _spacemouse_layers_dict(obj.spacemouse_layers)
        if obj.spacemouse_motion:
            result["spacemouse_motion"] = dict(obj.spacemouse_motion)
        if obj.screen:
            result["screen"] = dict(obj.screen)
        if obj.contexts:
            result["contexts"] = [_encoder(context) for context in obj.contexts]
        return result
    if isinstance(obj, ProfileContext):
        result = {"name": obj.name}
        if obj.title:
            result["title"] = obj.title
        if obj.app_modes:
            result["app_modes"] = list(obj.app_modes)
        if obj.segments is not None:
            result["segments"] = [_encoder(s) for s in obj.segments]
        if obj.spacemouse_buttons:
            result["spacemouse_buttons"] = _spacemouse_buttons_dict(obj.spacemouse_buttons)
        if obj.spacemouse_layers:
            result["spacemouse_layers"] = _spacemouse_layers_dict(obj.spacemouse_layers)
        if obj.screen:
            result["screen"] = dict(obj.screen)
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
            "script", "title", "modifiers", "axis", "command",
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
            spacemouse=_decoder(d.get("spacemouse", {}), SpaceMouseConfig),
            screen=_decoder(d.get("screen", {}), ScreenConfig),
            profiles=[_decoder(p, Profile) for p in d.get("profiles", [])],
            setup_done=bool(d.get("setup_done", True)),
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
            spacemouse_buttons=_spacemouse_buttons(d.get("spacemouse_buttons")),
            spacemouse_layers=_spacemouse_layers(d.get("spacemouse_layers")),
            spacemouse_motion=motion_rule(d.get("spacemouse_motion")),
            screen=screen_rule(d.get("screen")),
            contexts=_contexts(d.get("contexts")),
        )
    if cls is ProfileContext:
        segments = d.get("segments")
        title = d.get("title")
        modes = d.get("app_modes")
        return ProfileContext(
            name=str(d["name"]).strip()[:CONTEXT_NAME_MAX],
            title=title.strip()[:CONTEXT_TITLE_MAX] if isinstance(title, str) else "",
            app_modes=[mode.strip()[:CONTEXT_MODE_MAX] for mode in modes if isinstance(mode, str) and mode.strip()][:50]
            if isinstance(modes, list) else [],
            segments=[_decoder(s, Segment) for s in segments if isinstance(s, dict)] if isinstance(segments, list) else None,
            spacemouse_buttons=_spacemouse_buttons(d.get("spacemouse_buttons")),
            spacemouse_layers=_spacemouse_layers(d.get("spacemouse_layers")),
            screen=screen_rule(d.get("screen")),
        )
    if cls is ScreenConfig:
        if not isinstance(d, dict):
            d = {}
        pages = screen_pages(d.get("pages")) or list(SCREEN_PAGES)
        start = d.get("start_page")
        theme = d.get("theme")
        text_size = d.get("text_size")
        return ScreenConfig(
            enabled=bool(d.get("enabled", True)),
            brightness=int(_clamp(d.get("brightness", 70), 0, 100, 70)),
            dim_after_s=int(_clamp(d.get("dim_after_s", 300), 0, 86400, 300)),
            dim_brightness=int(_clamp(d.get("dim_brightness", 10), 0, 100, 10)),
            off_after_s=int(_clamp(d.get("off_after_s", 0), 0, 86400, 0)),
            wake_on_motion=bool(d.get("wake_on_motion", True)),
            pages=pages,
            start_page=start if start in pages else pages[0],
            follow_dial=bool(d.get("follow_dial", True)),
            press_feedback=bool(d.get("press_feedback", True)),
            theme=theme if theme in SCREEN_THEMES else "skin",
            text_size=text_size if text_size in SCREEN_TEXT_SIZES else "normal",
            show_long=bool(d.get("show_long", True)),
            clock_24h=bool(d.get("clock_24h", True)),
        )
    if cls is SpaceMouseConfig:
        socket = d.get("socket", "")
        return SpaceMouseConfig(
            enabled=bool(d.get("enabled", False)),
            socket=socket.strip() if isinstance(socket, str) else "",
            hold_ms=int(_clamp(d.get("hold_ms", 400), 100, 3000, 400)),
            axis_max=int(_clamp(d.get("axis_max", 400), 50, 5000, 400)),
            tilt_axes=_axis_pair(d.get("tilt_axes"), [5, 3]),
            tilt_invert=[bool(v) for v in _axis_pair(d.get("tilt_invert"), [False, False], axes=False)],
            twist_axis=int(_clamp(d.get("twist_axis", 4), 0, 7, 4)),
            twist_invert=bool(d.get("twist_invert", True)),
            twist_speed=int(_clamp(d.get("twist_speed", 360), 30, 1440, 360)),
            push_axis=int(_clamp(d.get("push_axis", 1), 0, 7, 1)),
            push_invert=bool(d.get("push_invert", True)),
            mute_others=bool(d.get("mute_others", True)),
            hide_buttons=bool(d.get("hide_buttons", False)),
            buttons=_spacemouse_buttons(d.get("buttons")),
            layers=_spacemouse_layers(d.get("layers")),
            motion=motion_rule(d.get("motion")),
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


def _axis_pair(value, default, axes=True):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return list(default)
    if not axes:
        return [bool(item) for item in value]
    return [int(_clamp(item, 0, 7, fallback)) for item, fallback in zip(value, default)]


def _spacemouse_action(values) -> SpaceMouseAction | None:
    if not isinstance(values, dict) or values.get("type") not in SPACEMOUSE_ACTION_TYPES:
        return None
    keys = values.get("keys")
    macro = values.get("macro")
    modifier = values.get("modifier")
    slot = values.get("slot")
    if values["type"] == "key_hold" and not (isinstance(keys, list) and keys):
        return None
    layer = _layer_name(values.get("layer"))
    if values["type"] == "layer" and layer is None:
        return None
    motion_name = values.get("motion")
    if values["type"] == "motion" and motion_name not in SPACEMOUSE_MOTION_ACTIONS:
        return None
    layer_mode = values.get("layer_mode")
    command = values.get("command")
    command = command.strip() if isinstance(command, str) else ""
    if values["type"] == "app" and not (command and len(command) <= APP_COMMAND_MAX):
        return None
    return SpaceMouseAction(
        type=values["type"],
        keys=[str(key) for key in keys] if isinstance(keys, list) and keys else None,
        macro=[_decoder(step, MacroStep) for step in macro if isinstance(step, dict)] if isinstance(macro, list) and macro else None,
        modifier=modifier if modifier in MODIFIER_KEYS else None,
        group=values["group"] if isinstance(values.get("group"), str) and values["group"] else None,
        function=values["function"] if isinstance(values.get("function"), str) and values["function"] else None,
        slot=slot if isinstance(slot, int) and not isinstance(slot, bool) and slot >= 0 else None,
        layer=layer if values["type"] == "layer" else None,
        layer_mode=(layer_mode if layer_mode in SPACEMOUSE_LAYER_MODES else "hold") if values["type"] == "layer" else None,
        motion=motion_name if values["type"] == "motion" else None,
        command=command if values["type"] == "app" else None,
    )


def _spacemouse_buttons(values) -> dict[str, SpaceMouseButton]:
    buttons: dict[str, SpaceMouseButton] = {}
    if not isinstance(values, dict):
        return buttons
    for key, entry in values.items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            continue
        if not 0 <= index < SPACEMOUSE_BUTTONS or not isinstance(entry, dict):
            continue
        label = entry.get("label")
        button = SpaceMouseButton(
            press=_spacemouse_action(entry.get("press")),
            hold=_spacemouse_action(entry.get("hold")),
            label=label.strip()[:SPACEMOUSE_LABEL_MAX] if isinstance(label, str) else "",
        )
        if button.press is not None or button.hold is not None or button.label:
            buttons[str(index)] = button
    return buttons


def _contexts(values) -> list[ProfileContext]:
    """The contexts of a profile; entries without a name are dropped, at most CONTEXTS_MAX."""
    if not isinstance(values, list):
        return []
    contexts = []
    for entry in values:
        if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
            continue
        contexts.append(_decoder(entry, ProfileContext))
    return contexts[:CONTEXTS_MAX]


def _spacemouse_action_dict(action: SpaceMouseAction) -> dict:
    result: dict = {"type": action.type}
    if action.keys:
        result["keys"] = list(action.keys)
    if action.macro:
        result["macro"] = [_encoder(step) for step in action.macro]
    for name in ("modifier", "group", "function", "slot"):
        if getattr(action, name) is not None:
            result[name] = getattr(action, name)
    for name in ("layer", "layer_mode", "motion", "command"):
        if getattr(action, name) is not None:
            result[name] = getattr(action, name)
    return result


def _spacemouse_buttons_dict(buttons: dict[str, SpaceMouseButton]) -> dict:
    result = {}
    for key, button in buttons.items():
        entry = {}
        if button.press is not None:
            entry["press"] = _spacemouse_action_dict(button.press)
        if button.hold is not None:
            entry["hold"] = _spacemouse_action_dict(button.hold)
        if button.label:
            entry["label"] = button.label
        if entry:
            result[str(key)] = entry
    return result


def _layer_name(value) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.strip()[:SPACEMOUSE_LAYER_NAME_MAX]
    return name or None


def _spacemouse_layers(values) -> dict[str, dict[str, SpaceMouseButton]]:
    layers: dict[str, dict[str, SpaceMouseButton]] = {}
    if not isinstance(values, dict):
        return layers
    for key, buttons in values.items():
        name = _layer_name(key)
        if name is None or len(layers) >= SPACEMOUSE_LAYERS_MAX:
            continue
        layers[name] = _spacemouse_buttons(buttons)
    return layers


def _spacemouse_layers_dict(layers: dict[str, dict[str, SpaceMouseButton]]) -> dict:
    return {name: _spacemouse_buttons_dict(buttons) for name, buttons in layers.items()}


# What the cap's motion is when nothing is overridden (spacenavd as it is).
MOTION_DEFAULTS = {
    "sensitivity": 1.0,
    "axis_sensitivity": [1.0] * 6,
    "invert": [False] * 6,
    "rotation": True,
    "translation": True,
}
MOTION_SENSITIVITY_RANGE = (0.1, 4.0)
MOTION_AXIS_RANGE = (0.0, 4.0)


def motion_rule(value) -> dict:
    """The valid keys of a cap-motion override (``sensitivity`` 0.1-4, the six
    ``axis_sensitivity`` values X Y Z RX RY RZ 0-4 where 0 locks that axis, the
    six ``invert`` flags, ``rotation`` and ``translation`` on/off); anything
    else is dropped. The values multiply/combine with spacenavd's own."""
    if not isinstance(value, dict):
        return {}
    rule: dict = {}
    if "sensitivity" in value:
        rule["sensitivity"] = round(_clamp(value["sensitivity"], *MOTION_SENSITIVITY_RANGE, 1.0), 3)
    axes = value.get("axis_sensitivity")
    if isinstance(axes, (list, tuple)) and len(axes) == 6:
        rule["axis_sensitivity"] = [round(_clamp(item, *MOTION_AXIS_RANGE, 1.0), 3) for item in axes]
    invert = value.get("invert")
    if isinstance(invert, (list, tuple)) and len(invert) == 6:
        rule["invert"] = [bool(item) for item in invert]
    for name in ("rotation", "translation"):
        if isinstance(value.get(name), bool):
            rule[name] = value[name]
    return rule


def effective_motion(*overrides) -> dict:
    """All five motion values: the defaults with each override laid over (the
    global one first, then the profile's)."""
    result = copy.deepcopy(MOTION_DEFAULTS)
    for override in overrides:
        for name, value in motion_rule(override).items():
            result[name] = value
    return result


def merge_spacemouse_layers(
    base: dict[str, dict[str, SpaceMouseButton]], over: dict[str, dict[str, SpaceMouseButton]],
) -> dict[str, dict[str, SpaceMouseButton]]:
    """Per layer, ``over``'s buttons laid over ``base``'s (merge_spacemouse_buttons)."""
    merged = {name: dict(buttons) for name, buttons in base.items()}
    for name, buttons in over.items():
        merged[name] = merge_spacemouse_buttons(merged.get(name, {}), buttons)
    return merged


def merge_spacemouse_buttons(
    base: dict[str, SpaceMouseButton], over: dict[str, SpaceMouseButton],
) -> dict[str, SpaceMouseButton]:
    """``over`` laid over ``base``: an entry with actions replaces the one below; an
    entry with neither press nor hold only names the binding below, which keeps
    its actions (with no binding below it stays label-only, i.e. unbound)."""
    merged = dict(base)
    for key, button in over.items():
        under = base.get(key)
        if button.press is None and button.hold is None and under is not None:
            merged[key] = SpaceMouseButton(press=under.press, hold=under.hold, label=button.label or under.label)
        else:
            merged[key] = button
    return merged


def spacemouse_buttons_for(config: "DialPadConfig") -> dict[str, SpaceMouseButton]:
    """The global SpaceMouse button map: the configured one when it has an entry with
    an action, else the default; label-only entries are laid over either."""
    configured = config.spacemouse.buttons
    has_actions = any(button.press is not None or button.hold is not None for button in configured.values())
    return merge_spacemouse_buttons(configured if has_actions else default_spacemouse_buttons(), configured)


def spacemouse_button_maps(config: "DialPadConfig") -> list[dict[str, SpaceMouseButton]]:
    """Every button map of the configuration: the global one and its layers, and
    each profile's (and context's, when it has them) own and their layers."""
    maps = [spacemouse_buttons_for(config), *config.spacemouse.layers.values()]
    for profile in config.profiles:
        owners = [profile, *(getattr(profile, "contexts", None) or [])]
        for owner in owners:
            maps.append(owner.spacemouse_buttons)
            maps.extend((getattr(owner, "spacemouse_layers", None) or {}).values())
    return maps


def spacemouse_macros(config: "DialPadConfig") -> list[list[MacroStep]]:
    """Every macro of the SpaceMouse buttons (global and per profile), so the
    virtual keyboard can have their keys."""
    buttons = spacemouse_button_maps(config)
    return [
        action.steps()
        for group in buttons for button in group.values()
        for action in (button.press, button.hold)
        if action is not None and action.type in ("macro", "app") and action.steps()
    ]


def spacemouse_hold_keys(config: "DialPadConfig") -> set[str]:
    """The evdev key names the ``key_hold`` actions hold."""
    buttons = spacemouse_button_maps(config)
    return {
        key for group in buttons for button in group.values()
        for action in (button.press, button.hold)
        if action is not None and action.type == "key_hold"
        for key in action.keys or []
    }


def spacemouse_modifier_keys(config: "DialPadConfig") -> set[str]:
    """The evdev key names the modifier actions hold."""
    buttons = spacemouse_button_maps(config)
    return {
        key for group in buttons for button in group.values()
        for action in (button.press, button.hold)
        if action is not None and action.type == "modifier" and action.modifier in MODIFIER_KEYS
        for key in MODIFIER_KEYS[action.modifier][:1]
    }


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


# --- Settings that travel between computers ------------------------------------------
#
# "Export all settings" writes the portable parts, "Import settings" merges them
# into the configuration of the computer it runs on and keeps everything below
# as it is. What belongs to the machine depends on its hardware:
#   device_name, geometry   the touchpad's layout and measured dial
#   activation              how this touchpad/keyboard switches the dial on
#                           (hold time, coactivator keys: tuned to its hardware)
#   backlight               the DialPad light only exists on a DialPad
#   emulation, mouse        which touchpad/mouse this computer has (modes, devices)
#   setup_done              the assistant ran here
#   spacemouse: enabled, socket, axis_max and the raw axes of tilt, twist, push
#                           (what spacenavd reports for the device on this computer)
# and travels: the profiles (with their SpaceMouse buttons, screen and dial
# look), the overlay look, the gesture feel (thresholds in degrees, hysteresis,
# timings: independent of the touchpad's resolution), the screen's look and
# pages, and the SpaceMouse behaviour (hold_ms, twist_speed, mute_others,
# hide_buttons, the global button map). User skin files are not included.
MACHINE_SECTIONS = ("version", "device_name", "geometry", "activation", "backlight", "emulation", "mouse", "setup_done")
SPACEMOUSE_MACHINE_FIELDS = (
    "enabled", "socket", "axis_max", "tilt_axes", "tilt_invert", "twist_axis", "twist_invert", "push_axis", "push_invert",
)
PORTABLE_SECTIONS = ("gesture", "overlay", "screen", "profiles")
SPACEMOUSE_PORTABLE_FIELDS = ("hold_ms", "twist_speed", "mute_others", "hide_buttons", "buttons", "layers", "motion")
SETTINGS_MARKER = "turnpad_settings"
SETTINGS_FORMAT = 1


def portable_config(cfg: DialPadConfig) -> dict:
    """The parts of ``cfg`` that travel to another computer, as plain JSON data."""
    data = _encoder(cfg)
    result = {name: data[name] for name in PORTABLE_SECTIONS}
    result["spacemouse"] = {name: data["spacemouse"][name] for name in SPACEMOUSE_PORTABLE_FIELDS}
    return result


def settings_document(cfg: DialPadConfig) -> dict:
    """What "Export all settings" writes: the portable parts and the format marker."""
    return {SETTINGS_MARKER: SETTINGS_FORMAT, **portable_config(cfg)}


def merge_portable(local: DialPadConfig, imported: "dict | DialPadConfig") -> DialPadConfig:
    """``local`` with its portable parts replaced by ``imported``'s (a document of
    ``settings_document()``/``portable_config()`` or another configuration).
    Sections the imported data lacks stay as they are; the machine's own
    settings are never touched. Raises ConfigError when a value is unusable.
    ``local`` is not changed."""
    if isinstance(imported, DialPadConfig):
        imported = portable_config(imported)
    data = copy.deepcopy(_encoder(local))
    try:
        for name in PORTABLE_SECTIONS:
            if name in imported:
                data[name] = copy.deepcopy(imported[name])
        spacemouse = imported.get("spacemouse")
        if isinstance(spacemouse, dict):
            for name in SPACEMOUSE_PORTABLE_FIELDS:
                if name in spacemouse:
                    data["spacemouse"][name] = copy.deepcopy(spacemouse[name])
        return _decoder(data, DialPadConfig)
    except KeyError as error:
        raise ConfigError(f"missing {error.args[0]!r}") from None
    except (TypeError, ValueError, AttributeError) as error:
        raise ConfigError(f"unreadable value: {error}") from None


def settings_from_text(text: str) -> dict:
    """The portable data of an exported settings file (or of a whole config.json).

    Raises ConfigError with a readable reason for anything else, including a
    profile export (which has its own import on the Apps & Profiles page).
    The values are checked when they are merged (``merge_portable``)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ConfigError(f"not valid JSON: {error.msg} at line {error.lineno}, column {error.colno}") from None
    if not isinstance(data, dict):
        raise ConfigError("the file holds no JSON object")
    if "dialpad_profiles" in data:
        raise ConfigError("this is an export of profiles: import it on the Apps & Profiles page")
    if SETTINGS_MARKER in data:
        version = data[SETTINGS_MARKER]
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ConfigError(f"unknown settings format {version!r}")
        if version > SETTINGS_FORMAT:
            raise ConfigError("these settings were exported by a newer version of Turnpad")
        return {name: data[name] for name in (*PORTABLE_SECTIONS, "spacemouse") if name in data}
    if "profiles" in data and "geometry" in data:
        # A whole config.json: take its portable parts.
        return portable_config(config_from_text(text))
    raise ConfigError("this is not a Turnpad settings file")


def describe_changes(before: DialPadConfig, after: DialPadConfig) -> list[str]:
    """What importing changes, one line per part; ``[]`` = nothing."""
    old, new = portable_config(before), portable_config(after)
    lines = []

    def key(profile):
        return profile.get("app_id") or ""

    old_profiles = {key(p): p for p in old["profiles"]}
    new_profiles = {key(p): p for p in new["profiles"]}
    added = [p["name"] for k, p in new_profiles.items() if k not in old_profiles]
    removed = [p["name"] for k, p in old_profiles.items() if k not in new_profiles]
    changed = [p["name"] for k, p in new_profiles.items() if k in old_profiles and p != old_profiles[k]]
    parts = []
    for count_names, word in ((added, "new"), (changed, "replaced"), (removed, "removed")):
        if count_names:
            parts.append(f"{len(count_names)} {word} ({', '.join(count_names[:4])}{', …' if len(count_names) > 4 else ''})")
    if parts:
        lines.append("Profiles: " + ", ".join(parts))
    for section, title in (
        ("overlay", "Dial look on the screen"),
        ("gesture", "Gesture feel"),
        ("screen", "SpaceMouse screen"),
        ("spacemouse", "SpaceMouse buttons and behaviour"),
    ):
        if old[section] != new[section]:
            lines.append(f"{title}: changed")
    return lines


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
