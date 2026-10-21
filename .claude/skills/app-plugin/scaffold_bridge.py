#!/usr/bin/env python3
"""Scaffold a Turnpad bridge for an application with a control channel.

    python3 .claude/skills/app-plugin/scaffold_bridge.py NAME --title "Title" \\
        --desktop-id org.example.App.desktop [--wire] [--root .]

Writes integrations/NAME/{__init__,catalog,control}.py and
tests/test_NAME_bridge.py (passing tests with TODOs), built on
integrations/bridge_base.py. With --wire it also registers the bridge
everywhere it must be listed: integrations/bridges.py BRIDGES, install.sh
(directory and install line), packaging/install-files.sh (for dir in ...),
tests/test_install.py BRIDGE_PACKAGES and packaging/check-imports.py.
Refuses to overwrite existing files. Prints what is left to do."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

NAME = re.compile(r"[a-z][a-z0-9_]{1,30}\Z")

INIT = '''"""The {title} bridge: {title} has a control channel but no Turnpad add-on,
so the driver's bridge host (integrations/bridges.py) links it to the app
link as the application "{link_app}" (desktop id ``{desktop_id}``).

TODO: one paragraph: how {title} is reached (port, socket, D-Bus name), how
the bridge authenticates, what the user must turn on ({doc}), and the
version of {title} whose sources were read.
"""

from __future__ import annotations

from integrations.bridge_base import BridgeError, BridgeGone, LinkedBridge

from . import catalog
from .control import Control, ControlError, ConnectionLost

NAME = "{name}"


class Session:
    """One connected {title}."""

    def __init__(self, control, version, commands):
        self._control = control
        self.version = version
        self.entries = catalog.wire_catalog(commands)
        self.detail = control.describe()
        self._known = {{entry["id"] for entry in self.entries}}

    @property
    def alive(self) -> bool:
        return self._control.connected

    def run(self, command_id):
        """Run one catalog entry; (ok, error). Never raises."""
        if command_id not in self._known:
            return False, "not in the catalog"
        try:
            # TODO: the application's own request for command_id.
            self._control.call("run", {{"command": command_id}})
        except ControlError as error:
            return False, str(error)
        return True, ""

    def poll(self) -> dict:
        """{{mode, label, detail}} of what the user is doing, {{}} when unknown.
        Raises BridgeGone (ConnectionLost) when the application is gone."""
        try:
            state = self._control.call("state", {{}})      # TODO: the application's request
        except ConnectionLost:
            raise
        except ControlError:
            return {{}}
        mode = str((state or {{}}).get("mode") or "")
        return {{"mode": mode, "label": mode.title(), "detail": ""}} if mode else {{}}

    def close(self) -> None:
        self._control.close()


class Bridge(LinkedBridge):
    NAME = NAME
    TITLE = "{title}"
    LINK_APP = "{link_app}"

    def __init__(self, socket_path, environ=None, **kwargs):
        super().__init__(socket_path, **kwargs)
        self._environ = environ

    def candidates(self):
        """[(key, candidate)] of running {title} instances, cheap (no connection).
        TODO: find them (token files, sockets in XDG_RUNTIME_DIR, D-Bus names)."""
        return []

    def connect(self, candidate) -> Session:
        control = Control(candidate)
        try:
            control.open()
            version = control.call("version", {{}})        # TODO
            commands = control.call("commands", {{}})      # TODO
        except ControlError:
            control.close()
            raise
        return Session(control, str(version or ""), commands)


def create(socket_path) -> Bridge:
    return Bridge(socket_path)


__all__ = ["Bridge", "BridgeError", "BridgeGone", "NAME", "Session", "create"]
'''

CATALOG = '''"""What the dial and the SpaceMouse buttons may run in {title}, as app link
catalog entries (see app_link.py: id [A-Za-z0-9_.:-]{{1,128}}, title, category,
icon, keys, modes; at most MAX_COMMANDS).

