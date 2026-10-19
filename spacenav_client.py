"""A client of spacenavd (the 3Dconnexion SpaceMouse daemon), standard library only.

spacenavd talks over a Unix socket (libspnav's protocol, verified against
spacenavd 1.3.1). Everything here is plain Python so the driver needs no
libspnav binding: the pure parts (``StreamParser``, ``pack_request``,
``StringAssembler``, ``socket_candidates``) are unit tested, ``SpacenavClient``
is the socket and is tested against a fake daemon.

Wire format. Events are 8 native int32: the type, then 7 values.
  0 motion (x y z rx ry rz period), 1 press (bnum), 2 release (bnum),
  3 dev, 4 cfg, 5 RAWAXIS (axis index, value), 6 RAWBUTTON (bnum, press).
Protocol v1 (handshake: write 0x7faa5501, the reply is 0x7faa5500 | version
within 300 ms, else the daemon is old and speaks v0) adds requests: an int32
type (0x7faa0000 | REQ_*) and 7 int32 of data, 32 bytes like an event; the
response echoes the type and carries the status in data[6]. Events and
responses share the socket: the first int32 tells them apart (high half 0x7faa
= response). Strings travel in 24 byte chunks.

spacenavd broadcasts RAWAXIS and RAWBUTTON before its dead zone, sensitivity,
button mapping and key emulation, to every client that asked for them with
REQ_SET_EVMASK, so they are the same whatever the daemon's configuration is.
"""

import json
import os
import select
import socket
import struct
import time
from collections import namedtuple

UEV_MOTION, UEV_PRESS, UEV_RELEASE, UEV_DEV, UEV_CFG, UEV_RAWAXIS, UEV_RAWBUTTON = range(7)

EVMASK_MOTION = 0x01
EVMASK_BUTTON = 0x02
EVMASK_DEV = 0x04
EVMASK_CFG = 0x08
EVMASK_RAWAXIS = 0x10
EVMASK_RAWBUTTON = 0x20

REQ_TAG = 0x7FAA0000
REQ_SET_NAME = 0x1000
REQ_SET_EVMASK = 0x1003
REQ_GET_EVMASK = 0x1004
REQ_DEV_NAME = 0x2000
REQ_DEV_NAXES = 0x2002
REQ_DEV_NBUTTONS = 0x2003
REQ_SCFG_SENS = 0x3000
REQ_GCFG_SENS = 0x3001
REQ_SCFG_BNMAP = 0x300A
REQ_GCFG_BNMAP = 0x300B
REQ_CHANGE_PROTO = 0x5500

MAX_PROTO_VER = 1
HANDSHAKE_TIMEOUT = 0.3
HANDSHAKE_RETRY_TIMEOUT = 1.0
REQUEST_TIMEOUT = 0.4
CHUNK_SIZE = 24
CONT_BIT = 0x10000

# An event and a request/response are both 8 int32.
MESSAGE = struct.Struct("=8i")
MESSAGE_SIZE = MESSAGE.size

# type: the UEV_* number; data: the 7 values after it.
SpnavEvent = namedtuple("SpnavEvent", "type data")
# type: the request echoed (with the tag); data: 7 values, data[6] is the status.
Response = namedtuple("Response", "type data")

DEFAULT_SOCKET = "/var/run/spnav.sock"


def float_to_bits(value):
    return struct.unpack("=i", struct.pack("=f", value))[0]


def bits_to_float(bits):
    return struct.unpack("=f", struct.pack("=i", bits))[0]


