#!/usr/bin/env python3
"""Paint the pictures of the SpacePilot Pro screen for GALLERY.md.

    python3 tools/render_screen_preview.py [output directory]

Writes preview/screen-<page>.png for every page from a demo configuration (a
Blender profile with labelled buttons) through screen/preview.py, the code the
settings app uses. No hardware and nothing under ~/.config is touched.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.presets import pack_for_app, profile_for_pack  # noqa: E402
from config.schema import SpaceMouseAction, SpaceMouseButton, _default_config  # noqa: E402
from screen import preview  # noqa: E402

OUTPUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "preview"
APP_ID = "org.blender.Blender.desktop"

# Blender 5.x defaults: key 1..5 short = a shortcut, long = a pie menu.
SHORT = (
    ("Edit mode", ["KEY_TAB"]),
    ("Persp / Ortho", ["KEY_KP5"]),
    ("Add", ["KEY_LEFTSHIFT", "KEY_A"]),
    ("X-ray", ["KEY_LEFTALT", "KEY_Z"]),
    ("Search", ["KEY_F3"]),
)
LONG = (
    ("Mode pie", ["KEY_LEFTCTRL", "KEY_TAB"]),
    ("View pie", ["KEY_GRAVE"]),
    ("Snap pie", ["KEY_LEFTSHIFT", "KEY_S"]),
    ("Shading pie", ["KEY_Z"]),
    ("Maximize", ["KEY_LEFTCTRL", "KEY_SPACE"]),
)


def demo_config():
    """The default configuration with a Blender profile (the advanced pack
    and labelled SpaceMouse buttons) and a plain dark screen theme."""
    config = _default_config()
    profile = profile_for_pack(pack_for_app(APP_ID), advanced=True)
    profile.app_id = APP_ID
    for number, ((short, keys), (long, hold)) in enumerate(zip(SHORT, LONG)):
        profile.spacemouse_buttons[str(12 + number)] = SpaceMouseButton(
            press=SpaceMouseAction("macro", keys=keys), label=short)
        profile.spacemouse_buttons[str(17 + number)] = SpaceMouseButton(
            press=SpaceMouseAction("key_hold", keys=hold), label=long)
    config.profiles.append(profile)
    config.screen.theme = "dark"
    return config, profile


def main() -> None:
    config, profile = demo_config()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    driver = {"modifiers": ["Ctrl"], "dial": True, "spacemouse": True}
    for page in preview.PAGE_TITLES:
        surface = preview.render(config, profile.name, page, scale=2, driver_state=driver,
                                 highlight=2 if page == "keys" else None)
        surface.write_to_png(str(OUTPUT / f"screen-{page}.png"))
        print(f"preview/screen-{page}.png")


if __name__ == "__main__":
    main()
