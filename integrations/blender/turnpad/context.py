"""What the user is doing in Blender, for the driver's contexts: the mode
(bpy.context.mode), a readable label and a detail (the mesh select mode in Edit
Mesh, otherwise the active tool). The driver gets it only when it changes."""

import bpy

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
    "EDIT_POINT_CLOUD": "Edit Point Cloud",
    "POSE": "Pose Mode",
    "SCULPT": "Sculpt Mode",
    "SCULPT_CURVES": "Sculpt Curves",
    "PAINT_VERTEX": "Vertex Paint",
    "PAINT_WEIGHT": "Weight Paint",
    "PAINT_TEXTURE": "Texture Paint",
    "PARTICLE": "Particle Edit",
    "EDIT_GREASE_PENCIL": "Edit Grease Pencil",
    "SCULPT_GREASE_PENCIL": "Sculpt Grease Pencil",
    "PAINT_GREASE_PENCIL": "Draw Grease Pencil",
    "WEIGHT_GREASE_PENCIL": "Weight Grease Pencil",
    "VERTEX_GREASE_PENCIL": "Vertex Grease Pencil",
}

SELECT_MODE_NAMES = ("Vertex", "Edge", "Face")


def label_for(mode):
    return MODE_LABELS.get(mode) or mode.replace("_", " ").title()


def select_mode_detail(flags):
    """'Vertex', 'Edge, Face'... from tool_settings.mesh_select_mode."""
    return ", ".join(name for name, on in zip(SELECT_MODE_NAMES, flags) if on)


def _tool_idname(mode):
    wm = bpy.context.window_manager
    workspace = getattr(bpy.context, "workspace", None)
    if workspace is None and wm.windows:
        workspace = wm.windows[0].workspace
    if workspace is None:
        return ""
    tool = workspace.tools.from_space_view3d_mode(mode, create=False)
    return tool.idname if tool is not None else ""


def read():
    """{"mode", "label", "detail"} of now; {} when Blender cannot tell."""
    mode = bpy.context.mode
    if not isinstance(mode, str) or not mode:
        return {}
    detail = ""
    try:
        if mode == "EDIT_MESH":
            detail = select_mode_detail(bpy.context.tool_settings.mesh_select_mode)
        else:
            detail = _tool_idname(mode)
    except Exception:
        detail = ""
    return {"mode": mode, "label": label_for(mode), "detail": detail}


class ContextTracker:
    """poll() returns the context when it differs from the last one returned
    (the first call always returns), else None."""

    def __init__(self, reader=read):
        self._reader = reader
        self._last = None

    def poll(self):
        current = self._reader()
        if not current or current == self._last:
            return None
        self._last = current
        return current

    def reset(self):
        self._last = None
