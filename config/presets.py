"""Built-in function library and application packs.

Mirrors the ASUS Dial / ProArt Creator Hub idea: a catalogue of ready
functions that can be dropped into any profile, plus pre-programmed packs
for popular creative and productivity applications.  Every preset is a
plain ``Segment`` and remains fully editable afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from config.labels import GROUP_ICON
from config.schema import MacroStep, Profile, Segment, SpaceMouseAction, SpaceMouseButton, StepType

VOLUME_QUERY = "pactl get-sink-volume @DEFAULT_SINK@ | grep -oP '\\d+%' | head -n 1 | tr -d '%'"
BRIGHTNESS_QUERY = "brightnessctl -m | cut -d, -f4 | tr -d '%'"
KBD_LIGHT_QUERY = "brightnessctl -d '*kbd_backlight' -m | cut -d, -f4 | tr -d '%'"

COLORS = {
    "audio": "#6cb6ff",
    "light": "#f4c95d",
    "navigate": "#9ad17b",
    "zoom": "#e7b98d",
    "history": "#c99bff",
    "media": "#ff8f8f",
    "tabs": "#7fd4c8",
    "brush": "#ffad66",
    "time": "#8fa8ff",
    "record": "#ff6b6b",
    "cut": "#ffb86b",
    "layers": "#7fd4c8",
}


_COLOR_KEYWORDS = (
    (("volume", "mute", "audio"), "audio"),
    (("bright", "light", "exposure"), "light"),
    (("scroll", "arrow", "photo"), "navigate"),
    (("zoom", "font", "rotate"), "zoom"),
    (("history", "undo", "redo", "back", "edit"), "history"),
    (("media", "track", "seek", "play", "shuttle"), "media"),
    (("tab", "workspace", "notif"), "tabs"),
    (("brush", "opacity", "scale", "label", "rating"), "brush"),
    (("frame", "timeline"), "time"),
)


def suggested_color(title: str, index: int = 0) -> str:
    """Colour for functions that have none, so older profiles look alike."""
    name = (title or "").lower()
    for words, key in _COLOR_KEYWORDS:
        if any(word in name for word in words):
            return COLORS[key]
    palette = list(COLORS.values())
    return palette[index % len(palette)]


def segment_color(segment: Segment, index: int = 0) -> str:
    return segment.color or suggested_color(segment.title, index)


def keys(*names: str, title: str | None = None) -> MacroStep:
    return MacroStep(type=StepType.KEYSTROKE, keys=list(names), title=title)


def app_step(command: str, *names: str, title: str | None = None) -> MacroStep:
    """A command of the focused application by id, through its app link (an
    add-on or a bridge); ``names`` are the keys pressed when it has none."""
    return MacroStep(type=StepType.APP, command=command, keys=list(names) or None, title=title)


def scroll(
    direction: str,
    *,
    amount: int = 120,
    axis: str | None = None,
    modifiers: list[str] | None = None,
    title: str | None = None,
) -> MacroStep:
    return MacroStep(
        type=StepType.SCROLL,
        direction=direction,
        value=amount,
        axis=axis,
        modifiers=modifiers,
        title=title,
    )


def _segment(
    title: str,
    cw: list[MacroStep],
    ccw: list[MacroStep],
    center: list[MacroStep] | None = None,
    *,
    color: str | None = None,
    value_command: str | None = None,
    unit: str | None = None,
    treshold: int | None = None,
) -> Segment:
    return Segment(
        title=title,
        macro_cw=cw,
        macro_ccw=ccw,
        macro_center=center or [],
        color=color,
        value_command=value_command,
        unit=unit,
        treshold=treshold,
    )


# --- system ---------------------------------------------------------------

def volume() -> Segment:
    return _segment(
        "Volume",
        [MacroStep(type=StepType.VOLUME, direction="up", value=1, title="Louder")],
        [MacroStep(type=StepType.VOLUME, direction="down", value=1, title="Quieter")],
        [MacroStep(type=StepType.MEDIA, keys=["KEY_MUTE"], title="Mute")],
        color=COLORS["audio"], value_command=VOLUME_QUERY, unit="%", treshold=30,
    )


def brightness() -> Segment:
    return _segment(
        "Brightness",
        [MacroStep(type=StepType.BRIGHTNESS, direction="up", value=1, title="Brighter")],
        [MacroStep(type=StepType.BRIGHTNESS, direction="down", value=1, title="Dimmer")],
        color=COLORS["light"], value_command=BRIGHTNESS_QUERY, unit="%", treshold=30,
    )


def keyboard_light() -> Segment:
    return _segment(
        "Keyboard light",
        [keys("KEY_KBDILLUMUP", title="Brighter")],
        [keys("KEY_KBDILLUMDOWN", title="Dimmer")],
        color=COLORS["light"], value_command=KBD_LIGHT_QUERY, unit="%", treshold=60,
    )


def mute() -> Segment:
    return _segment(
        "Mute", [], [], [MacroStep(type=StepType.MEDIA, keys=["KEY_MUTE"], title="Mute audio")],
        color=COLORS["audio"],
    )


def workspaces() -> Segment:
    return _segment(
        "Workspaces",
        [keys("KEY_LEFTMETA", "KEY_PAGEDOWN", title="Next workspace")],
        [keys("KEY_LEFTMETA", "KEY_PAGEUP", title="Previous workspace")],
        [keys("KEY_LEFTMETA", title="Overview")],
        color=COLORS["tabs"], treshold=60,
    )


# --- navigation -----------------------------------------------------------

def vertical_scroll() -> Segment:
    return _segment(
        "Scroll",
        [scroll("clockwise", title="Scroll up")],
        [scroll("counterclockwise", title="Scroll down")],
        color=COLORS["navigate"],
    )


def horizontal_scroll() -> Segment:
    return _segment(
        "Horizontal scroll",
        [scroll("clockwise", axis="horizontal", title="Scroll right")],
        [scroll("counterclockwise", axis="horizontal", title="Scroll left")],
        color=COLORS["navigate"],
    )


def zoom_wheel(title: str = "Zoom") -> Segment:
    """Ctrl + wheel zooms in nearly every GTK, Qt and browser application."""
    return _segment(
        title,
        [scroll("clockwise", modifiers=["KEY_LEFTCTRL"], title="Zoom in")],
        [scroll("counterclockwise", modifiers=["KEY_LEFTCTRL"], title="Zoom out")],
        [keys("KEY_LEFTCTRL", "KEY_0", title="Reset zoom")],
        color=COLORS["zoom"], treshold=30,
    )


def zoom_keys(title: str = "Zoom") -> Segment:
    return _segment(
        title,
        [keys("KEY_LEFTCTRL", "KEY_EQUAL", title="Zoom in")],
        [keys("KEY_LEFTCTRL", "KEY_MINUS", title="Zoom out")],
        [keys("KEY_LEFTCTRL", "KEY_0", title="Reset zoom")],
        color=COLORS["zoom"], treshold=45,
    )


def tabs() -> Segment:
    return _segment(
        "Tabs",
        [keys("KEY_LEFTCTRL", "KEY_PAGEDOWN", title="Next tab")],
        [keys("KEY_LEFTCTRL", "KEY_PAGEUP", title="Previous tab")],
        color=COLORS["tabs"], treshold=60,
    )


def browser_history() -> Segment:
    return _segment(
        "Back / Forward",
        [keys("KEY_LEFTALT", "KEY_RIGHT", title="Forward")],
        [keys("KEY_LEFTALT", "KEY_LEFT", title="Back")],
        [keys("KEY_F5", title="Reload")],
        color=COLORS["history"], treshold=90,
    )


def arrows() -> Segment:
    return _segment(
        "Arrow keys",
        [keys("KEY_RIGHT", title="Right")],
        [keys("KEY_LEFT", title="Left")],
        [keys("KEY_ENTER", title="Enter")],
        color=COLORS["navigate"], treshold=30,
    )


# --- media ----------------------------------------------------------------

def tracks() -> Segment:
    return _segment(
        "Media",
        [MacroStep(type=StepType.MEDIA, keys=["KEY_NEXTSONG"], title="Next track")],
        [MacroStep(type=StepType.MEDIA, keys=["KEY_PREVIOUSSONG"], title="Previous track")],
        [MacroStep(type=StepType.MEDIA, keys=["KEY_PLAYPAUSE"], title="Play / Pause")],
        color=COLORS["media"], treshold=90,
    )


def seek(forward: list[str] | None = None, back: list[str] | None = None) -> Segment:
    return _segment(
        "Seek",
        [keys(*(forward or ["KEY_RIGHT"]), title="Forward")],
        [keys(*(back or ["KEY_LEFT"]), title="Back")],
        [keys("KEY_SPACE", title="Play / Pause")],
        color=COLORS["media"], treshold=30,
    )


# --- editing --------------------------------------------------------------

def undo_redo(redo: list[str] | None = None) -> Segment:
    return _segment(
        "Undo / Redo",
        [keys(*(redo or ["KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_Z"]), title="Redo")],
        [keys("KEY_LEFTCTRL", "KEY_Z", title="Undo")],
        color=COLORS["history"], treshold=45,
    )


def font_size() -> Segment:
    return _segment(
        "Font size",
        [keys("KEY_LEFTCTRL", "KEY_RIGHTBRACE", title="Larger")],
        [keys("KEY_LEFTCTRL", "KEY_LEFTBRACE", title="Smaller")],
        color=COLORS["zoom"], treshold=45,
    )


# --- creative -------------------------------------------------------------
#
# Shortcuts below are the applications' own defaults, checked against their
# sources or manuals (GIMP app/actions/*.c, Krita krita_default.shortcuts,
# Inkscape share/keys/inkscape.xml, darktable src/views/darkroom.c,
# Blender keymap_data/blender_default.py, the Kdenlive, Shotcut and
# Audacity shortcut references).

CTRL = "KEY_LEFTCTRL"
SHIFT = "KEY_LEFTSHIFT"
ALT = "KEY_LEFTALT"


def _turn(title, cw, ccw, tap=None, *, color, treshold=30):
    """A function whose rotation and tap send plain shortcuts."""
    return _segment(
        title,
        [keys(*cw[1:], title=cw[0])],
        [keys(*ccw[1:], title=ccw[0])],
        [keys(*tap[1:], title=tap[0])] if tap else None,
        color=color, treshold=treshold,
    )


def _tap(title, tap, *, color, icon=None):
    """An action-only function: tapping its menu sector runs it."""
    segment = _segment(title, [], [], [keys(*tap[1:], title=tap[0])], color=color)
    segment.icon_url = icon
    return segment


def _group(title, items, *, icon=None):
    """Functions in one sector that opens them as a list beside the dial."""
    return Segment(title=title, items=items, icon_url=icon)


def _look(segment, look):
    """The dial's look while this function is selected (Segment.overlay)."""
    segment.overlay = dict(look)
    return segment


# How the dial looks while a function is in use, so it never hides the work:
# - CANVAS: a small label instead of the dial for controls used while looking
#   at the picture (brush size, opacity).
# - VIEW: full on touch, then nearly transparent while turning (zoom,
#   rotation, orbit), so the view stays visible.
# - TIMELINE: at the top of the screen, away from the timeline, and fading
#   while scrubbing.
CANVAS = {"visibility": "pill"}
VIEW = {"visibility": "fade", "fade_opacity": 20}
TIMELINE = {"position": "top", "visibility": "fade", "fade_opacity": 30}


def brush_size() -> Segment:
    return _turn("Brush size", ("Larger brush", "KEY_RIGHTBRACE"), ("Smaller brush", "KEY_LEFTBRACE"),
                 color=COLORS["brush"], treshold=20)


def frame_step() -> Segment:
    return _turn("Frame", ("Next frame", "KEY_RIGHT"), ("Previous frame", "KEY_LEFT"),
                 ("Play / Pause", "KEY_SPACE"), color=COLORS["time"], treshold=15)


