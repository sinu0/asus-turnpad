"""Non-blocking AF_UNIX client of the Turnpad driver's app link.

`tick()` is called from the application's timer (or a bridge's loop) and
never blocks: it (re)connects every RECONNECT_S seconds, sends what is queued,
reads what arrived, and hands `run` requests to `on_run(command_id) -> (ok,
error)`. Twin of integrations/blender/turnpad/client.py."""

import collections
import os
import socket
import time

from . import protocol

RECONNECT_S = 2.0
MAX_QUEUE = 256             # outgoing lines kept while the socket is busy
READ_CHUNK = 65536


class AppLinkClient:
    def __init__(self, path_provider, hello, catalog, on_run, clock=time.monotonic):
        """path_provider() -> socket path or None; hello = the hello message;
        catalog = the commands message; on_run(command_id) -> (ok, error)."""
        self._path_provider = path_provider
        self._hello = hello
        self._catalog = catalog
        self._on_run = on_run
        self._clock = clock
        self._sock = None
        self._next_try = 0.0
        self._outgoing = collections.deque()
        self._pending = b""             # unsent tail of the line being written
        self._buffer = protocol.LineBuffer()
        self._context = None            # last context message, re-sent on connect
        self.driver_protocol = None
        self.last_error = ""

    # state -------------------------------------------------------------

    @property
    def connected(self):
        return self._sock is not None

    def status(self):
        if self.connected:
            return "Connected to the Turnpad driver"
        return self.last_error or "Waiting for the Turnpad driver"

    # sending -----------------------------------------------------------

    def send_context(self, message):
        """Remember the context and queue it when connected."""
        self._context = message
        if self.connected:
            self._queue(message)

    def _queue(self, message):
        try:
            line = protocol.encode(message)
        except (protocol.LineTooLong, TypeError, ValueError) as err:
            self.last_error = "not sent: %s" % err
            return
        if len(self._outgoing) >= MAX_QUEUE:
            self._outgoing.popleft()
        self._outgoing.append(line)

    # connection --------------------------------------------------------

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._outgoing.clear()
        self._pending = b""
        self._buffer.clear()
        self.driver_protocol = None

    def _drop(self, reason):
        self.close()
        self.last_error = reason
        self._next_try = self._clock() + RECONNECT_S

    def _connect(self):
        path = self._path_provider()
        self._next_try = self._clock() + RECONNECT_S
        if not path:
            self.last_error = "No XDG_RUNTIME_DIR, set the socket path in the add-on preferences"
            return
        try:
            info = os.stat(path)
        except OSError:
            self.last_error = "Waiting for the Turnpad driver"
            return
        if info.st_uid != os.getuid():
            self.last_error = "The socket belongs to another user, not connecting"
            return
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.setblocking(False)
            sock.connect(path)
        except OSError as err:
            sock.close()
            self.last_error = "Cannot reach the Turnpad driver (%s)" % (err.strerror or err)
            return
        self._sock = sock
        self.last_error = ""
        self._queue(self._hello)
        self._queue(self._catalog)
        if self._context is not None:
            self._queue(self._context)

    def tick(self):
        """One pass: connect if due, write, read, dispatch. Never raises."""
        try:
            if self._sock is None:
                if self._clock() < self._next_try:
                    return
                self._connect()
                if self._sock is None:
                    return
            self._flush()
            if self._sock is not None:
                self._read()
        except OSError as err:
            self._drop("Connection lost (%s)" % (err.strerror or err))
        except protocol.LineTooLong:
            self._drop("The driver sent a line that is too long")

    def _flush(self):
        while self._sock is not None:
            if not self._pending:
                if not self._outgoing:
                    return
                self._pending = self._outgoing.popleft()
            try:
                sent = self._sock.send(self._pending)
            except (BlockingIOError, InterruptedError):
                return
            self._pending = self._pending[sent:]
            if self._pending:
                return

    def _read(self):
        while self._sock is not None:
            try:
                chunk = self._sock.recv(READ_CHUNK)
            except (BlockingIOError, InterruptedError):
                return
            if not chunk:
                self._drop("The Turnpad driver closed the connection")
                return
            for message in self._buffer.feed(chunk):
                self._dispatch(message)

    def _dispatch(self, message):
        hello = message.get("hello")
        if isinstance(hello, dict):
            self.driver_protocol = hello.get("protocol")
            return
        run = protocol.parse_run(message)
        if run is None:
            return
        command, seq = run
        try:
            ok, error = self._on_run(command)
        except Exception as err:        # a bug in a command must not end the link
            ok, error = False, "%s: %s" % (type(err).__name__, err)
        self._queue(protocol.result_message(seq, ok, error))
