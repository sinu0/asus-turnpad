"""Screen page helpers of the settings app, free of GTK so they are unit tested.

The page edits ``config.screen`` (the look, the power saving and the pages of
the colour screen of a SpacePilot Pro), the per-application overrides in
``Profile.screen`` and the labels of the function keys, which live in the
SpaceMouse button maps (``SpaceMouseButton.label``).
"""

import copy
import dataclasses
import importlib.util

from config.schema import (
    SCREEN_PAGES,
    SCREEN_TEXT_SIZES,
    SCREEN_THEMES,
    SPACEMOUSE_LABEL_MAX,
    ConfigError,
    ScreenConfig,
    SpaceMouseButton,
    config_from_text,
    screen_rule,
)
from config_app import spacemouse as sm

# Names of the pages (the preview module of the screen service knows the same).
PAGE_TITLES = {"keys": "Keys", "dial": "Dial", "device": "All buttons", "motion": "Motion", "clock": "Clock"}
PAGE_HINTS = {
    "keys": "The five function keys with what they do",
    "dial": "The live dial while it is on",
    "device": "Every button of the active application",
    "motion": "The movement of the cap",
    "clock": "The time",
}

THEME_LABELS = {"skin": "Dial skin", "dark": "Dark", "light": "Light", "contrast": "High contrast"}
TEXT_SIZE_LABELS = {"normal": "Normal", "large": "Large"}
THEME_CHOICES = tuple((theme, THEME_LABELS[theme]) for theme in SCREEN_THEMES)
TEXT_SIZE_CHOICES = tuple((size, TEXT_SIZE_LABELS[size]) for size in SCREEN_TEXT_SIZES)

# (seconds, label); 0 = never.
DIM_AFTER_CHOICES = ((0, "Never"), (30, "30 seconds"), (60, "1 minute"), (120, "2 minutes"),
                     (300, "5 minutes"), (600, "10 minutes"), (1800, "30 minutes"))
OFF_AFTER_CHOICES = ((0, "Never"), (300, "5 minutes"), (900, "15 minutes"), (1800, "30 minutes"), (3600, "1 hour"))

# The screen keys around the display: (key, what it does).
SCREEN_KEY_HELP = (
    ("Left / Right", "Previous / next page"),
    ("Up / Down", "Move in a list, or turn the dial"),
    ("OK", "Run or select"),
    ("Back", "Go back"),
    ("Menu", "The list of pages"),
    ("Settings", "Quick settings (brightness, theme…)"),
    ("Light", "Brightness"),
)

# The function keys whose labels the page edits.
FUNCTION_GROUP = "function"


def page_title(page, titles=None):
    return (titles or PAGE_TITLES).get(page, str(page).title())


# --- choices -------------------------------------------------------------------------


def seconds_label(seconds):
    """"Never", "45 seconds", "1 minute", "90 minutes" for a stored number of seconds."""
    seconds = int(seconds)
    if seconds <= 0:
        return "Never"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''}"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{seconds} seconds"


def choices_with(choices, value):
    """``choices`` with ``value`` added in its place when the list lacks it (a
    number written into config.json by hand must not be lost when the row is shown)."""
    if any(item[0] == value for item in choices):
        return tuple(choices)
    if not isinstance(value, int) or isinstance(value, bool):
        return tuple(choices)
    extra = (value, seconds_label(value))
    below = [item for item in choices if item[0] < value]
    above = [item for item in choices if item[0] >= value]
    return tuple(below) + (extra,) + tuple(above)


def choice_index(choices, value):
    """Position of ``value`` in ``choices``; the first one when it is not there."""
    for position, item in enumerate(choices):
        if item[0] == value:
            return position
    return 0


def choice_value(choices, index):
    if 0 <= index < len(choices):
        return choices[index][0]
    return choices[0][0]


# --- pages ---------------------------------------------------------------------------


def shown_pages(pages):
    """The known pages of ``pages`` in order, without repeats."""
    result = []
    for page in pages or ():
        if page in SCREEN_PAGES and page not in result:
            result.append(page)
    return result


def page_order(pages):
    """Every page: the shown ones in their order, then the hidden ones."""
    shown = shown_pages(pages)
    return shown + [page for page in SCREEN_PAGES if page not in shown]


def move_page(pages, page, delta):
    """``pages`` with ``page`` moved ``delta`` places (-1 earlier, 1 later) among
    the shown ones; unchanged when it cannot move."""
    result = shown_pages(pages)
    if page not in result:
        return result
    position = result.index(page)
    target = position + delta
    if not 0 <= target < len(result):
        return result
    result[position], result[target] = result[target], result[position]
    return result


def toggle_page(pages, page, shown, reference=None):
    """``pages`` with ``page`` shown or hidden. At least one page stays: hiding
    the last shown one does nothing. A page that comes back goes to the end, or
    in the order of ``reference`` (a list of pages) when given."""
    result = shown_pages(pages)
    if page not in SCREEN_PAGES:
        return result
    if shown:
        if page not in result:
            order = page_order(reference) if reference is not None else None
            if order is None:
                result.append(page)
            else:
                later = [other for other in order[order.index(page) + 1:] if other in result]
                result.insert(result.index(later[0]) if later else len(result), page)
    elif page in result and len(result) > 1:
        result.remove(page)
    return result


def fix_start_page(pages, start):
    """The start page, or the first page when ``start`` is not shown."""
    pages = shown_pages(pages)
    if start in pages:
        return start
    return pages[0] if pages else SCREEN_PAGES[0]


def effective_pages(config, profile=None):
    """The pages shown under ``profile``: its own set when it has one."""
    if profile is not None and profile.screen.get("pages"):
        return shown_pages(profile.screen["pages"])
    return shown_pages(config.screen.pages) or list(SCREEN_PAGES)


