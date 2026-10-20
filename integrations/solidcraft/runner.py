"""Runs a catalog entry in SolidCraft through the control channel.

SolidCraft's own commands go through ``ui.start``: exactly what its toolbar
button does (a tool or dialog opens, a command without one runs, a disabled
one says why in the status line). A sketch tool asked for outside a sketch is
sent as its key instead (``ui.key``): only the key opens Create Sketch and
starts the tool in the new sketch (keymap.rs press()). The bridge's view commands
change the camera at once (``ui.camera``, ``view.navigate`` with a motion),
without the half-second animation a view key starts, so a dial can turn them
step after step."""

from __future__ import annotations

import math
import re

from .catalog import VIEW_BY_ID
from .control import ControlError

ORBIT_STEP_DEGREES = 5.0        # one dial step; SolidCraft's arrow keys turn 15
ROLL_STEP_DEGREES = 5.0
ZOOM_STEP = 0.9                 # distance factor of one step in
PAN_STEP = 0.04                 # of the view's height per step
PITCH_LIMIT = math.pi / 2 - 1e-3


# Sketch commands that are not tools a key starts in a new sketch.
NOT_SKETCH_TOOLS = ("sketch.finish", "sketch.finish_auto_constrain", "sketch.construction")
SINGLE_KEY = re.compile(r"[A-Z0-9]\Z")


class Runner:
    def __init__(self, call, entries, viewport=lambda: None, mode=lambda: ""):
        """call(method, params) -> result (raises ControlError); entries = the
        catalog sent; viewport() -> [left, top, width, height] or None; mode()
        -> the context mode SolidCraft is in ("sketch" while one is edited)."""
        self._call = call
        self._entries = {entry["id"]: entry for entry in entries}
        self._viewport = viewport
        self._mode = mode

    def run(self, command_id: str) -> tuple[bool, str]:
        entry = self._entries.get(command_id)
        if entry is None:
            return False, "not in the catalog"
        try:
            view = VIEW_BY_ID.get(command_id)
            if view is not None:
                self._view(view)
            elif self._starts_a_sketch(entry):
                self._call("ui.key", {"key": entry["keys"]})
            else:
                self._call("ui.start", {"command": command_id})
        except ControlError as error:
            return False, str(error)
        return True, ""

    def _starts_a_sketch(self, entry) -> bool:
        return ("sketch" in entry.get("modes", ()) and entry["id"] not in NOT_SKETCH_TOOLS
                and bool(SINGLE_KEY.match(entry.get("keys") or "")) and self._mode() != "sketch")

    def _view(self, view) -> None:
        if view.action == "view":
            self._call("ui.view", {"view": view.amount, "animate": True})
        elif view.action == "palette":
            self._call("ui.palette", {})
        elif view.action == "pan":
            self._pan(*view.amount)
        elif view.action == "navigate":
            # What the navigation cube's arrows do: animated, in quarter turns.
            camera = self._call("ui.camera", {})
            self._call("engine.execute", {"command": "view.navigate",
                                          "params": {"camera": camera, "action": view.amount}})
        else:
            self._camera(view.action, view.amount)

    def _camera(self, field: str, sign: int) -> None:
        camera = self._call("ui.camera", {})
        if not isinstance(camera, dict):
            raise ControlError("no camera")
        try:
            if field == "zoom":
                change = {"distance": float(camera["distance"]) * (ZOOM_STEP if sign > 0 else 1 / ZOOM_STEP)}
            elif field == "pitch":
                pitch = float(camera["pitch"]) + sign * math.radians(ORBIT_STEP_DEGREES)
                change = {"pitch": max(-PITCH_LIMIT, min(PITCH_LIMIT, pitch))}
            else:
                step = ROLL_STEP_DEGREES if field == "roll" else ORBIT_STEP_DEGREES
                angle = (float(camera.get(field) or 0.0) + sign * math.radians(step)) % math.tau
                change = {field: angle}
        except (KeyError, TypeError, ValueError):
            raise ControlError("unexpected camera") from None
        self._call("ui.camera", change)

    def _pan(self, dx: int, dy: int) -> None:
        viewport = self._viewport()
        if not (isinstance(viewport, list) and len(viewport) == 4):
            viewport = (self._call("ui.inspect", {}) or {}).get("viewport")
        if not (isinstance(viewport, list) and len(viewport) == 4):
            raise ControlError("no viewport to pan")
        width, height = max(1.0, float(viewport[2])), max(1.0, float(viewport[3]))
        camera = self._call("ui.camera", {})
        step = height * PAN_STEP
        self._call("engine.execute", {"command": "view.navigate", "params": {
            "camera": camera, "motion": "pan", "delta": [dx * step, dy * step], "viewport": [width, height]}})
