#!/usr/bin/env python3
"""Turnpad on the colour screen of a 3Dconnexion SpacePilot Pro.

A separate user service (asus-turnpad-screen.service, started by udev when the
device is plugged in). It follows the driver over D-Bus (OverlayState,
OverlayDetails, DriverState; Control for what the screen keys do), config.json
(a directory monitor), and spacenavd as one more client (raw buttons for press
feedback, raw axes for the motion page; it never changes spacenavd's settings),
and draws the pages of screen/pages.py on the panel through screen/device.py.

    python3 /opt/asus-turnpad/screen/service.py

Exit status 3: nothing to do (switched off in the config, pyusb missing, no
supported screen at start); the unit does not restart then. 0: stopped by a
signal or the screen was unplugged (udev starts it again on the next plug).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import fields as dataclass_fields
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EXIT_UNSUPPORTED = 3
log = logging.getLogger("turnpad-screen")

# At most this many frames per second.
MAX_FPS = 30
CONFIG_RELOAD_MS = 200
SAVE_DELAY_MS = 1000
TICK_MS = 100
SPACENAV_RETRY_S = 2.0
RECONNECT_FOR_S = 5.0
RECONNECT_EVERY_MS = 500
# Raw axis values below this do not wake the screen or count as motion (noise).
MOTION_NOISE = 25
# The hidden game (screen/game.py): a frame every GAME_FRAME_MS while it plays;
# the driver is asked to silence the other spacenavd clients for MUTE_LEASE_MS,
# renewed every MUTE_RENEW_S, so a crashed service lets go within seconds.
GAME_FRAME_MS = 33
MUTE_LEASE_MS = 3000
MUTE_RENEW_S = 1.0
GAME_FILE_NAME = "screen-game.json"


# --- pure helpers ------------------------------------------------------------

def merge_screen_fields(config, values: dict) -> list[str]:
    """Set ``values`` on ``config.screen``; the names that are ScreenConfig fields."""
    from config.schema import ScreenConfig

    known = {item.name for item in dataclass_fields(ScreenConfig)}
    applied = []
    for name, value in values.items():
        if name in known:
            setattr(config.screen, name, list(value) if isinstance(value, list) else value)
            applied.append(name)
    return applied


def save_screen_fields(values: dict) -> bool:
    """Write only the fields of the ``screen`` section into config.json: the
    file is read again (it may have changed), the section changed and saved
    atomically by the project's save function."""
    from config.schema import ConfigError, load_config, save_config

    try:
        config = load_config()
    except (ConfigError, OSError) as error:
        log.warning("not saving the screen settings, config.json is unusable: %s", error)
        return False
    if not merge_screen_fields(config, values):
        return False
    try:
        save_config(config)
    except OSError as error:
        log.warning("could not save the screen settings: %s", error)
        return False
    return True


def read_config():
    """config.json as a configuration; the defaults when there is no file; None when unusable."""
    from config.schema import CONFIG_FILE, ConfigError, _default_config, config_from_text

    try:
        return config_from_text(CONFIG_FILE.read_text())
    except FileNotFoundError:
        return _default_config()
    except (ConfigError, OSError) as error:
        log.warning("config.json unusable: %s", error)
        return None


def parse_json_object(text) -> dict | None:
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def start_checks(config, present: list):
    """(exit status, reason) when the service has nothing to do, else None."""
    if config is not None and not config.screen.enabled:
        return EXIT_UNSUPPORTED, "the screen is switched off in the settings"
    if not present:
        return EXIT_UNSUPPORTED, "no SpaceMouse screen is plugged in"
    return None


def game_file() -> Path:
    from config.schema import CONFIG_DIR

    return CONFIG_DIR / GAME_FILE_NAME


