"""Glue between Blender and the app link: the client, the context tracker and
the runner behind one object the timer drives."""

import os

import bpy

from . import catalog
from . import client
from . import context
from . import protocol
from . import runner

CONTEXT_EVERY = 4           # timer ticks per context poll


class Service:
    def __init__(self, settings, run=runner.run, reader=context.read):
        """settings() -> (enabled, socket path override or "")."""
        self._settings = settings
        self._ticks = 0
        self.tracker = context.ContextTracker(reader)
        self.client = client.AppLinkClient(
            self._socket_path,
            protocol.hello_message("blender", "Blender", bpy.app.version_string, os.getpid()),
            protocol.commands_message(catalog.wire_catalog()),
            run,
        )

    def _socket_path(self):
        override = self._settings()[1].strip()
        return os.path.expanduser(override) if override else protocol.default_socket_path()

    def tick(self):
        """One timer pass; returns the seconds until the next one."""
        if not self._settings()[0]:
            if self.client.connected:
                self.client.close()
            return 0.5
        if self._ticks % CONTEXT_EVERY == 0:
            changed = self.tracker.poll()
            if changed is not None:
                self.client.send_context(
                    protocol.context_message(changed["mode"], changed["label"], changed["detail"]))
        self._ticks += 1
        self.client.tick()
        return 0.05

    def file_loaded(self):
        """A new file may change the mode: send the context again."""
        self.tracker.reset()
        self._ticks = 0

    def status(self):
        return self.client.status()

    def stop(self):
        self.client.close()
