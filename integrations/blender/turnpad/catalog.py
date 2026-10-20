"""The curated command list the add-on offers to the Turnpad driver.

Nothing but these entries ever runs: the driver sends an id, the add-on looks
it up here. No code and no operator names cross the socket. Pure data, no bpy.

Entry fields:
  id        stable name ("view.frame_selected"), what profiles store
  title     what the dial and the screen show
  category  group in the picker
  keys      Blender's default shortcut (a hint, and the driver's fallback)
  modes     bpy.context.mode values where it makes sense, [] = everywhere
  kind      "op"   operator started like the user would (INVOKE_DEFAULT:
                   interactive ones follow the mouse)
            "call" operator executed at once with `props` (EXEC_DEFAULT)
            "pie"  pie menu `target` at the pointer (wm.call_menu_pie)
            "menu" menu `target` at the pointer (wm.call_menu)
  target    operator "category.name", or the menu class name
  props     operator properties (fixed here, never from the socket)
  area      area type the command needs: "VIEW_3D", "ANY" (any area, a 3D
            view preferred) or "NONE" (a data operator; run in a 3D view's
            context when a window exists, bare otherwise)
"""

from dataclasses import dataclass, field

KINDS = ("op", "call", "pie", "menu")
AREAS = ("VIEW_3D", "ANY", "NONE")

EDIT_MODES = ("EDIT_MESH",)
OBJECT = ("OBJECT",)
SCULPT = ("SCULPT",)


@dataclass(frozen=True)
class Command:
    id: str
    title: str
    category: str
    keys: str
    kind: str
    target: str
    props: dict = field(default_factory=dict)
    modes: tuple = ()
    area: str = "VIEW_3D"
    icon: str = ""

    def wire(self):
        """The entry as the driver's catalog message has it."""
        return {"id": self.id, "title": self.title, "category": self.category,
                "icon": self.icon, "keys": self.keys, "modes": list(self.modes)}


def _make(category):
    def add(id, title, kind, target, keys="", props=None, modes=(), area="VIEW_3D"):
        return Command(id, title, category, keys, kind, target, dict(props or {}),
                       tuple(modes), area)
    return add


def _radial(prop, secondary):
    """wm.radial_control properties of the sculpt brush, as Blender's own keymap
    passes them (size on F, strength on Shift+F)."""
    brush = "tool_settings.sculpt.brush"
    unified = "tool_settings.sculpt.unified_paint_settings"
    return {
        "data_path_primary": "%s.%s" % (brush, prop),
        "data_path_secondary": "%s.%s" % (unified, prop),
        "use_secondary": "%s.%s" % (unified, secondary),
        "rotation_path": "%s.texture_slot.angle" % brush,
        "color_path": "%s.cursor_color_add" % brush,
        "fill_color_path": "",
        "fill_color_override_path": "",
        "fill_color_override_test_path": "",
        "zoom_path": "",
        "image_id": brush,
        "secondary_tex": False,
    }


_v = _make("View")
_m = _make("Modes")
_e = _make("Modeling")
_o = _make("Objects")
_t = _make("Transform")
_s = _make("Sculpt")
_a = _make("Animation")
_u = _make("UI")

