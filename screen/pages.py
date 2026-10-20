"""Painters of the screen's pages for the 320 x 240 landscape canvas.

Pure drawing (Cairo, no GTK, no hardware): ``paint(cr, scene)`` paints the
page of a ``Scene`` plus its overlays; the same code draws the panel
(screen.canvas.device_surface) and pictures (screen.preview). Content stays
above ``SAFE_BOTTOM``: the two bottom pixel rows are hidden by the bezel.
Icons come from screen.icons; without them the pages simply have none.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime

import cairo

from config.labels import keys_to_text, segment_icon
from config.presets import segment_color
from config.schema import DialPadConfig, Profile
from config.skins import DEFAULT_SKIN, parse_color, resolve_skin
from overlay import look
from overlay.painter import DialView, paint_dial
from screen import icons
from screen.canvas import HEIGHT, WIDTH
from screen.content import (
    EffectiveScreen,
    KeyEntry,
    KeyFace,
    bar_chips,
    format_speed,
    function_entries,
    grouped_entries,
    menu_segments,
    motion_state,
    resolve,
)
from screen.ui import PAGE_NAMES, UIView

FONT = "Adwaita Sans"
SAFE_BOTTOM = 238
BAR_H = 26
TOP = 27
# Full deflection of a raw axis on the motion page.
AXIS_FULL = 420
AXIS_LABELS = ("TX", "TY", "TZ", "RX", "RY", "RZ")
DEVICE_ROW_H = 24
DEVICE_HEADER_H = 20
ICON_NAMES = {"app": "input-dialpad-symbolic"}


# --- colours ----------------------------------------------------------------

def _rgb(value, fallback=(0.5, 0.5, 0.5)):
    return tuple(parse_color(value, tuple(fallback) + (1,))[:3])


def _flat(color):
    """A skin colour (r, g, b, a) over black."""
    red, green, blue, alpha = color
    return (red * alpha, green * alpha, blue * alpha)


def mix(first, second, amount):
    return tuple(a + (b - a) * amount for a, b in zip(first, second))


def on_color(color):
    """Text on a coloured badge: dark on light colours, light on dark ones."""
    luminance = 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]
    return (0.07, 0.075, 0.1) if luminance > 0.45 else (0.97, 0.97, 0.98)


@dataclass(frozen=True)
class Theme:
    bg: tuple
    card: tuple
    card_hi: tuple
    text: tuple
    muted: tuple
    faint: tuple
    accent: tuple


def make_theme(settings, skin=None, overlay_accent: str = "") -> Theme:
    """Colours for settings.theme (skin, dark, light, contrast); the profile's
    accent (settings.accent) wins over the overlay's."""
    name = getattr(settings, "theme", "skin")
    if name == "light":
        bg, text, muted, accent, lift = (0.94, 0.94, 0.96), (0.1, 0.1, 0.14), (0.38, 0.4, 0.46), (0.78, 0.48, 0.18), 0.07
    elif name == "contrast":
        bg, text, muted, accent, lift = (0, 0, 0), (1, 1, 1), (0.82, 0.82, 0.82), (1.0, 0.86, 0.2), 0.16
    elif name == "skin" and skin:
        palette = skin["palette"]
        bg, text, muted = _flat(palette["surfaceBottom"]), _flat(palette["text"]), _flat(palette["muted"])
        accent, lift = look.DEFAULT_ACCENT[:3], 0.07
    else:
        bg, text, muted, accent, lift = (0.07, 0.075, 0.1), (0.95, 0.95, 0.97), (0.62, 0.64, 0.7), look.DEFAULT_ACCENT[:3], 0.07
    if name != "contrast":
        accent = _rgb(overlay_accent, accent) if overlay_accent else accent
    override = getattr(settings, "accent", "")
    if override:
        accent = _rgb(override, accent)
    card = mix(bg, text, lift)
    return Theme(bg, card, mix(card, accent, 0.3), text, muted,
                 mix(bg, text, 0.55 if name == "contrast" else 0.38), accent)


# --- drawing helpers --------------------------------------------------------

