---
name: driver-edit
description: Change dialpad.py (the ~5000-line monolithic Turnpad driver) safely — CRLF line endings, which thread may do what, the locks, the state functions the tests extract, and the D-Bus contract that must not change. Use before editing dialpad.py or dialpad_dbus.py, and when a driver change breaks tests/test_driver_*.py.
---

# Editing the driver

## Line endings

`dialpad.py` is stored with CRLF in git. Every line you add must end in `\r\n`. Mixed endings break nothing at run time, but they show up as diff noise and the checks flag them. `git diff --check` reports every CR of this file as trailing whitespace, so do not use it on this file (`.claude/skills/turnpad-checks/checks.py` leaves it out and checks CRLF instead).

A safe way to edit is a Python replacement that keeps CRLF:

```bash
python3 - <<'EOF'
from pathlib import Path
p = Path("dialpad.py"); t = p.read_bytes().decode()
old = """def stop_app_link():\n""".replace("\n", "\r\n")
new = """def stop_app_link():\n    # …\n""".replace("\n", "\r\n")
assert t.count(old) == 1
p.write_bytes(t.replace(old, new).encode())
EOF
```

After any edit, normalise and check:

```bash
python3 -c "import re,pathlib; p=pathlib.Path('dialpad.py'); b=p.read_bytes(); p.write_bytes(re.sub(rb'\r?\n', b'\r\n', b))"
python3 -c "import ast; ast.parse(open('dialpad.py').read())"
```

To read it without the CRs: `sed -n 500,560p dialpad.py | tr -d '\r'`.

## Threads

| Thread | May | Never |
|---|---|---|
| touch loop (`listen_touchpad_events`, frame per `SYN_REPORT`) and the input loops (mouse, SpaceMouse) | change gesture state under `driver_state_lock`, queue work with `enqueue_action()` | subprocesses, D-Bus calls, file writes, sleeping |
| `dialpad-actions` worker | run macros, shell steps (`launch.py`), value queries, `apply_backlight()`, `reset_udev_device()` after a reload, app commands (`run_app_command`) | touching slots or the tracker |
| D-Bus service (GLib loop) | queue requests (`on_dbus_control`, reloads via `read_gui_config()`), emit signals | changing gesture state directly |
| app link and bridges (`app_link.py`, `integrations/bridges.py`) | their own sockets, `on_app_link_change` sets `pending_context_refresh` | executing anything received |

- Configuration reloads and application changes wait while a finger is tracked; `process_pending_driver_updates(gesture_idle)` applies them between gestures.
- Every write to the virtual device holds `uinput_lock`.
- `RotationTracker.reset()` runs on touch down, lift, centre and leaving the ring.
- Keep `finger_down` on the one tracked touch (`dial_touch_id`), and never clear `slots` on release.

## Contracts

- D-Bus `OverlayState` keeps the signature `bsasasidsbbb` (older extensions depend on it). New data goes into `OverlayDetails` JSON or `DriverState`.
- `GetStatus` values must be scalars (bool, str, int, float), because the D-Bus service drops anything else. Send structures as JSON text (`bridges`).
- `details` payloads never go to the Unix socket (the legacy PySide overlay applies whatever keys it gets).
- Renaming a state function means updating `STATE_FUNCTIONS` in `tests/test_driver_state.py` (the test extracts them through the AST).
- Services keep `NoNewPrivileges=yes`, and nothing started from the dial may reach root.

## Tests to run

```bash
python3 -m unittest tests.test_driver_state tests.test_driver_reload tests.test_driver_control \
    tests.test_emulation tests.test_spacemouse_loop tests.test_mouse_dial tests.test_dbus_state tests.test_install
python3 .claude/skills/turnpad-checks/checks.py --baseline
```
