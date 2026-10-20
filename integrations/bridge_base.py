"""What every bridge does the same way (integrations/bridges.py runs them):
look for the application every DISCOVER_S, link it to the driver's app link
when one answers, follow its context every CONTEXT_S, and let go when it goes.

A bridge subclasses ``LinkedBridge`` and writes only what is its own:

    class Bridge(LinkedBridge):
        NAME = TITLE = LINK_APP = ...
        def candidates(self):       # [(key, candidate)], cheap: no connection
        def connect(self, candidate) -> Session   # raises BridgeError

and a session for one connected application:

    class Session:
        version: str                 # the application's version, "" unknown
        entries: list[dict]          # the catalog (app link wire entries)
        detail: str                  # e.g. "port 7878"
        alive: bool                  # False once the connection is gone
        def run(self, command_id) -> (ok, error)   # never raises
        def poll(self) -> dict       # {mode, label, detail} or {}; raises
                                     # BridgeGone when the application is gone
        def close(self)

A candidate that does not lead to the application (``connect`` raised) rests
for REFUSE_S under its key, so a stale file or a busy port is not hammered.
Standard library and integrations.applink only."""

from __future__ import annotations

import logging
import time

from config.identity import SLUG
from integrations.applink import client as applink_client
from integrations.applink import protocol

log = logging.getLogger(SLUG)


class BridgeError(Exception):
    """The application refused or failed something (its own reason)."""


class BridgeGone(BridgeError):
    """The connection to the application is gone; it has been closed."""


class LinkedBridge:
    NAME = ""                   # for logs and GetStatus
    TITLE = ""                  # the application's name, sent in hello
    LINK_APP = ""               # app link name; must match its desktop id (app_link.app_matches)
    TICK_S = 0.05               # while linked: app link and requests
    CONTEXT_S = 0.5             # how often poll() runs
    DISCOVER_S = 2.0            # how often candidates() runs while unlinked
    REFUSE_S = 10.0             # a candidate that led nowhere rests

    def __init__(self, socket_path, clock=time.monotonic, link_factory=applink_client.AppLinkClient):
        self._socket_path = socket_path
        self._clock = clock
        self._link_factory = link_factory
        self._session = None
        self._link = None
        self._context = None
        self._next_discovery = 0.0
        self._next_context = 0.0
        self._refused = {}
        self.state = "waiting"
        self.detail = ""

    # what a bridge writes ------------------------------------------------------

    def candidates(self) -> list:
        raise NotImplementedError

    def connect(self, candidate):
        raise NotImplementedError

    # state ---------------------------------------------------------------------

    @property
    def linked(self) -> bool:
        return self._session is not None

    @property
    def context(self) -> dict:
        """The application's last context ({} before the first poll)."""
        return dict(self._context or {})

    def status(self) -> dict:
        """Read from other threads (GetStatus): each attribute is read once."""
        session, link = self._session, self._link
        return {
            "name": self.NAME, "state": self.state, "detail": self.detail,
            "version": session.version if session is not None else "",
            "driver": bool(link is not None and link.connected),
        }

    # the host's loop -------------------------------------------------------------

    def tick(self) -> float:
        """One pass; the seconds until the next one."""
        now = self._clock()
        if self._session is None:
            if now < self._next_discovery:
                return self._next_discovery - now
            self._next_discovery = now + self.DISCOVER_S
            self._discover(now)
            if self._session is None:
                return self.DISCOVER_S
        try:
            if now >= self._next_context:
                self._next_context = now + self.CONTEXT_S
                self._poll()
            self._link.tick()
        except BridgeGone as error:
            self._disconnect(str(error))
            return self.DISCOVER_S
        if not self._session.alive:
            # A command found the application gone (its error went back as the result).
            self._disconnect(f"{self.TITLE} closed the connection")
            return self.DISCOVER_S
        return self.TICK_S

    def stop(self) -> None:
        if self._session is not None:
            self._disconnect("stopped")
        self.state = "stopped"

    # linking ---------------------------------------------------------------------

    def _discover(self, now: float) -> None:
        self._refused = {key: until for key, until in self._refused.items() if until > now}
        for key, candidate in self.candidates():
            if key in self._refused:
                continue
            try:
                session = self.connect(candidate)
            except BridgeError as error:
                self._refused[key] = now + self.REFUSE_S
                self.detail = str(error)
                continue
            self._link_up(session)
            return
        self.state = "waiting"

    def _link_up(self, session) -> None:
        entries = list(session.entries)[:protocol.MAX_COMMANDS]
        self._session = session
        self._context = None
        self._next_context = 0.0
        self._link = self._link_factory(
            lambda: self._socket_path,
            protocol.hello_message(self.LINK_APP, self.TITLE, session.version, 0),
            protocol.commands_message(entries),
            session.run,
        )
        self.state = "linked"
        self.detail = f"{session.detail}, {len(entries)} commands" if session.detail else f"{len(entries)} commands"
        log.info("%s %s linked (%s)", self.TITLE, session.version or "?", self.detail)

    def _disconnect(self, reason: str) -> None:
        if self._link is not None:
            self._link.close()
        if self._session is not None:
            self._session.close()
            log.info("%s link closed: %s", self.TITLE, reason)
        self._link = None
        self._session = None
        self._context = None
        self.state = "waiting"
        self.detail = reason
        self._next_discovery = self._clock() + self.DISCOVER_S

    def _poll(self) -> None:
        context = self._session.poll()
        if context and context != self._context:
            self._context = context
            self._link.send_context(protocol.context_message(context["mode"], context["label"], context["detail"]))
