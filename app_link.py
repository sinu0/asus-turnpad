"""The app link: applications tell the driver what they can do and what they
are doing, and run commands on request (the Blender add-on is the first one).

The driver is the server of a Unix socket, ``$XDG_RUNTIME_DIR/asus-turnpad-apps.sock``
(``config.identity.app_link_socket_path()``), created with mode 0600 in the
user's private runtime directory (it is checked like ``desktop/kwin_script.py``
checks its directory) and served from a thread of its own, so a slow or stuck
application never reaches the touch loop. Peers must be this user (SO_PEERCRED).

Newline-delimited JSON objects, UTF-8, at most MAX_LINE bytes per line (a longer
line ends the connection).

Client to driver:
  {"hello": {"app": "blender", "name": "Blender", "version": "5.2.0", "pid": 1234, "protocol": 1}}
      first; the driver answers {"hello": {"protocol": 1}}
  {"commands": [{"id": "mesh.extrude_region_move", "title": "Extrude", "category": "Modeling",
                 "icon": "", "keys": "E", "modes": ["EDIT_MESH"]}, ...]}
      the catalog, at most MAX_COMMANDS entries (a longer one is ignored)
  {"context": {"mode": "EDIT_MESH", "label": "Edit Mesh", "detail": "Vertex"}}
      on every change
  {"result": {"seq": 7, "ok": true, "error": ""}}
Driver to client:
  {"run": {"id": "mesh.extrude_region_move", "seq": 7}}

Nothing received here is ever executed by the driver: a ``run`` request is only
sent to a client when the id is in that same client's own catalog, and the
application runs its own allowlisted entries. Several clients of one application
are possible; requests go to the one that spoke last.
"""

from __future__ import annotations

import collections
import json
import logging
import os
import re
import selectors
import socket
import stat
import struct
import threading
import time
from dataclasses import dataclass, field

from config.identity import SLUG, app_link_socket_path

log = logging.getLogger(SLUG)

PROTOCOL = 1
MAX_LINE = 64 * 1024            # bytes per line, the newline included
MAX_COMMANDS = 500              # catalog entries
MAX_CLIENTS = 16
MAX_OUTGOING = 256 * 1024       # unsent bytes before a client is dropped
MAX_PENDING_RUNS = 64           # requests waiting for their result per client
HELLO_TIMEOUT_S = 10.0          # a connection that never says hello is dropped
RESULT_TIMEOUT_S = 30.0         # a request without a result is forgotten
LOOP_SECONDS = 0.25

ID_PATTERN = re.compile(r"[A-Za-z0-9_.:\-]{1,128}\Z")
APP_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.\-]{0,63}\Z")

# run() outcomes
SENT = "sent"
NO_CLIENT = "no-client"
UNKNOWN_COMMAND = "unknown-command"
BUSY = "busy"


class LineTooLong(ValueError):
    """A line (or an unterminated stretch of bytes) beyond MAX_LINE."""


class LineBuffer:
    """Cuts a byte stream into JSON objects. Lines that are not UTF-8, not JSON
    or not an object are skipped; a line longer than MAX_LINE raises LineTooLong."""

    def __init__(self):
        self._data = b""
        # Leading bytes of _data already searched for an end of line.
        self._searched = 0
        self.skipped = 0

    def feed(self, chunk: bytes) -> list[dict]:
        data = self._data + chunk if self._data else chunk
        messages = []
        start = 0
        search = self._searched
        while True:
            end = data.find(b"\n", start + search)
            search = 0
            if end < 0:
                if len(data) - start >= MAX_LINE:
                    raise LineTooLong(f"no end of line in {len(data) - start} bytes")
                break
            if end - start + 1 > MAX_LINE:
                raise LineTooLong(f"line of {end - start + 1} bytes")
            line = data[start:end]
            start = end + 1
            if not line.strip():
                continue
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                self.skipped += 1
                continue
            if isinstance(message, dict):
                messages.append(message)
            else:
                self.skipped += 1
        self._data = data[start:]
        self._searched = len(self._data)
        return messages


