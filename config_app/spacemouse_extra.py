"""Layers, cap motion and app commands of the SpaceMouse page, free of GTK so they are unit tested.

Layers: ``SpaceMouseConfig.layers`` (global) and ``Profile.spacemouse_layers``
(per application) map a layer name to a button map; while a layer is on, its
buttons lie over the base map (the driver merges the profile's layer over the
global one of the same name). The page edits the layers of its scope: all
applications, or one profile (which also shows the global layers, so a binding
can be changed for that application only).

Motion: ``SpaceMouseConfig.motion`` and ``Profile.spacemouse_motion`` hold
cap-motion settings for other programs (``config.schema.motion_rule``); the
global one keeps only what differs from the defaults, a profile's override is a
whole snapshot (it replaces the global values key by key).

App commands: the catalog an application's add-on reports (``GetAppCommands``).
"""

import json
import math
from dataclasses import dataclass

from config.labels import parse_keys
from config.schema import (
    MOTION_DEFAULTS,
    MOTION_SENSITIVITY_RANGE,
    SPACEMOUSE_LAYER_NAME_MAX,
    SPACEMOUSE_LAYERS_MAX,
    SpaceMouseAction,
    SpaceMouseButton,
    effective_motion,
    motion_rule,
)
from config_app import spacemouse as sm

# --- layers ---------------------------------------------------------------------


def scope_layers(config, profile):
    """The dict that holds the layers of a scope (profile None = all applications)."""
    return config.spacemouse.layers if profile is None else profile.spacemouse_layers


def owners(config, profile):
    """The things whose button maps can name a layer of this scope: for all
    applications the global settings and every profile, for a profile it and its
    contexts. Each has ``spacemouse_buttons`` and ``spacemouse_layers``."""
    if profile is not None:
        return [profile, *(getattr(profile, "contexts", None) or [])]
    found = [config.spacemouse]
    for item in config.profiles:
        found.extend([item, *(getattr(item, "contexts", None) or [])])
    return found


def _owner_maps(owner):
    buttons = owner.buttons if hasattr(owner, "buttons") else owner.spacemouse_buttons
    layers = owner.layers if hasattr(owner, "layers") else owner.spacemouse_layers
    return [buttons, *layers.values()]


def _actions(config, profile):
    for owner in owners(config, profile):
        for buttons in _owner_maps(owner):
            for button in buttons.values():
                for action in (button.press, button.hold):
                    if action is not None:
                        yield action


def layer_names(config, profile):
    """The layers shown for a scope: the global ones, then the profile's own."""
    names = list(config.spacemouse.layers)
    if profile is not None:
        names += [name for name in profile.spacemouse_layers if name not in names]
    return names


def is_inherited_layer(config, profile, name):
    """A layer a profile gets from all applications and has no map of its own for."""
    return profile is not None and name in config.spacemouse.layers and name not in profile.spacemouse_layers


def action_layer_names(config, profile):
    """The layers a Switch layer action may name: those of the scope, and for
    all applications also the ones profiles define (a global button can switch
    a layer each application fills in its own way)."""
    names = layer_names(config, profile)
    if profile is None:
        for item in config.profiles:
            names += [name for name in item.spacemouse_layers if name not in names]
    return names


def check_layer_name(config, profile, name, ignore=None):
    """(clean name, error): an error text when the name cannot be a new layer."""
    clean = " ".join(str(name or "").split())[:SPACEMOUSE_LAYER_NAME_MAX]
    if not clean:
        return "", "Give the layer a name"
    taken = {existing.casefold() for existing in action_layer_names(config, profile) if existing != ignore}
    if profile is None:
        taken |= {existing.casefold() for item in config.profiles for existing in item.spacemouse_layers if existing != ignore}
    if clean.casefold() in taken:
        return clean, f"There is a layer called “{clean}” already"
    if ignore is None and len(scope_layers(config, profile)) >= SPACEMOUSE_LAYERS_MAX:
        return clean, f"At most {SPACEMOUSE_LAYERS_MAX} layers"
    return clean, ""


