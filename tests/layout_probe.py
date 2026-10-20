"""Walks the settings app at several window sizes and reports layout faults.

Run by tests/test_settings_layout.py inside a headless mutter, one window
size per process so the sizes run in parallel:

    python3 tests/layout_probe.py result.json "PX13 at 200 %"

Opens the app on the demo configuration of tools/screenshot_settings.py
(nothing is saved), resizes the window to what monitors from 1080p to 4K
give it, visits every page and the main dialogs, and writes the faults as
JSON: widgets that reach past the window's edge, and windows that cannot
shrink to the size.
"""

import copy
import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import screenshot_settings as demo  # noqa: E402
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from config.schema import MacroStep, Profile, SpaceMouseAction, SpaceMouseButton, StepType  # noqa: E402
from config_app import blender_addon  # noqa: E402
from config_app import spacemouse_extra as sx  # noqa: E402
from spacemouse_devices import SPACEPILOT_PRO, PresentScreen  # noqa: E402

# Window sizes in logical pixels: monitors minus the GNOME top bar.
SIZES = {
    "1080p at 150 %": (1280, 688),
    "PX13 at 200 %": (1440, 868),
    "1080p": (1920, 1048),
    "4K": (3840, 2128),
}
PAGES = ("actions", "profiles", "look", "device", "calibration", "spacemouse", "screen", "diagnostics")
CALIBRATION_RESULT = {"circle_diameter": 1012, "center_button_diameter": 388, "center_x": 3341, "center_y": 640}
# Long enough for a resize or a new page to be laid out and mapped.
SETTLE_MS = 120


def describe(widget):
    label = ""
    if isinstance(widget, (Gtk.Label, Gtk.Button)):
        label = widget.get_label() or ""
    elif isinstance(widget, Adw.PreferencesRow):
        label = widget.get_title() or ""
    return f"{type(widget).__name__}{f' “{label}”' if label else ''}"


def unfold(widget):
    """Open folded rows (the dial look of profiles and functions) so they count."""
    for expander in demo._descendants(widget, Adw.ExpanderRow):
        expander.set_expanded(True)


def overflowing(window):
    """The outermost widgets that reach past the window's left or right edge."""
    width = window.get_width()
    found = []

    def walk(widget):
        # Overlay scrollbars sit on the edge on purpose.
        if not widget.get_mapped() or isinstance(widget, Gtk.Scrollbar):
            return
        ok, bounds = widget.compute_bounds(window)
        if ok:
            left, right = bounds.get_x(), bounds.get_x() + bounds.get_width()
            if left < -1 or right > width + 1:
                found.append(f"{describe(widget)} spans {left:.0f}–{right:.0f} of {width}")
                return
        child = widget.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(window)
    return found


