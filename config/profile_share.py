"""Profiles as text: copy one out, paste one back, or let an AI assistant
write one. The skin editor does the same for skins (config.skins.ai_prompt).
"""

from __future__ import annotations

import json
import re

from config.labels import parse_keys
from config.schema import SPACEMOUSE_LAYER_MODES, SPACEMOUSE_LAYERS_MAX, SPACEMOUSE_MOTION_ACTIONS
from config.schema import APP_COMMAND_MAX, CONTEXT_MODE_MAX, CONTEXT_NAME_MAX, CONTEXTS_MAX, context_title_error
from config.schema import MACRO_FIELDS, MODIFIER_KEYS, SCREEN_PAGES, SCROLL_AXES, SPACEMOUSE_ACTION_TYPES, MacroStep, Profile, Segment, StepType, _decoder, _encoder

PROFILE_FORMAT = """A Turnpad profile is one JSON object:
- "name": shown in the settings app.
- "app_id": optional; the .desktop id of the application it is for, e.g. "org.gimp.GIMP.desktop" or "firefox.desktop". Leave it out for a default profile.
- "mode": "multi" (radial menu of functions) or "single" (the dial always runs the function named in "single_function").
- "sensitivity" and "scroll_speed": 0.25 to 4 (1 = normal); "remember_last": true reopens the last used function.
- "overlay": optional; how the on-screen dial shows for this profile, overriding the user's defaults: "position" ("top", "center", "lower", "bottom", "custom" with "custom_x" and "custom_y", the dial's centre as fractions 0 to 1 of the screen, or "pointer": next to the mouse pointer, never covering it, on "pointer_side" ("below-right", "below-left", "above-right", "above-left", "right", "left", "above" or "below") "pointer_gap" pixels away, 0 to 400, default 24), "visibility" ("full"; "fade": fades to "fade_opacity" percent while turning; "pill": a small label instead of the dial). Leave it out unless asked.
- "spacemouse_buttons": optional; what the buttons of a 3Dconnexion SpaceMouse do while this application is focused: {"<button number>": {"press": ACTION, "hold": ACTION, "label": "text for the SpaceMouse screen"}} with all three optional ("label", up to 40 characters, replaces the automatic description on a device screen; an entry with only a "label" just names the existing binding of that button and keeps its actions), ACTION being {"type": "macro", "keys": ["KEY_LEFTCTRL", "KEY_Z"]} (or "macro": steps like below), {"type": "modifier", "modifier": "Ctrl"} (Ctrl, Shift or Alt held while the button is down; press only), {"type": "key_hold", "keys": ["KEY_Z"]} (any keys held while the button is down, as press or as hold; e.g. a pie menu that opens while a key is held), {"type": "none"} (do nothing, leave the button to the application; overrides a global binding), {"type": "dial"}, {"type": "group", "group": "<group title>"} (optional title), {"type": "function", "function": "<title>"} (or "slot": menu place from 0), {"type": "select"}, {"type": "back"}, {"type": "app", "command": "<command id>", "keys": ["KEY_E"]} (runs a command of the focused application through its Turnpad add-on, e.g. Blender's "mesh.extrude_region_move"; "keys" is the shortcut pressed instead when the add-on is not connected), {"type": "layer", "layer": "<layer name>", "layer_mode": "hold"} (the other buttons use the layer's bindings: "hold" while this button is down, "latch" for the next button press only, "toggle" until pressed again) or {"type": "motion", "motion": "rotation"} (the cap's motion for other programs: "rotation" and "translation" switch that part off and on, "faster" and "slower" step the sensitivity, "reset" undoes it). Button numbers are the raw ones of the device; on a SpacePilot Pro: 0 Menu, 1 Fit, 2 Top (long press 6), 4 Right (3), 5 Front (7), 8 Roll (9), 10 ISO (11), 12-16 function keys 1-5 (a short press; the device itself sends 17-21 for a long press, so give those a "press" action only), 22 Esc, 23 Alt, 24 Shift, 25 Ctrl, 26 Rotation lock, 27 Pan/Zoom, 28 Dominant, 29 +, 30 -. Leave it out unless asked.
- "spacemouse_layers": optional; {"<layer name>": {"<button number>": {"press": ACTION, "hold": ACTION, "label": "..."}}} with the same buttons as "spacemouse_buttons": the bindings that replace the base ones while the layer is active (a layer action activates it). Leave it out unless asked.
- "spacemouse_motion": optional; how the cap's motion reaches other programs in this application, through spacenavd: "sensitivity" (0.1 to 4, 1 = as configured), "axis_sensitivity" (six numbers 0 to 4 for X, Y, Z, RX, RY, RZ; 0 locks an axis), "invert" (six true/false flags), "rotation" and "translation" (false switches that part off). Leave it out unless asked.
- "screen": optional; overrides for the screen of a SpaceMouse that has one, while this application is focused: "pages" (the pages shown, in order: "keys", "dial", "device", "motion", "clock"), "start_page" (one of them) and "accent" ("#rrggbb"). Leave it out unless asked.
- "contexts": optional; variants of this profile for a window title or a mode of the application, the first one that matches wins, otherwise the profile itself is in force: [{"name": "Edit mode", "title": "plain text, case-insensitive, contained in the window's title; several alternatives separated by | (any one matches); not a regular expression", "app_modes": ["EDIT_MESH"], "segments": [...], "spacemouse_buttons": {...}, "spacemouse_layers": {...}, "screen": {...}}]. Give "title" and/or "app_modes" (a context with neither never applies; with both, both must hold). "app_modes" are the modes a linked application reports (the Blender add-on: OBJECT, EDIT_MESH, SCULPT, POSE, PAINT_WEIGHT...). "segments" is the dial menu in that context (same fields as below; leave it out to keep the profile's menu); "spacemouse_buttons" and "spacemouse_layers" are laid over the profile's, button by button; "screen" over the profile's. Leave it out unless asked.
- "segments": the functions, clockwise from the top of the dial; 3 to 8 work best. Each one has:
  - "title": short and unique in the profile, shown on the dial.
  - "color": optional "#rrggbb".
  - "icon_url": optional GNOME symbolic icon name such as "edit-undo-symbolic"; without it the icon follows the title.
  - "treshold": optional degrees of rotation per step (default 90; 15 to 30 for fine control such as zoom or brush size).
  - "macro_cw" and "macro_ccw": steps run once per rotation step clockwise and counter-clockwise.
  - "macro_center": steps for a tap on the centre. A function with only "macro_center" is a launcher: choosing it in the menu runs it at once.
  - "value_command": optional shell command that prints a number shown in the centre; "unit" such as "%" (with % the dial draws a gauge).
  - "overlay": optional, like the profile's, while this function is selected; e.g. {"visibility": "pill"} keeps the view clear while zooming.
  - "items": optional; makes the function a group (a folder): choosing it opens a straight list of these functions beside the dial; turning moves the highlight, a tap on the centre chooses, holding it goes back. Items have the same fields but no "items" of their own (one level), and the group has no steps itself, only "title", "color", "icon_url", "enabled" and "overlay". Use a group when a profile needs more than about 8 functions.
  - "with_modifier": optional; other steps while a keyboard key is held, by key: {"Ctrl": {...}, "Shift": {...}, "Alt": {...}}, each with its own "macro_cw", "macro_ccw" and "macro_center". The held key stays pressed, so the application sees it with the steps (Ctrl + a scroll step zooms). Leave it out unless asked.

Steps run from top to bottom. Each is {"type": ...} with an optional "title", a short label shown as a hint below the dial ("Zoom in"):
- "keystroke": "keys" pressed together, modifiers first, as evdev names, e.g. ["KEY_LEFTCTRL", "KEY_Z"]. Names: KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_LEFTALT, KEY_LEFTMETA (Super), KEY_A to KEY_Z, KEY_0 to KEY_9, KEY_F1 to KEY_F24, KEY_LEFT, KEY_RIGHT, KEY_UP, KEY_DOWN, KEY_PAGEUP, KEY_PAGEDOWN, KEY_HOME, KEY_END, KEY_ENTER, KEY_ESC, KEY_TAB, KEY_SPACE, KEY_BACKSPACE, KEY_DELETE, KEY_LEFTBRACE ([), KEY_RIGHTBRACE (]), KEY_MINUS, KEY_EQUAL, KEY_COMMA, KEY_DOT, KEY_SLASH. They are key positions on a US keyboard.
- "media": "keys" is one of ["KEY_PLAYPAUSE"], ["KEY_NEXTSONG"], ["KEY_PREVIOUSSONG"], ["KEY_STOPCD"], ["KEY_MUTE"], ["KEY_MICMUTE"].
- "volume" and "brightness": "direction" "up" or "down", "value" key presses per step (usually 1).
- "scroll": "direction" "clockwise" (up or right) or "counterclockwise" (down or left), "value" 120 per wheel notch, optional "axis": "horizontal", optional "modifiers": keys held while scrolling, e.g. ["KEY_LEFTCTRL"] zooms in most applications.
- "shell": "script" is a shell command run as the user, e.g. "gnome-calculator". Prefer keys when the application has a shortcut.
- "app": "command" is the id of a command of the focused application, run through its Turnpad add-on (e.g. Blender's "view3d.view_selected" style ids); optional "keys" as in a keystroke are pressed instead when the add-on is not connected. Prefer it over keys where the application has no stable shortcut.
- "delay": "duration" in seconds between two steps."""