def add_layer(config, profile, name):
    """Create an empty layer; returns its name. ValueError says why not."""
    clean, error = check_layer_name(config, profile, name)
    if error:
        raise ValueError(error)
    scope_layers(config, profile)[clean] = {}
    return clean


def rename_layer(config, profile, old, new):
    """Rename a layer of the scope and every Switch layer action that names it.
    Returns the new name; ValueError says why not (a layer of All applications
    is renamed there, also when a profile has a map of its own for it)."""
    if old not in scope_layers(config, profile) or (profile is not None and old in config.spacemouse.layers):
        raise ValueError("This layer belongs to All applications; rename it there")
    clean, error = check_layer_name(config, profile, new, ignore=old)
    if error:
        raise ValueError(error)
    if clean == old:
        return old
    for owner in owners(config, profile):
        layers = owner.layers if hasattr(owner, "layers") else owner.spacemouse_layers
        if old in layers:
            # A profile's map of the same name is the same layer, filled in per application.
            layers[clean] = layers.pop(old)
    for action in _actions(config, profile):
        if action.type == "layer" and action.layer == old:
            action.layer = clean
    return clean


def layer_users(config, profile, name):
    """How many actions switch to the layer ``name``."""
    return sum(1 for action in _actions(config, profile) if action.type == "layer" and action.layer == name)


def remove_layer(config, profile, name):
    """Remove a layer of the scope (for all applications also each profile's map
    of the same name) and turn the actions that switched to it into pass-through.
    A profile's map that only overrode a global layer just goes. Returns how many
    actions were cleared."""
    scope_layers(config, profile).pop(name, None)
    still_there = name in layer_names(config, profile)
    if profile is None:
        for item in config.profiles:
            for owner in (item, *(getattr(item, "contexts", None) or [])):
                owner.spacemouse_layers.pop(name, None)
        still_there = False
    else:
        for context in getattr(profile, "contexts", None) or []:
            context.spacemouse_layers.pop(name, None)
    cleared = 0
    if not still_there:
        for owner in owners(config, profile):
            for buttons in _owner_maps(owner):
                for button in buttons.values():
                    for slot in ("press", "hold"):
                        action = getattr(button, slot)
                        if action is not None and action.type == "layer" and action.layer == name:
                            setattr(button, slot, SpaceMouseAction("none"))
                            cleared += 1
    return cleared


# --- the button of a scope and layer ----------------------------------------------


def current_binding(config, profile, layer, index):
    """(button, source) in force for ``index`` in the scope with ``layer`` selected
    (None = the base map). Source: "profile" / "global" for the base map,
    "layer" (the scope's own layer map), "global-layer" (the one of all
    applications under a profile), "base" (a layer button that does not
    bind: the base binding stays), or None (nothing is bound)."""
    button, source = sm.binding(config, profile, index)
    if layer is None:
        return button, source
    source = "base" if button is not None and (button.press is not None or button.hold is not None) else None
    key = str(index)
    chain = []
    if profile is not None:
        chain.append(("global-layer", config.spacemouse.layers.get(layer, {})))
    chain.append(("layer", scope_layers(config, profile).get(layer, {})))
    for name, layer_map in chain:
        entry = layer_map.get(key)
        if entry is None:
            continue
        if entry.press is None and entry.hold is None:
            # A label only: the binding below keeps its actions.
            if button is not None:
                button = SpaceMouseButton(press=button.press, hold=button.hold, label=entry.label or button.label)
            else:
                button = entry
        else:
            button, source = entry, name
    return button, source


def own_source(profile, layer):
    """The source current_binding() reports for an entry of the edited map itself."""
    if layer is not None:
        return "layer"
    return "global" if profile is None else "profile"


def owns(config, profile, layer, index):
    """Whether the edited map has actions of its own for ``index``."""
    return current_binding(config, profile, layer, index)[1] == own_source(profile, layer)


def edited_map(config, profile, layer):
    """The map a binding is stored in: the global base map (the defaults are
    stored with the first edit), a profile's, or a layer's (made when missing)."""
    if layer is not None:
        return scope_layers(config, profile).setdefault(layer, {})
    if profile is None:
        return sm.editable_global(config)
    return profile.spacemouse_buttons