def read_high_score(path) -> int:
    """The game's best score; 0 when there is no file or it is unusable."""
    try:
        value = json.loads(Path(path).read_text()).get("high_score", 0)
    except (OSError, ValueError, AttributeError):
        return 0
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def write_high_score(path, score: int) -> bool:
    """Write the best score atomically (a temporary file next to it, then a rename)."""
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps({"high_score": int(score)}) + "\n")
        os.replace(temporary, path)
    except OSError as error:
        log.warning("could not save the game's high score: %s", error)
        return False
    return True


class MuteHeartbeat:
    """When to ask the driver to silence the other spacenavd clients: ``update()``
    gives the Control command to send now, or None. While the game plays it is
    ``mute:MUTE_LEASE_MS`` at once and again every ``MUTE_RENEW_S``; when the
    game stops playing a single ``mute:0``."""

    def __init__(self, lease_ms: int = MUTE_LEASE_MS, renew_s: float = MUTE_RENEW_S):
        self.lease_ms = lease_ms
        self.renew_s = renew_s
        self.active = False
        self.sent_at = 0.0

    def update(self, playing: bool, now: float):
        if playing:
            if not self.active or now - self.sent_at >= self.renew_s:
                self.active = True
                self.sent_at = now
                return f"mute:{self.lease_ms}"
            return None
        if self.active:
            self.active = False
            return "mute:0"
        return None


# --- the service -------------------------------------------------------------