TODO: name the source files the commands and their default keys come from.
"""

from __future__ import annotations

from integrations.applink.protocol import MAX_COMMANDS

# Commands of the bridge itself (things the application has no command for,
# e.g. camera steps a dial can turn): (id, title, category, keys).
OWN_COMMANDS = ()


def app_entries(commands) -> list[dict]:
    """The application's command list as catalog entries.
    TODO: map the application's fields; leave out what must never run from a
    dial (file dialogs that write, quitting, settings)."""
    entries = []
    for command in commands if isinstance(commands, list) else []:
        if not isinstance(command, dict) or not command.get("id"):
            continue
        entries.append({{
            "id": str(command["id"]), "title": str(command.get("title") or command["id"]),
            "category": str(command.get("category") or ""), "icon": "",
            "keys": str(command.get("keys") or ""), "modes": [],
        }})
    return entries


def wire_catalog(commands) -> list[dict]:
    entries = [{{"id": i, "title": t, "category": c, "icon": "", "keys": k, "modes": []}}
               for i, t, c, k in OWN_COMMANDS]
    seen = {{entry["id"] for entry in entries}}
    for entry in app_entries(commands):
        if entry["id"] not in seen:
            seen.add(entry["id"])
            entries.append(entry)
    return entries[:MAX_COMMANDS]
'''

CONTROL = '''"""{title}'s control channel. TODO: transport (TCP JSON lines, a Unix
socket, D-Bus, a WebSocket), authentication and limits, with the source file
of {title} each fact was read from. See integrations/solidcraft/control.py for
a token-protected localhost JSON-lines client with timeouts."""

from __future__ import annotations

from integrations.bridge_base import BridgeError, BridgeGone

CALL_TIMEOUT_S = 3.0


class ControlError(BridgeError):
    """{title} refused or failed a request (its own reason)."""


class ConnectionLost(ControlError, BridgeGone):
    """The connection is gone or in an unknown state; it has been closed."""


class Control:
    """One connection. call() blocks at most CALL_TIMEOUT_S and closes the
    connection (ConnectionLost) when no reply comes in time."""

    def __init__(self, candidate):
        self._candidate = candidate
        self.connected = False

    def describe(self) -> str:
        return str(self._candidate)

    def open(self) -> None:
        raise ConnectionLost("TODO: connect to {title}")

    def call(self, method: str, params: dict | None = None):
        raise ConnectionLost("TODO: send a request to {title}")

    def close(self) -> None:
        self.connected = False
'''

TEST = '''"""The {title} bridge (integrations/{name}/). TODO: a fake {title} control
channel (see tests/fake_solidcraft.py) and the end-to-end test of
tests/test_bridges.py BridgeTest: driver (AppLink.run) -> bridge -> fake."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app_link  # noqa: E402
from integrations import {name}  # noqa: E402
from integrations.{name} import catalog  # noqa: E402


class {cls}BridgeTest(unittest.TestCase):
    def test_link_name_matches_the_desktop_id(self):
        self.assertTrue(app_link.app_matches({name}.Bridge.LINK_APP, "{desktop_id}"))

    def test_catalog_survives_the_driver(self):
        entries = catalog.wire_catalog([{{"id": "demo.command", "title": "Demo", "keys": "Ctrl+D"}}])
        self.assertEqual(app_link.clean_catalog(entries), entries)

    def test_nothing_running(self):
        bridge = {name}.Bridge("/nonexistent/socket")
        self.assertEqual(bridge.tick(), bridge.DISCOVER_S)
        self.assertEqual(bridge.status()["state"], "waiting")


if __name__ == "__main__":
    unittest.main()