class Pen:
    """A context with the theme and the text size; all sizes are for the
    normal text size and grow with text_size "large"."""

    def __init__(self, cr, theme: Theme, large: bool = False):
        self.cr = cr
        self.theme = theme
        self.k = 1.18 if large else 1.0

    def size(self, base: float) -> float:
        return round(base * self.k * 2) / 2

    def set_font(self, size, bold):
        self.cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
        self.cr.set_font_size(size)

    def width(self, text, size, bold=False, scaled=True):
        self.set_font(self.size(size) if scaled else size, bold)
        return self.cr.text_extents(text).x_advance

    def fit(self, text, max_w, size, bold=False, scaled=True):
        """The text, cut with "…" to fit max_w."""
        text = str(text)
        if max_w is None or self.width(text, size, bold, scaled) <= max_w:
            return text
        while text and self.width(text + "…", size, bold, scaled) > max_w:
            text = text[:-1]
        return text.rstrip() + "…" if text else "…"

    def text(self, text, x, y, size, color, bold=False, anchor="l", max_w=None, scaled=True):
        """Draw text with its baseline at y; returns the width drawn."""
        px = self.size(size) if scaled else size
        text = self.fit(text, max_w, size, bold, scaled)
        self.set_font(px, bold)
        width = self.cr.text_extents(text).x_advance
        left = x - {"l": 0, "c": width / 2, "r": width}[anchor]
        self.cr.move_to(left, y)
        self.cr.set_source_rgb(*color[:3])
        self.cr.show_text(text)
        return width

    def wrap(self, text, width, size, bold, lines):
        """Greedy word wrap into at most ``lines`` lines, the last one cut."""
        words, rows, current = str(text).split(), [], ""
        for word in words:
            trial = f"{current} {word}".strip()
            if current and self.width(trial, size, bold) > width and len(rows) < lines - 1:
                rows.append(current)
                current = word
            else:
                current = trial
        rows.append(current)
        return [self.fit(row, width, size, bold) for row in rows if row]

    def rrect(self, x, y, w, h, r):
        cr = self.cr
        r = min(r, w / 2, h / 2)
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
        cr.close_path()

    def fill_rrect(self, x, y, w, h, r, color, alpha=1.0):
        self.rrect(x, y, w, h, r)
        self.cr.set_source_rgba(*color[:3], alpha)
        self.cr.fill()

    def stroke_rrect(self, x, y, w, h, r, color, width=2.0):
        self.rrect(x, y, w, h, r)
        self.cr.set_line_width(width)
        self.cr.set_source_rgb(*color[:3])
        self.cr.stroke()

    def dot(self, x, y, r, color):
        self.cr.new_path()
        self.cr.arc(x, y, r, 0, 2 * math.pi)
        self.cr.set_source_rgb(*color[:3])
        self.cr.fill()

    def chip(self, x, y, text, bg, fg, h=16, max_w=None):
        """A small rounded label with its left edge at x, top at y; returns its
        width (``max_w`` cuts the text with an ellipsis)."""
        if max_w is not None:
            text = self.fit(text, max_w - 12, 11, True)
        width = self.width(text, 11, True) + 12
        self.fill_rrect(x, y, width, h, h / 2, bg)
        self.text(text, x + width / 2, y + h - 4.5, 11, fg, True, "c")
        return width

    def badge(self, x, y, size, label, color):
        self.fill_rrect(x, y, size, size, 6, color)
        self.text(label, x + size / 2, y + size / 2 + self.size(16) * 0.36, 16, on_color(color), True, "c",
                  max_w=size - 2)

    def icon(self, name, x, y, size, color):
        if name:
            icons.draw_icon(self.cr, name, x, y, size, (*color[:3], 1))

    def hold_symbol(self, x, y, color):
        """A circular arrow centred at x, y: "hold" (a font has no ⟳)."""
        cr = self.cr
        radius = 4.2
        start, end = -math.pi * 0.35, math.pi * 1.5
        cr.save()
        cr.new_path()
        cr.set_line_width(1.5)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_source_rgb(*color[:3])
        cr.arc(x, y, radius, start, end)
        cr.stroke()
        tip_x, tip_y = x + math.cos(start) * radius, y + math.sin(start) * radius
        cr.move_to(tip_x - 3.2, tip_y - 0.8)
        cr.line_to(tip_x + 1.4, tip_y - 2.8)
        cr.line_to(tip_x + 1.0, tip_y + 2.6)
        cr.close_path()
        cr.fill()
        cr.restore()

    def turn_arrow(self, x, y, clockwise, color):
        cr = self.cr
        radius = 5.5
        start = -math.pi * 0.85 if clockwise else -math.pi * 0.15
        end = math.pi * 0.45 if clockwise else math.pi * 0.55
        cr.save()
        cr.new_path()
        cr.set_line_width(1.6)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_source_rgb(*color[:3])
        (cr.arc if clockwise else cr.arc_negative)(x, y, radius, start, end)
        cr.stroke()
        tip_x, tip_y = x + math.cos(end) * radius, y + math.sin(end) * radius
        tangent = end + (math.pi / 2 if clockwise else -math.pi / 2)
        for spread in (0.5, -0.5):
            angle = tangent + math.pi + spread
            cr.move_to(tip_x, tip_y)
            cr.line_to(tip_x + math.cos(angle) * 4, tip_y + math.sin(angle) * 4)
        cr.stroke()
        cr.restore()

    def triangle(self, x, y, size, direction, color):
        """A small filled triangle pointing left or right, centred at x, y."""
        cr = self.cr
        cr.new_path()
        sign = 1 if direction == "right" else -1
        cr.move_to(x + sign * size / 2, y)
        cr.line_to(x - sign * size / 2, y - size / 2)
        cr.line_to(x - sign * size / 2, y + size / 2)
        cr.close_path()
        cr.set_source_rgb(*color[:3])
        cr.fill()


# --- the scene --------------------------------------------------------------

@dataclass
class Scene:
    """Everything a frame shows."""

    config: DialPadConfig
    profile: Profile
    settings: EffectiveScreen
    view: UIView
    entries: list
    # The dial's OverlayState / OverlayDetails (overlay.look.new_state(), new_details()).
    state: dict = field(default_factory=look.new_state)
    details: dict = field(default_factory=look.new_details)
    # DriverState (modifiers, keys_held, spacemouse, dial ...).
    driver: dict = field(default_factory=dict)
    # Raw axes: TX, TY, TZ, RX, RY, RZ.
    axes: tuple = (0, 0, 0, 0, 0, 0)
    now: datetime = field(default_factory=datetime.now)
    connected: bool = False
    skin: dict | None = None
    # The linked application's name ("Blender") for the clock page's status line.
    app_name: str = ""


