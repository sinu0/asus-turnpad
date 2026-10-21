"""The SolidCraft bridge: SolidCraft (open-source parametric CAD) has no
add-ons yet, but a control channel (control.py). The bridge finds a running
SolidCraft by its token file, reads its command registry and joins the
driver's app link as the application "solidcraft" (``ai.storyteller.solidcraft``
is its desktop id), so dial functions and SpaceMouse buttons run its commands
by id, whatever window the pointer is over, and profile contexts follow its
ribbon tab. SolidCraft must run with a control port: SOLIDCRAFT_CONTROL_PORT
(set once in ~/.config/environment.d, see SOLIDCRAFT.md) or ``--control PORT``,
and have Turnpad among its plugins (plugin.py): the bridge links it only while
the plugin is installed and turned on in SolidCraft's Preferences › Plugins.

Runs in the driver's bridge host (integrations/bridges.py), on its thread;
the life of the link is integrations/bridge_base.py's.
"""

from __future__ import annotations

import os
import socket

from integrations.bridge_base import LinkedBridge

from . import catalog, plugin
from .context import context_of, viewport_of
from .control import ConnectionLost, ControlClient, ControlError, find_instances, port_owner, read_token, token_dirs
from .runner import Runner

NAME = "solidcraft"
PLUGIN_CHECK_EVERY = 4          # polls (2 s) between looks at the plugin and its switch
OFF_IN_SOLIDCRAFT = "turned off in SolidCraft's Preferences › Plugins"
NOT_INSTALLED = "Turnpad is not among SolidCraft's plugins (Turnpad settings › Plugins)"


class Session:
    """One SolidCraft window on its control port."""

    def __init__(self, instance, control, about, commands, plugin_installed=lambda: True):
        self.port = instance.port
        self._plugin_installed = plugin_installed
        self._polls = 0
        self._control = control
        self.version = str(about.get("version") or "") if isinstance(about, dict) else ""
        self.entries = catalog.wire_catalog(commands)
        self.detail = f"port {instance.port}"
        self._labels = catalog.labels(self.entries)
        self._viewport = None
        self._mode = ""
        self._runner = Runner(control.call, self.entries, viewport=lambda: self._viewport, mode=lambda: self._mode)

    @property
    def alive(self) -> bool:
        return self._control.connected

    def run(self, command_id):
        return self._runner.run(command_id)

    def poll(self) -> dict:
        self._polls += 1
        if self._polls % PLUGIN_CHECK_EVERY == 0:
            self._check_plugin()
        try:
            inspect = self._control.call("ui.inspect", {})
        except ConnectionLost:
            raise
        except ControlError:
            return {}
        self._viewport = viewport_of(inspect)
        context = context_of(inspect, self._labels)
        self._mode = context.get("mode", "")
        return context

    def _check_plugin(self) -> None:
        """The plugin removed or turned off: the link ends (BridgeGone)."""
        if not self._plugin_installed():
            self._control.close()
            raise ConnectionLost(NOT_INSTALLED)
        try:
            prefs = self._control.call("ui.prefs", {})
        except ConnectionLost:
            raise
        except ControlError:
            return
        if plugin.disabled_in(prefs):
            self._control.close()
            raise ConnectionLost(OFF_IN_SOLIDCRAFT)

    def close(self) -> None:
        self._control.close()


class Bridge(LinkedBridge):
    NAME = NAME
    TITLE = "SolidCraft"
    LINK_APP = "solidcraft"

    def __init__(self, socket_path, environ=None, connect=socket.create_connection, uid=None, owner=port_owner, **kwargs):
        super().__init__(socket_path, **kwargs)
        self._environ = environ
        self._connect = connect
        self._uid = os.getuid() if uid is None else uid
        self._owner = owner

    def status(self) -> dict:
        session = self._session
        status = super().status()
        status["port"] = session.port if session is not None else 0
        return status

    def candidates(self):
        """Token files of running SolidCraft windows, newest first; a file
        rests under its path and time, so a new launch is tried at once."""
        return [((instance.token_path, instance.mtime), instance)
                for instance in find_instances(token_dirs(self._environ), self._uid)]

    def connect(self, instance) -> Session:
        installed = lambda: plugin.installed_for(instance.token_path, self._environ)  # noqa: E731
        if not installed():
            raise ControlError(NOT_INSTALLED)
        owner = self._owner(instance.port)
        if owner is not None and owner != self._uid:
            raise ControlError(f"port {instance.port} belongs to another user")
        control = ControlClient(instance.port, read_token(instance.token_path, self._uid), connect=self._connect)
        try:
            control.open()
            about = control.call("ui.help", {})
            prefs = control.call("ui.prefs", {})
            if plugin.disabled_in(prefs):
                raise ControlError(OFF_IN_SOLIDCRAFT)
            commands = control.call("engine.commands", {})
        except ControlError as error:
            control.close()
            raise ControlError(f"port {instance.port}: {error}") from None
        return Session(instance, control, about, commands, plugin_installed=installed)


def create(socket_path) -> Bridge:
    return Bridge(socket_path)
