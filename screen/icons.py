"""Symbolic icons for the screen's pages, looked up by name in the icon themes.

The theme directories are searched for ``<name>.svg`` (Adwaita and hicolor
keep their symbolic icons under ``symbolic/<category>/``), rendered with Rsvg
and recoloured through a mask like the dial does. Without librsvg or without
the icon nothing is drawn.
"""

from __future__ import annotations

import os

try:
    import gi

    gi.require_version("Rsvg", "2.0")
    from gi.repository import Rsvg
except (ImportError, ValueError):
    Rsvg = None

THEME_DIRS = ("Adwaita", "AdwaitaLegacy", "hicolor")
# Directories of a theme that can hold symbolic icons, in the order tried.
CATEGORIES = ("symbolic/actions", "symbolic/devices", "symbolic/apps", "symbolic/status", "symbolic/ui",
              "symbolic/places", "symbolic/mimetypes", "symbolic/categories", "symbolic/emblems",
              "symbolic/legacy", "symbolic/emotes", "scalable/actions", "scalable/devices", "scalable/apps")

_paths = {}
_handles = {}


def _data_dirs():
    home = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    system = (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    return [home, *[directory for directory in system if directory]]


def find_icon(name: str) -> str | None:
    """The path of an icon (a theme name or an absolute .svg path), or None."""
    if not name:
        return None
    if name in _paths:
        return _paths[name]
    found = None
    if name.startswith("/"):
        found = name if name.endswith(".svg") and os.path.isfile(name) else None
    else:
        stem = name if name.endswith(".svg") else f"{name}.svg"
        for data in _data_dirs():
            for theme in THEME_DIRS:
                for category in CATEGORIES:
                    path = os.path.join(data, "icons", theme, category, stem)
                    if os.path.isfile(path):
                        found = path
                        break
                if found:
                    break
            if found:
                break
    _paths[name] = found
    return found


def _handle(name):
    if name in _handles:
        return _handles[name]
    handle = None
    path = find_icon(name)
    if Rsvg is not None and path:
        try:
            handle = Rsvg.Handle.new_from_file(path)
        except Exception:
            handle = None
    _handles[name] = handle
    return handle


def available() -> bool:
    return Rsvg is not None


def draw_icon(cr, name, x, y, size, color) -> None:
    """Paint a symbolic icon at x, y (size x size) in ``color`` (r, g, b[, a]);
    nothing when it cannot be found or rendered."""
    handle = _handle(name)
    if handle is None:
        return
    cr.push_group()
    try:
        viewport = Rsvg.Rectangle()
        viewport.x, viewport.y, viewport.width, viewport.height = x, y, size, size
        handle.render_document(cr, viewport)
    except Exception:
        pass
    mask = cr.pop_group()
    cr.save()
    cr.set_source_rgba(*(tuple(color) + (1,) if len(color) == 3 else color))
    cr.mask(mask)
    cr.restore()