def dialogs(app, profile):
    """Open each main dialog in turn; yields its name while it is shown."""
    segment = profile.segments[0]
    app._show_function_library(profile)
    yield "function library"
    app._show_function_editor(profile, segment)
    yield "function editor"
    held = next(
        row for row in demo._descendants(app._window.get_visible_dialog(), Adw.ComboRow)
        if row.get_title() == "Actions for"
    )
    held.set_selected(1)
    yield "function editor, Ctrl held"
    for kind in StepType:
        demo.close_dialogs(app)
        app._show_step_dialog(segment, "macro_cw", None, lambda: None)
        dialog = app._window.get_visible_dialog()
        combo = next(demo._descendants(dialog, Adw.ComboRow))
        names = [combo.get_model().get_string(index) for index in range(combo.get_model().get_n_items())]
        labels = [label for label, step_type in demo.app_main.ACTION_TYPES if step_type is kind]
        if not labels or labels[0] not in names:
            continue
        combo.set_selected(names.index(labels[0]))
        yield f"action: {labels[0]}"
    demo.close_dialogs(app)
    group = demo.apps_group()
    profile.segments.append(group)
    app._show_function_editor(profile, group)
    yield "group editor"
    demo.close_dialogs(app)
    app._show_function_library(profile, group)
    yield "function library of a group"
    demo.close_dialogs(app)
    app._show_function_editor(profile, group.items[0], group)
    yield "function in a group"
    profile.segments.remove(group)
    demo.close_dialogs(app)
    app._choose_pack_variant(demo.app_main.pack_for_app("org.blender.Blender.desktop"), "Blender", lambda _advanced: None)
    yield "basic or advanced pack"
    demo.close_dialogs(app)
    app._show_skin_editor(None, {"name": "Probe", "base": "proart"})
    yield "skin editor"
    demo.close_dialogs(app)
    # A SpaceMouse button with a long macro and a function by name, for all
    # applications and for a profile.
    title = profile.segments[0].title
    app.config.spacemouse.buttons = {
        "13": SpaceMouseButton(
            press=SpaceMouseAction("macro", keys=["KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_TAB"], macro=[
                MacroStep(type=StepType.KEYSTROKE, keys=["KEY_LEFTCTRL", "KEY_C"]),
                MacroStep(type=StepType.SHELL, script="notify-send a-rather-long-command-line --with-options"),
            ]),
            hold=SpaceMouseAction("function", function=title),
        ),
    }
    app._sm_scope = None
    app._refresh_spacemouse_page()
    app._show_spacemouse_button_dialog(18)
    yield "SpaceMouse button editor of a dual-function key"
    demo.close_dialogs(app)
    app._show_spacemouse_button_dialog(0)
    yield "SpaceMouse button editor of a plain key"
    demo.close_dialogs(app)
    app._sm_scope = profile
    app._refresh_spacemouse_page()
    app._show_spacemouse_button_dialog(18)
    yield "SpaceMouse button editor of a profile"
    demo.close_dialogs(app)
    app._sm_scope = None
    app._refresh_spacemouse_page()
    yield from context_dialogs(app, profile)
    yield from spacemouse_extra_dialogs(app, profile)


def context_dialogs(app, profile):
    """A profile's contexts, the editor of one and the macro recorder."""
    from config.schema import ProfileContext
    from config_app.context_dialogs import ContextEditor
    from config_app.recorder_dialog import MacroRecorderDialog

    first = ProfileContext(
        "Edit Mesh", title="Blender | .blend", app_modes=["EDIT_MESH"],
        segments=copy.deepcopy(profile.segments),
        spacemouse_buttons={"13": SpaceMouseButton(press=SpaceMouseAction("macro", macro=[
            MacroStep(type=StepType.KEYSTROKE, keys=["KEY_E"])]))},
        screen={"start_page": "dial"},
    )
    second = ProfileContext("Sculpting, a context with a rather long name to see how the row copes",
                            app_modes=["SCULPT", "PAINT_WEIGHT"])
    profile.contexts = [first, second, ProfileContext("Nothing yet")]
    app._changed()
    app._view_stack.set_visible_child_name("actions")
    yield "profile with contexts"
    editor = ContextEditor(app, profile, first)
    editor.present()
    yield "context editor"
    editor.catalog = {
        "name": "Blender", "connected": True, "context": {"mode": "OBJECT", "label": "Object Mode", "detail": "Selected"},
        "commands": [{"id": "a", "modes": ["EDIT_MESH", "SCULPT", "POSE", "PARTICLE", "PAINT_VERTEX", "OBJECT"]}],
    }
    editor._refill_modes()
    editor._show_live()
    editor.try_entry.set_text("Cube.blend - Blender 5.2")
    yield "context editor with the application's modes"
    editor.title_entry.set_text(" | ")
    yield "context editor with a bad title condition"
    # No toast about the unsaved condition in the next pictures.
    editor.title_entry.set_text("Blender")
    demo.close_dialogs(app)
    editor = ContextEditor(app, profile, second)
    editor.present()
    yield "context editor without an own menu"
    demo.close_dialogs(app)
    editor = ContextEditor(app, profile, profile.contexts[2])
    editor.present()
    yield "context editor of a context without conditions"
    demo.close_dialogs(app)
    recorder = MacroRecorderDialog(app._window, 2, lambda _steps, _replace: None, nodes=[])
    recorder.present()
    yield "macro recorder: countdown without a readable keyboard"
    recorder.recording.events.clear()
    recorder.state = "recording"
    for number, name in enumerate(("KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_TAB", "KEY_A", "KEY_B", "KEY_ENTER")):
        recorder.recording.add(name, 1, number * 0.6)
        recorder.recording.add(name, 0, number * 0.6 + 0.1)
    recorder._show_state()
    yield "macro recorder: recording"
    recorder._stop()
    recorder.pauses.set_active(True)
    yield "macro recorder: stopped, with the pauses kept"
    demo.close_dialogs(app)
    profile.contexts = []
    app._changed()


