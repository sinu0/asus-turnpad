"""What the dial and the SpaceMouse buttons may run in SolidCraft, as app link
catalog entries: the bridge's own view commands (camera steps a dial can turn,
which SolidCraft has no keys for) and SolidCraft's toolbar commands, read from
its registry (``engine.commands``) when the bridge connects.

Checked against SolidCraft 41a62d2 (2026-10-20): crates/engine/src/cmd/*.rs
(CommandSpec ids, labels, tabs, keys), crates/ui-egui/src/keymap.rs and
toolbar.rs (keys the window itself handles: arrows turn the view 15 degrees,
Shift+arrows 90, Ctrl+arrows pan, F6 fits, S opens command search)."""

from __future__ import annotations

from dataclasses import dataclass

from integrations.applink.protocol import MAX_COMMANDS

VIEW_CATEGORY = "View"
# Commands the toolbar does not carry but a dial should reach.
EXTRA_COMMANDS = ("edit.undo", "edit.redo")


@dataclass(frozen=True)
class ViewCommand:
    id: str
    title: str
    # What the runner does (runner.py): camera field and sign, a pan or a view.
    action: str
    amount: object = None
    # SolidCraft's own key for the same thing, shown in the settings app.
    keys: str = ""


VIEW_COMMANDS = (
    # Same directions as SolidCraft's arrow keys (navigation.rs view_key):
    # Left moves the eye to the left, which turns the camera's yaw up.
    ViewCommand("turnpad.orbit_left", "Orbit left", "yaw", +1, "Left"),
    ViewCommand("turnpad.orbit_right", "Orbit right", "yaw", -1, "Right"),
    ViewCommand("turnpad.orbit_up", "Orbit up", "pitch", +1, "Up"),
    ViewCommand("turnpad.orbit_down", "Orbit down", "pitch", -1, "Down"),
    ViewCommand("turnpad.roll_cw", "Roll clockwise", "roll", +1),
    ViewCommand("turnpad.roll_ccw", "Roll counterclockwise", "roll", -1),
    ViewCommand("turnpad.roll_quarter_cw", "Roll 90° clockwise", "navigate", "clockwise"),
    ViewCommand("turnpad.roll_quarter_ccw", "Roll 90° counterclockwise", "navigate", "counterclockwise"),
    ViewCommand("turnpad.zoom_in", "Zoom in", "zoom", +1),
    ViewCommand("turnpad.zoom_out", "Zoom out", "zoom", -1),
    ViewCommand("turnpad.pan_left", "Pan left", "pan", (-1, 0), "Ctrl+Left"),
    ViewCommand("turnpad.pan_right", "Pan right", "pan", (1, 0), "Ctrl+Right"),
    ViewCommand("turnpad.pan_up", "Pan up", "pan", (0, -1), "Ctrl+Up"),
    ViewCommand("turnpad.pan_down", "Pan down", "pan", (0, 1), "Ctrl+Down"),
    ViewCommand("turnpad.view_fit", "Fit", "view", "fit", "F6"),
    ViewCommand("turnpad.view_home", "Home view", "view", "home"),
    ViewCommand("turnpad.view_iso", "Isometric", "view", "iso"),
    ViewCommand("turnpad.view_front", "Front", "view", "front"),
    ViewCommand("turnpad.view_back", "Back", "view", "back"),
    ViewCommand("turnpad.view_top", "Top", "view", "top"),
    ViewCommand("turnpad.view_bottom", "Bottom", "view", "bottom"),
    ViewCommand("turnpad.view_left", "Left", "view", "left"),
    ViewCommand("turnpad.view_right", "Right", "view", "right"),
    ViewCommand("turnpad.search", "Command search", "palette", None, "S"),
)
VIEW_BY_ID = {command.id: command for command in VIEW_COMMANDS}


def mode_of_tab(tab) -> str:
    """A ribbon tab as an app link mode: "SHEET METAL" is "sheet_metal"."""
    return "_".join(str(tab or "").lower().split())


def _title(text) -> str:
    return " ".join(word.capitalize() for word in str(text or "").split())


def _wanted(command: dict) -> bool:
    if not isinstance(command, dict) or command.get("planned"):
        return False
    return bool(command.get("tab")) or command.get("id") in EXTRA_COMMANDS


def app_entries(commands) -> list[dict]:
    """SolidCraft's registry (the result of ``engine.commands``) as catalog
    entries: toolbar commands and EXTRA_COMMANDS, in registry order."""
    entries = []
    for command in commands if isinstance(commands, list) else []:
        if not _wanted(command):
            continue
        identifier = str(command.get("id") or "")
        tab, panel = command.get("tab") or "", command.get("panel") or ""
        category = " › ".join(part for part in (_title(tab), _title(panel)) if part) or "Edit"
        entries.append({
            "id": identifier,
            "title": str(command.get("label") or identifier),
            "category": category,
            "icon": "",
            "keys": str(command.get("shortcut") or ""),
            "modes": [mode_of_tab(tab)] if tab else [],
        })
    return entries


def view_entries() -> list[dict]:
    return [{"id": command.id, "title": command.title, "category": VIEW_CATEGORY, "icon": "",
             "keys": command.keys, "modes": []} for command in VIEW_COMMANDS]


def wire_catalog(commands) -> list[dict]:
    """The whole catalog the bridge sends: view commands first, then
    SolidCraft's, cut at the driver's limit."""
    entries = view_entries()
    seen = {entry["id"] for entry in entries}
    for entry in app_entries(commands):
        if entry["id"] not in seen:
            seen.add(entry["id"])
            entries.append(entry)
    return entries[:MAX_COMMANDS]


def labels(entries) -> dict[str, str]:
    """Command id -> title, for the context's detail line."""
    return {entry["id"]: entry["title"] for entry in entries}