def shuttle() -> Segment:
    """J / K / L shuttle shared by Kdenlive, DaVinci Resolve and Shotcut."""
    return _turn("Shuttle", ("Play forward", "KEY_L"), ("Play reverse", "KEY_J"),
                 ("Stop", "KEY_K"), color=COLORS["time"], treshold=90)


def resolve_timeline_zoom() -> Segment:
    return _turn("Timeline zoom", ("Zoom in", CTRL, "KEY_EQUAL"), ("Zoom out", CTRL, "KEY_MINUS"),
                 ("Fit timeline", SHIFT, "KEY_Z"), color=COLORS["zoom"], treshold=45)


# GIMP 3
def gimp_profile_segments() -> list[Segment]:
    return [
        _turn("Brush size", ("Larger brush +10", SHIFT, "KEY_RIGHTBRACE"), ("Smaller brush -10", SHIFT, "KEY_LEFTBRACE"),
              color=COLORS["brush"], treshold=20),
        _turn("Opacity", ("More opaque", SHIFT, "KEY_DOT"), ("More transparent", SHIFT, "KEY_COMMA"),
              color=COLORS["light"], treshold=15),
        _turn("Zoom", ("Zoom in", "KEY_KPPLUS"), ("Zoom out", "KEY_KPMINUS"),
              ("Fit image", CTRL, SHIFT, "KEY_J"), color=COLORS["zoom"]),
        _turn("Layers", ("Layer below", "KEY_PAGEDOWN"), ("Layer above", "KEY_PAGEUP"),
              ("Top layer", "KEY_HOME"), color=COLORS["layers"], treshold=60),
        undo_redo([CTRL, "KEY_Y"]),
    ]


# Krita 5
def krita_profile_segments() -> list[Segment]:
    return [
        brush_size(),
        _turn("Opacity", ("More opaque", "KEY_O"), ("More transparent", "KEY_I"),
              color=COLORS["light"], treshold=20),
        _turn("Zoom", ("Zoom in", CTRL, "KEY_EQUAL"), ("Zoom out", CTRL, "KEY_MINUS"),
              ("Zoom 100 %", CTRL, "KEY_0"), color=COLORS["zoom"]),
        _turn("Rotate canvas", ("Rotate right", CTRL, "KEY_RIGHTBRACE"), ("Rotate left", CTRL, "KEY_LEFTBRACE"),
              ("Mirror view", "KEY_M"), color=COLORS["navigate"]),
        _turn("Layers", ("Next layer", "KEY_PAGEUP"), ("Previous layer", "KEY_PAGEDOWN"),
              color=COLORS["layers"], treshold=60),
        undo_redo(),
    ]


# Inkscape 1.x
def inkscape_profile_segments() -> list[Segment]:
    return [
        _turn("Zoom", ("Zoom in", "KEY_EQUAL"), ("Zoom out", "KEY_MINUS"),
              ("Fit page", "KEY_5"), color=COLORS["zoom"]),
        _turn("Rotate", ("Rotate clockwise", "KEY_RIGHTBRACE"), ("Rotate counter-clockwise", "KEY_LEFTBRACE"),
              color=COLORS["navigate"]),
        _turn("Scale", ("Grow selection", "KEY_DOT"), ("Shrink selection", "KEY_COMMA"),
              color=COLORS["brush"]),
        _turn("Arrange", ("Raise", "KEY_PAGEUP"), ("Lower", "KEY_PAGEDOWN"),
              color=COLORS["layers"], treshold=60),
        undo_redo(),
    ]


# darktable 5
def darktable_profile_segments() -> list[Segment]:
    return [
        _turn("Zoom", ("Zoom in", CTRL, SHIFT, "KEY_EQUAL"), ("Zoom out", CTRL, "KEY_MINUS"),
              ("Fit image", ALT, "KEY_3"), color=COLORS["zoom"]),
        _turn("Photo", ("Next image", "KEY_SPACE"), ("Previous image", "KEY_BACKSPACE"),
              ("Close-up", ALT, "KEY_1"), color=COLORS["navigate"], treshold=60),
        _turn("Rating", ("Five stars", "KEY_5"), ("Clear rating", "KEY_0"),
              ("Reject", "KEY_R"), color=COLORS["light"], treshold=90),
        _turn("Colour label", ("Green label", "KEY_F3"), ("Red label", "KEY_F1"),
              ("Yellow label", "KEY_F2"), color=COLORS["media"], treshold=90),
        undo_redo([CTRL, "KEY_Y"]),
    ]


# Blender 4/5
def blender_profile_segments() -> list[Segment]:
    return [
        frame_step(),
        _turn("Keyframe", ("Next keyframe", "KEY_DOWN"), ("Previous keyframe", "KEY_UP"),
              ("Insert keyframe", "KEY_I"), color=COLORS["light"], treshold=45),
        _turn("Zoom view", ("Zoom in", CTRL, "KEY_EQUAL"), ("Zoom out", CTRL, "KEY_MINUS"),
              ("View all", "KEY_HOME"), color=COLORS["zoom"]),
        undo_redo(),
    ]


def _sequence(title: str, *presses: str) -> list[MacroStep]:
    """A key sequence such as FreeCAD's "V, F": one keystroke per press."""
    return [keys(press, title=title if index == 0 else None) for index, press in enumerate(presses)]


def _tap_sequence(title, presses, *, color, icon=None):
    """An action-only function whose tap presses several keys one after the other."""
    segment = _segment(title, [], [], _sequence(title, *presses), color=color)
    segment.icon_url = icon
    return segment


# FreeCAD 1.x (src/Gui/CommandView.cpp, src/Mod/Sketcher/Gui/Command*.cpp
# accelerators): Ctrl++ / Ctrl+- zoom (QKeySequence::ZoomIn / ZoomOut, sent as
# the keypad keys), Shift+Left / Shift+Right turn the view by 90 degrees, 0 is
# the isometric view, V, F fits all, D dimensions in a sketch, H and C
# constrain horizontal and coincident.
def freecad_profile_segments() -> list[Segment]:
    zoom = _turn("Zoom", ("Zoom in", CTRL, "KEY_KPPLUS"), ("Zoom out", CTRL, "KEY_KPMINUS"), color=COLORS["zoom"])
    zoom.macro_center = _sequence("Fit all", "KEY_V", "KEY_F")
    return [
        zoom,
        _turn("Rotate view", ("Rotate right", SHIFT, "KEY_RIGHT"), ("Rotate left", SHIFT, "KEY_LEFT"),
              ("Isometric view", "KEY_0"), color=COLORS["navigate"], treshold=90),
        _turn("Constraint", ("Horizontal", "KEY_H"), ("Coincident", "KEY_C"),
              ("Dimension", "KEY_D"), color=COLORS["brush"], treshold=90),
        undo_redo([CTRL, "KEY_Y"]),
    ]


# SolidCraft 0.18 (github.com/bherbruck/solidcraft, checked at 41a62d2). The
# bridge (integrations/solidcraft) runs every step by id, in the window that
# has the focus; without it the step's keys are pressed: the arrows turn the
# view 15 degrees and Ctrl+arrows pan (crates/ui-egui/src/navigation.rs
# view_key), F6 fits, S opens command search, Ctrl+Z / Ctrl+Y undo and redo
# (toolbar.rs), Ctrl+8 looks at the selection, the wheel zooms at the pointer.
# Command keys are the CommandSpec defaults (crates/engine/src/cmd): E
# extrude, F fillet, H hole, Q press pull, M move, I measure; in a sketch L
# line, R rectangle, C circle, D dimension, T trim, O offset, P project, X
# construction (a sketch key outside a sketch opens Create Sketch first).
# Roll, the standard views and Home have no keys: they need the bridge.
def _app_turn(title, cw, ccw, tap=None, *, color, treshold=30):
    """Like _turn, with (title, command id, *fallback keys) per action."""
    return _segment(
        title,
        [app_step(cw[1], *cw[2:], title=cw[0])],
        [app_step(ccw[1], *ccw[2:], title=ccw[0])],
        [app_step(tap[1], *tap[2:], title=tap[0])] if tap else None,
        color=color, treshold=treshold,
    )


def _app_tap(title, tap, *, color, icon=None):
    """An action-only function that runs a command of the application."""
    segment = _segment(title, [], [], [app_step(tap[1], *tap[2:], title=tap[0])], color=color)
    segment.icon_url = icon
    return segment


def _solidcraft_orbit() -> Segment:
    return _app_turn("Orbit", ("Orbit right", "turnpad.orbit_right", "KEY_RIGHT"),
                     ("Orbit left", "turnpad.orbit_left", "KEY_LEFT"), ("Fit", "turnpad.view_fit", "KEY_F6"),
                     color=COLORS["navigate"], treshold=20)


def _solidcraft_tilt() -> Segment:
    return _app_turn("Orbit up / down", ("Orbit down", "turnpad.orbit_down", "KEY_DOWN"),
                     ("Orbit up", "turnpad.orbit_up", "KEY_UP"), ("Home view", "turnpad.view_home"),
                     color=COLORS["navigate"], treshold=20)


def _solidcraft_zoom() -> Segment:
    # The wheel: SolidCraft zooms at the pointer, with or without the bridge.
    return _segment("Zoom", [scroll("clockwise", title="Zoom in")], [scroll("counterclockwise", title="Zoom out")],
                    [app_step("turnpad.view_fit", "KEY_F6", title="Fit")], color=COLORS["zoom"], treshold=30)


def _solidcraft_pan() -> Segment:
    pan = _app_turn("Pan", ("Pan right", "turnpad.pan_right", CTRL, "KEY_RIGHT"),
                    ("Pan left", "turnpad.pan_left", CTRL, "KEY_LEFT"), color=COLORS["navigate"], treshold=20)
    pan.macro_center = [keys(CTRL, "KEY_8", title="Look at the selection")]
    return pan


def _solidcraft_undo() -> Segment:
    return _app_turn("Undo / Redo", ("Redo", "edit.redo", CTRL, "KEY_Y"), ("Undo", "edit.undo", CTRL, "KEY_Z"),
                     color=COLORS["history"], treshold=45)


def _solidcraft_search() -> Segment:
    return _app_tap("Command search", ("Command search", "turnpad.search", "KEY_S"),
                    color=COLORS["zoom"], icon="edit-find-symbolic")


def solidcraft_profile_segments() -> list[Segment]:
    return [_solidcraft_orbit(), _solidcraft_tilt(), _solidcraft_zoom(), _solidcraft_pan(),
            _solidcraft_undo(), _solidcraft_search()]


# KiCad 9 (common/tool/actions.cpp, pcbnew/tools/pcb_actions.cpp, Linux
# defaults): F1 / F2 zoom in / out, Home fits, N / Shift+N change the grid,
# R / Shift+R rotate, F flips (PCB editor), + / - switch the layer, Ctrl+Y redoes.
def kicad_profile_segments() -> list[Segment]:
    return [
        _turn("Zoom", ("Zoom in", "KEY_F1"), ("Zoom out", "KEY_F2"),
              ("Fit to screen", "KEY_HOME"), color=COLORS["zoom"]),
        _turn("Grid", ("Next grid", "KEY_N"), ("Previous grid", SHIFT, "KEY_N"),
              color=COLORS["navigate"], treshold=45),
        _turn("Rotate", ("Rotate clockwise", SHIFT, "KEY_R"), ("Rotate counter-clockwise", "KEY_R"),
              ("Flip to the other side", "KEY_F"), color=COLORS["brush"], treshold=45),
        _turn("Layer", ("Next layer", SHIFT, "KEY_EQUAL"), ("Previous layer", "KEY_MINUS"),
              color=COLORS["layers"], treshold=60),
        undo_redo([CTRL, "KEY_Y"]),
    ]


