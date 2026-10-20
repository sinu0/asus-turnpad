"""Human-readable names for keys and macro steps.

Shared by the settings application and the driver, which forwards the
labels to the overlay as rotation hints ("Zoom out  ‹  ›  Zoom in").
"""

from __future__ import annotations

from config.schema import MacroStep, StepType

KEY_LABELS = {
    "KEY_LEFTCTRL": "Ctrl",
    "KEY_RIGHTCTRL": "Ctrl",
    "KEY_LEFTSHIFT": "Shift",
    "KEY_RIGHTSHIFT": "Shift",
    "KEY_LEFTALT": "Alt",
    "KEY_RIGHTALT": "AltGr",
    "KEY_LEFTMETA": "Super",
    "KEY_RIGHTMETA": "Super",
    "KEY_ENTER": "Enter",
    "KEY_ESC": "Esc",
    "KEY_SPACE": "Space",
    "KEY_BACKSPACE": "Backspace",
    "KEY_TAB": "Tab",
    "KEY_PAGEUP": "Page Up",
    "KEY_PAGEDOWN": "Page Down",
    "KEY_LEFTBRACE": "[",
    "KEY_RIGHTBRACE": "]",
    "KEY_EQUAL": "=",
    "KEY_MINUS": "-",
    "KEY_COMMA": ",",
    "KEY_DOT": ".",
    "KEY_SLASH": "/",
    "KEY_LEFT": "←",
    "KEY_RIGHT": "→",
    "KEY_UP": "↑",
    "KEY_DOWN": "↓",
    "KEY_MUTE": "Mute",
    "KEY_PLAYPAUSE": "Play/Pause",
    "KEY_NEXTSONG": "Next track",
    "KEY_PREVIOUSSONG": "Previous track",
    "KEY_VOLUMEUP": "Volume up",
    "KEY_VOLUMEDOWN": "Volume down",
    "KEY_KBDILLUMUP": "Keyboard light up",
    "KEY_KBDILLUMDOWN": "Keyboard light down",
}