BLENDER_CATALOG = json.dumps({
    "app": "blender", "name": "Blender", "version": "5.2.0", "connected": True, "context": {},
    "commands": [
        {"id": "view.frame_selected", "title": "Frame selected", "category": "View", "keys": "Numpad ."},
        {"id": "mesh.extrude_region_move", "title": "Extrude a region and move it along its normal", "category": "Modeling",
         "keys": "E"},
        {"id": "mesh.bevel", "title": "Bevel", "category": "Modeling", "keys": "Ctrl+B"},
        {"id": "wm.search_menu", "title": "Search for a command anywhere in Blender", "category": "UI", "keys": "F3"},
    ],
})


def spacemouse_extra_dialogs(app, profile):
    """Layers, cap motion, app commands and the Blender add-on on the SpaceMouse page."""
    config = app.config
    blender = Profile(name="Blender", app_id="org.blender.Blender.desktop")
    config.profiles.append(blender)
    saved = (config.spacemouse.buttons, config.spacemouse.layers, config.spacemouse.motion)
    config.spacemouse.buttons = {
        "0": SpaceMouseButton(press=SpaceMouseAction("layer", layer="Shift layer", layer_mode="toggle")),
        "12": SpaceMouseButton(press=SpaceMouseAction("layer", layer="Shift layer", layer_mode="latch"),
                               hold=SpaceMouseAction("motion", motion="faster")),
        "13": SpaceMouseButton(press=SpaceMouseAction("motion", motion="translation")),
        "14": SpaceMouseButton(press=SpaceMouseAction(
            "app", command="mesh.extrude_region_move", keys=["KEY_E"])),
    }
    config.spacemouse.layers = {"Shift layer": {
        "13": SpaceMouseButton(press=SpaceMouseAction("macro", keys=["KEY_LEFTCTRL", "KEY_Z"])),
    }, "A layer with a rather long name to see the row wrap": {}}
    config.spacemouse.motion = {"sensitivity": 1.5, "rotation": False}
    app._sm_catalogs["blender"] = sx.parse_catalog(BLENDER_CATALOG)
    app._view_stack.set_visible_child_name("spacemouse")
    app._sm_scope, app._sm_layer = None, None
    app._refresh_spacemouse_page()
    yield "SpaceMouse page with layers and global motion"
    app._sm_layer = "Shift layer"
    app._refresh_spacemouse_page()
    yield "SpaceMouse page in a layer"
    app._show_spacemouse_button_dialog(13)
    yield "SpaceMouse button editor in a layer"
    demo.close_dialogs(app)
    app._sm_layer = None
    app._refresh_spacemouse_page()
    for number, name in ((12, "a layer switch"), (13, "a cap motion toggle"), (14, "an offline app command")):
        app._show_spacemouse_button_dialog(number)
        yield f"SpaceMouse button editor with {name}"
        demo.close_dialogs(app)
    app._sm_catalogs.pop("blender")
    app._sm_scope = blender
    blender.spacemouse_buttons = {"14": SpaceMouseButton(press=SpaceMouseAction(
        "app", command="mesh.extrude_region_move", keys=["KEY_E"]))}
    blender.spacemouse_motion = {"sensitivity": 2.0, "rotation": True, "translation": False}
    app._refresh_spacemouse_page()
    yield "SpaceMouse page of a profile with its own motion"
    app._show_spacemouse_button_dialog(14)
    yield "SpaceMouse button editor with an app command without a list"
    demo.close_dialogs(app)
    app._sm_catalogs["blender"] = sx.parse_catalog(BLENDER_CATALOG)
    app._show_spacemouse_button_dialog(14)
    yield "SpaceMouse button editor with an app command from the list"
    demo.close_dialogs(app)
    app._show_command_picker("blender", app._sm_catalogs["blender"], lambda _command: None)
    yield "app command picker"
    demo.close_dialogs(app)
    app._show_blender_result(blender_addon.InstallResult(
        True, "blender", None, "$ blender --command extension build --source-dir /opt/asus-turnpad/integrations/blender/turnpad "
        "--output-dir /home/user/.cache/asus-turnpad/blender\nbuilding: turnpad-1.0.0.zip\ncomplete"))
    yield "Blender add-on installed"
    demo.close_dialogs(app)
    app._show_blender_result(blender_addon.InstallResult(
        False, "zip", Path("/home/user/.cache/asus-turnpad/blender/turnpad-1.0.0.zip"),
        "Blender was not found on the PATH; building the zip here.", blender_addon.MANUAL_STEPS))
    yield "Blender add-on built by hand"
    demo.close_dialogs(app)
    config.profiles.remove(blender)
    config.spacemouse.buttons, config.spacemouse.layers, config.spacemouse.motion = saved
    app._sm_scope, app._sm_layer = None, None
    app._refresh_spacemouse_page()
    app._view_stack.set_visible_child_name("actions")