def effective_start(config, profile=None):
    pages = effective_pages(config, profile)
    if profile is not None and profile.screen.get("start_page") in pages:
        return profile.screen["start_page"]
    return fix_start_page(pages, config.screen.start_page)


# --- per application -----------------------------------------------------------------


def set_override(profile, key, value):
    """Set (or, with None, remove) one override of a profile's ``screen`` dict
    (start_page, pages, accent); a value the schema would drop is ignored."""
    values = dict(profile.screen)
    if value is None:
        values.pop(key, None)
    else:
        values[key] = list(value) if key == "pages" else value
    checked = screen_rule(values)
    if value is not None and key not in checked:
        return False
    profile.screen = checked
    return True


def has_overrides(profile):
    return bool(profile.screen)


def settings_customised(config):
    """Whether the stored settings differ from the defaults (so the page stays
    reachable while the screen is unplugged)."""
    return config.screen != ScreenConfig() or any(profile.screen for profile in config.profiles)


# --- the screen section and the screen service --------------------------------------
#
# The screen service writes only the ``screen`` section of config.json (quick
# settings, the Light key). The window must take such a change over and must
# never write its older copy back: a field the window did not change since it
# last read or saved the file is taken from the file.

SCREEN_FIELDS = tuple(field.name for field in dataclasses.fields(ScreenConfig))


def read_screen(path):
    """The ``screen`` section stored in the file at ``path``; None when the file
    cannot be read or is not a usable configuration."""
    try:
        return config_from_text(path.read_text()).screen
    except (OSError, ConfigError):
        return None


class ScreenSync:
    """Which fields of ``config.screen`` the window changed and has not saved yet."""

    def __init__(self):
        self.dirty = set()

    def mark(self, *names):
        unknown = [name for name in names if name not in SCREEN_FIELDS]
        if unknown:
            raise ValueError(f"not a screen setting: {unknown}")
        self.dirty.update(names)

    def clear(self):
        self.dirty.clear()

    def merged(self, current, stored):
        """``stored`` with the fields the window changed laid over it."""
        result = copy.deepcopy(stored)
        for name in self.dirty:
            setattr(result, name, copy.deepcopy(getattr(current, name)))
        return result

    def adopt(self, current, stored):
        """The settings the window should hold now, or None when ``current``
        already is that."""
        result = self.merged(current, stored)
        return None if result == current else result


# --- function key labels -------------------------------------------------------------


def function_keys(model):
    """The function keys of ``model`` in number order (empty for a model without)."""
    keys = [key for key in model.keys if key.group == FUNCTION_GROUP]
    return sorted(keys, key=lambda key: key.label)


def label_only(button):
    """A button entry that only carries a label: it keeps the actions of what lies under it."""
    return button is not None and button.press is None and button.hold is None and bool(button.label)


def _with_label(base, label):
    return SpaceMouseButton(
        press=copy.deepcopy(base.press) if base is not None else None,
        hold=copy.deepcopy(base.hold) if base is not None else None,
        label=label,
    )


def effective_button(config, profile, index):
    """The button the driver runs under ``profile`` (None = all applications): a
    label-only entry is merged over the actions below it."""
    return sm.binding(config, profile, index)[0]


def label_of(config, profile, index):
    """The label of the effective binding of ``index`` under ``profile`` ("" = automatic)."""
    button = effective_button(config, profile, index)
    return button.label if button is not None else ""


def auto_text(config, profile, index):
    """What the screen shows without a label: the description of the binding."""
    return sm.describe_button(effective_button(config, profile, index), use_label=False)


def clean_label(text):
    return str(text or "").strip()[:SPACEMOUSE_LABEL_MAX]


def set_label(config, profile, index, text):
    """Write the label of button ``index`` for all applications (``profile`` None)
    or one profile; returns whether anything changed.

    A scope gets an entry of its own only when it needs one: a label for a
    button without an own binding is a label-only entry (it keeps the actions
    underneath), an entry that has actions just gets its label, and emptying the
    label of a label-only entry removes it."""
    text = clean_label(text)
    key = str(index)
    if label_of(config, profile, index) == text:
        return False
    buttons = profile.spacemouse_buttons if profile is not None else config.spacemouse.buttons
    entry = buttons.get(key)
    if text:
        buttons[key] = SpaceMouseButton(label=text) if entry is None or label_only(entry) else _with_label(entry, text)
        return True
    if entry is not None:
        if label_only(entry):
            del buttons[key]
        else:
            buttons[key] = _with_label(entry, "")
    if profile is not None and label_of(config, profile, index):
        # The label comes from all applications: an entry with the actions and no label hides it.
        sm.store(buttons, index, _with_label(effective_button(config, profile, index), ""))
    return True


# --- status --------------------------------------------------------------------------


def service_state_text(output):
    """Words for what ``systemctl --user is-active`` printed ("" = could not ask)."""
    state = str(output or "").strip().lower()
    return {
        "active": "Running",
        "activating": "Starting",
        "reloading": "Running",
        "inactive": "Not running",
        "deactivating": "Stopping",
        "failed": "Failed (see journalctl --user -u asus-turnpad-screen)",
    }.get(state, "Unknown" if not state else state.capitalize())


def pyusb_missing():
    """Whether the Python module the screen service needs (pyusb) is not installed."""
    try:
        return importlib.util.find_spec("usb") is None
    except (ImportError, ValueError):
        return True


def connection_text(present):
    """"SpacePilot Pro · USB bus 1, device 4" for a PresentScreen, or why there is none."""
    if not present:
        return "No SpaceMouse with a screen is connected"
    first = present[0]
    return f"{first.model.name} · USB bus {first.busnum}, device {first.devnum}"