# Older configurations store keys as raw evdev codes
# (from linux/input-event-codes.h).
EVDEV_CODE_NAMES = {
    0: "KEY_RESERVED", 1: "KEY_ESC", 2: "KEY_1", 3: "KEY_2", 4: "KEY_3", 5: "KEY_4",
    6: "KEY_5", 7: "KEY_6", 8: "KEY_7", 9: "KEY_8", 10: "KEY_9", 11: "KEY_0",
    12: "KEY_MINUS", 13: "KEY_EQUAL", 14: "KEY_BACKSPACE", 15: "KEY_TAB", 16: "KEY_Q",
    17: "KEY_W", 18: "KEY_E", 19: "KEY_R", 20: "KEY_T", 21: "KEY_Y", 22: "KEY_U",
    23: "KEY_I", 24: "KEY_O", 25: "KEY_P", 26: "KEY_LEFTBRACE", 27: "KEY_RIGHTBRACE",
    28: "KEY_ENTER", 29: "KEY_LEFTCTRL", 30: "KEY_A", 31: "KEY_S", 32: "KEY_D",
    33: "KEY_F", 34: "KEY_G", 35: "KEY_H", 36: "KEY_J", 37: "KEY_K", 38: "KEY_L",
    39: "KEY_SEMICOLON", 40: "KEY_APOSTROPHE", 41: "KEY_GRAVE", 42: "KEY_LEFTSHIFT",
    43: "KEY_BACKSLASH", 44: "KEY_Z", 45: "KEY_X", 46: "KEY_C", 47: "KEY_V",
    48: "KEY_B", 49: "KEY_N", 50: "KEY_M", 51: "KEY_COMMA", 52: "KEY_DOT",
    53: "KEY_SLASH", 54: "KEY_RIGHTSHIFT", 55: "KEY_KPASTERISK", 56: "KEY_LEFTALT",
    57: "KEY_SPACE", 58: "KEY_CAPSLOCK", 59: "KEY_F1", 60: "KEY_F2", 61: "KEY_F3",
    62: "KEY_F4", 63: "KEY_F5", 64: "KEY_F6", 65: "KEY_F7", 66: "KEY_F8", 67: "KEY_F9",
    68: "KEY_F10", 69: "KEY_NUMLOCK", 70: "KEY_SCROLLLOCK", 71: "KEY_KP7",
    72: "KEY_KP8", 73: "KEY_KP9", 74: "KEY_KPMINUS", 75: "KEY_KP4", 76: "KEY_KP5",
    77: "KEY_KP6", 78: "KEY_KPPLUS", 79: "KEY_KP1", 80: "KEY_KP2", 81: "KEY_KP3",
    82: "KEY_KP0", 83: "KEY_KPDOT", 85: "KEY_ZENKAKUHANKAKU", 86: "KEY_102ND",
    87: "KEY_F11", 88: "KEY_F12", 89: "KEY_RO", 90: "KEY_KATAKANA", 91: "KEY_HIRAGANA",
    92: "KEY_HENKAN", 93: "KEY_KATAKANAHIRAGANA", 94: "KEY_MUHENKAN",
    95: "KEY_KPJPCOMMA", 96: "KEY_KPENTER", 97: "KEY_RIGHTCTRL", 98: "KEY_KPSLASH",
    99: "KEY_SYSRQ", 100: "KEY_RIGHTALT", 101: "KEY_LINEFEED", 102: "KEY_HOME",
    103: "KEY_UP", 104: "KEY_PAGEUP", 105: "KEY_LEFT", 106: "KEY_RIGHT", 107: "KEY_END",
    108: "KEY_DOWN", 109: "KEY_PAGEDOWN", 110: "KEY_INSERT", 111: "KEY_DELETE",
    112: "KEY_MACRO", 113: "KEY_MUTE", 114: "KEY_VOLUMEDOWN", 115: "KEY_VOLUMEUP",
    116: "KEY_POWER", 117: "KEY_KPEQUAL", 118: "KEY_KPPLUSMINUS", 119: "KEY_PAUSE",
    120: "KEY_SCALE", 121: "KEY_KPCOMMA", 122: "KEY_HANGEUL", 123: "KEY_HANJA",
    124: "KEY_YEN", 125: "KEY_LEFTMETA", 126: "KEY_RIGHTMETA", 127: "KEY_COMPOSE",
    128: "KEY_STOP", 129: "KEY_AGAIN", 130: "KEY_PROPS", 131: "KEY_UNDO",
    132: "KEY_FRONT", 133: "KEY_COPY", 134: "KEY_OPEN", 135: "KEY_PASTE",
    136: "KEY_FIND", 137: "KEY_CUT", 138: "KEY_HELP", 139: "KEY_MENU", 140: "KEY_CALC",
    141: "KEY_SETUP", 142: "KEY_SLEEP", 143: "KEY_WAKEUP", 144: "KEY_FILE",
    145: "KEY_SENDFILE", 146: "KEY_DELETEFILE", 147: "KEY_XFER", 148: "KEY_PROG1",
    149: "KEY_PROG2", 150: "KEY_WWW", 151: "KEY_MSDOS", 152: "KEY_COFFEE",
    153: "KEY_ROTATE_DISPLAY", 154: "KEY_CYCLEWINDOWS", 155: "KEY_MAIL",
    156: "KEY_BOOKMARKS", 157: "KEY_COMPUTER", 158: "KEY_BACK", 159: "KEY_FORWARD",
    160: "KEY_CLOSECD", 161: "KEY_EJECTCD", 162: "KEY_EJECTCLOSECD",
    163: "KEY_NEXTSONG", 164: "KEY_PLAYPAUSE", 165: "KEY_PREVIOUSSONG",
    166: "KEY_STOPCD", 167: "KEY_RECORD", 168: "KEY_REWIND", 169: "KEY_PHONE",
    170: "KEY_ISO", 171: "KEY_CONFIG", 172: "KEY_HOMEPAGE", 173: "KEY_REFRESH",
    174: "KEY_EXIT", 175: "KEY_MOVE", 176: "KEY_EDIT", 177: "KEY_SCROLLUP",
    178: "KEY_SCROLLDOWN", 179: "KEY_KPLEFTPAREN", 180: "KEY_KPRIGHTPAREN",
    181: "KEY_NEW", 182: "KEY_REDO", 183: "KEY_F13", 184: "KEY_F14", 185: "KEY_F15",
    186: "KEY_F16", 187: "KEY_F17", 188: "KEY_F18", 189: "KEY_F19", 190: "KEY_F20",
    191: "KEY_F21", 192: "KEY_F22", 193: "KEY_F23", 194: "KEY_F24", 200: "KEY_PLAYCD",
    201: "KEY_PAUSECD", 202: "KEY_PROG3", 203: "KEY_PROG4", 204: "KEY_ALL_APPLICATIONS",
    205: "KEY_SUSPEND", 206: "KEY_CLOSE", 207: "KEY_PLAY", 208: "KEY_FASTFORWARD",
    209: "KEY_BASSBOOST", 210: "KEY_PRINT", 211: "KEY_HP", 212: "KEY_CAMERA",
    213: "KEY_SOUND", 214: "KEY_QUESTION", 215: "KEY_EMAIL", 216: "KEY_CHAT",
    217: "KEY_SEARCH", 218: "KEY_CONNECT", 219: "KEY_FINANCE", 220: "KEY_SPORT",
    221: "KEY_SHOP", 222: "KEY_ALTERASE", 223: "KEY_CANCEL", 224: "KEY_BRIGHTNESSDOWN",
    225: "KEY_BRIGHTNESSUP", 226: "KEY_MEDIA", 227: "KEY_SWITCHVIDEOMODE",
    228: "KEY_KBDILLUMTOGGLE", 229: "KEY_KBDILLUMDOWN", 230: "KEY_KBDILLUMUP",
    231: "KEY_SEND", 232: "KEY_REPLY", 233: "KEY_FORWARDMAIL", 234: "KEY_SAVE",
    235: "KEY_DOCUMENTS", 236: "KEY_BATTERY", 237: "KEY_BLUETOOTH", 238: "KEY_WLAN",
    239: "KEY_UWB", 240: "KEY_UNKNOWN", 241: "KEY_VIDEO_NEXT", 242: "KEY_VIDEO_PREV",
    243: "KEY_BRIGHTNESS_CYCLE", 244: "KEY_BRIGHTNESS_AUTO", 245: "KEY_DISPLAY_OFF",
    246: "KEY_WWAN", 247: "KEY_RFKILL", 248: "KEY_MICMUTE",
}