# Kdenlive 24/25
def kdenlive_profile_segments() -> list[Segment]:
    return [
        frame_step(),
        shuttle(),
        _turn("Edit points", ("Next snap point", ALT, "KEY_RIGHT"), ("Previous snap point", ALT, "KEY_LEFT"),
              ("Cut clip", SHIFT, "KEY_R"), color=COLORS["cut"], treshold=45),
        _turn("Timeline zoom", ("Zoom in", CTRL, "KEY_EQUAL"), ("Zoom out", CTRL, "KEY_MINUS"),
              color=COLORS["zoom"]),
        undo_redo(),
    ]


# DaVinci Resolve 19/20
def resolve_profile_segments() -> list[Segment]:
    return [
        frame_step(),
        shuttle(),
        _turn("Edit points", ("Next edit", "KEY_DOWN"), ("Previous edit", "KEY_UP"),
              ("Split clip", CTRL, "KEY_BACKSLASH"), color=COLORS["cut"], treshold=45),
        resolve_timeline_zoom(),
        undo_redo(),
    ]


# Shotcut
def shotcut_profile_segments() -> list[Segment]:
    return [
        frame_step(),
        shuttle(),
        _turn("Edit points", ("Next edit", ALT, "KEY_RIGHT"), ("Previous edit", ALT, "KEY_LEFT"),
              ("Split at playhead", "KEY_S"), color=COLORS["cut"], treshold=45),
        _turn("Timeline zoom", ("Zoom in", "KEY_EQUAL"), ("Zoom out", "KEY_MINUS"),
              ("Fit timeline", "KEY_0"), color=COLORS["zoom"]),
        undo_redo([CTRL, "KEY_Y"]),
    ]


# Audacity 3
def audacity_profile_segments() -> list[Segment]:
    return [
        _turn("Cursor", ("Jump right", "KEY_DOT"), ("Jump left", "KEY_COMMA"),
              ("Play / Stop", "KEY_SPACE"), color=COLORS["time"], treshold=20),
        _turn("Zoom", ("Zoom in", CTRL, "KEY_1"), ("Zoom out", CTRL, "KEY_3"),
              ("Fit to width", CTRL, "KEY_F"), color=COLORS["zoom"]),
        _turn("Clips", ("Next clip", ALT, "KEY_DOT"), ("Previous clip", ALT, "KEY_COMMA"),
              ("Split", CTRL, "KEY_I"), color=COLORS["cut"], treshold=60),
        undo_redo([CTRL, "KEY_Y"]),
        volume(),
    ]


# OBS Studio ships without hotkeys. The profile sends unused combinations;
# assign each once in OBS: Settings -> Hotkeys (the same key may be used for
# a start and a stop action).
OBS_HOTKEYS = {
    "record": (CTRL, SHIFT, "KEY_F9"),
    "stream": (CTRL, SHIFT, "KEY_F10"),
    "pause": (CTRL, SHIFT, "KEY_F11"),
    "mic": (CTRL, SHIFT, "KEY_F12"),
    "replay": (CTRL, SHIFT, "KEY_F8"),
}


def obs_profile_segments() -> list[Segment]:
    return [
        _tap("Record", ("Start / stop recording", *OBS_HOTKEYS["record"]), color=COLORS["record"]),
        _tap("Stream", ("Start / stop streaming", *OBS_HOTKEYS["stream"]), color=COLORS["history"]),
        _tap("Pause", ("Pause / resume recording", *OBS_HOTKEYS["pause"]), color=COLORS["light"]),
        _tap("Mic", ("Mute / unmute microphone", *OBS_HOTKEYS["mic"]), color=COLORS["audio"]),
        _tap("Replay", ("Save replay", *OBS_HOTKEYS["replay"]), color=COLORS["navigate"]),
        volume(),
    ]


# --- launch ---------------------------------------------------------------
#
# A function with nothing to turn is a launcher: choosing it in the radial
# menu runs its tap action at once and the dial stays in the menu, like the
# radial menus of 3D mice. Commands run through launch.py, so a program
# keeps running without blocking the dial. The shell picks the first tool
# that exists, so one preset fits several desktops.

def _launcher(title: str, icon: str, step: MacroStep) -> Segment:
    segment = _segment(title, [], [], [step])
    segment.icon_url = icon
    return segment


def _shell(script: str, title: str) -> MacroStep:
    return MacroStep(type=StepType.SHELL, script=script, title=title)


def calculator() -> Segment:
    return _launcher("Calculator", "accessories-calculator-symbolic", _shell(
        "gnome-calculator || kcalc || qalculate-gtk || galculator", "Open the calculator"))


def terminal() -> Segment:
    return _launcher("Terminal", "utilities-terminal-symbolic", _shell(
        "xdg-terminal-exec || kgx || gnome-terminal || konsole || x-terminal-emulator", "Open a terminal"))


def files() -> Segment:
    return _launcher("Files", "folder-symbolic", _shell('xdg-open "$HOME"', "Open the home folder"))


def screenshot() -> Segment:
    return _launcher("Screenshot", "camera-photo-symbolic", keys("KEY_SYSRQ", title="Take a screenshot"))


def lock_screen() -> Segment:
    return _launcher("Lock screen", "system-lock-screen-symbolic", _shell("loginctl lock-session", "Lock the screen"))


def run_script() -> Segment:
    return _launcher("My script", "application-x-executable-symbolic", _shell(
        "$HOME/.local/bin/turnpad-action", "Run ~/.local/bin/turnpad-action (change it to your script)"))


def apps_group() -> Segment:
    """Launchers in one group: more programs without smaller sectors."""
    return Segment(title="Apps", items=[calculator(), terminal(), files(), screenshot(), lock_screen()])


# --- more everyday functions ------------------------------------------------
#
# Taps work everywhere: in the radial menu and in a group's list choosing one
# runs it at once and the dial stays where it was.

def play_pause() -> Segment:
    return _segment(
        "Play / Pause", [], [], [MacroStep(type=StepType.MEDIA, keys=["KEY_PLAYPAUSE"], title="Play / Pause")],
        color=COLORS["media"],
    )


def mic_mute() -> Segment:
    return _segment(
        "Microphone", [], [], [MacroStep(type=StepType.MEDIA, keys=["KEY_MICMUTE"], title="Mute microphone")],
        color=COLORS["audio"],
    )


def overview() -> Segment:
    return _tap("Overview", ("Activities overview", "KEY_LEFTMETA"), color=COLORS["tabs"], icon="view-app-grid-symbolic")


def pages() -> Segment:
    segment = _segment(
        "Pages",
        [keys("KEY_PAGEDOWN", title="Page down")],
        [keys("KEY_PAGEUP", title="Page up")],
        [keys("KEY_LEFTCTRL", "KEY_HOME", title="Go to the start")],
        color=COLORS["navigate"], treshold=45,
    )
    segment.icon_url = "view-paged-symbolic"
    return segment


def up_down() -> Segment:
    segment = _segment(
        "Up / Down",
        [keys("KEY_DOWN", title="Down")],
        [keys("KEY_UP", title="Up")],
        [keys("KEY_ENTER", title="Enter")],
        color=COLORS["navigate"], treshold=30,
    )
    segment.icon_url = "go-down-symbolic"
    return segment


def copy() -> Segment:
    return _tap("Copy", ("Copy", CTRL, "KEY_C"), color=COLORS["history"], icon="edit-copy-symbolic")


def cut() -> Segment:
    return _tap("Cut", ("Cut", CTRL, "KEY_X"), color=COLORS["cut"], icon="edit-cut-symbolic")


def paste() -> Segment:
    return _tap("Paste", ("Paste", CTRL, "KEY_V"), color=COLORS["history"], icon="edit-paste-symbolic")


def select_all() -> Segment:
    return _tap("Select all", ("Select all", CTRL, "KEY_A"), color=COLORS["navigate"], icon="edit-select-all-symbolic")


def find() -> Segment:
    return _tap("Find", ("Find", CTRL, "KEY_F"), color=COLORS["zoom"], icon="edit-find-symbolic")


def save() -> Segment:
    return _tap("Save", ("Save", CTRL, "KEY_S"), color=COLORS["record"], icon="document-save-symbolic")


def fullscreen() -> Segment:
    return _tap("Full screen", ("Full screen", "KEY_F11"), color=COLORS["tabs"], icon="view-fullscreen-symbolic")


# Ready groups: a whole set in one sector, e.g. a "Clipboard" next to the
# functions of an application.

def clipboard_group() -> Segment:
    return _group("Clipboard", [copy(), cut(), paste(), select_all(), undo_redo()], icon="edit-paste-symbolic")


def media_group() -> Segment:
    return _group("Media controls", [play_pause(), tracks(), volume(), mute(), mic_mute()],
                  icon="media-playback-start-symbolic")


def system_group() -> Segment:
    return _group("System", [volume(), brightness(), keyboard_light(), mic_mute(), workspaces(), overview()],
                  icon="preferences-system-symbolic")


def navigation_group() -> Segment:
    return _group("Navigation", [vertical_scroll(), horizontal_scroll(), pages(), up_down(), tabs(), browser_history()],
                  icon="view-continuous-symbolic")


def documents_group() -> Segment:
    return _group("Documents", [undo_redo(), find(), save(), select_all(), font_size(), zoom_keys()],
                  icon="document-edit-symbolic")


@dataclass(frozen=True)
class FunctionPreset:
    title: str
    category: str
    icon_name: str
    description: str
    factory: Callable[[], Segment]