def overlay_appearance(config: DialPadConfig) -> dict:
    return look.load_appearance(asdict(config.overlay))


def default_skin(config: DialPadConfig):
    """The overlay's skin as the dial paints it (built-in and user skins)."""
    from config.skins import load_skins

    try:
        return resolve_skin(load_skins(), look.skin_id(overlay_appearance(config), True))
    except Exception:
        return resolve_skin({}, DEFAULT_SKIN)


# --- top bar ----------------------------------------------------------------

def _held_labels(driver: dict) -> list:
    labels = [str(name) for name in driver.get("modifiers") or []]
    labels += [keys_to_text([name]) or str(name) for name in driver.get("keys_held") or []]
    return labels


def clock_text(now: datetime, settings) -> str:
    if settings.clock_24h:
        return f"{now:%H:%M}"
    return f"{now:%I:%M}".lstrip("0") + (" PM" if now.hour >= 12 else " AM")


def paint_bar(pen: Pen, scene: Scene) -> None:
    theme = pen.theme
    pages = scene.view.pages
    start = 262 - 10 * len(pages)
    chips = bar_chips(scene.driver)
    held = _held_labels(scene.driver)[:3]
    layer = any(chip.kind == "layer" for chip in chips)
    pen.dot(14, 13, 6, theme.accent)
    name = scene.profile.name or "Turnpad"
    drawn = pen.text(name, 26, 18, 14, theme.text, True, max_w=62 if chips or held else 84)
    x = 26 + drawn + 8
    limit = start - 8
    colors = {
        "layer": (theme.accent, on_color(theme.accent)),
        "context": (theme.card_hi, theme.text),
        "mode": (theme.card, theme.muted),
        "motion": (mix(theme.card, (1.0, 0.6, 0.25), 0.55), (0.1, 0.07, 0.04)),
    }
    # The layer first, then what is held right now, then the standing state.
    shown = [(chip.text, *colors[chip.kind]) for chip in chips if chip.kind == "layer"]
    shown += [(label, theme.accent, on_color(theme.accent)) for label in held]
    shown += [(chip.text, *colors[chip.kind]) for chip in chips if chip.kind != "layer"]
    for text, background, foreground in shown:
        room = limit - x
        if room < 34:
            break
        x += pen.chip(x, 5, text, background, foreground, max_w=min(room, 72)) + 4
    if layer:
        # An active layer is hard to miss: a line under the whole bar.
        pen.cr.rectangle(0, BAR_H - 2, WIDTH, 2)
        pen.cr.set_source_rgb(*theme.accent)
        pen.cr.fill()
    for position, page in enumerate(pages):
        current = page == scene.view.page
        pen.dot(start + position * 10, 13, 3 if current else 2.2, theme.text if current else mix(theme.bg, theme.text, 0.3))
    pen.text(clock_text(scene.now, scene.settings), 312, 18, 13, theme.muted, False, "r")


# --- keys page --------------------------------------------------------------

def _face_color(face: KeyFace, theme: Theme):
    color = _rgb(face.color, theme.faint) if face.color else theme.faint
    # A layer's face from the base map is dimmed: it is not what the layer changed.
    return mix(theme.card, color, 0.42) if face.inherited else color


def _face_text(face: KeyFace, theme: Theme):
    if not face.bound:
        return theme.faint
    return theme.muted if face.inherited else theme.text


def _face_title(face: KeyFace) -> str:
    return face.title or "–"


def paint_keys(pen: Pen, scene: Scene) -> None:
    theme, cr = pen.theme, pen.cr
    show_long = scene.settings.show_long
    row_h, gap = 40, 2
    left_w = 190 if show_long else 304
    for number, entry in enumerate(function_entries(scene.entries)):
        y = TOP + number * (row_h + gap)
        short, long = entry.short, entry.long
        short_at = number * 2 if show_long else number
        long_at = short_at + 1
        color = _face_color(short, theme)
        pen.fill_rrect(4, y, 312, row_h, 8, theme.card)
        flashes = scene.view.flashes
        # The pressed face flashes in its colour.
        for index, face, x0, x1 in ((entry.index, short, 4, 4 + left_w), (entry.long_index, long, 4 + left_w, 316)):
            if index in flashes and face is not None and (index == entry.index or show_long):
                cr.save()
                pen.rrect(4, y, 312, row_h, 8)
                cr.clip()
                cr.rectangle(x0, y, x1 - x0, row_h)
                cr.set_source_rgba(*_face_color(face, theme), 0.45)
                cr.fill()
                cr.restore()
        if scene.view.highlight is not None:
            if scene.view.highlight == short_at:
                pen.stroke_rrect(5, y + 1, left_w - 2, row_h - 2, 7, theme.accent)
            elif show_long and scene.view.highlight == long_at:
                pen.stroke_rrect(left_w + 5, y + 1, 316 - left_w - 6, row_h - 2, 7, theme.accent)
        pen.fill_rrect(4, y, 5, row_h, 2.5, color)
        pen.badge(14, y + 7, 26, entry.key.label, color)
        text_x = 48
        if icons.available() and short.icon:
            pen.icon(short.icon, 46, y + 9, 18, theme.muted if short.inherited else theme.text)
            text_x = 68
        pen.text(_face_title(short), text_x, y + 19, 16, _face_text(short, theme), True,
                 max_w=left_w - text_x - 2)
        if short.detail:
            pen.text(short.detail, text_x, y + 34, 11, theme.muted, False, max_w=left_w - text_x - 2)
        if show_long and long is not None:
            lcolor = _face_color(long, theme)
            drawn = pen.text("hold", 308, y + 16, 11, lcolor, False, "r")
            pen.hold_symbol(308 - drawn - 8, y + 12.5, lcolor)
            pen.text(_face_title(long), 308, y + 33, 13, _face_text(long, theme), True, "r",
                     max_w=316 - left_w - 12)


