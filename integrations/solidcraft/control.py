"""SolidCraft's control channel (docs/control-protocol.md in SolidCraft's
repository): ``solidcraft --control PORT`` (or SOLIDCRAFT_CONTROL_PORT=PORT)
answers JSON lines on 127.0.0.1:PORT. The first request of a connection
carries the per-launch token SolidCraft writes, readable by the user alone, to
``<data folder>/solidcraft/control/PORT.token``; the file is removed when it
quits. Standard library only."""

from __future__ import annotations

import json
import os
import re
import socket
import stat
from dataclasses import dataclass

from integrations.bridge_base import BridgeError, BridgeGone

HOST = "127.0.0.1"
FLATPAK_ID = "ai.storyteller.solidcraft"
TOKEN_FILE = re.compile(r"([0-9]{1,5})\.token\Z")
TOKEN = re.compile(r"[0-9A-Fa-f]{32,4096}\Z")   # 256 bits in hex; SOLIDCRAFT_CONTROL_TOKEN takes 32+
MAX_TOKEN_BYTES = 4096
MAX_REPLY = 16 * 1024 * 1024                    # SolidCraft's own line limit
CALL_TIMEOUT_S = 3.0
LISTEN = "0A"                                   # TCP_LISTEN in /proc/net/tcp


class ControlError(BridgeError):
    """SolidCraft refused or failed a request (its own error text)."""


class ConnectionLost(ControlError, BridgeGone):
    """The connection is gone or in an unknown state; it has been closed."""


@dataclass(frozen=True)
class Instance:
    """A running SolidCraft, as its token file tells: newest first."""
    port: int
    token_path: str
    mtime: float


def token_dirs(environ=None) -> list[str]:
    """Where SolidCraft writes its token files: SOLIDCRAFT_CONTROL_DIR, the
    data folder of a native build, of the Flatpak and of the Snap."""
    environ = os.environ if environ is None else environ
    home = environ.get("HOME") or os.path.expanduser("~")
    data = environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    dirs = [
        environ.get("SOLIDCRAFT_CONTROL_DIR") or "",
        os.path.join(data, "solidcraft", "control"),
        os.path.join(home, ".var", "app", FLATPAK_ID, "data", "solidcraft", "control"),
        os.path.join(home, "snap", "solidcraft", "current", ".local", "share", "solidcraft", "control"),
    ]
    unique = []
    for directory in dirs:
        if directory and directory not in unique:
            unique.append(directory)
    return unique


def _private(info, uid: int) -> bool:
    """A regular file of this user that nobody else may read or write."""
    return stat.S_ISREG(info.st_mode) and info.st_uid == uid and not info.st_mode & 0o077


def find_instances(dirs, uid: int | None = None) -> list[Instance]:
    """Token files of running SolidCraft windows, newest first. Files that are
    links, another user's or readable by others are not SolidCraft's: skipped."""
    uid = os.getuid() if uid is None else uid
    found = []
    for directory in dirs:
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            match = TOKEN_FILE.match(entry.name)
            if not match or not 0 < int(match.group(1)) < 65536:
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if _private(info, uid):
                found.append(Instance(int(match.group(1)), entry.path, info.st_mtime))
    found.sort(key=lambda instance: instance.mtime, reverse=True)
    return found


def read_token(path: str, uid: int | None = None) -> str:
    """The token in ``path``, checked again on the open file."""
    uid = os.getuid() if uid is None else uid
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError as error:
        raise ControlError(f"cannot read the token file: {error.strerror or error}") from None
    try:
        if not _private(os.fstat(descriptor), uid):
            raise ControlError("the token file is not private to this user")
        data = os.read(descriptor, MAX_TOKEN_BYTES + 1)
    finally:
        os.close(descriptor)
    token = data.decode("ascii", "replace").strip()
    if len(data) > MAX_TOKEN_BYTES or not TOKEN.match(token):
        raise ControlError("the token file holds no token")
    return token


def port_owner(port: int, tables=("/proc/net/tcp", "/proc/net/tcp6")) -> int | None:
    """The uid of the socket listening on localhost ``port``, None when the
    kernel does not tell (no /proc, or nothing listens)."""
    wanted = f":{port:04X}"
    for table in tables:
        try:
            with open(table, encoding="ascii") as rows:
                next(rows, None)
                for row in rows:
                    fields = row.split()
                    if len(fields) > 7 and fields[3] == LISTEN and fields[1].endswith(wanted):
                        return int(fields[7])
        except (OSError, ValueError):
            continue
    return None


class ControlClient:
    """One authenticated connection; ``call()`` blocks for at most ``timeout``
    seconds. A reply that does not come in time leaves the connection in an
    unknown state, so it is closed (ConnectionLost) rather than reused."""

    def __init__(self, port: int, token: str, timeout: float = CALL_TIMEOUT_S, connect=socket.create_connection):
        self.port = port
        self._token = token
        self._timeout = timeout
        self._connect = connect
        self._sock = None
        self._buffer = b""
        self._next_id = 0
        self._authenticated = False

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def open(self) -> None:
        try:
            self._sock = self._connect((HOST, self.port), self._timeout)
            self._sock.settimeout(self._timeout)
        except OSError as error:
            self._sock = None
            raise ConnectionLost(f"cannot connect to port {self.port}: {error.strerror or error}") from None
        self._buffer = b""
        self._authenticated = False

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._buffer = b""

    def _lost(self, reason: str) -> ConnectionLost:
        self.close()
        return ConnectionLost(reason)

    def call(self, method: str, params: dict | None = None):
        """The result of one request; ControlError with SolidCraft's reason when it fails."""
        if self._sock is None:
            raise ConnectionLost("not connected")
        self._next_id += 1
        request = {"id": self._next_id, "method": method, "params": params or {}}
        if not self._authenticated:
            request["token"] = self._token
        try:
            self._sock.sendall(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
            reply = self._reply(self._next_id)
        except socket.timeout:
            raise self._lost(f"{method}: no reply within {self._timeout:g} s") from None
        except OSError as error:
            raise self._lost(f"{method}: {error.strerror or error}") from None
        if reply.get("ok") is True:
            self._authenticated = True
            return reply.get("result")
        error = str(reply.get("error") or "failed")
        if error.startswith("unauthorized") or not self._authenticated:
            # SolidCraft closes a connection whose first line is refused.
            raise self._lost(error)
        raise ControlError(error)

    def _reply(self, request_id: int) -> dict:
        while True:
            end = self._buffer.find(b"\n")
            while end >= 0:
                line, self._buffer = self._buffer[:end], self._buffer[end + 1:]
                try:
                    reply = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    reply = None
                # Replies to other ids (none are pending: a late one closes the
                # connection first) are not this request's.
                if isinstance(reply, dict) and reply.get("id") == request_id:
                    return reply
                end = self._buffer.find(b"\n")
            if len(self._buffer) > MAX_REPLY:
                raise self._lost("reply too long")
            chunk = self._sock.recv(65536)
            if not chunk:
                raise self._lost("SolidCraft closed the connection")
            self._buffer += chunk
