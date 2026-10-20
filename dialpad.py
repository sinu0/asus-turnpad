#!/usr/bin/env python3

import logging
import os
import importlib
import sys
import threading
from time import monotonic, sleep, time

# X11 libraries are only needed in X11 sessions (python-xlib, xcffib from the distribution).
X11_LIBS_AVAILABLE = False
try:
    import Xlib.display
    import Xlib.X
    import Xlib.XK
    import xcffib
    import xcffib.xkb
    X11_LIBS_AVAILABLE = True
except Exception:
    pass

# libxkbcommon through ctypes (xkb.py), nothing to compile at install.
import xkb
from libevdev import EV_ABS, EV_KEY, EV_REL, EV_SYN, Device, InputEvent, device
# Only a fallback when i2ctransfer (i2c-tools) fails.
try:
    from periphery import I2C
except ImportError:
    I2C = None
from typing import Optional
import re
import math
import subprocess
import configparser
import ast
import signal
import mmap
import fcntl
import shutil
import glob
import socket
import json
import queue
import hashlib
import select

import launch
import layout_detect
from dir_watch import DirectoryWatcher
from mouse_dial import MouseDial, MousePointer, find_mouse
from spacemouse_dial import ButtonMachine, LayerState, ListStepper, MotionSession, MuteLease, SpaceCap
from spacemouse_dial import motion_neutral, motion_values
from spacenav_client import (
    EVMASK_RAWAXIS,
    EVMASK_RAWBUTTON,
    UEV_MOTION,
    UEV_PRESS,
    UEV_RAWAXIS,
    UEV_RAWBUTTON,
    UEV_RELEASE,
    RestoreFile,
    SpacenavClient,
    socket_candidates,
)
from desktop import keyboard_layout
from backlight import KeyboardBacklight, commands_for_level
from config.identity import APP_ID, SLUG, socket_path
from desktop.active_window import ActiveWindow, Context as ActiveWindowContext
from desktop import kwin_script
from desktop import notifications
import app_suggest
from app_link import AppLink, BUSY, NO_CLIENT, SENT, UNKNOWN_COMMAND
from dial_gesture import (
    CalibrationSession,
    DEFAULT_HYSTERESIS,
    SCROLL_TRIGGER_DEGREES,
    RotationTracker,
    emulated_dial,
    scroll_acceleration_multiplier,
    select_dial_touch,
    slice_index,
    touch_angle,
)

try:
    from dialpad_dbus import DialPadDBusService, parse_control
    DBUS_SERVICE_AVAILABLE = True
except Exception:
    DialPadDBusService = None
    parse_control = None
    DBUS_SERVICE_AVAILABLE = False

try:
    from config.schema import CONFIG_DIR, CONFIG_FILE, MacroStep, StepType, config_from_text
    from config.schema import MODIFIER_KEYS as SCHEMA_MODIFIER_KEYS
    from config.schema import merge_spacemouse_buttons, spacemouse_binds, spacemouse_buttons_for, spacemouse_hold_keys
    from config.schema import spacemouse_macros, spacemouse_modifier_keys
    from config.schema import effective_motion, merge_spacemouse_layers
    from config.legacy_adapter import META_KEY, shortcuts_for_context, shortcuts_from_config
    from config.runtime import MacroExecutionError, MacroExecutor
    CONFIG_APP_AVAILABLE = True
except Exception:
    CONFIG_DIR = None
    CONFIG_FILE = None
    SCHEMA_MODIFIER_KEYS = None
    spacemouse_binds = None
    spacemouse_buttons_for = None
    merge_spacemouse_buttons = None
    spacemouse_hold_keys = None
    spacemouse_macros = None
    spacemouse_modifier_keys = None
    effective_motion = None
    merge_spacemouse_layers = None
    MacroStep = None
    StepType = None
    config_from_text = None
    META_KEY = "_meta"
    shortcuts_from_config = None
    shortcuts_for_context = None
    MacroExecutionError = None
    MacroExecutor = None
    CONFIG_APP_AVAILABLE = False

SOCKET_PATH = socket_path()
# What a SpaceMouse run changed in spacenavd (muted, hidden buttons), kept so a
# crash can be undone at the next start; an unused button index to hide buttons on.
SPACENAV_RESTORE_FILE = str(CONFIG_DIR / "spacenav-restore.json") if CONFIG_DIR is not None else ""
SPACENAV_UNUSED_BUTTON = 63
# Control "mute:MS": another program (the SpacePilot screen's game) asks to silence the
# other spacenavd clients for a while; the SpaceMouse source reads it every pass.
spacemouse_mute = MuteLease()
sock = None
dbus_service = None
driver_state_lock = threading.RLock()
# Orders the snapshots publish_driver_state() hands to the D-Bus service; nothing else is taken under it.
driver_state_publish_lock = threading.Lock()
pending_active_application = None
pending_config_reload = False
# Configuration read and checked by ReloadConfig, applied by the touch loop
# between gestures: (config, shortcuts, revision) from read_gui_config().
pending_gui_config = None
# The applied config.json (first characters of its SHA-256), why a newer one
# was not applied, and whether one waits for the finger to lift (GetStatus).
config_status = {"revision": "", "error": "", "pending": False}
# Commands from other programs (the D-Bus method Control, e.g. the keys of a
# SpaceMouse screen): parsed tuples (see dialpad_dbus.parse_control), applied
# by the input loop in process_pending_driver_updates().
pending_controls = []
PENDING_CONTROLS_MAX = 32
# The focused window's title as a push (SetActiveWindow) delivers it, applied
# with the pending application; None = no push, the polled window_title counts.
pending_window_title = None
pending_window_title_set = False
pushed_window_title = None
# The app link's context changed (its thread asks the input loop to look again).
pending_context_refresh = False
# The profile context in force ("" = the plain profile).
context_name = ""
# The app link server (app_link.py), None when it did not start.
app_link = None
# The last thing worth telling the user on a device screen: a command that could
# not run. "until" is when it stops counting for the DriverState.
APP_NOTICE_SECONDS = 6
app_notice = {"id": 0, "text": "", "until": 0.0}
# The once-per-application offer of a ready profile (app_suggest.py): the pack on offer and until when.
suggester = None
SUGGESTION_SECONDS = 20
suggestion_shown = ("", 0.0)
# The mouse dial's and SpaceMouse's sources (listen_input_sources), for GetStatus and cleanup().
input_sources = []
# Guided calibration: requested over D-Bus, run on the touch thread.
pending_calibration = None
calibration = None
calibration_last_emit = 0

def init_socket():
    global sock

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        log.info("DialPad socket initialized at %s", SOCKET_PATH)

    except Exception as e:
        log.error("Failed to initialize socket: %s", e)
        sock = None

def on_dbus_active_application(app_id):
    global pending_active_application, pending_window_title, pending_window_title_set

    normalized_app_id = str(app_id).strip().lower() if app_id is not None else None
    with driver_state_lock:
        pending_active_application = normalized_app_id or None
        if normalized_app_id:
            # This caller knows no title: the polled one counts again.
            pending_window_title = None
            pending_window_title_set = True

def on_dbus_active_window(app_id, window_title_text):
    """SetActiveWindow: the focused application and the window's title (a KWin
    script that follows caption changes, the Shell extension)."""
    global pending_active_application, pending_window_title, pending_window_title_set

    normalized_app_id = str(app_id).strip().lower() if app_id is not None else None
    with driver_state_lock:
        pending_active_application = normalized_app_id or None
        if normalized_app_id:
            pending_window_title = str(window_title_text or "")[:300]
            pending_window_title_set = True

def on_dbus_app_commands(app):
    """GetAppCommands: the catalog of the linked application as JSON."""
    if app_link is None:
        return json.dumps({"app": str(app), "name": "", "version": "", "connected": False, "context": {}, "commands": []})
    return json.dumps(app_link.commands(str(app)))

def on_dbus_reload_config():
    """Read and check config.json now; the touch loop applies it between gestures.

    False when the file cannot be used: the driver keeps what it runs and
    GetStatus() says why in config_error.
    """
    global pending_config_reload, pending_gui_config

    try:
        prepared = read_gui_config()
    except Exception as error:
        log.error("config.json not applied, keeping the running configuration: %s", error)
        with driver_state_lock:
            config_status["error"] = str(error)
        return False

    with driver_state_lock:
        pending_gui_config = prepared
        pending_config_reload = True
        config_status.update(error="", pending=True)
    return True

def process_pending_driver_updates(gesture_idle=True):
    """Apply what the D-Bus thread requested.

    While a finger is on the dial (gesture_idle False) configuration and
    application changes wait, so one gesture never mixes two configurations
    or virtual devices.
    """
    global pending_active_application, pending_config_reload, pending_calibration, pending_gui_config
    global pending_window_title, pending_window_title_set, pushed_window_title, pending_context_refresh

    with driver_state_lock:
        calibration_request = pending_calibration
        pending_calibration = None
        if gesture_idle:
            active_application = pending_active_application
            reload_config = pending_config_reload
            prepared = pending_gui_config
            controls = list(pending_controls)
            title_pushed = pending_window_title_set
            new_window_title = pending_window_title
            refresh_context = pending_context_refresh
            pending_active_application = None
            pending_config_reload = False
            pending_gui_config = None
            pending_window_title = None
            pending_window_title_set = False
            pending_context_refresh = False
            pending_controls.clear()
        else:
            active_application = None
            reload_config = False
            controls = []
            title_pushed = False
            new_window_title = None
            refresh_context = False

    if title_pushed:
        pushed_window_title = new_window_title

    if calibration_request == "start":
        start_calibration()
    elif calibration_request == "cancel":
        stop_calibration(cancelled=True)

    if reload_config and prepared is not None:
        if apply_gui_config(prepared) and active_application is None:
            active_application = window_binary

    if refresh_context and active_application is None:
        # The linked application's mode changed: the profile's contexts are looked at again.
        active_application = window_binary

    if active_application is not None:
        # After a reload, colours, hints and titles may have changed even
        # when the application did not.
        window_was_changed(active_application, force=reload_config)

    # After the application: a command meets the profile it was sent for.
    for command in controls:
        try:
            apply_control(command)
        except Exception:
            log.exception("Control command %r failed", command)

    publish_driver_state()

def on_dbus_start_calibration():
    global pending_calibration

    # An emulated dial has no printed ring to trace, a mouse or SpaceMouse no touchpad.
    if emulation or touchpad_free:
        return False
    with driver_state_lock:
        pending_calibration = "start"
    return True

def on_dbus_toggle():
    # The action thread switches, so a shortcut never waits for the touchpad
    # grab or the pointer setting.
    enqueue_action(lambda: toggle_top_right_icon(dialpad))
    return True

def on_dbus_control(command):
    """Queue a dial command (D-Bus Control) for the input loop. True when it
    was understood, not when it changed anything; a command for a button that
    does nothing a command can do is not understood."""
    parsed = parse_control(command) if parse_control is not None else None
    if parsed is None:
        return False
    if parsed[0] == "button" and control_button_action(parsed[1]) is None:
        return False
    if parsed[0] == "mute":
        # A lease, not a command for the input loop: it must not wait for a gesture.
        spacemouse_mute.grant(parsed[1], time())
        return True
    with driver_state_lock:
        if len(pending_controls) >= PENDING_CONTROLS_MAX:
            del pending_controls[0]
        pending_controls.append(parsed)
    return True

def on_dbus_cancel_calibration():
    global pending_calibration

    with driver_state_lock:
        pending_calibration = "cancel"
    return True

def emit_calibration_state(point=None):
    global calibration_last_emit

    if calibration is None or not dbus_service:
        return
    state = calibration.state()
    state["layout"] = model or ""
    state["touchpad_max_x"] = int(max_x)
    state["touchpad_max_y"] = int(max_y)
    if point is not None:
        state["point"] = [int(point[0]), int(point[1])]
    calibration_last_emit = time()
    try:
        dbus_service.emit_calibration_progress(state)
    except Exception:
        log.exception("Failed to emit calibration progress")

def start_calibration():
    """Collect raw touches instead of dial gestures until both circles are traced."""
    global calibration

    calibration = CalibrationSession(time())
    # Keep the pointer still while the user traces the printed circles.
    set_touchpad_prop_send_events(0)
    log.info("Guided calibration started")
    emit_calibration_state()

def stop_calibration(cancelled=False):
    global calibration

    if calibration is None:
        return
    if cancelled:
        calibration.cancelled = True
        calibration.message = "Calibration cancelled"
    emit_calibration_state()
    log.info("Guided calibration %s", "cancelled" if cancelled else "finished: %s" % calibration.result())
    calibration = None
    set_touchpad_prop_send_events(1)

def calibration_frame(touch_x, touch_y, finger_down, prev_finger_down):
    """Feed one touch frame to the calibration session."""
    if calibration.expired(time()):
        calibration.error = "Calibration timed out"
        stop_calibration(cancelled=True)
        return
    if finger_down and touch_x is not None and touch_y is not None:
        if calibration.add_point(touch_x, touch_y) and time() - calibration_last_emit > 0.05:
            emit_calibration_state(point=(touch_x, touch_y))
    elif prev_finger_down and not finger_down:
        calibration.finish_stroke()
        if calibration.done:
            stop_calibration()
        else:
            emit_calibration_state()

def get_dbus_status():
    with driver_state_lock:
        config_state = dict(config_status)
    return {
        "enabled": bool(dialpad),
        "application": app_name or "",
        "session_type": xdg_session_type or "",
        "layout": model or "",
        "selected_function": (
            globals().get("title") or ""
            if globals().get("center_activated", False)
            else ""
        ),
        "menu_active": bool(
            multi_app_mode and not globals().get("center_activated", False)
        ),
        "submenu": submenu or "",
        "profile": str(profile_meta().get("name") or ""),
        "mode": "multi" if multi_app_mode else "single",
        "touchpad_max_x": int(globals().get("max_x") or 0),
        "touchpad_max_y": int(globals().get("max_y") or 0),
        "calibrating": calibration is not None,
        "emulation": bool(emulation),
        "mouse": bool(mouse_mode),
        "mouse_device": getattr(globals().get("mouse_device"), "name", "") or "",
        "spacemouse": any(source.name == "spacemouse" and source.connected for source in input_sources),
        "spacemouse_device": next(
            (source.device_name for source in input_sources if source.name == "spacemouse" and source.connected), ""
        ),
        "touchpad": touchpad_name or "",
        "config_revision": config_state["revision"],
        "config_error": config_state["error"],
        "config_pending": config_state["pending"],
    }

def driver_state():
    """What other programs (the SpaceMouse screen) follow, as the DriverState
    signal carries it. Read-only; callable from any thread. It takes no lock:
    publish_driver_state() is called with driver_state_lock held, and the
    D-Bus thread calls this for GetDriverState."""
    config_revision = config_status["revision"]
    held = [getattr(key, "name", str(key)) for key in list(spacemouse_applied_keys)]
    modifiers = []
    for name, keys in (SCHEMA_MODIFIER_KEYS or {}).items():
        if keys[0] in held:
            modifiers.append(name)
            held.remove(keys[0])
    spacemouse_source = next((source for source in input_sources if source.name == "spacemouse" and source.connected), None)
    return {
        "profile": str(profile_meta().get("name") or ""),
        "app": window_binary or "",
        "dial": bool(dialpad),
        "mode": "multi" if multi_app_mode else "single",
        "group": submenu or "",
        "function": (globals().get("title") or "") if globals().get("center_activated", False) else "",
        "modifiers": modifiers,
        "keys_held": held,
        "spacemouse": spacemouse_source is not None,
        "spacemouse_device": spacemouse_source.device_name if spacemouse_source is not None else "",
        "layer": spacemouse_source.layers.active if spacemouse_source is not None else "",
        "motion": dict(spacemouse_source.motion_shown) if spacemouse_source is not None else {
            "sensitivity": 1.0, "rotation": True, "translation": True,
        },
        "config_revision": config_revision,
        "app_link": app_link is not None and app_link.connected(window_binary),
        "app_context": app_link.context_for(window_binary) if app_link is not None else {},
        "context": context_name,
        "suggest": suggestion_shown[0] if monotonic() < suggestion_shown[1] else "",
        "notice": app_notice_state(),
    }

def app_notice_state():
    """The last notice while it counts ({id, text}), else {}."""
    if app_notice["text"] and monotonic() < app_notice["until"]:
        return {"id": app_notice["id"], "text": app_notice["text"]}
    return {}

def set_app_notice(text):
    """Tell the screens something that did not work (DriverState "notice")."""
    global app_notice

    log.info("%s", text)
    app_notice = {"id": app_notice["id"] + 1, "text": str(text), "until": monotonic() + APP_NOTICE_SECONDS}
    publish_driver_state()

def publish_driver_state():
    """Tell the D-Bus service the current driver state; it sends the signal
    only when the state differs from the last one, so calling this often is cheap.
    Computing and handing over happen under one lock, so an older snapshot
    cannot arrive after a newer one."""
    if not dbus_service:
        return
    try:
        with driver_state_publish_lock:
            dbus_service.emit_driver_state(driver_state())
    except Exception:
        log.exception("Failed to publish the driver state")

# KDE Plasma: a KWin script pushes the focused application (desktop/kwin_script.py).
kde_script = None
kde_script_closed = False

def start_kwin_script():
    """Load the KWin script that reports the focused application (Plasma on
    Wayland only: on KDE X11 the x11 polling backend answers with the binary
    path, and a second source with desktop ids would fight it)."""
    global kde_script

    if not kwin_script.is_kde() or xdg_session_type != "wayland" or kde_script_closed:
        return
    if kwin_script.script_path() is None:
        log.warning("No XDG_RUNTIME_DIR for the KWin script; the focused application is polled instead")
        return
    try:
        script = kwin_script.KWinScript(kwin_script.GioBus())
        script.load()
    except Exception as error:
        log.warning("Could not load the KWin script for the focused application (%s); polling instead", error)
        return
    kde_script = script
    if kde_script_closed:
        # The driver was stopped while the script was loading.
        stop_kwin_script()

def stop_kwin_script():
    global kde_script, kde_script_closed

    kde_script_closed = True
    script, kde_script = kde_script, None
    if script is not None:
        try:
            script.unload()
        except Exception as error:
            log.debug("Could not unload the KWin script: %s", error)

# The app link (app_link.py): applications with an add-on (Blender) tell the
# driver their commands and mode, and run commands on request.

def on_app_link_change(kind):
    """The link server's thread: an application came, went or changed its mode.
    The profile contexts are looked at again by the input loop."""
    global pending_context_refresh

    if kind in ("hello", "context", "gone"):
        with driver_state_lock:
            pending_context_refresh = True
    publish_driver_state()

def on_app_link_result(app, command_id, ok, error):
    if ok:
        log.debug("%s ran %s", app, command_id)
    else:
        set_app_notice("%s: %s failed%s" % (app, command_id, ": " + error if error else ""))

def run_app_command(command, fallback_keys=None):
    """An app step (action thread): ask the focused application's add-on to run
    ``command``. Without a connected add-on, or when it does not know the command,
    the fallback keys are pressed; without those the failure is logged and shown
    on the screens. True when the command was handed to the add-on."""
    outcome, linked = app_link.run(window_binary, command) if app_link is not None else (NO_CLIENT, "")
    if outcome == SENT:
        return True
    keys = [key for key in fallback_keys or [] if isinstance(key, str) and key]
    if keys:
        resolved = [resolve_evdev_key(key) for key in keys]
        send_key_event(resolved[0] if len(resolved) == 1 else resolved)
        log.debug("%s: %s -> shortcut %s", outcome, command, "+".join(keys))
        return False
    if outcome == UNKNOWN_COMMAND:
        set_app_notice("%s does not know the command %s" % (linked or "The application", command))
    elif outcome == BUSY:
        set_app_notice("%s is busy: %s skipped" % (linked or "The application", command))
    else:
        set_app_notice("No Turnpad add-on connected: %s skipped" % command)
    return False

def start_app_link():
    global app_link, suggester

    if CONFIG_DIR is not None and suggester is None:
        try:
            suggester = app_suggest.Suggester(CONFIG_DIR / "suggested.json")
        except Exception:
            log.exception("The profile offers are off")
    link = AppLink(on_change=on_app_link_change, on_result=on_app_link_result)
    if link.start():
        app_link = link

def stop_app_link():
    global app_link

    link, app_link = app_link, None
    if link is not None:
        link.stop()

def suggest_profile(window_id):
    """An application with a ready-made pack but no profile got the focus: offer
    the profile once per application (a notification with a button, and the
    DriverState "suggest" for the screens)."""
    global suggestion_shown

    if suggester is None or gui_config is None:
        return
    suggestion = suggester.consider(window_id, profile_exists_for_window(window_id))
    if suggestion is None:
        return
    log.info("Offering the ready profile %s for %s", suggestion.pack, suggestion.window_id)
    suggestion_shown = (suggestion.pack, monotonic() + SUGGESTION_SECONDS)
    threading.Thread(target=send_profile_offer, args=(suggestion,), name="dialpad-offer", daemon=True).start()

def send_profile_offer(suggestion):
    try:
        notifications.ActionNotifier().notify(
            "Ready profile for %s" % suggestion.pack,
            "Turnpad has a dial menu and buttons for %s. Create the profile?" % suggestion.pack,
            [("create", "Create profile")],
            lambda key: open_add_profile(suggestion) if key == "create" else None,
            icon=APP_ID,
        )
    except Exception as error:
        log.info("No notification for the profile offer (%s)", error)

def open_add_profile(suggestion):
    """The settings app, asked to add the profile (it starts the pack flow)."""
    program = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_app", "main.py")
    argv = [sys.executable or "python3", program, "--add-profile=" + suggestion.app_id]
    threading.Thread(target=launch.run, args=(argv,), name="dialpad-settings", daemon=True).start()

def init_dbus():
    global dbus_service

    if not DBUS_SERVICE_AVAILABLE:
        return

    try:
        service = DialPadDBusService(
            on_active_application=on_dbus_active_application,
            on_reload_config=on_dbus_reload_config,
            on_start_calibration=on_dbus_start_calibration,
            status_provider=get_dbus_status,
            on_cancel_calibration=on_dbus_cancel_calibration,
            on_toggle=on_dbus_toggle,
            on_control=on_dbus_control,
            driver_state_provider=driver_state,
            on_active_window=on_dbus_active_window,
            on_app_commands=on_dbus_app_commands,
        )
    except Exception:
        log.exception("Failed to create the DialPad D-Bus service")
        return

    dbus_service = service

    def start_service():
        try:
            if not service.start():
                log.debug("DialPad D-Bus service is unavailable")
                return
        except Exception:
            log.exception("Failed to start the DialPad D-Bus service")
            return
        # The service owns its name now: KWin's script can report to it.
        start_kwin_script()

    try:
        threading.Thread(
            target=start_service,
            name="dialpad-dbus-startup",
            daemon=True,
        ).start()
    except Exception:
        dbus_service = None
        log.exception("Failed to schedule DialPad D-Bus startup")

PYWAYLAND_AVAILABLE = False
try:
    from pywayland.client import Display
    from pywayland.protocol.wayland import WlSeat
    PYWAYLAND_AVAILABLE = True
except Exception:
    pass