FUNCTION_LIBRARY: tuple[FunctionPreset, ...] = (
    FunctionPreset("Volume", "System", "audio-volume-high-symbolic", "Turn to change volume, tap to mute", volume),
    FunctionPreset("Brightness", "System", "display-brightness-symbolic", "Screen brightness with live value", brightness),
    FunctionPreset("Keyboard light", "System", "keyboard-brightness-symbolic", "Keyboard backlight level", keyboard_light),
    FunctionPreset("Mute", "System", "audio-volume-muted-symbolic", "Tap the sector to toggle mute", mute),
    FunctionPreset("Microphone", "System", "audio-input-microphone-symbolic", "Tap to mute or unmute the microphone", mic_mute),
    FunctionPreset("Workspaces", "System", "view-grid-symbolic", "Switch workspaces, tap for overview", workspaces),
    FunctionPreset("Overview", "System", "view-app-grid-symbolic", "Tap for the activities overview (Super)", overview),
    FunctionPreset("Scroll", "Navigation", "view-continuous-symbolic", "Smooth vertical scrolling", vertical_scroll),
    FunctionPreset("Horizontal scroll", "Navigation", "object-flip-horizontal-symbolic", "Timelines, wide tables and canvases", horizontal_scroll),
    FunctionPreset("Zoom", "Navigation", "zoom-in-symbolic", "Ctrl + wheel zoom, tap to reset", zoom_wheel),
    FunctionPreset("Tabs", "Navigation", "tab-new-symbolic", "Next / previous tab", tabs),
    FunctionPreset("Back / Forward", "Navigation", "go-previous-symbolic", "Browser history, tap to reload", browser_history),
    FunctionPreset("Arrow keys", "Navigation", "go-next-symbolic", "Left / right arrows, tap for Enter", arrows),
    FunctionPreset("Up / Down", "Navigation", "go-down-symbolic", "Up / down arrows for lists and menus, tap for Enter", up_down),
    FunctionPreset("Pages", "Navigation", "view-paged-symbolic", "Page down / up, tap to go to the start", pages),
    FunctionPreset("Full screen", "Navigation", "view-fullscreen-symbolic", "Tap for full screen (F11)", fullscreen),
    FunctionPreset("Media", "Media", "media-playback-start-symbolic", "Next / previous track, tap to play", tracks),
    FunctionPreset("Seek", "Media", "media-seek-forward-symbolic", "Arrow-key seeking in players", seek),
    FunctionPreset("Play / Pause", "Media", "media-playback-start-symbolic", "Tap to play or pause any player", play_pause),
    FunctionPreset("Undo / Redo", "Editing", "edit-undo-symbolic", "Step through history", undo_redo),
    FunctionPreset("Font size", "Editing", "font-x-generic-symbolic", "LibreOffice-style Ctrl+[ / Ctrl+]", font_size),
    FunctionPreset("Copy", "Editing", "edit-copy-symbolic", "Tap to copy (Ctrl+C)", copy),
    FunctionPreset("Cut", "Editing", "edit-cut-symbolic", "Tap to cut (Ctrl+X)", cut),
    FunctionPreset("Paste", "Editing", "edit-paste-symbolic", "Tap to paste (Ctrl+V)", paste),
    FunctionPreset("Select all", "Editing", "edit-select-all-symbolic", "Tap to select all (Ctrl+A)", select_all),
    FunctionPreset("Find", "Editing", "edit-find-symbolic", "Tap to search (Ctrl+F)", find),
    FunctionPreset("Save", "Editing", "document-save-symbolic", "Tap to save (Ctrl+S)", save),
    FunctionPreset("Brush size", "Creative", "document-edit-symbolic", "[ and ] in GIMP, Krita, Photoshop", brush_size),
    FunctionPreset("Frame", "Creative", "media-skip-forward-symbolic", "Frame stepping in video editors", frame_step),
    FunctionPreset("Timeline zoom", "Creative", "zoom-fit-best-symbolic", "Ctrl+= / Ctrl+- timeline zoom", resolve_timeline_zoom),
    FunctionPreset("Shuttle", "Creative", "media-seek-forward-symbolic", "J / K / L in video editors", shuttle),
    FunctionPreset("Calculator", "Launch", "accessories-calculator-symbolic", "Choose it in the menu to open a calculator", calculator),
    FunctionPreset("Terminal", "Launch", "utilities-terminal-symbolic", "Choose it in the menu to open a terminal", terminal),
    FunctionPreset("Files", "Launch", "folder-symbolic", "Open the home folder", files),
    FunctionPreset("Screenshot", "Launch", "camera-photo-symbolic", "The Print key: the desktop's screenshot tool", screenshot),
    FunctionPreset("Lock screen", "Launch", "system-lock-screen-symbolic", "Lock the session", lock_screen),
    FunctionPreset("My script", "Launch", "application-x-executable-symbolic", "Runs a command or bash script of yours; edit its tap action", run_script),
    FunctionPreset("Apps", "Groups", GROUP_ICON, "Calculator, terminal, files, screenshot and lock screen in one list", apps_group),
    FunctionPreset("Clipboard", "Groups", "edit-paste-symbolic", "Copy, cut, paste, select all and undo in one list", clipboard_group),
    FunctionPreset("Media controls", "Groups", "media-playback-start-symbolic", "Play, tracks, volume, mute and microphone", media_group),
    FunctionPreset("System", "Groups", "preferences-system-symbolic", "Volume, brightness, keyboard light, microphone, workspaces", system_group),
    FunctionPreset("Navigation", "Groups", "view-continuous-symbolic", "Scrolling, pages, arrows, tabs and history", navigation_group),
    FunctionPreset("Documents", "Groups", "document-edit-symbolic", "Undo, find, save, select all, font size and zoom", documents_group),
)

LIBRARY_CATEGORIES = ("System", "Navigation", "Media", "Editing", "Creative", "Launch", "Groups")


def default_segments() -> list[Segment]:
    """ASUS Dial-like system defaults used for the fallback profile."""
    return [volume(), brightness(), vertical_scroll(), zoom_wheel(), undo_redo(), tracks()]


# --- advanced packs ---------------------------------------------------------
#
# Basic packs keep a short menu with the most used functions. Advanced packs
# put more into groups, ordered by the stages of the work rather than by
# technical kind: what turns (sizes, zoom, time) is on the dial, the tap does
# what usually follows the turn (fit the view, play, insert a keyframe, cut
# at the playhead). Precise controls need more turning (higher treshold),
# fast ones less. The dial's look keeps the work visible (CANVAS, VIEW,
# TIMELINE above). Keys go to the window and editor under the pointer, as
# with the keyboard.


# Blender 4/5 (keymap_data/blender_default.py): Space plays (default
# "Spacebar Action: Play"), Up / Down jump to the previous / next keyframe,
# I inserts a keyframe and Alt+I deletes it in the 3D view, numpad orbits
# (Ctrl+numpad pans), [ / ] scale the brush in sculpt and paint modes.
def blender_advanced_segments() -> list[Segment]:
    view = _group("View", [
        _look(_turn("Zoom view", ("Zoom in", CTRL, "KEY_EQUAL"), ("Zoom out", CTRL, "KEY_MINUS"),
                    ("Frame selected", "KEY_KPDOT"), color=COLORS["zoom"]), VIEW),
        _look(_turn("Orbit", ("Orbit right", "KEY_KP6"), ("Orbit left", "KEY_KP4"),
                    ("View all", "KEY_HOME"), color=COLORS["navigate"], treshold=20), VIEW),
        _look(_turn("Orbit up / down", ("Orbit down", "KEY_KP2"), ("Orbit up", "KEY_KP8"),
                    ("Perspective / Orthographic", "KEY_KP5"), color=COLORS["navigate"], treshold=20), VIEW),
        _look(_turn("Pan", ("Pan right", CTRL, "KEY_KP6"), ("Pan left", CTRL, "KEY_KP4"),
                    ("Camera view", "KEY_KP0"), color=COLORS["navigate"], treshold=20), VIEW),
        _tap("Local view", ("Isolate the selection", "KEY_KPSLASH"), color=COLORS["layers"], icon="view-reveal-symbolic"),
        _tap("X-ray", ("Toggle X-ray", ALT, "KEY_Z"), color=COLORS["light"], icon="view-reveal-symbolic"),
    ], icon="camera-video-symbolic")
    animation = _group("Animation", [
        _look(_turn("Keyframe", ("Next keyframe", "KEY_DOWN"), ("Previous keyframe", "KEY_UP"),
                    ("Insert keyframe", "KEY_I"), color=COLORS["light"], treshold=45), TIMELINE),
        _look(_turn("Time jump", ("Jump forward", CTRL, "KEY_RIGHT"), ("Jump back", CTRL, "KEY_LEFT"),
                    color=COLORS["time"], treshold=45), TIMELINE),
        _look(_turn("Start / End", ("Last frame", SHIFT, "KEY_RIGHT"), ("First frame", SHIFT, "KEY_LEFT"),
                    ("Play backwards", CTRL, SHIFT, "KEY_SPACE"), color=COLORS["time"], treshold=90), TIMELINE),
        _tap("Delete keyframe", ("Delete keyframe", ALT, "KEY_I"), color=COLORS["record"], icon="starred-symbolic"),
        _tap("Marker", ("Add marker (timeline)", "KEY_M"), color=COLORS["history"], icon="bookmark-new-symbolic"),
    ], icon="starred-symbolic")
    return [
        _look(frame_step(), TIMELINE),
        view,
        animation,
        _look(_turn("Brush size", ("Larger brush", "KEY_RIGHTBRACE"), ("Smaller brush", "KEY_LEFTBRACE"),
                    color=COLORS["brush"], treshold=20), CANVAS),
        undo_redo(),
    ]


# FreeCAD 1.x: V, O / V, P orthographic / perspective, V, S fits the
# selection, Home is the home view; sketcher: G, L line, G, M polyline, G, R
# rectangle, G, C circle, G, A arc, G, T trim (all need the sketch open).
def freecad_advanced_segments() -> list[Segment]:
    basic = freecad_profile_segments()
    views = _group("Views", [
        _tap("Isometric", ("Isometric view", "KEY_0"), color=COLORS["navigate"], icon="view-reveal-symbolic"),
        _tap("Home view", ("Home view", "KEY_HOME"), color=COLORS["navigate"], icon="go-next-symbolic"),
        _tap_sequence("Fit all", ("KEY_V", "KEY_F"), color=COLORS["zoom"], icon="view-reveal-symbolic"),
        _tap_sequence("Fit selection", ("KEY_V", "KEY_S"), color=COLORS["zoom"], icon="view-reveal-symbolic"),
        _tap_sequence("Orthographic", ("KEY_V", "KEY_O"), color=COLORS["light"], icon="camera-video-symbolic"),
        _tap_sequence("Perspective", ("KEY_V", "KEY_P"), color=COLORS["light"], icon="camera-video-symbolic"),
    ], icon="camera-video-symbolic")
    sketch = _group("Sketch", [
        _tap_sequence("Line", ("KEY_G", "KEY_L"), color=COLORS["brush"], icon="document-edit-symbolic"),
        _tap_sequence("Polyline", ("KEY_G", "KEY_M"), color=COLORS["brush"], icon="document-edit-symbolic"),
        _tap_sequence("Rectangle", ("KEY_G", "KEY_R"), color=COLORS["brush"], icon="document-edit-symbolic"),
        _tap_sequence("Circle", ("KEY_G", "KEY_C"), color=COLORS["brush"], icon="document-edit-symbolic"),
        _tap_sequence("Arc", ("KEY_G", "KEY_A"), color=COLORS["brush"], icon="document-edit-symbolic"),
        _tap_sequence("Trim", ("KEY_G", "KEY_T"), color=COLORS["cut"], icon="edit-cut-symbolic"),
    ], icon="document-edit-symbolic")
    constrain = _group("Constrain", [
        _tap("Dimension", ("Dimension", "KEY_D"), color=COLORS["light"], icon="starred-symbolic"),
        _tap("Horizontal", ("Horizontal constraint", "KEY_H"), color=COLORS["navigate"], icon="go-next-symbolic"),
        _tap("Vertical", ("Vertical constraint", "KEY_V"), color=COLORS["navigate"], icon="go-up-symbolic"),
        _tap("Coincident", ("Coincident constraint", "KEY_C"), color=COLORS["layers"], icon="view-list-symbolic"),
    ], icon="starred-symbolic")
    return [
        _look(basic[0], VIEW),  # Zoom
        _look(basic[1], VIEW),  # Rotate view
        views,
        sketch,
        constrain,
        basic[3],  # Undo / Redo
    ]


