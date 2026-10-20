"""Turnpad: lets the Turnpad driver (a dial or SpaceMouse buttons) run Blender
commands and follow the mode you are in. It talks to the driver through a
local socket; nothing but the commands in catalog.py ever runs."""

import traceback

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, StringProperty

from . import service as _service

_state = {"service": None, "class": False}


def _log(where):
    print("Turnpad: %s failed:\n%s" % (where, traceback.format_exc()))


def _settings():
    """(enabled, socket path override) from the add-on preferences."""
    try:
        prefs = bpy.context.preferences.addons[__package__].preferences
        return bool(prefs.enabled), str(prefs.socket_path)
    except Exception:
        return True, ""


class TurnpadPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    enabled: BoolProperty(
        name="Connect to Turnpad",
        description="Talk to the Turnpad driver while Blender runs",
        default=True,
    )
    socket_path: StringProperty(
        name="Socket",
        description="Path of the driver's socket; empty = asus-turnpad-apps.sock in "
                    "the runtime directory",
        subtype="FILE_PATH",
        default="",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "enabled")
        layout.prop(self, "socket_path")
        service = _state["service"]
        layout.label(text="Status: " + (service.status() if service else "not running"))


def _tick():
    service = _state["service"]
    if service is None:
        return None
    try:
        return service.tick()
    except Exception:
        _log("timer")
        return 1.0


@persistent
def _on_load_post(*_args):
    try:
        if _state["service"] is not None:
            _state["service"].file_loaded()
    except Exception:
        _log("load_post")


def register():
    if _state["class"]:
        return
    try:
        bpy.utils.register_class(TurnpadPreferences)
        _state["class"] = True
        _state["service"] = _service.Service(_settings)
        if _on_load_post not in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.append(_on_load_post)
        if not bpy.app.timers.is_registered(_tick):
            bpy.app.timers.register(_tick, first_interval=0.05, persistent=True)
    except Exception:
        _log("register")


def unregister():
    try:
        if bpy.app.timers.is_registered(_tick):
            bpy.app.timers.unregister(_tick)
    except Exception:
        _log("unregister timer")
    try:
        if _on_load_post in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.remove(_on_load_post)
    except Exception:
        _log("unregister handler")
    service, _state["service"] = _state["service"], None
    if service is not None:
        try:
            service.stop()
        except Exception:
            _log("unregister service")
    if _state["class"]:
        _state["class"] = False
        try:
            bpy.utils.unregister_class(TurnpadPreferences)
        except Exception:
            _log("unregister class")
