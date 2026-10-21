"""The bridge host: applications that offer a control channel instead of
add-ons (SolidCraft) get a bridge, a small client of the app link that the
driver runs on a thread of its own, so they need no program of their own.

A bridge joins the app link exactly as an add-on does (hello, catalog,
context, results over the same socket), so the driver treats both alike and
"nothing received is ever executed by the driver" still holds: the driver
only sends ``run`` requests for ids of the bridge's own catalog, and the
bridge turns them into the application's own commands.

A bridge is a module listed in BRIDGES with
  NAME                      short name, for logs and GetStatus
  create(socket_path)       -> an object with
      tick() -> float       one pass that never blocks for long (a request to
                            the application has a timeout); the seconds until
                            it wants the next one
      stop()                close everything
      status() -> dict      state for GetStatus (JSON values only)
Bridges run one after the other on this one thread. A bridge that raises is
logged and rests for FAILURE_REST_S, the others go on.
"""

from __future__ import annotations

import importlib
import logging
import threading
import time

from config.identity import SLUG

log = logging.getLogger(SLUG)

BRIDGES = ("integrations.solidcraft",)
MIN_WAIT_S = 0.02
MAX_WAIT_S = 1.0
FAILURE_REST_S = 10.0


class BridgeHost:
    def __init__(self, socket_path, modules=BRIDGES, clock=time.monotonic):
        self._socket_path = socket_path
        self._modules = tuple(modules)
        self._clock = clock
        self._bridges = []          # [(name, bridge)]
        self._rest_until = {}       # name -> clock time
        self._stop = threading.Event()
        self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Create the bridges and start the thread; False without a socket or bridges."""
        if self.running:
            return True
        if not self._socket_path:
            return False
        bridges = []
        for module_name in self._modules:
            try:
                module = importlib.import_module(module_name)
                bridges.append((str(module.NAME), module.create(self._socket_path)))
            except Exception:
                log.exception("Bridge %s not started", module_name)
        if not bridges:
            return False
        self._bridges = bridges
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=f"{SLUG}-bridges", daemon=True)
        self._thread.start()
        log.info("Application bridges: %s", ", ".join(name for name, _bridge in bridges))
        return True

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            # A pass waits at most for one request's timeout; the driver's stop
            # has 5 s in all, so do not wait for it longer (the thread is a daemon).
            thread.join(1.0)
            if thread.is_alive():
                # Still in a request: its timeout ends it, the process exit closes the sockets.
                self._thread = None
                return
        self._thread = None
        for name, bridge in self._bridges:
            try:
                bridge.stop()
            except Exception:
                log.exception("Bridge %s did not stop cleanly", name)

    def status(self) -> dict:
        """{name: bridge.status()}; read from any thread (plain attributes, no lock,
        so a bridge waiting for its application never holds up GetStatus)."""
        result = {}
        for name, bridge in self._bridges:
            try:
                result[name] = bridge.status()
            except Exception as error:
                result[name] = {"state": "error", "detail": str(error)}
        return result

    def tick_all(self) -> float:
        """One pass over every bridge that is not resting; the seconds to wait."""
        wait = MAX_WAIT_S
        now = self._clock()
        for name, bridge in self._bridges:
            if self._rest_until.get(name, 0.0) > now:
                continue
            try:
                wait = min(wait, float(bridge.tick()))
            except Exception:
                log.exception("Bridge %s failed; it rests for %d s", name, FAILURE_REST_S)
                self._rest_until[name] = now + FAILURE_REST_S
                try:
                    bridge.stop()
                except Exception:
                    pass
        return max(MIN_WAIT_S, min(MAX_WAIT_S, wait))

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(self.tick_all())