# SolidCraft, advanced: the view, the standard views, then the stages of a
# part: sketch, model, check. Taps of tools open SolidCraft's own tool or
# dialog, as its toolbar does.
def solidcraft_advanced_segments() -> list[Segment]:
    view = _group("View", [
        _look(_solidcraft_orbit(), VIEW),
        _look(_solidcraft_tilt(), VIEW),
        _look(_app_turn("Roll", ("Roll clockwise", "turnpad.roll_cw"), ("Roll counterclockwise", "turnpad.roll_ccw"),
                        ("Home view", "turnpad.view_home"), color=COLORS["navigate"], treshold=20), VIEW),
        _look(_solidcraft_pan(), VIEW),
        _look(_app_turn("Pan up / down", ("Pan down", "turnpad.pan_down", CTRL, "KEY_DOWN"),
                        ("Pan up", "turnpad.pan_up", CTRL, "KEY_UP"), color=COLORS["navigate"], treshold=20), VIEW),
    ], icon="camera-video-symbolic")
    views = _group("Views", [
        _app_tap("Fit", ("Fit", "turnpad.view_fit", "KEY_F6"), color=COLORS["zoom"], icon="view-reveal-symbolic"),
        _app_tap("Home view", ("Home view", "turnpad.view_home"), color=COLORS["navigate"], icon="go-next-symbolic"),
        _app_tap("Isometric", ("Isometric view", "turnpad.view_iso"), color=COLORS["navigate"], icon="view-reveal-symbolic"),
        _app_tap("Front", ("Front view", "turnpad.view_front"), color=COLORS["navigate"], icon="view-reveal-symbolic"),
        _app_tap("Top", ("Top view", "turnpad.view_top"), color=COLORS["navigate"], icon="go-up-symbolic"),
        _app_tap("Right", ("Right view", "turnpad.view_right"), color=COLORS["navigate"], icon="go-next-symbolic"),
    ], icon="view-reveal-symbolic")
    sketch = _group("Sketch", [
        _app_tap("Create sketch", ("Create sketch", "sketch.create"), color=COLORS["brush"], icon="document-edit-symbolic"),
        _app_tap("Line", ("Line", "sketch.line", "KEY_L"), color=COLORS["brush"], icon="document-edit-symbolic"),
        _app_tap("Rectangle", ("2-point rectangle", "sketch.rectangle.two_point", "KEY_R"),
                 color=COLORS["brush"], icon="document-edit-symbolic"),
        _app_tap("Circle", ("Center diameter circle", "sketch.circle.center", "KEY_C"),
                 color=COLORS["brush"], icon="document-edit-symbolic"),
        _app_tap("Dimension", ("Sketch dimension", "sketch.dimension", "KEY_D"), color=COLORS["light"], icon="starred-symbolic"),
        _app_tap("Trim", ("Trim", "sketch.trim", "KEY_T"), color=COLORS["cut"], icon="edit-cut-symbolic"),
        _app_tap("Finish sketch", ("Finish sketch", "sketch.finish"), color=COLORS["layers"], icon="go-next-symbolic"),
    ], icon="document-edit-symbolic")
    model = _group("Model", [
        _app_tap("Extrude", ("Extrude", "solid.extrude", "KEY_E"), color=COLORS["layers"], icon="go-up-symbolic"),
        _app_tap("Revolve", ("Revolve", "solid.revolve"), color=COLORS["layers"], icon="view-refresh-symbolic"),
        _app_tap("Fillet", ("Fillet", "solid.fillet", "KEY_F"), color=COLORS["brush"], icon="document-edit-symbolic"),
        _app_tap("Chamfer", ("Chamfer", "solid.chamfer"), color=COLORS["brush"], icon="document-edit-symbolic"),
        _app_tap("Hole", ("Hole", "solid.hole", "KEY_H"), color=COLORS["cut"], icon="edit-cut-symbolic"),
        _app_tap("Shell", ("Shell", "solid.shell"), color=COLORS["cut"], icon="edit-cut-symbolic"),
        _app_tap("Press pull", ("Press pull", "solid.press_pull", "KEY_Q"), color=COLORS["layers"], icon="go-up-symbolic"),
    ], icon="go-up-symbolic")
    check = _group("Inspect", [
        _app_tap("Measure", ("Measure", "inspect.measure", "KEY_I"), color=COLORS["light"], icon="starred-symbolic"),
        _app_tap("Section", ("Section analysis", "inspect.section"), color=COLORS["light"], icon="view-reveal-symbolic"),
        _app_tap("Parameters", ("Change parameters", "parameters.change"), color=COLORS["history"],
                 icon="preferences-system-symbolic"),
        _app_tap("Appearance", ("Appearance", "appearance.assign", "KEY_A"), color=COLORS["brush"], icon="starred-symbolic"),
    ], icon="starred-symbolic")
    return [
        view,
        _look(_solidcraft_zoom(), VIEW),
        views,
        sketch,
        model,
        check,
        _solidcraft_undo(),
        _solidcraft_search(),
    ]


# KiCad 9: W / Shift+W next / previous track width, ' and \ via size, X routes
# a single track, 6 a differential pair, B / Ctrl+B fill / unfill the zones,
# ` highlights the net, ~ clears it, U selects the connection, M moves,
# G drags, E opens the properties. Flip, routing and zones are PCB editor only.
def kicad_advanced_segments() -> list[Segment]:
    basic = kicad_profile_segments()
    edit = _group("Edit", [
        _tap("Move", ("Move", "KEY_M"), color=COLORS["navigate"], icon="go-next-symbolic"),
        _tap("Drag", ("Drag", "KEY_G"), color=COLORS["navigate"], icon="go-next-symbolic"),
        _tap("Properties", ("Properties", "KEY_E"), color=COLORS["light"], icon="document-edit-symbolic"),
        basic[2],  # Rotate (tap flips)
    ], icon="document-edit-symbolic")
    route = _group("Route", [
        _tap("Route track", ("Route a single track", "KEY_X"), color=COLORS["record"], icon="list-add-symbolic"),
        _tap("Route pair", ("Route a differential pair", "KEY_6"), color=COLORS["record"], icon="list-add-symbolic"),
        _turn("Track width", ("Next track width", "KEY_W"), ("Previous track width", SHIFT, "KEY_W"),
              color=COLORS["brush"], treshold=60),
        _turn("Via size", ("Larger via", "KEY_APOSTROPHE"), ("Smaller via", "KEY_BACKSLASH"),
              color=COLORS["brush"], treshold=60),
        _tap("Fill zones", ("Fill all zones", "KEY_B"), color=COLORS["navigate"], icon="color-select-symbolic"),
        _tap("Unfill zones", ("Unfill all zones", CTRL, "KEY_B"), color=COLORS["history"], icon="edit-clear-symbolic"),
    ], icon="list-add-symbolic")
    nets = _group("Nets", [
        _tap("Highlight net", ("Highlight the net", "KEY_GRAVE"), color=COLORS["light"], icon="starred-symbolic"),
        _tap("Clear highlight", ("Clear the highlight", SHIFT, "KEY_GRAVE"), color=COLORS["history"], icon="edit-clear-symbolic"),
        _tap("Select connection", ("Select the connection", "KEY_U"), color=COLORS["layers"], icon="view-list-symbolic"),
    ], icon="starred-symbolic")
    return [
        _look(basic[0], VIEW),  # Zoom
        _look(basic[1], CANVAS),  # Grid
        basic[3],  # Layer
        edit,
        route,
        nets,
        basic[4],  # Undo / Redo
    ]


# Kdenlive 25 (src/mainwindow_actions.cpp): Alt+Left/Right snap points,
# Ctrl+Left/Right markers, G adds or removes a timeline marker, Shift+R cuts
# the clip at the playhead, V / B insert / overwrite the zone, Z lifts,
# Shift+X extracts, Home / End go to the clip's start and end.
def kdenlive_advanced_segments() -> list[Segment]:
    playback = _group("Playback", [
        _look(shuttle(), TIMELINE),
        _look(_turn("Clip start / end", ("Clip end", "KEY_END"), ("Clip start", "KEY_HOME"),
                    color=COLORS["time"], treshold=90), TIMELINE),
        _tap("Play zone", ("Play the zone", CTRL, "KEY_SPACE"), color=COLORS["media"],
             icon="media-playback-start-symbolic"),
    ], icon="media-playback-start-symbolic")
    edit = _group("Edit", [
        _tap("Insert zone", ("Insert the zone", "KEY_V"), color=COLORS["navigate"], icon="insert-object-symbolic"),
        _tap("Overwrite zone", ("Overwrite with the zone", "KEY_B"), color=COLORS["record"], icon="insert-object-symbolic"),
        _tap("Lift", ("Lift the zone", "KEY_Z"), color=COLORS["history"], icon="go-up-symbolic"),
        _tap("Extract", ("Extract the zone", SHIFT, "KEY_X"), color=COLORS["cut"], icon="edit-cut-symbolic"),
        undo_redo(),
    ], icon="edit-cut-symbolic")
    return [
        _look(frame_step(), TIMELINE),
        _look(_turn("Edit points", ("Next snap point", ALT, "KEY_RIGHT"), ("Previous snap point", ALT, "KEY_LEFT"),
                    ("Cut clip", SHIFT, "KEY_R"), color=COLORS["cut"], treshold=45), TIMELINE),
        _look(_turn("Markers", ("Next marker", CTRL, "KEY_RIGHT"), ("Previous marker", CTRL, "KEY_LEFT"),
                    ("Add / remove marker", "KEY_G"), color=COLORS["history"], treshold=60), TIMELINE),
        _look(_turn("Timeline zoom", ("Zoom in", CTRL, "KEY_EQUAL"), ("Zoom out", CTRL, "KEY_MINUS"),
                    color=COLORS["zoom"]), TIMELINE),
        playback,
        edit,
        volume(),
    ]


# DaVinci Resolve 19/20, Edit page: Up / Down previous / next edit,
# Shift+Up / Shift+Down previous / next marker, M adds a marker, , and .
# nudge the clip a frame, Ctrl+\ splits, Shift+Z fits the timeline.
def resolve_advanced_segments() -> list[Segment]:
    playback = _group("Playback", [
        _look(shuttle(), TIMELINE),
        _tap("Mark in", ("Mark in", "KEY_I"), color=COLORS["navigate"], icon="go-next-symbolic"),
        _tap("Mark out", ("Mark out", "KEY_O"), color=COLORS["navigate"], icon="go-next-symbolic"),
    ], icon="media-playback-start-symbolic")
    return [
        _look(frame_step(), TIMELINE),
        _look(_turn("Edit points", ("Next edit", "KEY_DOWN"), ("Previous edit", "KEY_UP"),
                    ("Split clip", CTRL, "KEY_BACKSLASH"), color=COLORS["cut"], treshold=45), TIMELINE),
        _look(_turn("Markers", ("Next marker", SHIFT, "KEY_DOWN"), ("Previous marker", SHIFT, "KEY_UP"),
                    ("Add marker", "KEY_M"), color=COLORS["history"], treshold=60), TIMELINE),
        _look(_turn("Nudge clip", ("Nudge right", "KEY_DOT"), ("Nudge left", "KEY_COMMA"),
                    color=COLORS["brush"], treshold=45), TIMELINE),
        _look(resolve_timeline_zoom(), TIMELINE),
        playback,
        undo_redo(),
        volume(),
    ]


# Shotcut: I / O set in and out, J/K/L shuttle, S splits at the playhead.
def shotcut_advanced_segments() -> list[Segment]:
    marks = _group("In / Out", [
        _tap("Set in", ("Set in point", "KEY_I"), color=COLORS["navigate"], icon="go-next-symbolic"),
        _tap("Set out", ("Set out point", "KEY_O"), color=COLORS["navigate"], icon="go-next-symbolic"),
    ], icon="go-next-symbolic")
    frame, shuttle_, edit_points, zoom, undo = shotcut_profile_segments()
    return [_look(frame, TIMELINE), _look(shuttle_, TIMELINE), _look(edit_points, TIMELINE), _look(zoom, TIMELINE),
            marks, undo, volume()]


# Audacity 3: Home / End skip to start and end, R records, Ctrl+2 zooms to
# normal, Ctrl+T trims outside the selection, Ctrl+L silences, Ctrl+J joins.
def audacity_advanced_segments() -> list[Segment]:
    editing = _group("Edit audio", [
        _tap("Split", ("Split", CTRL, "KEY_I"), color=COLORS["cut"], icon="edit-cut-symbolic"),
        _tap("Join", ("Join", CTRL, "KEY_J"), color=COLORS["cut"], icon="edit-cut-symbolic"),
        _tap("Trim", ("Trim outside the selection", CTRL, "KEY_T"), color=COLORS["cut"], icon="edit-cut-symbolic"),
        _tap("Silence", ("Silence the selection", CTRL, "KEY_L"), color=COLORS["audio"], icon="audio-volume-muted-symbolic"),
        _tap("Duplicate", ("Duplicate", CTRL, "KEY_D"), color=COLORS["history"], icon="edit-copy-symbolic"),
    ], icon="edit-cut-symbolic")
    return [
        _turn("Cursor", ("Jump right", "KEY_DOT"), ("Jump left", "KEY_COMMA"),
              ("Play / Stop", "KEY_SPACE"), color=COLORS["time"], treshold=20),
        _turn("Zoom", ("Zoom in", CTRL, "KEY_1"), ("Zoom out", CTRL, "KEY_3"),
              ("Zoom normal", CTRL, "KEY_2"), color=COLORS["zoom"]),
        _turn("Start / End", ("Skip to end", "KEY_END"), ("Skip to start", "KEY_HOME"),
              ("Record", "KEY_R"), color=COLORS["record"], treshold=90),
        _turn("Clips", ("Next clip", ALT, "KEY_DOT"), ("Previous clip", ALT, "KEY_COMMA"),
              ("Split", CTRL, "KEY_I"), color=COLORS["cut"], treshold=60),
        editing,
        undo_redo([CTRL, "KEY_Y"]),
        volume(),
    ]