def encode(message: dict) -> bytes:
    return json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


# --- what clients send, checked ----------------------------------------------------


def _text(value, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def clean_entry(entry) -> dict | None:
    """One catalog entry in its stored form; None when it has no usable id."""
    if not isinstance(entry, dict):
        return None
    identifier = entry.get("id")
    if not isinstance(identifier, str) or not ID_PATTERN.match(identifier):
        return None
    modes = entry.get("modes")
    return {
        "id": identifier,
        "title": _text(entry.get("title"), 100) or identifier,
        "category": _text(entry.get("category"), 60),
        "icon": _text(entry.get("icon"), 100),
        "keys": _text(entry.get("keys"), 60),
        "modes": [_text(mode, 40) for mode in modes[:20] if _text(mode, 40)] if isinstance(modes, list) else [],
    }


def clean_catalog(entries) -> list[dict] | None:
    """The catalog of a ``commands`` message; None when it is not a list or has
    more than MAX_COMMANDS entries. Unusable entries are left out, a later entry
    with the same id replaces the earlier one."""
    if not isinstance(entries, list) or len(entries) > MAX_COMMANDS:
        return None
    catalog: dict[str, dict] = {}
    for entry in entries:
        cleaned = clean_entry(entry)
        if cleaned is not None:
            catalog[cleaned["id"]] = cleaned
    return list(catalog.values())


def clean_context(values) -> dict:
    if not isinstance(values, dict):
        return {}
    context = {
        "mode": _text(values.get("mode"), 60),
        "label": _text(values.get("label"), 100),
        "detail": _text(values.get("detail"), 100),
    }
    return context if context["mode"] else {}


def clean_hello(values) -> dict | None:
    if not isinstance(values, dict):
        return None
    app = _text(values.get("app"), 64).lower()
    if not APP_PATTERN.match(app):
        return None
    pid = values.get("pid")
    protocol = values.get("protocol")
    return {
        "app": app,
        "name": _text(values.get("name"), 100) or app,
        "version": _text(values.get("version"), 40),
        "pid": pid if isinstance(pid, int) and not isinstance(pid, bool) else 0,
        "protocol": protocol if isinstance(protocol, int) and not isinstance(protocol, bool) else 0,
    }


def _tokens(value) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", str(value).lower()) if token}


def app_matches(link_app: str, window_id) -> bool:
    """Whether the focused application (a desktop id, a binary path or an
    application name) is the linked application: ``blender`` is
    ``org.blender.Blender.desktop``, ``/usr/bin/blender`` and ``Blender``."""
    app = str(link_app or "").lower()
    if not app or not window_id:
        return False
    value = str(window_id).lower()
    if value == app:
        return True
    tokens = _tokens(value)
    wanted = _tokens(app)
    return bool(wanted) and wanted <= tokens


# --- the socket directory ----------------------------------------------------------


def check_private_directory(directory) -> None:
    """Refuse a directory that is not this user's alone."""
    info = os.lstat(directory)
    if not stat.S_ISDIR(info.st_mode):
        raise PermissionError(f"{directory} is not a directory")
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError(f"{directory} is not a private directory of this user")


def claim_path(path: str) -> None:
    """Make ``path`` free for bind(): a stale socket of ours is removed, a live one
    (another driver) or anything else is refused."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise PermissionError(f"{path} exists and is not a socket of this user")
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(0.5)
        probe.connect(path)
    except (ConnectionRefusedError, FileNotFoundError):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return
    except OSError as error:
        raise RuntimeError(f"cannot tell whether {path} is in use: {error}") from None
    finally:
        probe.close()
    raise RuntimeError(f"another Turnpad driver listens on {path}")


def peer_is_this_user(connection: socket.socket) -> bool:
    """SO_PEERCRED says the peer runs as us; True where the platform cannot tell."""
    try:
        data = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    except (AttributeError, OSError):
        return True
    _pid, uid, _gid = struct.unpack("3i", data)
    return uid == os.getuid()


# --- clients and the server --------------------------------------------------------


@dataclass
class AppClient:
    connection: socket.socket
    connected_at: float
    buffer: LineBuffer = field(default_factory=LineBuffer)
    outgoing: bytearray = field(default_factory=bytearray)
    hello: dict | None = None
    catalog: dict = field(default_factory=dict)             # id -> entry
    catalog_list: list = field(default_factory=list)
    context: dict = field(default_factory=dict)
    last_active: int = 0                                    # server counter at the last message
    pending: collections.OrderedDict = field(default_factory=collections.OrderedDict)  # seq -> (id, time)

    @property
    def app(self) -> str:
        return self.hello["app"] if self.hello else ""

    def info(self) -> dict:
        hello = self.hello or {}
        return {
            "app": hello.get("app", ""), "name": hello.get("name", ""), "version": hello.get("version", ""),
            "pid": hello.get("pid", 0), "commands": len(self.catalog), "context": dict(self.context),
        }


class AppLink:
    """The server. ``on_change(kind)`` is called from the server thread (kind
    ``hello``, ``catalog``, ``context`` or ``gone``) after the state changed and
    no lock is held; ``on_result(app, command_id, ok, error)`` likewise."""

    def __init__(self, path: str | None = None, on_change=None, on_result=None, clock=time.monotonic):
        self.path = path if path is not None else app_link_socket_path()
        self._on_change = on_change
        self._on_result = on_result
        self._clock = clock
        self._lock = threading.RLock()
        self._clients: list[AppClient] = []
        self._counter = 0
        self._seq = 0
        # Last catalog of each application, kept after the application closes,
        # so the settings app can still offer its commands.
        self._last_catalogs: dict[str, dict] = {}
        self._selector: selectors.BaseSelector | None = None
        self._listener: socket.socket | None = None
        self._wake_read: socket.socket | None = None
        self._wake_write: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False

    # lifecycle -----------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Bind the socket and start serving; False (with a log line) when that is not possible."""
        if self.running:
            return True
        if not self.path:
            log.info("No XDG_RUNTIME_DIR: the app link is off")
            return False
        listener = None
        try:
            check_private_directory(os.path.dirname(self.path))
            claim_path(self.path)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(self.path)
            os.chmod(self.path, 0o600)
            listener.listen(8)
            listener.setblocking(False)
        except (OSError, RuntimeError) as error:
            if listener is not None:
                listener.close()
            log.warning("App link not started: %s", error)
            return False
        self._listener = listener
        self._wake_read, self._wake_write = socket.socketpair()
        self._wake_read.setblocking(False)
        self._wake_write.setblocking(False)
        self._selector = selectors.DefaultSelector()
        self._selector.register(listener, selectors.EVENT_READ, "listener")
        self._selector.register(self._wake_read, selectors.EVENT_READ, "wake")
        self._stopping = False
        self._thread = threading.Thread(target=self._serve, name=f"{SLUG}-app-link", daemon=True)
        self._thread.start()
        log.info("App link listening on %s", self.path)
        return True

    def stop(self) -> None:
        self._stopping = True
        self._wake()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(2.0)
        self._thread = None

    def _close_all(self) -> None:
        with self._lock:
            clients, self._clients = self._clients, []
        for client in clients:
            self._close(client.connection)
        for item in (self._listener, self._wake_read, self._wake_write):
            if item is not None:
                self._close(item)
        if self._selector is not None:
            self._selector.close()
        self._selector = self._listener = self._wake_read = self._wake_write = None
        if self.path:
            try:
                if stat.S_ISSOCK(os.lstat(self.path).st_mode):
                    os.unlink(self.path)
            except OSError:
                pass

    @staticmethod
    def _close(item) -> None:
        try:
            item.close()
        except OSError:
            pass

    def _wake(self) -> None:
        wake = self._wake_write
        if wake is not None:
            try:
                wake.send(b"x")
            except OSError:
                pass

    # reading the state (any thread) --------------------------------------------

    def clients(self) -> list[dict]:
        with self._lock:
            return [client.info() for client in self._clients if client.hello]

    def _client_for(self, window_id) -> AppClient | None:
        """The most recently active client whose application is the focused one."""
        with self._lock:
            matching = [c for c in self._clients if c.hello and app_matches(c.app, window_id)]
            return max(matching, key=lambda c: c.last_active, default=None)

    def connected(self, window_id) -> bool:
        return self._client_for(window_id) is not None

    def context_for(self, window_id) -> dict:
        client = self._client_for(window_id)
        with self._lock:
            return dict(client.context) if client is not None else {}

    def app_for(self, window_id) -> str:
        """The linked application's name for the focused window, "" when none is connected."""
        client = self._client_for(window_id)
        return client.app if client is not None else ""

    def commands(self, app: str) -> dict:
        """What GetAppCommands answers for ``app`` (a link name or a desktop id):
        {"app", "name", "version", "connected", "context", "commands": [...]}.
        Not connected: the last catalog seen, if any, with connected false."""
        with self._lock:
            matching = [c for c in self._clients if c.hello and (c.app == str(app).lower() or app_matches(c.app, app))]
            client = max(matching, key=lambda c: c.last_active, default=None)
            if client is not None:
                hello = client.hello
                return {
                    "app": hello["app"], "name": hello["name"], "version": hello["version"], "connected": True,
                    "context": dict(client.context), "commands": list(client.catalog_list),
                }
            for name, remembered in self._last_catalogs.items():
                if name == str(app).lower() or app_matches(name, app):
                    return {
                        "app": name, "name": remembered["name"], "version": remembered["version"],
                        "connected": False, "context": {}, "commands": list(remembered["commands"]),
                    }
        return {"app": str(app), "name": "", "version": "", "connected": False, "context": {}, "commands": []}

    # running commands (any thread) ---------------------------------------------

    def run(self, window_id, command_id: str) -> tuple[str, str]:
        """Ask the application of the focused window to run ``command_id``.
        Returns (outcome, app): SENT, NO_CLIENT (nobody linked), UNKNOWN_COMMAND
        (the id is not in that client's catalog; nothing is sent) or BUSY."""
        with self._lock:
            client = self._client_for(window_id)
            if client is None:
                return NO_CLIENT, ""
            if not isinstance(command_id, str) or command_id not in client.catalog:
                return UNKNOWN_COMMAND, client.app
            if len(client.outgoing) > MAX_OUTGOING // 2:
                return BUSY, client.app
            self._seq += 1
            seq = self._seq
            client.pending[seq] = (command_id, self._clock())
            while len(client.pending) > MAX_PENDING_RUNS:
                client.pending.popitem(last=False)
            client.outgoing += encode({"run": {"id": command_id, "seq": seq}})
            app = client.app
        self._wake()
        return SENT, app

    # the server thread -----------------------------------------------------------

    def _serve(self) -> None:
        try:
            while not self._stopping:
                try:
                    events = self._selector.select(LOOP_SECONDS)
                except OSError:
                    break
                for key, _mask in events:
                    if key.data == "listener":
                        self._accept()
                    elif key.data == "wake":
                        try:
                            self._wake_read.recv(4096)
                        except OSError:
                            pass
                    else:
                        self._read(key.data)
                self._flush_all()
                self._expire()
        except Exception:
            log.exception("The app link stopped")
        finally:
            self._close_all()

    def _accept(self) -> None:
        for _ in range(8):
            try:
                connection, _address = self._listener.accept()
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            with self._lock:
                crowded = len(self._clients) >= MAX_CLIENTS
            if crowded or not peer_is_this_user(connection):
                self._close(connection)
                continue
            connection.setblocking(False)
            client = AppClient(connection=connection, connected_at=self._clock())
            with self._lock:
                self._clients.append(client)
            self._selector.register(connection, selectors.EVENT_READ, client)

    def _read(self, client: AppClient) -> None:
        notes = []
        try:
            while True:
                try:
                    chunk = client.connection.recv(65536)
                except (BlockingIOError, InterruptedError):
                    break
                if not chunk:
                    self._drop(client, "closed")
                    return
                for message in client.buffer.feed(chunk):
                    notes.extend(self._handle(client, message))
                if len(chunk) < 65536:
                    break
        except LineTooLong as error:
            log.warning("App link: %s sent a line that is too long (%s), dropped", client.app or "a client", error)
            self._drop(client, "line too long")
            return
        except OSError as error:
            self._drop(client, str(error))
            return
        for note in notes:
            self._notify(*note)

    def _handle(self, client: AppClient, message: dict) -> list[tuple]:
        """Apply one message; the notifications to send once the lock is free."""
        notes: list[tuple] = []
        with self._lock:
            hello = message.get("hello")
            if client.hello is None:
                # Nothing counts before a valid hello.
                cleaned = clean_hello(hello)
                if cleaned is None:
                    return notes
                client.hello = cleaned
                client.last_active = self._next()
                client.outgoing += encode({"hello": {"protocol": PROTOCOL}})
                if cleaned["protocol"] != PROTOCOL:
                    log.info("App link: %s speaks protocol %s, this driver %s", cleaned["app"], cleaned["protocol"], PROTOCOL)
                notes.append(("hello", client.app))
                return notes
            if isinstance(hello, dict):
                return notes
            if "commands" in message:
                catalog = clean_catalog(message["commands"])
                if catalog is None:
                    log.warning("App link: %s sent an unusable catalog, ignored", client.app)
                else:
                    client.catalog = {entry["id"]: entry for entry in catalog}
                    client.catalog_list = catalog
                    self._last_catalogs[client.app] = {
                        "name": client.hello["name"], "version": client.hello["version"], "commands": list(catalog),
                    }
                    notes.append(("catalog", client.app))
                client.last_active = self._next()
            elif "context" in message:
                context = clean_context(message["context"])
                if context != client.context:
                    client.context = context
                    notes.append(("context", client.app))
                client.last_active = self._next()
            elif isinstance(message.get("result"), dict):
                result = message["result"]
                seq = result.get("seq")
                pending = client.pending.pop(seq, None) if isinstance(seq, int) and not isinstance(seq, bool) else None
                client.last_active = self._next()
                if pending is not None:
                    notes.append(("result", client.app, pending[0], result.get("ok") is True, _text(result.get("error"), 300)))
        return notes

    def _next(self) -> int:
        self._counter += 1
        return self._counter

    def _notify(self, kind: str, *details) -> None:
        try:
            if kind == "result":
                if self._on_result is not None:
                    self._on_result(*details)
            elif self._on_change is not None:
                self._on_change(kind)
        except Exception:
            log.exception("App link callback failed")

    def _flush_all(self) -> None:
        with self._lock:
            clients = [client for client in self._clients if client.outgoing]
        for client in clients:
            try:
                with self._lock:
                    sent = client.connection.send(bytes(client.outgoing[:65536]))
                    del client.outgoing[:sent]
                    too_much = len(client.outgoing) > MAX_OUTGOING
            except (BlockingIOError, InterruptedError):
                with self._lock:
                    too_much = len(client.outgoing) > MAX_OUTGOING
            except OSError as error:
                self._drop(client, str(error))
                continue
            if too_much:
                log.warning("App link: %s does not read, dropped", client.app or "a client")
                self._drop(client, "does not read")

    def _expire(self) -> None:
        now = self._clock()
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            if client.hello is None and now - client.connected_at > HELLO_TIMEOUT_S:
                self._drop(client, "no hello")
                continue
            with self._lock:
                for seq in [seq for seq, (_id, sent) in client.pending.items() if now - sent > RESULT_TIMEOUT_S]:
                    del client.pending[seq]

    def _drop(self, client: AppClient, reason: str) -> None:
        with self._lock:
            if client not in self._clients:
                return
            self._clients.remove(client)
            known = client.hello is not None
        try:
            self._selector.unregister(client.connection)
        except (KeyError, ValueError, OSError):
            pass
        self._close(client.connection)
        log.debug("App link: %s left (%s)", client.app or "a client", reason)
        if known:
            self._notify("gone")
