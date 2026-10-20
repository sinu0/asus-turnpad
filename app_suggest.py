"""The one-time offer of a ready profile: when an application that has a
pre-programmed pack (config/presets.py) gets the focus and no profile exists
for it, the driver offers to create one, once per application. What was
offered is remembered in ``suggested.json`` next to config.json.

Pure (no hardware, no desktop): the driver does the notification."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from config.identity import SLUG

log = logging.getLogger(SLUG)

MAX_REMEMBERED = 500


@dataclass(frozen=True)
class Suggestion:
    pack: str           # the pack's name, "Blender"
    app_id: str         # what the settings app gets as --add-profile
    window_id: str      # the focused application as the driver saw it


def window_key(window_id) -> str:
    return str(window_id or "").strip().lower()


def load(path) -> set[str]:
    """The application ids offered so far; empty when the file is missing or unusable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    items = data.get("offered") if isinstance(data, dict) else None
    return {window_key(item) for item in items if isinstance(item, str) and item.strip()} if isinstance(items, list) else set()


def save(path, offered: set[str]) -> bool:
    """Write the file atomically; False when it could not be written."""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".suggested-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump({"offered": sorted(offered)[:MAX_REMEMBERED]}, stream, indent=2)
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
    except OSError as error:
        log.warning("Could not remember the profile offer in %s: %s", path, error)
        return False
    return True


class Suggester:
    """``consider()`` says whether to offer a profile for the focused application:
    only for an application with a pack, without a profile, and once per application
    id (the offer is written to the file before it is returned, so a crash cannot
    repeat it). Thread-safe."""

    def __init__(self, path, find_pack=None):
        if find_pack is None:
            from config.presets import pack_for_app as find_pack
        self.path = path
        self._find_pack = find_pack
        self._lock = threading.Lock()
        self._offered = load(path)

    def offered(self, window_id) -> bool:
        with self._lock:
            return window_key(window_id) in self._offered

    def consider(self, window_id, has_profile: bool) -> Suggestion | None:
        key = window_key(window_id)
        if not key or has_profile or key.startswith("-"):
            # An id starting with "-" could be read as an option by the settings app.
            return None
        pack = self._find_pack(key)
        if pack is None:
            return None
        with self._lock:
            if key in self._offered:
                return None
            self._offered.add(key)
            save(self.path, self._offered)
        # A desktop id goes on as it is, in its own case (the memory key is lower case);
        # for a binary path or a name the pack's own id.
        app_id = str(window_id).strip() if key.endswith(".desktop") else pack.app_id
        return Suggestion(pack=pack.name, app_id=app_id, window_id=key)
