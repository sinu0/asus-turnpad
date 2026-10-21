"""SolidCraft's state as an app link context, from ``ui.inspect``: the mode is
the ribbon tab in use (``solid``, ``sketch`` while a sketch is edited,
``render`` in the Render workspace, ...; catalog.mode_of_tab), the label its
name and the detail the tool or dialog that is open."""

from __future__ import annotations

import re

from .catalog import mode_of_tab

DIALOG_KIND = re.compile(r"\bkind:\s*([A-Za-z][A-Za-z0-9_]*)")


def _words(name: str) -> str:
    """"SketchDimension" -> "Sketch Dimension"."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("_", " ")


def context_of(inspect, labels) -> dict:
    """{mode, label, detail} of an ``ui.inspect`` result; {} when it has no tab."""
    if not isinstance(inspect, dict):
        return {}
    ui = inspect.get("ui") if isinstance(inspect.get("ui"), dict) else {}
    tab = str(ui.get("tab") or "")
    mode = mode_of_tab(tab)
    if not mode:
        return {}
    detail = ""
    tool = inspect.get("tool")
    dialog = inspect.get("dialog")
    if isinstance(tool, str) and tool:
        detail = labels.get(tool, tool)
    elif isinstance(dialog, str) and dialog:
        match = DIALOG_KIND.search(dialog)
        detail = _words(match.group(1)) if match else ""
    return {"mode": mode, "label": " ".join(word.capitalize() for word in tab.split()), "detail": detail}


def viewport_of(inspect):
    """[left, top, width, height] of the 3D view, None when unknown."""
    viewport = inspect.get("viewport") if isinstance(inspect, dict) else None
    if isinstance(viewport, list) and len(viewport) == 4 and all(isinstance(v, (int, float)) for v in viewport):
        return list(viewport)
    return None