class ProfileTextError(ValueError):
    """Pasted text that is not a usable profile; the message says why."""


def profile_json(profile: Profile) -> str:
    return json.dumps(_encoder(profile), indent=2)


def ai_prompt(profile: Profile) -> str:
    """Instructions an assistant needs to change or build a profile."""
    return (
        "Build a profile for Turnpad, the ASUS DialPad driver for Linux: a touch dial on the "
        "touchpad that runs keyboard shortcuts, scrolling and commands from a radial menu.\n\n"
        + PROFILE_FORMAT
        + "\n\nUse the application's real default keyboard shortcuts. Reply with only the JSON "
        "object. Start from this profile and change it as I describe:\n\n"
        + profile_json(profile)
        + "\n\nMy idea: "
    )


def _looks_like_profiles(value) -> bool:
    if isinstance(value, dict):
        return "segments" in value or "profiles" in value
    return isinstance(value, list) and bool(value) and all(isinstance(item, dict) for item in value)


def _first_json(text: str):
    # Assistants wrap JSON in prose or ``` fences, and the prose may quote
    # small JSON bits such as a key list; take the first profile-like value.
    first = None
    for match in re.finditer(r"[\[{]", text):
        try:
            value, _end = json.JSONDecoder().raw_decode(text, match.start())
        except ValueError:
            continue
        if _looks_like_profiles(value):
            return value
        if first is None and isinstance(value, dict):
            first = value
    if first is None:
        raise ProfileTextError("No JSON found. Copy the assistant's whole answer.")
    return first