def format_keys(keys: list[str] | None) -> str:
    rendered = []
    for key in keys or []:
        key = str(key)
        if key.isdigit():
            key = EVDEV_CODE_NAMES.get(int(key), f"KEY_{key}")
        rendered.append(KEY_LABELS.get(key, key.removeprefix("KEY_").replace("_", " ").title()))
    return " + ".join(rendered) if rendered else "Not set"


MODIFIER_KEYS = (
    "KEY_LEFTCTRL", "KEY_RIGHTCTRL", "KEY_LEFTSHIFT", "KEY_RIGHTSHIFT",
    "KEY_LEFTALT", "KEY_RIGHTALT", "KEY_LEFTMETA", "KEY_RIGHTMETA",
)

# Short names for typing shortcuts ("Ctrl+F12"); any other key is typed as its
# evdev name with or without "KEY_" (F12, Home, KEY_F13).
KEY_ALIASES = {
    "CTRL": "KEY_LEFTCTRL", "CONTROL": "KEY_LEFTCTRL",
    "SHIFT": "KEY_LEFTSHIFT",
    "ALT": "KEY_LEFTALT",
    "ALTGR": "KEY_RIGHTALT",
    "SUPER": "KEY_LEFTMETA", "WIN": "KEY_LEFTMETA", "META": "KEY_LEFTMETA",
    "ESC": "KEY_ESC", "ESCAPE": "KEY_ESC",
    "ENTER": "KEY_ENTER", "RETURN": "KEY_ENTER",
    "DEL": "KEY_DELETE", "INS": "KEY_INSERT",
    "PGUP": "KEY_PAGEUP", "PGDN": "KEY_PAGEDOWN", "PGDOWN": "KEY_PAGEDOWN",
    "PRINT": "KEY_SYSRQ", "PRTSC": "KEY_SYSRQ", "PRINTSCREEN": "KEY_SYSRQ",
    "PLUS": "KEY_EQUAL",
    "[": "KEY_LEFTBRACE", "]": "KEY_RIGHTBRACE", "=": "KEY_EQUAL", "-": "KEY_MINUS",
    ",": "KEY_COMMA", ".": "KEY_DOT", "/": "KEY_SLASH", ";": "KEY_SEMICOLON",
    "'": "KEY_APOSTROPHE", "`": "KEY_GRAVE", "\\": "KEY_BACKSLASH",
    "←": "KEY_LEFT", "→": "KEY_RIGHT", "↑": "KEY_UP", "↓": "KEY_DOWN",
}