# Krita 5.2 (krita/krita.action): [ / ] brush size, I / O opacity, K / L
# darker / lighter colour, X swaps and D resets the colours, E the eraser,
# / the previous preset, , / . favourite presets, PgUp / PgDown layers,
# Insert a new paint layer, Ctrl+J duplicates, Ctrl+E merges down.
def krita_advanced_segments() -> list[Segment]:
    brush = _group("Brush", [
        _look(_turn("Brush colour", ("Lighter colour", "KEY_L"), ("Darker colour", "KEY_K"),
                    ("Swap colours", "KEY_X"), color=COLORS["light"], treshold=20), CANVAS),
        _look(_turn("Presets", ("Next favourite preset", "KEY_COMMA"), ("Previous favourite preset", "KEY_DOT"),
                    ("Last used preset", "KEY_SLASH"), color=COLORS["brush"], treshold=60), CANVAS),
        _tap("Eraser", ("Toggle the eraser", "KEY_E"), color=COLORS["history"], icon="edit-clear-symbolic"),
        _tap("Reset colours", ("Black and white", "KEY_D"), color=COLORS["navigate"], icon="color-select-symbolic"),
    ], icon="document-edit-symbolic")
    layers = _group("Layers", [
        _turn("Select layer", ("Layer above", "KEY_PAGEUP"), ("Layer below", "KEY_PAGEDOWN"),
              color=COLORS["layers"], treshold=60),
        _turn("Move layer", ("Move up", CTRL, "KEY_PAGEUP"), ("Move down", CTRL, "KEY_PAGEDOWN"),
              color=COLORS["layers"], treshold=60),
        _tap("New layer", ("New paint layer", "KEY_INSERT"), color=COLORS["navigate"], icon="list-add-symbolic"),
        _tap("Duplicate layer", ("Duplicate layer", CTRL, "KEY_J"), color=COLORS["history"], icon="edit-copy-symbolic"),
        _tap("Merge down", ("Merge with the layer below", CTRL, "KEY_E"), color=COLORS["cut"], icon="go-down-symbolic"),
    ], icon="view-list-symbolic")
    view = krita_profile_segments()
    return [
        _look(brush_size(), CANVAS),
        _look(view[1], CANVAS),  # Opacity
        _look(view[2], VIEW),  # Zoom
        _look(view[3], VIEW),  # Rotate canvas
        brush,
        layers,
        undo_redo(),
    ]


# GIMP 3: Shift+Ctrl+N new layer, Shift+Ctrl+D duplicates, Ctrl+H anchors,
# X swaps and D resets the colours.
def gimp_advanced_segments() -> list[Segment]:
    basic = gimp_profile_segments()
    layers = _group("Layers", [
        _rename(basic[3], "Select layer"),
        _tap("New layer", ("New layer", CTRL, SHIFT, "KEY_N"), color=COLORS["navigate"], icon="list-add-symbolic"),
        _tap("Duplicate layer", ("Duplicate layer", CTRL, SHIFT, "KEY_D"), color=COLORS["history"], icon="edit-copy-symbolic"),
        _tap("Anchor", ("Anchor the floating layer", CTRL, "KEY_H"), color=COLORS["cut"], icon="go-down-symbolic"),
    ], icon="view-list-symbolic")
    colours = _group("Colours", [
        _tap("Swap colours", ("Swap colours", "KEY_X"), color=COLORS["light"], icon="color-select-symbolic"),
        _tap("Reset colours", ("Black and white", "KEY_D"), color=COLORS["navigate"], icon="color-select-symbolic"),
    ], icon="color-select-symbolic")
    return [
        _look(basic[0], CANVAS),  # Brush size
        _look(basic[1], CANVAS),  # Opacity
        _look(basic[2], VIEW),  # Zoom
        layers,
        colours,
        basic[4],  # Undo / Redo
        clipboard_group(),
    ]


# Inkscape 1.x: H / V flip, Ctrl+D duplicates, Ctrl+G groups, Shift+Ctrl+G
# ungroups, Home / End raise to the top and lower to the bottom.
def inkscape_advanced_segments() -> list[Segment]:
    basic = inkscape_profile_segments()
    objects = _group("Objects", [
        _tap("Duplicate", ("Duplicate", CTRL, "KEY_D"), color=COLORS["history"], icon="edit-copy-symbolic"),
        _tap("Group", ("Group", CTRL, "KEY_G"), color=COLORS["layers"], icon="view-list-symbolic"),
        _tap("Ungroup", ("Ungroup", CTRL, SHIFT, "KEY_G"), color=COLORS["layers"], icon="view-list-symbolic"),
        _tap("Flip horizontal", ("Flip horizontally", "KEY_H"), color=COLORS["navigate"], icon="object-flip-horizontal-symbolic"),
        _tap("Flip vertical", ("Flip vertically", "KEY_V"), color=COLORS["navigate"], icon="object-flip-vertical-symbolic"),
        _turn("Top / Bottom", ("Raise to top", "KEY_HOME"), ("Lower to bottom", "KEY_END"),
              color=COLORS["layers"], treshold=90),
    ], icon="view-list-symbolic")
    return [
        _look(basic[0], VIEW),  # Zoom
        _look(basic[1], CANVAS),  # Rotate
        _look(basic[2], CANVAS),  # Scale
        basic[3],  # Arrange
        objects,
        basic[4],  # Undo / Redo
        clipboard_group(),
    ]


# darktable 5: in the lighttable Ctrl+C / Ctrl+V copy and paste the history
# stack and Ctrl+D duplicates the image; F11 is full screen.
def darktable_advanced_segments() -> list[Segment]:
    basic = darktable_profile_segments()
    develop = _group("Edits", [
        _tap("Copy edits", ("Copy the history stack", CTRL, "KEY_C"), color=COLORS["history"], icon="edit-copy-symbolic"),
        _tap("Paste edits", ("Paste the history stack", CTRL, "KEY_V"), color=COLORS["history"], icon="edit-paste-symbolic"),
        _tap("Duplicate image", ("Duplicate the image", CTRL, "KEY_D"), color=COLORS["layers"], icon="image-x-generic-symbolic"),
        basic[4],  # Undo / Redo
    ], icon="edit-copy-symbolic")
    culling = _group("Culling", [basic[2], basic[3]], icon="starred-symbolic")  # Rating, colour labels
    return [
        _look(basic[0], VIEW),  # Zoom
        _look(basic[1], VIEW),  # Photo
        culling,
        develop,
        fullscreen(),
    ]


def obs_advanced_segments() -> list[Segment]:
    basic = obs_profile_segments()
    output = _group("Output", [basic[1], basic[2], basic[4]], icon="media-record-symbolic")  # Stream, Pause, Replay
    return [basic[0], basic[3], output, volume(), mute(), media_group()]


def browser_segments() -> list[Segment]:
    return [vertical_scroll(), zoom_wheel(), tabs(), browser_history(), volume()]


def browser_advanced_segments() -> list[Segment]:
    tab_group = _group("Tabs", [
        _rename(tabs(), "Switch tabs"),
        _tap("New tab", ("New tab", CTRL, "KEY_T"), color=COLORS["tabs"], icon="tab-new-symbolic"),
        _tap("Close tab", ("Close tab", CTRL, "KEY_W"), color=COLORS["record"], icon="window-close-symbolic"),
        _tap("Reopen tab", ("Reopen the closed tab", CTRL, SHIFT, "KEY_T"), color=COLORS["history"], icon="edit-undo-symbolic"),
    ], icon="view-paged-symbolic")
    return [vertical_scroll(), zoom_wheel(), browser_history(), tab_group,
            _group("Page", [pages(), find(), fullscreen(), horizontal_scroll()], icon="view-paged-symbolic"),
            media_group()]


def libreoffice_segments() -> list[Segment]:
    return [vertical_scroll(), zoom_wheel(), font_size(), undo_redo([CTRL, "KEY_Y"])]


def libreoffice_advanced_segments() -> list[Segment]:
    text = _group("Text", [
        font_size(),
        _tap("Bold", ("Bold", CTRL, "KEY_B"), color=COLORS["zoom"], icon="format-text-bold-symbolic"),
        _tap("Italic", ("Italic", CTRL, "KEY_I"), color=COLORS["zoom"], icon="format-text-italic-symbolic"),
        _tap("Underline", ("Underline", CTRL, "KEY_U"), color=COLORS["zoom"], icon="format-text-underline-symbolic"),
        _tap("Align left", ("Align left", CTRL, "KEY_L"), color=COLORS["navigate"], icon="format-justify-left-symbolic"),
        _tap("Centre", ("Centre", CTRL, "KEY_E"), color=COLORS["navigate"], icon="format-justify-center-symbolic"),
    ], icon="font-x-generic-symbolic")
    return [vertical_scroll(), pages(), zoom_wheel(), text, undo_redo([CTRL, "KEY_Y"]), clipboard_group(), save()]


def code_editor_segments() -> list[Segment]:
    return [vertical_scroll(), _reset_with(zoom_keys(), "Reset zoom", CTRL, "KEY_KP0"),
            _rename(tabs(), "Editor tabs"), undo_redo(), horizontal_scroll()]


# Visual Studio Code / VSCodium on Linux: Ctrl+Alt+- / Ctrl+Shift+- go back
# and forward, Alt+Up/Down move the line, Ctrl+] / Ctrl+[ indent, Ctrl+/
# comments, Ctrl+P opens a file, Ctrl+Shift+P the command palette.
def code_editor_advanced_segments() -> list[Segment]:
    editing = _group("Edit code", [
        _turn("Move line", ("Line down", ALT, "KEY_DOWN"), ("Line up", ALT, "KEY_UP"),
              color=COLORS["history"], treshold=45),
        _turn("Indent", ("Indent", CTRL, "KEY_RIGHTBRACE"), ("Outdent", CTRL, "KEY_LEFTBRACE"),
              color=COLORS["navigate"], treshold=60),
        _tap("Comment", ("Toggle comment", CTRL, "KEY_SLASH"), color=COLORS["zoom"], icon="font-x-generic-symbolic"),
        _tap("Find", ("Find", CTRL, "KEY_F"), color=COLORS["zoom"], icon="edit-find-symbolic"),
        save(),
    ], icon="document-edit-symbolic")
    go = _group("Go to", [
        _turn("Back / Forward", ("Forward", CTRL, SHIFT, "KEY_MINUS"), ("Back", CTRL, ALT, "KEY_MINUS"),
              color=COLORS["history"], treshold=60),
        _tap("Open file", ("Quick open", CTRL, "KEY_P"), color=COLORS["navigate"], icon="document-open-symbolic"),
        _tap("Commands", ("Command palette", CTRL, SHIFT, "KEY_P"), color=COLORS["tabs"], icon="system-search-symbolic"),
        pages(),
    ], icon="go-next-symbolic")
    return code_editor_segments()[:4] + [editing, go, clipboard_group()]


def spotify_segments() -> list[Segment]:
    return [tracks(), volume(), seek([SHIFT, "KEY_RIGHT"], [SHIFT, "KEY_LEFT"])]


def spotify_advanced_segments() -> list[Segment]:
    return spotify_segments() + [mute(), _group("System", [brightness(), mic_mute(), overview()],
                                                icon="preferences-system-symbolic")]


def video_player_segments() -> list[Segment]:
    return [seek(), volume(), tracks()]


# F is full screen in VLC, mpv, Celluloid, Totem and Haruna.
def video_player_advanced_segments() -> list[Segment]:
    return video_player_segments() + [
        _tap("Full screen", ("Full screen", "KEY_F"), color=COLORS["tabs"], icon="view-fullscreen-symbolic"),
        mute(), brightness(),
    ]