def store_binding(config, profile, layer, index, button):
    """Save ``button`` for ``index`` in the edited map."""
    buttons = edited_map(config, profile, layer)
    if layer is None and profile is None:
        sm.store_global(buttons, index, button)
    else:
        sm.store(buttons, index, button)


def drop_binding(config, profile, layer, index):
    """Give the edited map's own binding of ``index`` up (the one below applies again)."""
    if layer is None and profile is None:
        return
    edited_map(config, profile, layer).pop(str(index), None)


def row_info(config, profile, layer, key_row, titles=None):
    """(text, tooltip, dim) of a key's row in the button list under the scope and layer."""
    own = own_source(profile, layer)
    parts, details, dim, inherited = [], [], True, set()
    for position, index in enumerate(key_row.indexes):
        button, source = current_binding(config, profile, layer, index)
        text = sm.describe_button(button, titles=titles)
        plain = sm.describe_button(button, use_label=False, titles=titles)
        if key_row.dual:
            prefix = ("Short: ", "Long: ")[position]
            text, plain = prefix + text, prefix + plain
        parts.append(text)
        details.append(plain if plain != text else text)
        if source in ("global", "global-layer", "base") and (profile is not None or layer is not None):
            inherited.add("from All applications" if source in ("global", "global-layer") else "from base")
        has_actions = button is not None and (button.press is not None or button.hold is not None)
        if has_actions and source is not None and (own == "global" or source == own):
            dim = False
    text = " · ".join(parts)
    if inherited:
        text = f"{text} · {' / '.join(sorted(inherited))}"
    tooltip = " · ".join(details) + " (button " + " / ".join(str(index) for index in key_row.indexes) + ")"
    return text, tooltip, dim


def all_maps(config):
    """Every button map, for the key list to know which raw indexes are bound."""
    maps = [sm.global_buttons(config), *config.spacemouse.layers.values()]
    for item in config.profiles:
        maps.append(item.spacemouse_buttons)
        maps.extend(item.spacemouse_layers.values())
        for context in item.contexts:
            maps.append(context.spacemouse_buttons)
            maps.extend(context.spacemouse_layers.values())
    return maps


# --- cap motion --------------------------------------------------------------------

AXIS_NAMES = ("X", "Y", "Z", "RX", "RY", "RZ")
AXIS_HELP = (
    "Slide left / right", "Push / pull", "Lift / press down",
    "Tilt forward / back", "Twist", "Tilt left / right",
)


def sensitivity_from_position(position):
    """The sensitivity at a slider position 0-100: logarithmic, 0.1 to 1 over the
    first half, 1 to 4 over the second, so 50 is spacenavd's own."""
    position = min(max(float(position), 0.0), 100.0)
    low, high = MOTION_SENSITIVITY_RANGE
    value = 10 ** ((position - 50) / 50) if position <= 50 else 4 ** ((position - 50) / 50)
    return round(min(max(value, low), high), 2)


def position_from_sensitivity(value):
    low, high = MOTION_SENSITIVITY_RANGE
    value = min(max(float(value), low), high)
    if value <= 1:
        return round(50 + 50 * math.log10(value))
    return round(50 + 50 * math.log(value, 4))


def motion_overridden(profile):
    return profile is not None and bool(profile.spacemouse_motion)


def motion_shown(config, profile):
    """The five values the page shows: the defaults, the global settings, the profile's override."""
    return effective_motion(config.spacemouse.motion, profile.spacemouse_motion if profile is not None else None)


def use_global_motion(profile, use_global, config):
    """Switch a profile between the global motion settings and its own (a snapshot
    of what is in force, to change from there)."""
    profile.spacemouse_motion = {} if use_global else motion_rule(effective_motion(config.spacemouse.motion))


