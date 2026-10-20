"""Turnpad app link, the Blender side: newline-delimited JSON over a Unix
socket (see docs of the driver's app_link.py). Pure Python, no bpy."""

import json
import os

PROTOCOL = 1
SOCKET_NAME = "asus-turnpad-apps.sock"
MAX_LINE = 64 * 1024        # bytes per line, the newline included
MAX_COMMANDS = 500          # catalog size the driver accepts
MAX_ERROR = 300             # characters of an error text sent back


class LineTooLong(ValueError):
    """A line (or an unterminated stretch of bytes) beyond MAX_LINE."""


def default_socket_path(environ=None):
    """$XDG_RUNTIME_DIR/asus-turnpad-apps.sock, None without a runtime dir."""
    environ = os.environ if environ is None else environ
    runtime = environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        return None
    return os.path.join(runtime, SOCKET_NAME)


def encode(message):
    """One message as a UTF-8 line."""
    line = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(line) > MAX_LINE:
        raise LineTooLong("message of %d bytes" % len(line))
    return line


def hello_message(app, name, version, pid):
    return {"hello": {"app": app, "name": name, "version": version,
                      "pid": int(pid), "protocol": PROTOCOL}}


def commands_message(entries):
    """The catalog as the driver wants it: `entries` are wire dicts."""
    entries = list(entries)
    if len(entries) > MAX_COMMANDS:
        raise ValueError("catalog of %d entries" % len(entries))
    return {"commands": entries}


def context_message(mode, label, detail):
    return {"context": {"mode": mode, "label": label, "detail": detail}}


def result_message(seq, ok, error=""):
    return {"result": {"seq": seq, "ok": bool(ok), "error": str(error or "")[:MAX_ERROR]}}


def parse_run(message):
    """(id, seq) of a driver `run` request, None for anything else or a
    malformed one."""
    run = message.get("run") if isinstance(message, dict) else None
    if not isinstance(run, dict):
        return None
    command, seq = run.get("id"), run.get("seq")
    if not isinstance(command, str) or not command:
        return None
    if isinstance(seq, bool) or not isinstance(seq, int):
        return None
    return command, seq


class LineBuffer:
    """Cuts a byte stream into JSON objects. Lines that are not UTF-8, not
    JSON or not an object are skipped (`skipped` counts them); a line longer
    than MAX_LINE raises LineTooLong, and the connection should be dropped."""

    def __init__(self):
        self._data = b""
        self.skipped = 0

    def feed(self, chunk):
        self._data += chunk
        messages = []
        while True:
            end = self._data.find(b"\n")
            if end < 0:
                if len(self._data) >= MAX_LINE:
                    raise LineTooLong("no end of line in %d bytes" % len(self._data))
                break
            if end + 1 > MAX_LINE:
                raise LineTooLong("line of %d bytes" % (end + 1))
            line, self._data = self._data[:end], self._data[end + 1:]
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
        return messages

    def clear(self):
        self._data = b""