# --- dial page --------------------------------------------------------------

def dial_view(scene: Scene):
    """(DialView, look view dict) of the scene's overlay state and details."""
    state, details = scene.state, scene.details
    appearance = overlay_appearance(scene.config)
    skin = scene.skin or resolve_skin({}, DEFAULT_SKIN)
    segments = look.segment_state(state)
    selected = look.is_selected_mode(state)
    unit = state.get("unit") or ""
    shows_value = bool(unit) and not state.get("valueShowOnlyProgress")
    colors = list(details.get("colors") or [])
    view = DialView(
        skin, segments["titles"], colors, appearance["accent"], segments["icons"],
        title="" if state.get("title") == "Menu" else state.get("title") or "", selected=selected,
        value=state.get("value") if shows_value else None, unit=unit if shows_value else "",
        profile=details.get("profile") or scene.profile.name,
        submenu=details.get("submenu") if isinstance(details.get("submenu"), dict) else None,
    )
    return view, look.make_view(state, details, appearance, skin)


def _paint_list(pen: Pen, view, submenu: dict, x: float, y: float, width: float, height: float) -> None:
    """The open group's list, drawn for this screen (the Shell's list scales
    down to unreadable text here)."""
    theme = pen.theme
    pen.fill_rrect(x, y, width, height, 10, theme.card)
    group_color = view.colors[submenu["index"]] if 0 <= submenu["index"] < len(view.colors) else view.accent
    pen.fill_rrect(x, y + 8, 3, height - 16, 1.5, group_color)
    pen.text(submenu["title"].upper(), x + 12, y + 17, 11, theme.muted, True, max_w=width - 60)
    count = len(submenu["items"])
    if count > submenu["rows"]:
        pen.text(f"{submenu['selected'] + 1}/{count}", x + width - 8, y + 17, 11, theme.muted, False, "r")
    row_h = max(24, math.floor((height - 26) / submenu["rows"]))
    row_h = min(row_h, round(pen.size(26)))
    for row in range(submenu["rows"]):
        position = submenu["first"] + row
        top = y + 24 + row * row_h
        color = submenu["colors"][position]
        chosen = position == submenu["selected"]
        if chosen:
            pen.fill_rrect(x + 6, top, width - 12, row_h - 2, 7, color)
        fg = on_color(color) if chosen else theme.text
        icon = submenu["icons"][position] or look.icon_name_for(submenu["items"][position])
        pen.icon(icon, x + 12, top + (row_h - 2 - 16) / 2, 16, fg if chosen else color)
        pen.text(submenu["items"][position], x + 34, top + (row_h - 2) / 2 + pen.size(13) * 0.36, 13, fg, chosen,
                 max_w=width - 44)


def _paint_dial_off(pen: Pen) -> None:
    theme = pen.theme
    pen.icon(ICON_NAMES["app"], 136, 66, 48, theme.faint)
    pen.text("Dial off", 160, 150, 22, theme.text, True, "c")
    pen.text("Press Menu to turn it on", 160, 176, 13, theme.muted, False, "c")
    pen.text("or OK on the screen", 160, 194, 13, theme.muted, False, "c")


def paint_dial_page(pen: Pen, scene: Scene) -> None:
    theme = pen.theme
    if not scene.state.get("enabled"):
        _paint_dial_off(pen)
        return
    view, look_view = dial_view(scene)
    submenu = look.open_submenu(look_view)
    selected = look.is_selected_mode(scene.state)
    available = SAFE_BOTTOM - TOP - 1
    if submenu:
        size = 152
        paint_dial(pen.cr, view, -2, TOP + (available - size) / 2 + 6, size, draw_icon=icons.draw_icon)
        _paint_list(pen, view, submenu, 148, TOP, 168, available)
        return
    size = available
    paint_dial(pen.cr, view, 0, TOP, size, draw_icon=icons.draw_icon)
    x, width = 204, 112
    title = scene.state.get("title") or ""
    if title == "Menu":
        title = ""
    color = _rgb(scene.details.get("color"), theme.accent) if selected else theme.accent
    if not selected and title:
        titles = view.titles
        color = view.colors[titles.index(title)] if title in titles and titles.index(title) < len(view.colors) else theme.accent
        color = _rgb(color, theme.accent) if isinstance(color, str) else color[:3]
    heading = title or (scene.details.get("profile") or scene.profile.name or "Dial")
    y = TOP + 22
    for line in pen.wrap(heading, width, 17, True, 2):
        pen.text(line, x, y, 17, color if title else theme.text, True)
        y += pen.size(20)
    if selected:
        if view.unit and view.value is not None:
            pen.text(f"{look.format_value(view.value)}{view.unit}", x, y + 18, 30, theme.text, True, max_w=width)
            y += pen.size(34)
        hints = scene.details.get("hints") if isinstance(scene.details.get("hints"), dict) else {}
        y += 8
        for key, clockwise in (("ccw", False), ("cw", True)):
            if hints.get(key):
                pen.turn_arrow(x + 6, y - 4, clockwise, color)
                pen.text(hints[key], x + 18, y, 11, theme.muted, False, max_w=width - 18)
                y += 18
        legend = [("▲▼", "turn"), ("OK", hints.get("center") or "tap centre"), ("Back", "back")]
    else:
        legend = [("▲▼", "choose"), ("OK", "open"), ("Back", "back")]
    # The arrows are drawn, not typed (the font may lack them).
    _legend_keys(pen, x, SAFE_BOTTOM - 70, legend, width)


