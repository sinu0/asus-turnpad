"""Pictures of the screen's pages, for the settings app and the README.

``render()`` is the public entry: it paints a page from a configuration with
the code the panel uses (screen.pages), without any hardware.
"""

from __future__ import annotations

import sys
from datetime import datetime

from screen import pages
from screen.canvas import HEIGHT, WIDTH, preview_surface
from screen.content import catalog_index, face_buttons, find_profile, resolve
from screen.ui import ScreenUI

PAGE_TITLES = {"keys": "Keys", "dial": "Dial", "device": "All buttons", "motion": "Motion", "clock": "Clock"}


def render(config, profile_name=None, page="keys", *, scale=2, driver_state=None, highlight=None):
    """An ARGB32 ImageSurface (320 * scale x 240 * scale) of ``page`` for the
    profile called ``profile_name`` ("" or None = the default profile).

    ``driver_state`` is a DriverState dict (modifiers, keys_held, dial ...); it
    may also carry the dial's live ``overlay_state`` / ``overlay_details``
    (overlay.look dicts), otherwise the dial and motion pages show demo data;
    ``context``, ``layer`` and ``motion`` pick a profile context, a button layer
    and the motion chips, ``app_commands`` (GetAppCommands' answer) names the
    faces of app commands.
    ``highlight`` is the keys page's highlighted face (0-9: the function
    keys' short and long faces in order). Never raises: a broken
    configuration gives a picture with an error note.
    """
    surface, cr = preview_surface(scale)
    try:
        driver = dict(driver_state or {})
        if profile_name:
            driver["profile"] = profile_name
        commands = catalog_index(driver.pop("app_commands", None) or {})
        resolved = resolve(config, driver, commands)
        profile, settings, entries = resolved.profile, resolved.settings, resolved.entries
        ui = ScreenUI(settings)
        ui.set_context(face_buttons(entries, settings.show_long), pages.device_steps(entries, settings.text_size == "large"))
        ui.page = page if page in PAGE_TITLES else "keys"
        if highlight is not None and ui.faces:
            ui.highlight = max(0, min(len(ui.faces) - 1, int(highlight)))
        state = driver.pop("overlay_state", None)
        details = driver.pop("overlay_details", None)
        if state is None or details is None:
            state, details = pages.demo_overlay(config, profile)
        driver.setdefault("profile", profile.name)
        driver.setdefault("spacemouse", True)
        driver.setdefault("dial", bool(state.get("enabled")))
        scene = pages.build_scene(
            config, profile.name, ui.view(), state=state, details=details, driver=driver,
            axes=pages.DEMO_AXES, now=datetime.now(), connected=bool(driver["spacemouse"]), commands=commands,
        )
        pages.paint(cr, scene)
    except Exception as error:  # noqa: BLE001 - a picture, whatever happens
        try:
            from screen.pages import Theme, paint_error

            surface, cr = preview_surface(scale)
            dark = (0.07, 0.075, 0.1)
            paint_error(cr, Theme(dark, dark, dark, (0.95, 0.95, 0.97), (0.62, 0.64, 0.7), (0.4, 0.4, 0.45),
                                  (0.9, 0.7, 0.5)), f"{type(error).__name__}: {error}")
        except Exception:  # noqa: BLE001
            print(f"screen preview failed: {error}", file=sys.stderr)
    return surface