'''


def write(path: Path, text: str) -> None:
    if path.exists():
        sys.exit(f"{path} exists; not overwriting")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"wrote {path}")


def _tuple(items: list[str]) -> str:
    return "(" + ", ".join(items) + ("," if len(items) == 1 else "") + ")"


def _add_to_tuple(text: str, pattern: str, item: str, where: str) -> str:
    """Add ``item`` (a literal) to the tuple the regex group 1 of ``pattern`` holds."""
    match = re.search(pattern, text, re.M)
    if not match:
        sys.exit(f"{where}: {pattern!r} not found; wire it by hand")
    items = [part.strip() for part in match.group(1).split(",") if part.strip()]
    if item not in items:
        items.append(item)
    return text[:match.start(1) - 1] + _tuple(items) + text[match.end(1) + 1:]


def wire(root: Path, name: str) -> None:
    """All edits are checked before any file is written."""
    module = f"integrations.{name}"
    package = f"integrations/{name}"
    edits = {}

    path = root / "integrations" / "bridges.py"
    edits[path] = _add_to_tuple(path.read_text(), r"^BRIDGES = \(([^)]*)\)", f'"{module}"', str(path))

    path = root / "install.sh"
    text = path.read_text()
    folder = ' "$INSTALL_DIR_PATH/gnome/extension"'
    if text.count(folder) != 1:
        sys.exit(f"{path}: the mkdir line with{folder} not found once; wire it by hand")
    text = text.replace(folder, f' "$INSTALL_DIR_PATH/{package}"' + folder)
    lines = list(re.finditer(r'^ *sudo install -m 644 -t "\$INSTALL_DIR_PATH/integrations/[a-z0-9_]+" .*\n', text, re.M))
    if not lines:
        sys.exit(f"{path}: no install line of a bridge; wire it by hand")
    last = lines[-1]
    text = text[:last.end()] + f'    sudo install -m 644 -t "$INSTALL_DIR_PATH/{package}" {package}/*.py\n' + text[last.end():]
    edits[path] = text

    path = root / "packaging" / "install-files.sh"
    text = path.read_text()
    match = re.search(r"^for dir in ([^;]*\bintegrations\b[^;]*); do$", text, re.M)
    if not match:
        sys.exit(f"{path}: the 'for dir in ... integrations ...; do' line not found; wire it by hand")
    edits[path] = text[:match.end(1)] + f" {package}" + text[match.end(1):]

    path = root / "tests" / "test_install.py"
    edits[path] = _add_to_tuple(path.read_text(), r"^BRIDGE_PACKAGES = \(([^)]*)\)", f'"{package}"', str(path))

    path = root / "packaging" / "check-imports.py"
    text = path.read_text()
    anchor = "import integrations.bridges"
    at = text.find(anchor)
    if at < 0:
        sys.exit(f"{path}: no '{anchor}' line; wire it by hand")
    line_end = text.index("\n", at) + 1
    edits[path] = text[:line_end] + f"import {module}  # noqa: E402\n" + text[line_end:]

    for path, text in edits.items():
        path.write_text(text)
        print(f"edited {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="module name, e.g. obs (lower case)")
    parser.add_argument("--title", required=True, help="the application's name, e.g. 'OBS Studio'")
    parser.add_argument("--desktop-id", required=True, help="its desktop id, e.g. com.obsproject.Studio.desktop")
    parser.add_argument("--link-app", help="app link name (default: NAME); must match the desktop id")
    parser.add_argument("--wire", action="store_true", help="also register it in the host, installers and tests")
    parser.add_argument("--root", default=".", help="the repository (default: .)")
    args = parser.parse_args()
    if not NAME.match(args.name):
        sys.exit("NAME: lower case letters, digits and _")
    root = Path(args.root).resolve()
    if not (root / "integrations" / "bridge_base.py").exists():
        sys.exit(f"{root} is not the Turnpad repository")
    sys.path.insert(0, str(root))
    import app_link  # noqa: E402

    link_app = args.link_app or args.name.replace("_", "-")
    if not app_link.app_matches(link_app, args.desktop_id):
        sys.exit(f"app link name {link_app!r} does not match {args.desktop_id!r} (app_link.app_matches); "
                 "pick --link-app from the desktop id's words")
    values = dict(name=args.name, title=args.title, link_app=link_app, desktop_id=args.desktop_id,
                  doc=f"{args.name.upper()}.md", cls="".join(part.capitalize() for part in args.name.split("_")))
    package = root / "integrations" / args.name
    write(package / "__init__.py", INIT.format(**values))
    write(package / "catalog.py", CATALOG.format(**values))
    write(package / "control.py", CONTROL.format(**values))
    write(root / "tests" / f"test_{args.name}_bridge.py", TEST.format(**values))
    if args.wire:
        wire(root, args.name)
    print(f"""
Left to do (.claude/skills/app-plugin/SKILL.md, steps 4-8):
  - control.py, catalog.py, Session.run/poll, Bridge.candidates/connect: the TODOs
  - tests/fake_{args.name}.py + an end-to-end test like tests/test_bridges.py BridgeTest
  - a pack in config/presets.py with app_step()/_app_press() and key fallbacks ({args.desktop_id})
  - {values['doc']}, README.md, CHANGELOG.md, AGENTS.md, packaging/install-files.sh doc list
  - {'' if args.wire else 'run again with --wire, or register it by hand; '}python3 -m unittest discover -s tests""")


if __name__ == "__main__":
    main()