def _legend_keys(pen: Pen, x: float, y: float, rows: list, width: float) -> None:
    theme = pen.theme
    for key, what in rows:
        if key == "▲▼":
            pen.fill_rrect(x, y - 12, 26, 16, 8, theme.card_hi)
            for offset, up in ((-5, True), (5, False)):
                pen.cr.new_path()
                top = y - 4 + (-1.5 if up else 1.5)
                tip = -3.2 if up else 3.2
                pen.cr.move_to(x + 13 + offset, top + tip)
                pen.cr.line_to(x + 13 + offset - 3, top - tip)
                pen.cr.line_to(x + 13 + offset + 3, top - tip)
                pen.cr.close_path()
                pen.cr.set_source_rgb(*theme.text)
                pen.cr.fill()
            used = 26
        else:
            used = pen.chip(x, y - 12, key, theme.card_hi, theme.text, 16)
        pen.text(what, x + used + 6, y, 11, theme.muted, False, max_w=width - used - 6)
        y += 21


# --- device page ------------------------------------------------------------

def device_blocks(entries: list, large: bool = False) -> list:
    """The device page as blocks (kind, payload, height): group headers, rows
    of two plain keys, and one row per key that has a long press."""
    scale = 1.18 if large else 1.0
    row_h, header_h = round(DEVICE_ROW_H * scale), round(DEVICE_HEADER_H * scale)
    merged = []
    for title, members in grouped_entries(entries):
        if title.startswith("System") and merged and merged[-1][0].startswith("Motion"):
            merged[-1] = ("Motion & system keys", merged[-1][1] + members)
        else:
            merged.append((title, members))
    blocks = []
    for title, members in merged:
        blocks.append(("header", title, header_h))
        if any(entry.long_index is not None for entry in members):
            blocks.extend(("dual", entry, row_h) for entry in members)
        else:
            for position in range(0, len(members), 2):
                blocks.append(("pair", members[position:position + 2], row_h))
    return blocks


def device_row_step(large: bool = False) -> int:
    return round(DEVICE_ROW_H * (1.18 if large else 1.0))


def device_steps(entries: list, large: bool = False) -> int:
    """How many scroll steps the device page needs to show everything."""
    total = sum(height for _kind, _payload, height in device_blocks(entries, large))
    visible = SAFE_BOTTOM - TOP - 1
    return max(0, math.ceil((total - visible) / device_row_step(large)))


def _device_face(pen: Pen, face, native: str, x, y, width, bold=True):
    """A key's action coloured, or its device name dimmed when the app handles it."""
    theme = pen.theme
    if face is not None and face.bound:
        pen.text(face.title, x, y, 13, _face_color(face, theme), bold and not face.inherited, max_w=width)
    else:
        pen.text(native, x, y, 13, theme.faint, False, max_w=width)


def _layer_of(scene: Scene) -> str:
    return str(scene.driver.get("layer") or "")


def paint_device(pen: Pen, scene: Scene) -> None:
    theme, cr = pen.theme, pen.cr
    large = pen.k > 1
    step = device_row_step(large)
    cr.save()
    cr.rectangle(0, TOP, WIDTH, SAFE_BOTTOM - TOP - 1)
    cr.clip()
    y = TOP - scene.view.scroll * step
    first_header = True
    for kind, payload, height in device_blocks(scene.entries, large):
        base = y + height / 2 + pen.size(13) * 0.36
        if kind == "header":
            pen.text(payload.upper(), 10, y + height - 5, 11, theme.muted, True)
            if first_header:
                note = "dimmed: from the base keys" if _layer_of(scene) else "dimmed: the app's own"
                pen.text(note, 312, y + height - 5, 11, theme.faint, False, "r")
                first_header = False
        elif kind == "pair":
            for column, entry in enumerate(payload):
                x = 4 + column * 158
                pen.fill_rrect(x, y + 1, 154, height - 2, 6, theme.card)
                face_color = _face_color(entry.short, theme)
                pen.fill_rrect(x + 5, y + 4, 40, height - 8, 5, face_color if entry.short.bound else mix(theme.card, theme.text, 0.12))
                pen.text(entry.key.label, x + 25, base, 11, on_color(face_color) if entry.short.bound else theme.muted, True, "c", max_w=38)
                _device_face(pen, entry.short, entry.key.name, x + 52, base, 98)
        else:
            entry = payload
            pen.fill_rrect(4, y + 1, 312, height - 2, 6, theme.card)
            face_color = _face_color(entry.short, theme)
            pen.fill_rrect(9, y + 4, 40, height - 8, 5, face_color if entry.short.bound else mix(theme.card, theme.text, 0.12))
            pen.text(entry.key.label, 29, base, 11, on_color(face_color) if entry.short.bound else theme.muted, True, "c", max_w=38)
            _device_face(pen, entry.short, entry.key.name, 56, base, 112)
            lcolor = _face_color(entry.long, theme) if entry.long else theme.faint
            if entry.long_index is not None:
                pen.hold_symbol(180, base - 4, lcolor)
            _device_face(pen, entry.long, entry.key.long_name, 190, base, 120)
        y += height
    cr.restore()
    steps = device_steps(scene.entries, large)
    if steps:
        track = SAFE_BOTTOM - TOP - 3
        thumb = max(16, track * (SAFE_BOTTOM - TOP - 1) / (steps * step + SAFE_BOTTOM - TOP - 1))
        offset = (track - thumb) * scene.view.scroll / steps
        pen.fill_rrect(317, TOP + 1 + offset, 2, thumb, 1, theme.muted)