SYSTEMD_JOURNAL_AVAILABLE = False
try:
    from systemd.journal import JournalHandler
    SYSTEMD_JOURNAL_AVAILABLE = True
except Exception:
    pass

PYATSPI_AVAILABLE = False
try:
    import pyatspi
    PYATSPI_AVAILABLE = True
except Exception:
    pass

QDBUS = shutil.which("qdbus") or shutil.which("qdbus6") or shutil.which("qdbus-qt6")

# Logging setup
logging.basicConfig(
    format='%(asctime)s %(levelname)s %(message)s',
    level=os.environ.get('LOG', 'INFO')
)
log = logging.getLogger(SLUG)
if SYSTEMD_JOURNAL_AVAILABLE:
    log.addHandler(JournalHandler())

xauth_in_tmp_dir = glob.glob('/tmp/xauth_*')
if len(xauth_in_tmp_dir) > 0:
  latest_xauth_file = max(xauth_in_tmp_dir, key=os.path.getmtime)   
  os.environ['XAUTHORITY'] = latest_xauth_file
  log.info("X11 has xauth file in /tmp folder with filename changed each boot, currently {}".format(os.environ['XAUTHORITY']))

# Detect session type
xdg_session_type = os.environ.get('XDG_SESSION_TYPE')
if not xdg_session_type:
    log.error("XDG session type is not set. Exiting.")
    sys.exit(1)

# Setup display for X11
display = None
xkb_conn = None
display_var = None
display_wayland = None
keyboard_state = None
display_wayland_var = None
keymap_loaded = False
listening_touchpad_events_started = False
active_modifiers = set()
modifiers = set()
coactivator_modifiers = set()
activation_lock = threading.Lock()
multi_app_mode = None
multi_app_mode_titles = None
multi_app_mode_icons = None
app_specific_shortcuts = {}
app_name = None
coactivator_keys = None

if xdg_session_type == "x11":
    if not X11_LIBS_AVAILABLE:
        log.error("X11 libraries are not available. Please install python-xlib and xcffib.")
        sys.exit(1)

    try:
        display_var = os.environ.get('DISPLAY')
        display = Xlib.display.Display(display_var)
        log.info("X11 detected and connected succesfully to the display {}".format(display_var))
        xkb_conn = xcffib.connect()
        xkb_ext = xkb_conn(xcffib.xkb.key)
        xkb_ext.UseExtension(
            xcffib.xkb.MAJOR_VERSION,
            xcffib.xkb.MINOR_VERSION
        ).reply()

        xkb_conn_setup = xkb_conn.get_setup()
        log.info("X11 detected and connected succesfully to the xcffib")
    except Exception as e:
        log.error(f"Failed to connect to X11 display: {e}")
        sys.exit(1)
else:
    display_wayland_var = os.environ.get('WAYLAND_DISPLAY')
    # Without pywayland the keymap is compiled from the desktop's layout
    # settings (load_keymap_from_rule_names).
    if not PYWAYLAND_AVAILABLE:
        log.info("pywayland is not installed; the keyboard layout comes from the desktop settings")
    else:
        try:
            display_wayland = Display(display_wayland_var)
            display_wayland.connect()
            log.info("Wayland session detected and connected.")
        except Exception as e:
            display_wayland = None
            log.warning(f"Failed to connect to Wayland display: {e}; the keyboard layout comes from the desktop settings")

dialpad: bool = False

# DialPad layout model
model = None
if len(sys.argv) > 1:
    model = sys.argv[1]
# Packages start the driver without knowing the laptop.
if model in (None, "", layout_detect.AUTO):
    model = layout_detect.detect_layout(os.path.join(os.path.dirname(os.path.abspath(__file__)), "layouts"))
    log.info("Layout detected for this laptop: %s", model)
try:
    model_layout = importlib.import_module('layouts.' + model)
except:
    layouts_dir = "layouts"
    available_layouts = [os.path.splitext(f)[0] for f in os.listdir(layouts_dir) if f.endswith(".py")]

    log.error(
        f"DialPad layout *.py from dir '{layouts_dir}' is required as first argument. "
        f"Re-run install script or add missing first argument. "
        f"Available layouts: {', '.join(available_layouts)}"
    )
    sys.exit(1)

# Config file dir
config_file_dir = ""
if len(sys.argv) > 2:
    config_file_dir = sys.argv[2]
# When is given config dir empty or is used default -> to ./ because the config watcher needs a folder (nor nothing = "")
if config_file_dir == "":
     config_file_dir = "./"
config_file_dir = os.path.join(config_file_dir, "")
os.makedirs(config_file_dir, exist_ok=True)

# Layout
circle_diameter = getattr(model_layout, "circle_diameter", 0)
center_button_diameter = getattr(model_layout, "center_button_diameter", 0)
circle_center_x = getattr(model_layout, "circle_center_x", 0)
circle_center_y = getattr(model_layout, "circle_center_y", 0)
top_right_icon_width = getattr(model_layout, "top_right_icon_width", 0)
top_right_icon_height = getattr(model_layout, "top_right_icon_height", 0)

# App-specific configuration (add more mappings as needed)
app_shortcuts = getattr(model_layout, "app_shortcuts", {})
gui_config = None
legacy_app_shortcuts = app_shortcuts
angle_hysteresis = DEFAULT_HYSTERESIS
center_hold_time = 0.5
# Function last used per application, restored like the ASUS Dial does:
# (group, function), group None for a function of the radial menu itself.
last_function_by_app = {}
# A group's list beside the dial: the group while its list is open or one of
# its functions is selected, and the highlighted function of the list.
submenu = None
submenu_index = 0
# Turning per step of the highlight in a group's list.
SUBMENU_STEP_DEGREES = 30

def read_gui_config():
    """(config, shortcuts, revision) from config.json; raises when it is unusable.

    Without config.json (or the config package) config is None and the
    shortcuts are the layout's.
    """
    if not CONFIG_APP_AVAILABLE or CONFIG_FILE is None or not CONFIG_FILE.exists():
        return None, legacy_app_shortcuts, ""
    data = CONFIG_FILE.read_bytes()
    config = config_from_text(data.decode("utf-8"))
    return config, shortcuts_from_config(config), hashlib.sha256(data).hexdigest()[:12]

def set_config_status(**values):
    with driver_state_lock:
        config_status.update(values)

def load_gui_shortcuts():
    global app_shortcuts, gui_config

    try:
        gui_config, app_shortcuts, revision = read_gui_config()
    except Exception as error:
        log.error("Failed to load config.json, using the layout's shortcuts: %s", error)
        app_shortcuts = legacy_app_shortcuts
        gui_config = None
        set_config_status(error=str(error))
        return False

    set_config_status(revision=revision, error="")
    if gui_config is None:
        return False
    log.info("Loaded GUI configuration from %s", CONFIG_FILE)
    return True

def apply_gui_config(prepared):
    """Run a configuration from read_gui_config(); keep the running one if it fails."""
    global app_shortcuts, gui_config

    previous = (gui_config, app_shortcuts)
    gui_config, app_shortcuts, revision = prepared
    try:
        apply_gui_settings()
        enable_config_macro_capabilities(reset_udev=True)
    except Exception as error:
        log.exception("Configuration reload failed; keeping the running configuration")
        gui_config, app_shortcuts = previous
        try:
            apply_gui_settings()
        except Exception:
            log.exception("Restoring the running configuration failed")
        set_config_status(error=str(error) or type(error).__name__, pending=False)
        return False

    set_config_status(revision=revision, error="", pending=False)
    log.info("Applied configuration %s", revision or "(layout shortcuts)")
    return True

def apply_gui_settings():
    global circle_diameter, center_button_diameter, circle_center_x, circle_center_y
    global top_right_icon_width, top_right_icon_height
    global activation_time, coactivator_keys, default_treshold
    global slices_minimum_count, disable_due_inactivity_time, angle_hysteresis
    global center_hold_time, socket_send_progress_above_treshold

    if gui_config is None:
        return False

    geometry = gui_config.geometry
    activation = gui_config.activation
    gesture = gui_config.gesture

    if (gui_config.emulation.enabled and not mouse_mode) != emulation:
        log.info("DialPad emulation %s after the driver restarts", "starts" if gui_config.emulation.enabled else "ends")
    if gui_config.mouse.enabled != mouse_mode:
        log.info("Mouse dial %s after the driver restarts", "starts" if gui_config.mouse.enabled else "ends")
    if gui_config.spacemouse.enabled != spacemouse_wanted:
        log.info("SpaceMouse mode %s after the driver restarts", "starts" if gui_config.spacemouse.enabled else "ends")

    if emulation:
        # The emulated dial follows the touchpad, not a calibrated geometry.
        if "max_x" in globals():
            apply_emulated_geometry()
    # A configuration created for another laptop model (for example an
    # imported file) must not move the dial away from this touchpad's ring.
    elif gui_config.device_name and model and gui_config.device_name != model:
        log.warning(
            "config.json geometry is for layout %s, the driver runs %s; using the layout geometry",
            gui_config.device_name, model,
        )
    else:
        circle_diameter = geometry.circle_diameter
        center_button_diameter = geometry.center_button_diameter
        circle_center_x = geometry.center_x
        circle_center_y = geometry.center_y
        top_right_icon_width = geometry.width
        top_right_icon_height = geometry.height

    activation_time = activation.activation_time
    coactivator_keys = activation.coactivator_keys
    default_treshold = gesture.default_threshold
    slices_minimum_count = gesture.slices_minimum_count
    disable_due_inactivity_time = gesture.disable_inactivity_time
    angle_hysteresis = max(0, gesture.hysteresis)
    center_hold_time = max(0.2, float(gesture.center_hold_time))
    socket_send_progress_above_treshold = int(gui_config.overlay.progress_above_degrees)

    if keymap_loaded and "load_evdev_keys_for_coactivator_modifiers" in globals():
        try:
            load_evdev_keys_for_coactivator_modifiers(coactivator_keys)
        except Exception:
            log.debug("Failed to load GUI co-activator modifiers", exc_info=True)

    # A changed brightness setting applies to a DialPad that is on.
    if dialpad and "enqueue_action" in globals():
        enqueue_action(apply_backlight)

    return True

load_gui_shortcuts()

# Mouse dial (config.json "mouse") for computers without a touchpad: a mouse
# key switches the dial, the wheel turns it, the middle button is the centre.
# No touchpad, no DialPad hardware. Read once; the settings app restarts the
# driver when it changes.
mouse_mode = bool(gui_config is not None and gui_config.mouse.enabled)
# SpaceMouse mode (config.json "spacemouse"): a 3Dconnexion SpaceMouse through
# spacenavd. It works alone (no touchpad) or together with the mouse dial; with
# a touchpad mode (a DialPad found, or the emulation) it is not supported yet.
spacemouse_wanted = bool(gui_config is not None and gui_config.spacemouse.enabled)
spacemouse_alone = spacemouse_wanted and not mouse_mode and not (gui_config.emulation.enabled)
# Without a touchpad to find, this is decided after the search below.
touchpad_optional = mouse_mode or spacemouse_alone
touchpad_free = mouse_mode
spacemouse_mode = spacemouse_wanted and mouse_mode
# DialPad emulation (config.json "emulation") for touchpads without a DialPad:
# any touchpad, no commands to its hardware (no light, nothing to unlock), the
# whole touchpad is the dial while it is on. Read once likewise; the mouse dial
# replaces it when both are set.
emulation = bool(gui_config is not None and gui_config.emulation.enabled) and not mouse_mode
emulation_corner = True
# Touchpads whose axes differ in resolution: x distances times this.
x_scale = 1.0
if mouse_mode:
    log.info("Mouse dial: the wheel turns the dial, the middle button is its centre")
    if gui_config.emulation.enabled:
        log.warning("Mouse dial and DialPad emulation are both on; the mouse dial wins")
elif emulation:
    log.info("DialPad emulation: the whole touchpad is the dial while it is on")
if spacemouse_wanted and not mouse_mode and gui_config.emulation.enabled:
    log.warning("SpaceMouse mode is not supported together with Touchpad mode yet; ignoring it")

# Figure out devices from devices file
touchpad: Optional[str] = None
touchpad_name: Optional[str] = None
device_id: Optional[str] = None
keyboard_device_id: Optional[str] = None
device_addr: Optional[int] = None
hidraw_path: Optional[str] = None
keyboard: Optional[str] = None

# Constants
try_times = 5
try_sleep = 0.1

# Look into the devices file #
while try_times > 0:

    touchpad_detected = 0
    keyboard_detected = 0

    with open('/proc/bus/input/devices', 'r') as f:
        lines = f.readlines()
        for line in lines:
            # Look for the touchpad #

            # https://github.com/mohamed-badaoui/asus-touchpad-numpad-driver/issues/87
            # https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/95
            # https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/110
            # https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/161
            # https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/198
            # https://github.com/asus-linux-drivers/asus-stylus-driver/issues/17
            if mouse_mode:
                pass
            elif touchpad_detected == 0 and emulation and line.startswith("N: Name=") and layout_detect.is_touchpad(line) and "DialPad" not in line:
                touchpad_detected = 1
                log.info('Detecting touchpad for the emulated dial from string: \"%s\"', line.strip())
                touchpad_name = line.split("\"")[1]
            elif (touchpad_detected == 0 and not emulation and ("Name=\"ASUE" in line or "Name=\"ELAN" in line or "Name=\"ASUP" in line or "Name=\"ASUF" in line or "Name=\"ASCE" in line or "Name=\"ASCF" in line or "Name=\"ASCP" in line) and "Touchpad" in line and not "9009" in line and not "9008" in line):
                touchpad_detected = 1
                log.info('Detecting touchpad from string: \"%s\"', line.strip())
                touchpad_name = line.split("\"")[1]

                # https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/161
                if ("ASUF1416" in line or "ASUF1205" in line or "ASUF1204" in line):
                  device_addr = 0x38
                else:
                  device_addr = 0x15

            if touchpad_detected == 1:
                if "S: " in line:
                    # search device id
                    device_id = re.sub(r".*i2c-(\d+)/.*$", r'\1', line).replace("\n", "")
                    log.info('Set touchpad device id %s from %s', device_id, line.strip())
                    hid_device = "/sys" + line.split("=", 1)[1].strip().split("/input/")[0]
                    hidraw_nodes = sorted(glob.glob(f"{hid_device}/hidraw/hidraw*"))
                    hidraw_path = f"/dev/{os.path.basename(hidraw_nodes[0])}" if hidraw_nodes else None

                if "H: " in line:
                    touchpad = line.split("event")[1]
                    touchpad = touchpad.split(" ")[0]
                    touchpad_detected = 2
                    log.info('Set touchpad id %s from %s', touchpad, line.strip())

            # Look for the keyboard
            if keyboard_detected == 0 and ("Name=\"AT Translated Set 2 keyboard" in line or (("Name=\"ASUE" in line or "Name=\"Asus" in line or "Name=\"ASUP" in line or "Name=\"ASUF" in line) and "Keyboard" in line)):
                keyboard_detected = 1
                log.info(
                    'Detecting keyboard from string: \"%s\"', line.strip())

            # We look for keyboard
            if keyboard_detected == 1 and "H: " in line:
                keyboard = line.split("event")[1]
                keyboard = keyboard.split(" ")[0]
                keyboard_detected = 2
                log.info('Set keyboard id %s from %s', keyboard, line.strip())

              # Do not stop looking if touchpad and keyboard have been found
            # because more drivers can be installed
            # https://github.com/mohamed-badaoui/asus-touchpad-numpad-driver/issues/87
            # https://github.com/asus-linux-drivers/asus-numberpad-driver/issues/95
            #if touchpad_detected == 2 and keyboard_detected == 2:
            #    break

    # The mouse dial needs no touchpad (the mouse is looked for later, and
    # again whenever it is gone).
    if (touchpad_detected != 2 and not touchpad_optional) or keyboard_detected != 2:
        try_times -= 1
        if try_times == 0:
            if touchpad_optional:
                # Not an error without a touchpad, and nothing to dump.
                if keyboard_detected != 2:
                    log.info("No ASUS keyboard; Ctrl/Shift/Alt variants of functions need it")
            else:
                with open('/proc/bus/input/devices', 'r') as f:
                    lines = f.readlines()
                    for line in lines:
                        log.error(line)
            if keyboard_detected != 2 and not touchpad_optional:
                log.error("Can't find keyboard (code: %s)", keyboard_detected)
                # keyboard is optional, no sys.exit(1)!
            if touchpad_detected != 2 and not touchpad_optional:
                log.error("Can't find touchpad (code: %s)", touchpad_detected)
                if not emulation:
                    log.error("No touchpad with a DialPad found; other touchpads need Driver > Touchpad mode in the settings app")
                sys.exit(1)
            # The emulation never talks to the touchpad over I2C.
            if touchpad_detected == 2 and not emulation and not mouse_mode and not device_id.isnumeric():
                log.error("Can't find device id")
                sys.exit(1)
    else:
        break

    sleep(try_sleep)

# SpaceMouse mode alone runs when there is no DialPad touchpad; with one the
# touchpad mode wins.
if spacemouse_alone:
    if touchpad_detected == 2:
        log.warning("SpaceMouse mode is not supported together with DialPad mode yet (a DialPad touchpad was found); ignoring it")
    else:
        touchpad_free = True
        spacemouse_mode = True
        log.info("SpaceMouse mode: no touchpad needed")

# The emulation, the mouse dial and the SpaceMouse send nothing to the touchpad: no hidraw, no I2C bus.
if emulation or touchpad_free:
    hidraw_path = None
else:
    # Is the hidraw node of the touchpad usable? (preferred, I2C is a fallback)
    if hidraw_path:
        try:
            with open(hidraw_path, "rb+", buffering=0):
                pass
            log.info("DialPad backlight will be switched via %s", hidraw_path)
        except Exception as e:
            log.info("Can not open %s: %s, I2C will be used", hidraw_path, e)
            hidraw_path = None

    # Open a handle to "/dev/i2c-x", representing the I2C bus
    path = f"/dev/i2c-{device_id}"
    try:
        # Prefer python-periphery if it works
        i2c = I2C(path)
        i2c.close()
        log.debug("Successfully opened I2C bus via python-periphery at %s", path)
    except Exception as e:
        log.debug("periphery.I2C failed to open %s: %s, trying raw open()", path, e)
        try:
            # Fallback: try to open the device file directly
            with open(path, "rb+", buffering=0) as f:
                pass
            log.debug("Successfully opened I2C bus via raw open() at %s", path)
        except Exception as e2:
            if hidraw_path:
                log.debug("Can not open the I2C bus connection (id: %s): %s, only hidraw will be used", device_id, e2)
            else:
                log.error(
                    "Can not open the I2C bus connection (id: %s) at %s: %s",
                    device_id,
                    path,
                    e2,
                )
                sys.exit(1)

# Config
CONFIG_FILE_NAME = "dialpad_dev"
CONFIG_SECTION = "main"
CONFIG_ENABLED = "enabled"
CONFIG_SOCKET_ENABLED = "socket_enabled"
CONFIG_SOCKET_ENABLED_DEFAULT = True
CONFIG_ENABLED_DEFAULT = False
CONFIG_SLICES_MINIMUM_COUNT = "slices_minimum_count"
CONFIG_SLICES_MINIMUM_COUNT_DEFAULT = 4
CONFIG_DEFAULT_TRESHOLD = "default_treshold"
CONFIG_DEFAULT_TRESHOLD_DEFAULT = 90
CONFIG_DISABLE_DUE_INACTIVITY_TIME = "disable_due_inactivity_time"
CONFIG_DISABLE_DUE_INACTIVITY_TIME_DEFAULT = 120
CONFIG_TOUCHPAD_DISABLES_DIALPAD = "touchpad_disables_dialpad"
CONFIG_TOUCHPAD_DISABLES_DIALPAD_DEFAULT = True
CONFIG_ACTIVATION_TIME = "activation_time"
CONFIG_ACTIVATION_TIME_DEFAULT = True
CONFIG_SUPPRESS_APP_SPECIFICS_SHORTCUTS = "config_supress_app_specifics_shortcuts"
CONFIG_SUPPRESS_APP_SPECIFICS_SHORTCUTS_DEFAULT = False
CONFIG_TOP_RIGHT_ICON_COACTIVATOR_KEY = "top_right_icon_coactivator_key"
CONFIG_TOP_RIGHT_ICON_COACTIVATOR_KEY_DEFAULT = ""  # Empty means no co-activator required
CONFIG_SOCKET_SEND_PROGRESS_ABOVE_TRESHOLD = "socket_send_progress_above_treshold"
CONFIG_SOCKET_SEND_PROGRESS_ABOVE_TRESHOLD_DEFAULT = 120

config_file_path = config_file_dir + CONFIG_FILE_NAME
config = configparser.ConfigParser()
config_lock = threading.Lock()

# Start monitoring the touchpad (the mouse dial has none: its device is opened
# by listen_input_sources())
if touchpad_free:
    fd_t = None
    d_t = None
    abs_x = abs_y = None
    min_x = max_x = min_y = max_y = 0
else:
    fd_t = open('/dev/input/event' + str(touchpad), 'rb')
    d_t = Device(fd_t)

    # Get touchpad dimensions
    abs_x = d_t.absinfo[EV_ABS.ABS_X]
    abs_y = d_t.absinfo[EV_ABS.ABS_Y]
    min_x, max_x = abs_x.minimum, abs_x.maximum
    min_y, max_y = abs_y.minimum, abs_y.maximum
    log.info('Touchpad min-max: x %d-%d, y %d-%d', min_x, max_x, min_y, max_y)

def apply_emulated_geometry():
    """Ring, centre button and corner of the emulated dial, from the touchpad's size."""
    global circle_diameter, center_button_diameter, circle_center_x, circle_center_y
    global top_right_icon_width, top_right_icon_height, emulation_corner, x_scale

    settings = gui_config.emulation if gui_config is not None else None
    emulation_corner = settings.corner if settings is not None else True
    dial = emulated_dial(
        min_x, max_x, min_y, max_y,
        settings.center_size if settings is not None else 30,
        abs_x.resolution or 0, abs_y.resolution or 0,
    )
    circle_diameter = dial["circle_diameter"]
    center_button_diameter = dial["center_button_diameter"]
    circle_center_x = dial["circle_center_x"]
    circle_center_y = dial["circle_center_y"]
    top_right_icon_width = dial["top_right_icon_width"]
    top_right_icon_height = dial["top_right_icon_height"]
    x_scale = dial["x_scale"]
    log.info("Emulated dial: %s, corner activation %s", dial, "on" if emulation_corner else "off")

if emulation:
    apply_emulated_geometry()

last_event_time = 0

def overlay_enabled():
    """Overlay state is wanted by the D-Bus overlay or the socket overlay.

    The GNOME extension listens on D-Bus, so it must not depend on the
    original driver's ``socket_enabled`` switch, which only concerns the
    Unix socket of the PySide overlay.
    """
    return dbus_service is not None or bool(globals().get("socket_enabled"))

