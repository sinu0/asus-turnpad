"""The screen's behaviour without hardware or drawing: which page shows, what
the nine keys around the screen do, the overlays, the idle timers.

``ScreenUI`` is a state machine. Time is passed in (monotonic seconds), every
call returns a list of effects for the service to carry out:

  ("control", "rotate:1")      a command for the driver's D-Bus method Control
  ("brightness", 70)           set the backlight (also while dimming or off)
  ("save", {"theme": "dark"})  write these fields into config.json's screen section
  ("redraw",)                  something visible changed
  ("game", "start"|"end")      the hidden game (screen.game) started or ended
  ("high_score", 1200)         a new high score to keep

The Konami code on the keys starts the game; while it runs the pages are not
drawn, the screen does not dim and the keys belong to the game.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from config.schema import SCREEN_TEXT_SIZES, SCREEN_THEMES
from screen.game import Game, Konami

KEY_IDS = ("settings", "back", "menu", "ok", "right", "left", "down", "up", "light")

# The Light key cycles through these, then starts again.
BRIGHTNESS_STEPS = (100, 70, 40, 15, 0)
BRIGHTNESS_STEP = 10
BRIGHTNESS_MIN = 5
DIM_CHOICES = (0, 30, 60, 120, 300, 600, 1800)
TOAST_SECONDS = 1.2
# Messages of the driver stay longer than the answer to a key press.
NOTICE_SECONDS = 3.0
FLASH_SECONDS = 0.35
REPEAT_DELAY = 0.4
REPEAT_INTERVAL = 0.12
# The game ends after this long without a key or the cap while it is not running.
GAME_IDLE_SECONDS = 60.0

# Quick settings: (field, title); values change with Left/Right.
SETTINGS_ROWS = (
    ("brightness", "Brightness"),
    ("dim_after_s", "Dim after"),
    ("theme", "Theme"),
    ("text_size", "Text size"),
    ("show_long", "Long-press labels"),
    ("follow_dial", "Follow the dial"),
    ("start_page", "Start page"),
    ("press_feedback", "Press feedback"),
)
PAGE_NAMES = {"keys": "Keys", "dial": "Dial", "device": "All buttons", "motion": "Motion", "clock": "Clock"}


def format_seconds(seconds: int) -> str:
    if seconds <= 0:
        return "Never"
    if seconds < 60:
        return f"{seconds} s"
    return f"{seconds // 60} min"


def setting_text(settings, name: str) -> str:
    """How quick settings show a value."""
    value = getattr(settings, name)
    if name == "brightness":
        return f"{value} %"
    if name == "dim_after_s":
        return format_seconds(value)
    if isinstance(value, bool):
        return "On" if value else "Off"
    if name == "start_page":
        return PAGE_NAMES.get(value, str(value))
    return str(value).capitalize()


@dataclass
class Toast:
    title: str
    detail: str = ""
    color: str = ""
    until: float = 0.0


@dataclass
class UIView:
    """What the painter needs from the UI."""

    page: str
    pages: list
    overlay: str | None
    overlay_index: int
    highlight: int | None
    scroll: int
    toast: Toast | None
    flashes: set
    power: str
    settings_rows: list
    # The hidden game while it runs, else None.
    game: object = None


class ScreenUI:
    def __init__(self, settings, now: float = 0.0):
        self.settings = replace(settings)
        self.settings.pages = list(settings.pages)
        self.pages = list(settings.pages)
        self.page = settings.start_page
        # The page to go back to when the dial goes off (follow_dial).
        self.previous_page = None
        # None, "picker" or "settings".
        self.overlay = None
        self.overlay_index = 0
        self.highlight = None
        self.scroll = 0
        self.dial_on = False
        # Button indexes of the keys page's faces, and how far the device page scrolls.
        self.faces = []
        self.device_steps = 0
        self.toast = None
        self.flashes = {}
        self.last_activity = now
        # awake, dim or off.
        self.power = "awake"
        self.level = None
        self.held = {}
        # The hidden game: the code that starts it, the game while it runs, the
        # best score so far (set by the service from its file) and the last input.
        self.konami = Konami()
        self.game = None
        self.high_score = 0
        self.game_input_at = now

    # --- output

    def target_level(self) -> int:
        if self.power == "off":
            return 0
        if self.power == "dim":
            return min(self.settings.dim_brightness, self.settings.brightness)
        return self.settings.brightness

    def _sync_level(self, effects: list) -> None:
        level = self.target_level()
        if level != self.level:
            self.level = level
            effects.append(("brightness", level))

    def start(self, now: float) -> list:
        self.last_activity = now
        effects = []
        self._sync_level(effects)
        effects.append(("redraw",))
        return effects

    def view(self, now: float | None = None) -> UIView:
        toast = self.toast if self.toast and (now is None or now < self.toast.until) else None
        flashes = {index for index, until in self.flashes.items() if now is None or now < until}
        return UIView(
            self.page, list(self.pages), self.overlay, self.overlay_index, self.highlight, self.scroll,
            toast, flashes, self.power,
            [(title, setting_text(self.settings, name)) for name, title in SETTINGS_ROWS],
            self.game,
        )

    # --- time

    def _desired_power(self, now: float) -> str:
        if self.game is not None:
            return "awake"
        idle = now - self.last_activity
        off_after, dim_after = self.settings.off_after_s, self.settings.dim_after_s
        if off_after > 0 and idle >= off_after:
            return "off"
        if dim_after > 0 and idle >= dim_after:
            return "dim"
        return "awake"

    def activity(self, now: float, kind: str = "button") -> list:
        """Something happened (kind: button, key, motion, state): wake up. Cap
        motion only counts with wake_on_motion."""
        if self.game is not None:
            self.game_input_at = now
        if kind == "motion" and not self.settings.wake_on_motion:
            return []
        self.last_activity = now
        effects = []
        if self.power != "awake":
            self.power = "awake"
            self._sync_level(effects)
            effects.append(("redraw",))
        return effects

    def tick(self, now: float) -> list:
        effects = []
        changed = False
        if self.toast is not None and now >= self.toast.until:
            self.toast = None
            changed = True
        for index in [index for index, until in self.flashes.items() if now >= until]:
            del self.flashes[index]
            changed = True
        for name in list(self.held):
            if name not in self.held:
                continue
            if now >= self.held[name]:
                # A late tick repeats once, not for every step it missed.
                self.held[name] = now + REPEAT_INTERVAL
                effects.extend(self._press(name, now))
        if self.game is not None and not self.game.running and now - self.game_input_at >= GAME_IDLE_SECONDS:
            effects.extend(self.end_game(now))
        power = self._desired_power(now)
        if power != self.power:
            self.power = power
            self._sync_level(effects)
            changed = True
        if changed:
            effects.append(("redraw",))
        return effects

    def next_deadline(self, now: float) -> float | None:
        """The monotonic time of the next thing that needs a tick, None when nothing is pending."""
        times = [self.held[name] for name in self.held]
        if self.game is not None and not self.game.running:
            times.append(self.game_input_at + GAME_IDLE_SECONDS)
        if self.toast is not None:
            times.append(self.toast.until)
        times.extend(self.flashes.values())
        off_after, dim_after = self.settings.off_after_s, self.settings.dim_after_s
        if self.game is not None:
            pass  # the screen stays awake
        elif self.power == "awake":
            times.extend(self.last_activity + after for after in (dim_after, off_after) if after > 0)
        elif self.power == "dim" and off_after > 0:
            times.append(self.last_activity + off_after)
        return min(times) if times else None

    # --- the driver and the config

    def set_context(self, faces: list, device_steps: int = 0) -> list:
        """The keys page's button indexes and the device page's scroll range."""
        before = (self.faces, self.device_steps, self.highlight, self.scroll)
        self.faces = list(faces)
        self.device_steps = max(0, int(device_steps))
        if self.highlight is not None:
            self.highlight = min(self.highlight, len(self.faces) - 1) if self.faces else None
        self.scroll = max(0, min(self.scroll, self.device_steps))
        return [("redraw",)] if before != (self.faces, self.device_steps, self.highlight, self.scroll) else []

    def set_dial(self, on: bool, now: float) -> list:
        """The dial went on or off (DriverState)."""
        if bool(on) == self.dial_on:
            return []
        self.dial_on = bool(on)
        effects = []
        if self.dial_on and self.game is not None:
            effects.extend(self.end_game(now))
        if self.settings.follow_dial and "dial" in self.pages:
            if self.dial_on:
                if self.page != "dial":
                    self.previous_page = self.page
                    self.page = "dial"
                self.overlay = None
                effects.extend(self.activity(now, "state"))
            elif self.page == "dial" and self.previous_page is not None:
                self.page, self.previous_page = self.previous_page, None
        effects.append(("redraw",))
        return effects

    def apply_config(self, settings, keep=()) -> list:
        """New effective settings (a config reload or another profile); the
        fields in ``keep`` stay as they are (changes still waiting to be saved)."""
        merged = replace(settings)
        merged.pages = list(settings.pages)
        for name in keep:
            if hasattr(merged, name):
                setattr(merged, name, getattr(self.settings, name))
        self.settings = merged
        self.pages = list(merged.pages)
        if self.page not in self.pages and not (self.page == "dial" and self.previous_page is not None):
            self.page = merged.start_page
            self.previous_page = None
        effects = []
        self._sync_level(effects)
        effects.append(("redraw",))
        return effects

    def feedback(self, now: float, index: int | None, title: str, detail: str = "", color: str = "") -> list:
        """A button was pressed: show what it does for a moment (press_feedback)."""
        if not self.settings.press_feedback or not title or self.game is not None:
            return []
        self.toast = Toast(title, detail, color, now + TOAST_SECONDS)
        if index is not None:
            self.flashes[index] = now + FLASH_SECONDS
        return [("redraw",)]

    def notify(self, now: float, title: str, detail: str = "", color: str = "", seconds: float = NOTICE_SECONDS) -> list:
        """The driver or an application has something to say (a changed motion,
        a notice, a profile on offer): a toast whatever press_feedback says, and
        the screen wakes up for it."""
        if not title or self.game is not None:
            return []
        self.toast = Toast(title, detail, color, now + seconds)
        return [*self.activity(now, "state"), ("redraw",)]

    # --- screen keys

    def key_down(self, name: str, now: float) -> list:
        if name not in KEY_IDS:
            return []
        if self.game is not None:
            return self._game_key_down(name, now)
        asleep = self.power == "off"
        effects = self.activity(now, "key")
        if asleep:
            self.konami.reset()
            return effects  # the first key press only wakes the screen
        if self.konami.feed(name) and not self.dial_on:
            return effects + self.start_game(now)
        effects.extend(self._press(name, now))
        if name in ("up", "down"):
            self.held[name] = now + REPEAT_DELAY
        return effects

    def key_up(self, name: str, now: float) -> list:
        self.held.pop(name, None)
        if self.game is not None:
            self.game.release(name)
            return [("redraw",)] if not self.game.running else []
        return []

    # --- the hidden game

    def start_game(self, now: float) -> list:
        self.konami.reset()
        self.held.clear()
        self.overlay = None
        self.game = Game(seed=int(now * 1000) & 0xFFFFFFFF, high_score=self.high_score)
        self.game_input_at = now
        effects = [("game", "start")]
        effects.extend(self.activity(now, "state"))
        effects.append(("redraw",))
        return effects

    def end_game(self, now: float) -> list:
        """Back to the pages (the game was left, the dial turned on or it sat idle)."""
        if self.game is None:
            return []
        self.game = None
        self.konami.reset()
        self.last_activity = now
        effects = [("game", "end")]
        self._sync_level(effects)
        effects.append(("redraw",))
        return effects

    def game_step(self, dt: float, tilt: float, twist: float, fire: bool, now: float) -> list:
        """One frame of the game with the cap's input; effects when it ended a round."""
        game = self.game
        if game is None or not game.running:
            return []
        game.set_input(tilt=tilt, twist=twist, fire=fire)
        state = game.state
        game.step(dt)
        effects = [("redraw",)]
        if state != game.state and game.state == "over":
            self.game_input_at = now
            if game.high_score > self.high_score:
                self.high_score = game.high_score
                effects.append(("high_score", game.high_score))
        return effects

    def _game_key_down(self, name: str, now: float) -> list:
        self.game_input_at = now
        if name == "light":
            return self._light()  # the backlight keeps working
        if self.game.press(name) == "exit":
            return self.end_game(now)
        return [("redraw",)]

    def keys_changed(self, old_mask: int, new_mask: int, key_bits: dict, now: float) -> list:
        """Presses and releases from two successive masks (key_bits: id -> bit)."""
        effects = []
        for name, bit in key_bits.items():
            was, is_ = bool(old_mask & bit), bool(new_mask & bit)
            if is_ and not was:
                effects.extend(self.key_down(name, now))
            elif was and not is_:
                effects.extend(self.key_up(name, now))
        return effects

    def _press(self, name: str, now: float) -> list:
        handler = {
            "light": self._light, "menu": self._menu, "settings": self._settings_key, "back": self._back,
            "left": lambda: self._horizontal(-1), "right": lambda: self._horizontal(1),
            "up": lambda: self._vertical(-1), "down": lambda: self._vertical(1), "ok": self._ok,
        }[name]
        return handler()

    def _light(self) -> list:
        current = self.settings.brightness
        lower = [step for step in BRIGHTNESS_STEPS if step < current]
        self.settings.brightness = lower[0] if lower else BRIGHTNESS_STEPS[0]
        effects = [("save", {"brightness": self.settings.brightness})]
        self._sync_level(effects)
        effects.append(("redraw",))
        return effects

    def _menu(self) -> list:
        if self.overlay == "picker":
            self.overlay = None
        else:
            self.overlay = "picker"
            self.overlay_index = self.pages.index(self.page) if self.page in self.pages else 0
        return [("redraw",)]

    def _settings_key(self) -> list:
        if self.overlay == "settings":
            self.overlay = None
        else:
            self.overlay = "settings"
            self.overlay_index = 0
        return [("redraw",)]

    def go_page(self, page: str) -> list:
        if page == self.page:
            return []
        self.page = page
        self.previous_page = None
        return [("redraw",)]

    def _back(self) -> list:
        if self.overlay is not None:
            self.overlay = None
            return [("redraw",)]
        if self.page == "dial" and self.dial_on:
            return [("control", "back")]
        return self.go_page(self.settings.start_page)

    def _horizontal(self, direction: int) -> list:
        if self.overlay == "settings":
            return self._change_setting(direction, wrap=False)
        if self.overlay is not None or not self.pages:
            return []
        if self.page in self.pages:
            position = (self.pages.index(self.page) + direction) % len(self.pages)
        else:
            position = 0 if direction > 0 else len(self.pages) - 1
        return self.go_page(self.pages[position])

    def _vertical(self, direction: int) -> list:
        if self.overlay == "settings":
            return self._move_overlay(direction, len(SETTINGS_ROWS))
        if self.overlay == "picker":
            return self._move_overlay(direction, len(self.pages))
        if self.page == "keys":
            if not self.faces:
                return []
            if self.highlight is None:
                self.highlight = 0 if direction > 0 else len(self.faces) - 1
            else:
                self.highlight = max(0, min(len(self.faces) - 1, self.highlight + direction))
            return [("redraw",)]
        if self.page == "dial":
            return [("control", f"rotate:{direction}")] if self.dial_on else []
        if self.page == "device":
            scroll = max(0, min(self.device_steps, self.scroll + direction))
            if scroll == self.scroll:
                return []
            self.scroll = scroll
            return [("redraw",)]
        return []

    def _move_overlay(self, direction: int, count: int) -> list:
        index = max(0, min(count - 1, self.overlay_index + direction))
        if index == self.overlay_index:
            return []
        self.overlay_index = index
        return [("redraw",)]

    def _ok(self) -> list:
        if self.overlay == "settings":
            return self._change_setting(1, wrap=True)
        if self.overlay == "picker":
            page = self.pages[self.overlay_index] if 0 <= self.overlay_index < len(self.pages) else self.page
            self.overlay = None
            return self.go_page(page) + [("redraw",)]
        if self.page == "keys":
            if self.highlight is not None and 0 <= self.highlight < len(self.faces):
                return [("control", f"button:{self.faces[self.highlight]}")]
            return []
        if self.page == "dial":
            return [("control", "select" if self.dial_on else "dial-on")]
        return []

    # --- quick settings

    def _change_setting(self, direction: int, wrap: bool) -> list:
        name = SETTINGS_ROWS[self.overlay_index][0]
        settings = self.settings
        value = getattr(settings, name)
        if name == "brightness":
            value = value + direction * BRIGHTNESS_STEP
            value = BRIGHTNESS_MIN if value > 100 and wrap else min(100, value)
            value = max(BRIGHTNESS_MIN, value)
        elif isinstance(value, bool):
            value = not value
        else:
            choices = {"dim_after_s": DIM_CHOICES, "theme": SCREEN_THEMES, "text_size": SCREEN_TEXT_SIZES,
                       "start_page": tuple(self.pages)}[name]
            if name == "dim_after_s":
                position = min(range(len(choices)), key=lambda i: abs(choices[i] - value))
            else:
                position = choices.index(value) if value in choices else 0
            position += direction
            if wrap or name not in ("dim_after_s",):
                position %= len(choices)
            else:
                position = max(0, min(len(choices) - 1, position))
            value = choices[position]
        if value == getattr(settings, name):
            return []
        setattr(settings, name, value)
        effects = [("save", {name: value})]
        self._sync_level(effects)
        effects.append(("redraw",))
        return effects