# --- motion page ------------------------------------------------------------

def _norm(value, full=AXIS_FULL):
    return max(-1.0, min(1.0, float(value) / full))


def paint_motion(pen: Pen, scene: Scene) -> None:
    theme, cr = pen.theme, pen.cr
    axes = list(scene.axes) + [0] * (6 - len(scene.axes))
    translate, rotate = (0.56, 0.66, 1.0), (1.0, 0.68, 0.4)
    for number, label in enumerate(AXIS_LABELS):
        y = TOP + 4 + number * 34
        color = translate if number < 3 else rotate
        pen.text(label, 8, y + 18, 13, theme.muted, True)
        pen.fill_rrect(36, y + 4, 136, 16, 8, theme.card)
        value = _norm(axes[number])
        pen.fill_rrect(103, y + 5, 2, 14, 1, theme.faint)
        if abs(value) > 0.01:
            left = 104 + (0 if value > 0 else value * 66)
            pen.fill_rrect(left, y + 6, max(4, abs(value) * 66), 12, 6, color)
        pen.text(str(round(axes[number])), 206, y + 18, 11, theme.muted, False, "r")
    # The cap seen from above: the dot is the tilt, the arc the twist.
    config = scene.config.spacemouse
    cx, cy, radius = 262, 98, 42
    tilt_h = _norm(axes[config.tilt_axes[0]] if 0 <= config.tilt_axes[0] < 6 else 0, config.axis_max)
    tilt_v = _norm(axes[config.tilt_axes[1]] if 0 <= config.tilt_axes[1] < 6 else 0, config.axis_max)
    if config.tilt_invert[0]:
        tilt_h = -tilt_h
    if config.tilt_invert[1]:
        tilt_v = -tilt_v
    twist = _norm(axes[config.twist_axis] if 0 <= config.twist_axis < 6 else 0, config.axis_max)
    if config.twist_invert:
        twist = -twist
    push = _norm(axes[config.push_axis] if 0 <= config.push_axis < 6 else 0, config.axis_max)
    if config.push_invert:
        push = -push
    pen.dot(cx, cy, radius, theme.card)
    cr.set_line_width(1)
    cr.set_source_rgb(*theme.faint)
    for dx, dy in ((radius, 0), (0, radius)):
        cr.move_to(cx - dx, cy - dy)
        cr.line_to(cx + dx, cy + dy)
    cr.stroke()
    cr.arc(cx, cy, radius * 0.5, 0, 2 * math.pi)
    cr.stroke()
    if abs(twist) > 0.02:
        cr.new_path()
        cr.set_line_width(4)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_source_rgb(*theme.accent)
        start = -math.pi / 2
        end = start + twist * math.radians(150)
        (cr.arc if twist > 0 else cr.arc_negative)(cx, cy, radius + 7, start, end)
        cr.stroke()
    length = math.hypot(tilt_h, tilt_v)
    scale = 1 / length if length > 1 else 1
    pen.dot(cx + tilt_h * scale * (radius - 8), cy + tilt_v * scale * (radius - 8), 7 + max(0.0, push) * 4, theme.accent)
    pen.text("tilt  ·  twist", cx, cy + radius + 22, 11, theme.muted, False, "c")
    pen.text("push" if push > 0.3 else "", cx, cy + radius + 38, 11, theme.accent, True, "c")
    paint_motion_status(pen, scene, cx)


def paint_motion_status(pen: Pen, scene: Scene, cx: float) -> None:
    """Under the cap: the sensitivity and which parts of the motion are on (the
    driver's session toggles for other programs)."""
    theme = pen.theme
    motion = motion_state(scene.driver)
    if motion is None:
        return
    pen.text("Speed " + format_speed(motion["sensitivity"]), cx, 196, 13, theme.text, True, "c", max_w=104)
    for row, (label, on) in enumerate((("ROT", motion["rotation"]), ("PAN", motion["translation"]))):
        text = label if on else f"{label} off"
        width = pen.width(text, 11, True) + 12
        top = 202 + row * 18
        if on:
            pen.chip(cx - width / 2, top, text, theme.accent, on_color(theme.accent))
        else:
            pen.chip(cx - width / 2, top, text, theme.card, theme.faint)


# --- clock page -------------------------------------------------------------