def setup_assistant_dialogs(app):
    """The first-run assistant (both steps, with the mouse rows and the hint
    about two openers) and the key capture, with a mouse and without one."""
    config = app.config
    saved = (config.setup_done, config.mouse.enabled, config.emulation.enabled, config.spacemouse.enabled)
    config.setup_done = False
    app._show_setup_assistant()
    yield "setup assistant: what was found"
    assistant = app._setup_assistant
    assistant._show_choice()
    yield "setup assistant: DialPad mode"
    assistant._radios["mouse"].set_active(True)
    assistant.sm_row.set_active(True)
    yield "setup assistant: Mouse-only mode with a SpaceMouse"
    assistant.picker.capture()
    yield "key capture"
    demo.close_dialogs(app)
    demo.close_dialogs(app)
    demo.close_dialogs(app)
    config.setup_done, config.mouse.enabled, config.emulation.enabled, config.spacemouse.enabled = saved
    # Closing the assistant by hand must not have changed the modes.
    app._rebuild_page("diagnostics")


def visibility_faults(app):
    """Pages the hardware does not need are hidden, Show all pages brings them back."""
    faults = []
    config = app.config
    saved = (config.mouse.enabled, config.emulation.enabled, config.spacemouse.enabled)
    found, status, show_all = app._found, app._driver_status, app._app_settings["show_all_pages"]
    config.mouse.enabled = config.emulation.enabled = config.spacemouse.enabled = False
    app._found = demo.app_main.hw.Detected()
    app._driver_status = None
    app._app_settings["show_all_pages"] = False
    app._view_stack.set_visible_child_name("actions")
    app._update_page_visibility()
    for name in ("calibration", "spacemouse"):
        if app._view_pages[name].get_visible():
            faults.append(f"the {name} page shows on a computer without its hardware")
    app._app_settings["show_all_pages"] = True
    app._update_page_visibility()
    for name in PAGES:
        if not app._view_pages[name].get_visible():
            faults.append(f"Show all pages does not show the {name} page")
    config.mouse.enabled, config.emulation.enabled, config.spacemouse.enabled = saved
    app._found, app._driver_status, app._app_settings["show_all_pages"] = found, status, show_all
    app._update_page_visibility()
    return faults


def sidebar_faults(app):
    """The list that stands in for Adw.ViewSwitcherSidebar before Libadwaita 1.9
    must follow the pages both ways."""
    sidebar = next((box for box in demo._descendants(app._window, Gtk.ListBox)
                    if box.has_css_class("navigation-sidebar")), None)
    if hasattr(Adw, "ViewSwitcherSidebar"):
        return []
    if sidebar is None:
        return ["no page sidebar"]
    faults = []
    for index, page in enumerate(PAGES):
        app._view_stack.set_visible_child_name(page)
        selected = sidebar.get_selected_row()
        if selected is None or selected.get_index() != index:
            faults.append(f"sidebar does not follow the {page} page")
    for index, page in reversed(list(enumerate(PAGES))):
        sidebar.select_row(sidebar.get_row_at_index(index))
        if app._view_stack.get_visible_child_name() != page:
            faults.append(f"sidebar row {index} does not show the {page} page")
    return faults


