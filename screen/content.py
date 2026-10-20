"""What the screen shows for a configuration: the faces of the keys, the
effective settings of the active profile. Pure: no hardware, no toolkit.

A key's face says what the key does in the active application: the binding of
the global map with the profile's over it (``spacemouse_effective_buttons()``
in dialpad.py), described the way the settings app describes it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields, replace

from config.labels import GROUP_ICON, describe_macro, icon_for_title, keys_to_text, segment_icon
from config.presets import COLORS, segment_color
from config.schema import (
    SCREEN_PAGES,
    DialPadConfig,
    MacroStep,
    Profile,
    ProfileContext,
    ScreenConfig,
    Segment,
    SpaceMouseAction,
    SpaceMouseButton,
    StepType,
    all_functions,
    merge_spacemouse_buttons,
    merge_spacemouse_layers,
    spacemouse_buttons_for,
)
from spacemouse_devices import GROUP_LABELS, GROUPS, SPACEPILOT_PRO, Key, Model

KINDS = ("function", "group", "macro", "hold", "modifier", "dial", "select", "back", "layer", "motion", "app",
         "pass", "unbound")
# Colour of a face without one of its own; "" = no colour (dimmed on the screen).
KIND_COLORS = {
    "macro": COLORS["time"],
    "hold": COLORS["history"],
    "modifier": COLORS["zoom"],
    "dial": COLORS["layers"],
    "select": COLORS["navigate"],
    "back": COLORS["navigate"],
    "layer": COLORS["light"],
    "motion": COLORS["zoom"],
    "app": COLORS["brush"],
    "pass": "",
    "unbound": "",
}
PASS_TITLE = "App's own"
LAYER_MODE_WORDS = {"hold": "hold", "latch": "once", "toggle": "toggle"}
MOTION_TITLES = {"rotation": "Rotation", "translation": "Pan/Zoom", "faster": "Faster", "slower": "Slower",
                 "reset": "Reset motion"}
MOTION_ICONS = {"rotation": "object-rotate-right-symbolic", "translation": "zoom-fit-best-symbolic",
                "faster": "media-seek-forward-symbolic", "slower": "media-seek-backward-symbolic",
                "reset": "view-refresh-symbolic"}


@dataclass(frozen=True)
class KeyFace:
    """One press of a key: a ``title`` (big), a ``detail`` line (small, e.g.
    the shortcut), a ``color`` ("#rrggbb" or ""), a theme ``icon`` name and the
    ``kind`` of the action."""

    title: str = ""
    detail: str = ""
    color: str = ""
    icon: str = ""
    kind: str = "unbound"
    # Shown while a layer is active: the face comes from the base map, the layer does not override it.
    inherited: bool = False

    @property
    def bound(self) -> bool:
        return self.kind not in ("pass", "unbound")


UNBOUND = KeyFace()


@dataclass(frozen=True)
class KeyEntry:
    """A physical key with its faces: ``short`` (index) and ``long`` (the
    device's own long press, or the hold action of a plain key; None = none)."""

    key: Key
    short: KeyFace
    long: KeyFace | None = None

    @property
    def index(self) -> int:
        return self.key.index

    @property
    def long_index(self) -> int | None:
        return self.key.long_index


@dataclass
class EffectiveScreen(ScreenConfig):
    """The ``screen`` section with a profile's overrides applied."""

    accent: str = ""


# --- profiles ---------------------------------------------------------------

def default_profile(config: DialPadConfig) -> Profile | None:
    """The profile without an application (the dial's default menu), else the first one."""
    for profile in config.profiles:
        if profile.app_id is None:
            return profile
    return config.profiles[0] if config.profiles else None


def find_profile(config: DialPadConfig, name: str | None) -> Profile:
    """The profile called ``name``; "" or an unknown name gives the default
    profile (an empty one when the configuration has none)."""
    if name:
        for profile in config.profiles:
            if profile.name == name:
                return profile
    return default_profile(config) or Profile(name="Default")


def find_context(profile: Profile, name: str | None) -> ProfileContext | None:
    """The context of ``profile`` called ``name`` (DriverState ``context``), else None."""
    if name:
        for context in getattr(profile, "contexts", None) or []:
            if context.name == name:
                return context
    return None


def effective_profile(profile: Profile, context: ProfileContext | None = None) -> Profile:
    """``profile`` as the context in force changes it: the context's dial menu
    (when it has its own), its buttons and layers over the profile's, its screen
    overrides over the profile's. The name stays the profile's."""
    if context is None:
        return profile
    derived = replace(profile)
    if context.segments is not None:
        derived.segments = context.segments
    derived.spacemouse_buttons = merge_spacemouse_buttons(profile.spacemouse_buttons or {},
                                                          context.spacemouse_buttons or {})
    derived.spacemouse_layers = merge_spacemouse_layers(getattr(profile, "spacemouse_layers", None) or {},
                                                        context.spacemouse_layers or {})
    derived.screen = {**(profile.screen if isinstance(profile.screen, dict) else {}),
                      **(context.screen if isinstance(context.screen, dict) else {})}
    return derived


def menu_segments(config: DialPadConfig, profile: Profile) -> list[Segment]:
    """The entries of the dial's menu, in order: what ``function`` slots count.
    Groups without an enabled function are left off. A profile without enabled
    functions (it exists for its buttons) shows the default profile's menu."""
    def entries(owner):
        return [s for s in owner.active_segments() if not s.is_group or s.active_items()]

    found = entries(profile)
    if found:
        return found
    base = default_profile(config)
    if base is not None and base is not profile:
        found = entries(base)
    if not found:
        found = next((e for p in config.profiles if (e := entries(p))), [])
    return found


def effective_screen(config: DialPadConfig, profile: Profile | None = None) -> EffectiveScreen:
    """``config.screen`` with the profile's pages, start page and accent."""
    values = {item.name: getattr(config.screen, item.name) for item in fields(ScreenConfig)}
    values["pages"] = list(values["pages"])
    rule = profile.screen if profile is not None and isinstance(profile.screen, dict) else {}
    if rule.get("pages"):
        values["pages"] = [page for page in rule["pages"] if page in SCREEN_PAGES]
    if not values["pages"]:
        values["pages"] = list(SCREEN_PAGES)
    if rule.get("start_page") in values["pages"]:
        values["start_page"] = rule["start_page"]
    if values["start_page"] not in values["pages"]:
        values["start_page"] = values["pages"][0]
    return EffectiveScreen(accent=str(rule.get("accent") or ""), **values)


def effective_buttons(config: DialPadConfig, profile: Profile) -> dict[str, SpaceMouseButton]:
    """The map in force: the global one with the profile's buttons over it (a
    label-only entry only names the binding below it)."""
    return merge_spacemouse_buttons(spacemouse_buttons_for(config), profile.spacemouse_buttons or {})


def layer_map(config: DialPadConfig, profile: Profile, layer: str) -> dict[str, SpaceMouseButton]:
    """What ``layer`` binds: the global layer with the profile's (or the
    context's, when ``profile`` is an effective profile) laid over it."""
    layers = merge_spacemouse_layers(config.spacemouse.layers or {}, getattr(profile, "spacemouse_layers", None) or {})
    return layers.get(layer, {})


def layer_overrides(buttons: dict[str, SpaceMouseButton]) -> set[str]:
    """The buttons a layer map really binds (a label-only entry only names)."""
    return {key for key, button in buttons.items() if button.press is not None or button.hold is not None}


# --- app commands -------------------------------------------------------------

def catalog_index(data) -> dict[str, dict]:
    """GetAppCommands' answer (a dict or its JSON) as {command id: entry}."""
    if isinstance(data, (str, bytes)):
        import json

        try:
            data = json.loads(data)
        except ValueError:
            return {}
    commands = data.get("commands") if isinstance(data, dict) else None
    index = {}
    for entry in commands if isinstance(commands, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"]:
            index[entry["id"]] = entry
    return index


def prettify_command(command: str) -> str:
    """A command id as words when the catalog is unknown: "mesh.extrude_region_move" -> "Extrude region move"."""
    word = str(command or "").rsplit(".", 1)[-1].replace("_", " ").strip()
    return word[:1].upper() + word[1:] if word else "Command"


# --- the driver's state -------------------------------------------------------

def motion_state(driver: dict | None) -> dict | None:
    """DriverState ``motion`` as {sensitivity, rotation, translation}, None when it has none."""
    motion = (driver or {}).get("motion")
    if not isinstance(motion, dict) or not motion:
        return None
    try:
        sensitivity = float(motion.get("sensitivity", 1.0))
    except (TypeError, ValueError):
        sensitivity = 1.0
    return {"sensitivity": sensitivity, "rotation": bool(motion.get("rotation", True)),
            "translation": bool(motion.get("translation", True))}


def format_speed(value: float) -> str:
    """"×1.5" (two decimals at most, no trailing zeros)."""
    return "×" + f"{value:.2f}".rstrip("0").rstrip(".")


@dataclass(frozen=True)
class Chip:
    """A label of the top bar: ``kind`` is layer, context, mode or motion."""

    text: str
    kind: str


def bar_chips(driver: dict | None) -> list[Chip]:
    """The chips the top bar adds to the profile's name, most important first:
    the active layer, the context in force (or the linked app's mode label, and
    that label too when it differs), motion that is not at its default."""
    driver = driver or {}
    chips = []
    layer = str(driver.get("layer") or "")
    if layer:
        chips.append(Chip(layer, "layer"))
    context = str(driver.get("context") or "")
    app_context = driver.get("app_context") if isinstance(driver.get("app_context"), dict) else {}
    label = str(app_context.get("label") or "")
    if context:
        chips.append(Chip(context, "context"))
    if label and label != context:
        chips.append(Chip(label, "mode"))
    motion = motion_state(driver)
    if motion is not None:
        if not motion["rotation"]:
            chips.append(Chip("ROT off", "motion"))
        if not motion["translation"]:
            chips.append(Chip("PAN off", "motion"))
        if abs(motion["sensitivity"] - 1.0) > 0.005:
            chips.append(Chip(format_speed(motion["sensitivity"]), "motion"))
    return chips


@dataclass(frozen=True)
class Notice:
    """A message for the screen's toast."""

    title: str
    detail: str = ""
    kind: str = ""


def change_notice(old: dict | None, new: dict | None) -> Notice | None:
    """What a new DriverState tells that deserves a toast, compared with the
    previous one: a notice of the add-on or the driver, a ready profile on offer,
    a change of the cap's motion. Nothing for the first state (``old`` empty) and
    no motion toast when the application or profile changed (the motion is then
    that application's own)."""
    if not old or not new:
        return None
    old_notice = old.get("notice") if isinstance(old.get("notice"), dict) else {}
    notice = new.get("notice") if isinstance(new.get("notice"), dict) else {}
    if notice.get("text") and notice.get("id") != old_notice.get("id"):
        return Notice(str(notice["text"]), "", "notice")
    suggest = str(new.get("suggest") or "")
    if suggest and suggest != str(old.get("suggest") or ""):
        return Notice(f"Ready profile for {suggest} — check the notification", "", "suggest")
    before, after = motion_state(old), motion_state(new)
    if before is None or after is None:
        return None
    if any(old.get(name) != new.get(name) for name in ("profile", "app", "context")):
        return None
    parts = []
    for name, word in (("rotation", "Rotation"), ("translation", "Pan/Zoom")):
        if before[name] != after[name]:
            parts.append(f"{word} {'on' if after[name] else 'off'}")
    if abs(before["sensitivity"] - after["sensitivity"]) > 0.005:
        parts.append(f"Speed {format_speed(after['sensitivity'])}")
    return Notice(" · ".join(parts), "", "motion") if parts else None


# --- faces ------------------------------------------------------------------

def _shortcut(steps: list[MacroStep]) -> str:
    """The first keystroke of a macro as typed shortcut text ("Ctrl+Z"), or ""."""
    for step in steps:
        if step.type is StepType.KEYSTROKE and step.keys:
            return "+".join(re.sub(r"^Kp(\w+)$", r"Num \1", part) for part in keys_to_text(step.keys).split("+"))
    return ""


def _macro_face(action: SpaceMouseAction, label: str) -> KeyFace:
    steps = action.steps()
    if not steps:
        return KeyFace(label or "Empty", "", KIND_COLORS["macro"], "input-keyboard-symbolic", "macro")
    shortcut = _shortcut(steps)
    named = next((step.title for step in steps if step.title), "")
    described = named or describe_macro(steps) or "Macro"
    if label:
        title, detail = label, shortcut or described
    elif named and shortcut:
        title, detail = named, shortcut
    else:
        title, detail = described, ""
    return KeyFace(title, detail, KIND_COLORS["macro"], "input-keyboard-symbolic", "macro")


def _find_function(config: DialPadConfig, profile: Profile, title: str) -> Segment | None:
    for segment in menu_segments(config, profile):
        if segment.title == title:
            return segment
    for _group, item in all_functions(menu_segments(config, profile)):
        if item.title == title:
            return item
    return None


def _segment_face(segment: Segment, color: str, label: str) -> KeyFace:
    if segment.is_group:
        count = len(segment.active_items())
        return KeyFace(label or segment.title, f"{count} functions" if not label else segment.title,
                       color, segment_icon(segment) or GROUP_ICON, "group")
    detail = describe_macro(segment.macro_cw) or describe_macro(segment.macro_center)
    if label:
        detail = segment.title
    return KeyFace(label or segment.title, detail, color, segment_icon(segment), "function")


def _motion_face(action: SpaceMouseAction, label: str, motion: dict | None) -> KeyFace:
    which = action.motion or ""
    title = label or MOTION_TITLES.get(which, "Motion")
    icon = MOTION_ICONS.get(which, "")
    if which in ("rotation", "translation"):
        if motion is None:
            return KeyFace(title, "on / off", KIND_COLORS["motion"], icon, "motion")
        on = motion[which]
        return KeyFace(title, "on" if on else "off", KIND_COLORS["motion"] if on else "", icon, "motion")
    detail = format_speed(motion["sensitivity"]) if motion is not None and which in ("faster", "slower") else ""
    return KeyFace(title, detail, KIND_COLORS["motion"], icon, "motion")


def _app_face(action: SpaceMouseAction, label: str, commands: dict | None) -> KeyFace:
    entry = (commands or {}).get(action.command or "")
    named = str(entry.get("title") or "") if isinstance(entry, dict) else ""
    title = label or named or prettify_command(action.command or "")
    detail = keys_to_text(action.keys) if action.keys else ""
    if not detail and isinstance(entry, dict):
        detail = str(entry.get("keys") or "")
    if label and not detail:
        detail = named or ""
    return KeyFace(title, detail, KIND_COLORS["app"], "system-run-symbolic", "app")


def face_for_action(action: SpaceMouseAction | None, config: DialPadConfig, profile: Profile, label: str = "",
                    *, commands: dict | None = None, motion: dict | None = None) -> KeyFace:
    """The face of one action; ``label`` replaces the automatic title.
    ``commands`` is the focused application's catalog (``catalog_index()``),
    ``motion`` the cap's motion (``motion_state()``): app and motion faces use them."""
    if action is None:
        return UNBOUND if not label else KeyFace(label)
    kind = action.type
    if kind == "none":
        return KeyFace(label or PASS_TITLE, "", "", "", "pass")
    if kind == "macro":
        return _macro_face(action, label)
    if kind == "key_hold":
        text = keys_to_text(action.keys) or "keys"
        return KeyFace(label or f"Hold {text}", text if label else "", KIND_COLORS["hold"], "input-keyboard-symbolic", "hold")
    if kind == "modifier":
        return KeyFace(label or str(action.modifier or "Modifier"), "", KIND_COLORS["modifier"], "", "modifier")
    if kind == "dial":
        return KeyFace(label or "Dial", "", KIND_COLORS["dial"], "input-dialpad-symbolic", "dial")
    if kind == "select":
        return KeyFace(label or "Select", "", KIND_COLORS["select"], "object-select-symbolic", "select")
    if kind == "back":
        return KeyFace(label or "Back", "", KIND_COLORS["back"], "go-previous-symbolic", "back")
    if kind == "layer":
        mode = LAYER_MODE_WORDS.get(action.layer_mode or "hold", "hold")
        return KeyFace(label or f"Layer: {action.layer or '?'}", mode if not label else f"{action.layer or '?'} · {mode}",
                       KIND_COLORS["layer"], "view-paged-symbolic", "layer")
    if kind == "motion":
        return _motion_face(action, label, motion)
    if kind == "app":
        return _app_face(action, label, commands)
    if kind == "function":
        if action.function:
            segment = _find_function(config, profile, action.function)
            if segment is None:
                return KeyFace(label or action.function, "", "", icon_for_title(action.function), "function")
            segments = menu_segments(config, profile)
            index = segments.index(segment) if segment in segments else 0
            return _segment_face(segment, segment_color(segment, index), label)
        if profile.mode == "single":
            return UNBOUND  # the driver cannot enter a function of a single-function profile
        segments = menu_segments(config, profile)
        slot = action.slot if action.slot is not None else -1
        if not 0 <= slot < len(segments):
            return KeyFace(label, "", "", "", "unbound") if label else UNBOUND
        segment = segments[slot]
        return _segment_face(segment, segment_color(segment, slot), label)
    if kind == "group":
        segments = menu_segments(config, profile)
        groups = [(index, s) for index, s in enumerate(segments) if s.is_group]
        pick = next(((i, s) for i, s in groups if s.title == action.group), None) if action.group else (groups[0] if groups else None)
        if pick is None:
            return KeyFace(label or (action.group or "Group"), "", "", GROUP_ICON, "group")
        return _segment_face(pick[1], segment_color(pick[1], pick[0]), label)
    return UNBOUND


def _face(button: SpaceMouseButton | None, config, profile, use_hold=False, **extra) -> KeyFace | None:
    if button is None:
        return None
    if use_hold:
        return face_for_action(button.hold, config, profile, **extra) if button.hold is not None else None
    if button.press is None:
        # A key bound by its hold action only still shows something.
        return face_for_action(None, config, profile, button.label, **extra)
    return face_for_action(button.press, config, profile, button.label, **extra)


def key_entries(config: DialPadConfig, profile: Profile, model: Model = SPACEPILOT_PRO, *, layer: str = "",
                commands: dict | None = None, motion: dict | None = None) -> list[KeyEntry]:
    """Every physical key of the model with the faces it has in ``profile``.

    With a ``layer`` the faces are those of the base map with the layer's map
    laid over it; faces the layer does not bind are ``inherited``."""
    buttons = effective_buttons(config, profile)
    own = None
    if layer:
        over = layer_map(config, profile, layer)
        buttons = merge_spacemouse_buttons(buttons, over)
        own = layer_overrides(over)
    extra = {"commands": commands, "motion": motion}
    entries = []
    palette = list(COLORS.values())

    def face_of(index: int, use_hold: bool = False) -> KeyFace | None:
        face = _face(buttons.get(str(index)), config, profile, use_hold, **extra)
        if face is not None and own is not None and str(index) not in own:
            face = replace(face, inherited=True)
        return face

    for key in model.keys:
        short = face_of(key.index) or UNBOUND
        long = None
        if key.long_index is not None:
            long = face_of(key.long_index) or UNBOUND
        else:
            long = face_of(key.index, use_hold=True)
        if key.group == "function":
            # Macros and held keys have no colour of their own: one per key, so the rows tell apart.
            color = palette[(int(key.id) - 1) * 2 % len(palette)]
            short, long = (replace(face, color=color) if face is not None and face.kind in ("macro", "hold") else face
                           for face in (short, long))
        entries.append(KeyEntry(key, short, long))
    return entries


def function_entries(entries: list[KeyEntry]) -> list[KeyEntry]:
    """The function keys 1 to 5, the ones the keys page lists."""
    return [entry for entry in entries if entry.key.group == "function"]


def face_buttons(entries: list[KeyEntry], show_long: bool = True) -> list[int]:
    """Button indexes of the keys page's faces in highlight order: each row's
    short face, then its long one."""
    indexes = []
    for entry in function_entries(entries):
        indexes.append(entry.index)
        if show_long and entry.long_index is not None:
            indexes.append(entry.long_index)
    return indexes


def entry_for_index(entries: list[KeyEntry], index: int) -> tuple[KeyEntry, KeyFace | None] | None:
    """(entry, face) of a raw button index, the long face for a long index."""
    for entry in entries:
        if entry.index == index:
            return entry, entry.short
        if entry.long_index is not None and entry.long_index == index:
            return entry, entry.long
    return None


def grouped_entries(entries: list[KeyEntry], skip=("function",)) -> list[tuple[str, list[KeyEntry]]]:
    """(group title, entries) in display order, without the groups in ``skip``."""
    result = []
    for group in GROUPS:
        if group in skip:
            continue
        members = [entry for entry in entries if entry.key.group == group]
        if members:
            result.append((GROUP_LABELS.get(group, group.title()), members))
    return result


# --- everything the screen shows follows the driver --------------------------

@dataclass
class Resolved:
    """What the driver's state makes of the configuration: the profile in force
    (context applied, the profile's name), the context, the screen settings, the
    layer and the key faces."""

    base: Profile
    profile: Profile
    context: ProfileContext | None
    settings: EffectiveScreen
    layer: str
    entries: list


def resolve(config: DialPadConfig, driver: dict | None = None, commands: dict | None = None,
            model: Model = SPACEPILOT_PRO) -> Resolved:
    """Profile, context, layer and faces for a DriverState ``driver`` (a dict, may be empty)."""
    driver = driver or {}
    base = find_profile(config, str(driver.get("profile") or ""))
    context = find_context(base, str(driver.get("context") or ""))
    profile = effective_profile(base, context)
    layer = str(driver.get("layer") or "")
    return Resolved(
        base, profile, context, effective_screen(config, profile), layer,
        key_entries(config, profile, model, layer=layer, commands=commands, motion=motion_state(driver)),
    )


def resolution_key(config: DialPadConfig, driver: dict | None, commands_version: int = 0) -> tuple:
    """What decides the faces: when it stays the same, so do ``resolve()``'s results."""
    driver = driver or {}
    motion = motion_state(driver)
    return (
        find_profile(config, str(driver.get("profile") or "")).name, str(driver.get("context") or ""),
        str(driver.get("layer") or ""), tuple(sorted(motion.items())) if motion else (),
        str(driver.get("app") or ""), commands_version,
    )