def _keys(names, where: str) -> list[str]:
    if not isinstance(names, list) or not names:
        raise ProfileTextError(f"{where}: \"keys\" must be a list of key names.")
    keys, unknown = parse_keys(" ".join(str(name) for name in names))
    if unknown:
        raise ProfileTextError(f"{where}: unknown key {', '.join(unknown)}.")
    return keys


def _check_step(step: MacroStep, where: str) -> None:
    if step.type in (StepType.KEYSTROKE, StepType.MEDIA):
        step.keys = _keys(step.keys, where)
    elif step.type is StepType.SCROLL:
        if step.axis not in (None, *SCROLL_AXES):
            raise ProfileTextError(f"{where}: \"axis\" must be \"vertical\" or \"horizontal\".")
        if step.modifiers:
            step.modifiers = _keys(step.modifiers, where)
    elif step.type is StepType.SHELL and not (step.script or "").strip():
        raise ProfileTextError(f"{where}: a shell step needs a \"script\".")
    elif step.type is StepType.COMMAND and not (step.program or "").strip():
        raise ProfileTextError(f"{where}: a command step needs a \"program\".")
    elif step.type is StepType.APP:
        _check_app_command(step.command, where)
        if step.keys:
            step.keys = _keys(step.keys, where)


def _check_app_command(command, where: str) -> None:
    """The id of a command of the linked application (an app step or action)."""
    if not (isinstance(command, str) and command.strip() and len(command.strip()) <= APP_COMMAND_MAX):
        raise ProfileTextError(f"{where}: an app step needs the application's command id in \"command\".")


