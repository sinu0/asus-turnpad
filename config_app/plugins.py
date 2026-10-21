"""What the settings app's Plugins page decides, without GTK (tests/test_plugins_page.py).

SolidCraft: the control port that SolidCraft opens only when asked
(``SOLIDCRAFT_CONTROL_PORT``, set for the session in
``~/.config/environment.d/solidcraft.conf``), Turnpad's plugin in SolidCraft's
data folders (integrations/solidcraft/plugin.py) and the state of the bridge
as the driver's ``GetStatus`` reports it (``bridges``, JSON text). Blender:
the add-on's status comes from ``GetAppCommands``, see spacemouse_extra.py."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass

from integrations.solidcraft import plugin

ENV_NAME = "SOLIDCRAFT_CONTROL_PORT"
ENV_FILE_NAME = "solidcraft.conf"
DEFAULT_PORT = 7878
SOLIDCRAFT_DESKTOP_ID = "ai.storyteller.solidcraft.desktop"
FLATPAK_ID = "ai.storyteller.solidcraft"
ASSIGNMENT = re.compile(r"^\s*SOLIDCRAFT_CONTROL_PORT\s*=\s*\"?(\d{1,5})\"?\s*$")
# Where each of SolidCraft's data folders comes from (plugin.data_roots() order).
ROOT_KINDS = ("Native build", "Flatpak", "Snap")


def environment_dir(environ=None) -> str:
    """~/.config/environment.d (systemd reads it for the user's session)."""
    environ = os.environ if environ is None else environ
    home = environ.get("HOME") or os.path.expanduser("~")
    config = environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return os.path.join(config, "environment.d")


def env_file(environ=None) -> str:
    return os.path.join(environment_dir(environ), ENV_FILE_NAME)


def _port(text) -> int | None:
    try:
        port = int(text)
    except (TypeError, ValueError):
        return None
    return port if 0 < port < 65536 else None


@dataclass(frozen=True)
class PortSetting:
    port: int | None        # the port the next session gives SolidCraft, None = none
    path: str               # the environment.d file that sets it, "" = none
    ours: bool              # set by our file (the Plugins page may change it)
    current: int | None     # in this session's environment now


def port_setting(environ=None) -> PortSetting:
    """SOLIDCRAFT_CONTROL_PORT as environment.d sets it (files in name order, the
    last one wins, like systemd) and as this session has it."""
    environ = os.environ if environ is None else environ
    directory = environment_dir(environ)
    found, path = None, ""
    try:
        names = sorted(name for name in os.listdir(directory) if name.endswith(".conf"))
    except OSError:
        names = []
    for name in names:
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as handle:
                lines = handle.read(64 * 1024).splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            match = ASSIGNMENT.match(line)
            if match and _port(match.group(1)):
                found, path = _port(match.group(1)), os.path.join(directory, name)
    return PortSetting(found, path, path == env_file(environ), _port(environ.get(ENV_NAME)))


def write_port(port: int = DEFAULT_PORT, environ=None) -> str:
    """Write our environment.d file (atomically); its path."""
    if not _port(port):
        raise ValueError(f"not a port: {port}")
    directory = environment_dir(environ)
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".solidcraft.", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("# Written by Turnpad (Plugins page): SolidCraft opens its control port,\n"
                         "# which Turnpad's SolidCraft bridge uses. Takes effect at the next login.\n"
                         f"{ENV_NAME}={int(port)}\n")
        os.chmod(temporary, 0o644)
        os.replace(temporary, env_file(environ))
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return env_file(environ)


def remove_port(environ=None) -> bool:
    """Remove our environment.d file (only ours); False when there was none."""
    try:
        os.unlink(env_file(environ))
    except FileNotFoundError:
        return False
    return True


def solidcraft_present(environ=None, which=shutil.which, exists=os.path.exists) -> bool:
    """SolidCraft is installed: on the PATH, as a Flatpak (user or system) or a Snap,
    or it has a data folder (it ran here once)."""
    environ = os.environ if environ is None else environ
    home = environ.get("HOME") or os.path.expanduser("~")
    places = (
        os.path.join(home, ".local", "share", "flatpak", "app", FLATPAK_ID),
        os.path.join("/var/lib/flatpak/app", FLATPAK_ID),
        "/snap/solidcraft",
    )
    return bool(which("solidcraft")) or any(exists(place) for place in places) or any(
        exists(root) for root in plugin.data_roots(environ))


@dataclass(frozen=True)
class PluginFolder:
    kind: str               # "Native build", "Flatpak", "Snap"
    root: str               # SolidCraft's data folder
    state: str              # plugin.INSTALLED / OUTDATED / MISSING / FOREIGN
    exists: bool            # the data folder exists (SolidCraft of that kind ran here)


def plugin_folders(environ=None, exists=os.path.exists) -> list[PluginFolder]:
    """Turnpad's plugin in each of SolidCraft's data folders. The native one is
    always listed; the Flatpak's and Snap's only when they exist."""
    folders = []
    for index, (kind, root) in enumerate(zip(ROOT_KINDS, plugin.data_roots(environ))):
        there = exists(root)
        if index == 0 or there:
            folders.append(PluginFolder(kind, root, plugin.install_state(root), there))
    return folders


def plugin_action(state: str) -> str | None:
    """The button a folder gets: "Add", "Update", "Remove", or None (not ours)."""
    return {plugin.MISSING: "Add", plugin.OUTDATED: "Update", plugin.INSTALLED: "Remove"}.get(state)


def describe_folder(folder: PluginFolder) -> str:
    text = {
        plugin.INSTALLED: "Turnpad is in SolidCraft's plugins",
        plugin.OUTDATED: "An older Turnpad plugin: Update writes the current one",
        plugin.MISSING: "Not added: SolidCraft is not linked from this installation",
        plugin.FOREIGN: "A plugin folder named turnpad that Turnpad did not write: left alone",
    }[folder.state]
    return f"{text} · {folder.root}"


def bridge_status(status, name: str = "solidcraft") -> dict | None:
    """The bridge's entry of GetStatus (``bridges`` is JSON text); None when the
    driver is not running or has no such bridge (an older driver)."""
    if not isinstance(status, dict):
        return None
    try:
        bridges = json.loads(status.get("bridges") or "{}")
    except (TypeError, ValueError):
        return None
    entry = bridges.get(name) if isinstance(bridges, dict) else None
    return entry if isinstance(entry, dict) else None


def describe_link(bridge, setting: PortSetting, folders) -> str:
    """The status line of the SolidCraft link, with the next thing to do."""
    if bridge is None:
        return "The Turnpad driver is not running, or it is too old for the SolidCraft bridge"
    state, detail = bridge.get("state"), str(bridge.get("detail") or "")
    if state == "linked":
        version = f" {bridge['version']}" if bridge.get("version") else ""
        return f"Linked to SolidCraft{version} · {detail}" if detail else f"Linked to SolidCraft{version}"
    if not any(folder.state in (plugin.INSTALLED, plugin.OUTDATED) for folder in folders):
        return "Waiting: add Turnpad to SolidCraft's plugins below"
    if setting.port is None and setting.current is None:
        return "Waiting: turn on SolidCraft's control port below, then log out and in"
    if state == "error":
        return f"The bridge failed: {detail}"
    if detail:
        return f"Waiting for SolidCraft · last: {detail}"
    return "Waiting for SolidCraft to start with its control port"


def describe_port(setting: PortSetting) -> str:
    if setting.port is not None:
        where = "Turnpad's file" if setting.ours else os.path.basename(setting.path)
        line = f"SolidCraft opens port {setting.port} ({where} in ~/.config/environment.d)"
        if setting.current != setting.port:
            line += " · log out and in once for it to apply"
        return line
    if setting.current is not None:
        return f"This session sets port {setting.current}, but nothing sets it for the next one"
    return "Off: SolidCraft starts without a control port, so Turnpad cannot reach it"
