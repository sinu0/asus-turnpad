"""Helpers of the contexts editor (GTK-free, unit tested): names and order of a
profile's contexts, the application's modes with friendly labels, the title
condition check, and the "scope" the SpaceMouse page uses to edit the buttons of
one context (``ContextScope`` stands in for a Profile there)."""

from __future__ import annotations

import copy
import json
import re
from config.schema import (
    CONTEXT_NAME_MAX,
    CONTEXTS_MAX,
    CONTEXT_MODE_MAX,
    CONTEXT_TITLE_MAX,
    ProfileContext,
    context_title_error,
    context_title_matches,
)

# Blender's modes as the add-on reports them (Context.mode); other words are
# made readable by mode_label().
MODE_LABELS = {
    "OBJECT": "Object Mode",
    "EDIT_MESH": "Edit Mesh",
    "EDIT_CURVE": "Edit Curve",
    "EDIT_CURVES": "Edit Curves",
    "EDIT_SURFACE": "Edit Surface",
    "EDIT_TEXT": "Edit Text",
    "EDIT_ARMATURE": "Edit Armature",
    "EDIT_METABALL": "Edit Metaball",
    "EDIT_LATTICE": "Edit Lattice",
    "EDIT_GPENCIL": "Edit Grease Pencil",
    "POSE": "Pose Mode",
    "SCULPT": "Sculpt Mode",
    "SCULPT_CURVES": "Sculpt Curves",
    "PAINT_WEIGHT": "Weight Paint",
    "PAINT_VERTEX": "Vertex Paint",
    "PAINT_TEXTURE": "Texture Paint",
    "PAINT_GPENCIL": "Grease Pencil Draw",
    "PARTICLE": "Particle Edit",
}


def mode_label(mode: str) -> str:
    """EDIT_MESH -> "Edit Mesh"."""
    mode = str(mode or "")
    if mode in MODE_LABELS:
        return MODE_LABELS[mode]
    return " ".join(word.capitalize() for word in re.split(r"[_\s]+", mode) if word) or mode


def normalize_mode(text, known=()) -> str:
    """A mode typed by hand: a known mode's id or label (any case) gives the id,
    anything else is kept as typed (the application's own word), trimmed."""
    text = str(text or "").strip()[:CONTEXT_MODE_MAX]
    lowered = text.lower()
    for mode in known:
        if lowered in (mode.lower(), mode_label(mode).lower()):
            return mode
    return text


def parse_app_commands(text) -> dict:
    """The JSON of GetAppCommands as a dict; {} when it cannot be read."""
    try:
        data = json.loads(text) if isinstance(text, str) else text
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def known_modes(app_commands, current_mode="", chosen=()) -> list[str]:
    """The application's modes the editor offers: those the commands of the
    catalog name, the mode it is in now and the ones already chosen (so a mode
    the add-on no longer reports stays visible). Order: catalog, then the rest."""
    modes: list[str] = []

    def add(mode):
        if isinstance(mode, str) and mode.strip() and mode not in modes:
            modes.append(mode)

    data = parse_app_commands(app_commands)
    for command in data.get("commands") or []:
        modes_of_command = command.get("modes") if isinstance(command, dict) else None
        for mode in modes_of_command if isinstance(modes_of_command, list) else []:
            add(mode)
    context = data.get("context")
    add(context.get("mode") if isinstance(context, dict) else None)
    add(current_mode)
    for mode in chosen:
        add(mode)
    return modes


# The title condition check ---------------------------------------------------------------

def title_check(title, window_title) -> tuple[str, str]:
    """(state, message) of a title condition against a test string: state is "empty"
    (no condition), "invalid" (message says why), "unchecked" (no test string),
    "match" or "nomatch". The condition is plain text, alternatives separated by |."""
    title = str(title or "")
    if not title.strip():
        return "empty", "No title condition: every window of the application counts"
    error = context_title_error(title)
    if error is not None:
        return "invalid", error[:1].upper() + error[1:]
    if not window_title:
        return "unchecked", "Type a window title above to try the condition"
    if context_title_matches(title, window_title):
        return "match", "Matches this window title"
    return "nomatch", "Does not match this window title"


# Editing a profile's list of contexts ------------------------------------------------------

def unique_name(profile, name, ignore=None) -> str:
    """``name`` (trimmed, at most CONTEXT_NAME_MAX characters), numbered when another
    context of the profile has it already."""
    name = (str(name or "").strip() or "Context")[:CONTEXT_NAME_MAX]
    taken = {context.name for context in profile.contexts if context is not ignore}
    if name not in taken:
        return name
    index = 2
    while True:
        suffix = f" {index}"
        candidate = name[:CONTEXT_NAME_MAX - len(suffix)] + suffix
        if candidate not in taken:
            return candidate
        index += 1


def can_add(profile) -> bool:
    return len(profile.contexts) < CONTEXTS_MAX


def new_context(profile, name="Context", title="", app_modes=()) -> ProfileContext:
    """A context appended to the profile (last = lowest priority)."""
    context = ProfileContext(name=unique_name(profile, name), title=title[:CONTEXT_TITLE_MAX],
                             app_modes=list(app_modes))
    profile.contexts.append(context)
    return context


def move_context(profile, context, offset) -> bool:
    """Move a context earlier (-1) or later (+1) in the priority order."""
    index = profile.contexts.index(context)
    target = index + offset
    if not 0 <= target < len(profile.contexts):
        return False
    profile.contexts[index], profile.contexts[target] = profile.contexts[target], profile.contexts[index]
    return True


def set_own_menu(profile, context, own) -> None:
    """Switch the context's own dial menu on (a copy of the profile's functions to
    start from) or off (the profile's menu again)."""
    if own and context.segments is None:
        context.segments = copy.deepcopy(profile.segments)
    elif not own:
        context.segments = None


def menu_view(profile, context):
    """A Profile-shaped view whose ``segments`` is the context's own list (the
    same list object, so the menu editor's in-place edits reach the context)."""
    from dataclasses import replace

    return replace(profile, segments=context.segments, contexts=[], mode="multi", single_function=None)


def conditions_text(context) -> str:
    parts = []
    if context.title:
        parts.append(f"title contains {context.title}")
    if context.app_modes:
        parts.append(", ".join(mode_label(mode) for mode in context.app_modes))
    return " · ".join(parts) or "No condition yet: never used"


def context_summary(context) -> str:
    """The subtitle of a context's row."""
    parts = [conditions_text(context)]
    if context.segments is not None:
        parts.append(f"own menu ({len(context.segments)})")
    if context.spacemouse_buttons or context.spacemouse_layers:
        count = len(context.spacemouse_buttons)
        parts.append(f"own buttons ({count})" if count else "own layers")
    if context.screen.get("start_page"):
        parts.append(f"screen starts on {context.screen['start_page']}")
    return "  ·  ".join(parts)


# The SpaceMouse page's scope ---------------------------------------------------------------

class ContextScope:
    """What the SpaceMouse page edits when "Applies to" is a profile's context: it
    looks like a Profile for the button list (name, app id, buttons, layers,
    functions for the pickers) and stores into the context. ``parent`` is the
    profile whose buttons the context's are laid over."""

    def __init__(self, profile, context):
        self.parent = profile
        self.context = context

    @property
    def name(self):
        return f"{self.parent.name} › {self.context.name}"

    @property
    def app_id(self):
        return self.parent.app_id

    @property
    def spacemouse_buttons(self):
        return self.context.spacemouse_buttons

    @spacemouse_buttons.setter
    def spacemouse_buttons(self, value):
        self.context.spacemouse_buttons = value

    @property
    def spacemouse_layers(self):
        return self.context.spacemouse_layers

    @spacemouse_layers.setter
    def spacemouse_layers(self, value):
        self.context.spacemouse_layers = value

    @property
    def spacemouse_motion(self):
        # Cap motion is set per application: a context shares its profile's.
        return self.parent.spacemouse_motion

    @spacemouse_motion.setter
    def spacemouse_motion(self, value):
        self.parent.spacemouse_motion = value

    @property
    def segments(self):
        return self.context.segments if self.context.segments is not None else self.parent.segments

    def __eq__(self, other):
        return isinstance(other, ContextScope) and other.parent is self.parent and other.context is self.context

    def __hash__(self):
        return hash((id(self.parent), id(self.context)))


def scope_entries(config) -> list[tuple[str, object]]:
    """(label, scope) for the SpaceMouse page's "Applies to" list after "All
    applications": every profile, each followed by its contexts."""
    entries = []
    for profile in config.profiles:
        entries.append((profile.name if profile.app_id is None else f"{profile.name}  ·  app", profile))
        for context in profile.contexts:
            entries.append((f"{profile.name} › {context.name}", ContextScope(profile, context)))
    return entries


def scope_alive(config, scope) -> bool:
    """Whether ``scope`` (None, a Profile or a ContextScope) still exists in ``config``."""
    if scope is None:
        return True
    if isinstance(scope, ContextScope):
        return scope.parent in config.profiles and scope.context in scope.parent.contexts
    return scope in config.profiles


def button_maps(config) -> list[dict]:
    """The buttons maps of every profile and context (the page sizes its list by them)."""
    maps = []
    for profile in config.profiles:
        maps.append(profile.spacemouse_buttons)
        maps.extend(context.spacemouse_buttons for context in profile.contexts)
    return maps