def _check_spacemouse(buttons, where: str) -> None:
    """The raw ``spacemouse_buttons``: known action types, key_hold with keys."""
    if buttons is None:
        return
    if not isinstance(buttons, dict):
        raise ProfileTextError(f"{where}: \"spacemouse_buttons\" must map button numbers to press and hold actions.")
    for number, entry in buttons.items():
        label = f"{where}, SpaceMouse button {number}"
        if not isinstance(entry, dict):
            raise ProfileTextError(f"{label}: expected {{\"press\": ..., \"hold\": ...}}.")
        if entry.get("label") is not None and not isinstance(entry["label"], str):
            raise ProfileTextError(f"{label}: \"label\" must be text.")
        for slot in ("press", "hold"):
            action = entry.get(slot)
            if action is None:
                continue
            kind = action.get("type") if isinstance(action, dict) else None
            if kind not in SPACEMOUSE_ACTION_TYPES:
                raise ProfileTextError(f"{label}: unknown {slot} action {kind!r}; use " + ", ".join(SPACEMOUSE_ACTION_TYPES) + ".")
            if kind == "key_hold" and not (isinstance(action.get("keys"), list) and action["keys"]):
                raise ProfileTextError(f"{label}: a key_hold action needs \"keys\".")
            if kind == "layer":
                if not (isinstance(action.get("layer"), str) and action["layer"].strip()):
                    raise ProfileTextError(f"{label}: a layer action needs the \"layer\" name.")
                if action.get("layer_mode") not in (None, *SPACEMOUSE_LAYER_MODES):
                    raise ProfileTextError(f"{label}: \"layer_mode\" must be " + ", ".join(SPACEMOUSE_LAYER_MODES) + ".")
            if kind == "app":
                _check_app_command(action.get("command"), label)
            if kind == "motion" and action.get("motion") not in SPACEMOUSE_MOTION_ACTIONS:
                raise ProfileTextError(f"{label}: a motion action needs \"motion\": " + ", ".join(SPACEMOUSE_MOTION_ACTIONS) + ".")


def _check_spacemouse_layers(layers, where: str) -> None:
    """The raw ``spacemouse_layers``: layer name -> a button map like ``spacemouse_buttons``."""
    if layers is None:
        return
    if not isinstance(layers, dict) or len(layers) > SPACEMOUSE_LAYERS_MAX:
        raise ProfileTextError(f"{where}: \"spacemouse_layers\" must map layer names to button maps (at most {SPACEMOUSE_LAYERS_MAX}).")
    for name, buttons in layers.items():
        _check_spacemouse(buttons, f"{where}, layer {name!r}")


def _check_motion(motion, where: str) -> None:
    """The raw ``spacemouse_motion``: an object; lists of six numbers/flags."""
    if motion is None:
        return
    if not isinstance(motion, dict):
        raise ProfileTextError(f"{where}: \"spacemouse_motion\" must be an object.")
    for name in ("axis_sensitivity", "invert"):
        if name in motion and not (isinstance(motion[name], list) and len(motion[name]) == 6):
            raise ProfileTextError(f"{where}: motion \"{name}\" must be a list of six values (X Y Z RX RY RZ).")