# Files (GNOME Files, Dolphin, Nemo, Thunar): Alt+Left/Right history,
# Alt+Up the parent folder, Ctrl+H hidden files, Ctrl+PgUp/PgDown tabs.
def file_manager_segments() -> list[Segment]:
    return [
        vertical_scroll(),
        _turn("Back / Forward", ("Forward", ALT, "KEY_RIGHT"), ("Back", ALT, "KEY_LEFT"),
              ("Parent folder", ALT, "KEY_UP"), color=COLORS["history"], treshold=60),
        zoom_wheel(),
        tabs(),
        _tap("Hidden files", ("Show hidden files", CTRL, "KEY_H"), color=COLORS["light"], icon="view-reveal-symbolic"),
    ]


def file_manager_advanced_segments() -> list[Segment]:
    return file_manager_segments() + [
        up_down(),
        _group("Files", [copy(), cut(), paste(), select_all(),
                         _tap("New tab", ("New tab", CTRL, "KEY_T"), color=COLORS["tabs"], icon="tab-new-symbolic")],
               icon="folder-symbolic"),
    ]


# --- SpaceMouse buttons -----------------------------------------------------
#
# The SpacePilot Pro keys, by raw index (spacemouse_devices.py): 1 Fit, view
# keys 2 Top, 3 Left (long R), 4 Right, 5 Front, 6 Bottom (long T), 7 Back
# (long F), 8 Roll cw, 9 Roll ccw (long Roll), 10 ISO 1, 11 ISO 2 (long ISO),
# function keys 12-16 (short) and 17-21 (long press, the device tells them
# apart), Esc / Alt / Shift / Ctrl 22-25. Packs bind the function keys with a
# press action each (a long press may hold keys for a pie menu), the view keys
# where the application has shortcuts of its own, and leave the rest to the
# global map. Menu (0) stays the dial. Labels are for the device's screen.


def _press(label: str, *names: str) -> SpaceMouseButton:
    """A button that presses one key chord."""
    return SpaceMouseButton(press=SpaceMouseAction("macro", keys=list(names)), label=label)


def _hold(label: str, *names: str) -> SpaceMouseButton:
    """A button that holds the keys while it is down (a Blender pie menu)."""
    return SpaceMouseButton(press=SpaceMouseAction("key_hold", keys=list(names)), label=label)


def _presses(label: str, *chords: tuple[str, ...]) -> SpaceMouseButton:
    """A button that presses several chords one after the other ("V, F")."""
    return SpaceMouseButton(press=SpaceMouseAction("macro", macro=[keys(*chord) for chord in chords]), label=label)


def _pass_through() -> SpaceMouseButton:
    """Leave the button to the application, which reads the 3D mouse itself."""
    return SpaceMouseButton(press=SpaceMouseAction("none"))


def _function_keys(*pairs: tuple[SpaceMouseButton, SpaceMouseButton]) -> dict[str, SpaceMouseButton]:
    """(short, long) of the five function keys: indexes 12-16 and 17-21."""
    buttons = {}
    for number, (short, long) in enumerate(pairs):
        buttons[str(12 + number)] = short
        buttons[str(17 + number)] = long
    return buttons


# Blender 5.2 (keymap_data/blender_default.py), the map of the user's hand-tuned
# profile. Blender reads Fit, the view keys and Roll (and Esc, Alt, Shift, Ctrl,
# + and -) from spacenavd itself, so they pass through; its NDOF Menu panel is
# why hide_buttons is wanted with Menu as the dial. ISO 1 / 2 are unbound in
# Blender: camera view (Num0) and camera to view (Ctrl+Alt+Num0). Pie menus
# are opened by holding their key.
def blender_spacemouse_buttons() -> dict[str, SpaceMouseButton]:
    buttons = {str(index): _pass_through() for index in (*range(1, 10), 22, 23, 24, 25, 29, 30)}
    buttons["10"] = _press("Camera view", "KEY_KP0")
    buttons["11"] = _press("Camera to view", CTRL, ALT, "KEY_KP0")
    buttons.update(_function_keys(
        (_press("Edit mode", "KEY_TAB"), _hold("Mode pie", CTRL, "KEY_TAB")),
        (_press("Persp / Ortho", "KEY_KP5"), _hold("View pie", "KEY_GRAVE")),
        (_press("Add", SHIFT, "KEY_A"), _hold("Snap pie", SHIFT, "KEY_S")),
        (_press("X-ray", ALT, "KEY_Z"), _hold("Shading pie", "KEY_Z")),
        (_press("Search", "KEY_F3"), _press("Maximize", CTRL, "KEY_SPACE")),
    ))
    return buttons


# FreeCAD 1.x (src/Gui/CommandView.cpp, Mod/Sketcher/Gui/Command*.cpp). Its
# 3Dconnexion button map is empty until the user loads a model in Customize >
# Spaceball Buttons, so the view keys press FreeCAD's own view shortcuts
# (1 front, 2 top, 3 right, 4 rear, 5 bottom, 6 left, 0 isometric, V, F fit
# all, V, S fit the selection, Shift+Left / Right turn the view). The
# sketcher keys (G, L line ...) need an open sketch; H horizontal, C coincident.
def freecad_spacemouse_buttons() -> dict[str, SpaceMouseButton]:
    buttons = {
        "1": _presses("Fit all", ("KEY_V",), ("KEY_F",)),
        "2": _press("Top", "KEY_2"),
        "3": _press("Left", "KEY_6"),
        "4": _press("Right", "KEY_3"),
        "5": _press("Front", "KEY_1"),
        "6": _press("Bottom", "KEY_5"),
        "7": _press("Rear", "KEY_4"),
        "8": _press("Rotate right", SHIFT, "KEY_RIGHT"),
        "9": _press("Rotate left", SHIFT, "KEY_LEFT"),
        "10": _press("Isometric", "KEY_0"),
        "11": _presses("Fit selection", ("KEY_V",), ("KEY_S",)),
    }
    buttons.update(_function_keys(
        (_presses("Line", ("KEY_G",), ("KEY_L",)), _presses("Arc", ("KEY_G",), ("KEY_A",))),
        (_presses("Rectangle", ("KEY_G",), ("KEY_R",)), _presses("Circle", ("KEY_G",), ("KEY_C",))),
        (_press("Dimension", "KEY_D"), _presses("Trim", ("KEY_G",), ("KEY_T",))),
        (_press("Horizontal", "KEY_H"), _press("Coincident", "KEY_C")),
        (_presses("Orthographic", ("KEY_V",), ("KEY_O",)), _presses("Perspective", ("KEY_V",), ("KEY_P",))),
    ))
    return buttons


def _app_press(label: str, command: str, *names: str) -> SpaceMouseButton:
    """A button that runs an application command (the keys without its link)."""
    return SpaceMouseButton(press=SpaceMouseAction("app", command=command, keys=list(names) or None), label=label)


# SolidCraft reads the cap (spacenavd or hidraw) but no button
# (apps/solidcraft/src/space_mouse/spacenavd.rs skips button records), so
# every key is free: the view keys run the bridge's views (90-degree rolls as
# the navigation cube's arrows), the function keys its tools (see the dial
# pack above for the keys pressed without the bridge).
def solidcraft_spacemouse_buttons() -> dict[str, SpaceMouseButton]:
    buttons = {
        "1": _app_press("Fit", "turnpad.view_fit", "KEY_F6"),
        "2": _app_press("Top", "turnpad.view_top"),
        "6": _app_press("Bottom", "turnpad.view_bottom"),
        "4": _app_press("Right", "turnpad.view_right"),
        "3": _app_press("Left", "turnpad.view_left"),
        "5": _app_press("Front", "turnpad.view_front"),
        "7": _app_press("Back", "turnpad.view_back"),
        "8": _app_press("Roll 90° right", "turnpad.roll_quarter_cw"),
        "9": _app_press("Roll 90° left", "turnpad.roll_quarter_ccw"),
        "10": _app_press("Isometric", "turnpad.view_iso"),
        "11": _app_press("Home view", "turnpad.view_home"),
    }
    buttons.update(_function_keys(
        (_app_press("Extrude", "solid.extrude", "KEY_E"), _app_press("Revolve", "solid.revolve")),
        (_app_press("Fillet", "solid.fillet", "KEY_F"), _app_press("Chamfer", "solid.chamfer")),
        (_app_press("Hole", "solid.hole", "KEY_H"), _app_press("Shell", "solid.shell")),
        (_app_press("Create sketch", "sketch.create"), _app_press("Finish sketch", "sketch.finish")),
        (_app_press("Measure", "inspect.measure", "KEY_I"), _app_press("Search", "turnpad.search", "KEY_S")),
    ))
    return buttons


# Krita 5 (krita/data/shortcuts/krita_default.shortcuts): Ctrl+0 zoom 100 %,
# Tab canvas only, Ctrl+= / Ctrl+- zoom, Ctrl+] / Ctrl+[ rotate the canvas,
# M / Alt+M mirror. Krita has no default for fit and none for a reset rotation.
def krita_spacemouse_buttons() -> dict[str, SpaceMouseButton]:
    buttons = {
        "8": _press("Rotate right", CTRL, "KEY_RIGHTBRACE"),
        "9": _press("Rotate left", CTRL, "KEY_LEFTBRACE"),
    }
    buttons.update(_function_keys(
        (_press("Zoom 100%", CTRL, "KEY_0"), _press("Canvas only", "KEY_TAB")),
        (_press("Zoom in", CTRL, "KEY_EQUAL"), _press("Zoom out", CTRL, "KEY_MINUS")),
        (_press("Rotate right", CTRL, "KEY_RIGHTBRACE"), _press("Rotate left", CTRL, "KEY_LEFTBRACE")),
        (_press("Mirror view", "KEY_M"), _press("Mirror cursor", ALT, "KEY_M")),
        (_press("Undo", CTRL, "KEY_Z"), _press("Redo", CTRL, SHIFT, "KEY_Z")),
    ))
    return buttons


# GIMP 3 (app/actions/view-actions.c, edit-actions.c): Ctrl+Shift+J fits the
# image, + / - on the keypad zoom, 1 is 1:1, ` reverts the zoom, Shift+1 (!)
# resets flip and rotate, F11 full screen, Ctrl+Z / Ctrl+Y, X / D colours.
def gimp_spacemouse_buttons() -> dict[str, SpaceMouseButton]:
    buttons = {"1": _press("Fit image", CTRL, SHIFT, "KEY_J")}
    buttons.update(_function_keys(
        (_press("Zoom in", "KEY_KPPLUS"), _press("Zoom out", "KEY_KPMINUS")),
        (_press("Zoom 1:1", "KEY_1"), _press("Revert zoom", "KEY_GRAVE")),
        (_press("Reset view", SHIFT, "KEY_1"), _press("Full screen", "KEY_F11")),
        (_press("Undo", CTRL, "KEY_Z"), _press("Redo", CTRL, "KEY_Y")),
        (_press("Swap colours", "KEY_X"), _press("Default colours", "KEY_D")),
    ))
    return buttons


# Inkscape 1.x (share/keys/inkscape.xml): 5 zoom page, 4 drawing, 3 selection,
# = / - zoom, ` / ~ previous / next zoom, S selector, N node tool, H / V flip.
def inkscape_spacemouse_buttons() -> dict[str, SpaceMouseButton]:
    buttons = {"1": _press("Zoom page", "KEY_5")}
    buttons.update(_function_keys(
        (_press("Zoom drawing", "KEY_4"), _press("Zoom selection", "KEY_3")),
        (_press("Zoom in", "KEY_EQUAL"), _press("Zoom out", "KEY_MINUS")),
        (_press("Previous zoom", "KEY_GRAVE"), _press("Next zoom", SHIFT, "KEY_GRAVE")),
        (_press("Selector", "KEY_S"), _press("Node tool", "KEY_N")),
        (_press("Flip horizontal", "KEY_H"), _press("Flip vertical", "KEY_V")),
    ))
    return buttons