def send_to_socket(payload: dict):
    if dbus_service:
        try:
            dbus_service.emit_overlay_state(payload)
        except Exception:
            log.exception("Failed to emit DialPad D-Bus overlay state")
        # Profile, dial, group and function changes all pass here.
        publish_driver_state()

    # Presentation details are D-Bus only; the legacy socket overlay does not
    # understand them.
    if not sock or not globals().get("socket_enabled") or set(payload) <= {"details"}:
        return
    payload = {key: value for key, value in payload.items() if key != "details"}

    msg = {
        "ts": time(),
        **payload
    }

    try:
        sock.sendto(
            (json.dumps(msg) + "\n").encode("utf-8"),
            SOCKET_PATH
        )
    except Exception:
        pass

def parse_value_from_config(value):
    if value == '0':
        return False
    elif value == '1':
        return True
    else:
        return value

def parse_value_to_config(value):
    if value == True:
        return '1'
    elif value == False:
        return '0'
    else:
        return str(value)

def config_save():
    global config_file_dir, config_file_path

    try:
        with open(config_file_path, 'w') as configFile:
            config.write(configFile)
            log.debug('Writting to config file: \"%s\"', configFile)
    except:
        log.error('Error during writting to config file: \"%s\"', config_file_path)
        pass

def config_set(key, value, no_save=False, already_has_lock=False):
    global config, config_file_dir, config_lock

    if not already_has_lock:
        #log.debug("config_set: config_lock.acquire will be called")
        config_lock.acquire()
        #log.debug("config_set: config_lock.acquire called succesfully")

    config.set(CONFIG_SECTION, key, parse_value_to_config(value))
    log.info('Setting up for config file key: \"%s\" with value: \"%s\"', key, value)

    if not no_save:
        config_save()

    if not already_has_lock:
        # keep the lock while the config watcher sees this save (deadlock)
        sleep(0.1)
        config_lock.release()

    return value

# methods for read & write from config file
def config_get(key, key_default):
    try:
        value = config.get(CONFIG_SECTION, key)
        parsed_value = parse_value_from_config(value)
        return parsed_value
    except:
        config.set(CONFIG_SECTION, key, parse_value_to_config(key_default))
        return key_default

def read_config_file():
    global config, config_file_path

    try:
        if not config.has_section(CONFIG_SECTION):
            config.add_section(CONFIG_SECTION)

        config.read(config_file_path)
    except:
        pass

def is_multifunction(app_config):

    standard_keys = {"center", "clockwise", "counterclockwise"}

    non_standard_keys = []
    non_standard_keys_icons = []

    for key, value in app_config.items():
        if key in standard_keys or str(key).startswith("_"):
            continue

        non_standard_keys.append(key)

        # Keep icons index-aligned with titles; segments without an icon
        # get an empty entry instead of shifting the following icons.
        icon = ""
        if isinstance(value, dict):
            icon = value.get("icon") or ""
            icons = value.get("icons")
            if icons and value.get("value"):
                try:
                    icon = icons[get_current_value(value)]
                except (KeyError, IndexError, TypeError):
                    pass
        non_standard_keys_icons.append(icon)

    return bool(len(non_standard_keys)), non_standard_keys, non_standard_keys_icons

def load_evdev_keys_for_coactivator_modifiers(coactivator_keys):
    global coactivator_modifiers

    if not coactivator_keys:
        return

    coactivator_modifiers.clear()
    for coactivator_key_name in coactivator_keys:

        try:
            coactivator_keysym_name = mod_name_to_specific_keysym_name(coactivator_key_name)
            coactivator_evdev_key = get_keysym_name_associated_to_evdev_key_reflecting_current_layout()[coactivator_keysym_name]
        except KeyError:
            log.warning("Unknown co-activator key %r; use Control, Shift or Alt", coactivator_key_name)
            continue
        coactivator_modifiers.add(coactivator_evdev_key)

    log.debug("Loaded co-activator modifiers succesfully")

def load_all_config_values():
    global config
    global disable_due_inactivity_time
    global touchpad_disables_dialpad
    global activation_time
    global config_lock
    global slices_minimum_count
    global default_treshold
    global suppress_app_specifics_shortcuts
    global coactivator_keys
    global socket_enabled
    global multi_app_mode_titles
    global multi_app_mode_icons
    global socket_send_progress_above_treshold
    global coactivator_modifiers

    #log.debug("load_all_config_values: config_lock.acquire will be called")
    config_lock.acquire()
    #log.debug("load_all_config_values: config_lock.acquire called succesfully")

    read_config_file()

    disable_due_inactivity_time = float(config_get(CONFIG_DISABLE_DUE_INACTIVITY_TIME, CONFIG_DISABLE_DUE_INACTIVITY_TIME_DEFAULT))
    touchpad_disables_dialpad = config_get(CONFIG_TOUCHPAD_DISABLES_DIALPAD, CONFIG_TOUCHPAD_DISABLES_DIALPAD_DEFAULT)
    activation_time = float(config_get(CONFIG_ACTIVATION_TIME, CONFIG_ACTIVATION_TIME_DEFAULT))
    enabled = config_get(CONFIG_ENABLED, CONFIG_ENABLED_DEFAULT)
    slices_minimum_count = int(config_get(CONFIG_SLICES_MINIMUM_COUNT, CONFIG_SLICES_MINIMUM_COUNT_DEFAULT))
    default_treshold = int(config_get(CONFIG_DEFAULT_TRESHOLD, CONFIG_DEFAULT_TRESHOLD_DEFAULT))
    suppress_app_specifics_shortcuts = int(config_get(CONFIG_SUPPRESS_APP_SPECIFICS_SHORTCUTS, CONFIG_SUPPRESS_APP_SPECIFICS_SHORTCUTS_DEFAULT))

    coactivator_keys = config_get(CONFIG_TOP_RIGHT_ICON_COACTIVATOR_KEY, CONFIG_TOP_RIGHT_ICON_COACTIVATOR_KEY_DEFAULT).strip().split()

    load_evdev_keys_for_coactivator_modifiers(coactivator_keys)

    socket_enabled = config_get(CONFIG_SOCKET_ENABLED, CONFIG_SOCKET_ENABLED_DEFAULT)
    socket_send_progress_above_treshold = int(config_get(CONFIG_SOCKET_SEND_PROGRESS_ABOVE_TRESHOLD, CONFIG_SOCKET_SEND_PROGRESS_ABOVE_TRESHOLD_DEFAULT))

    multi_app_mode_titles = pad_to_minimum(multi_app_mode_titles, slices_minimum_count)
    multi_app_mode_icons = pad_to_minimum(multi_app_mode_icons, slices_minimum_count)

    config_lock.release()

    if enabled is not dialpad:
        toggle_top_right_icon(dialpad)

def send_value_to_touchpad_via_hidraw(value):
    global hidraw_path

    if not hidraw_path:
        return False

    # The command sent via I2C below without the I2C-HID SET_REPORT header
    # (0x05 0x00 0x3d 0x03 0x06 0x00 0x07 0x00), which the kernel adds:
    # feature report with id 0x0d.
    data = bytearray([0x0d, 0x14, 0x03, int(value, 16), 0xad])

    # HIDIOCSFEATURE(len) = _IOC(_IOC_READ | _IOC_WRITE, 'H', 0x06, len)
    HIDIOCSFEATURE = 0xC0000000 | (len(data) << 16) | (ord('H') << 8) | 0x06

    try:
        with open(hidraw_path, "rb+", buffering=0) as f:
            fcntl.ioctl(f, HIDIOCSFEATURE, data)
        log.debug("Feature report sent via %s", hidraw_path)
        return True
    except Exception as e:
        log.info("hidraw (%s) failed: %s; using I2C from now on", hidraw_path, e)
        # Do not try again, missing permissions will not fix themselves.
        hidraw_path = None

    return False

def send_value_to_touchpad(value):
    # A touchpad without a DialPad (or none at all) gets no DialPad commands.
    if emulation or touchpad_free:
        return True
    if send_value_to_touchpad_via_hidraw(value):
        return True

    return send_value_to_touchpad_via_i2c(value)

def send_value_to_touchpad_via_i2c(value):
    global device_id, device_addr

    data = [
        0x05, 0x00, 0x3d, 0x03, 0x06, 0x00, 0x07, 0x00,
        0x0d, 0x14, 0x03, int(value, 16), 0xad,
    ]

    path = f"/dev/i2c-{device_id}"

    # 1) i2ctransfer (from i2c-tools)
    try:
        hex_data = [f"0x{b:02x}" for b in data]
        cmd = [
            "i2ctransfer",
            "-f", "-y",
            str(device_id),
            f"w{len(data)}@0x{device_addr:x}",
        ] + hex_data

        log.debug("Trying I2C via i2ctransfer: %s", " ".join(cmd))
        subprocess.run(cmd, check=True, capture_output=True)
        log.debug("I2C transfer successful via i2ctransfer")
        return True
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode().strip() if e.stderr else str(e)
        log.error("i2ctransfer failed: %s; falling back to python-periphery", stderr)
    except Exception as e:
        log.error("Error during i2ctransfer: %s; falling back to python-periphery", e)

    # 2) Fallback: python-periphery
    try:
        with I2C(path) as i2c:
            msg = I2C.Message(data)
            i2c.transfer(device_addr, [msg])
            log.debug("Sent I2C data via python-periphery on %s", path)
            return True
    except Exception as e:
        log.error("periphery.I2C transfer failed on %s: %s", path, e)

    return False

def enable_config_macro_capabilities(reset_udev=False):
    if MacroStep is None or StepType is None:
        return

    enabled_keys_count = len(enabled_evdev_keys)

    def enable_macro_key(key):
        try:
            if not isinstance(key, str):
                raise ValueError("Macro keys must be strings")
            if key.isdigit():
                evdev_key = EV_KEY.codes[int(key)]
            else:
                evdev_key = getattr(EV_KEY, key)
            if getattr(evdev_key, "type", None) != EV_KEY:
                raise ValueError("Macro key is not an EV_KEY")
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            log.debug("Skipping invalid configuration macro key %r", key)
            return

        enable_key(evdev_key)

    def enable_macro_keys(keys):
        if not isinstance(keys, list):
            if keys is not None:
                log.debug("Skipping invalid configuration macro keys %r", keys)
            return
        for key in keys:
            enable_macro_key(key)

    def process_macro(macro):
        for step in macro:
            if step.type in (StepType.KEYSTROKE, StepType.MEDIA, StepType.VOLUME, StepType.BRIGHTNESS, StepType.APP):
                enable_macro_keys(step.keys)
            if step.type is StepType.SCROLL:
                enable_key([EV_REL.REL_WHEEL, EV_REL.REL_WHEEL_HI_RES])
                if step.axis == "horizontal":
                    enable_key([EV_REL.REL_HWHEEL, EV_REL.REL_HWHEEL_HI_RES])
                enable_macro_keys(step.modifiers)
            elif step.type is StepType.VOLUME:
                enable_key([EV_KEY.KEY_VOLUMEUP, EV_KEY.KEY_VOLUMEDOWN])
            elif step.type is StepType.BRIGHTNESS:
                enable_key([EV_KEY.KEY_BRIGHTNESSUP, EV_KEY.KEY_BRIGHTNESSDOWN])
            elif step.type is StepType.MEDIA and step.direction is not None:
                enable_macro_key(step.direction)

    def track_modifier_keys(names):
        # The keyboard listener follows these keys for the function's
        # variants with Ctrl, Shift or Alt held.
        for name in names if isinstance(names, list) else []:
            code = getattr(EV_KEY, name, None) if isinstance(name, str) else None
            if code is not None:
                modifiers.add(code)

    def scan_shortcuts(value):
        if isinstance(value, dict):
            macro = value.get("macro")
            if isinstance(macro, list) and all(isinstance(step, MacroStep) for step in macro):
                process_macro(macro)
                track_modifier_keys(value.get("modifier"))
            for nested_value in value.values():
                scan_shortcuts(nested_value)
        elif isinstance(value, list):
            for nested_value in value:
                scan_shortcuts(nested_value)

    scan_shortcuts(app_shortcuts)

    # Keys of the SpaceMouse buttons' macros and modifiers.
    config = globals().get("gui_config")
    if config is not None and globals().get("spacemouse_macros") is not None:
        for macro in spacemouse_macros(config):
            process_macro(macro)
        enable_macro_keys(sorted(spacemouse_modifier_keys(config) | spacemouse_hold_keys(config)))

    if reset_udev and udev and len(enabled_evdev_keys) > enabled_keys_count:
        # The action thread rebuilds the device: the dial keeps running
        # meanwhile and the actions queued after it use the new keys.
        enqueue_action(reset_udev_device, required=True)

def initialize_virtual_device():
    global udev, dev, modifiers

    try:
        # create the virtual device
        dev = Device()
        # Other makes' touchpads may have a one-word name. The suffix keeps
        # the mouse finder (find_mouse) from taking this device for a mouse.
        dev.name = " ".join((touchpad_name or "Mouse").split(" ")[:2]) + " DialPad"

        # aggregate all shortcuts to enable
        shortcuts_to_enable = {}

        shortcuts_to_enable = app_shortcuts

        def process_config(config):
            if not isinstance(config, dict):
                return

            field = config.get("key")
            if field is None:
                return

            # single key
            if isEvent(field):
                enable_key(field)
            # list of keys (EV_KEY or EV_REL)
            elif isEventList(field):
                for k in field:
                    enable_key(k)
            # otherwise treat as character
            else:
                set_evdev_key_for_char(field, '')

            # add modifier if present
            mod = config.get("modifier")
            if mod is not None:
                modifiers.add(mod)

        # enable all keys from the aggregated configuration
        for shortcuts in shortcuts_to_enable.values():
            for action, configs in shortcuts.items():
                if isinstance(configs, dict):
                    for sublist in configs.values():
                        if not isinstance(sublist, list):
                            sublist = [sublist]
                        for config in sublist:
                            process_config(config)
                else:
                    if not isinstance(configs, list):
                        configs = [configs]
                    for config in configs:
                        process_config(config)

        enable_config_macro_capabilities()

        # create the uinput device
        udev = dev.create_uinput_device()
        log.info("Virtual device initialized successfully.")

        sleep(0.5)

    except Exception as e:
        log.error(f"Error initializing virtual device: {e}")
        sys.exit(1)

gsettings_failure_count = 0
gsettings_max_failure_count = 1

qdbus_failure_count = 0
qdbus_max_failure_count = 1

if QDBUS is None:
    qdbus_failure_count = qdbus_max_failure_count

xinput_failure_count = 0
xinput_max_failure_count = 1

synclient_status_failure_count = 0
synclient_status_max_failure_count = 1

# Desktop-specific lookups of the focused window live in desktop/active_window.py.
active_window = ActiveWindow(ActiveWindowContext(
    session_type=xdg_session_type,
    x11_display=lambda: display,
    qdbus=QDBUS,
))

def get_active_window_title():
    return active_window.query()

GENERIC_DESKTOP_ID_WORDS = {"studio", "desktop", "app", "editor", "client", "player", "viewer"}

def profile_matches_window(profile_id, value):
    """Match a desktop id as well as the human app name supplied by AT-SPI."""
    if not value:
        return False
    profile_id = str(profile_id).lower()
    value = str(value).lower()
    stem = profile_id.removesuffix(".desktop")
    candidates = {profile_id, stem}
    # GNOME Shell gives ``org.darktable.darktable.desktop``, while
    # PyAT-SPI commonly supplies just ``Darktable``.  The final desktop-id
    # component is the stable bridge between the two forms.
    # A generic last component ("com.obsproject.Studio") would also match
    # unrelated apps such as LM Studio; the vendor part is used instead.
    if "." in stem:
        parts = stem.split(".")
        last_component = parts[-1]
        if last_component in GENERIC_DESKTOP_ID_WORDS and len(parts) >= 3:
            last_component = parts[-2]
        if len(last_component) >= 3 and last_component not in GENERIC_DESKTOP_ID_WORDS:
            candidates.add(last_component)
    return any(candidate and candidate in value for candidate in candidates)

def profile_exists_for_window(window_id):
    """Whether any profile of the configuration (an empty one too, which the shortcut
    sets leave out) is selected for this application."""
    if gui_config is None or not window_id:
        return False
    return any(
        profile.app_id and profile_matches_window(profile.app_id, window_id)
        for profile in gui_config.profiles
    )

def get_appropriate_app_name_and_shortcuts(window_binary, window_title):

    app_name = None

    # 1. window_binary (exact match)
    if window_binary:
        binary_lower = str(window_binary).lower()
        app_name = next(
            (app for app in app_shortcuts if app != "none" and profile_matches_window(app, binary_lower)),
            None
        )
    # 2. window_title (may be only part of)
    if not app_name and window_title:
        app_name = next(
            (app for app in app_shortcuts if app != "none" and profile_matches_window(app, window_title)),
            None,
        )

    # 3. not a specific app -> 'none' shortcuts block
    if not app_name:
        app_name = "none"

    shortcuts = app_shortcuts.get(app_name, app_shortcuts["none"])

    return app_name, shortcuts

def resolve_evdev_key(key):
    try:
        if key.isdigit():
            return EV_KEY.codes[int(key)]
        return getattr(EV_KEY, key)
    except (AttributeError, IndexError, KeyError, ValueError):
        raise MacroExecutionError("Unknown evdev key: %s" % key)

# Sends to the virtual device and its replacement (reset_udev_device) are
# serialised, so a key's press and release always reach the same device.
uinput_lock = threading.RLock()

def set_keys_held(keys, pressed, device=None):
    if not keys:
        return
    with uinput_lock:
        target = device or udev
        if not target:
            return
        try:
            for key in (keys if pressed else reversed(keys)):
                target.send_events([
                    InputEvent(key, 1 if pressed else 0),
                    InputEvent(EV_SYN.SYN_REPORT, 0)
                ])
        except Exception as e:
            log.error(f"Error sending modifier event: {e}")

# Keys held for scroll steps (Ctrl + wheel = zoom) stay pressed while the
# dial keeps turning, like a hand holding Ctrl on a mouse. Releasing them
# after every notch let applications that smooth scrolling (Firefox) see
# part of the wheel motion without Ctrl, so they zoomed and scrolled.
SCROLL_MODIFIER_RELEASE_SECONDS = 0.35
scroll_modifier_lock = threading.Lock()
scroll_modifiers_held = []
scroll_modifier_generation = 0

def release_scroll_modifiers(generation=None):
    global scroll_modifiers_held
    with scroll_modifier_lock:
        # A newer scroll step re-armed the release; this timer is stale.
        if generation is not None and generation != scroll_modifier_generation:
            return
        keys, scroll_modifiers_held = scroll_modifiers_held, []
        # A key a SpaceMouse button still holds stays down.
        set_keys_held([key for key in keys if key not in spacemouse_held_keys], False)

def hold_scroll_modifiers(keys):
    """Keep ``keys`` pressed and postpone their release; True if newly pressed."""
    global scroll_modifiers_held, scroll_modifier_generation
    with scroll_modifier_lock:
        scroll_modifier_generation += 1
        generation = scroll_modifier_generation
        newly_pressed = False
        if keys != scroll_modifiers_held:
            set_keys_held([key for key in scroll_modifiers_held if key not in spacemouse_held_keys], False)
            set_keys_held(keys, True)
            scroll_modifiers_held = list(keys)
            newly_pressed = bool(keys)
    if keys:
        timer = threading.Timer(
            SCROLL_MODIFIER_RELEASE_SECONDS, release_scroll_modifiers, args=(generation,)
        )
        timer.daemon = True
        timer.start()
    return newly_pressed

def send_wheel(codes, notches, high_resolution):
    """Send one wheel step as a single input frame."""
    if not udev:
        log.error("Virtual device is not initialized. Cannot send scroll events.")
        return
    events = []
    if notches:
        events.append(InputEvent(codes[0], notches))
    events.append(InputEvent(codes[1], high_resolution))
    events.append(InputEvent(EV_SYN.SYN_REPORT, 0))
    with uinput_lock:
        try:
            udev.send_events(events)
        except Exception as e:
            log.error(f"Error sending scroll event: {e}")

def execute_config_macro(steps, scroll_multiplier=1):
    if not CONFIG_APP_AVAILABLE:
        log.warning("Cannot execute configuration macro: config runtime is unavailable")
        return

    def send_keys(keys):
        resolved_keys = [resolve_evdev_key(key) for key in keys]
        send_key_event(resolved_keys[0] if len(resolved_keys) == 1 else resolved_keys)

    def scroll(direction, value, axis=None, held=None):
        sign = 1 if direction.lower() in ("clockwise", "up", "right") else -1
        multiplier = max(0.25, min(float(scroll_multiplier), 24))
        high_resolution = int(round(sign * abs(value) * multiplier))
        if high_resolution == 0:
            return
        # Whole notches for applications without high-resolution support.
        notches = int(high_resolution / 120)
        if axis == "horizontal":
            codes = [EV_REL.REL_HWHEEL, EV_REL.REL_HWHEEL_HI_RES]
        else:
            codes = [EV_REL.REL_WHEEL, EV_REL.REL_WHEEL_HI_RES]
        held_keys = [resolve_evdev_key(key) for key in held or []]
        if hold_scroll_modifiers(held_keys):
            # Let the compositor register the modifier before the wheel.
            sleep(0.02)
        send_wheel(codes, notches, high_resolution)

    def volume(direction, value):
        key = EV_KEY.KEY_VOLUMEUP if direction.lower() in ("up", "clockwise") else EV_KEY.KEY_VOLUMEDOWN
        for _ in range(max(1, min(abs(value), 20))):
            send_key_event(key)

    def brightness(direction, value):
        key = EV_KEY.KEY_BRIGHTNESSUP if direction.lower() in ("up", "clockwise") else EV_KEY.KEY_BRIGHTNESSDOWN
        for _ in range(max(1, min(abs(value), 20))):
            send_key_event(key)

    def media(value):
        send_keys([value] if isinstance(value, str) else value)

    def run_program(program, args):
        # Returns once the program ended or after a short wait (launch.py).
        launch.run([program, *args])

    try:
        MacroExecutor(
            send_keys=send_keys,
            scroll=scroll,
            media=media,
            brightness=brightness,
            volume=volume,
            run_program=run_program,
            run_shell=execute_command,
            sleep=sleep,
            run_app=run_app_command,
        ).execute(steps)
    except MacroExecutionError as error:
        log.error("Configuration macro failed: %s", error)
    except Exception:
        log.exception("Configuration macro execution failed")

def modifier_names(modifier):
    """Key names in a shortcut's "modifier": an evdev code, a name or a list of them."""
    if modifier is None:
        return set()
    items = modifier if isinstance(modifier, (list, tuple, set)) else [modifier]
    return {getattr(item, "name", str(item)) for item in items}

def shortcuts_for_modifiers(entries, held):
    """The entries bound to a held keyboard modifier, otherwise those without one.

    A function without a variant for the held key keeps working; the
    application sees the held key with it (Ctrl + scroll zooms).
    """
    entries = [entry for entry in entries if isinstance(entry, dict)]
    held_names = modifier_names(held)
    bound = [entry for entry in entries if modifier_names(entry.get("modifier")) & held_names]
    return bound or [entry for entry in entries if not entry.get("modifier")]