def steps(app, result, size_names):
    window = app._window
    profile = app.config.profiles[0]
    result.extend(sidebar_faults(app))
    result.extend(visibility_faults(app))
    app._select_profile(profile)
    for size_name in size_names:
        width, height = SIZES[size_name]
        yield from demo.resize(window, width, height)
        minimum = window.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        if minimum > width:
            result.append(f"{size_name}: the window needs {minimum} px but the screen gives {width}")
        if abs(window.get_width() - width) > 1:
            result.append(f"{size_name}: window is {window.get_width()} px wide, wanted {width}")
        for page in PAGES:
            app._view_stack.set_visible_child_name(page)
            unfold(window)
            yield
            result.extend(f"{size_name}, {page} page: {fault}" for fault in overflowing(window))
        # The Screen page again for a profile with its own pages, start page and colour.
        app._view_stack.set_visible_child_name("screen")
        app._screen_scope = profile
        profile.screen = {"pages": ["dial", "keys", "clock"], "start_page": "dial", "accent": "#33aa66"}
        app._refresh_screen_page()
        unfold(window)
        yield
        result.extend(f"{size_name}, screen page of a profile: {fault}" for fault in overflowing(window))
        app._screen_scope = None
        profile.screen = {}
        app._refresh_screen_page()
        # A finished guided calibration waiting for Keep or Revert.
        app._view_stack.set_visible_child_name("calibration")
        app._apply_calibration(CALIBRATION_RESULT, "proartp16")
        yield
        result.extend(f"{size_name}, calibration review: {fault}" for fault in overflowing(window))
        app._finish_calibration_review(keep=False, quiet=True)
        app._view_stack.set_visible_child_name("actions")
        for dialog_name in dialogs(app, profile):
            unfold(window)
            yield
            if os.environ.get("TURNPAD_PROBE_TRACE"):
                print(f"{size_name}: {dialog_name}: {window.get_visible_dialog() is not None}", file=sys.stderr)
            result.extend(f"{size_name}, {dialog_name}: {fault}" for fault in overflowing(window))
        for dialog_name in setup_assistant_dialogs(app):
            yield
            if os.environ.get("TURNPAD_PROBE_TRACE"):
                print(f"{size_name}: {dialog_name}: {window.get_visible_dialog() is not None}", file=sys.stderr)
            result.extend(f"{size_name}, {dialog_name}: {fault}" for fault in overflowing(window))
    app.quit()


def main():
    output = Path(sys.argv[1])
    size_names = sys.argv[2:] or list(SIZES)
    config = demo.demo_config()
    # Show every dial look row: the screen picker, the side of the pointer
    # and the fade level.
    config.overlay.position = "custom"
    config.overlay.visibility = "fade"
    config.profiles[0].overlay = {"position": "pointer", "pointer_side": "above-left", "pointer_gap": 40,
                                  "visibility": "fade"}
    config.profiles[0].segments[0].overlay = {"position": "custom", "visibility": "fade"}
    # Both openers on: the Driver page shows its hint.
    config.mouse.enabled = True
    config.spacemouse.enabled = True
    demo.app_main.CONFIG_FILE = Path(__file__)
    demo.app_main.load_config = lambda: config
    demo.app_main.load_skins = demo.builtin_skins
    # The Screen page shows only with a SpacePilot Pro plugged in.
    demo.app_main.screens_present = lambda: [PresentScreen(SPACEPILOT_PRO, 1, 4, "/probe")]
    # A computer with every device, every page shown.
    demo.demo_hardware()
    Adw.init()
    app = demo.ScreenshotApp()
    app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
    result = []

    def start(_app):
        runner = steps(app, result, size_names)

        def tick():
            try:
                next(runner)
            except StopIteration:
                return GLib.SOURCE_REMOVE
            except Exception:
                # Without this the loop stops but the app keeps running.
                result.append(f"the probe failed:\n{traceback.format_exc()}")
                app.quit()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        GLib.timeout_add(SETTLE_MS, tick)

    app.connect("activate", start)
    app.run([])
    output.write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