# darktable 5 (src/libs/navigation.c, copy_history.c, thumbtable.c, ratings.c,
# views/darkroom.c, gui/gtk.c): Alt+3 fits the image, Tab hides the panels,
# Ctrl+B colour assessment (darkroom), R rejects, 0 clears the rating,
# Ctrl+C / Ctrl+V copy and paste the history stack, Ctrl+D duplicates,
# Shift+O raw overexposure (darkroom), Ctrl+Z / Ctrl+Y.
def darktable_spacemouse_buttons() -> dict[str, SpaceMouseButton]:
    buttons = {"1": _press("Fit image", ALT, "KEY_3")}
    buttons.update(_function_keys(
        (_press("Panels", "KEY_TAB"), _press("Colour check", CTRL, "KEY_B")),
        (_press("Reject", "KEY_R"), _press("Clear rating", "KEY_0")),
        (_press("Copy edits", CTRL, "KEY_C"), _press("Paste edits", CTRL, "KEY_V")),
        (_press("Duplicate", CTRL, "KEY_D"), _press("Overexposed", SHIFT, "KEY_O")),
        (_press("Undo", CTRL, "KEY_Z"), _press("Redo", CTRL, "KEY_Y")),
    ))
    return buttons


# Kdenlive 25 (src/mainwindow_actions.cpp, monitor/monitormanager.cpp): Space
# plays, K pauses, S / X the select and razor tools, V / B insert / overwrite
# the zone, Z lifts, Shift+X extracts, I / O set in and out.
def kdenlive_spacemouse_buttons() -> dict[str, SpaceMouseButton]:
    return _function_keys(
        (_press("Play / pause", "KEY_SPACE"), _press("Pause", "KEY_K")),
        (_press("Select tool", "KEY_S"), _press("Razor tool", "KEY_X")),
        (_press("Insert zone", "KEY_V"), _press("Overwrite zone", "KEY_B")),
        (_press("Lift", "KEY_Z"), _press("Extract", SHIFT, "KEY_X")),
        (_press("Set in", "KEY_I"), _press("Set out", "KEY_O")),
    )


# DaVinci Resolve 19/20, Edit page. Third-party shortcut references only (the
# application is closed source): Space plays, K stops, A / B the selection and
# blade tools, F9 / F10 insert / overwrite, I / O mark, Ctrl+Z, Ctrl+Shift+Z,
# Shift+Z fits the timeline. On Wayland Resolve may ignore virtual keys.
def resolve_spacemouse_buttons() -> dict[str, SpaceMouseButton]:
    buttons = {"1": _press("Fit timeline", SHIFT, "KEY_Z")}
    buttons.update(_function_keys(
        (_press("Play / pause", "KEY_SPACE"), _press("Stop", "KEY_K")),
        (_press("Select tool", "KEY_A"), _press("Blade tool", "KEY_B")),
        (_press("Insert", "KEY_F9"), _press("Overwrite", "KEY_F10")),
        (_press("Mark in", "KEY_I"), _press("Mark out", "KEY_O")),
        (_press("Undo", CTRL, "KEY_Z"), _press("Redo", CTRL, SHIFT, "KEY_Z")),
    ))
    return buttons


# KiCad 9 has no 3D mouse support and no view keys that fit every editor (the
# 3D viewer's Z / X / Y would start routing in the PCB editor), so only Fit and
# the function keys: Home fits, F1 / F2 zoom, M moves, G drags, R rotates,
# E opens the properties, Ctrl+Z / Ctrl+Y, Ctrl+Home zooms to the objects,
# N is the next grid.
def kicad_spacemouse_buttons() -> dict[str, SpaceMouseButton]:
    buttons = {"1": _press("Fit to screen", "KEY_HOME")}
    buttons.update(_function_keys(
        (_press("Zoom in", "KEY_F1"), _press("Zoom out", "KEY_F2")),
        (_press("Move", "KEY_M"), _press("Drag", "KEY_G")),
        (_press("Rotate", "KEY_R"), _press("Properties", "KEY_E")),
        (_press("Undo", CTRL, "KEY_Z"), _press("Redo", CTRL, "KEY_Y")),
        (_press("Zoom to objects", CTRL, "KEY_HOME"), _press("Next grid", "KEY_N")),
    ))
    return buttons



@dataclass(frozen=True)
class AppPack:
    name: str
    hints: tuple[str, ...]
    # The basic variant: a short menu with the most used functions.
    factory: Callable[[], list[Segment]]
    # Desktop ID used when the profile is added without the app installed.
    app_id: str = ""
    category: str = "Other"
    note: str = ""
    # The advanced variant: more functions, in groups.
    advanced: Callable[[], list[Segment]] | None = None
    # The dial's look for the whole advanced profile (Profile.overlay).
    advanced_look: tuple[tuple[str, object], ...] = ()
    # SpaceMouse buttons of the pack (Profile.spacemouse_buttons), built fresh
    # on each call; the advanced variant has its own only when it needs one.
    spacemouse: Callable[[], dict[str, SpaceMouseButton]] | None = None
    spacemouse_advanced: Callable[[], dict[str, SpaceMouseButton]] | None = None

    def segments(self, advanced: bool = False) -> list[Segment]:
        return (self.advanced if advanced and self.advanced else self.factory)()

    def spacemouse_buttons(self, advanced: bool = False) -> dict[str, SpaceMouseButton]:
        build = self.spacemouse_advanced if advanced and self.spacemouse_advanced else self.spacemouse
        return build() if build else {}


# The menu of a timeline editor shows at the top, away from the timeline.
TIMELINE_MENU = (("position", "top"),)

# Creator applications first: the ProArt spirit of the laptop.
APP_PACKS: tuple[AppPack, ...] = (
    AppPack("GIMP", ("gimp",), gimp_profile_segments, "org.gimp.GIMP.desktop", "Photo & graphics",
            advanced=gimp_advanced_segments, spacemouse=gimp_spacemouse_buttons),
    AppPack("Krita", ("krita",), krita_profile_segments, "org.kde.krita.desktop", "Photo & graphics",
            advanced=krita_advanced_segments, spacemouse=krita_spacemouse_buttons),
    AppPack("Inkscape", ("inkscape",), inkscape_profile_segments, "org.inkscape.Inkscape.desktop", "Photo & graphics",
            advanced=inkscape_advanced_segments, spacemouse=inkscape_spacemouse_buttons),
    AppPack("darktable", ("darktable",), darktable_profile_segments, "org.darktable.darktable.desktop", "Photo & graphics",
            advanced=darktable_advanced_segments, spacemouse=darktable_spacemouse_buttons),
    AppPack("Blender", ("blender",), blender_profile_segments, "org.blender.Blender.desktop", "3D",
            advanced=blender_advanced_segments, spacemouse=blender_spacemouse_buttons),
    AppPack("FreeCAD", ("freecad",), freecad_profile_segments, "org.freecad.FreeCAD.desktop", "3D",
            "Keys reach the 3D view or the sketch only while it has the focus",
            advanced=freecad_advanced_segments, spacemouse=freecad_spacemouse_buttons),
    AppPack("SolidCraft", ("solidcraft",), solidcraft_profile_segments, "ai.storyteller.solidcraft.desktop", "3D",
            "Runs SolidCraft's commands when it has a control port (SOLIDCRAFT.md), its keys otherwise",
            advanced=solidcraft_advanced_segments, spacemouse=solidcraft_spacemouse_buttons),
    AppPack("KiCad", ("kicad", "pcbnew", "eeschema"), kicad_profile_segments, "org.kicad.pcbnew.desktop",
            "Electronics", "For the PCB editor; the schematic editor shares zoom, grid, move and rotate "
            "(add a profile for it too)",
            advanced=kicad_advanced_segments, spacemouse=kicad_spacemouse_buttons),
    AppPack("Kdenlive", ("kdenlive",), kdenlive_profile_segments, "org.kde.kdenlive.desktop", "Video",
            advanced=kdenlive_advanced_segments, advanced_look=TIMELINE_MENU,
            spacemouse=kdenlive_spacemouse_buttons),
    AppPack("DaVinci Resolve", ("resolve", "davinci"), resolve_profile_segments,
            "com.blackmagicdesign.resolve.desktop", "Video",
            advanced=resolve_advanced_segments, advanced_look=TIMELINE_MENU,
            spacemouse=resolve_spacemouse_buttons),
    AppPack("Shotcut", ("shotcut",), shotcut_profile_segments, "org.shotcut.Shotcut.desktop", "Video",
            advanced=shotcut_advanced_segments, advanced_look=TIMELINE_MENU),
    AppPack("Audacity", ("audacity",), audacity_profile_segments, "org.audacityteam.Audacity.desktop", "Audio",
            advanced=audacity_advanced_segments, advanced_look=TIMELINE_MENU),
    AppPack("OBS Studio", ("obsproject", "obs-studio"), obs_profile_segments, "com.obsproject.Studio.desktop",
            "Audio", "Assign Ctrl+Shift+F8 to F12 in OBS: Settings, Hotkeys", advanced=obs_advanced_segments),
    AppPack("Browser", ("firefox", "chrom", "brave", "vivaldi", "microsoft-edge", "epiphany", "librewolf", "zen_browser", "zen-browser"),
            browser_segments, "firefox.desktop", "Everyday", advanced=browser_advanced_segments),
    AppPack("LibreOffice", ("libreoffice",), libreoffice_segments, "libreoffice-writer.desktop", "Everyday",
            advanced=libreoffice_advanced_segments),
    AppPack("Code editor", ("code", "codium", "zed", "gnome.textedit", "kate"), code_editor_segments,
            "code.desktop", "Everyday", "The advanced variant uses the keys of VS Code and VSCodium",
            advanced=code_editor_advanced_segments),
    AppPack("Spotify", ("spotify",), spotify_segments, "spotify.desktop", "Everyday",
            advanced=spotify_advanced_segments),
    AppPack("Video player", ("vlc", "mpv", "celluloid", "totem", "showtime", "haruna", "smplayer"),
            video_player_segments, "vlc.desktop", "Everyday", advanced=video_player_advanced_segments),
    AppPack("Files", ("nautilus", "dolphin", "nemo", "thunar"), file_manager_segments,
            "org.gnome.Nautilus.desktop", "Everyday", advanced=file_manager_advanced_segments),
)


def _reset_with(segment: Segment, title: str, *names: str) -> Segment:
    """Replace a function's tap action (VS Code resets zoom with Ctrl+Numpad 0)."""
    segment.macro_center = [keys(*names, title=title)]
    return segment


def _rename(segment: Segment, title: str) -> Segment:
    segment.title = title
    return segment


def pack_for_app(app_id: str | None) -> AppPack | None:
    """Return the pre-programmed pack whose hint occurs in the app id."""
    if not app_id:
        return None
    lowered = app_id.lower()
    for pack in APP_PACKS:
        if any(hint in lowered for hint in pack.hints):
            return pack
    return None


def profile_for_app(name: str, app_id: str, advanced: bool = False) -> Profile:
    """Create an application profile, pre-programmed when a pack matches."""
    pack = pack_for_app(app_id)
    if pack is None:
        return Profile(name=name, app_id=app_id)
    return _pack_profile(pack, name, app_id, advanced)


def profile_for_pack(pack: AppPack, advanced: bool = False) -> Profile:
    """Profile for a pack without the application installed."""
    return _pack_profile(pack, pack.name, pack.app_id, advanced)


def _pack_profile(pack: AppPack, name: str, app_id: str, advanced: bool) -> Profile:
    advanced = advanced and pack.advanced is not None
    return Profile(name=name, app_id=app_id, segments=pack.segments(advanced),
                   overlay=dict(pack.advanced_look) if advanced else {},
                   spacemouse_buttons=pack.spacemouse_buttons(advanced))


def variant_summary(pack: AppPack, advanced: bool = False) -> str:
    """"6 functions" or "7 sectors, 3 groups with 14 functions" for a pack variant."""
    segments = pack.segments(advanced)
    groups = [segment for segment in segments if segment.is_group]
    if not groups:
        return f"{len(segments)} functions"
    inside = sum(len(group.items) for group in groups)
    return (f"{len(segments)} sectors, {len(groups)} group{'s' if len(groups) > 1 else ''} "
            f"with {inside} more functions")
