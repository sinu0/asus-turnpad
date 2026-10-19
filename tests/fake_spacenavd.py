"""A fake spacenavd on a Unix socket, for the SpaceMouse client and loop tests.

It speaks the v1 protocol of spacenavd 1.3.1 for the requests Turnpad uses
(see spacenav_client.py), records them, and sends the events a test queues.
"""

import os
import socket
import struct
import threading
import time

import spacenav_client as proto


class FakeSpacenavd:
    def __init__(self, directory, version=1, device_name="3Dconnexion  SpacePilot PRO ", first_reply_delay=0.0):
        self.path = os.path.join(directory, "spnav.sock")
        self.first_reply_delay = first_reply_delay  # seconds the first connection waits before answering
        self.accepted = []
        self.version = version  # None: an old daemon that never answers the handshake
        self.device_name = device_name
        self.sensitivity = 1.0
        self.evmask = 0x03
        self.button_map = {}
        self.client_name = ""
        # Called with (button, mapped) before a button mapping is applied; and the
        # mapped values the daemon refuses.
        self.on_set_bnmap = None
        self.refuse_bnmap_values = set()
        self.requests = []
        self.floats = []
        self.connections = 0
        self.lock = threading.Lock()
        self.client = None
        self.stopped = False
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.path)
        self.server.listen(4)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    # --- test side ------------------------------------------------------------------

    def send(self, data):
        """Bytes to the client (events, see spacenav_client.pack_event)."""
        deadline = time.monotonic() + 2
        while self.client is None and time.monotonic() < deadline:
            time.sleep(0.005)
        with self.lock:
            self.client.sendall(data)

    def drop_client(self):
        with self.lock:
            if self.client is not None:
                try:
                    self.client.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.client.close()
                self.client = None

    def wait_for(self, condition, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.005)
        return condition()

    def stop(self):
        self.stopped = True
        self.drop_client()
        for client in self.accepted:
            try:
                client.close()
            except OSError:
                pass
        try:
            self.server.close()
        except OSError:
            pass

    # --- daemon side -------------------------------------------------------------------

    def serve(self):
        while not self.stopped:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            self.connections += 1
            with self.lock:
                self.client = client
                self.accepted.append(client)
            threading.Thread(target=self.handle, args=(client,), daemon=True).start()

    def read_exact(self, client, size):
        data = b""
        while len(data) < size:
            chunk = client.recv(size - len(data))
            if not chunk:
                raise OSError("closed")
            data += chunk
        return data

    def handle(self, client):
        try:
            first = self.read_exact(client, 4)
            value = struct.unpack("=i", first)[0]
            if self.version is None:
                # An old daemon takes the four bytes for a sensitivity.
                self.floats.append(struct.unpack("=f", first)[0])
                while True:
                    self.floats.append(struct.unpack("=f", self.read_exact(client, 4))[0])
            reply = proto.REQ_TAG | proto.REQ_CHANGE_PROTO | min(value & 0xFF, self.version)
            if self.connections == 1 and self.first_reply_delay:
                time.sleep(self.first_reply_delay)
            with self.lock:
                client.sendall(struct.pack("=i", reply))
            strings = {}
            while True:
                message = proto.MESSAGE.unpack(self.read_exact(client, proto.MESSAGE_SIZE))
                self.request(client, message[0] & 0xFFFF, list(message[1:]), strings)
        except OSError:
            pass

    def respond(self, client, request, data=(), status=0):
        with self.lock:
            client.sendall(proto.pack_response(request, data, status))

    def request(self, client, request, data, strings):
        self.requests.append((request, data))
        if request == proto.REQ_SET_NAME:
            remaining = data[6] & 0xFFFF
            strings.setdefault("name", b"")
            strings["name"] += struct.pack("=6i", *data[:6])[:min(remaining, proto.CHUNK_SIZE)]
            self.client_name = strings["name"].decode()
            return  # no response
        if request == proto.REQ_SET_EVMASK:
            self.evmask = data[0]
            self.respond(client, request)
        elif request == proto.REQ_GET_EVMASK:
            self.respond(client, request, [self.evmask])
        elif request == proto.REQ_GCFG_SENS:
            self.respond(client, request, [proto.float_to_bits(self.sensitivity)])
        elif request == proto.REQ_SCFG_SENS:
            self.sensitivity = proto.bits_to_float(data[0])
            self.respond(client, request)
        elif request == proto.REQ_GCFG_BNMAP:
            self.respond(client, request, [data[0], self.button_map.get(data[0], data[0])])
        elif request == proto.REQ_SCFG_BNMAP:
            if self.on_set_bnmap is not None:
                self.on_set_bnmap(data[0], data[1])
            if data[1] in self.refuse_bnmap_values:
                self.respond(client, request, status=-1)
                return
            self.button_map[data[0]] = data[1]
            self.respond(client, request)
        elif request == proto.REQ_DEV_NAME:
            raw = self.device_name.encode()
            if not raw:
                self.respond(client, request, status=-1)
                return
            remaining, first = len(raw), True
            for offset in range(0, len(raw), proto.CHUNK_SIZE):
                chunk = raw[offset:offset + proto.CHUNK_SIZE].ljust(proto.CHUNK_SIZE, b"\0")
                flag = remaining | (0 if first else proto.CONT_BIT)
                with self.lock:
                    client.sendall(struct.pack("=i", proto.REQ_TAG | request) + chunk + struct.pack("=i", flag))
                remaining -= proto.CHUNK_SIZE
                first = False
        else:
            self.respond(client, request, status=-1)
