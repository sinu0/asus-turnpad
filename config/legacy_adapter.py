from __future__ import annotations

from config.labels import GROUP_ICON, describe_macro
from config.presets import segment_color
from config.schema import MODIFIER_KEYS, DialPadConfig, MacroStep, Profile, Segment, _default_config

# Profile-level settings travel in a reserved key that the driver never
# mistakes for a radial-menu function.
META_KEY = "_meta"


def _legacy_trigger(macro: list[MacroStep], title: str, modifier: str | None = None) -> list[dict]:
    if not macro:
        return []
    entry = {"macro": list(macro), "trigger": "immediate", "title": title}
    if modifier:
        # The driver runs this entry instead of the plain one while one of
        # these keyboard keys is held.
        entry["modifier"] = list(MODIFIER_KEYS[modifier])
    return [entry]


def _legacy_triggers(segment: Segment, field: str) -> list[dict]:
    entries = _legacy_trigger(getattr(segment, field), segment.title)
    for name, actions in segment.with_modifier.items():
        if name in MODIFIER_KEYS:
            entries += _legacy_trigger(getattr(actions, field), segment.title, name)
    return entries


def _hints(segment: Segment) -> dict:
    return {
        "cw": describe_macro(segment.macro_cw),
        "ccw": describe_macro(segment.macro_ccw),
        "center": describe_macro(segment.macro_center),
    }


def _legacy_segment(segment: Segment, index: int = 0) -> dict:
    result = {
        "icon": segment.icon_url,
        "clockwise": _legacy_triggers(segment, "macro_cw"),
        "counterclockwise": _legacy_triggers(segment, "macro_ccw"),
        "center": _legacy_triggers(segment, "macro_center"),
        "color": segment_color(segment, index),
        "hints": _hints(segment),
    }
    if segment.treshold is not None:
        result["treshold"] = segment.treshold
    # The driver runs "value" as a shell command to show the current level.
    if segment.value_command:
        result["value"] = segment.value_command
    if segment.unit:
        result["unit"] = segment.unit
    if segment.overlay:
        result["overlay"] = dict(segment.overlay)
    return result


def _legacy_group(group: Segment, index: int) -> dict:
    """A group: no actions of its own, its enabled functions in list order."""
    result = {
        "icon": group.icon_url or GROUP_ICON,
        "clockwise": [],
        "counterclockwise": [],
        "center": [],
        "color": segment_color(group, index),
        "hints": {},
        "items": {
            item.title: _legacy_segment(item, position)
            for position, item in enumerate(group.active_items())
        },
    }
    if group.overlay:
        result["overlay"] = dict(group.overlay)
    return result


def _profile_meta(profile: Profile) -> dict:
    meta = {
        "name": profile.name,
        "mode": profile.mode,
        "sensitivity": profile.sensitivity,
        "scroll_speed": profile.scroll_speed,
        "remember_last": profile.remember_last,
        "overlay": dict(profile.overlay),
    }
    if profile.spacemouse_buttons:
        meta["spacemouse_buttons"] = dict(profile.spacemouse_buttons)
    return meta


def _profile_shortcuts(profile: Profile) -> dict | None:
    # A group without functions to open has no place on the dial.
    segments = [
        segment for segment in profile.active_segments()
        if not segment.is_group or segment.active_items()
    ]
    if not segments:
        return None
    meta = _profile_meta(profile)

    if profile.mode == "single" and profile.single_segment() is not None:
        # Single-function mode uses the driver's original flat layout: the
        # dial is permanently bound to one function, the centre runs its tap
        # action.
        segment = profile.single_segment()
        legacy = _legacy_segment(segment, segments.index(segment))
        meta["single"] = {
            "title": segment.title,
            **{
                key: legacy[key]
                for key in ("icon", "color", "hints", "treshold", "value", "unit", "overlay")
                if key in legacy
            },
        }
        return {
            META_KEY: meta,
            "clockwise": legacy["clockwise"],
            "counterclockwise": legacy["counterclockwise"],
            "center": legacy["center"],
        }

    return {
        META_KEY: meta,
        "center": [],
        **{
            segment.title: _legacy_group(segment, index) if segment.is_group else _legacy_segment(segment, index)
            for index, segment in enumerate(segments)
        },
    }


def _buttons_only_shortcuts(profile: Profile, base: dict) -> dict:
    """A profile without functions that binds SpaceMouse buttons: the dial keeps
    showing the default profile's menu (``base``), the profile's name and buttons
    are the ones in force while its application has focus."""
    shortcuts = dict(base)
    meta = dict(base.get(META_KEY) or {})
    meta.update(_profile_meta(profile))
    shortcuts[META_KEY] = meta
    return shortcuts


def shortcuts_from_config(config: DialPadConfig) -> dict:
    shortcuts = {}
    first_populated = None
    buttons_only = []
    for profile in config.profiles:
        profile_shortcuts = _profile_shortcuts(profile)
        # An unfinished application profile must fall back to the default one,
        # unless it only exists for its SpaceMouse buttons.
        if profile_shortcuts is None:
            if profile.spacemouse_buttons:
                buttons_only.append(profile)
            continue
        if first_populated is None:
            first_populated = profile_shortcuts
        if profile.app_id is None:
            shortcuts.setdefault("none", profile_shortcuts)
        else:
            shortcuts[profile.app_id] = profile_shortcuts

    if "none" not in shortcuts:
        shortcuts["none"] = (
            first_populated.copy()
            if first_populated is not None
            else {"center": [], "clockwise": [], "counterclockwise": []}
        )
    for profile in buttons_only:
        if profile.app_id is None:
            # The default profile with buttons only: the first menu that exists, with its buttons.
            shortcuts["none"] = _buttons_only_shortcuts(profile, shortcuts["none"])
        else:
            shortcuts[profile.app_id] = _buttons_only_shortcuts(profile, shortcuts["none"])
    return shortcuts


if __name__ == "__main__":
    shortcuts = shortcuts_from_config(_default_config())
    assert "none" in shortcuts
    assert len([key for key in shortcuts["none"] if key not in ("center", META_KEY)]) == 6
    print("Smoke test passed")