def set_motion(config, profile, name, value, index=None):
    """Change one motion value (``index`` picks the axis of the six-value lists)."""
    shown = motion_shown(config, profile)
    if index is None:
        shown[name] = value
    else:
        values = list(shown[name])
        values[index] = value
        shown[name] = values
    rule = motion_rule(shown)
    if profile is None:
        config.spacemouse.motion = {key: value for key, value in rule.items() if value != MOTION_DEFAULTS[key]}
    else:
        profile.spacemouse_motion = rule


def reset_motion(config, profile):
    if profile is None:
        config.spacemouse.motion = {}
    else:
        profile.spacemouse_motion = {}


def motion_status(state):
    """"Now: ×1.5, rotation off" from the driver state's ``motion`` ({} = unknown)."""
    if not isinstance(state, dict) or "sensitivity" not in state:
        return "Now: not known while the driver or the SpaceMouse is not running"
    parts = [f"×{float(state['sensitivity']):g}"]
    off = [name for key, name in (("rotation", "rotation"), ("translation", "pan and zoom")) if not state.get(key, True)]
    parts += [f"{name} off" for name in off]
    if not off:
        parts.append("rotation and pan/zoom on")
    return "Now: " + ", ".join(parts) + " — set with the device's buttons"


def parse_driver_state(text):
    """The JSON of DriverState / GetDriverState as a dict ({} when unusable)."""
    try:
        state = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


# --- app commands ---------------------------------------------------------------------


@dataclass(frozen=True)
class AppCommand:
    id: str
    title: str
    category: str = ""
    keys: str = ""
    modes: tuple = ()


@dataclass(frozen=True)
class Catalog:
    app: str = ""
    name: str = ""
    version: str = ""
    connected: bool = False
    commands: tuple = ()

    @property
    def known(self):
        return bool(self.commands)

    def titles(self):
        return {command.id: command.title for command in self.commands}

    def find(self, command_id):
        return next((command for command in self.commands if command.id == command_id), None)


def app_name_for(profile):
    """What GetAppCommands is asked for: the profile's application id ("" = none)."""
    return str(getattr(profile, "app_id", None) or "") if profile is not None else ""


def parse_catalog(text):
    """The reply of GetAppCommands; anything unusable is an empty catalog."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return Catalog()
    if not isinstance(data, dict):
        return Catalog()
    commands = []
    for entry in data.get("commands") or []:
        if not isinstance(entry, dict) or not str(entry.get("id") or "").strip():
            continue
        modes = entry.get("modes")
        commands.append(AppCommand(
            id=str(entry["id"]).strip(),
            title=str(entry.get("title") or entry["id"]),
            category=str(entry.get("category") or ""),
            keys=str(entry.get("keys") or ""),
            modes=tuple(str(mode) for mode in modes) if isinstance(modes, list) else (),
        ))
    return Catalog(
        app=str(data.get("app") or ""), name=str(data.get("name") or ""), version=str(data.get("version") or ""),
        connected=bool(data.get("connected")), commands=tuple(commands),
    )


def filter_commands(commands, query):
    """The commands whose title, id, category or keys contain every word of ``query``."""
    words = str(query or "").casefold().split()
    if not words:
        return list(commands)
    return [
        command for command in commands
        if all(word in " ".join((command.title, command.id, command.category, command.keys)).casefold() for word in words)
    ]


def grouped_commands(commands):
    """[(category, [commands])] in the order the catalog lists them; no category = "Other"."""
    groups = {}
    for command in commands:
        groups.setdefault(command.category or "Other", []).append(command)
    return list(groups.items())


def fallback_keys(command):
    """The evdev keys of a command's default shortcut, None when it has none or
    one the key table does not know (such as a mouse gesture)."""
    if command is None or not command.keys.strip():
        return None
    keys, unknown = parse_keys(command.keys)
    return keys if keys and not unknown else None


def describe_catalog(catalog):
    """The status line of an application's command list."""
    name = catalog.name or "the application"
    if catalog.connected:
        version = f" {catalog.version}" if catalog.version else ""
        return f"Connected to {name}{version} · {len(catalog.commands)} commands"
    if catalog.commands:
        return f"{name} is not running with the add-on now · the list of the last time ({len(catalog.commands)} commands)"
    return "No command list yet: it appears when the application has run with its add-on connected"