def emulate_shortcuts(app_shortcuts, touch_input, event_code, active_modifiers, duration_held=0, scroll_multiplier=1):
    global suppress_app_specifics_shortcuts

    matched_shortcuts = app_shortcuts.get(touch_input, [])
    if not isinstance(matched_shortcuts, list):
        matched_shortcuts = [matched_shortcuts]

    for shortcut in shortcuts_for_modifiers(matched_shortcuts, active_modifiers):
        key_code = shortcut.get("key")
        key_value = shortcut.get("value")
        command = shortcut.get("command")
        trigger_mode = shortcut.get("trigger", "release")
        modifier = shortcut.get("modifier")
        required_duration = shortcut.get("duration", 0)  # Default to 0 (immediate)

        if duration_held is None or duration_held >= required_duration:
            if (trigger_mode == "immediate" and event_code) or\
                (trigger_mode == "release" and not event_code) or\
                (touch_input == "center"):

                if "macro" in shortcut:
                    execute_config_macro(shortcut["macro"], scroll_multiplier)
                    if any(
                        getattr(step, "type", None) is StepType.SCROLL
                        for step in shortcut["macro"]
                    ):
                        log.debug("Executed accelerated scroll macro")
                    else:
                        log.info("Executed configuration macro with modifier %s", modifier)
                    return shortcut

                if key_code:
                    send_key_event(key_code, key_value)
                if command:
                    execute_command(command)

                log.info(f"Executed shortcut: {key_code} with value {key_value} with modifier {modifier} (Held for {duration_held:.2f}s)")
                return shortcut # Stop after first valid shortcut
        elif (trigger_mode == "immediate" and event_code) or (trigger_mode == "release" and not event_code):
            log.info(f"Shortcut {key_code} with value {key_value} requires {required_duration}s, but was held for {duration_held:.2f}s")

    return None

def shortcut_has_scroll_action(shortcuts, direction):
    """Return whether a rotation direction resolves to a config scroll macro."""
    entries = shortcuts.get(direction, []) if isinstance(shortcuts, dict) else []
    if not isinstance(entries, list):
        entries = [entries]

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        macros = entry.get("macro", [])
        if StepType is not None and any(
            getattr(step, "type", None) is StepType.SCROLL for step in macros
        ):
            return True
    return False

def send_key_event(key_code, key_value=None, log_event=True):
    with uinput_lock:
        _send_key_event(key_code, key_value, log_event)

def _send_key_event(key_code, key_value, log_event):
    if not udev:
        log.error("Virtual device is not initialized. Cannot send key events.")
        return

    try:
        if isinstance(key_code, list):
            if key_code and key_value:
                for c, v in zip(key_code, key_value):
                    if c.type == EV_REL:
                        udev.send_events([
                            InputEvent(c, v),
                            InputEvent(EV_SYN.SYN_REPORT, 0)
                        ])
            elif key_code:
                for c in key_code:
                    if c.type == EV_KEY:
                        udev.send_events([
                            InputEvent(c, 1),
                            InputEvent(EV_SYN.SYN_REPORT, 0)
                        ])
                for c in key_code:
                    if c.type == EV_KEY:
                        udev.send_events([
                            InputEvent(c, 0),
                            InputEvent(EV_SYN.SYN_REPORT, 0)
                        ])
        else:
            udev.send_events([
                InputEvent(key_code, 1),
                InputEvent(EV_SYN.SYN_REPORT, 0)
            ])
            udev.send_events([
                InputEvent(key_code, 0),
                InputEvent(EV_SYN.SYN_REPORT, 0)
         ])

        if log_event:
            log.info(f"Sent key {key_code, key_value}")

    except Exception as e:
        log.error(f"Error sending key event: {e}")

# Brightness of the DialPad light (backlight.py); None keeps the firmware's.
keyboard_backlight = None

def backlight_level():
    settings = gui_config.backlight if gui_config is not None else None
    if settings is None or settings.follow_keyboard:
        if keyboard_backlight is not None:
            keyboard_backlight.refresh()
            level = keyboard_backlight.level()
            if level is not None:
                return level
        if settings is None:
            return None
    return settings.level

def apply_backlight():
    level = backlight_level()
    if dialpad and level is not None:
        for command in commands_for_level(level):
            send_value_to_touchpad(command)

def activate_dialpad():
    global dialpad, multi_app_mode_titles, multi_app_mode_icons

    # A previous touch session must never leave the next activation in a
    # selected function with no visible way back to the menu.
    reset_center()

    # unlock
    send_value_to_touchpad("0x60")
    # activate
    send_value_to_touchpad("0x01")

    config_set(CONFIG_ENABLED, True)

    #set_touchpad_prop_tap_to_click(false)

    dialpad = True

    if emulation:
        set_emulated_dial_owns_touchpad(True)

    # Brightness reads the keyboard backlight over D-Bus: not on the touch thread.
    enqueue_action(apply_backlight)

    if overlay_enabled():
        send_to_socket({CONFIG_ENABLED: dialpad})

    show_initial_function_state()

def deactivate_dialpad():
    global dialpad

    # Discard any selected radial function before the next activation.
    reset_center()

    # lock
    send_value_to_touchpad("0x61")
    # deactivate
    send_value_to_touchpad("0x00")

    config_set(CONFIG_ENABLED, False)

    dialpad = False

    if emulation:
        set_emulated_dial_owns_touchpad(False)

    if overlay_enabled():
        send_to_socket({CONFIG_ENABLED: dialpad})

def set_emulated_dial_owns_touchpad(owns):
    """While the emulated dial is on, the whole touchpad is the dial.

    The grab keeps every touch from the compositor at once; libinput is
    also switched off, so when it gets the touchpad back it starts from the
    fingers really on it, not from the ones it saw before the grab.
    """
    try:
        if owns:
            d_t.grab()
        else:
            d_t.ungrab()
    except Exception:
        log.warning("Could not %s the touchpad", "grab" if owns else "release", exc_info=True)
    # gsettings, qdbus or xinput run as programs: not on the touch thread.
    enqueue_action(lambda: set_touchpad_prop_send_events(0 if owns else 1))

# Function to enable/disable the DialPad
def toggle_top_right_icon(current_state_is_enabled):

    if current_state_is_enabled:
        deactivate_dialpad()
    else:
        activate_dialpad()

    log.info(f"Toggling top-right icon: {'Disabling' if current_state_is_enabled else 'Enabling'}")

def handle_rotation(direction):
    """
    Handle rotation events based on the detected direction.
    :param direction: "clockwise" or "counterclockwise"
    """
    log.info(f"Handling rotation: {direction}")
    if direction == "clockwise":
        # Example: Increase volume
        log.info("Volume up triggered")
        send_key_event(EV_KEY.KEY_VOLUMEUP)  # Replace with your specific action
    elif direction == "counterclockwise":
        # Example: Decrease volume
        log.info("Volume down triggered")
        send_key_event(EV_KEY.KEY_VOLUMEDOWN)  # Replace with your specific action

def is_pressed_touchpad_top_right_icon():
    global top_right_icon_width, top_right_icon_height, abs_mt_slot_x_values, abs_mt_slot_y_values, abs_mt_slot_value, maxx

    if abs_mt_slot_x_values[abs_mt_slot_value] >= maxx - top_right_icon_width and\
        abs_mt_slot_y_values[abs_mt_slot_value] >= 0 and abs_mt_slot_y_values[abs_mt_slot_value] <= top_right_icon_height:
            return True

    return False

def check_dialpad_automatical_disable_or_idle_due_inactivity():
    global disable_due_inactivity_time, last_event_time, dialpad, stop_threads

    while not stop_threads:

        if\
            disable_due_inactivity_time and\
            dialpad and\
            last_event_time != 0 and\
            time() > disable_due_inactivity_time + last_event_time:

            deactivate_dialpad()
            log.info("DialPad deactivated")

        sleep(1)

def qdbusSet(cmd):
    global qdbus_failure_count, qdbus_max_failure_count

    if qdbus_failure_count < qdbus_max_failure_count:
        try:
            sudo_user = os.environ.get('SUDO_USER')

            if sudo_user is not None:
                final_cmd = ['runuser', '-u', sudo_user] + cmd
            else:
                final_cmd = cmd

            log.debug(final_cmd)
            ret = subprocess.call(final_cmd)

            if ret != 0:
                raise subprocess.CalledProcessError(ret, final_cmd)
            return True

        except Exception as e:
            log.debug(e, exc_info=True)
            qdbus_failure_count+=1
    else:
        log.debug('Qdbus failed more then: \"%s\" so is not try anymore', qdbus_max_failure_count)
    return False

def qdbusSetTouchpadEnabled(value):
    cmd = [
        QDBUS,
        'org.kde.KWin',
        f'/org/kde/KWin/InputDevice/event{touchpad}',
        'org.freedesktop.DBus.Properties.Set',
        'org.kde.KWin.InputDevice',
        'enabled',
        str(bool(value)).lower()
    ]
    return qdbusSet(cmd)

def gsettingsSet(path, name, value):
    global gsettings_failure_count, gsettings_max_failure_count

    if gsettings_failure_count < gsettings_max_failure_count:
        try:
            sudo_user = os.environ.get('SUDO_USER')
            if sudo_user is not None:
                cmd = ['runuser', '-u', sudo_user, 'gsettings', 'set', path, name, str(value)]
            else:
                cmd = ['gsettings', 'set', path, name, str(value)]

            log.debug(cmd)
            ret = subprocess.call(cmd)
            if ret != 0:
                raise subprocess.CalledProcessError(ret, cmd)
            return True
        except Exception as e:
            log.debug(e, exc_info=True)
            gsettings_failure_count+=1
    else:
        log.debug('Gsettings failed more than: "%s" so is not trying anymore', gsettings_max_failure_count)
    return False

def gsettingsSetTouchpadSendEvents(value):
    return gsettingsSet('org.gnome.desktop.peripherals.touchpad', 'send-events', 'enabled' if value else 'disabled')

def set_touchpad_prop_send_events(value):
    global touchpad_name, xinput_failure_count, xinput_max_failure_count, synclient_status_failure_count, synclient_status_max_failure_count

    # 1. priority - gsettings (gnome) or qdbus (kde)
    if gsettings_failure_count < gsettings_max_failure_count and gsettingsSetTouchpadSendEvents(value):
        return
    if qdbus_failure_count < qdbus_max_failure_count and qdbusSetTouchpadEnabled(value):
        return

    # 2. priority - xinput
    if xinput_failure_count >= xinput_max_failure_count:
        log.debug('Setting libinput Send Events via xinput failed more than: "%s" times so is not trying anymore', xinput_max_failure_count)
    else:
        try:
            cmd = ["xinput", "enable" if value else "disable", touchpad_name]
            log.debug(cmd)
            ret = subprocess.call(cmd)
            if ret != 0:
                raise subprocess.CalledProcessError(ret, cmd)
            return
        except Exception:
            xinput_failure_count+=1
            log.debug('Setting libinput Send Events via xinput failed')

    # 3. priority - synclient
    if synclient_status_failure_count >= synclient_status_max_failure_count:
        log.debug('Setting libinput Send Events via synclient failed more than: "%s" times so is not trying anymore', synclient_status_max_failure_count)
        return
    try:
        cmd = ["synclient", "TouchpadOff=" + str(0 if value else 1)]
        log.debug(cmd)
        ret = subprocess.call(cmd)
        if ret != 0:
            raise subprocess.CalledProcessError(ret, cmd)
    except Exception:
        synclient_status_failure_count+=1


def are_modifier_keys_pressed(modifier_names):

    global display, keyboard_state, xkb_conn, active_modifiers

    if not modifier_names:
        return True

    # wayland
    if keyboard_state:

        try:
            for modifier_name in modifier_names:
                modifier_keysym_name = mod_name_to_specific_keysym_name(modifier_name)
                modifier_evdev_key = load_evdev_key_for_wayland(modifier_keysym_name, keyboard_state)
                if modifier_evdev_key not in active_modifiers:
                    log.debug("Modifier %s not pressed (evdev)", modifier_name)
                    return False
        except:
            pass

    # x11
    if xkb_conn and X11_LIBS_AVAILABLE:

        X11_MODIFIER_INDEX = {
            "Shift": 0,
            "Control": 2,
            "Alt": 3
        }

        try:
            reply = xkb_conn(xcffib.xkb.key).GetState(
                xcffib.xkb.ID.UseCoreKbd
            ).reply()

            active_mods = reply.mods

            for modifier_name in modifier_names:
                idx = X11_MODIFIER_INDEX.get(modifier_name)
                if idx is not None and not (active_mods & (1 << idx)):
                    log.debug("Modifier %s not pressed (xcffib)", modifier_name)
                    return False

        except Exception as e:
            log.exception(f"Failed to get X11 modifier state via XKB: {e}")

    log.debug("All modifier keys pressed: %s", modifier_names)

    return True

# Store key press start times
key_press_times = {}

import subprocess

def get_current_value(shortcut_entry):
    value = shortcut_entry.get("value")
    if value:
        try:
            result = subprocess.check_output(value, shell=True).decode().strip()
            return result
        except Exception as e:
            log.debug(f"Failed to get value for shortcut: {e}")
            return None
    return None

def get_current_value_unit(shortcut_entry):
    value = shortcut_entry.get("unit")
    return value

def execute_command(command):
    # A program started this way keeps running without blocking the dial.
    launch.run_shell(command)

def reset_center():
    global center_activated, title, treshold, submenu, submenu_index

    center_activated = False
    title = None
    treshold = None
    submenu = None
    submenu_index = 0

def show_pointer_enabled():
    overlay = getattr(gui_config, "overlay", None)
    return bool(getattr(overlay, "show_pointer", True)) and bool(overlay_enabled())

def profile_meta():
    shortcuts = globals().get("app_specific_shortcuts")
    meta = shortcuts.get(META_KEY) if isinstance(shortcuts, dict) else None
    return meta if isinstance(meta, dict) else {}

def single_function_settings():
    """Settings of the bound function in single-function mode."""
    single = profile_meta().get("single")
    return single if isinstance(single, dict) else {}

def profile_float(key, default=1.0):
    try:
        return max(0.25, min(float(profile_meta().get(key, default)), 4.0))
    except (TypeError, ValueError):
        return default

def group_settings(group_title=None):
    """The menu entry of a group, the open one by default; {} if it is none."""
    group_title = group_title or submenu
    group = app_specific_shortcuts.get(group_title) if multi_app_mode and group_title else None
    return group if isinstance(group, dict) and isinstance(group.get("items"), dict) else {}

def is_group(function_title):
    return bool(function_title) and bool(group_settings(function_title))

def submenu_titles():
    return list(group_settings().get("items", {})) if submenu else []

def function_entry(function_title):
    """A function's shortcuts: from the open group's list, else from the menu."""
    entries = group_settings().get("items") if submenu else app_specific_shortcuts
    entry = entries.get(function_title) if isinstance(entries, dict) and function_title else None
    return entry if isinstance(entry, dict) else {}

def function_settings(function_title):
    """Return the segment dictionary that defines a function's metadata."""
    if multi_app_mode:
        return function_entry(function_title)
    return single_function_settings()

def menu_colors():
    colors = []
    for menu_title in multi_app_mode_titles or []:
        segment = app_specific_shortcuts.get(menu_title) if menu_title else None
        colors.append(segment.get("color") or "" if isinstance(segment, dict) else "")
    return colors

def send_details(**details):
    if overlay_enabled():
        send_to_socket({"details": details})

def overlay_look(function_title=None):
    """How the on-screen dial shows: the profile's overrides, then the
    selected function's; the extension applies them over config defaults."""
    look = {}
    sources = [profile_meta()]
    if submenu:
        sources.append(group_settings())
    if function_title is not None:
        sources.append(function_settings(function_title))
    for source in sources:
        if isinstance(source.get("overlay"), dict):
            look.update(source["overlay"])
    return look

def send_function_details(function_title):
    settings = function_settings(function_title)
    hints = settings.get("hints") if isinstance(settings.get("hints"), dict) else {}
    send_details(
        profile=profile_meta().get("name") or "",
        mode="multi" if multi_app_mode else "single",
        color=settings.get("color") or "",
        hints=hints,
        hold_returns=bool(multi_app_mode),
        pointer=-1,
        look=overlay_look(function_title),
        # A function of a group is not in the menu: its icon comes along,
        # and holding the centre goes back to the group's list.
        icon=settings.get("icon") or "",
        parent=submenu or "",
        submenu=None,
    )

def show_multifunction_menu():
    if overlay_enabled():
        send_details(
            profile=profile_meta().get("name") or "",
            mode="multi",
            colors=menu_colors(),
            color="",
            hints={},
            pointer=-1,
            look=overlay_look(),
            parent="",
            submenu=None,
        )
        send_to_socket({
            "titles": multi_app_mode_titles,
            "icons": multi_app_mode_icons,
            "title": "Menu",
        })

def show_submenu():
    """The open group's list beside the menu; the highlighted function is
    the title, so the centre (and an older overlay) shows it."""
    global title, submenu_index

    items = group_settings().get("items", {})
    titles = list(items)
    if not titles:
        return_to_multifunction_menu()
        return
    submenu_index = max(0, min(submenu_index, len(titles) - 1))
    title = titles[submenu_index]
    if overlay_enabled():
        menu_titles = multi_app_mode_titles or []
        # No pointer here: the touch loop moves the finger dot meanwhile.
        send_details(
            profile=profile_meta().get("name") or "",
            mode="multi",
            colors=menu_colors(),
            color=items[title].get("color") or "",
            hints={},
            look=overlay_look(),
            parent="",
            submenu={
                "title": submenu,
                "index": menu_titles.index(submenu) if submenu in menu_titles else -1,
                "items": titles,
                "icons": [entry.get("icon") or "" for entry in items.values()],
                "colors": [entry.get("color") or "" for entry in items.values()],
                "selected": submenu_index,
            },
        )
        send_to_socket({
            "titles": multi_app_mode_titles,
            "icons": multi_app_mode_icons,
            "title": title,
        })

def open_submenu(group_title, index=0):
    """Open a group's list: turning moves the highlight, a tap on the centre
    chooses, holding it goes back to the menu."""
    global center_activated, submenu, submenu_index

    center_activated = False
    submenu = group_title
    submenu_index = index
    show_submenu()
    log.info("Opened the DialPad list of %s", group_title)

def move_submenu_highlight(direction):
    """One step along the list; it stops at both ends."""
    global submenu_index

    step = 1 if direction == "clockwise" else -1
    index = max(0, min(len(submenu_titles()) - 1, submenu_index + step))
    if index == submenu_index:
        return False
    submenu_index = index
    show_submenu()
    return True

def enter_function(function_title, query_value=True):
    """Bind the dial to one function of the current multi-function profile."""
    global center_activated, title

    selected_shortcuts = function_entry(function_title)
    center_activated = True
    title = function_title
    if app_name and profile_meta().get("remember_last", True):
        last_function_by_app[function_memory_key()] = (submenu, function_title)
    if overlay_enabled():
        send_function_details(function_title)
        send_to_socket({
            "titles": [],
            "icons": [],
            "value": 0,
            "unit": "",
            "title": function_title,
        })
    if query_value and dialpad:
        enqueue_action(lambda: send_function_value(selected_shortcuts, function_title))
    return True

def show_single_function():
    global title

    settings = single_function_settings()
    title = settings.get("title") or None
    if overlay_enabled():
        send_function_details(title)
        send_to_socket({"titles": [], "icons": [], "value": 0, "unit": "", "title": title or ""})
    if title and dialpad and settings.get("value"):
        function_title = title
        enqueue_action(lambda: send_function_value(settings, function_title))

def function_memory_key():
    """What the last used function is remembered under: the profile, and the
    context in force when it has one."""
    return (app_name, context_name) if context_name else app_name

def remembered_function():
    """(group, function) to reopen for this application, or None."""
    if not multi_app_mode or not profile_meta().get("remember_last", True):
        return None
    remembered = last_function_by_app.get(function_memory_key())
    if not isinstance(remembered, tuple) or len(remembered) != 2:
        return None
    group, function_title = remembered
    menu_titles = multi_app_mode_titles or []
    if group is None:
        segment = app_specific_shortcuts.get(function_title) if function_title in menu_titles else None
    else:
        segment = group_settings(group).get("items", {}).get(function_title) if group in menu_titles else None
    if isinstance(segment, dict) and (segment.get("clockwise") or segment.get("counterclockwise")):
        return remembered
    return None

def show_initial_function_state():
    """Show the menu, the remembered function or the single bound function."""
    global submenu

    if multi_app_mode:
        remembered = remembered_function()
        reset_center()
        if remembered:
            submenu = remembered[0]
            enter_function(remembered[1])
        else:
            show_multifunction_menu()
    elif single_function_settings():
        reset_center()
        show_single_function()

# Actions (macros, shell commands, value queries) can take tens of
# milliseconds. They run on one worker thread in order, so the touchpad
# loop never stalls and the dial stays smooth.
ACTION_QUEUE_SIZE = 32
action_queue = queue.Queue(maxsize=ACTION_QUEUE_SIZE)

def action_worker():
    while not stop_threads:
        job = action_queue.get()
        if job is None:
            break
        try:
            job()
        except Exception:
            log.exception("DialPad action failed")

def enqueue_action(job, required=False):
    try:
        action_queue.put_nowait(job)
    except queue.Full:
        if required:
            threading.Thread(target=job, name="dialpad-actions-overflow", daemon=True).start()
        else:
            log.warning("DialPad action queue is full; dropping an action")

def send_function_value(shortcuts, function_title):
    """Query a function's current value off the touch thread and show it."""
    value = get_current_value(shortcuts)
    if overlay_enabled() and value is not None:
        send_to_socket({
            "value": value,
            "unit": get_current_value_unit(shortcuts),
            "title": function_title,
        })

def run_rotation_action(shortcuts, direction, modifiers, scroll_multiplier, function_title, show_value, value_source=None):
    shortcut = emulate_shortcuts(
        shortcuts,
        direction,
        1,
        modifiers,
        scroll_multiplier=scroll_multiplier,
    )
    if not shortcut or not show_value or not overlay_enabled():
        return

    value_source = value_source if value_source is not None else shortcuts
    value = get_current_value(shortcut)
    unit = get_current_value_unit(shortcut)
    if value is None:
        value = get_current_value(value_source)
    if unit is None:
        unit = get_current_value_unit(value_source)

    send_to_socket({
        "input": direction,
        "value": value,
        "unit": unit,
        "title": shortcut.get("title", function_title) if isinstance(shortcut, dict) else function_title,
    })

def run_center_action(shortcuts, modifiers, duration_held, function_title):
    shortcut = emulate_shortcuts(
        shortcuts,
        "center",
        0,
        modifiers,
        duration_held,
    )
    if not shortcut:
        return
    selected_shortcuts = shortcuts.get(function_title, {})
    command = (
        selected_shortcuts.get("command")
        if isinstance(selected_shortcuts, dict)
        else None
    )
    if command:
        execute_command(command)

def run_menu_action(selected_shortcuts, modifiers, selected_title):
    if emulate_shortcuts(selected_shortcuts, "center", 0, modifiers):
        log.info("Executed DialPad menu action %s", selected_title)

def run_function_center_action(selected_shortcuts, modifiers, selected_title):
    if emulate_shortcuts(selected_shortcuts, "center", 0, modifiers):
        log.info("Executed centre action of %s", selected_title)
        send_function_value(selected_shortcuts, selected_title)