def spnavrc_socket(text):
    """The ``socket = <path>`` of /etc/spnavrc (libspnav's hacky parser), or None."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("socket") and "=" in line:
            path = line.split("=", 1)[1].strip()
            if path:
                return path
    return None


def socket_candidates(configured="", environ=None, spnavrc_text=""):
    """Socket paths in libspnav's order: the config's own, $SPNAV_SOCKET,
    ``socket =`` in /etc/spnavrc, $XDG_RUNTIME_DIR/spnav.sock, /var/run/spnav.sock."""
    environ = os.environ if environ is None else environ
    candidates = []
    if configured:
        candidates.append(configured)
    if environ.get("SPNAV_SOCKET"):
        candidates.append(environ["SPNAV_SOCKET"])
    path = spnavrc_socket(spnavrc_text)
    if path:
        candidates.append(path)
    if environ.get("XDG_RUNTIME_DIR"):
        candidates.append(environ["XDG_RUNTIME_DIR"] + "/spnav.sock")
    candidates.append(DEFAULT_SOCKET)
    return list(dict.fromkeys(candidates))


def pack_request(request, data=()):
    """A v1 request: the tag, the request and up to 7 data values."""
    values = [int(value) for value in data][:7]
    values += [0] * (7 - len(values))
    return MESSAGE.pack(REQ_TAG | request, *values)


def pack_string_request(request, text):
    """A string request (SET_NAME, ...): 24 byte chunks, the last data value is
    the remaining length, with CONT_BIT on the continuation chunks."""
    raw = text.encode("utf-8")
    remaining = len(raw)
    chunks = []
    first = True
    offset = 0
    while True:
        chunk = raw[offset:offset + CHUNK_SIZE].ljust(CHUNK_SIZE, b"\0")
        flag = remaining | (0 if first else CONT_BIT)
        chunks.append(struct.pack("=i", REQ_TAG | request) + chunk + struct.pack("=i", flag))
        assert len(chunks[-1]) == MESSAGE_SIZE
        offset += CHUNK_SIZE
        remaining -= CHUNK_SIZE
        first = False
        if remaining <= 0:
            return b"".join(chunks)


class StringAssembler:
    """The chunks of a string response (spnav_recv_str)."""

    def __init__(self):
        self.parts = []
        self.expect = None

    def feed(self, response):
        """The string when complete, None when more chunks follow; ValueError on a failure."""
        status = response.data[6]
        if status < 0:
            raise ValueError("the daemon refused")
        length = status & 0xFFFF
        if not status & CONT_BIT:
            self.parts, self.expect = [], length
        elif self.expect is None or length != self.expect:
            raise ValueError("unexpected string chunk")
        raw = struct.pack("=6i", *response.data[:6])
        take = min(length, CHUNK_SIZE)
        self.parts.append(raw[:take])
        self.expect -= take
        if self.expect <= 0:
            return b"".join(self.parts).decode("utf-8", "replace")
        return None


class StreamParser:
    """Splits the byte stream into events and responses.

    Both are 32 bytes; the first int32 tells them apart: a response's type has
    0x7faa in the high half. ``feed()`` returns what is complete; the rest
    waits for more bytes. Messages of an unknown event type are dropped.
    """

    def __init__(self):
        self.buffer = b""

    def feed(self, data):
        self.buffer += data
        items = []
        while len(self.buffer) >= MESSAGE_SIZE:
            values = MESSAGE.unpack_from(self.buffer)
            self.buffer = self.buffer[MESSAGE_SIZE:]
            if (values[0] >> 16) & 0xFFFF == 0x7FAA:
                items.append(Response(values[0], tuple(values[1:])))
            elif 0 <= values[0] <= UEV_RAWBUTTON:
                items.append(SpnavEvent(values[0], tuple(values[1:])))
        return items


def pack_event(kind, *values):
    """An event as the daemon writes it (for tests and fakes)."""
    values = list(values)[:7]
    return MESSAGE.pack(kind, *(values + [0] * (7 - len(values))))


def pack_response(request, data=(), status=0):
    values = list(data)[:6]
    values += [0] * (6 - len(values))
    return MESSAGE.pack(REQ_TAG | request, *values, status)


class SpacenavClient:
    """One connection to spacenavd; all calls come from one thread (the driver's input loop)."""

    def __init__(self):
        self.sock = None
        self.proto = 0
        self.parser = StreamParser()
        self.queue = []
        self.responses = []

    @property
    def connected(self):
        return self.sock is not None

    def fileno(self):
        return self.sock.fileno() if self.sock is not None else None

    def connect(self, candidates):
        """Connect to the first reachable socket and negotiate the protocol; False if none."""
        for path in candidates:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.connect(path)
            except OSError:
                sock.close()
                continue
            self.sock = sock
            self.parser = StreamParser()
            self.queue = []
            self.responses = []
            try:
                self.proto = self._handshake(HANDSHAKE_TIMEOUT)
                if self.proto == 0:
                    # A slow v1 daemon's late reply would sit in the stream as a
                    # stray int32 and break its framing: ask again on a fresh
                    # connection and wait longer before believing in v0.
                    self.sock.close()
                    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self.sock.connect(path)
                    self.parser = StreamParser()
                    self.proto = self._handshake(HANDSHAKE_RETRY_TIMEOUT, old_daemon=True)
            except OSError:
                self.close()
                continue
            self.sock.setblocking(False)
            return True
        return False

    def _handshake(self, timeout, old_daemon=False):
        self.sock.sendall(struct.pack("=i", REQ_TAG | REQ_CHANGE_PROTO | MAX_PROTO_VER))
        deadline = time.monotonic() + timeout
        buffer = b""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select([self.sock], [], [], remaining)
            if not ready:
                break
            data = self.sock.recv(4096)
            if not data:
                raise OSError("spacenavd closed the connection")
            buffer += data
            while len(buffer) >= 4:
                first = struct.unpack_from("=i", buffer)[0]
                if first & ~0xFF == REQ_TAG | REQ_CHANGE_PROTO:
                    # What follows (events) belongs to the stream.
                    self.parser.buffer = buffer[4:]
                    return first & 0xFF
                if len(buffer) < MESSAGE_SIZE:
                    break
                buffer = buffer[MESSAGE_SIZE:]  # an event before the answer: not for us
        if old_daemon:
            # An old daemon took our bytes for a sensitivity: put it back to 1.0.
            self.sock.sendall(struct.pack("=f", 1.0))
        return 0

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self.proto = 0

    def _receive(self, timeout=0.0):
        """Read what is there into the parser; the new items. OSError when the daemon is gone."""
        if timeout:
            ready, _, _ = select.select([self.sock], [], [], timeout)
            if not ready:
                return []
        items = []
        while True:
            try:
                data = self.sock.recv(65536)
            except BlockingIOError:
                break
            if not data:
                raise ConnectionError("spacenavd closed the connection")
            items.extend(self.parser.feed(data))
            if len(data) < 65536:
                break
        return items

    def read_events(self):
        """The events that arrived (non-blocking), including those seen during a request."""
        events, self.queue = self.queue, []
        items = self.parser.feed(b"") + self._receive()  # the first from bytes kept by the handshake
        events.extend(item for item in items if isinstance(item, SpnavEvent))
        return events

    def request(self, request, data=(), timeout=REQUEST_TIMEOUT):
        """Send a request and wait for its response (events meanwhile are kept for
        ``read_events``); None on a timeout or a refusal (negative status)."""
        if self.sock is None or self.proto < 1:
            return None
        self.responses = []  # stale ones of an earlier request
        self.sock.sendall(pack_request(request, data))
        return self._await(REQ_TAG | request, timeout)

    def _await(self, tag, timeout):
        """The next response with ``tag``; the events that arrive meanwhile are queued."""
        deadline = time.monotonic() + timeout
        while True:
            for position, item in enumerate(self.responses):
                if item.type == tag:
                    del self.responses[position]
                    return item if item.data[6] >= 0 else None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            for item in self._receive(remaining):
                (self.queue if isinstance(item, SpnavEvent) else self.responses).append(item)

    def request_string(self, request, timeout=REQUEST_TIMEOUT):
        response = self.request(request, timeout=timeout)
        if response is None:
            return None
        assembler = StringAssembler()
        while True:
            try:
                text = assembler.feed(response)
            except ValueError:
                return None
            if text is not None:
                return text
            response = self._await(REQ_TAG | request, timeout)
            if response is None:
                return None

    def send_name(self, name):
        if self.sock is not None and self.proto >= 1:
            self.sock.sendall(pack_string_request(REQ_SET_NAME, name))

    def set_evmask(self, mask):
        return self.request(REQ_SET_EVMASK, [mask]) is not None

    def device_name(self):
        return self.request_string(REQ_DEV_NAME)

    def get_sensitivity(self):
        response = self.request(REQ_GCFG_SENS)
        return bits_to_float(response.data[0]) if response is not None else None

    def set_sensitivity(self, value):
        return self.request(REQ_SCFG_SENS, [float_to_bits(value)]) is not None

    def get_button_map(self, button):
        response = self.request(REQ_GCFG_BNMAP, [button])
        return response.data[1] if response is not None else None

    def set_button_map(self, button, mapped):
        return self.request(REQ_SCFG_BNMAP, [button, mapped]) is not None


class RestoreFile:
    """What a run changed in spacenavd (its sensitivity while other programs
    are muted, remapped buttons), kept in a small JSON file so the next start
    can undo it after a crash."""

    def __init__(self, path):
        self.path = path

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def save(self, data):
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            temporary = self.path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            os.replace(temporary, self.path)
        except OSError:
            pass

    def clear(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass
