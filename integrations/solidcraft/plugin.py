"""Turnpad as a SolidCraft plugin: a folder ``<data>/solidcraft/plugins/turnpad``
with the ``plugin.json`` SolidCraft lists under Preferences › Plugins
(crates/ui-egui/src/plugins.rs: id, name, version, description and nothing
else; the id is the folder's name). SolidCraft runs no plugin code yet, so
the folder holds only the manifest; the work is the bridge's. The plugin is
the switch: the bridge links a SolidCraft only while the plugin is installed
in its data folder and not turned off there (``disabled_plugins`` of its
preferences, read through ``ui.prefs``).

The settings app installs and removes it (Plugins page); the driver only
reads. Standard library only."""

from __future__ import annotations

import json
import os
import tempfile

from .control import FLATPAK_ID

PLUGIN_ID = "turnpad"
# Raised when what the bridge does for SolidCraft changes (the manifest's version).
PLUGIN_VERSION = "1.0.0"
MANIFEST = {
    "id": PLUGIN_ID,
    "name": "Turnpad",
    "version": PLUGIN_VERSION,
    # SolidCraft shows one line of it: what it does, then how to unlink.
    "description": "Dial, touchpad and SpaceMouse control by Turnpad. Untick to unlink Turnpad.",
}
MAX_MANIFEST_BYTES = 16 * 1024      # what SolidCraft reads

# install_state() answers
INSTALLED = "installed"
OUTDATED = "outdated"               # an older Turnpad manifest
MISSING = "missing"
FOREIGN = "foreign"                 # a folder of that name that is not ours


def data_roots(environ=None) -> list[str]:
    """SolidCraft's data folders: native build, Flatpak, Snap (control.token_dirs' parents)."""
    environ = os.environ if environ is None else environ
    home = environ.get("HOME") or os.path.expanduser("~")
    data = environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    return [
        os.path.join(data, "solidcraft"),
        os.path.join(home, ".var", "app", FLATPAK_ID, "data", "solidcraft"),
        os.path.join(home, "snap", "solidcraft", "current", ".local", "share", "solidcraft"),
    ]


def plugin_dir(root: str) -> str:
    return os.path.join(root, "plugins", PLUGIN_ID)


def root_of_token(token_path: str):
    """The data folder a token file lies in (``<root>/control/PORT.token``), None
    for a folder SOLIDCRAFT_CONTROL_DIR moved elsewhere."""
    control = os.path.dirname(token_path)
    return os.path.dirname(control) if os.path.basename(control) == "control" else None


def read_manifest(root: str):
    """The manifest in the plugin folder of ``root`` as a dict, None when there is none
    a plugin page would read (a link, too large, not JSON)."""
    folder = plugin_dir(root)
    path = os.path.join(folder, "plugin.json")
    try:
        if os.path.islink(folder) or os.path.islink(path) or os.path.getsize(path) > MAX_MANIFEST_BYTES:
            return None
        with open(path, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


def install_state(root: str) -> str:
    manifest = read_manifest(root)
    if manifest is None:
        return FOREIGN if os.path.lexists(plugin_dir(root)) else MISSING
    if manifest.get("id") != PLUGIN_ID or manifest.get("name") != MANIFEST["name"]:
        return FOREIGN
    return INSTALLED if manifest == MANIFEST else OUTDATED


def installed(root: str) -> bool:
    """Whether the bridge may link SolidCraft of this data folder (an older manifest counts)."""
    return install_state(root) in (INSTALLED, OUTDATED)


def installed_for(token_path: str, environ=None) -> bool:
    """The plugin of the SolidCraft whose token file this is; with a moved control
    folder the data folder is unknown, so any of them."""
    root = root_of_token(token_path)
    roots = [root] if root else data_roots(environ)
    return any(installed(candidate) for candidate in roots)


def install(root: str) -> str:
    """Write the manifest (replacing an older one of ours); the folder's path.
    Refuses a folder of that name that is not Turnpad's, or a link."""
    state = install_state(root)
    if state == FOREIGN:
        raise OSError(f"{plugin_dir(root)} exists and is not Turnpad's; not touching it")
    folder = plugin_dir(root)
    os.makedirs(folder, exist_ok=True)
    if os.path.islink(folder):
        raise OSError(f"{folder} is a link; not writing through it")
    descriptor, temporary = tempfile.mkstemp(prefix=".plugin.", suffix=".json", dir=folder)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(MANIFEST, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.chmod(temporary, 0o644)
        os.replace(temporary, os.path.join(folder, "plugin.json"))
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return folder


def remove(root: str) -> bool:
    """Remove Turnpad's plugin folder; False when there was none of ours."""
    if not installed(root):
        return False
    folder = plugin_dir(root)
    os.unlink(os.path.join(folder, "plugin.json"))
    try:
        os.rmdir(folder)
    except OSError:
        pass                        # something else in it: leave the folder
    return True


def disabled_in(prefs) -> bool:
    """Whether SolidCraft's preferences (the result of ``ui.prefs``) turn the plugin off.
    A SolidCraft without a plugin page has no ``disabled_plugins``: on."""
    disabled = prefs.get("disabled_plugins") if isinstance(prefs, dict) else None
    return isinstance(disabled, list) and PLUGIN_ID in disabled