def function_has_center_action(function_title):
    center = function_entry(function_title).get("center") or []
    return bool(shortcuts_for_modifiers(center if isinstance(center, list) else [center], active_modifiers))

def select_multifunction_segment():
    """Select the highlighted radial-menu segment or invoke an action-only one."""
    global center_activated, title

    # A final touch-release frame can arrive after the DialPad was disabled.
    # It must not select a function in the next session.
    if not dialpad or not multi_app_mode or not title or title == "Menu":
        return False

    # A group opens its list at once; one level only, so not inside a list.
    if not submenu and is_group(title):
        open_submenu(title)
        return True

    selected_shortcuts = function_entry(title)
    has_rotation = bool(
        selected_shortcuts.get("clockwise")
        or selected_shortcuts.get("counterclockwise")
    )
    if not has_rotation:
        # Action-only segments (for example Mute or Notifications) are
        # invoked by tapping their sector and then stay in the menu, or in
        # the list they were chosen from.
        selected_title = title
        if submenu:
            show_submenu()
        else:
            title = None
            show_multifunction_menu()
        modifiers = set(active_modifiers)
        enqueue_action(lambda: run_menu_action(selected_shortcuts, modifiers, selected_title))
        return True

    selected_title = title
    enter_function(selected_title)
    log.info("Selected DialPad function %s; hold the center to return to the menu", selected_title)
    return True

def return_to_multifunction_menu():
    selected_title = title
    reset_center()
    # Leaving a function on purpose means the next activation opens the menu.
    last_function_by_app.pop(function_memory_key(), None)
    show_multifunction_menu()
    log.info("Returned to the DialPad menu from %s", selected_title)

def leave_function():
    """One level back: from a function of a group to its list, from
    anything else to the menu."""
    if submenu and center_activated:
        titles = submenu_titles()
        last_function_by_app.pop(function_memory_key(), None)
        open_submenu(submenu, titles.index(title) if title in titles else 0)
    else:
        return_to_multifunction_menu()

def release_center(duration_held, hold_fired):
    """The centre button is let go: a finger lifted from it, or the mouse's
    middle button released. A hold already went back (hold_fired)."""
    if multi_app_mode:
        if hold_fired:
            return
        if center_activated:
            # Tap runs the function's own centre action
            # (for example "Fit image"); a long press or a
            # function without one returns to the menu.
            if duration_held < center_hold_time and function_has_center_action(title):
                selected_shortcuts = function_entry(title)
                function_title = title
                modifiers = set(active_modifiers)
                enqueue_action(
                    lambda selected_shortcuts=selected_shortcuts, modifiers=modifiers, function_title=function_title:
                        run_function_center_action(selected_shortcuts, modifiers, function_title)
                )
            else:
                leave_function()
        else:
            select_multifunction_segment()
    else:
        shortcuts = app_specific_shortcuts
        function_title = title
        modifiers = set(active_modifiers)
        enqueue_action(
            lambda shortcuts=shortcuts, modifiers=modifiers, duration_held=duration_held, function_title=function_title:
                run_center_action(shortcuts, modifiers, duration_held, function_title)
        )

def show_menu_after_gesture():
    """Back to showing the menu (or the open list) when no function is selected."""
    if multi_app_mode and not center_activated:
        if submenu:
            show_submenu()
        else:
            show_multifunction_menu()

def queue_rotation_action(shortcuts, direction, scroll_multiplier, function_title, show_value, value_source):
    """One turning step of the selected function, run on the action thread."""
    modifiers = set(active_modifiers)
    enqueue_action(
        lambda: run_rotation_action(shortcuts, direction, modifiers, scroll_multiplier, function_title, show_value, value_source)
    )

def rotate_selected_function(direction):
    """A turning step without the touch loop's threshold (the mouse wheel's notch)."""
    settings = function_settings(title)
    shortcuts = function_entry(title) if center_activated else {}
    if not shortcuts:
        shortcuts = app_specific_shortcuts
    scroll_multiplier = profile_float("scroll_speed") if shortcut_has_scroll_action(shortcuts, direction) else 1
    queue_rotation_action(shortcuts, direction, scroll_multiplier, title, True, settings if settings else shortcuts)

def rotate_menu_highlight(direction):
    """One step along the radial menu, wrapping around; empty sectors are skipped."""
    global title

    titles = multi_app_mode_titles or []
    filled = [index for index, menu_title in enumerate(titles) if menu_title]
    if not filled:
        return False
    step = 1 if direction == "clockwise" else -1
    if title and title in titles:
        index = filled[(filled.index(titles.index(title)) + step) % len(filled)]
    else:
        index = filled[0] if step > 0 else filled[-1]
    title = titles[index]
    if overlay_enabled():
        send_to_socket({"titles": multi_app_mode_titles, "icons": multi_app_mode_icons, "title": title})
    return True

def highlight_menu_slice(slice_idx):
    """Highlight the radial menu's sector ``slice_idx`` (a finger's slice, or
    the mouse pointer's)."""
    global title

    try:
        title = multi_app_mode_titles[slice_idx]
    except IndexError:
        title = None

    if overlay_enabled():
        send_to_socket({"titles": multi_app_mode_titles, "icons": multi_app_mode_icons, "title": title})

def turn_selected_function(rotation, angle):
    """The finger's (or the mouse pointer's) angle in a selected function or
    the single function: the tracker's threshold crossings run its turning
    actions, the progress goes to the overlay."""
    global treshold

    settings = function_settings(title)
    try:
        treshold = int(settings["treshold"])
    except (KeyError, TypeError, ValueError):
        treshold = default_treshold

    # A scroll uses a smaller angular step for smooth low-speed
    # control.  Other actions retain their configured threshold.
    sht = function_entry(title) if center_activated else {}
    if not sht:
        sht = app_specific_shortcuts
    is_scroll_action = shortcut_has_scroll_action(sht, "clockwise") or shortcut_has_scroll_action(sht, "counterclockwise")
    trigger_threshold = min(treshold, SCROLL_TRIGGER_DEGREES) if is_scroll_action else treshold
    # Per-application sensitivity, as in ASUS Dial settings.
    trigger_threshold = max(2, trigger_threshold / profile_float("sensitivity"))
    show_progress = trigger_threshold >= socket_send_progress_above_treshold

    previous_progress = rotation.progress(trigger_threshold)
    triggered = rotation.update(angle, time(), trigger_threshold)

    if triggered is None:
        progress = rotation.progress(trigger_threshold)
        if show_progress and overlay_enabled() and abs(progress - previous_progress) >= 3:
            send_to_socket({
                "value_angle_start": rotation.start_angle or 0.0,
                "value": progress,
                "title": title,
                "value_show_only_progress": True
            })
    else:
        direction, degrees_per_second = triggered
        scroll_multiplier = (
            scroll_acceleration_multiplier(degrees_per_second)
            * profile_float("scroll_speed")
            if shortcut_has_scroll_action(sht, direction)
            else 1
        )
        log.debug(
            "Threshold crossed (%s) at %.0f°/s, scroll multiplier x%d",
            direction,
            degrees_per_second,
            scroll_multiplier,
        )

        if show_progress and overlay_enabled():
            send_to_socket({
                "value_angle_start": rotation.start_angle or 0.0,
                "value": 0,
                "title": title,
                "value_show_only_progress": True
            })

        queue_rotation_action(
            sht, direction, scroll_multiplier, title,
            not show_progress, settings if settings else sht,
        )

def rotate_dial(direction):
    """One step of the dial for the mouse wheel: along the menu, along an
    open list, or turning the selected function."""
    if multi_app_mode and submenu and not center_activated:
        move_submenu_highlight(direction)
    elif multi_app_mode and not center_activated:
        rotate_menu_highlight(direction)
    else:
        rotate_selected_function(direction)

def go_back():
    """One level back: out of a function or a list (the mouse's right button,
    the SpaceMouse's back action); nothing in the plain menu."""
    if multi_app_mode and (center_activated or submenu):
        leave_function()
        return True
    return False

def tap_center():
    """A tap on the centre button, on what is highlighted (a SpaceMouse
    button let go after tilting)."""
    if not dialpad or not title:
        return False
    release_center(0.0, False)
    show_menu_after_gesture()
    return True

def open_group_list(group_title=None):
    """Open the list of a group of the current profile's menu (the first one by default)."""
    if not (dialpad and multi_app_mode):
        return False
    groups = [entry for entry in (multi_app_mode_titles or []) if entry and is_group(entry)]
    target = group_title if group_title in groups else (groups[0] if groups and not group_title else None)
    if target is None:
        return False
    open_submenu(target)
    return True

def enter_named_function(function_title=None, slot=None):
    """Enter a function of the current profile by name, or by its place in the
    menu (``slot``, from 0; a group opens its list). Functions inside groups
    are found too."""
    global title

    if not (dialpad and multi_app_mode):
        return False
    titles = [entry for entry in (multi_app_mode_titles or []) if entry]
    if function_title is None:
        if slot is None or slot >= len(titles):
            return False
        function_title = titles[slot]
    if function_title in titles:
        reset_center()
        title = function_title
        return select_multifunction_segment()
    for group in titles:
        items = group_settings(group).get("items") if is_group(group) else None
        if items and function_title in items:
            open_submenu(group, list(items).index(function_title))
            title = function_title
            return select_multifunction_segment()
    return False

def spacemouse_layer_maps():
    """The button layers in force: the global ones with the current profile's
    over them, per layer (name -> button map)."""
    layers = {}
    if gui_config is not None:
        layers = {name: dict(buttons) for name, buttons in gui_config.spacemouse.layers.items()}
    profile_layers = profile_meta().get("spacemouse_layers")
    if isinstance(profile_layers, dict) and profile_layers:
        layers = merge_spacemouse_layers(layers, profile_layers)
    return layers

def spacemouse_effective_buttons(layer=""):
    """The map in force: the global one with the current profile's buttons over it
    (a profile's binding replaces the global one of that button, ``none`` included;
    an entry that only has a label just names the binding below it). With a
    ``layer`` active, that layer's buttons lie over the result in the same way."""
    buttons = {}
    if gui_config is not None and spacemouse_buttons_for is not None:
        buttons.update(spacemouse_buttons_for(gui_config))
    profile_buttons = profile_meta().get("spacemouse_buttons")
    if isinstance(profile_buttons, dict):
        buttons = merge_spacemouse_buttons(buttons, profile_buttons) if merge_spacemouse_buttons is not None else {**buttons, **profile_buttons}
    if layer:
        layer_buttons = spacemouse_layer_maps().get(layer)
        if layer_buttons:
            buttons = merge_spacemouse_buttons(buttons, layer_buttons)
    return buttons

def spacemouse_binding(index, layer=""):
    """The actions of a SpaceMouse button: the profile's, else the global map's,
    with the active ``layer`` over both."""
    return spacemouse_effective_buttons(layer).get(str(index))

def spacemouse_bound_buttons():
    """Every button Turnpad acts on with the current profile: in the base map or in
    any of the layers (those are hidden from other programs with hide_buttons)."""
    bound = {int(key) for key, binding in spacemouse_effective_buttons().items() if spacemouse_binds(binding)}
    for layer in spacemouse_layer_maps():
        bound.update(int(key) for key, binding in spacemouse_effective_buttons(layer).items() if spacemouse_binds(binding))
    return bound

def spacemouse_source():
    """The connected SpaceMouse source of the input loop, if any."""
    return next(
        (source for source in globals().get("input_sources", []) if source.name == "spacemouse" and source.connected),
        None,
    )

def spacemouse_action_keys(action):
    """The virtual keyboard keys a modifier or key_hold action holds, in press order."""
    names = []
    if action.type == "modifier" and SCHEMA_MODIFIER_KEYS and action.modifier in SCHEMA_MODIFIER_KEYS:
        names = SCHEMA_MODIFIER_KEYS[action.modifier][:1]
    elif action.type == "key_hold":
        names = action.keys or []
    keys = []
    for name in names:
        key = getattr(EV_KEY, name, None) if isinstance(name, str) else None
        if key is not None and getattr(key, "type", None) == EV_KEY and key not in keys:
            keys.append(key)
    return keys

def run_spacemouse_action(action):
    """Run one SpaceMouse action (a modifier is held by the input loop)."""
    kind = action.type
    if kind in ("macro", "app"):
        steps = action.steps()
        if steps:
            enqueue_action(lambda: execute_config_macro(steps))
    elif kind == "dial":
        toggle_top_right_icon(dialpad)
    elif kind in ("group", "function"):
        if not dialpad:
            toggle_top_right_icon(dialpad)
        if kind == "group":
            open_group_list(action.group)
        else:
            enter_named_function(action.function, action.slot)
    elif kind == "select":
        tap_center()
    elif kind == "back":
        go_back()

def control_button_action(index, layer=""):
    """The press action Control's ``button:INDEX`` runs: the effective binding's
    press (in the active ``layer``), unless it only holds keys or leaves the button
    alone; a hold layer has no button to hold."""
    binding = spacemouse_binding(index, layer)
    action = binding.press if binding is not None else None
    if action is None or action.type in ("none", "modifier", "key_hold"):
        return None
    if action.type == "layer" and action.layer_mode in (None, "hold"):
        return None
    return action

def apply_control(command):
    """Run one parsed Control command (dialpad_dbus.parse_control) on the input
    loop, where the SpaceMouse buttons run their actions."""
    verb, argument = command
    if verb == "toggle":
        toggle_top_right_icon(dialpad)
    elif verb == "dial-on":
        if not dialpad:
            toggle_top_right_icon(dialpad)
    elif verb == "dial-off":
        if dialpad:
            toggle_top_right_icon(dialpad)
    elif verb == "rotate":
        if dialpad:
            for _ in range(abs(argument)):
                rotate_dial("clockwise" if argument > 0 else "counterclockwise")
    elif verb == "select":
        tap_center()
    elif verb == "back":
        go_back()
    elif verb == "function":
        if not dialpad:
            toggle_top_right_icon(dialpad)
        enter_named_function(slot=argument)
    elif verb == "function-title":
        if not dialpad:
            toggle_top_right_icon(dialpad)
        enter_named_function(argument)
    elif verb == "group":
        if not dialpad:
            toggle_top_right_icon(dialpad)
        open_group_list(argument)
    elif verb == "button":
        source = spacemouse_source()
        action = control_button_action(argument, source.layers.active if source is not None else "")
        if action is not None:
            if action.type in ("layer", "motion"):
                # Their state lives in the SpaceMouse source.
                if source is not None:
                    source.run_state_action(argument, action)
            else:
                run_spacemouse_action(action)

window_binary = None

def update_touch_slots(slots, current_slot, event):
    """Apply one multi-touch event to ``slots``; return the current slot."""
    if event.matches(EV_ABS.ABS_MT_SLOT):
        return event.value
    if event.matches(EV_ABS.ABS_MT_TRACKING_ID):
        if event.value == -1:
            slots.pop(current_slot, None)
        else:
            # A new tracking id is a new finger: start from a clean state.
            slots[current_slot] = {'tracking_id': event.value}
        return current_slot
    for code, key in (
        (EV_ABS.ABS_MT_POSITION_X, 'x'),
        (EV_ABS.ABS_MT_POSITION_Y, 'y'),
        (EV_ABS.ABS_MT_TOOL_TYPE, 'tool'),
    ):
        if event.matches(code):
            slots.setdefault(current_slot, {})[key] = event.value
            break
    return current_slot

def listen_touchpad_events():
    global slices_minimum_count, activation_time, last_event_time, dialpad, active_modifiers, coactivator_keys, title, center_activated, treshold, multi_app_mode_titles, app_specific_shortcuts, window_binary

    DEBOUNCE_MS = 30
    SETTLE_FRAMES = 3
    # The overlay moves its finger dot without repainting, so small steps
    # cost almost nothing; at most one update per touch frame is sent.
    POINTER_STEP_DEGREES = 1

    log.info("Listening to touchpad events...")

    finger_key = EV_KEY.BTN_TOUCH if d_t.has(EV_KEY.BTN_TOUCH) else EV_KEY.BTN_TOOL_FINGER

    # Frame buffer state. The dial follows one finger (by its tracking id),
    # so a palm or a second hand elsewhere on the pad neither takes over the
    # dial nor hides the moment that finger lifts.
    slots = {}
    current_slot = 0
    finger_down = False
    dial_touch_id = None

    # Gesture state
    touch_start_time = None
    within_top_right_icon = False
    icon_activated = False
    last_slice = None
    center_button_triggered = False
    tap_disabled = False
    touching_sent = False
    coactivator_blocked = False
    center_enter_time = 0
    center_hold_fired = False
    last_pointer_angle = None
    title = None
    center_activated = False
    treshold = None
    first_touch_outside = None
    # Emulation: a touch that starts on the corner only switches the dial.
    corner_touch = False
    settle_counter = 0
    prev_finger_down = False
    rotation = RotationTracker(angle_hysteresis, DEBOUNCE_MS)

    while not stop_threads:
        try:
            for event in d_t.events():

                last_event_time = time()

                if event.matches(EV_SYN.SYN_REPORT):
                    process_pending_driver_updates(gesture_idle=not prev_finger_down)
                    rotation.hysteresis = angle_hysteresis
                    circle_radius = circle_diameter / 2
                    center_button_radius = center_button_diameter / 2
                    top_right_icon_bounds = {
                        "x_min": max_x - top_right_icon_width,
                        "x_max": max_x,
                        # min_y: other makes' touchpads may not start at 0.
                        "y_min": min_y,
                        "y_max": min_y + top_right_icon_height
                    }

                    # The emulated dial can do without the corner (a shortcut
                    # switches it); it then owns the whole touchpad while on.
                    corner_enabled = emulation_corner or not emulation

                    def on_corner(x, y):
                        return corner_enabled and (
                            top_right_icon_bounds["x_min"] <= x <= top_right_icon_bounds["x_max"] and
                            top_right_icon_bounds["y_min"] <= y <= top_right_icon_bounds["y_max"])

                    # Frame completed — process gesture logic once per frame
                    def dial_claims(x, y):
                        if calibration is not None:
                            return True
                        if on_corner(x, y):
                            return True
                        if emulation:
                            return bool(dialpad)
                        return bool(dialpad) and math.hypot(x - circle_center_x, y - circle_center_y) <= circle_radius

                    picked = select_dial_touch(slots, dial_touch_id, dial_claims)
                    if picked is not None and dial_touch_id is not None and picked[0] != dial_touch_id:
                        # The followed finger lifted while another one touched
                        # the dial: finish this touch first, start the next
                        # one on the following frame.
                        picked = None
                    if picked is None:
                        dial_touch_id = None
                        touch_x = None
                        touch_y = None
                        finger_down = False
                    else:
                        dial_touch_id, touch_x, touch_y = picked
                        finger_down = True

                    if finger_down and not prev_finger_down:
                        coactivator_blocked = False
                        touch_start_time = time()
                        within_top_right_icon = False
                        icon_activated = False
                        last_slice = None
                        rotation.reset()
                        corner_touch = emulation and on_corner(touch_x, touch_y)
                        log.debug("Finger detected.")

                    if finger_down:
                        settle_counter += 1
                    else:
                        settle_counter = 0

                    if calibration is not None:
                        # Calibration owns the touchpad: no activation and no
                        # dial actions while the circles are traced.
                        calibration_frame(touch_x, touch_y, finger_down, prev_finger_down)
                        prev_finger_down = finger_down
                        continue

                    if touch_x is not None and touch_y is not None and finger_down:
                        # Phase 1: top-right icon activation
                        if on_corner(touch_x, touch_y):
                            if not within_top_right_icon:
                                log.debug("Touch entered top-right icon bounds.")
                            within_top_right_icon = True

                            if touch_start_time and not icon_activated and not coactivator_blocked:
                                if (time() - touch_start_time) >= activation_time:
                                    if dialpad or (not dialpad and are_modifier_keys_pressed(coactivator_keys)):
                                        toggle_top_right_icon(dialpad)
                                        icon_activated = True
                                    else:
                                        log.debug("DialPad activation blocked: co-activator key(s) not pressed: %s", " ".join(coactivator_keys))
                                        coactivator_blocked = True
                        else:
                            if within_top_right_icon:
                                log.debug("Touch left top-right icon bounds. Canceling the action.")
                            within_top_right_icon = False
                            touch_start_time = None
                            icon_activated = False

                        # Calculate distance and angle for circle-based detection
                        dx = (touch_x - circle_center_x) * x_scale
                        dy = touch_y - circle_center_y
                        distance = math.sqrt(dx**2 + dy**2)
                        angle = touch_angle(dx, dy)

                        # The emulated dial has no ring edge: the whole
                        # touchpad is the ring, except a touch on the corner.
                        if emulation:
                            on_dial = dialpad and not corner_touch
                        else:
                            on_dial = distance <= circle_radius and dialpad

                        if on_dial:

                            first_touch_outside = True

                            # The emulated dial keeps the pointer off while on.
                            if not tap_disabled and not emulation:
                                set_touchpad_prop_send_events(0)
                                tap_disabled = True

                            if not touching_sent:
                                touching_sent = True
                                if overlay_enabled():
                                    send_to_socket({"touching": True})

                            if distance < center_button_radius:

                                # The ring reference restarts after the centre
                                # so returning to the ring cannot jump.
                                rotation.reset()
                                if last_pointer_angle is not None:
                                    last_pointer_angle = None
                                    send_details(pointer=-1)

                                # Holding the centre inside a function goes
                                # back to the menu without lifting the finger.
                                if (
                                    center_button_triggered
                                    and multi_app_mode
                                    and (center_activated or submenu)
                                    and not center_hold_fired
                                    and time() - center_enter_time >= center_hold_time
                                ):
                                    center_hold_fired = True
                                    leave_function()

                                if not center_button_triggered:

                                    log.debug("Touch detected in center button area. Title is: %s", title)

                                    if title:
                                        if overlay_enabled():
                                            send_to_socket({"input": "center", "value": 1, "title": title})
                                        center_enter_time = time()
                                        center_hold_fired = False

                                        center_button_triggered = True
                                        icon_activated = True
                            else:

                                if show_pointer_enabled() and (
                                    last_pointer_angle is None
                                    or abs((angle - last_pointer_angle + 180) % 360 - 180) >= POINTER_STEP_DEGREES
                                ):
                                    last_pointer_angle = angle
                                    send_details(pointer=round(angle, 1))

                                if center_button_triggered:
                                    center_button_triggered = False
                                    center_hold_fired = False
                                    icon_activated = False
                                    center_enter_time = 0
                                    if overlay_enabled():
                                        send_to_socket({"input": "center", "value": 0, "title": title})
                                    log.debug("Touch ended in center button area.")

                                if multi_app_mode and submenu and not center_activated:
                                    # A group's list: turning moves the
                                    # highlight, the centre chooses.
                                    if settle_counter >= SETTLE_FRAMES:
                                        moved = rotation.update(
                                            angle, time(),
                                            max(2, SUBMENU_STEP_DEGREES / profile_float("sensitivity")),
                                        )
                                        if moved is not None:
                                            move_submenu_highlight(moved[0])
                                elif multi_app_mode and not center_activated:

                                    current_slice_idx = slice_index(
                                        angle,
                                        max(len(multi_app_mode_titles), slices_minimum_count),
                                    )

                                    if current_slice_idx != last_slice:
                                        log.debug(
                                            "TOUCH x=%d y=%d | center x=%d y=%d | angle=%.1f° | slice=%d",
                                            touch_x, touch_y,
                                            circle_center_x, circle_center_y,
                                            angle,
                                            current_slice_idx,
                                        )

                                        highlight_menu_slice(current_slice_idx)

                                        last_slice = current_slice_idx
                                elif settle_counter >= SETTLE_FRAMES:

                                    turn_selected_function(rotation, angle)
                        else:
                            rotation.reset()
                            if first_touch_outside:
                                log.debug("Touch outside the circle for first time. Ignoring.")
                                first_touch_outside = None

                    elif not finger_down and prev_finger_down:
                        # Finger lifted — handle release cleanup
                        within_top_right_icon = False
                        icon_activated = False
                        last_slice = None
                        rotation.reset()

                        duration_held = time() - center_enter_time
                        if last_pointer_angle is not None:
                            last_pointer_angle = None
                            send_details(pointer=-1)
                        if center_button_triggered:
                            release_center(duration_held, center_hold_fired)
                        elif multi_app_mode and not center_activated and title and not submenu:
                            # A tap on an outer menu sector selects it
                            # directly; in a list, lifting after turning
                            # only leaves the highlight where it is.
                            select_multifunction_segment()

                        if center_button_triggered and overlay_enabled():
                            send_to_socket({"input": "center", "value": 0, "title": title})

                        center_button_triggered = False
                        center_hold_fired = False
                        center_enter_time = 0

                        show_menu_after_gesture()

                        if touching_sent:
                            touching_sent = False
                            if overlay_enabled():
                                send_to_socket({"touching": False})

                        if tap_disabled:
                            set_touchpad_prop_send_events(1)
                            tap_disabled = False

                        # Configuration and focus changes that came while the
                        # finger was on the dial apply now, between gestures.
                        process_pending_driver_updates()

                    prev_finger_down = finger_down

                elif event.matches(EV_ABS):
                    current_slot = update_touch_slots(slots, current_slot, event)

                elif event.matches(finger_key):
                    if event.value == 1:
                        key_press_times[event.code] = time()

            # The event generator only ends when the device goes away.
            break

        except device.EventsDroppedException:
            # The kernel buffer overflowed; resynchronise this device and keep
            # the gesture state instead of restarting the listener.
            log.debug("Touchpad events dropped; resynchronising")
            # A plain sync drains libevdev's resync queue and ends. With
            # force=True every call starts a new sync, so the loop never
            # finished and the driver stopped reacting to touches.
            # The sync events describe the fingers currently on the pad.
            for sync_event in d_t.sync():
                if sync_event.matches(EV_ABS):
                    current_slot = update_touch_slots(slots, current_slot, sync_event)
            settle_counter = 0
            rotation.reset()
        except Exception:
            log.exception("Error in listen_touchpad_events")
            break

