"""Runs a catalog entry in Blender.

Only entries of catalog.CATALOG run, with the properties written there. An
operator needs a window, screen, area and region (a timer has none), so the
call is wrapped in `bpy.context.temp_override` of the largest area of the type
the entry needs. Without a window (`blender -b`) the entries that need one
fail with a readable error."""

import bpy

from . import catalog
from . import context
from . import protocol

# What an operator returns that counts as done: it ran, or a modal operator /
# menu is now waiting for the user.
DONE = {"FINISHED", "RUNNING_MODAL", "INTERFACE"}


def _window_region(area):
    for region in area.regions:
        if region.type == "WINDOW":
            return region
    return None


def find_override(area_type):
    """Keyword arguments for temp_override of the best area, None when there is
    no such area. "ANY" takes any area but prefers a 3D view; the largest wins."""
    best = None
    for window in bpy.context.window_manager.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area_type == "ANY":
                preferred = 1 if area.type == "VIEW_3D" else 0
            elif area.type == area_type:
                preferred = 1
            else:
                continue
            region = _window_region(area)
            if region is None:
                continue
            rank = (preferred, area.width * area.height)
            if best is None or rank > best[0]:
                best = (rank, {"window": window, "screen": screen, "area": area, "region": region})
    return None if best is None else best[1]


def _operator(path):
    category, name = path.split(".")
    return getattr(getattr(bpy.ops, category), name)


def _execute(command):
    if command.kind == "pie":
        return bpy.ops.wm.call_menu_pie("INVOKE_DEFAULT", name=command.target)
    if command.kind == "menu":
        return bpy.ops.wm.call_menu("INVOKE_DEFAULT", name=command.target)
    mode = "INVOKE_DEFAULT" if command.kind == "op" else "EXEC_DEFAULT"
    return _operator(command.target)(mode, **command.props)


def _judge(command, result):
    result = set(result)
    if result & DONE:
        return True, ""
    if command.target == "wm.context_toggle" and "CANCELLED" in result:
        # It changed a property that is not part of the undo history (a
        # space's, as the sidebar) and says CANCELLED for exactly that.
        return True, ""
    if "CANCELLED" in result:
        return False, "cancelled by Blender (nothing to do in this state?)"
    if "PASS_THROUGH" in result:
        return False, "nothing to do in this state"
    return False, "not handled (%s)" % ", ".join(sorted(result))


def _short(err):
    text = str(err).strip().replace("\n", " ")
    if "poll() failed" in text:
        text = "not available here (wrong mode, selection or area)"
    return text[:protocol.MAX_ERROR] or type(err).__name__


def run(command_id):
    """Run one catalog entry; (ok, error). Never raises."""
    command = catalog.BY_ID.get(command_id)
    if command is None:
        return False, "unknown command"
    try:
        # Operators whose poll lets a wrong mode through can crash Blender
        # (paint.mask_flood_fill in Object Mode did), so `modes` is enforced.
        mode = bpy.context.mode
        if command.modes and mode not in command.modes:
            return False, "only in %s" % ", ".join(context.label_for(m) for m in command.modes)
        if bpy.app.background and command.kind in ("pie", "menu"):
            return False, "menus need the Blender window (this is a background session)"
        override = find_override(command.area if command.area != "NONE" else "VIEW_3D")
        if override is None:
            if command.area == "VIEW_3D":
                return False, "no 3D view"
            if command.area == "ANY":
                return False, "no Blender window"
            override = {}
        with bpy.context.temp_override(**override):
            result = _execute(command)
        return _judge(command, result)
    except Exception as err:
        return False, _short(err)