KNOWN_KEYS = frozenset(EVDEV_CODE_NAMES.values()) - {"KEY_RESERVED"}


def order_keys(keys: list[str]) -> list[str]:
    """Modifiers first, each key once: the driver presses keys in list order."""
    unique = list(dict.fromkeys(keys))
    return [key for key in unique if key in MODIFIER_KEYS] + [key for key in unique if key not in MODIFIER_KEYS]


def parse_keys(text: str) -> tuple[list[str], list[str]]:
    """Read "Ctrl+Shift+Z", "ctrl f12" or "KEY_LEFTALT KEY_LEFT".

    Returns the evdev names in press order and the words not understood.
    """
    keys, unknown = [], []
    for word in text.replace("+", " ").split():
        name = word.upper()
        key = KEY_ALIASES.get(name)
        if key is None:
            key = name if name.startswith("KEY_") else f"KEY_{name}"
        if key in KNOWN_KEYS:
            keys.append(key)
        else:
            unknown.append(word)
    return order_keys(keys), unknown


def keys_to_text(keys: list[str] | None) -> str:
    """Write keys the way parse_keys() reads them back, e.g. "Ctrl+F12"."""
    names = {key: alias for alias, key in KEY_ALIASES.items() if alias in "[]=-,./;'`\\"}
    names.update({
        "KEY_LEFTCTRL": "Ctrl", "KEY_LEFTSHIFT": "Shift", "KEY_LEFTALT": "Alt", "KEY_RIGHTALT": "AltGr",
        "KEY_LEFTMETA": "Super", "KEY_PAGEUP": "PgUp", "KEY_PAGEDOWN": "PgDn", "KEY_SYSRQ": "Print",
    })
    rendered = []
    for key in keys or []:
        key = str(key)
        if key.isdigit():
            key = EVDEV_CODE_NAMES.get(int(key), f"KEY_{key}")
        rendered.append(names.get(key) or key.removeprefix("KEY_").title())
    return "+".join(rendered)


def describe_step(step: MacroStep) -> str:
    if step.title:
        return step.title
    up = step.direction in ("up", "clockwise", "right")
    if step.type is StepType.VOLUME:
        if step.keys and step.direction is None:
            return format_keys(step.keys)
        return "Volume up" if up else "Volume down"
    if step.type is StepType.BRIGHTNESS:
        if step.keys and step.direction is None:
            return format_keys(step.keys)
        return "Brighter" if up else "Dimmer"
    if step.type is StepType.SCROLL:
        prefix = "+".join(format_keys([key]) for key in step.modifiers or [])
        if step.axis == "horizontal":
            label = "Scroll right" if up else "Scroll left"
        else:
            label = "Scroll up" if up else "Scroll down"
        return f"{prefix} {label}" if prefix else label
    if step.type in (StepType.MEDIA, StepType.KEYSTROKE):
        return format_keys(step.keys)
    if step.type in (StepType.COMMAND, StepType.SHELL):
        return "Command"
    if step.type is StepType.DELAY:
        return f"Wait {step.duration or 0:g} s"
    if step.type is StepType.APP:
        # The application's command; the shortcut it falls back to is the better hint.
        if step.keys:
            return format_keys(step.keys)
        return (step.command or "").rsplit(".", 1)[-1].replace("_", " ").capitalize()
    return step.type.value.replace("_", " ").title()