# Mouse dial: the mouse is looked for every MOUSE_RETRY_SECONDS (a wireless
# receiver may not be plugged in yet, or goes away), the loop wakes every
# MOUSE_POLL_SECONDS for the hold of the middle button and for a dial switched
# over D-Bus.
MOUSE_RETRY_SECONDS = 2
MOUSE_POLL_SECONDS = 0.05
mouse_device = None
mouse_file = None
mouse_grabbed = False
# The loop grabs, the action thread and cleanup() may release.
mouse_lock = threading.Lock()

def mouse_toggle_key():
    """The configured toggle key as an evdev code (KEY_F13 if it is unknown)."""
    name = gui_config.mouse.toggle_key if gui_config is not None else "KEY_F13"
    key = getattr(EV_KEY, name, None)
    if key is None or not isinstance(key, type(EV_KEY.KEY_F13)):
        log.error("Mouse dial: unknown toggle key %s, using KEY_F13", name)
        key = EV_KEY.KEY_F13
    return key

def set_mouse_grab(owns):
    """Keep the pointer, the clicks and the wheel from the desktop while the dial is on."""
    global mouse_grabbed

    with mouse_lock:
        if mouse_device is None or owns == mouse_grabbed:
            return
        try:
            if owns:
                mouse_device.grab()
            else:
                mouse_device.ungrab()
        except Exception:
            log.warning("Could not %s the mouse", "grab" if owns else "release", exc_info=True)
        # Not tried again every poll; a mouse someone else grabbed stays theirs.
        mouse_grabbed = owns

def close_mouse():
    """Let the mouse go (ungrabbed) after the dial or the device is gone."""
    global mouse_device, mouse_file, mouse_grabbed

    with mouse_lock:
        if mouse_device is not None and mouse_grabbed:
            try:
                mouse_device.ungrab()
            except Exception:
                pass
        mouse_grabbed = False
        mouse_device = None
        if mouse_file is not None:
            try:
                mouse_file.close()
            except Exception:
                pass
            mouse_file = None

def open_mouse(key_code):
    """Open the configured mouse if it is there; False when it is not."""
    global mouse_device, mouse_file, mouse_grabbed

    try:
        with open("/proc/bus/input/devices", "r") as devices:
            node = find_mouse(devices.read(), gui_config.mouse.device, key_code)
    except OSError:
        return False
    if node is None:
        return False
    handle = None
    try:
        handle = open("/dev/input/" + node, "rb")
        os.set_blocking(handle.fileno(), False)
        opened = Device(handle)
    except Exception as error:
        log.warning("Can not open the mouse /dev/input/%s: %s", node, error)
        if handle is not None:
            handle.close()
        return False
    with mouse_lock:
        mouse_file = handle
        mouse_device = opened
        mouse_grabbed = False
    log.info('Mouse dial: using "%s" (%s)', opened.name, node)
    return True

class CenterGesture:
    """The centre button of an input source (the mouse's middle or left
    button, the SpaceMouse cap's push): a press counts when an entry is
    highlighted, like a finger on the centre."""

    def __init__(self):
        self.pressed = False
        # Called after a release with whether it opened a group's list.
        self.settled = []

    def press(self):
        if title:
            if overlay_enabled():
                send_to_socket({"input": "center", "value": 1, "title": title})
            self.pressed = True

    def hold(self):
        if self.pressed and multi_app_mode and (center_activated or submenu):
            leave_function()

    def release(self, duration, hold_fired):
        if not self.pressed:
            return
        list_before = submenu
        release_center(duration, hold_fired)
        if overlay_enabled():
            send_to_socket({"input": "center", "value": 0, "title": title})
        self.pressed = False
        show_menu_after_gesture()
        opened_list = bool(submenu) and not list_before
        for listener in self.settled:
            listener(opened_list)

    def reset(self):
        self.pressed = False

    def apply(self, action):
        """Run a MouseDial centre action; False if it is not one."""
        if action.kind == "center_press":
            self.press()
        elif action.kind == "center_hold":
            self.hold()
        elif action.kind == "center_release":
            self.release(action.duration, action.hold_fired)
        else:
            return False
        return True


# Keys that SpaceMouse modifier buttons hold; the scroll steps' own holding of
# Ctrl must not let go of them.
spacemouse_held_keys = set()
# The keys the SpaceMouse buttons want down (written on the input thread), and
# the ones the action thread has pressed on the virtual keyboard for them.
spacemouse_key_lock = threading.Lock()
spacemouse_target_keys = []
spacemouse_applied_keys = []

def reconcile_spacemouse_keys():
    """Press and release the virtual keyboard's keys until they are the keys the
    buttons want. It always works from the newest wish, so jobs that run late,
    twice or out of order (the overflow thread of a full action queue) still end
    right and never leave a key stuck."""
    with spacemouse_key_lock:
        target = list(spacemouse_target_keys)
        release = [key for key in spacemouse_applied_keys if key not in target]
        press = [key for key in target if key not in spacemouse_applied_keys]
        # A key scroll steps are holding too (zoom) stays down for them; they let go of it.
        set_keys_held([key for key in release if key not in scroll_modifiers_held], False)
        for key in release:
            spacemouse_applied_keys.remove(key)
        set_keys_held(press, True)
        spacemouse_applied_keys.extend(press)
    publish_driver_state()

def restore_spacenav_leftovers(client, restore):
    """Undo what a run that was killed left in spacenavd: other programs muted
    (sensitivity 0) or buttons hidden. The restore file says what it was."""
    saved = restore.load()
    if not saved or client.proto < 1:
        return
    sensitivity = saved.get("sensitivity")
    if isinstance(sensitivity, (int, float)) and client.get_sensitivity() == 0.0:
        client.set_sensitivity(float(sensitivity))
        log.info("SpaceMouse: restored spacenavd's sensitivity %s", sensitivity)
    for button, mapped in (saved.get("bnmap") or {}).items():
        try:
            client.set_button_map(int(button), int(mapped))
        except (TypeError, ValueError):
            pass
    motion = saved.get("motion")
    if isinstance(motion, dict):
        try:
            sens = float(motion["sens"])
            axis = [float(value) for value in motion["axis"]]
            invert = [bool(value) for value in motion["invert"]]
        except (KeyError, TypeError, ValueError):
            sens = None
        if sens is not None and len(axis) == 6 and len(invert) == 6:
            client.set_sensitivity(sens)
            client.set_axis_sensitivity(axis)
            client.set_invert(invert)
            log.info("SpaceMouse: restored spacenavd's motion settings")
    restore.clear()

def restore_spacenav_at_start():
    """At driver start, also with SpaceMouse mode off (it may have been on when
    the driver died): a short, non-blocking visit to spacenavd if there is
    something to give back. The file stays when spacenavd is not there."""
    restore = RestoreFile(SPACENAV_RESTORE_FILE)
    if not SPACENAV_RESTORE_FILE or restore.load() is None:
        return
    client = SpacenavClient()
    try:
        configured = gui_config.spacemouse.socket if gui_config is not None else ""
        try:
            with open("/etc/spnavrc", "r") as rc:
                spnavrc = rc.read()
        except OSError:
            spnavrc = ""
        if client.connect(socket_candidates(configured, os.environ, spnavrc)):
            restore_spacenav_leftovers(client, restore)
    except OSError as error:
        log.warning("Could not give spacenavd back what the last run changed: %s", error)
    finally:
        client.close()

class InputSource:
    """One device that drives the dial from the main thread's select loop
    (listen_input_sources): it finds its device, reads it and looks for it
    again when it is gone."""

    name = ""
    # The file object to select on; None while the device is not there.
    handle = None
    center_held = False

    def __init__(self, center):
        self.center = center
        self.next_search = 0
        self.reported_missing = False

    @property
    def connected(self):
        return self.handle is not None

    def search(self, now):
        """Look for the device, at most every MOUSE_RETRY_SECONDS."""

    def process(self, readable, now):
        """Handle what the device sent (readable: the files select found ready)."""

    def sync(self, dial_on):
        """Follow the dial's state (grab the mouse, mute other clients)."""

    def context_changed(self):
        """The dial turned on or off, or a function or list was entered or left."""

    def lost(self, error):
        """The device went away."""

    def close(self):
        """Let the device go, as it was."""


class MouseSource(InputSource):
    """The mouse: the toggle key switches the dial, the wheel turns it, the
    middle and left buttons are the centre (left: select, middle: also hold to
    go back), the right button goes back, and with config mouse.motion the
    direction the mouse moves is the dial's angle (see MouseDial, MousePointer)."""

    name = "mouse"
    # Same rule as the touch loop: at most one finger dot update per step.
    POINTER_STEP_DEGREES = 1

    def __init__(self, center):
        super().__init__(center)
        settings = gui_config.mouse if gui_config is not None else None
        self.motion = bool(settings is not None and settings.motion)
        self.toggle_key = mouse_toggle_key()
        self.mouse = MouseDial(center_hold_time)
        self.pointer = MousePointer(settings.motion_radius if settings is not None else 300)
        self.rotation = RotationTracker(angle_hysteresis, 30)
        self.last_slice = None
        self.last_pointer_angle = None
        self.reported_missing = False
        log.info(
            "Mouse dial: toggle key %s, motion %s",
            self.toggle_key.name, "on" if self.motion else "off",
        )

    @property
    def handle(self):
        return mouse_file if mouse_device is not None else None

    @property
    def connected(self):
        return mouse_device is not None

    @property
    def center_held(self):
        return self.mouse.center_held

    def search(self, now):
        if now < self.next_search:
            return
        self.next_search = now + MOUSE_RETRY_SECONDS
        if open_mouse(self.toggle_key.value):
            self.reported_missing = False
            self.mouse.set_active(dialpad)
        elif not self.reported_missing:
            self.reported_missing = True
            log.warning(
                'No mouse with %s and a wheel found%s; looking again every %d s',
                self.toggle_key.name,
                f' named "{gui_config.mouse.device}"' if gui_config.mouse.device else "",
                MOUSE_RETRY_SECONDS,
            )

    def context_changed(self):
        """No heading yet (the hand has to travel again), no finger dot."""
        self.pointer.reset()
        self.rotation.reset()
        self.last_slice = None
        if self.last_pointer_angle is not None:
            self.last_pointer_angle = None
            send_details(pointer=-1)

    def follow_motion(self):
        """The hand's heading as a finger's angle: the sector it points to, a
        list's highlight or the selected function's turning."""
        pointer, rotation = self.pointer, self.rotation
        if pointer.in_dead_zone:
            # Like a finger in the centre button.
            rotation.reset()
            if self.last_pointer_angle is not None:
                self.last_pointer_angle = None
                send_details(pointer=-1)
            return
        angle = pointer.angle()
        rotation.hysteresis = angle_hysteresis
        if pointer.jumped:
            # The hand reversed: a new reference, not a turn that fires actions.
            # (In the menu a jump just picks the new sector.)
            rotation.reset()
        if show_pointer_enabled() and (
            self.last_pointer_angle is None
            or abs((angle - self.last_pointer_angle + 180) % 360 - 180) >= self.POINTER_STEP_DEGREES
        ):
            self.last_pointer_angle = angle
            send_details(pointer=round(angle, 1))
        if multi_app_mode and submenu and not center_activated:
            moved = rotation.update(angle, time(), max(2, SUBMENU_STEP_DEGREES / profile_float("sensitivity")))
            if moved is not None:
                move_submenu_highlight(moved[0])
        elif multi_app_mode and not center_activated:
            slice_idx = slice_index(angle, max(len(multi_app_mode_titles or []), slices_minimum_count))
            if slice_idx != self.last_slice:
                self.last_slice = slice_idx
                highlight_menu_slice(slice_idx)
        else:
            turn_selected_function(rotation, angle)

    def handle_action(self, action):
        if self.center.apply(action):
            return
        if action.kind == "toggle":
            toggle_top_right_icon(dialpad)
            self.mouse.set_active(dialpad)
        elif action.kind == "rotate":
            rotate_dial(action.direction)
        elif action.kind == "back":
            go_back()

    def process(self, readable, now):
        global last_event_time

        mouse = self.mouse
        mouse.hold_time = center_hold_time
        # The dial can also be switched over D-Bus or by the inactivity timer.
        mouse.set_active(dialpad)

        moved_x = moved_y = 0
        if self.handle in readable:
            events = []
            try:
                for event in mouse_device.events():
                    events.append(event)
            except device.EventsDroppedException:
                log.debug("Mouse events dropped; resynchronising")
                for _ in mouse_device.sync():
                    pass
            for event in events:
                last_event_time = time()
                if event.matches(self.toggle_key):
                    actions = mouse.toggle_key(event.value)
                elif event.matches(EV_REL.REL_WHEEL):
                    # REL_WHEEL_HI_RES is ignored: one notch, one step.
                    actions = mouse.wheel(event.value)
                elif event.matches(EV_KEY.BTN_MIDDLE):
                    actions = mouse.middle(event.value, time())
                elif event.matches(EV_KEY.BTN_LEFT):
                    actions = mouse.left(event.value, time())
                elif event.matches(EV_KEY.BTN_RIGHT):
                    actions = mouse.right(event.value)
                elif event.matches(EV_REL.REL_X):
                    moved_x += event.value
                    continue
                elif event.matches(EV_REL.REL_Y):
                    moved_y += event.value
                    continue
                else:
                    continue
                for action in actions:
                    self.handle_action(action)
                    mouse.set_active(dialpad)

        for action in mouse.tick(time()):
            self.handle_action(action)

        if self.motion and dialpad and (moved_x or moved_y):
            self.pointer.move(moved_x, moved_y)
            self.follow_motion()

    def sync(self, dial_on):
        # Grab after the toggle key is released: the desktop saw it go
        # down and must see it go up (and the other way round).
        if dial_on != mouse_grabbed and not self.mouse.toggle_down:
            set_mouse_grab(dial_on)

    def lost(self, error):
        log.warning("Lost the mouse (%s); looking for it again", error)
        close_mouse()
        self.mouse.set_active(False)
        self.mouse.toggle_down = False
        self.center.reset()
        self.next_search = time() + MOUSE_RETRY_SECONDS

    def close(self):
        close_mouse()