class ScreenService:
    def __init__(self, config, device):
        from gi.repository import GLib

        self.GLib = GLib
        self.loop = GLib.MainLoop()
        self.exit_status = 0
        self.config = config
        self.device = device
        self.screen = device.screen
        self.key_bits = {key.id: key.bit for key in self.screen.keys}
        self.bus = None
        self.subscriptions = []
        self.watch = 0
        self.monitors = []
        self.reload_source = 0
        self.save_source = 0
        self.pending_save = {}
        self.tick_source = 0
        self.render_source = 0
        self.last_render = 0.0
        self.dirty = True
        self.last_frame = None
        self.last_clock = ""
        self.level_applied = None
        self.reconnect_until = 0.0
        self.stopping = False
        # What the driver says; empty while it is not running.
        self.driver = {}
        self.state = None
        self.details = None
        self.axes = [0] * 6
        self.motion_dirty = False
        self.spacemouse_up = False
        self.key_mask = 0
        self.key_thread = None
        self.key_stop = threading.Event()
        self.spacenav_stop = threading.Event()
        self.spacenav_thread = None
        self.skin = None
        # Catalogs of linked applications by app name (GetAppCommands): {"name", "index"}; the
        # faces of app commands use the focused application's.
        self.catalogs = {}
        self.catalog_version = 0
        self.catalog_fetching = set()
        self.link_seen = ("", False)
        self.model_key = None
        # The hidden game: its frame timer, the cap's reader and the driver mute lease.
        self.game_source = 0
        self.game_last = 0.0
        self.cap = None
        self.heartbeat = MuteHeartbeat()
        self._build_ui()

    # --- model of the screen

    def _profile_name(self) -> str:
        return str(self.driver.get("profile") or "")

    def _app(self) -> str:
        return str(self.driver.get("app") or "").lower()

    def _catalog(self) -> dict:
        return self.catalogs.get(self._app()) or {}

    def _load_model(self) -> None:
        from screen import content, pages

        self.resolved = content.resolve(self.config, self.driver, self._catalog().get("index"))
        self.profile = self.resolved.profile
        self.settings = self.resolved.settings
        self.entries = self.resolved.entries
        self.model_key = content.resolution_key(self.config, self.driver, self.catalog_version)
        self.skin = pages.default_skin(self.config)

    def _build_ui(self) -> None:
        from screen.ui import ScreenUI

        self._load_model()
        self.ui = ScreenUI(self.settings, self._now())
        self.ui.high_score = read_high_score(game_file())
        self._sync_context()

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    def _sync_context(self) -> None:
        from screen import content, pages

        large = self.settings.text_size == "large"
        self.ui.set_context(content.face_buttons(self.entries, self.settings.show_long),
                            pages.device_steps(self.entries, large))

    def _refresh_model(self, reset_cap: bool = True) -> None:
        """Config, profile, context, layer or motion changed: new faces and
        settings, same UI state."""
        self._load_model()
        if reset_cap:
            self.cap = None  # the axes may have been configured differently
        self.run_effects(self.ui.apply_config(self.settings, keep=tuple(self.pending_save)))
        self._sync_context()
        self.dirty = True

    # --- start and stop

    def start(self) -> None:
        import gi
        from gi.repository import Gio

        self.Gio = Gio
        self._connect_dbus()
        self._watch_config()
        self.run_effects(self.ui.start(self._now()))
        self.tick_source = self.GLib.timeout_add(TICK_MS, self._on_tick)
        self._start_keys()
        self._start_spacenav()
        self.schedule_render()
        log.info("running on %s", self.device.present.model.name)

    def request_stop(self, status: int = 0) -> None:
        if self.stopping:
            return
        self.stopping = True
        self.exit_status = status
        self.loop.quit()

    def stop(self) -> None:
        """Last frame, then release everything."""
        from screen import canvas, pages

        self.key_stop.set()
        self.spacenav_stop.set()
        for source in (self.tick_source, self.render_source, self.reload_source, self.save_source, self.game_source):
            if source:
                self.GLib.source_remove(source)
        if self.heartbeat.active:
            self.control_sync("mute:0")
            self.heartbeat.active = False
        if self.save_source and self.pending_save:
            save_screen_fields(self.pending_save)
        for monitor in self.monitors:
            monitor.cancel()
        if self.bus is not None:
            for subscription in self.subscriptions:
                self.bus.signal_unsubscribe(subscription)
            if self.watch:
                self.Gio.bus_unwatch_name(self.watch)
        if self.device is not None and not self.device.closed:
            try:
                surface, cr = canvas.device_surface()
                pages.paint_stopped(cr)
                self.device.send(canvas.frame_bytes(surface))
            except Exception as error:  # noqa: BLE001
                log.debug("no last frame: %s", error)
            self.device.close()
        for thread in (self.key_thread, self.spacenav_thread):
            if thread is not None:
                thread.join(1.0)

    # --- D-Bus

    def _connect_dbus(self) -> None:
        from config import identity

        try:
            self.bus = self.Gio.bus_get_sync(self.Gio.BusType.SESSION, None)
        except self.GLib.Error as error:
            log.warning("no session bus, showing the configuration only: %s", error.message)
            return
        for name, handler in (("OverlayState", self._on_overlay_state), ("OverlayDetails", self._on_overlay_details),
                              ("DriverState", self._on_driver_state)):
            self.subscriptions.append(self.bus.signal_subscribe(
                identity.DBUS_NAME, identity.DBUS_INTERFACE, name, identity.DBUS_PATH, None,
                self.Gio.DBusSignalFlags.NONE, handler))
        self.watch = self.Gio.bus_watch_name_on_connection(
            self.bus, identity.DBUS_NAME, self.Gio.BusNameWatcherFlags.NONE,
            lambda *_: self._fetch_driver(), lambda *_: self._driver_gone())

    def _call(self, method, parameters, reply, done=None, failed=None) -> None:
        from config import identity

        def finished(bus, result):
            try:
                value = bus.call_finish(result).unpack()
            except self.GLib.Error as error:
                log.debug("%s failed: %s", method, error.message)
                if failed:
                    failed()
                return
            if done:
                done(value)

        self.bus.call(identity.DBUS_NAME, identity.DBUS_PATH, identity.DBUS_INTERFACE, method, parameters,
                      self.GLib.VariantType(reply), self.Gio.DBusCallFlags.NONE, 1000, None, finished)

    def _fetch_driver(self) -> None:
        self._call("GetDriverState", None, "(s)", lambda value: self._merge_driver(value[0]))
        self._call("GetOverlayDetails", None, "(s)", lambda value: self._merge_details(value[0]))

    def _driver_gone(self) -> None:
        if not self.driver and self.state is None:
            return
        self.driver, self.state, self.details = {}, None, None
        self._driver_changed()

    def control(self, command: str) -> None:
        if self.bus is None:
            return
        log.debug("control %s", command)
        self._call("Control", self.GLib.Variant("(s)", (command,)), "(b)")

    def control_sync(self, command: str) -> None:
        """Control without the main loop (at exit); a missing driver is fine."""
        from config import identity

        if self.bus is None:
            return
        try:
            self.bus.call_sync(identity.DBUS_NAME, identity.DBUS_PATH, identity.DBUS_INTERFACE, "Control",
                               self.GLib.Variant("(s)", (command,)), None, self.Gio.DBusCallFlags.NONE, 300, None)
        except self.GLib.Error as error:
            log.debug("%s failed: %s", command, error.message)

    def _on_overlay_state(self, _bus, _sender, _path, _interface, _signal, parameters) -> None:
        from overlay import look

        if self.state is None:
            self.state = look.new_state()
        look.apply_overlay_state(self.state, parameters.unpack())
        if "dial" not in self.driver:
            self._follow_dial(bool(self.state["enabled"]))
        self.dirty = True
        self.schedule_render()

    def _on_overlay_details(self, _bus, _sender, _path, _interface, _signal, parameters) -> None:
        self._merge_details(parameters.unpack()[0])

    def _merge_details(self, text) -> None:
        from overlay import look

        if self.details is None:
            self.details = look.new_details()
        look.merge_details(self.details, text)
        self.dirty = True
        self.schedule_render()

    def _on_driver_state(self, _bus, _sender, _path, _interface, _signal, parameters) -> None:
        self._merge_driver(parameters.unpack()[0])

    def _merge_driver(self, text) -> None:
        values = parse_json_object(text)
        if values is None:
            return
        from screen import content, ui as screen_ui

        old, self.driver = self.driver, values
        self._driver_changed()
        notice = content.change_notice(old, values)
        if notice is not None:
            seconds = 4.0 if notice.kind == "suggest" else screen_ui.NOTICE_SECONDS
            self.run_effects(self.ui.notify(self._now(), notice.title, notice.detail, seconds=seconds))

    def _follow_dial(self, on: bool) -> None:
        self.run_effects(self.ui.set_dial(on, self._now()))

    def _driver_changed(self) -> None:
        from screen import content

        self._fetch_catalog_if_needed()
        if content.resolution_key(self.config, self.driver, self.catalog_version) != self.model_key:
            changed = content.find_profile(self.config, self._profile_name()).name != self.profile.name
            self._refresh_model(reset_cap=changed)
        self._follow_dial(bool(self.driver.get("dial", self.state["enabled"] if self.state else False)))
        self.dirty = True
        self.schedule_render()

    def _fetch_catalog_if_needed(self) -> None:
        """The focused application's commands (GetAppCommands) when its link comes
        up or the application is new to us; cached per application."""
        app, link = self._app(), bool(self.driver.get("app_link"))
        seen, self.link_seen = self.link_seen, (app, link)
        if not app or self.bus is None or app in self.catalog_fetching:
            return
        if app in self.catalogs and not (link and seen != (app, True)):
            return
        self.catalog_fetching.add(app)
        self._call("GetAppCommands", self.GLib.Variant("(s)", (app,)), "(s)",
                   lambda value: self._catalog_arrived(app, value[0]), lambda: self.catalog_fetching.discard(app))

    def _catalog_arrived(self, app: str, text: str) -> None:
        from screen import content

        self.catalog_fetching.discard(app)
        data = parse_json_object(text) or {}
        self.catalogs[app] = {"name": str(data.get("name") or ""), "index": content.catalog_index(data)}
        self.catalog_version += 1
        self._driver_changed()

    # --- config.json

    def _watch_config(self) -> None:
        from config.schema import CONFIG_DIR, CONFIG_FILE
        from config.skins import SKINS_DIR

        self._watch_directory(CONFIG_DIR, lambda name: name in (CONFIG_FILE.name, "skins"))
        self._watch_directory(SKINS_DIR, lambda name: name.endswith((".json", ".png")))

    def _watch_directory(self, path, relevant) -> None:
        try:
            monitor = self.Gio.File.new_for_path(str(path)).monitor_directory(self.Gio.FileMonitorFlags.WATCH_MOVES, None)
        except self.GLib.Error:
            return

        def changed(_monitor, file, other, _event):
            names = [item.get_basename() for item in (file, other) if item is not None]
            if any(relevant(name) for name in names):
                self._schedule_reload()

        monitor.connect("changed", changed)
        self.monitors.append(monitor)

    def _schedule_reload(self) -> None:
        if self.reload_source:
            self.GLib.source_remove(self.reload_source)

        def reload():
            self.reload_source = 0
            self.reload_config()
            return self.GLib.SOURCE_REMOVE

        self.reload_source = self.GLib.timeout_add(CONFIG_RELOAD_MS, reload)

    def reload_config(self) -> None:
        config = read_config()
        if config is None:
            return  # keep what is shown
        self.config = config
        if not config.screen.enabled:
            log.info("the screen was switched off in the settings")
            self.request_stop(EXIT_UNSUPPORTED)
            return
        self._refresh_model()
        self.schedule_render()

    def _queue_save(self, values: dict) -> None:
        self.pending_save.update(values)
        if self.save_source:
            self.GLib.source_remove(self.save_source)

        def save():
            self.save_source = 0
            values, self.pending_save = self.pending_save, {}
            save_screen_fields(values)
            return self.GLib.SOURCE_REMOVE

        self.save_source = self.GLib.timeout_add(SAVE_DELAY_MS, save)

    # --- effects of the UI

    def run_effects(self, effects) -> None:
        for effect in effects:
            kind = effect[0]
            if kind == "control":
                self.control(effect[1])
            elif kind == "brightness":
                self._set_brightness(effect[1])
            elif kind == "save":
                self._queue_save(effect[1])
            elif kind == "redraw":
                self.dirty = True
            elif kind == "high_score":
                write_high_score(game_file(), effect[1])
        self._sync_game()
        if self.dirty:
            self.schedule_render()

    # --- the hidden game

    def _sync_game(self) -> None:
        """Keep the game's frame timer and the driver's mute lease in step with
        the game: both only while it plays."""
        game = self.ui.game
        playing = game is not None and game.running
        if playing and not self.game_source:
            self.game_last = self._now()
            self.game_source = self.GLib.timeout_add(GAME_FRAME_MS, self._game_tick)
        command = self.heartbeat.update(playing, self._now())
        if command:
            self.control(command)

    def _game_tick(self) -> bool:
        game = self.ui.game
        if game is None or not game.running:
            self.game_source = 0
            return False
        now = self._now()
        dt, self.game_last = now - self.game_last, now
        if self.cap is None:
            from screen.game import CapReader

            self.cap = CapReader.from_config(self.config.spacemouse)
        tilt, twist, fire = self.cap.read(self.axes)
        self.run_effects(self.ui.game_step(dt, tilt, twist, fire, now))
        self.dirty = True
        self.schedule_render()
        return True

    def _set_brightness(self, level: int) -> None:
        if self.device is None or level == self.level_applied:
            return
        from screen.device import DeviceGone

        try:
            self.device.set_brightness(level)
            self.level_applied = level
        except DeviceGone as error:
            self._device_lost(error)

    # --- keys of the screen

    def _start_keys(self) -> None:
        self.key_stop.clear()
        self.key_thread = threading.Thread(target=self._key_loop, args=(self.device,), name="screen-keys", daemon=True)
        self.key_thread.start()

    def _key_loop(self, device) -> None:
        from screen.device import DeviceGone

        mask = 0
        while not self.key_stop.is_set():
            try:
                new = device.read_keys(200)
            except DeviceGone as error:
                if not self.key_stop.is_set():
                    self.GLib.idle_add(self._device_lost, error)
                return
            if new is None or new == mask:
                continue
            old, mask = mask, new
            self.GLib.idle_add(self._on_keys, old, new)

    def _on_keys(self, old: int, new: int) -> bool:
        self.run_effects(self.ui.keys_changed(old, new, self.key_bits, self._now()))
        return False

    # --- spacenavd

    def _start_spacenav(self) -> None:
        self.spacenav_stop.clear()
        self.spacenav_thread = threading.Thread(target=self._spacenav_loop, name="screen-spacenav", daemon=True)
        self.spacenav_thread.start()

    def _spacenav_loop(self) -> None:
        import select

        from spacenav_client import (EVMASK_RAWAXIS, EVMASK_RAWBUTTON, SpacenavClient, socket_candidates)

        while not self.spacenav_stop.is_set():
            client = SpacenavClient()
            try:
                connected = client.connect(socket_candidates(self.config.spacemouse.socket))
                if connected and client.proto >= 1 and client.set_evmask(EVMASK_RAWAXIS | EVMASK_RAWBUTTON):
                    client.send_name("Turnpad screen")
                    self.GLib.idle_add(self._set_spacemouse, True)
                    while not self.spacenav_stop.is_set():
                        ready, _, _ = select.select([client.sock], [], [], 0.2)
                        events = client.read_events() if ready or client.queue else []
                        if events:
                            self.GLib.idle_add(self._on_spacenav_events, events)
            except (OSError, ConnectionError, ValueError) as error:
                log.debug("spacenavd: %s", error)
            finally:
                client.close()
            self.GLib.idle_add(self._set_spacemouse, False)
            self.spacenav_stop.wait(SPACENAV_RETRY_S)

    def _set_spacemouse(self, up: bool) -> bool:
        if up != self.spacemouse_up:
            self.spacemouse_up = up
            self.dirty = True
            self.schedule_render()
        return False

    def _on_spacenav_events(self, events) -> bool:
        from spacenav_client import UEV_RAWAXIS, UEV_RAWBUTTON

        now = self._now()
        for event in events:
            if event.type == UEV_RAWAXIS:
                index, value = event.data[0], event.data[1]
                if 0 <= index < 6:
                    self.axes[index] = value
                    if abs(value) >= MOTION_NOISE:
                        self.run_effects(self.ui.activity(now, "motion"))
                    self.motion_dirty = True
            elif event.type == UEV_RAWBUTTON:
                index, pressed = event.data[0], event.data[1]
                if pressed:
                    self._button_pressed(index, now)
        if self.motion_dirty and self.ui.page == "motion":
            self.dirty = True
        self.schedule_render()
        return False

    def _button_pressed(self, index: int, now: float) -> None:
        from screen import content

        self.run_effects(self.ui.activity(now, "button"))
        found = content.entry_for_index(self.entries, index)
        if found is None:
            return
        entry, face = found
        if face is None or face.kind in ("modifier", "unbound"):
            return
        # A function key's face is on the keys page at its own position.
        faces = content.face_buttons(self.entries, self.settings.show_long)
        self.run_effects(self.ui.feedback(now, index if index in faces else None, face.title or "–", face.detail, face.color))

    # --- clock and idle timers

    def _on_tick(self) -> bool:
        from screen import pages

        self.run_effects(self.ui.tick(self._now()))
        self._sync_game()
        text = pages.clock_text(datetime.now(), self.settings)
        if text != self.last_clock:
            self.last_clock = text
            self.dirty = True
        if self.dirty:
            self.schedule_render()
        return True

    # --- drawing

    def schedule_render(self) -> None:
        if self.render_source or self.stopping or self.device is None:
            return
        wait = max(0.0, self.last_render + 1.0 / MAX_FPS - time.monotonic())
        self.render_source = self.GLib.timeout_add(max(1, int(wait * 1000)), self._render)

    def build_scene(self):
        from screen import pages

        return pages.build_scene(
            self.config, self._profile_name(), self.ui.view(self._now()),
            state=self.state, details=self.details, driver=self.driver, axes=self.axes,
            connected=self.spacemouse_up or bool(self.driver.get("spacemouse")), skin=self.skin,
            commands=self._catalog().get("index"), app_name=self._catalog().get("name", ""), resolved=self.resolved,
        )

    def _render(self) -> bool:
        from screen import canvas, pages
        from screen.device import DeviceGone

        self.render_source = 0
        if self.device is None or not self.dirty:
            return False
        self.dirty = False
        self.motion_dirty = False
        self.last_render = time.monotonic()
        surface, cr = canvas.device_surface()
        try:
            scene = self.build_scene()
            scene.profile, scene.entries = self.profile, self.entries
            pages.paint(cr, scene)
        except Exception:  # noqa: BLE001 - keep the screen going
            log.exception("could not draw the page")
            return False
        frame = canvas.frame_bytes(surface)
        digest = hashlib.blake2b(frame, digest_size=16).digest()
        if digest == self.last_frame:
            return False
        try:
            self.device.send(frame)
        except DeviceGone as error:
            self._device_lost(error)
            return False
        self.last_frame = digest
        return False

    # --- the device went away

    def _device_lost(self, error) -> bool:
        if self.device is None or self.stopping:
            return False
        log.warning("the screen is not reachable: %s", error)
        self.key_stop.set()
        self.device.close()
        self.device = None
        self.last_frame = None
        self.level_applied = None
        self.reconnect_until = time.monotonic() + RECONNECT_FOR_S
        self.GLib.timeout_add(RECONNECT_EVERY_MS, self._try_reconnect)
        return False

    def _try_reconnect(self) -> bool:
        from screen.device import DeviceGone, ScreenDevice, UsbMissing

        if self.stopping:
            return False
        try:
            self.device = ScreenDevice.open()
        except (DeviceGone, UsbMissing) as error:
            if time.monotonic() >= self.reconnect_until:
                log.info("the screen is gone (%s); leaving, udev starts the service again on the next plug", error)
                self.request_stop(0)
                return False
            return True
        log.info("the screen is back")
        self.screen = self.device.screen
        self._start_keys()
        self.level_applied = None
        self._set_brightness(self.ui.target_level())
        self.dirty = True
        self.schedule_render()
        return False