def link_status(scene: Scene) -> str:
    """"Blender add-on connected  ·  Edit Mesh" for a linked application."""
    name = scene.app_name or str(scene.driver.get("app") or "").rsplit(".", 1)[-1].title() or "Application"
    text = f"{name} add-on connected"
    context = scene.driver.get("app_context") if isinstance(scene.driver.get("app_context"), dict) else {}
    label = str(context.get("label") or "")
    return f"{text}  ·  {label}" if label else text


def paint_clock(pen: Pen, scene: Scene) -> None:
    theme = pen.theme
    now = scene.now
    pen.text(clock_text(now, scene.settings), 160, 112, 66, theme.text, True, "c", scaled=False)
    pen.text(f"{now:%A}, {now.day} {now:%B}", 160, 142, 17, theme.muted, False, "c", max_w=300)
    name = scene.profile.name or "Turnpad"
    width = pen.width(name, 16, True)
    pen.dot(160 - width / 2 - 12, 174, 5, theme.accent)
    pen.text(name, 160 - width / 2, 180, 16, theme.text, True, max_w=280)
    connected = scene.connected or bool(scene.driver.get("spacemouse"))
    dial = "Dial on" if scene.driver.get("dial") or scene.state.get("enabled") else "Dial off"
    status = f"SpaceMouse connected  ·  {dial}" if connected else f"SpaceMouse not found  ·  {dial}"
    pen.text(status, 160, 220, 12, theme.muted, False, "c", max_w=300)
    if scene.driver.get("app_link"):
        pen.text(link_status(scene), 160, 202, 12, theme.accent, False, "c", max_w=300)


# --- overlays ---------------------------------------------------------------

def _veil(pen: Pen, alpha: float) -> None:
    pen.cr.set_source_rgba(0, 0, 0, alpha)
    pen.cr.paint()


def paint_picker(pen: Pen, scene: Scene) -> None:
    theme = pen.theme
    pages = scene.view.pages
    row_h = round(pen.size(28))
    width, height = 210, 36 + row_h * len(pages)
    x, y = (WIDTH - width) / 2, TOP + 6
    _veil(pen, 0.55)
    pen.fill_rrect(x, y, width, height, 12, theme.card)
    pen.stroke_rrect(x + 0.5, y + 0.5, width - 1, height - 1, 12, theme.faint, 1)
    pen.text("PAGES", x + 16, y + 21, 11, theme.muted, True)
    for position, page in enumerate(pages):
        top = y + 30 + position * row_h
        chosen = position == scene.view.overlay_index
        if chosen:
            pen.fill_rrect(x + 6, top, width - 12, row_h - 2, 8, theme.accent)
        fg = on_color(theme.accent) if chosen else theme.text
        pen.text(PAGE_NAMES.get(page, page.title()), x + 18, top + (row_h - 2) / 2 + pen.size(15) * 0.36, 15, fg, True)
        if page == scene.view.page:
            pen.dot(x + width - 20, top + (row_h - 2) / 2, 3.5, fg)