class SpaceMouseSource(InputSource):
    """A 3Dconnexion SpaceMouse through spacenavd (spacenav_client.py): its
    raw buttons and axes arrive whatever spacenavd's configuration is. Buttons
    run their actions (ButtonMachine), tilt picks, twist turns, a push on the
    cap is the centre button (SpaceCap)."""

    name = "spacemouse"

    def __init__(self, center):
        super().__init__(center)
        settings = gui_config.spacemouse
        self.settings = settings
        self.client = SpacenavClient()
        self.cap = SpaceCap(
            settings.axis_max, settings.tilt_axes, settings.tilt_invert, settings.twist_axis,
            settings.twist_invert, settings.twist_speed, settings.push_axis, settings.push_invert,
        )
        self.buttons = ButtonMachine(settings.hold_ms)
        self.push = MouseDial(center_hold_time)
        self.rotation = RotationTracker(angle_hysteresis, 30)
        self.stepper = ListStepper()
        self.restore = RestoreFile(SPACENAV_RESTORE_FILE)
        self.device_name = ""
        self.muted = False
        self.saved_sensitivity = None
        self.hidden = {}
        self.held_keys = []
        # How many buttons hold each key down right now.
        self.held_counts = {}
        self.hidden_for = None
        # Buttons physically down now, and the ones whose mapping waits for their release.
        self.physical_down = set()
        self.deferred_buttons = set()
        self.last_slice = None
        self.last_pointer_angle = None
        # Button layers, and the cap's motion in spacenavd (see sync_motion()).
        self.layers = LayerState()
        self.layer_key = None
        self.motion_sessions = {}
        self.motion_original = None
        self.motion_applied = None
        self.motion_shown = {"sensitivity": 1.0, "rotation": True, "translation": True}
        center.settled.append(self.center_settled)
        log.info("SpaceMouse mode: looking for spacenavd")

    def center_settled(self, opened_list):
        """A push on the cap chose something: the button that opened the menu has
        nothing left to select (it would select twice, or not close the dial).
        A group's list that just opened keeps its opener."""
        if not opened_list:
            self.buttons.clear_opener()

    @property
    def handle(self):
        return self.client.sock

    @property
    def connected(self):
        return self.client.connected

    @property
    def center_held(self):
        return self.push.center_held or self.buttons.gesture_active

    def search(self, now):
        if now < self.next_search:
            return
        self.next_search = now + MOUSE_RETRY_SECONDS
        try:
            with open("/etc/spnavrc", "r") as rc:
                spnavrc = rc.read()
        except OSError:
            spnavrc = ""
        if not self.client.connect(socket_candidates(self.settings.socket, os.environ, spnavrc)):
            if not self.reported_missing:
                self.reported_missing = True
                log.warning("spacenavd is not reachable; looking again every %d s", MOUSE_RETRY_SECONDS)
            return
        self.reported_missing = False
        client = self.client
        self.cap.reset()
        self.buttons = ButtonMachine(self.settings.hold_ms)
        self.push.set_active(False)
        try:
            self.negotiate()
        except OSError as error:
            # The daemon went away right after connecting: not a reason to stop the driver.
            self.lost(error)
            return
        log.info("SpaceMouse: connected to spacenavd (protocol %d)%s", client.proto,
                 f', device "{self.device_name}"' if self.device_name else "")

    def negotiate(self):
        """Requests after connecting; OSError when the daemon is gone."""
        client = self.client
        self.give_back_leftovers()
        if client.proto >= 1:
            client.send_name("Turnpad")
            if not client.set_evmask(EVMASK_RAWAXIS | EVMASK_RAWBUTTON):
                log.warning("spacenavd refused the raw event mask; the SpaceMouse may not work")
            self.device_name = " ".join((client.device_name() or "").split())
            self.hidden_for = None  # sync() hides what the profile binds
        else:
            self.device_name = ""
            log.warning(
                "spacenavd speaks protocol 0 (old): no raw events, so spacenavd's own button "
                "mapping and dead zones apply, and other programs are not muted"
            )

    def give_back_leftovers(self):
        """A previous run that crashed may have left other programs muted or buttons hidden."""
        restore_spacenav_leftovers(self.client, self.restore)

    def sync_hidden_buttons(self):
        """With hide_buttons: map exactly the buttons the current profile binds to an
        unused one (other programs do not see them; Turnpad still gets them as
        raw buttons) and give the others back. Called when the profile or the
        configuration changes."""
        if self.client.proto < 1 or gui_config is None:
            return
        wanted = set()
        if gui_config.spacemouse.hide_buttons:
            wanted = spacemouse_bound_buttons()
        # A button that is down now keeps its mapping until it is up: changing it
        # mid-press would leave the press or the release with the wrong client.
        self.deferred_buttons = {
            button for button in (set(self.hidden) ^ wanted) if button in self.physical_down
        }
        for button in sorted(set(self.hidden) - wanted - self.deferred_buttons):
            if self.client.set_button_map(button, self.hidden[button]):
                del self.hidden[button]
        for button in sorted(wanted - set(self.hidden) - self.deferred_buttons):
            mapped = self.client.get_button_map(button)
            if mapped is None:
                continue
            if mapped == SPACENAV_UNUSED_BUTTON:
                # Already hidden (by a run that left it so): its own button is what to give back.
                mapped = button
            # The original goes into the restore file before the mapping changes.
            self.hidden[button] = mapped
            self.save_restore()
            if not self.client.set_button_map(button, SPACENAV_UNUSED_BUTTON):
                del self.hidden[button]
        self.save_restore()

    def save_restore(self):
        data = {}
        if self.muted and self.saved_sensitivity is not None:
            data["sensitivity"] = self.saved_sensitivity
        if self.hidden:
            data["bnmap"] = {str(button): mapped for button, mapped in self.hidden.items()}
        if self.motion_original is not None:
            data["motion"] = self.motion_original
        if data:
            self.restore.save(data)
        else:
            self.restore.clear()

    def mute(self):
        """Silence the other spacenavd clients while the dial is on (global sensitivity 0)."""
        sensitivity = self.client.get_sensitivity()
        if sensitivity is None:
            return
        self.saved_sensitivity = sensitivity
        self.muted = True
        self.save_restore()
        self.client.set_sensitivity(0.0)

    def unmute(self):
        if self.saved_sensitivity is not None:
            self.client.set_sensitivity(self.saved_sensitivity)
        self.muted = False
        self.saved_sensitivity = None
        self.save_restore()

    # --- the cap's motion for other programs -------------------------------------

    def motion_base(self):
        """The configured motion of the current profile, before the session's changes."""
        return effective_motion(gui_config.spacemouse.motion, profile_meta().get("spacemouse_motion"))

    def motion_target(self):
        base = self.motion_base()
        session = self.motion_sessions.get(app_name or "")
        return session.apply(base) if session is not None else base

    def motion_action(self, action):
        """A motion button: change the session of the focused application."""
        if gui_config is None or effective_motion is None:
            return
        session = self.motion_sessions.setdefault(app_name or "", MotionSession())
        session.act(action.motion, self.motion_base())

    def read_motion(self):
        """What spacenavd has now (its own configuration), or None."""
        # Muted, the global sensitivity is 0: the value to give back is the saved one.
        sens = self.saved_sensitivity if self.muted else self.client.get_sensitivity()
        axis = self.client.get_axis_sensitivity()
        invert = self.client.get_invert()
        if sens is None or axis is None or invert is None:
            return None
        return {"sens": sens, "axis": axis, "invert": [1 if flag else 0 for flag in invert]}

    def push_motion(self, sens, axis, invert):
        """Set spacenavd's sensitivity, axis sensitivities and inversion; True when it took all."""
        if self.muted:
            # The other programs stay silent: the value to give back at unmute is the new one.
            self.saved_sensitivity = sens
            self.save_restore()
            done = True
        else:
            done = self.client.set_sensitivity(sens)
        done = self.client.set_axis_sensitivity(axis) and done
        return self.client.set_invert(invert) and done

    def sync_motion(self):
        """Make spacenavd's motion settings those of the focused application
        (profile over the global motion, then the session's changes). Nothing
        is touched while the effective motion is spacenavd's own; before the
        first change its values go into the restore file, and they are given
        back when the motion is neutral again, on exit and at the next start."""
        if self.client.proto < 1 or gui_config is None or effective_motion is None:
            return
        target = self.motion_target()
        self.motion_shown = {
            "sensitivity": round(target["sensitivity"], 2),
            "rotation": target["rotation"],
            "translation": target["translation"],
        }
        if target == self.motion_applied:
            return
        # Whatever happens, this target is not tried again until something changes.
        self.motion_applied = target
        neutral = motion_neutral(target)
        if self.motion_original is None:
            if neutral:
                return
            original = self.read_motion()
            if original is None:
                log.warning("spacenavd did not tell its motion settings; leaving them alone")
                return
            self.motion_original = original
            self.save_restore()
        sens, axis, invert = motion_values(self.motion_original, target)
        done = self.push_motion(sens, axis, invert)
        if neutral and done:
            self.motion_original = None
            self.save_restore()
        elif not done:
            log.warning("spacenavd refused part of the motion settings")

    def restore_motion(self):
        """Give spacenavd its own motion settings back; True when it took them."""
        original = self.motion_original
        if original is None:
            return True
        done = self.client.set_sensitivity(original["sens"])
        done = self.client.set_axis_sensitivity(original["axis"]) and done
        return self.client.set_invert(original["invert"]) and done

    # --- layers ---------------------------------------------------------------------

    def layer_action(self, index, action):
        """A layer button that is not a hold (a hold goes through the button machine)."""
        if action.layer and action.layer_mode in ("latch", "toggle"):
            self.layers.activate(index, action.layer, action.layer_mode)

    def run_state_action(self, index, action):
        """Layer and motion actions that Control's ``button:INDEX`` triggers."""
        if action.type == "layer":
            self.layer_action(index, action)
        elif action.type == "motion":
            self.motion_action(action)

    def context_changed(self):
        self.rotation.reset()
        self.stepper.reset()
        self.last_slice = None
        if self.last_pointer_angle is not None:
            self.last_pointer_angle = None
            send_details(pointer=-1)

    def hold_key(self, action, pressed):
        """Hold or release what a modifier or key_hold action keeps down. A key
        that two buttons hold goes down with the first and up with the last."""
        for key in spacemouse_action_keys(action):
            count = self.held_counts.get(key, 0)
            if pressed:
                self.held_counts[key] = count + 1
            elif count > 1:
                self.held_counts[key] = count - 1
            else:
                self.held_counts.pop(key, None)
        self.want_keys()

    def want_keys(self):
        """Tell the action thread which keys the buttons hold now."""
        self.held_keys = list(self.held_counts)
        with spacemouse_key_lock:
            spacemouse_target_keys[:] = self.held_keys
        spacemouse_held_keys.intersection_update(self.held_keys)
        spacemouse_held_keys.update(self.held_keys)
        enqueue_action(reconcile_spacemouse_keys, required=True)

    def run_buttons(self, actions):
        for item in actions:
            if item.kind == "action":
                if item.action.type in ("layer", "motion"):
                    self.run_state_action(item.index, item.action)
                    continue
                run_spacemouse_action(item.action)
                if item.action.type in ("select", "back"):
                    self.buttons.clear_opener()
            elif item.kind == "hold_down":
                self.hold_key(item.action, True)
            elif item.kind == "hold_up":
                self.hold_key(item.action, False)
            elif item.kind == "layer_down":
                self.layers.hold_down(item.index, item.action.layer)
            elif item.kind == "layer_up":
                self.layers.hold_up(item.index)
            elif item.kind == "select":
                tap_center()
            elif item.kind == "close":
                if dialpad:
                    toggle_top_right_icon(dialpad)

    def button(self, index, pressed, now):
        global last_event_time

        last_event_time = time()
        dial_on = bool(dialpad)
        if pressed:
            self.physical_down.add(index)
        else:
            self.physical_down.discard(index)
            if index in self.deferred_buttons:
                # A mapping change that waited for this button to come up.
                self.hidden_for = None
        if pressed:
            # The button that switched a latch or toggle layer on switches it off again.
            if self.layers.preempt(index):
                return
            binding = spacemouse_binding(index, self.layers.active)
            # A latched layer is for this one press.
            self.layers.consume()
            if binding is None:
                return
            self.run_buttons(self.buttons.press(index, now, binding, dial_on))
        else:
            self.run_buttons(self.buttons.release(index, now, dial_on))

    def axis(self, index, value, now):
        change = self.cap.set_axis(index, value)
        if change == "push":
            actions = self.push.middle(1, now)
        elif change == "release":
            actions = self.push.middle(0, now)
        else:
            return
        for action in actions:
            self.center.apply(action)

    def follow_cap(self, now):
        """Tilt and twist as a finger's angle would drive the dial."""
        global last_event_time

        cap, rotation = self.cap, self.rotation
        angle = cap.tilt_angle()
        if angle is None:
            if self.last_pointer_angle is not None:
                self.last_pointer_angle = None
                send_details(pointer=-1)
        else:
            last_event_time = time()
            if show_pointer_enabled() and (
                self.last_pointer_angle is None
                or abs((angle - self.last_pointer_angle + 180) % 360 - 180) >= 1
            ):
                self.last_pointer_angle = angle
                send_details(pointer=round(angle, 1))

        in_menu = multi_app_mode and not center_activated
        if in_menu and submenu:
            direction, fraction = cap.list_direction()
            step = self.stepper.update(direction, fraction, now)
            if step and move_submenu_highlight("clockwise" if step > 0 else "counterclockwise") and title:
                self.buttons.note_highlight()
        elif in_menu:
            self.stepper.reset()
            if angle is not None:
                slice_idx = slice_index(angle, max(len(multi_app_mode_titles or []), slices_minimum_count))
                if slice_idx != self.last_slice:
                    self.last_slice = slice_idx
                    highlight_menu_slice(slice_idx)
                    if title:
                        self.buttons.note_highlight()
            else:
                # Back in the dead zone: tilting to the same sector again highlights it again.
                self.last_slice = None

        if cap.tick(now):
            last_event_time = time()
            rotation.hysteresis = angle_hysteresis
            if in_menu:
                moved = rotation.update(cap.twist_angle, now, max(2, SUBMENU_STEP_DEGREES / profile_float("sensitivity")))
                if moved is not None:
                    rotate_dial(moved[0])
                    # The twist moved the highlight: a tilt to the sector it left must act.
                    self.last_slice = None
                    if title:
                        self.buttons.note_highlight()
            else:
                turn_selected_function(rotation, cap.twist_angle)
        else:
            rotation.reset()

    def process(self, readable, now):
        self.push.hold_time = center_hold_time
        self.push.set_active(dialpad)
        proto0 = self.client.proto < 1
        for event in self.client.read_events():
            if event.type == UEV_RAWAXIS:
                self.axis(event.data[0], event.data[1], now)
            elif event.type == UEV_RAWBUTTON:
                self.button(event.data[0], bool(event.data[1]), now)
            elif proto0 and event.type in (UEV_PRESS, UEV_RELEASE):
                self.button(event.data[0], event.type == UEV_PRESS, now)
            elif proto0 and event.type == UEV_MOTION:
                for axis_index in range(6):
                    self.axis(axis_index, event.data[axis_index], now)
        self.run_buttons(self.buttons.tick(now, bool(dialpad)))
        if dialpad:
            self.follow_cap(now)
        for action in self.push.tick(now):
            self.center.apply(action)

    def sync(self, dial_on):
        if not dial_on:
            self.buttons.dial_off()
        # The profile (focus) or the configuration changed: hide what it binds.
        meta = profile_meta()
        buttons_key = (
            app_name, id(gui_config), id(meta.get("spacemouse_buttons")), id(meta.get("spacemouse_layers")),
        )
        if buttons_key != self.hidden_for:
            self.hidden_for = buttons_key
            self.sync_hidden_buttons()
        # Another profile (or context) or a new configuration: a latched or toggled layer ends.
        layer_key = (id(gui_config), id(meta) if meta else 0)
        if layer_key != self.layer_key:
            if self.layer_key is not None:
                self.layers.clear_sticky()
            self.layer_key = layer_key
        if self.client.proto >= 1:
            # While the dial is on (with mute_others) or a Control "mute:MS" lease runs.
            wanted = spacemouse_mute.wanted(dial_on, self.settings.mute_others, time())
            if wanted and not self.muted:
                self.mute()
            elif not wanted and self.muted:
                self.unmute()
            self.sync_motion()

    def release_keys(self, queued=False):
        """Let go of the modifier keys the buttons hold. ``queued``: through the
        action thread, after the presses it may still have waiting."""
        self.held_counts = {}
        self.want_keys()
        if not queued:
            reconcile_spacemouse_keys()

    def lost(self, error):
        log.warning("Lost spacenavd (%s); looking for it again", error)
        self.release_keys(queued=True)
        self.buttons.release_all()
        self.client.close()
        self.muted = False
        self.saved_sensitivity = None
        self.hidden = {}
        self.hidden_for = None
        # What was changed in spacenavd stays in the restore file for the next connection.
        self.motion_original = None
        self.motion_applied = None
        self.layers.clear()
        self.held_counts = {}
        self.physical_down = set()
        self.deferred_buttons = set()
        self.push.set_active(False)
        self.center.reset()
        self.next_search = time() + MOUSE_RETRY_SECONDS

    def close(self):
        self.release_keys()
        if self.client.connected and self.client.proto >= 1:
            try:
                if self.muted:
                    self.unmute()
                if self.restore_motion():
                    self.motion_original = None
                # What cannot be given back stays in the restore file for the next start.
                self.hidden = {
                    button: mapped for button, mapped in self.hidden.items()
                    if not self.client.set_button_map(button, mapped)
                }
                self.save_restore()
            except OSError:
                pass
        self.client.close()


def listen_input_sources():
    """The input loop of the mouse dial and the SpaceMouse mode, on the main
    thread like the touch loop: one select over the devices that are there.
    Each source finds its device again when it is unplugged or spacenavd
    restarts."""
    global last_event_time, title, center_activated, treshold

    title = None
    center_activated = False
    treshold = None

    center = CenterGesture()
    sources = input_sources
    sources.clear()
    if mouse_mode:
        sources.append(MouseSource(center))
    if spacemouse_mode:
        sources.append(SpaceMouseSource(center))
    touching_sent = False
    # What the heading or the highlight means: it starts again when this changes.
    last_context = None

    while not stop_threads:
        try:
            now = time()
            for source in sources:
                if not source.connected:
                    try:
                        source.search(now)
                    except OSError as error:
                        source.lost(error)

            handles = [source.handle for source in sources if source.connected]
            if handles:
                readable, _, _ = select.select(handles, [], [], MOUSE_POLL_SECONDS)
            else:
                # Updates from D-Bus (focus, configuration, Toggle) still apply.
                sleep(MOUSE_POLL_SECONDS)
                readable = []

            now = time()
            for source in sources:
                if not source.connected:
                    continue
                try:
                    source.process(readable, now)
                except OSError as error:
                    source.lost(error)

            if not dialpad:
                center.reset()

            # A fresh start when the dial turns on and after entering or
            # leaving a function or a list.
            context = (
                bool(dialpad), bool(center_activated), submenu,
                title if center_activated else None,
            )
            if context != last_context:
                last_context = context
                for source in sources:
                    source.context_changed()

            # The overlay stays up while the dial is on.
            if bool(dialpad) != touching_sent:
                touching_sent = bool(dialpad)
                if overlay_enabled():
                    send_to_socket({"touching": touching_sent})

            for source in sources:
                if source.connected:
                    try:
                        source.sync(bool(dialpad))
                    except OSError as error:
                        source.lost(error)

            # Configuration and focus changes wait for the centre button.
            process_pending_driver_updates(gesture_idle=not any(source.center_held for source in sources))

        except Exception:
            log.exception("Error in listen_input_sources")
            break

def config_dir_event(name):
    """A file in the config dir was written or moved in (config watcher thread).

    The driver's own saves hold config_lock while the event arrives
    (config_set() keeps it 0.1 s after saving), so they are not read back.
    """
    global config_lock

    if config_lock.locked():
        log.info("check_config_values_changes: detected internal change of config file -> do nothing -> would be deadlock")
        return False
    return True


def check_config_values_changes():
    # The watcher calls this 0.1 s after the first accepted event, because the
    # file might be read so fast that changes will not be there yet.
    log.info("check_config_values_changes: detected external change of config file -> loading changes")
    load_all_config_values()
    # Settings from the GUI configuration take precedence.
    apply_gui_settings()


def gsettingsGet(path, name):
    global gsettings_failure_count, gsettings_max_failure_count

    if gsettings_failure_count < gsettings_max_failure_count:
        try:
            cmd = ['gsettings', 'get', path, name]
            result = subprocess.check_output(cmd).rstrip()
            return result
        except Exception as e:
            log.debug(e, exc_info=True)
            gsettings_failure_count+=1
    else:
        log.debug('Gsettings failed more then: \"%s\" so is not try anymore', gsettings_max_failure_count)

udev = None
threads = []
stop_threads = False
enabled_evdev_keys = []

# only to avoid first - x11 only
gnome_current_layout = None
# only to avoid first - x11 even wayland (e.g. Ubuntu 22.04)
gnome_current_layout_index = None
keysym_name_associated_to_evdev_key_reflecting_current_layout = None


def mod_name_to_specific_keysym_name(mod_name):
    global display_wayland

    mod_to_specific_keysym_name = {
        'Control': 'Control_L',
        'Shift': 'Shift_L',
        'Lock': 'Caps_Lock',
        'Mod1': 'Alt_L',
        'Mod2': 'Num_Lock',
        'Mod3': 'Caps_Lock',
        'Mod4': 'Meta_L',
        'Mod5': 'Scroll_Lock',
        'NumLock': 'Num_Lock',
        'Alt': 'Alt_L',
        'LevelThree': 'ISO_Level3_Shift',
        'LAlt': 'Alt_L',
        'RAlt': 'Alt_R',
        'RControl': 'Control_R',
        'LControl': 'Control_L',
        'ScrollLock': 'Scroll_Lock',
        'LevelFive': 'ISO_Level5_Shift',
        'AltGr': 'Alt_R',
        'Meta': 'Meta_L',
        'Super': 'Meta_L',
        'Hyper': 'Hyper_L',
        'Ctrl': 'Control_L',
        'Win': 'Meta_L'
    }

    if display and X11_LIBS_AVAILABLE:

        mods_to_indexes_x11 = {
            "Shift": Xlib.X.ShiftMapIndex,
            "Lock": Xlib.X.LockMapIndex,
            "Control": Xlib.X.ControlMapIndex,
            "Alt": Xlib.X.Mod1MapIndex,
            "Mod1": Xlib.X.Mod1MapIndex,
            "Mod2": Xlib.X.Mod2MapIndex,
            "Mod3": Xlib.X.Mod3MapIndex,
            "Mod4": Xlib.X.Mod4MapIndex,
            "Mod5": Xlib.X.Mod5MapIndex,
            "AltGr": Xlib.X.Mod5MapIndex,
        }

        if mod_name in mods_to_indexes_x11:

            mods = display.get_modifier_mapping()
            first_keycode = mods[mods_to_indexes_x11[mod_name]][0]
            if first_keycode:
                keysym = display.keycode_to_keysym(first_keycode, 0)
                for key in Xlib.XK.__dict__:
                    if key.startswith("XK") and Xlib.XK.__dict__[key] == keysym:
                        return key[3:]
        return mod_to_specific_keysym_name[mod_name]
    elif keyboard_state:

        keymap = keyboard_state.get_keymap()
        num_mods = keymap.num_mods()

        for keycode in keymap:
            keyboard_state_clean = keymap.state_new()
            key_state = keyboard_state_clean.update_key(keycode, xkb.KeyDirection.XKB_KEY_DOWN)

            key_layouts_count = keymap.num_layouts_for_key(keycode)
            for layout in range(key_layouts_count):

                if gnome_current_layout_index is not None:
                    layout_is_active = gnome_current_layout_index % key_layouts_count == layout
                else:
                    layout_is_active = keyboard_state.layout_index_is_active(layout, xkb.StateComponent.XKB_STATE_LAYOUT_EFFECTIVE)

                if layout_is_active:
                    for mod_index in range(0, num_mods):

                        is_key_mod = key_state & xkb.StateComponent.XKB_STATE_MODS_DEPRESSED
                        if is_key_mod:

                            is_mod_active = keyboard_state_clean.mod_index_is_active(mod_index, xkb.StateComponent.XKB_STATE_MODS_DEPRESSED)
                            if is_mod_active:
                                if keymap.mod_get_name(mod_index) == mod_name:

                                    keysyms = keymap.key_get_syms_by_level(keycode, layout, 0)

                                    if len(keysyms) != 1:
                                        continue

                                    keysym_name = xkb.keysym_get_name(keysyms[0])
                                    #log.info(mod_name)
                                    #log.info(keycode)
                                    #log.info(keysym_name)
                                    return keysym_name

        return mod_to_specific_keysym_name[mod_name]
    else:
        return mod_to_specific_keysym_name[mod_name]

def listen_keyboard_events():
    """
    Listen for keyboard events to track active modifier keys.
    """
    global active_modifiers, modifiers, coactivator_modifiers, keyboard

    if keyboard is None:
        log.warning("No keyboard detected; skipping keyboard listener.")
        return

    log.info("Listening to keyboard events...")

    try:
        fd_k = open('/dev/input/event' + str(keyboard), 'rb')
        d_k = Device(fd_k)
    except Exception as e:
        log.error(f"Error opening the keyboard device: {e}")
        return

    while not stop_threads:
        try:
            for event in d_k.events():
                if event.type == EV_KEY and (event.code in modifiers or event.code in coactivator_modifiers):

                    if event.value == 1:  # Key Pressed
                        active_modifiers.add(event.code)
                    elif event.value == 0:  # Key Released
                        active_modifiers.discard(event.code)

                    log.debug(f"Active modifiers: {active_modifiers}")
            break

        except device.EventsDroppedException:
            for _ in d_k.sync(True):
                pass
        except Exception as e:
            log.error(f"Error in listen_keyboard_events: {e}")
            break

# default are for unicode shortcuts + is loaded layout during start (BackSpace, Return - enter, asterisk, minus etc. can be found using xev)
def set_defaults_keysym_name_associated_to_evdev_key_reflecting_current_layout():
    global keysym_name_associated_to_evdev_key_reflecting_current_layout

    keysym_name_associated_to_evdev_key_reflecting_current_layout = {
         # unicode shortcut - for hex value
        '0': '',
        '1': '',
        '2': '',
        '3': '',
        '4': '',
        '5': '',
        '6': '',
        '7': '',
        '8': '',
        '9': '',
        'a': '',
        'b': '',
        'c': '',
        'd': '',
        'e': '',
        'f': '',
        # unicode shortcut - start sequence
        mod_name_to_specific_keysym_name('Shift'): '',
        mod_name_to_specific_keysym_name('Control'): '',
        # possible co-activator key (together with Shift and Control above)
        mod_name_to_specific_keysym_name('Alt'): '',
        'u': '',
        # unicode shortcut - end sequence
        'space': ''
    }

def get_keysym_name_associated_to_evdev_key_reflecting_current_layout():
    global keysym_name_associated_to_evdev_key_reflecting_current_layout

    # lazy initialization because of loading modifiers inside Shift & Control
    if not keysym_name_associated_to_evdev_key_reflecting_current_layout:
        set_defaults_keysym_name_associated_to_evdev_key_reflecting_current_layout()

    return keysym_name_associated_to_evdev_key_reflecting_current_layout

def load_evdev_key_for_wayland(char, keyboard_state):
    global gnome_current_layout_index

    keysym = xkb.keysym_from_name(char)

    keymap = keyboard_state.get_keymap()
    num_mods = keymap.num_mods()

    for keycode in keymap:

        key_layouts_count = keymap.num_layouts_for_key(keycode)
        for layout in range(key_layouts_count):

            num_levels = keymap.num_levels_for_key(keycode, layout)

            for level in range(0, num_levels):
                mod_masks_for_level = keymap.key_get_mods_for_level(keycode, layout, level)

                if len(mod_masks_for_level) < 1:
                    continue

                keysyms = keymap.key_get_syms_by_level(keycode, layout, level)

                if len(keysyms) != 1 or keysyms[0] != keysym:
                    continue

                for mod_mask_index in range(0, len(mod_masks_for_level)):

                    mod_evdev_keys = []
                    for mod_index in range(0, num_mods):

                        if (mod_masks_for_level[mod_mask_index] & (1 << mod_index) == 0):
                            continue

                        mod_name = keymap.mod_get_name(mod_index)

                        mod_as_evdev_key = load_evdev_key_for_wayland(mod_name_to_specific_keysym_name(mod_name), keyboard_state)
                        mod_evdev_keys.append(mod_as_evdev_key)

                        if not mod_as_evdev_key:
                            continue

                    if len(mod_evdev_keys) > 0:
                        key = mod_evdev_keys + [EV_KEY.codes[int(keycode - 8)]]
                    else:
                        key = EV_KEY.codes[int(keycode - 8)]


                    if gnome_current_layout_index is not None:
                        layout_is_active = gnome_current_layout_index % key_layouts_count == layout
                    else:
                        layout_is_active = keyboard_state.layout_index_is_active(layout, xkb.StateComponent.XKB_STATE_LAYOUT_EFFECTIVE)

                    enable_key(key)

                    if layout_is_active:
                        set_evdev_key_for_char(char, key)
                        return key