# --- the program -------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Turnpad on the SpacePilot Pro's screen")
    parser.add_argument("--debug", action="store_true", help="log every command")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, stream=sys.stderr,
                        format="turnpad-screen: %(levelname)s %(message)s")

    from spacemouse_devices import screens_present
    from screen.device import DeviceGone, ScreenDevice, UsbMissing

    config = read_config()
    verdict = start_checks(config, screens_present())
    if verdict:
        log.info("%s", verdict[1])
        return verdict[0]
    if config is None:
        from config.schema import _default_config

        config = _default_config()
    try:
        device = ScreenDevice.open()
    except UsbMissing as error:
        log.info("%s", error)
        return EXIT_UNSUPPORTED
    except DeviceGone as error:
        log.error("cannot open the screen: %s", error)
        return 1

    service = ScreenService(config, device)
    import gi

    try:
        gi.require_version("GLibUnix", "2.0")
        from gi.repository import GLibUnix

        signal_add = GLibUnix.signal_add
    except (ImportError, ValueError):
        signal_add = service.GLib.unix_signal_add  # GLib before 2.80
    for number in (signal.SIGINT, signal.SIGTERM):
        signal_add(service.GLib.PRIORITY_DEFAULT, number, lambda: (service.request_stop(0), service.GLib.SOURCE_REMOVE)[1])
    service.start()
    try:
        service.loop.run()
    finally:
        service.stop()
    return service.exit_status


if __name__ == "__main__":
    sys.exit(main())