def paint_settings(pen: Pen, scene: Scene) -> None:
    theme = pen.theme
    rows = scene.view.settings_rows
    row_h = round(pen.size(25))
    x, y, width = 10, TOP + 2, 300
    height = SAFE_BOTTOM - y - 2
    visible = max(1, int((height - 30) // row_h))
    first = max(0, min(len(rows) - visible, scene.view.overlay_index - visible // 2))
    _veil(pen, 0.6)
    pen.fill_rrect(x, y, width, height, 12, theme.card)
    pen.stroke_rrect(x + 0.5, y + 0.5, width - 1, height - 1, 12, theme.faint, 1)
    pen.text("QUICK SETTINGS", x + 16, y + 21, 11, theme.muted, True)
    for line in range(visible):
        position = first + line
        if position >= len(rows):
            break
        label, value = rows[position]
        top = y + 30 + line * row_h
        chosen = position == scene.view.overlay_index
        middle = top + (row_h - 2) / 2
        if chosen:
            pen.fill_rrect(x + 6, top, width - 12, row_h - 2, 8, theme.accent)
        fg = on_color(theme.accent) if chosen else theme.text
        pen.text(label, x + 18, middle + pen.size(14) * 0.36, 14, fg, chosen, max_w=150)
        right = x + width - 18
        if chosen:
            pen.triangle(right - 2, middle, 8, "right", fg)
            right -= 14
        drawn = pen.text(value, right, middle + pen.size(14) * 0.36, 14, fg, True, "r", max_w=120)
        if chosen:
            pen.triangle(right - drawn - 12, middle, 8, "left", fg)
    if len(rows) > visible:
        track = height - 40
        thumb = max(12, track * visible / len(rows))
        pen.fill_rrect(x + width - 6, y + 32 + (track - thumb) * first / (len(rows) - visible), 2, thumb, 1, theme.muted)


def paint_toast(pen: Pen, scene: Scene) -> None:
    toast = scene.view.toast
    if toast is None:
        return
    theme = pen.theme
    color = _rgb(toast.color, theme.accent) if toast.color else theme.accent
    height = round(pen.size(42))
    x, y, width = 10, SAFE_BOTTOM - 4 - height, 300
    pen.fill_rrect(x, y, width, height, 10, theme.card_hi)
    pen.fill_rrect(x, y, 5, height, 2.5, color)
    pen.stroke_rrect(x + 0.5, y + 0.5, width - 1, height - 1, 10, color, 1)
    detail_w = pen.width(toast.detail, 12) if toast.detail else 0
    if not toast.detail and pen.width(toast.title, 16, True) > width - 32:
        # A long message (a notice, a suggestion) runs over two lines.
        lines = pen.wrap(toast.title, width - 32, 13, True, 2)
        top = y + height / 2 - (len(lines) - 1) * pen.size(16) / 2 + pen.size(13) * 0.36
        for number, line in enumerate(lines):
            pen.text(line, x + 16, top + number * pen.size(16), 13, theme.text, True, max_w=width - 32)
        return
    pen.text(toast.title, x + 16, y + height / 2 + pen.size(16) * 0.36, 16, theme.text, True,
             max_w=width - 32 - (detail_w + 10 if toast.detail else 0))
    if toast.detail:
        pen.text(toast.detail, x + width - 12, y + height / 2 + pen.size(12) * 0.36, 12, theme.muted, False, "r",
                 max_w=130)


PAGE_PAINTERS = {
    "keys": paint_keys,
    "dial": paint_dial_page,
    "device": paint_device,
    "motion": paint_motion,
    "clock": paint_clock,
}


def paint_error(cr, theme: Theme, message: str) -> None:
    cr.set_source_rgb(*theme.bg)
    cr.paint()
    pen = Pen(cr, theme)
    pen.text("Cannot draw this page", 160, 110, 16, theme.text, True, "c")
    for number, line in enumerate(pen.wrap(message, 290, 11, False, 3)):
        pen.text(line, 160, 134 + number * 16, 11, theme.muted, False, "c")


def paint(cr, scene: Scene) -> None:
    """Paint a whole frame: background, top bar, the page, then overlays,
    the toast and the dimming of an idle screen."""
    theme = make_theme(scene.settings, scene.skin, overlay_appearance(scene.config)["accent"])
    if scene.view.game is not None:
        from screen import game

        game.paint(cr, scene.view.game, theme)  # the hidden game owns the whole screen
        return
    large = scene.settings.text_size == "large"
    pen = Pen(cr, theme, large)
    cr.set_source_rgb(*theme.bg)
    cr.paint()
    try:
        paint_bar(pen, scene)
        PAGE_PAINTERS.get(scene.view.page, paint_keys)(pen, scene)
        if scene.view.overlay == "picker":
            paint_picker(pen, scene)
        elif scene.view.overlay == "settings":
            paint_settings(pen, scene)
        paint_toast(pen, scene)
    except Exception as error:  # a broken page must not stop the screen
        cr.reset_clip()
        paint_error(cr, theme, f"{type(error).__name__}: {error}")
    if scene.view.power == "off":
        _veil(pen, 1.0)
    elif scene.view.power == "dim":
        _veil(pen, 0.45)


def paint_stopped(cr) -> None:
    """The last frame: the service has stopped."""
    theme = make_theme(EffectiveScreen(theme="dark"))
    cr.set_source_rgb(*theme.bg)
    cr.paint()
    pen = Pen(cr, theme)
    pen.text("Turnpad stopped", 160, 124, 20, theme.muted, True, "c")


def build_scene(config: DialPadConfig, profile_name: str | None, view: UIView, *, state=None, details=None,
                driver=None, axes=None, now=None, connected=False, skin=None, commands=None, app_name="",
                resolved=None) -> Scene:
    """A Scene for a configuration: the profile (and context and layer the
    driver state names), its effective settings and key faces. ``profile_name``
    wins over the driver state's profile; ``resolved`` (content.resolve()) saves
    working them out again."""
    driver = dict(driver or {})
    if profile_name:
        driver["profile"] = profile_name
    if resolved is None:
        resolved = resolve(config, driver, commands)
    return Scene(
        config=config, profile=resolved.profile, settings=resolved.settings, view=view,
        entries=resolved.entries,
        state=state if state is not None else look.new_state(),
        details=details if details is not None else look.new_details(),
        driver=driver, axes=tuple(axes) if axes is not None else (0,) * 6,
        now=now or datetime.now(), connected=connected, skin=skin if skin is not None else default_skin(config),
        app_name=app_name,
    )


# --- demo data (previews without a driver) -----------------------------------

DEMO_AXES = (35, -60, 12, -140, 230, 90)


def demo_overlay(config: DialPadConfig, profile: Profile):
    """(state, details): the dial on with the profile's menu, the second
    entry highlighted, or the list of the first group open."""
    segments = menu_segments(config, profile)
    titles = [segment.title for segment in segments]
    icon_names = [segment_icon(segment) if segment.is_group else segment.icon_url or "" for segment in segments]
    colors = [segment_color(segment, index) for index, segment in enumerate(segments)]
    state = look.new_state()
    state.update(enabled=True, titles=titles, icons=icon_names, menuTitles=titles, menuIcons=icon_names, touching=True)
    details = look.new_details()
    details.update(profile=profile.name, colors=colors, mode=profile.mode)
    group = next(((index, s) for index, s in enumerate(segments) if s.is_group), None)
    if group is not None:
        items = group[1].active_items()
        details["submenu"] = {
            "title": group[1].title, "items": [item.title for item in items],
            "icons": [segment_icon(item) for item in items],
            "colors": [segment_color(item, index) for index, item in enumerate(items)], "selected": min(1, len(items) - 1),
        }
        state["title"] = items[min(1, len(items) - 1)].title
    elif titles:
        state["title"] = titles[min(1, len(titles) - 1)]
    return state, details