def wl_load_keymap_state():
    global keyboard_state, keymap_loaded, coactivator_keys, udev

    log.debug("Wayland will try to load keymap")

    enabled_keys = len(enabled_evdev_keys)

    for char in get_keysym_name_associated_to_evdev_key_reflecting_current_layout().copy():
        load_evdev_key_for_wayland(char, keyboard_state)

    # one or more changed to something not enabled yet to send using udev device? -> udev device has to be re-created
    #
    # BUT only reset if event is not first one - driver is starting and keymap is not loaded yet
    if len(enabled_evdev_keys) > enabled_keys and keymap_loaded and udev:
        reset_udev_device()

    keymap_loaded = True

    log.debug("Wayland loaded keymap succesfully")
    log.debug(get_keysym_name_associated_to_evdev_key_reflecting_current_layout())

    load_evdev_keys_for_coactivator_modifiers(coactivator_keys)

def extract_icons(app_specific_shortcuts):
    icons = {}

    if not isinstance(app_specific_shortcuts, dict):
        return icons

    for name, definition in app_specific_shortcuts.items():
        if not isinstance(definition, dict):
            continue

        icon = definition.get("icon")
        if icon:
            icons[name] = icon

    return icons

def pad_to_minimum(arr, minimum):
    if arr is None:
        arr = []
    return arr + [None] * max(0, minimum - len(arr))

def select_profile_context(window_binary_local, shortcuts):
    """(context name, shortcuts) in force for the profile ``shortcuts``: its first
    context that matches the window's title (a pushed one, else the polled one) and
    the mode the linked application reports; the plain profile when none does."""
    if shortcuts_for_context is None or not isinstance(shortcuts, dict) or not shortcuts.get("_contexts"):
        return "", shortcuts
    window_title_now = pushed_window_title if pushed_window_title is not None else window_title
    link = app_link
    mode = link.context_for(window_binary_local).get("mode", "") if link is not None else ""
    return shortcuts_for_context(shortcuts, window_title_now, mode)

def window_was_changed(window_binary_local, force=False):
    global stop_threads, window_binary, window_title, app_name, app_specific_shortcuts, multi_app_mode, multi_app_mode_titles, multi_app_mode_icons,\
        center_activated, title, context_name

    # GNOME's fallback window query exposes a title but no application id.
    # Once the Shell extension has supplied an id over D-Bus, treating that
    # empty fallback as a new window would immediately replace an
    # application-specific profile (for example Darktable) with "none".
    if xdg_session_type == "wayland" and window_binary_local is None and window_binary is not None:
        return

    app_name_local, app_specific_shortcuts = get_appropriate_app_name_and_shortcuts(window_binary_local, window_title)
    context_local, app_specific_shortcuts = select_profile_context(window_binary_local, app_specific_shortcuts)
    multi_app_mode_local, multi_app_mode_titles_local, multi_app_mode_icons_local = is_multifunction(app_specific_shortcuts)

    update = False

    focus_changed = window_binary_local != window_binary
    if focus_changed:
        window_binary = window_binary_local
        update = True

    app_name_changed = app_name_local != app_name
    if app_name_changed:
        app_name = app_name_local
        update = True

    # A context of the same profile changes the buttons and what DriverState
    # says; the dial starts again only when its menu differs (checked below).
    if context_local != context_name:
        context_name = context_local
        log.info("Profile context: %s", context_local or "(none)")
        publish_driver_state()

    if focus_changed and not force and app_name_local == "none" and window_binary_local:
        suggest_profile(window_binary_local)

    if multi_app_mode_local != multi_app_mode:
        multi_app_mode = multi_app_mode_local
        update = True

    padded_titles = pad_to_minimum(multi_app_mode_titles_local, slices_minimum_count)
    if padded_titles != multi_app_mode_titles:
        multi_app_mode_titles = padded_titles
        update = True

    padded_icons = pad_to_minimum(multi_app_mode_icons_local, slices_minimum_count)
    if padded_icons != multi_app_mode_icons:
        multi_app_mode_icons = padded_icons
        update = True

    if not update and not force:
        return

    # The window watcher starts before the touch-event loop creates its
    # gesture state, so treat that early state as "menu active".
    current_title = globals().get("title")
    selected = globals().get("center_activated", False)
    if multi_app_mode and app_name_changed is False and (not submenu or is_group(submenu)):
        if submenu and not selected:
            # Configuration reload with a group's list open: keep it open.
            show_submenu()
            return
        if selected and current_title in (submenu_titles() if submenu else multi_app_mode_titles):
            # Configuration reload: keep the selected function, refresh its look.
            send_function_details(current_title)
            return

    show_initial_function_state()


def check_window():
    global stop_threads, window_binary, window_title, app_name, app_specific_shortcuts, multi_app_mode, multi_app_mode_titles, multi_app_mode_icons,\
        center_activated, title

    while not stop_threads:
        window_binary_local, window_title = get_active_window_title()
        window_was_changed(window_binary_local)
        sleep(0.5)


def check_gnome_layout():
    global stop_threads, gnome_current_layout, gnome_current_layout_index, keyboard_state, display_wayland_var, display_var

    while not stop_threads:

        mru_sources = gsettingsGet('org.gnome.desktop.input-sources', 'mru-sources')
        try:
          mru_sources_evaluated = ast.literal_eval(mru_sources.decode())
        except:
          mru_sources_evaluated = []

        sources = gsettingsGet('org.gnome.desktop.input-sources', 'sources')
        try:
          sources_evaluated = ast.literal_eval(sources.decode())
        except:
          sources_evaluated = []

        if len(mru_sources_evaluated) > 0 and mru_sources_evaluated[0] in sources_evaluated:

            mru_layout_index = sources_evaluated.index(mru_sources_evaluated[0])
            mru_layout = mru_sources_evaluated[0][1].split("+")[0]

            if display_wayland_var or keyboard_state:
                if keymap_rule_names_sources is not None and keymap_rule_names_sources != sources:
                    # the keymap is ours (no pywayland): the layouts changed
                    load_keymap_from_rule_names()
                if keyboard_state and gnome_current_layout_index is not mru_layout_index:

                    gnome_current_layout_index =  mru_layout_index
                    gnome_current_layout = mru_layout
                    wl_load_keymap_state()

            elif gnome_current_layout != mru_layout:

                    try:
                        cmd = ['setxkbmap', mru_layout, '-display', display_var]

                        log.debug(cmd)
                        subprocess.call(cmd)

                        gnome_current_layout = mru_layout
                        gnome_current_layout_index =  mru_layout_index
                    except:
                        log.exception('setxkbmap set failed')

        else:

            current = gsettingsGet('org.gnome.desktop.input-sources', 'current')

            current_evaluated = None
            try:
              current_evaluated = ast.literal_eval(current.decode().split(" ")[1])
            except:
              pass

            if current_evaluated is not None and current_evaluated < len(sources_evaluated):
                layout = sources_evaluated[current_evaluated][1].split("+")[0]

                # first run, would be unnecessary duplicated loading x11 keymap because X.org server notify all clients at start about Mapping and setxkbmap would trigger new second notify
                if gnome_current_layout == None:
                    gnome_current_layout = layout

                elif gnome_current_layout != layout:

                    try:
                        cmd = ['setxkbmap', layout, '-display', display_var]

                        log.debug(cmd)
                        subprocess.call(cmd)

                        gnome_current_layout = layout
                    except:
                        log.exception('setxkbmap set failed')

        sleep(0.5)


def cleanup():
    global dialpad, display, display_wayland, stop_threads, config_watcher, xkb_conn, sock, dbus_service

    log.info("Clean up started")

    # Never leave a held Ctrl behind (Ctrl + wheel zoom).
    try:
        release_scroll_modifiers()
    except Exception:
        log.exception("Failed to release scroll modifiers")

    # try deactivate first
    try:
        if dialpad:

            dialpad = False
            deactivate_dialpad()
            log.info("DialPad deactivated")

        # The action thread may not get to it before the driver exits.
        if emulation:
            set_touchpad_prop_send_events(1)

        # then clean up
        stop_threads=True
        try:
            action_queue.put_nowait(None)
        except Exception:
            pass

        if fd_t is not None:
            fd_t.close()
        stop_kwin_script()
        stop_app_link()
        for source in input_sources:
            try:
                source.close()
            except Exception:
                log.exception("Failed to close the %s", source.name)
        close_mouse()

        if display_wayland:
            display_wayland.disconnect()

        if display:
            try:
                display.close()
            # because may be already closed (e.g. closed connection by server in load_keymap_listener_x11)
            except:
                pass

        try:
            if xkb_conn is not None:
                xkb_conn.disconnect()
        except Exception:
            pass

        if config_watcher:
            config_watcher.close()
            log.info("check_config_values_changes: watching config file ended")

        if sock:
            sock.close()

        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)

        log.info("Clean up finished")
    except:
        log.exception("Clean up error")
        pass
    finally:
        if dbus_service:
            try:
                dbus_service.stop()
            except Exception:
                log.exception("Failed to stop the DialPad D-Bus service")
            dbus_service = None

threads = []
stop_threads = False
config_watcher = None

def isEvent(event):
    if hasattr(event, "name") and (event in EV_KEY.codes or event in EV_REL.codes):
        return True
    else:
        return False

def isEventList(events):
    if type(events) is list:
        for event in events:
            if not isEvent(event):
                return False
        return True
    else:
        return False

def enable_key(key_or_key_combination, reset_udev = False):
    global enabled_evdev_keys, dev, udev

    enabled_keys_count = len(enabled_evdev_keys)

    if isEvent(key_or_key_combination):
      if key_or_key_combination not in enabled_evdev_keys:
          enabled_evdev_keys.append(key_or_key_combination)
          dev.enable(key_or_key_combination)
    elif isEventList(key_or_key_combination):
      for key in key_or_key_combination:
        if key not in enabled_evdev_keys:
          enabled_evdev_keys.append(key)
          dev.enable(key)

    # one or more changed to something not enabled yet to send using udev device? -> udev device has to be re-created
    if len(enabled_evdev_keys) > enabled_keys_count and reset_udev:
      reset_udev_device()

def set_evdev_key_for_char(char, evdev_key):
    global keysym_name_associated_to_evdev_key_reflecting_current_layout

    # lazy initialization because of loading modifiers inside Shift & Control
    if not keysym_name_associated_to_evdev_key_reflecting_current_layout:
        set_defaults_keysym_name_associated_to_evdev_key_reflecting_current_layout()

    keysym_name_associated_to_evdev_key_reflecting_current_layout[char] = evdev_key

def load_evdev_key_for_x11(char):
    global display, keysym_name_associated_to_evdev_key_reflecting_current_layout

    keysym = Xlib.XK.string_to_keysym(char)

    if keysym == 0:
      return

    keycode = display.keysym_to_keycode(keysym)
    key = EV_KEY.codes[int(keycode) - 8]

    # bare
    if display.keycode_to_keysym(keycode, 0) == keysym:
      pass
    # shift
    elif display.keycode_to_keysym(keycode, 1) == keysym:
      key = [load_evdev_key_for_x11(mod_name_to_specific_keysym_name('Shift')), key]
    # altgr
    elif display.keycode_to_keysym(keycode, 2) == keysym:
      key = [load_evdev_key_for_x11(mod_name_to_specific_keysym_name('AltGr')), key]
    # shift altgr
    elif display.keycode_to_keysym(keycode, 3) == keysym:
      key = [load_evdev_key_for_x11(mod_name_to_specific_keysym_name('Shift')), load_evdev_key_for_x11(mod_name_to_specific_keysym_name('AltGr')), key]

    set_evdev_key_for_char(char, key)

    enable_key(key)

    return key

uinput_rebuild_lock = threading.Lock()

# necessary when are new keys enabled
def reset_udev_device():
    """Replace the virtual device by one with the keys enabled since."""
    global udev, scroll_modifiers_held

    # One rebuild at a time, so the newest capabilities always win.
    with uinput_rebuild_lock:
        new_device = dev.create_uinput_device()
        log.info("New device at {} ({})".format(new_device.devnode, new_device.syspath))

        # Sleep for a little bit so udev, libinput, Xorg, Wayland, ... all have
        # had a chance to see the device and initialize it. Until then events
        # keep going to the old device.
        sleep(0.5)

        with scroll_modifier_lock, uinput_lock:
            old_device, udev = udev, new_device
            # Keys held for zoom scrolling are released on the device that
            # pressed them; the next scroll step presses them on the new one.
            set_keys_held(scroll_modifiers_held, False, device=old_device)
            scroll_modifiers_held = []
            # Keys a SpaceMouse button still holds go down on the new device too.
            set_keys_held(list(spacemouse_applied_keys), True)
        close_virtual_device(old_device)

def close_virtual_device(device):
    """Remove a replaced virtual device at once.

    python-libevdev keeps devices in reference cycles: without this the old
    DialPad keyboard stayed until the garbage collector ran, and the desktop
    saw two of them.
    """
    uinput = getattr(device, "_uinput", None)
    if uinput is None:
        return
    # Later sends to it fail instead of reaching a destroyed device.
    device._uinput = None
    try:
        handle = uinput._uinput_device
        if handle is not None:
            uinput._uinput_device = None
            uinput._uinput_destroy(handle)
        log.info("Old virtual device removed")
    except Exception:
        log.debug("Could not remove the old virtual device now", exc_info=True)

def load_evdev_keys_for_x11():
  global enabled_evdev_keys, keymap_loaded, coactivator_keys, udev

  log.debug("X11 will try to load keymap")

  enabled_keys_count = len(enabled_evdev_keys)

  for char in get_keysym_name_associated_to_evdev_key_reflecting_current_layout().copy():
    load_evdev_key_for_x11(char)

  # one or more changed to something not enabled yet to send using udev device? -> udev device has to be re-created
  #
  # BUT only reset if event is not first one - driver is starting and keymap is not loaded yet
  if len(enabled_evdev_keys) > enabled_keys_count and keymap_loaded and udev:
    reset_udev_device()

  keymap_loaded = True

  log.debug("X11 loaded keymap succesfully")
  log.debug(get_keysym_name_associated_to_evdev_key_reflecting_current_layout())

  load_evdev_keys_for_coactivator_modifiers(coactivator_keys)

def wl_keyboard_keymap_handler(keyboard, format_, fd, size):
    global keyboard_state

    keymap_data = mmap.mmap(
       fd, size, prot=mmap.PROT_READ, flags=mmap.MAP_PRIVATE
    )
    xkb_context = xkb.Context()
    keymap = xkb_context.keymap_new_from_buffer(keymap_data, length=size - 1)
    keymap_data.close()

    keyboard_state = keymap.state_new()

    wl_load_keymap_state()

def wl_registry_handler(registry, id_, interface, version):
  log.debug(registry)
  log.debug(id_)
  log.debug(interface)
  log.debug(version)
  if interface == "wl_seat":
    seat = registry.bind(id_, WlSeat, version)
    keyboard = seat.get_keyboard()
    keyboard.dispatcher["keymap"] = wl_keyboard_keymap_handler

def load_keymap_listener_wayland():
    global stop_threads, display_wayland_var, display_wayland

    try:
        display_wayland = Display(display_wayland_var)
        display_wayland.connect()
        registry = display_wayland.get_registry()
        registry.dispatcher["global"] = wl_registry_handler
        display_wayland.dispatch(block=True)
        display_wayland.roundtrip()

        while not stop_threads and display_wayland.dispatch(block=True) != -1:
            pass
    except:
        if not keymap_loaded:
            log.exception("Wayland load keymap listener error, the keyboard layout comes from the desktop settings")
            load_keymap_from_rule_names()
            return
        log.exception("Wayland load keymap listener error. Exiting")
        os.kill(os.getpid(), signal.SIGUSR1)

# set when the keymap is compiled from layout names instead of read from the compositor
keymap_rule_names_sources = None

def load_keymap_from_rule_names():
    """Wayland without pywayland: compile the keymap from the layouts the
    desktop is set to (GNOME input sources, localectl, XKB_DEFAULT_*)."""
    global keyboard_state, keymap_rule_names_sources

    keymap_rule_names_sources = gsettingsGet('org.gnome.desktop.input-sources', 'sources')
    names, source = keyboard_layout.rule_names()
    context = xkb.Context(no_environment_names=True)
    try:
        keymap = context.keymap_new_from_names(**names)
    except xkb.XKBKeymapCreationFailure:
        log.warning("Keyboard layout %s from %s does not compile, using %s", names, source, keyboard_layout.FALLBACK)
        names, source = dict(keyboard_layout.FALLBACK), "default"
        keymap = context.keymap_new_from_names(**names)
    log.info("Keyboard layout %s from %s", names, source)

    keyboard_state = keymap.state_new()

    wl_load_keymap_state()

def load_keymap_listener_x11():
    global stop_threads, display, listening_touchpad_events_started

    try:

      while not stop_threads:

        event = display.next_event()
        if event.type == Xlib.X.MappingNotify and event.count > 0 and event.request == Xlib.X.MappingKeyboard:

          if listening_touchpad_events_started or not keymap_loaded:
            display.refresh_keyboard_mapping(event)
            load_evdev_keys_for_x11()
            #raise Xlib.error.ConnectionClosedError("fd") # testing purpose only
    except:
      log.exception("X11 load keymap listener error. Exiting")
      os.kill(os.getpid(), signal.SIGUSR1)

last_app = None

def _extract_app_info(acc):
    try:
        app = acc.getApplication()
        app_name = app.name if app else None
        title = acc.name
        return app_name, title
    except Exception:
        return None, None

def on_window_activated(event):
    global window_title, last_app

    try:
        # only activation event
        if event.detail1 != 1:
            return

        acc = event.source
        app_name_local, window_title = _extract_app_info(acc)

        if not app_name_local:
            return

        # deduplication
        if app_name_local == last_app:
            return

        last_app = app_name_local

        on_dbus_active_application(app_name_local)
    except Exception:
        pass
        
def accessibility_bus_reachable(timeout_ms=500):
    """Whether the accessibility bus answers. pyatspi aborts the whole process
    (a GLib error, not an exception) when it cannot reach that bus, e.g. when
    at-spi-bus-launcher's bus is gone, so it is asked first: the address from
    AT_SPI_BUS_ADDRESS or org.a11y.Bus, then a connection to it."""
    try:
        from gi.repository import Gio, GLib

        address = os.environ.get("AT_SPI_BUS_ADDRESS", "")
        if not address:
            # Own connections: the shared session bus exits the process when it goes away.
            flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
            session_address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
            session = Gio.DBusConnection.new_for_address_sync(session_address, flags, None, None)
            try:
                reply = session.call_sync(
                    "org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress", None,
                    GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, timeout_ms, None,
                )
                address = reply.unpack()[0]
            finally:
                session.close_sync(None)
        flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        Gio.DBusConnection.new_for_address_sync(address, flags, None, None).close_sync(None)
        return True
    except Exception as error:
        log.info("Accessibility bus not reachable, focused windows are found another way: %s", error)
        return False

def check_window_pyatspi():
    pyatspi.Registry.registerEventListener(
        on_window_activated,
        "object:state-changed:active",
    )

    pyatspi.Registry.start()

try:

    # init the socket
    init_socket()

    # A SpaceMouse run that was killed may have left spacenavd muted: undo it
    # now, whether or not SpaceMouse mode is on any more.
    restore_spacenav_at_start()

    # D-Bus startup runs independently so an unavailable session bus cannot delay the driver.
    init_dbus()
    start_app_link()

    # The keyboard brightness key also dims or brightens the DialPad light;
    # changes arrive in the D-Bus service's main loop.
    keyboard_backlight = KeyboardBacklight(on_change=lambda _level: enqueue_action(apply_backlight))
    if dialpad:
        enqueue_action(apply_backlight)

    # init the device
    initialize_virtual_device()

    if xdg_session_type == "wayland" and display_wayland and PYWAYLAND_AVAILABLE:
        t = threading.Thread(target=load_keymap_listener_wayland)
        t.daemon = True
        threads.append(t)
        t.start()
    elif xdg_session_type != "x11":
        load_keymap_from_rule_names()

    if xdg_session_type == "x11" and display and X11_LIBS_AVAILABLE:

        # when is the driver starting event is not received
        load_evdev_keys_for_x11()

        t = threading.Thread(target=load_keymap_listener_x11)
        t.daemon = True
        threads.append(t)
        t.start()

    # wait until is keymap loaded
    while not keymap_loaded:
        sleep(0.5)

    if emulation or touchpad_free:
        # The emulated dial takes the whole touchpad and the mouse dial the
        # mouse: never start with it on, and give the pointer back if the
        # driver stopped while it was on.
        config_lock.acquire()
        read_config_file()
        config_set(CONFIG_ENABLED, False, already_has_lock=True)
        config_lock.release()
        if emulation:
            set_touchpad_prop_send_events(1)
        # A stopped service must not leave the touchpad switched off or the
        # mouse grabbed.
        signal.signal(signal.SIGTERM, lambda _signal, _frame: sys.exit(0))

    # Load config values
    load_all_config_values()
    apply_gui_settings()
    config_lock.acquire()
    config_save()
    config_lock.release()

    # check changes in config values; the save above happened before the
    # watch exists, so it is not read back
    config_watcher = DirectoryWatcher(os.path.abspath(config_file_dir), check_config_values_changes, config_dir_event)
    try:
        config_watcher.start()
    except Exception:
        log.exception("Cannot watch the config dir %s; changes apply after a restart", config_file_dir)
        config_watcher = None

    t = threading.Thread(target=check_dialpad_automatical_disable_or_idle_due_inactivity)
    t.daemon = True
    threads.append(t)
    t.start()

    if keyboard:
        t = threading.Thread(target=listen_keyboard_events)
        t.daemon = True
        threads.append(t)
        t.start()

    t = threading.Thread(target=check_gnome_layout)
    t.daemon = True
    threads.append(t)
    t.start()

    desktop_by_pyatspi = None
    try:
        if PYATSPI_AVAILABLE and accessibility_bus_reachable():
            desktop_by_pyatspi = pyatspi.Registry.getDesktop(0)
    except:
        pass

    if PYATSPI_AVAILABLE and desktop_by_pyatspi:
        # for current window
        window_binary_local, window_title = get_active_window_title()
        window_was_changed(window_binary_local)

        # then listen for window change event
        t = threading.Thread(target=check_window_pyatspi)
        t.daemon = True
        threads.append(t)
        t.start()
    else:
        t = threading.Thread(target=check_window)
        t.daemon = True
        threads.append(t)
        t.start()

    t = threading.Thread(target=action_worker, name="dialpad-actions")
    t.daemon = True
    threads.append(t)
    t.start()

    # The touchpad (or mouse, SpaceMouse) listener runs on the main thread
    if mouse_mode or spacemouse_mode:
        listen_input_sources()
    else:
        listen_touchpad_events()
except Exception:
    log.exception("Listening touchpad events unexpectedly failed")
finally:
    cleanup()
    log.info("Exiting")
    sys.exit(1)