def describe_macro(steps: list[MacroStep]) -> str:
    """Label a whole macro by its first meaningful step."""
    for step in steps:
        if step.type is not StepType.DELAY:
            return describe_step(step)
    return ""


# Function names map to GNOME (Adwaita) symbolic icons; kept in sync with
# ICON_RULES in gnome/extension/extension.js. First match wins.
ICON_RULES = (
    (("terminal", "console"), "utilities-terminal"),
    (("calculator",), "accessories-calculator"),
    (("screenshot",), "camera-photo"),
    (("lock screen",), "system-lock-screen"),
    (("folder",), "folder"),
    (("browser",), "web-browser"),
    (("record",), "media-record"),
    (("stream",), "network-transmit"),
    (("pause",), "media-playback-pause"),
    (("mic",), "audio-input-microphone"),
    (("replay", "save"), "document-save"),
    (("point", "cut", "split", "clip", "snap"), "edit-cut"),
    (("layer",), "view-list"),
    (("keyframe",), "starred"),
    (("arrange", "order"), "view-sort-ascending"),
    (("cursor",), "go-next"),
    (("mirror",), "object-flip-horizontal"),
    (("mute",), "audio-volume-muted"),
    (("volume", "audio", "sound"), "audio-volume-high"),
    (("keyboard",), "keyboard-brightness"),
    (("bright", "exposure", "light"), "display-brightness"),
    (("horizontal",), "object-flip-horizontal"),
    (("scroll",), "object-flip-vertical"),
    (("timeline",), "zoom-fit-best"),
    (("zoom",), "system-search"),
    (("undo", "history", "edit"), "edit-undo"),
    (("redo",), "edit-redo"),
    (("seek", "shuttle"), "media-seek-forward"),
    (("frame",), "media-skip-forward"),
    (("media", "track", "play", "music"), "media-playback-start"),
    (("workspace",), "view-grid"),
    (("tab",), "view-paged"),
    (("back", "forward", "arrow"), "go-next"),
    (("brush", "opacity", "paint", "draw"), "document-edit"),
    (("rotate",), "object-rotate-right"),
    (("scale",), "zoom-fit-best"),
    (("photo", "image"), "image-x-generic"),
    (("rating", "star"), "starred"),
    (("label", "colour", "color"), "color-select"),
    (("font", "text"), "font-x-generic"),
    (("notif",), "preferences-system-notifications"),
)


# Offered by the icon picker of the settings app; all exist in Adwaita.
ICON_CHOICES = (
    "audio-volume-high", "audio-volume-muted", "audio-input-microphone", "display-brightness",
    "keyboard-brightness", "media-playback-start", "media-playback-pause", "media-skip-forward",
    "media-seek-forward", "media-record", "system-search", "zoom-fit-best", "edit-undo", "edit-redo",
    "edit-cut", "edit-copy", "edit-paste", "document-save", "document-edit", "view-list", "view-grid",
    "view-paged", "go-next", "go-previous", "object-rotate-right", "object-flip-horizontal",
    "image-x-generic", "camera-photo", "color-select", "font-x-generic", "starred",
    "utilities-terminal", "accessories-calculator", "folder", "system-lock-screen",
    "application-x-executable", "web-browser", "mail-unread", "input-keyboard", "input-gaming",
    "preferences-system", "network-wireless", "bluetooth-active", "night-light", "weather-clear",
    "face-smile", "input-dialpad",
)


# A group opens a list of functions; without an icon of its own it shows a list.
GROUP_ICON = "view-list-bullet-symbolic"


def segment_icon(segment) -> str:
    """The icon a function shows: its own, else one matching its name."""
    if segment.icon_url:
        return segment.icon_url
    return GROUP_ICON if getattr(segment, "is_group", False) else icon_for_title(segment.title)


def icon_for_title(title: str) -> str:
    name = (title or "").lower()
    for words, icon in ICON_RULES:
        if any(word in name for word in words):
            return f"{icon}-symbolic"
    return "input-dialpad-symbolic"
