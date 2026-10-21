"""A stand-in for SolidCraft's control channel (docs/control-protocol.md in
SolidCraft's repository) for the bridge tests: JSON lines on 127.0.0.1, a
per-launch token in a private token file, the first request of a connection
must carry it. Only the methods the bridge uses, with the shapes SolidCraft
41a62d2 answers (crates/ui-egui/src/control.rs)."""

import json
import math
import os
import secrets
import socket
import threading

REGISTRY = [
    {"id": "edit.undo", "label": "Undo", "tab": "", "panel": "", "icon": "undo", "shortcut": "Ctrl+Z",
     "params": "", "enabled": True, "disabled_reason": None},
    {"id": "solid.extrude", "label": "Extrude", "tab": "SOLID", "panel": "CREATE", "icon": "extrude",
     "shortcut": "E", "params": "distance…", "enabled": True, "disabled_reason": None},
    {"id": "solid.fillet", "label": "Fillet", "tab": "SOLID", "panel": "MODIFY", "icon": "fillet",
     "shortcut": "F", "params": "edges, radius", "enabled": True, "disabled_reason": None},
    {"id": "sketch.line", "label": "Line", "tab": "SKETCH", "panel": "CREATE", "icon": "line",
     "shortcut": "L", "params": "", "enabled": False, "disabled_reason": "no sketch is being edited"},
    {"id": "sheet.flange", "label": "Flange", "tab": "SHEET METAL", "panel": "CREATE", "icon": "",
     "shortcut": None, "params": "", "enabled": True, "disabled_reason": None},
    {"id": "document.inspect", "label": "Inspect", "tab": "", "panel": "", "icon": "", "shortcut": None,
     "params": "", "enabled": True, "disabled_reason": None},
    {"id": "solid.knurl", "label": "Knurl", "tab": "SOLID", "panel": "CREATE", "icon": "", "shortcut": None,
     "params": "", "enabled": True, "disabled_reason": None, "planned": "#9999"},
]


class FakeSolidCraft:
    def __init__(self, token_dir, version="0.18.1"):
        self.token = secrets.token_hex(32)
        self.version = version
        self.camera = {"target": [0.0, 0.0, 0.0], "yaw": 0.5, "pitch": 0.6, "roll": 0.0,
                       "distance": 200.0, "fov": 0.0, "up": "Z"}
        self.ui = {"tab": "MODEL"}
        self.tool = None
        self.dialog = None
        self.viewport = [0.0, 80.0, 1200.0, 800.0]
        self.requests = []          # (method, params) in order, authenticated ones only
        self.delay = threading.Event()   # set: requests are not answered (a busy UI thread)
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(16)
        self.port = self._listener.getsockname()[1]
        os.makedirs(token_dir, mode=0o700, exist_ok=True)
        self.token_path = os.path.join(token_dir, f"{self.port}.token")
        descriptor = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as token_file:
            token_file.write(self.token + "\n")
        self._connections = []
        self._stopping = False
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def methods(self):
        return [method for method, _params in self.requests]

    def stop(self):
        """SolidCraft quits: the token file goes, the port closes."""
        self._stopping = True
        try:
            os.unlink(self.token_path)
        except OSError:
            pass
        try:
            self._listener.shutdown(socket.SHUT_RDWR)    # wakes the accept()
        except OSError:
            pass
        self._listener.close()
        for connection in list(self._connections):
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    def _accept(self):
        while not self._stopping:
            try:
                connection, _address = self._listener.accept()
            except OSError:
                return
            self._connections.append(connection)
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, connection):
        authenticated = False
        buffer = b""
        try:
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    return
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    request = json.loads(line)
                    if not authenticated:
                        if request.get("token") != self.token:
                            self._send(connection, {"id": request.get("id"), "ok": False,
                                                    "error": "unauthorized: wrong or missing token"})
                            connection.close()
                            return
                        authenticated = True
                    if self.delay.is_set():
                        continue
                    self.requests.append((request["method"], request.get("params") or {}))
                    try:
                        result = self._handle(request["method"], request.get("params") or {})
                        reply = {"id": request["id"], "ok": True, "result": result}
                    except ValueError as error:
                        reply = {"id": request["id"], "ok": False, "error": str(error)}
                    self._send(connection, reply)
        except OSError:
            return

    @staticmethod
    def _send(connection, reply):
        connection.sendall(json.dumps(reply).encode() + b"\n")

    def _handle(self, method, params):
        if method == "ui.help":
            return {"diagnostics": "", "version": self.version, "commit": "41a62d2", "built": "2026-10-20"}
        if method == "engine.commands":
            return REGISTRY
        if method == "ui.inspect":
            return {"ui": dict(self.ui), "camera": dict(self.camera), "viewport": self.viewport,
                    "tool": self.tool, "dialog": self.dialog, "renderer": "gpu"}
        if method == "ui.camera":
            for key in ("yaw", "pitch", "roll", "distance"):
                if key in params:
                    value = float(params[key])
                    if key == "pitch" and abs(value) > math.pi / 2:
                        raise ValueError("the camera must have a pitch within ±π/2")
                    self.camera[key] = value
            return dict(self.camera)
        if method == "ui.view":
            if params.get("view") not in ("fit", "home", "front", "back", "top", "bottom", "left", "right", "iso"):
                raise ValueError(f"unknown view `{params.get('view')}`")
            return {"animating": True}
        if method == "ui.palette":
            return {"open": True, "text": ""}
        if method == "ui.start":
            command = params.get("command")
            if command == "sketch.line":
                self.tool = command
            return {"tool": self.tool, "dialog": self.dialog}
        if method == "engine.execute":
            if params.get("command") != "view.navigate":
                raise ValueError("not in the fake")
            navigate = params.get("params") or {}
            if navigate.get("motion") != "pan" or "viewport" not in navigate:
                raise ValueError("view.navigate: viewport is required for pan")
            return {"camera": navigate["camera"], "fit": False, "continuous": True}
        raise ValueError(f"unknown method `{method}`")