CATALOG = (
    # View
    _v("view.frame_selected", "Frame selected", "call", "view3d.view_selected", "Numpad .",
       {"use_all_regions": False}),
    _v("view.frame_all", "Frame all", "call", "view3d.view_all", "Home", {"center": False}),
    _v("view.front", "Front view", "call", "view3d.view_axis", "Numpad 1", {"type": "FRONT"}),
    _v("view.back", "Back view", "call", "view3d.view_axis", "Ctrl+Numpad 1", {"type": "BACK"}),
    _v("view.right", "Right view", "call", "view3d.view_axis", "Numpad 3", {"type": "RIGHT"}),
    _v("view.left", "Left view", "call", "view3d.view_axis", "Ctrl+Numpad 3", {"type": "LEFT"}),
    _v("view.top", "Top view", "call", "view3d.view_axis", "Numpad 7", {"type": "TOP"}),
    _v("view.bottom", "Bottom view", "call", "view3d.view_axis", "Ctrl+Numpad 7", {"type": "BOTTOM"}),
    _v("view.orbit_left", "Orbit left", "call", "view3d.view_orbit", "Numpad 4", {"type": "ORBITLEFT"}),
    _v("view.orbit_right", "Orbit right", "call", "view3d.view_orbit", "Numpad 6", {"type": "ORBITRIGHT"}),
    _v("view.persp_ortho", "Perspective / orthographic", "call", "view3d.view_persportho", "Numpad 5"),
    _v("view.camera", "Camera view", "call", "view3d.view_camera", "Numpad 0"),
    _v("view.local_view", "Local view", "call", "view3d.localview", "Numpad /"),
    _v("view.xray", "Toggle X-ray", "call", "view3d.toggle_xray", "Alt+Z"),
    _v("view.wireframe", "Toggle wireframe", "call", "view3d.toggle_shading", "Shift+Z",
       {"type": "WIREFRAME"}),
    _v("view.overlays", "Toggle overlays", "call", "wm.context_toggle", "Shift+Alt+Z",
       {"data_path": "space_data.overlay.show_overlays"}),
    _v("view.cursor_to_selected", "Cursor to selected", "call", "view3d.snap_cursor_to_selected",
       "Shift+S"),
    _v("view.cursor_to_center", "Cursor to world origin", "call", "view3d.snap_cursor_to_center",
       "Shift+C"),
    _v("view.pie_view", "View pie", "pie", "VIEW3D_MT_view_pie", "`"),
    _v("view.pie_shading", "Shading pie", "pie", "VIEW3D_MT_shading_pie", "Z"),

    # Modes
    _m("mode.toggle_edit", "Edit mode on / off", "call", "object.mode_set", "Tab",
       {"mode": "EDIT", "toggle": True}),
    _m("mode.object", "Object mode", "call", "object.mode_set", "",
       {"mode": "OBJECT"}),
    _m("mode.sculpt", "Sculpt mode", "call", "object.mode_set", "",
       {"mode": "SCULPT"}),
    _m("mode.pie", "Mode pie", "pie", "VIEW3D_MT_object_mode_pie", "Ctrl+Tab"),
    _m("mode.select_vertex", "Vertex select", "call", "mesh.select_mode", "1",
       {"type": "VERT"}, EDIT_MODES),
    _m("mode.select_edge", "Edge select", "call", "mesh.select_mode", "2",
       {"type": "EDGE"}, EDIT_MODES),
    _m("mode.select_face", "Face select", "call", "mesh.select_mode", "3",
       {"type": "FACE"}, EDIT_MODES),

    # Modeling (Edit Mesh)
    _e("mesh.extrude", "Extrude", "op", "mesh.extrude_region_move", "E", None, EDIT_MODES),
    _e("mesh.inset", "Inset faces", "op", "mesh.inset", "I", None, EDIT_MODES),
    _e("mesh.bevel", "Bevel", "op", "mesh.bevel", "Ctrl+B", None, EDIT_MODES),
    _e("mesh.loop_cut", "Loop cut", "op", "mesh.loopcut_slide", "Ctrl+R", None, EDIT_MODES),
    _e("mesh.knife", "Knife", "op", "mesh.knife_tool", "K", None, EDIT_MODES),
    _e("mesh.merge", "Merge", "menu", "VIEW3D_MT_edit_mesh_merge", "M", None, EDIT_MODES),
    _e("mesh.fill", "Fill", "call", "mesh.fill", "Alt+F", None, EDIT_MODES),
    _e("mesh.edge_face_add", "Make edge / face", "call", "mesh.edge_face_add", "F", None, EDIT_MODES),
    _e("mesh.bridge", "Bridge edge loops", "call", "mesh.bridge_edge_loops", "", None, EDIT_MODES),
    _e("mesh.subdivide", "Subdivide", "call", "mesh.subdivide", "", None, EDIT_MODES),
    _e("mesh.separate", "Separate", "op", "mesh.separate", "P", None, EDIT_MODES),
    _e("mesh.dissolve", "Dissolve", "op", "mesh.dissolve_mode", "Ctrl+X", None, EDIT_MODES),
    _e("mesh.delete", "Delete menu", "menu", "VIEW3D_MT_edit_mesh_delete", "X", None, EDIT_MODES),
    _e("mesh.rip", "Rip", "op", "mesh.rip_move", "V", None, EDIT_MODES),
    _e("mesh.duplicate", "Duplicate", "op", "mesh.duplicate_move", "Shift+D", None, EDIT_MODES),
    _e("mesh.recalc_normals", "Recalculate normals", "call", "mesh.normals_make_consistent",
       "Shift+N", {"inside": False}, EDIT_MODES),
    _e("mesh.select_all", "Select all (mesh)", "call", "mesh.select_all", "A",
       {"action": "SELECT"}, EDIT_MODES),
    _e("mesh.select_none", "Select none (mesh)", "call", "mesh.select_all", "Alt+A",
       {"action": "DESELECT"}, EDIT_MODES),
    _e("mesh.select_invert", "Invert selection (mesh)", "call", "mesh.select_all", "Ctrl+I",
       {"action": "INVERT"}, EDIT_MODES),
    _e("mesh.select_linked", "Select linked", "call", "mesh.select_linked", "Ctrl+L", None, EDIT_MODES),
    _e("mesh.select_more", "Grow selection", "call", "mesh.select_more", "Ctrl+Numpad +", None, EDIT_MODES),
    _e("mesh.select_less", "Shrink selection", "call", "mesh.select_less", "Ctrl+Numpad -", None, EDIT_MODES),
    _e("mesh.hide", "Hide selected (mesh)", "call", "mesh.hide", "H", {"unselected": False}, EDIT_MODES),
    _e("mesh.reveal", "Reveal hidden (mesh)", "call", "mesh.reveal", "Alt+H", None, EDIT_MODES),
    _e("mesh.shade_smooth", "Shade smooth (mesh)", "call", "mesh.faces_shade_smooth", "", None, EDIT_MODES),
    _e("mesh.shade_flat", "Shade flat (mesh)", "call", "mesh.faces_shade_flat", "", None, EDIT_MODES),

    # Objects
    _o("object.add_menu", "Add menu", "menu", "VIEW3D_MT_add", "Shift+A", None, ()),
    _o("object.duplicate", "Duplicate", "op", "object.duplicate_move", "Shift+D", None, OBJECT),
    _o("object.duplicate_linked", "Duplicate linked", "op", "object.duplicate_move_linked", "Alt+D",
       None, OBJECT),
    _o("object.shade_smooth", "Shade smooth", "call", "object.shade_smooth", "", None, OBJECT),
    _o("object.shade_flat", "Shade flat", "call", "object.shade_flat", "", None, OBJECT),
    _o("object.join", "Join", "call", "object.join", "Ctrl+J", None, OBJECT),
    _o("object.parent_set", "Parent", "op", "object.parent_set", "Ctrl+P", None, OBJECT),
    _o("object.apply_all", "Apply all transforms", "call", "object.transform_apply", "Ctrl+A",
       {"location": True, "rotation": True, "scale": True}, OBJECT),
    _o("object.apply_scale", "Apply scale", "call", "object.transform_apply", "",
       {"location": False, "rotation": False, "scale": True}, OBJECT),
    _o("object.origin_to_geometry", "Origin to geometry", "call", "object.origin_set", "",
       {"type": "ORIGIN_GEOMETRY"}, OBJECT),
    _o("object.origin_to_cursor", "Origin to 3D cursor", "call", "object.origin_set", "",
       {"type": "ORIGIN_CURSOR"}, OBJECT),
    _o("object.hide", "Hide selected", "call", "object.hide_view_set", "H", {"unselected": False}, OBJECT),
    _o("object.hide_unselected", "Hide unselected", "call", "object.hide_view_set", "Shift+H",
       {"unselected": True}, OBJECT),
    _o("object.unhide", "Unhide all", "call", "object.hide_view_clear", "Alt+H", None, OBJECT),
    _o("object.delete", "Delete", "call", "object.delete", "X", {"use_global": False}, OBJECT),
    _o("object.select_all", "Select all", "call", "object.select_all", "A",
       {"action": "SELECT"}, OBJECT, "NONE"),
    _o("object.select_none", "Select none", "call", "object.select_all", "Alt+A",
       {"action": "DESELECT"}, OBJECT, "NONE"),
    _o("object.select_invert", "Invert selection", "call", "object.select_all", "Ctrl+I",
       {"action": "INVERT"}, OBJECT, "NONE"),
    _o("object.move_to_collection", "Move to collection", "op", "object.move_to_collection", "M",
       None, OBJECT),

    # Transform
    _t("transform.grab", "Grab / move", "op", "transform.translate", "G"),
    _t("transform.rotate", "Rotate", "op", "transform.rotate", "R"),
    _t("transform.scale", "Scale", "op", "transform.resize", "S"),
    _t("transform.pie_snap", "Snap pie", "pie", "VIEW3D_MT_snap_pie", "Shift+S"),
    _t("transform.pie_pivot", "Pivot pie", "pie", "VIEW3D_MT_pivot_pie", "."),
    _t("transform.pie_orientation", "Orientation pie", "pie", "VIEW3D_MT_orientations_pie", ","),
    _t("transform.snap", "Snapping on / off", "call", "wm.context_toggle", "Shift+Tab",
       {"data_path": "tool_settings.use_snap"}),
    _t("transform.proportional", "Proportional editing", "call", "wm.context_toggle", "O",
       {"data_path": "tool_settings.use_proportional_edit"}, EDIT_MODES),
    _t("transform.proportional_object", "Proportional editing (object)", "call", "wm.context_toggle",
       "O", {"data_path": "tool_settings.use_proportional_edit_objects"}, OBJECT),

    # Sculpt
    _s("sculpt.brush_size", "Brush size", "op", "wm.radial_control", "F",
       _radial("size", "use_unified_size"), SCULPT),
    _s("sculpt.brush_strength", "Brush strength", "op", "wm.radial_control", "Shift+F",
       _radial("strength", "use_unified_strength"), SCULPT),
    _s("sculpt.pie_mask", "Mask pie", "pie", "VIEW3D_MT_sculpt_mask_edit_pie", "A", None, SCULPT),
    _s("sculpt.pie_automasking", "Auto-masking pie", "pie", "VIEW3D_MT_sculpt_automasking_pie", "Alt+A",
       None, SCULPT),
    _s("sculpt.pie_face_sets", "Face sets pie", "pie", "VIEW3D_MT_sculpt_face_sets_edit_pie", "Alt+W",
       None, SCULPT),
    _s("sculpt.mask_invert", "Invert mask", "call", "paint.mask_flood_fill", "Ctrl+I",
       {"mode": "INVERT"}, SCULPT),
    _s("sculpt.mask_clear", "Clear mask", "call", "paint.mask_flood_fill", "Alt+M",
       {"mode": "VALUE", "value": 0.0}, SCULPT),
    _s("sculpt.voxel_remesh", "Voxel remesh", "call", "object.voxel_remesh", "Ctrl+R", None, SCULPT),
    _s("sculpt.quadriflow", "Quad remesh", "op", "object.quadriflow_remesh", "", None, SCULPT),
    _s("sculpt.dyntopo", "Dyntopo on / off", "call", "sculpt.dynamic_topology_toggle", "Ctrl+D",
       None, SCULPT),
    _s("sculpt.symmetrize", "Symmetrize", "call", "sculpt.symmetrize", "", None, SCULPT),

    # Animation
    _a("anim.insert_keyframe", "Insert keyframe", "call", "anim.keyframe_insert", "I"),
    _a("anim.pie_keyframe", "Keyframe pie", "pie", "ANIM_MT_keyframe_insert_pie", "I (drag)"),
    _a("anim.delete_keyframe", "Delete keyframe", "call", "anim.keyframe_delete_v3d", "Alt+I"),
    _a("anim.play", "Play / pause", "call", "screen.animation_play", "Space", None, (), "ANY"),
    _a("anim.next_keyframe", "Next keyframe", "call", "screen.keyframe_jump", "Up", {"next": True},
       (), "ANY"),
    _a("anim.prev_keyframe", "Previous keyframe", "call", "screen.keyframe_jump", "Down",
       {"next": False}, (), "ANY"),
    _a("anim.next_frame", "Next frame", "call", "screen.frame_offset", "Right", {"delta": 1}, (), "ANY"),
    _a("anim.prev_frame", "Previous frame", "call", "screen.frame_offset", "Left", {"delta": -1},
       (), "ANY"),
    _a("anim.jump_start", "Jump to start", "call", "screen.frame_jump", "Shift+Left", {"end": False},
       (), "ANY"),
    _a("anim.jump_end", "Jump to end", "call", "screen.frame_jump", "Shift+Right", {"end": True},
       (), "ANY"),
    _a("anim.auto_key", "Auto keying", "call", "wm.context_toggle", "",
       {"data_path": "tool_settings.use_keyframe_insert_auto"}, (), "ANY"),

    # UI
    _u("ui.search", "Search commands", "call", "wm.search_menu", "F3", None, (), "ANY"),
    _u("ui.maximize_area", "Maximize area", "call", "screen.screen_full_area", "Ctrl+Space", None, (), "ANY"),
    _u("ui.sidebar", "Sidebar", "call", "wm.context_toggle", "N",
       {"data_path": "space_data.show_region_ui"}),
    _u("ui.toolbar", "Toolbar", "call", "wm.context_toggle", "T",
       {"data_path": "space_data.show_region_toolbar"}),
    _u("ui.favorites", "Quick favorites", "menu", "SCREEN_MT_user_menu", "Q", None, (), "ANY"),
    _u("ui.undo", "Undo", "call", "ed.undo", "Ctrl+Z", None, (), "NONE"),
    _u("ui.redo", "Redo", "call", "ed.redo", "Ctrl+Shift+Z", None, (), "NONE"),
    _u("ui.undo_history", "Undo history", "op", "ed.undo_history", "Ctrl+Alt+Z", None, (), "ANY"),
    _u("ui.save", "Save", "call", "wm.save_mainfile", "Ctrl+S", None, (), "NONE"),
    _u("ui.render", "Render image", "op", "render.render", "F12", {"use_viewport": True}, (), "NONE"),
    _u("ui.render_view", "Show last render", "call", "render.view_show", "F11", None, (), "ANY"),
)

BY_ID = {command.id: command for command in CATALOG}


def wire_catalog():
    """The catalog message's entries."""
    return [command.wire() for command in CATALOG]


def validate():
    """Problems of the table itself (duplicate ids, unknown kinds...); the
    tests call it, the add-on need not."""
    problems = []
    seen = set()
    for command in CATALOG:
        if command.id in seen:
            problems.append("duplicate id " + command.id)
        seen.add(command.id)
        if command.kind not in KINDS:
            problems.append("%s: kind %r" % (command.id, command.kind))
        if command.area not in AREAS:
            problems.append("%s: area %r" % (command.id, command.area))
        if command.kind in ("op", "call") and command.target.count(".") != 1:
            problems.append("%s: operator %r" % (command.id, command.target))
        if command.kind in ("pie", "menu") and "." in command.target:
            problems.append("%s: menu %r" % (command.id, command.target))
        if not command.title or not command.category:
            problems.append("%s: no title or category" % command.id)
    return problems