def _check_screen(screen, where: str) -> None:
    """The raw profile ``screen``: known pages, a #rrggbb accent."""
    if screen is None:
        return
    if not isinstance(screen, dict):
        raise ProfileTextError(f"{where}: \"screen\" must be an object with \"pages\", \"start_page\" and \"accent\".")
    pages = screen.get("pages")
    if pages is not None and not (isinstance(pages, list) and all(page in SCREEN_PAGES for page in pages)):
        raise ProfileTextError(f"{where}: screen \"pages\" must be a list of " + ", ".join(SCREEN_PAGES) + ".")
    if screen.get("start_page") is not None and screen["start_page"] not in SCREEN_PAGES:
        raise ProfileTextError(f"{where}: screen \"start_page\" must be one of " + ", ".join(SCREEN_PAGES) + ".")
    accent = screen.get("accent")
    if accent is not None and not (isinstance(accent, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", accent.strip())):
        raise ProfileTextError(f"{where}: screen \"accent\" must be a colour like \"#3584e4\".")


def _check_contexts(contexts, where: str) -> None:
    """The raw ``contexts`` of a profile."""
    if contexts is None:
        return
    if not isinstance(contexts, list) or len(contexts) > CONTEXTS_MAX:
        raise ProfileTextError(f"{where}: \"contexts\" must be a list of at most {CONTEXTS_MAX} contexts.")
    for number, context in enumerate(contexts, 1):
        label = f"{where}, context {number}"
        if not isinstance(context, dict) or not str(context.get("name") or "").strip():
            raise ProfileTextError(f"{label} has no \"name\".")
        label = f"{where}, context “{str(context['name']).strip()[:CONTEXT_NAME_MAX]}”"
        title = context.get("title")
        if title is not None:
            error = context_title_error(title)
            if error:
                raise ProfileTextError(f"{label}: {error}.")
        modes = context.get("app_modes")
        if modes is not None and not (
            isinstance(modes, list) and all(isinstance(mode, str) and 0 < len(mode) <= CONTEXT_MODE_MAX for mode in modes)
        ):
            raise ProfileTextError(f"{label}: \"app_modes\" must be a list of mode names.")
        if not (str(title or "").strip() or modes):
            raise ProfileTextError(f"{label} needs a \"title\" or \"app_modes\", else it never applies.")
        if context.get("segments") is not None:
            if not isinstance(context["segments"], list):
                raise ProfileTextError(f"{label}: \"segments\" must be a list of functions.")
            _check_functions(context["segments"], label)
        _check_spacemouse(context.get("spacemouse_buttons"), label)
        _check_spacemouse_layers(context.get("spacemouse_layers"), label)
        _check_screen(context.get("screen"), label)


def _clean_contexts(profile: Profile, where: str) -> None:
    for context in profile.contexts:
        label = f"{where}, context “{context.name}”"
        _clean_spacemouse_buttons(context.spacemouse_buttons, label)
        for name, buttons in context.spacemouse_layers.items():
            _clean_spacemouse_buttons(buttons, f"{label}, layer {name!r}")
        if context.segments is not None:
            _clean_functions(context.segments, label)
            for segment in context.segments:
                if segment.is_group:
                    _clean_functions(segment.items, f"{label}, “{segment.title}”")


def _clean_spacemouse(profile: Profile, where: str) -> None:
    """Key names as evdev names (an assistant may write "Z" or "Ctrl"), the steps checked like a function's."""
    maps = [("", profile.spacemouse_buttons)]
    maps += [(f" layer {name!r}", buttons) for name, buttons in profile.spacemouse_layers.items()]
    for owner, buttons in maps:
        _clean_spacemouse_buttons(buttons, where + owner)


def _clean_spacemouse_buttons(buttons, where: str) -> None:
    for number, button in buttons.items():
        for slot, action in (("press", button.press), ("hold", button.hold)):
            if action is None:
                continue
            label = f"{where}, SpaceMouse button {number} ({slot})"
            if action.keys:
                action.keys = _keys(action.keys, label)
            for step in action.macro or []:
                _check_step(step, label)


def _profile(data, number: int) -> Profile:
    where = f"Profile {number}"
    if not isinstance(data, dict):
        raise ProfileTextError(f"{where} is not a JSON object.")
    if not isinstance(data.get("segments"), list):
        raise ProfileTextError(f"{where} has no \"segments\" list.")
    data = {"name": "Imported profile", **data}
    _check_functions(data["segments"], where)
    _check_spacemouse(data.get("spacemouse_buttons"), where)
    _check_spacemouse_layers(data.get("spacemouse_layers"), where)
    _check_motion(data.get("spacemouse_motion"), where)
    _check_screen(data.get("screen"), where)
    _check_contexts(data.get("contexts"), where)
    try:
        profile = _decoder(data, Profile)
    except (KeyError, TypeError, ValueError) as error:
        raise ProfileTextError(f"{where} could not be read: {error}") from None
    _clean_spacemouse(profile, where)
    _clean_contexts(profile, where)

    _clean_functions(profile.segments, where)
    for segment in profile.segments:
        if segment.is_group:
            _clean_functions(segment.items, f"{where}, “{segment.title}”")
    titles = [segment.title for segment in profile.segments if not segment.is_group]
    if profile.mode == "single" and profile.single_function not in titles and titles:
        profile.single_function = titles[0]
    return profile


def _check_functions(segments, where: str, nested: bool = False) -> None:
    for index, segment in enumerate(segments, 1):
        if not isinstance(segment, dict) or not str(segment.get("title") or "").strip():
            raise ProfileTextError(f"{where}, function {index} has no \"title\".")
        variants = segment.get("with_modifier") or {}
        if not isinstance(variants, dict) or any(
            name not in MODIFIER_KEYS or not isinstance(actions, dict) for name, actions in variants.items()
        ):
            raise ProfileTextError(
                f"{where}, “{segment['title']}”: \"with_modifier\" takes "
                + ", ".join(f'"{name}"' for name in MODIFIER_KEYS) + " with steps for each."
            )
        holders = [segment, *variants.values()]
        for field in MACRO_FIELDS:
            for step in [step for holder in holders for step in holder.get(field) or []]:
                kind = step.get("type") if isinstance(step, dict) else None
                if kind not in {item.value for item in StepType}:
                    raise ProfileTextError(
                        f"{where}, “{segment['title']}”: unknown step type {kind!r}; use "
                        + ", ".join(item.value for item in StepType) + "."
                    )
        if "items" in segment:
            if nested:
                raise ProfileTextError(
                    f"{where}, “{segment['title']}”: a function in a group cannot have \"items\"; "
                    "groups go one level deep."
                )
            if not isinstance(segment["items"], list):
                raise ProfileTextError(f"{where}, “{segment['title']}”: \"items\" must be a list of functions.")
            _check_functions(segment["items"], f"{where}, “{segment['title']}”", nested=True)


def _clean_functions(segments: list[Segment], where: str) -> None:
    """Unique titles, valid colours and steps; a group keeps no steps."""
    seen = set()
    for segment in segments:
        segment.title = segment.title.strip()
        title, copy = segment.title, 2
        while segment.title.lower() in seen:
            segment.title = f"{title} {copy}"
            copy += 1
        seen.add(segment.title.lower())
        if segment.color and not re.fullmatch(r"#[0-9a-fA-F]{6}", segment.color):
            segment.color = None
        if not isinstance(segment.treshold, int) or not 5 <= segment.treshold <= 360:
            segment.treshold = None
        if segment.is_group:
            segment.macro_cw, segment.macro_ccw, segment.macro_center = [], [], []
            segment.value_command = segment.unit = segment.treshold = None
            segment.with_modifier = {}
        for steps in segment.macro_lists():
            for step in steps:
                _check_step(step, f"{where}, “{segment.title}”")


def profiles_from_text(text: str) -> list[Profile]:
    """Read one profile, a list of them or an export file's content."""
    data = _first_json(text or "")
    if isinstance(data, dict) and "profiles" in data and "segments" not in data:
        data = data["profiles"]
    items = data if isinstance(data, list) else [data]
    if not items:
        raise ProfileTextError("The JSON holds no profile.")
    return [_profile(item, number) for number, item in enumerate(items, 1)]
