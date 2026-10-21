import copy
import json
import math
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import gi

gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk

from config.profile_share import ProfileTextError, profile_json, profiles_from_text
from config.profile_share import ai_prompt as profile_ai_prompt
from config.labels import (
    EVDEV_CODE_NAMES,
    ICON_CHOICES,
    MODIFIER_KEYS,
    describe_macro,
    describe_step,
    format_keys,
    keys_to_text,
    order_keys,
    parse_keys,
    segment_icon,
    GROUP_ICON,
    KNOWN_KEYS,
)
from config.presets import (
    FUNCTION_LIBRARY,
    LIBRARY_CATEGORIES,
    pack_for_app,
    profile_for_app,
    segment_color,
    variant_summary,
)
from config.schema import (
    CONFIG_DIR,
    CONFIG_FILE,
    DARKTABLE_APP_ID,
    MODIFIER_KEYS,
    OVERLAY_POSITIONS,
    POINTER_GAP_MAX,
    SCREEN_PAGES,
    SPACEMOUSE_LABEL_MAX,
    ConfigError,
    DialPadConfig,
    MacroStep,
    ModifierActions,
    Profile,
    Segment,
    SpaceMouseAction,
    SpaceMouseButton,
    StepType,
    _encoder,
    darktable_colour_labels_segment,
    describe_changes,
    load_config,
    merge_portable,
    normalize_key_name,
    portable_config,
    save_config,
    settings_document,
    settings_from_text,
    shell_commands,
)
from config.identity import APP_ID, APP_NAME, DBUS_INTERFACE, DBUS_NAME, DBUS_PATH, DRIVER_SERVICE, SCREEN_SERVICE
from config.migrate import layout_geometry, migrate_old_format
from config.skins import (
    DIAL_SHAPES,
    DIR_KEY,
    MENU_STYLES,
    PALETTE_KEYS,
    SKINS_DIR,
    TILE_SHAPES,
    TILE_SIZE_MAX,
    TILE_SIZE_MIN,
    ai_prompt,
    color_to_hex,
    delete_user_skin,
    is_builtin,
    load_skins,
    new_skin_id,
    resample_outline,
    resolve_skin,
    save_user_skin,
    skin_name,
    user_skins,
    without_private_keys,
)
from config_app import contexts as cx
from config_app import hardware as hw
from config_app import screen_page as sp
from config_app import spacemouse as sm
from config_app import spacemouse_extra as sx
from config_app.commandline import parse as parse_command_line
from config_app.commandline import resolve_app
from config_app.context_dialogs import contexts_group
from config_app.device_dialogs import MousePicker, SetupAssistant
from config_app.outline_editor import PRESETS, OutlineEditor
from config_app.plugins_ui import PluginsPage
from config_app.recorder_dialog import MacroRecorderDialog
from config_app.spacemouse_ui import SpaceMouseExtras
from desktop.color_scheme import ColorScheme
from overlay.gtk_icons import draw_icon
from overlay.painter import (
    LIST_MARGIN,
    PAINTED_D,
    DIAL_R,
    DialView,
    hit_function,
    open_submenu,
    paint_dial,
    paint_submenu,
    submenu_size,
)
from spacemouse_devices import SPACEPILOT_PRO, screens_present


STYLE_FILE = Path(__file__).with_name("style.css")
DEFAULT_ACCENT = "#e7b98d"
# The PX13 touchpad range; replaced by the driver's report when available.
DEFAULT_TOUCHPAD = (4010, 2443)
# Switches the dial on or off; for a keyboard shortcut.
TOGGLE_COMMAND = f"gdbus call --session --dest {DBUS_NAME} --object-path {DBUS_PATH} --method {DBUS_INTERFACE}.Toggle"

TRIGGERS = (
    ("macro_cw", "Turn clockwise", "object-rotate-right-symbolic",
     "Runs once per rotation step. Several actions run from top to bottom."),
    ("macro_ccw", "Turn counter-clockwise", "object-rotate-left-symbolic",
     "Runs once per rotation step. Several actions run from top to bottom."),
    ("macro_center", "Tap", "input-touchpad-symbolic",
     "Tap the centre while the function is active; a function with only a tap "
     "action runs straight from its menu sector"),
)

ACTION_TYPES = (
    ("Keyboard shortcut", StepType.KEYSTROKE),
    ("Media key", StepType.MEDIA),
    ("Volume", StepType.VOLUME),
    ("Brightness", StepType.BRIGHTNESS),
    ("Scroll", StepType.SCROLL),
    ("Command", StepType.SHELL),
    ("Wait", StepType.DELAY),
)

# The default menu, used to preview skins.
SAMPLE_TITLES = ("Volume", "Brightness", "Scroll", "Zoom", "Undo / Redo", "Media")
SAMPLE_COLORS = ("#6cb6ff", "#f4c95d", "#9ad17b", "#e7b98d", "#c99bff", "#ff8f8f")
MENU_STYLE_LABELS = {"bubbles": "Icon bubbles", "segments": "Ring of sectors"}
SHAPE_LABELS = {
    "circle": "Circle", "square": "Square", "octagon": "Octagon",
    "knurled": "Knurled edge", "scalloped": "Wavy edge", "custom": "Custom (draw it below)",
}
TILE_LABELS = {"circle": "Circle", "square": "Rounded square", "diamond": "Diamond", "hexagon": "Hexagon"}
# Background pictures are stored as PNG no larger than this.
BACKGROUND_SIZE = 720

POSITION_LABELS = {
    "center": "Centre of the screen",
    "lower": "Slightly below centre",
    "bottom": "Bottom of the screen",
    "top": "Top of the screen",
    "custom": "Custom place",
    "pointer": "Near the pointer",
}
POINTER_SIDE_LABELS = {
    "below-right": "Below right",
    "below-left": "Below left",
    "above-right": "Above right",
    "above-left": "Above left",
    "right": "Right",
    "left": "Left",
    "above": "Above",
    "below": "Below",
}
VISIBILITY_LABELS = {
    "full": "Always visible",
    "fade": "Fade while turning",
    "pill": "Small label instead of the dial",
}


def _load_proart_style():
    """Apply the local, intentionally restrained ProArt visual treatment."""
    # The palette is dark only; on a light desktop Adwaita would draw white
    # lists and cards on it.
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    display = Gdk.Display.get_default()
    if display is None or not STYLE_FILE.exists():
        return
    provider = Gtk.CssProvider()
    try:
        provider.load_from_path(str(STYLE_FILE))
    except GLib.Error:
        return
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


def _clear_children(widget):
    child = widget.get_first_child()
    while child is not None:
        next_child = child.get_next_sibling()
        widget.remove(child)
        child = next_child


def _hex_to_rgb(text, fallback=DEFAULT_ACCENT):
    rgba = Gdk.RGBA()
    if not text or not rgba.parse(text):
        rgba.parse(fallback)
    return rgba.red, rgba.green, rgba.blue


def _rgba_to_hex(rgba):
    return "#{:02x}{:02x}{:02x}".format(
        round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255)
    )


def _set_icon_image(image, icon):
    """Show a theme icon name or an absolute picture path."""
    if icon.startswith("/"):
        image.set_from_file(icon)
    else:
        image.set_from_icon_name(icon)


def _icon_image(icon):
    image = Gtk.Image()
    _set_icon_image(image, icon)
    return image


def _icon_button(icon_name, tooltip, callback, *css):
    button = Gtk.Button.new_from_icon_name(icon_name)
    button.set_tooltip_text(tooltip)
    button.set_valign(Gtk.Align.CENTER)
    button.add_css_class("flat")
    for name in css:
        button.add_css_class(name)
    button.connect("clicked", lambda _button: callback())
    return button


def _button_row(title, icon_name):
    """A row that acts as a button; callers connect "activated".

    Adw.ButtonRow needs Libadwaita 1.6; older ones get an activatable ActionRow.
    """
    if hasattr(Adw, "ButtonRow"):
        return Adw.ButtonRow(title=title, start_icon_name=icon_name)
    row = Adw.ActionRow(title=title, activatable=True)
    row.add_prefix(Gtk.Image.new_from_icon_name(icon_name))
    return row


def _page_sidebar(stack):
    """Navigation between the pages of an Adw.ViewStack.

    Adw.ViewSwitcherSidebar needs Libadwaita 1.9; older ones (Ubuntu 24.04,
    Debian 13) get a navigation list showing the same pages that follows the
    stack both ways, also when code switches the page.
    """
    if hasattr(Adw, "ViewSwitcherSidebar"):
        sidebar = Adw.ViewSwitcherSidebar()
        sidebar.set_stack(stack)
        return sidebar
    sidebar = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
    sidebar.add_css_class("navigation-sidebar")
    names = []
    pages = stack.get_pages()
    for index in range(pages.get_n_items()):
        page = pages.get_item(index)
        names.append(page.get_name())
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.append(Gtk.Image.new_from_icon_name(page.get_icon_name()))
        box.append(Gtk.Label(label=page.get_title(), xalign=0, hexpand=True))
        row = Gtk.ListBoxRow(child=box)
        # A page can be hidden (Screen without a screen): its row goes too.
        row.set_visible(page.get_visible())
        page.connect("notify::visible", lambda page, _param, row=row: row.set_visible(page.get_visible()))
        sidebar.append(row)

    def show_page(_sidebar, row):
        if row is not None and stack.get_visible_child_name() != names[row.get_index()]:
            stack.set_visible_child_name(names[row.get_index()])

    def follow_stack(*_args):
        name = stack.get_visible_child_name()
        if name in names:
            row = sidebar.get_row_at_index(names.index(name))
            if sidebar.get_selected_row() is not row:
                sidebar.select_row(row)

    sidebar.connect("row-selected", show_page)
    stack.connect("notify::visible-child-name", follow_stack)
    follow_stack()
    return sidebar


def _spin_row(title, subtitle, value, minimum, maximum, step, digits, changed):
    row = Adw.SpinRow.new_with_range(minimum, maximum, step)
    row.set_title(title)
    if subtitle:
        row.set_subtitle(subtitle)
    row.set_digits(digits)
    row.set_value(value)
    row.connect("notify::value", lambda row, _param: changed(row.get_value()))
    return row


def _scale_row(title, subtitle, value, changed):
    """A row with a 0-100 slider."""
    row = Adw.ActionRow(title=title)
    if subtitle:
        row.set_subtitle(subtitle)
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
    scale.set_value(value)
    scale.set_draw_value(True)
    scale.set_value_pos(Gtk.PositionType.RIGHT)
    scale.set_size_request(180, -1)
    scale.set_valign(Gtk.Align.CENTER)
    scale.connect("value-changed", lambda scale: changed(int(round(scale.get_value()))))
    row.add_suffix(scale)
    return row


def _switch_row(title, subtitle, active, changed):
    row = Adw.SwitchRow(title=title)
    if subtitle:
        row.set_subtitle(subtitle)
    row.set_active(active)
    row.connect("notify::active", lambda row, _param: changed(row.get_active()))
    return row


def _combo_row(title, subtitle, labels, selected, changed):
    row = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels))
    if subtitle:
        row.set_subtitle(subtitle)
    row.set_selected(selected)
    row.connect("notify::selected", lambda row, _param: changed(row.get_selected()))
    return row


def _colour_swatch(colour, size=14):
    area = Gtk.DrawingArea()
    area.set_content_width(size)
    area.set_content_height(size)
    area.set_valign(Gtk.Align.CENTER)

    def draw(_area, cr, width, height):
        cr.arc(width / 2, height / 2, min(width, height) / 2 - 0.5, 0, 2 * math.pi)
        cr.set_source_rgb(*_hex_to_rgb(colour))
        cr.fill()

    area.set_draw_func(draw)
    return area


class _ScreenPicture(Gtk.Widget):
    """The preview of the screen: 4:3, as wide as it gets up to 640 px and then as
    high as that needs. A scrolled page gives a widget its minimum height, so the
    height must follow the width in the minimum too (a Gtk.Picture alone has none)."""

    WIDTH, HEIGHT = 640, 480

    def __init__(self, picture):
        super().__init__()
        self._picture = picture
        picture.set_parent(self)

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 0, self.WIDTH, -1, -1
        height = self.HEIGHT if for_size < 0 else round(min(for_size, self.WIDTH) * self.HEIGHT / self.WIDTH)
        return height, height, -1, -1

    def do_size_allocate(self, width, height, baseline):
        self._picture.allocate(width, height, baseline, None)

    def do_dispose(self):
        if self._picture is not None:
            self._picture.unparent()
            self._picture = None
        Gtk.Widget.do_dispose(self)


class Application(SpaceMouseExtras, PluginsPage, Adw.Application):
    # (name, title, icon, builder) of the pages in the sidebar's order.
    PAGES = (
        ("actions", "Dial Controls", "input-dialpad-symbolic", "_build_actions_page"),
        ("profiles", "Apps & Profiles", "applications-graphics-symbolic", "_build_profiles_page"),
        ("look", "Skins", "preferences-desktop-appearance-symbolic", "_build_look_page"),
        ("device", "Behaviour", "preferences-system-symbolic", "_build_behaviour_page"),
        ("calibration", "Calibration", "find-location-symbolic", "_build_calibration_page"),
        ("spacemouse", "SpaceMouse", "input-gaming-symbolic", "_build_spacemouse_page"),
        ("screen", "Screen", "video-display-symbolic", "_build_screen_page"),
        ("plugins", "Plugins", "application-x-addon-symbolic", "_build_plugins_page"),
        ("diagnostics", "Driver", "utilities-terminal-symbolic", "_build_diagnostics_page"),
    )

    def __init__(self):
        # HANDLES_COMMAND_LINE: a second launch (--add-profile from the driver's
        # notification) is forwarded to the window that is open.
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.config: DialPadConfig | None = None
        self._save_source = 0
        self._window = None
        self._toasts = None
        self._view_stack = None
        self._profiles_box = None
        self._actions_box = None
        self._actions_heading = None
        self._actions_subtitle = None
        self._profile_dropdown = None
        self._profile_dropdown_handler = 0
        self._selected_profile: Profile | None = None
        self._preview_title = None
        self._dial_preview = None
        self._skins = {}
        self._skin_gallery = None
        self._style_rows = {}
        self._color_scheme = None
        self._diagnostics_rows = {}
        self._session_bus = None
        self._calibration_preview = None
        self._touchpad_size = DEFAULT_TOUCHPAD
        self._geometry_rows = {}
        self._calibration_row = None
        self._calibration_start = None
        self._calibration_cancel = None
        self._calibration_points = []
        self._calibration_signal = 0
        self._calibration_keep = None
        self._calibration_revert = None
        # Geometry before a guided measurement, kept until the user keeps or
        # reverts the result: (TouchGeometry, device_name).
        self._calibration_backup = None
        # SpaceMouse page: the profile whose buttons are edited (None = all
        # applications), its widgets and the "press a button" listener.
        self._sm_scope: Profile | None = None
        self._sm_group = None
        self._sm_scope_row = None
        self._sm_scope_handler = 0
        self._sm_rows = {}
        self._sm_identify_row = None
        self._sm_probe = None
        self._sm_extra_buttons = set()
        self._sm_keys_box = None
        self._sm_device = ""
        self._sm_status_row = None
        self._sm_restart_source = 0
        self._init_spacemouse_extras()
        self._init_plugins_page()
        # Screen page: the profile edited (None = all applications), the
        # SpaceMouse screens found, the preview and the containers that are
        # filled again when something they show changes.
        self._screen_scope: Profile | None = None
        self._screen_scope_row = None
        self._screen_scope_handler = 0
        self._screen_present = []
        self._screen_page = None
        self._screen_status_rows = {}
        self._screen_look_box = None
        self._screen_pages_box = None
        self._screen_app_box = None
        self._screen_page_buttons = {}
        self._screen_preview = None
        self._screen_preview_frame = None
        self._screen_preview_note = None
        self._screen_preview_title = None
        self._screen_preview_page = "keys"
        self._screen_preview_source = 0
        self._screen_monitor = None
        self._screen_reload_source = 0
        # Fields of config.screen changed here and not saved yet (see screen_page.ScreenSync).
        self._screen_sync = sp.ScreenSync()
        # The pages (a stable Adw.Bin per page, so a page can be built again
        # after the configuration was replaced) and which of them fit the hardware.
        self._page_bins = {}
        self._view_pages = {}
        self._found = hw.Detected()
        self._driver_status = None
        self._app_settings = dict(hw.APP_SETTINGS_DEFAULTS)
        self._mouse_picker = None
        self._openers_group = None
        self._setup_assistant = None

    # --- lifecycle --------------------------------------------------------

    def do_activate(self):
        if self._window is not None:
            self._window.present()
            return

        self.config = (
            migrate_old_format(layout_name=self._detect_layout(), config_dir=str(PROJECT_ROOT))
            if not CONFIG_FILE.exists()
            else load_config()
        )
        self._selected_profile = self._default_profile()
        self._skins = load_skins()
        self._watch_system_style()

        _load_proart_style()
        self._window = Adw.ApplicationWindow(application=self)
        self._window.set_title(APP_NAME)
        self._window.set_default_size(1080, 760)
        self._window.add_css_class("proart-window")

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        brand.add_css_class("proart-brand")
        emblem = Gtk.Image.new_from_icon_name("input-dialpad-symbolic")
        emblem.set_pixel_size(20)
        brand.append(emblem)
        title = Gtk.Label(label=APP_NAME, xalign=0)
        title.add_css_class("title-4")
        brand.append(title)
        header.set_title_widget(brand)
        toolbar.add_top_bar(header)

        self._view_stack = Adw.ViewStack()
        self._app_settings = hw.load_app_settings()
        self._found = hw.detect()
        for name, page_title, icon_name, builder in self.PAGES:
            bin_ = Adw.Bin()
            bin_.set_child(getattr(self, builder)())
            page = self._view_stack.add_titled(bin_, name, page_title)
            page.set_icon_name(icon_name)
            self._page_bins[name] = bin_
            self._view_pages[name] = page
        self._screen_page = self._view_pages["screen"]
        self._update_page_visibility()
        self._view_stack.connect("notify::visible-child", self._on_visible_page_changed)

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        sidebar = _page_sidebar(self._view_stack)
        sidebar.set_size_request(218, -1)
        sidebar.add_css_class("proart-sidebar")
        content.append(sidebar)
        self._view_stack.set_hexpand(True)
        content.append(self._view_stack)
        toolbar.set_content(content)

        self._toasts = Adw.ToastOverlay()
        self._toasts.set_child(toolbar)
        self._window.set_content(self._toasts)
        self._window.connect("notify::is-active", self._on_window_active)
        self._refresh_all()
        self._watch_config_file()
        self._window.present()
        self._refresh_diagnostics()
        if not self.config.setup_done:
            # First run: after the window is up.
            GLib.idle_add(lambda: self._show_setup_assistant() or GLib.SOURCE_REMOVE)

    def do_command_line(self, command_line):
        """--add-profile APP_ID: open Apps & Profiles and add a profile for that application."""
        add_profile, error = parse_command_line(command_line.get_arguments()[1:])
        if error:
            command_line.printerr(f"{error}\nUsage: main.py [--add-profile APP_ID]\n")
            return 2
        self.activate()
        if add_profile and self.config is not None:
            self._add_profile_for_app_id(add_profile)
        return 0

    def _detect_layout(self):
        """Return the layout the installed driver runs with (geometry differs per model)."""
        connection = self._get_session_bus()
        if connection is not None:
            try:
                reply = connection.call_sync(
                    DBUS_NAME, DBUS_PATH, DBUS_INTERFACE, "GetStatus", None,
                    GLib.VariantType.new("(a{sv})"), Gio.DBusCallFlags.NONE, 1500, None,
                )
                layout = reply.unpack()[0].get("layout")
                if layout:
                    return str(layout)
            except GLib.Error:
                pass
        # The service passes the layout as the first argument of dialpad.py.
        for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
            try:
                args = cmdline.read_bytes().split(b"\0")
            except OSError:
                continue
            for index, arg in enumerate(args[:-1]):
                if arg.endswith(b"dialpad.py") and args[index + 1]:
                    return args[index + 1].decode(errors="ignore")
        return "proartp16"

    def do_shutdown(self):
        self._stop_spacemouse_probe()
        for source in (self._screen_preview_source, self._screen_reload_source):
            if source:
                GLib.source_remove(source)
        self._screen_preview_source = self._screen_reload_source = 0
        if self._screen_monitor is not None:
            self._screen_monitor.cancel()
            self._screen_monitor = None
        # A measurement nobody kept is not accepted: the driver goes back too.
        if self._calibration_backup is not None and self.config is not None:
            geometry, self.config.device_name = self._calibration_backup
            self.config.geometry = copy.copy(geometry)
            self._calibration_backup = None
            self._schedule_save()
        if self._sm_restart_source:
            # A SpaceMouse setting waits for its delayed restart: save and restart now.
            GLib.source_remove(self._sm_restart_source)
            self._sm_restart_source = 0
            self._restart_driver()
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_config()
            # Send the reload before the process ends.
            if self._session_bus is not None:
                try:
                    self._session_bus.flush_sync(None)
                except GLib.Error:
                    pass
        Adw.Application.do_shutdown(self)

    def _toast(self, text):
        if self._toasts is not None:
            self._toasts.add_toast(Adw.Toast.new(text))

    def _schedule_save(self):
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(400, self._save_config)

    def _save_config(self):
        self._save_source = 0
        if self.config is not None:
            # The screen service may have saved its settings since this window read them.
            self._adopt_screen_from_disk()
            save_config(self.config)
            self._screen_sync.clear()
            self._call_driver("ReloadConfig", callback=self._on_reload_reply)
        return GLib.SOURCE_REMOVE

    def _on_reload_reply(self, connection, result, *_user_data, success=None, unreachable=None):
        """Tell why when the driver refused the saved file (it keeps its settings)."""
        try:
            (accepted,) = connection.call_finish(result).unpack()
        except (GLib.Error, AttributeError):
            # No driver on the bus: it reads the file when it starts.
            if unreachable:
                self._toast(unreachable)
            return
        if accepted:
            if success:
                self._toast(success)
            self._refresh_diagnostics()
            return

        def refused(status_connection, status_result, *_args):
            try:
                status = status_connection.call_finish(status_result).unpack()[0]
                error = str(self._unpack_dbus_value(status.get("config_error")) or "")
            except (GLib.Error, AttributeError, IndexError):
                error = ""
            self._toast(f"The driver kept its previous settings: {error}" if error
                        else "The driver kept its previous settings")
            self._refresh_diagnostics()

        self._call_driver("GetStatus", "(a{sv})", refused)

    def _get_session_bus(self):
        if self._session_bus is not None:
            return self._session_bus
        try:
            self._session_bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return None
        return self._session_bus

    def _call_driver(self, method, reply="(b)", callback=None):
        connection = self._get_session_bus()
        if connection is None:
            if callback is not None:
                callback(None, None)
            return
        connection.call(
            DBUS_NAME,
            DBUS_PATH,
            DBUS_INTERFACE,
            method,
            None,
            GLib.VariantType.new(reply),
            Gio.DBusCallFlags.NONE,
            3000,
            None,
            callback,
        )

    def _changed(self, rebuild=True):
        """Persist the configuration and refresh what depends on it."""
        if rebuild:
            self._refresh_all()
        elif self._dial_preview is not None:
            self._dial_preview.queue_draw()
        self._schedule_save()

    # --- profile helpers ----------------------------------------------------

    def _default_profile(self):
        if self.config is None:
            return None
        for profile in self.config.profiles:
            if profile.app_id is None:
                return profile
        return self.config.profiles[0] if self.config.profiles else None

    def _current_profile(self):
        if self.config is None:
            return None
        if self._selected_profile in self.config.profiles:
            return self._selected_profile
        self._selected_profile = self._default_profile()
        return self._selected_profile

    @staticmethod
    def _profile_summary(profile):
        active = profile.active_segments()
        if not active:
            return "No functions — falls back to the default profile"
        if profile.mode == "single":
            segment = profile.single_segment()
            return f"Single function · {segment.title}"
        return f"Radial menu · {len(active)} function{'s' if len(active) != 1 else ''}"

    @staticmethod
    def _app_icon(app_id):
        if app_id:
            try:
                info = Gio.DesktopAppInfo.new(app_id)
            except TypeError:
                info = None
            if info is not None and info.get_icon() is not None:
                image = Gtk.Image.new_from_gicon(info.get_icon())
                image.set_pixel_size(32)
                return image
        image = Gtk.Image.new_from_icon_name(
            "input-dialpad-symbolic" if app_id is None else "application-x-executable-symbolic"
        )
        image.set_pixel_size(24)
        return image

    @staticmethod
    def _unique_title(profile, title, ignore=None, group=None):
        """A title no other function of the menu (or of the group) has."""
        siblings = group.items if group is not None else profile.segments
        existing = {segment.title for segment in siblings if segment is not ignore}
        if title not in existing:
            return title
        index = 2
        while f"{title} {index}" in existing:
            index += 1
        return f"{title} {index}"

    def _select_profile(self, profile, show=True):
        self._selected_profile = profile
        self._preview_title = None
        self._refresh_all()
        if show and self._view_stack is not None:
            self._view_stack.set_visible_child_name("actions")

    def _refresh_all(self):
        self._refresh_profiles_page()
        self._rebuild_actions_page()
        self._refresh_spacemouse_page()
        self._refresh_screen_page()

    def _on_visible_page_changed(self, stack, _param):
        name = stack.get_visible_child_name()
        if name != "spacemouse":
            self._stop_spacemouse_probe()
        if name == "actions":
            self._rebuild_actions_page()
        elif name in ("diagnostics", "spacemouse"):
            self._refresh_diagnostics()
        elif name == "plugins":
            self._refresh_plugins_page()
        elif name == "screen":
            self._adopt_screen_from_disk()
            self._refresh_screen_presence()
            self._refresh_screen_status()
            self._fill_screen_app()
            self._queue_screen_preview()

    def _scrolled_content(self, content, width=760):
        clamp = Adw.Clamp()
        clamp.set_maximum_size(width)
        clamp.set_child(content)
        scroll = Gtk.ScrolledWindow()
        scroll.set_child(clamp)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        return scroll

    @staticmethod
    def _page_box():
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.set_margin_top(18)
        content.set_margin_bottom(36)
        content.set_margin_start(12)
        content.set_margin_end(12)
        return content

    # --- Apps & Profiles ------------------------------------------------------

    def _build_profiles_page(self):
        content = self._page_box()

        intro = Gtk.Label(
            label=(
                "Like ASUS Dial, the DialPad switches profile with the focused "
                "application. Applications without their own profile use the default one."
            ),
            xalign=0,
            wrap=True,
        )
        intro.add_css_class("dim-label")
        content.append(intro)

        self._profiles_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.append(self._profiles_box)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_app = Gtk.Button(label="Add application…")
        add_app.add_css_class("suggested-action")
        add_app.connect("clicked", lambda _button: self._show_app_picker())
        buttons.append(add_app)
        add_default = Gtk.Button(label="Add default profile")
        add_default.connect("clicked", lambda _button: self._show_name_dialog("New default profile", "", self._create_default_profile))
        buttons.append(add_default)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        buttons.append(spacer)
        import_button = Gtk.Button(label="Import…")
        import_button.connect("clicked", lambda _button: self._import_profiles())
        buttons.append(import_button)
        export_button = Gtk.Button(label="Export…")
        export_button.connect("clicked", lambda _button: self._export_profiles())
        buttons.append(export_button)
        content.append(buttons)
        return self._scrolled_content(content)

    def _refresh_profiles_page(self):
        if self._profiles_box is None or self.config is None:
            return
        _clear_children(self._profiles_box)
        default_group = Adw.PreferencesGroup(
            title="Default profiles",
            description="Used when the focused application has no profile. The first one is active.",
        )
        app_group = Adw.PreferencesGroup(
            title="Application profiles",
            description="Switched automatically when the application gains focus.",
        )
        for profile in self.config.profiles:
            group = default_group if profile.app_id is None else app_group
            group.add(self._profile_row(profile))
        self._profiles_box.append(default_group)
        self._profiles_box.append(app_group)

    def _profile_row(self, profile):
        row = Adw.ActionRow(title=GLib.markup_escape_text(profile.name))
        subtitle = self._profile_summary(profile)
        if profile.contexts:
            subtitle = f"{subtitle} · {len(profile.contexts)} context{'s' if len(profile.contexts) != 1 else ''}"
        if profile.app_id:
            subtitle = f"{subtitle}\n{profile.app_id}"
        row.set_subtitle(GLib.markup_escape_text(subtitle))
        row.set_subtitle_lines(2)
        row.add_prefix(self._app_icon(profile.app_id))
        if profile is self._current_profile():
            row.add_css_class("proart-selected-row")
        row.set_activatable(True)
        row.connect("activated", lambda _row: self._select_profile(profile))

        row.add_suffix(_icon_button("go-next-symbolic", "Edit dial controls", lambda: self._select_profile(profile)))
        row.add_suffix(_icon_button("edit-copy-symbolic", "Duplicate profile", lambda: self._duplicate_profile(profile)))
        row.add_suffix(_icon_button(
            "document-edit-symbolic", "Rename profile",
            lambda: self._show_name_dialog("Rename profile", profile.name, lambda name: self._rename_profile(profile, name)),
        ))
        row.add_suffix(_icon_button("user-trash-symbolic", "Remove profile", lambda: self._confirm_remove_profile(profile), "error"))
        return row

    def _create_default_profile(self, name):
        profile = Profile(name=name, app_id=None)
        self.config.profiles.append(profile)
        self._select_profile(profile)
        self._schedule_save()

    def _rename_profile(self, profile, name):
        profile.name = name
        self._changed()

    def _duplicate_profile(self, profile):
        clone = copy.deepcopy(profile)
        clone.name = f"{profile.name} (copy)"
        # A copy of an application profile would shadow the original.
        clone.app_id = None
        self.config.profiles.insert(self.config.profiles.index(profile) + 1, clone)
        self._select_profile(clone, show=False)
        self._schedule_save()
        self._toast(f"Created “{clone.name}” as a default profile")

    def _confirm_remove_profile(self, profile):
        dialog = Adw.AlertDialog(
            heading=f"Remove “{profile.name}”?",
            body="Its functions and actions will be deleted.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def respond(_dialog, response):
            if response == "remove" and profile in self.config.profiles:
                self.config.profiles.remove(profile)
                if self._selected_profile is profile:
                    self._selected_profile = self._default_profile()
                self._changed()

        dialog.connect("response", respond)
        dialog.present(self._window)

    def _show_name_dialog(self, heading, value, accept):
        dialog = Adw.AlertDialog(heading=heading)
        entry = Gtk.Entry()
        entry.set_text(value)
        entry.set_activates_default(True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", "Save")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")

        def respond(_dialog, response):
            name = entry.get_text().strip()
            if response == "ok" and name:
                accept(name)

        dialog.connect("response", respond)
        dialog.present(self._window)
        entry.grab_focus()

    def _show_app_picker(self, query=""):
        dialog = Adw.Dialog(title="Add application", content_width=520, content_height=620)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_bottom(12)
        search = Gtk.SearchEntry()
        search.set_placeholder_text("Search applications")
        search.set_text(query)
        box.append(search)
        note = Gtk.Label(
            label="Applications marked “Pre-programmed” start with ready functions.",
            xalign=0, wrap=True,
        )
        note.add_css_class("dim-label")
        note.add_css_class("caption")
        box.append(note)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_child(listbox)
        box.append(scroll)
        view.set_content(box)
        dialog.set_child(view)

        existing = {profile.app_id for profile in self.config.profiles if profile.app_id}
        apps = [app for app in Gio.AppInfo.get_all() if app.get_id() and app.should_show()]
        apps.sort(key=lambda app: (pack_for_app(app.get_id()) is None, app.get_display_name().lower()))

        def choose(app):
            dialog.close()
            self._add_application(app.get_display_name(), app.get_id())

        def rebuild():
            _clear_children(listbox)
            query = search.get_text().strip().lower()
            for app in apps:
                if query and query not in app.get_display_name().lower() and query not in app.get_id().lower():
                    continue
                row = Adw.ActionRow(title=GLib.markup_escape_text(app.get_display_name()))
                row.set_subtitle(GLib.markup_escape_text(app.get_id()))
                icon = app.get_icon()
                image = Gtk.Image.new_from_gicon(icon) if icon else Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
                image.set_pixel_size(32)
                row.add_prefix(image)
                pack = pack_for_app(app.get_id()) or (app.get_id() == DARKTABLE_APP_ID)
                if app.get_id() in existing:
                    badge = Gtk.Label(label="Has profile")
                    badge.add_css_class("dim-label")
                    row.add_suffix(badge)
                elif pack:
                    badge = Gtk.Label(label="Pre-programmed")
                    badge.add_css_class("proart-badge")
                    badge.set_valign(Gtk.Align.CENTER)
                    row.add_suffix(badge)
                row.set_activatable(True)
                row.connect("activated", lambda _row, app=app: choose(app))
                listbox.append(row)

        search.connect("search-changed", lambda _entry: rebuild())
        rebuild()
        dialog.present(self._window)
        search.grab_focus()

    def _add_profile_for_app_id(self, app_id):
        """Add a profile for the application ``app_id`` (what the driver saw: a
        desktop id or a program name), asking basic or advanced when a pack exists."""
        if self._view_stack is not None:
            self._view_stack.set_visible_child_name("profiles")
        apps = [(app.get_id(), app.get_display_name()) for app in Gio.AppInfo.get_all() if app.get_id()]
        pack = pack_for_app(app_id)
        desktop_id, name, known = resolve_app(app_id, apps, pack.name if pack else None)
        if not known and pack is None:
            # Nothing to start from: the normal list, searched for the application.
            self._show_app_picker(app_id)
            return
        self._add_application(name, desktop_id)

    def _add_application(self, name, app_id):
        for profile in self.config.profiles:
            if profile.app_id == app_id:
                self._select_profile(profile)
                return

        def add(advanced):
            profile = profile_for_app(name, app_id, advanced)
            self.config.profiles.append(profile)
            self._select_profile(profile)
            self._schedule_save()
            if profile.segments:
                variant = "advanced" if advanced else "basic"
                self._toast(f"Added the {variant} pre-programmed functions for {name}")

        pack = pack_for_app(app_id)
        if pack is None or pack.advanced is None:
            add(False)
            return
        self._choose_pack_variant(pack, name, add)

    def _choose_pack_variant(self, pack, name, chosen):
        """Ask for the basic or the advanced pre-programmed functions."""
        dialog = Adw.AlertDialog(
            heading=f"Pre-programmed functions for {name}",
            body=(
                f"Basic: {variant_summary(pack)}, a short menu with the most used ones.\n\n"
                f"Advanced: {variant_summary(pack, True)}, grouped by the stages of the work; "
                "the dial keeps out of the way of the picture or timeline while you turn."
                + (f"\n\n{pack.note}." if pack.note else "")
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("basic", "Basic")
        dialog.add_response("advanced", "Advanced")
        dialog.set_response_appearance("advanced", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("advanced")
        dialog.set_close_response("cancel")

        def respond(_dialog, response):
            if response in ("basic", "advanced"):
                chosen(response == "advanced")

        dialog.connect("response", respond)
        dialog.present(self._window)

    def _export_profiles(self):
        dialog = Gtk.FileDialog(title="Export DialPad profiles", initial_name="dialpad-profiles.json")

        def finish(file_dialog, result):
            try:
                file = file_dialog.save_finish(result)
            except GLib.Error:
                return
            data = {
                "dialpad_profiles": 1,
                "profiles": [_encoder(profile) for profile in self.config.profiles],
            }
            try:
                Path(file.get_path()).write_text(json.dumps(data, indent=2) + "\n")
            except OSError as error:
                self._toast(f"Export failed: {error.strerror}")
                return
            self._toast(f"Exported {len(self.config.profiles)} profiles")

        dialog.save(self._window, None, finish)

    def _import_profiles(self):
        dialog = Gtk.FileDialog(title="Import DialPad profiles")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        json_filter = Gtk.FileFilter()
        json_filter.set_name("JSON")
        json_filter.add_suffix("json")
        filters.append(json_filter)
        dialog.set_filters(filters)

        def finish(file_dialog, result):
            try:
                file = file_dialog.open_finish(result)
                profiles = profiles_from_text(Path(file.get_path()).read_text())
            except (GLib.Error, OSError, ValueError) as error:
                self._toast(f"Import failed: {error}")
                return
            self._confirm_profiles(profiles, lambda: self._add_profiles(profiles))

        dialog.open(self._window, None, finish)

    def _confirm_profiles(
        self, profiles, apply, heading=None, body="", responses=None, spacemouse_buttons=None, known_commands=(),
        spacemouse_layers=None,
    ):
        """Ask before using imported profiles; always when they run commands.

        responses maps response ids to labels and apply takes the chosen id;
        without them there is one "Import" choice and apply takes nothing.
        spacemouse_buttons are imported global SpaceMouse buttons whose commands
        count too; commands in known_commands (already set up here) do not.
        """
        commands = [
            command for command in shell_commands(profiles, spacemouse_buttons, spacemouse_layers)
            if command not in known_commands
        ]
        if not commands and responses is None:
            apply()
            return
        if commands:
            # Shared profiles can carry shell commands; they run as you.
            listing = "\n".join(f"• {profile} › {function}: {command}" for profile, function, command in commands[:12])
            if len(commands) > 12:
                listing += f"\n… and {len(commands) - 12} more"
            body = (body + "\n\n" if body else "") + (
                ("These profiles start" if len(profiles) > 1 else "This profile starts")
                + " programs or shell commands with your rights when you use its functions. "
                "Use it only if you trust where it comes from.\n\n" + listing
            )
        alert = Adw.AlertDialog(heading=heading or "Run these commands?", body=body)
        alert.add_response("cancel", "Cancel")
        for response, label in (responses or {"import": "Import"}).items():
            alert.add_response(response, label)
            if commands:
                alert.set_response_appearance(response, Adw.ResponseAppearance.DESTRUCTIVE)
        if not commands:
            alert.set_response_appearance(next(iter(responses)), Adw.ResponseAppearance.SUGGESTED)
        alert.set_default_response("cancel")
        alert.set_close_response("cancel")

        def chosen(_alert, response):
            if response == "cancel":
                return
            if responses is None:
                apply()
            else:
                apply(response)

        alert.connect("response", chosen)
        alert.present(self._window)

    def _add_profiles(self, profiles):
        """Add profiles; one for an application that has a profile replaces it."""
        replaced = 0
        for profile in profiles:
            existing = next(
                (item for item in self.config.profiles if profile.app_id and item.app_id == profile.app_id),
                None,
            )
            if existing is not None:
                self.config.profiles[self.config.profiles.index(existing)] = profile
                replaced += 1
            else:
                self.config.profiles.append(profile)
        self._changed()
        message = f"Imported {len(profiles)} profiles"
        if replaced:
            message += f" ({replaced} replaced)"
        self._toast(message)

    def _profile_ai_group(self, profile):
        group = Adw.PreferencesGroup(
            title="Build with AI",
            description="Copy the prompt into ChatGPT, Claude or any other assistant and describe what you "
            "want, e.g. “functions for Inkscape with zoom, rotate and layers”. Then copy its whole answer "
            "and paste it here; it is checked before anything changes.",
        )
        clipboard = self._window.get_display().get_clipboard()

        def action_row(title, subtitle, icon, callback):
            row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
            row.add_suffix(Gtk.Image.new_from_icon_name(icon))
            row.connect("activated", lambda _row: callback())
            group.add(row)

        def copy(text, message):
            clipboard.set(text)
            self._toast(message)

        action_row("Copy prompt for AI", "Format description and this profile, ready to paste into a chat",
                   "edit-copy-symbolic",
                   lambda: copy(profile_ai_prompt(profile), "Prompt copied — paste it into your AI assistant"))
        action_row("Copy JSON", "Share this profile or keep a copy", "edit-copy-symbolic",
                   lambda: copy(profile_json(profile), "Profile copied as JSON"))
        action_row("Paste from AI", "Use a profile from the clipboard: the assistant's answer or copied JSON",
                   "edit-paste-symbolic", lambda: clipboard.read_text_async(
                       None, lambda source, result: self._paste_profiles(profile, source, result)))
        return group

    def _paste_profiles(self, current, source, result):
        try:
            text = source.read_text_finish(result) or ""
        except GLib.Error:
            text = ""
        try:
            profiles = profiles_from_text(text)
        except ProfileTextError as error:
            alert = Adw.AlertDialog(heading="Not a usable profile", body=str(error))
            alert.add_response("close", "Close")
            alert.present(self._window)
            return
        if len(profiles) > 1:
            self._confirm_profiles(profiles, lambda: self._add_profiles(profiles))
            return

        (pasted,) = profiles
        owner = next((item for item in self.config.profiles
                      if pasted.app_id and item.app_id == pasted.app_id and item is not current), None)
        functions = ", ".join(segment.title for segment in pasted.segments) or "no functions"
        body = f"“{pasted.name}”: {functions}."
        if owner is not None:
            # One profile per application.
            responses = {"replace": f"Replace “{owner.name}”"}
        else:
            responses = {"replace": f"Replace “{current.name}”", "add": "Add as new profile"}

        def apply(response):
            if response == "add":
                self.config.profiles.append(pasted)
            else:
                target = owner or current
                self.config.profiles[self.config.profiles.index(target)] = pasted
            self._selected_profile = pasted
            self._changed()
            self._toast(f"Using “{pasted.name}” with {len(pasted.segments)} functions")

        self._confirm_profiles([pasted], apply, heading="Use the pasted profile?", body=body, responses=responses)

    # --- Dial Controls --------------------------------------------------------

    def _build_actions_page(self):
        content = self._page_box()

        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        hero.add_css_class("proart-hero")
        copy_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        copy_box.set_valign(Gtk.Align.CENTER)
        copy_box.set_hexpand(True)
        eyebrow = Gtk.Label(label="DIAL CONTROLS", xalign=0)
        eyebrow.add_css_class("proart-eyebrow")
        copy_box.append(eyebrow)
        self._actions_heading = Gtk.Label(xalign=0)
        self._actions_heading.add_css_class("title-1")
        self._actions_heading.set_ellipsize(3)
        copy_box.append(self._actions_heading)
        self._actions_subtitle = Gtk.Label(xalign=0, wrap=True)
        self._actions_subtitle.add_css_class("dim-label")
        copy_box.append(self._actions_subtitle)

        switcher = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        switcher.set_margin_top(8)
        label = Gtk.Label(label="Profile")
        label.add_css_class("caption-heading")
        switcher.append(label)
        self._profile_dropdown = Gtk.DropDown.new(Gtk.StringList.new([]), None)
        self._profile_dropdown_handler = self._profile_dropdown.connect(
            "notify::selected", self._on_profile_dropdown
        )
        switcher.append(self._profile_dropdown)
        copy_box.append(switcher)
        hero.append(copy_box)

        self._dial_preview = Gtk.DrawingArea()
        self._dial_preview.set_content_width(210)
        self._dial_preview.set_content_height(210)
        self._dial_preview.set_draw_func(self._draw_dial_preview)
        self._dial_preview.set_valign(Gtk.Align.CENTER)
        self._dial_preview.set_tooltip_text("Click a function to edit it")
        click = Gtk.GestureClick.new()
        click.connect("pressed", self._on_preview_clicked)
        self._dial_preview.add_controller(click)
        hero.append(self._dial_preview)
        content.append(hero)

        self._actions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.append(self._actions_box)
        return self._scrolled_content(content)

    def _on_profile_dropdown(self, dropdown, _param):
        index = dropdown.get_selected()
        if self.config is None or not 0 <= index < len(self.config.profiles):
            return
        profile = self.config.profiles[index]
        if profile is not self._current_profile():
            GLib.idle_add(lambda: self._select_profile(profile, show=False) and False)

    def _sync_profile_dropdown(self):
        if self._profile_dropdown is None or self.config is None:
            return
        self._profile_dropdown.handler_block(self._profile_dropdown_handler)
        labels = [
            profile.name if profile.app_id is None else f"{profile.name}  ·  app"
            for profile in self.config.profiles
        ]
        self._profile_dropdown.set_model(Gtk.StringList.new(labels))
        profile = self._current_profile()
        if profile in self.config.profiles:
            self._profile_dropdown.set_selected(self.config.profiles.index(profile))
        self._profile_dropdown.handler_unblock(self._profile_dropdown_handler)

    def _rebuild_actions_page(self):
        if self._actions_box is None or self._actions_heading is None:
            return
        _clear_children(self._actions_box)
        self._sync_profile_dropdown()
        profile = self._current_profile()
        if profile is None:
            self._actions_heading.set_label("No profile")
            self._actions_subtitle.set_label("Create a profile on the Apps & Profiles page.")
            return

        self._actions_heading.set_label(profile.name)
        self._actions_subtitle.set_label(
            self._profile_summary(profile)
            + (f" · {profile.app_id}" if profile.app_id else " · default profile")
        )
        self._actions_box.append(self._profile_settings_group(profile))
        self._actions_box.append(self._functions_group(profile))
        self._actions_box.append(contexts_group(self, profile))
        self._actions_box.append(self._profile_ai_group(profile))
        if self._dial_preview is not None:
            self._dial_preview.queue_draw()

    def _profile_settings_group(self, profile):
        group = Adw.PreferencesGroup(
            title="Profile behaviour",
            description="Mode, sensitivity and scroll speed apply only to this profile.",
        )

        mode = _combo_row(
            "Dial mode",
            "Radial menu with several functions, or the dial bound to one function",
            ["Radial menu", "Single function"],
            1 if profile.mode == "single" else 0,
            lambda index: self._set_profile_mode(profile, index),
        )
        group.add(mode)

        # The dial can be bound to a function, not to a group.
        titles = [segment.title for segment in profile.active_segments() if not segment.is_group]
        if profile.mode == "single" and titles:
            selected = profile.single_segment()
            group.add(_combo_row(
                "Bound function",
                "Turning the dial always controls this function",
                titles,
                titles.index(selected.title) if selected else 0,
                lambda index: self._set_single_function(profile, titles[index]),
            ))

        group.add(_spin_row(
            "Sensitivity",
            "Higher values need less rotation per step",
            profile.sensitivity, 0.25, 4.0, 0.05, 2,
            lambda value: self._set_profile_value(profile, "sensitivity", round(value, 2)),
        ))
        group.add(_spin_row(
            "Scroll speed",
            "Multiplies scroll distance for Scroll and Zoom functions",
            profile.scroll_speed, 0.25, 4.0, 0.05, 2,
            lambda value: self._set_profile_value(profile, "scroll_speed", round(value, 2)),
        ))
        if profile.mode != "single":
            group.add(_switch_row(
                "Remember last function",
                "Reopen the last used function instead of the menu",
                profile.remember_last,
                lambda active: self._set_profile_value(profile, "remember_last", active),
            ))

        def set_look(key, value):
            if value is None:
                profile.overlay.pop(key, None)
            else:
                profile.overlay[key] = value
            self._changed(rebuild=False)

        group.add(self._dial_look_expander(
            "Where and how the dial shows for this profile and its menu",
            profile.overlay.get, set_look, "As default",
            lambda key: getattr(self.config.overlay, key),
        ))
        return group

    def _functions_group(self, profile):
        group = Adw.PreferencesGroup(
            title="Functions",
            description=(
                "Shown clockwise from the top of the dial. Click a function to edit "
                "its rotation and tap actions."
            ),
        )
        add = Gtk.Button()
        add.add_css_class("suggested-action")
        add.set_child(Adw.ButtonContent(icon_name="list-add-symbolic", label="Add function"))
        add.set_valign(Gtk.Align.CENTER)
        add.connect("clicked", lambda _button: self._show_function_library(profile))
        group.set_header_suffix(add)

        if not profile.segments:
            empty = Adw.ActionRow(
                title="No functions yet",
                subtitle="Add functions from the library or apply a pre-programmed pack.",
            )
            empty.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
            group.add(empty)

        pack = pack_for_app(profile.app_id)
        if pack is not None and len(profile.segments) < 3:
            row = Adw.ActionRow(
                title=f"Pre-programmed functions for {pack.name}",
                subtitle=", ".join(segment.title for segment in pack.factory()),
            )
            row.add_prefix(Gtk.Image.new_from_icon_name("starred-symbolic"))
            variants = [("Basic", False)] + ([("Advanced", True)] if pack.advanced else [])
            for label, advanced in variants:
                apply = Gtk.Button(label=label)
                apply.set_valign(Gtk.Align.CENTER)
                apply.set_tooltip_text(variant_summary(pack, advanced))
                apply.connect("clicked", lambda _button, advanced=advanced: self._apply_pack(profile, pack, advanced))
                row.add_suffix(apply)
            group.add(row)

        last = len(profile.segments) - 1
        for index, segment in enumerate(profile.segments):
            group.add(self._function_row(profile, segment, index, last))
        return group

    def _function_row(self, profile, segment, index, last, group=None, refresh=None):
        """A function of the menu, or of group (refresh then redraws the
        group's editor after a change)."""
        row = Adw.ActionRow(title=GLib.markup_escape_text(segment.title))
        parts = []
        if segment.is_group:
            titles = [item.title for item in segment.active_items()]
            count = f"Group of {len(titles)} function{'s' if len(titles) != 1 else ''}"
            parts.append(f"{count}: {', '.join(titles)}" if titles else "Empty group: add functions to it")
        ccw, cw, tap = (describe_macro(segment.macro_ccw), describe_macro(segment.macro_cw),
                        describe_macro(segment.macro_center))
        if ccw or cw:
            parts.append(f"↺ {ccw or '—'}   ↻ {cw or '—'}")
        if tap:
            parts.append(f"Tap: {tap}")
        if segment.value_command:
            parts.append(f"shows value{(' in ' + segment.unit) if segment.unit else ''}")
        held = [name for name, actions in segment.with_modifier.items() if not actions.is_empty()]
        if held:
            parts.append(f"more with {' / '.join(held)}")
        row.set_subtitle(GLib.markup_escape_text("  ·  ".join(parts) or "No actions yet"))
        row.add_prefix(_colour_swatch(segment_color(segment, index), 12))
        row.add_prefix(_icon_image(segment_icon(segment)))
        if not segment.enabled:
            row.add_css_class("dim-label")
        row.set_activatable(True)
        row.connect("activated", lambda _row: self._show_function_editor(profile, segment, group, refresh))

        up = _icon_button("go-up-symbolic", "Move earlier on the dial" if group is None else "Move up the list",
                          lambda: self._move_segment(profile, segment, -1, group, refresh))
        up.set_sensitive(index > 0)
        row.add_suffix(up)
        down = _icon_button("go-down-symbolic", "Move later on the dial" if group is None else "Move down the list",
                            lambda: self._move_segment(profile, segment, 1, group, refresh))
        down.set_sensitive(index < last)
        row.add_suffix(down)

        toggle = Gtk.Switch()
        toggle.set_valign(Gtk.Align.CENTER)
        toggle.set_active(segment.enabled)
        toggle.set_tooltip_text("Show this function on the dial" if group is None else "Show this function in the list")
        toggle.connect("notify::active",
                       lambda switch, _param: self._set_segment_enabled(segment, switch.get_active(), refresh))
        row.add_suffix(toggle)
        row.add_suffix(_icon_button("user-trash-symbolic", "Remove function",
                                    lambda: self._remove_segment(profile, segment, group, refresh), "error"))
        return row

    def _set_profile_mode(self, profile, index):
        mode = "single" if index == 1 else "multi"
        if profile.mode != mode:
            profile.mode = mode
            GLib.idle_add(lambda: self._changed() and False)

    def _set_single_function(self, profile, title):
        if profile.single_function != title:
            profile.single_function = title
            self._changed(rebuild=False)

    def _set_profile_value(self, profile, attribute, value):
        if getattr(profile, attribute) != value:
            setattr(profile, attribute, value)
            self._changed(rebuild=False)

    def _set_segment_enabled(self, segment, enabled, refresh=None):
        if segment.enabled != enabled:
            segment.enabled = enabled

            def changed():
                self._changed()
                if refresh is not None:
                    refresh()
                return False

            GLib.idle_add(changed)

    def _move_segment(self, profile, segment, offset, group=None, refresh=None):
        siblings = group.items if group is not None else profile.segments
        index = siblings.index(segment)
        target = index + offset
        if 0 <= target < len(siblings):
            siblings[index], siblings[target] = siblings[target], segment
            self._changed()
            if refresh is not None:
                refresh()

    def _remove_segment(self, profile, segment, group=None, refresh=None):
        siblings = group.items if group is not None else profile.segments
        if segment in siblings:
            siblings.remove(segment)
            if group is None and profile.single_function == segment.title:
                profile.single_function = None
            self._changed()
            if refresh is not None:
                refresh()
            self._toast(f"Removed “{segment.title}”")

    def _apply_pack(self, profile, pack, advanced=False):
        for segment in pack.segments(advanced):
            segment.title = self._unique_title(profile, segment.title)
            profile.segments.append(segment)
        if advanced and pack.advanced_look and not profile.overlay:
            profile.overlay = dict(pack.advanced_look)
        self._changed()
        self._toast(f"Added the {'advanced' if advanced else 'basic'} {pack.name} functions")

    def _preview_segments(self, profile):
        if profile is None:
            return []
        if profile.mode == "single" and profile.single_segment() is not None:
            segment = profile.single_segment()
            return [segment] if segment else []
        # As on the dial: a group without functions to open is left out.
        return [segment for segment in profile.active_segments() if not segment.is_group or segment.active_items()]

    def _watch_system_style(self):
        self._color_scheme = ColorScheme()
        self._color_scheme.on_changed(self._on_skins_changed)

    def _system_is_light(self):
        return self._color_scheme is not None and not self._color_scheme.prefers_dark()

    def _skin_attribute(self):
        """The overlay setting the dial uses right now: skin or skin_light."""
        overlay = self.config.overlay
        return "skin_light" if overlay.follow_system_style and self._system_is_light() else "skin"

    def _skin(self, skin_id=None):
        return resolve_skin(self._skins, skin_id or getattr(self.config.overlay, self._skin_attribute()))

    def _preview_view(self, profile):
        segments = self._preview_segments(profile)
        if profile is None:
            return None
        titles = [segment.title for segment in segments]
        colors = [segment_color(segment, index) for index, segment in enumerate(segments)]
        icons = [segment_icon(segment) if segment.is_group else segment.icon_url or "" for segment in segments]
        accent = self.config.overlay.accent or DEFAULT_ACCENT
        if profile.mode == "single" and segments and not segments[0].is_group:
            segment = segments[0]
            return DialView(
                self._skin(), titles, colors, accent, icons, title=segment.title, selected=True,
                value=50 if segment.unit == "%" else None, unit=segment.unit or "", profile=profile.name,
            )
        return DialView(
            self._skin(), titles, colors, accent, icons,
            title=self._preview_title or "", profile=profile.name,
        )

    @staticmethod
    def _preview_square(width, height):
        size = min(width, height)
        return (width - size) / 2, (height - size) / 2, size

    def _draw_dial_preview(self, _area, cr, width, height):
        view = self._preview_view(self._current_profile())
        if view is not None:
            paint_dial(cr, view, *self._preview_square(width, height), draw_icon=draw_icon)

    def _on_preview_clicked(self, _gesture, _count, x, y):
        profile = self._current_profile()
        segments = self._preview_segments(profile)
        view = self._preview_view(profile)
        if view is None or not segments:
            return
        left, top, size = self._preview_square(self._dial_preview.get_width(), self._dial_preview.get_height())
        if profile.mode == "single":
            if math.hypot(x - left - size / 2, y - top - size / 2) <= size / 2:
                self._show_function_editor(profile, segments[0])
            return
        index = hit_function(view, x - left, y - top, size)
        if index is not None:
            self._preview_title = segments[index].title
            self._dial_preview.queue_draw()
            self._show_function_editor(profile, segments[index])

    def _draw_group_preview(self, profile, group, cr, width, height):
        """The dial with the group's list open beside it, as on screen."""
        segments = [
            segment for segment in profile.active_segments()
            if segment is group or not segment.is_group or segment.active_items()
        ]
        items = group.active_items()
        submenu = {
            "title": group.title,
            "items": [item.title for item in items],
            "icons": [segment_icon(item) for item in items],
            "colors": [segment_color(item, index) for index, item in enumerate(items)],
            "selected": 0,
        } if items else None
        view = DialView(
            self._skin(),
            [segment.title for segment in segments],
            [segment_color(segment, index) for index, segment in enumerate(segments)],
            self.config.overlay.accent or DEFAULT_ACCENT,
            [segment_icon(segment) if segment.is_group else segment.icon_url or "" for segment in segments],
            title=items[0].title if items else group.title,
            submenu=submenu,
        )
        size = height
        scale = size / PAINTED_D
        opened = open_submenu(view)
        if opened is None:
            paint_dial(cr, view, (width - size) / 2, 0, size, draw_icon=draw_icon)
            return
        list_width, list_height = submenu_size(len(items))
        # The list starts at the edge of the dial's box, as in dial.js.
        reach = (DIAL_R - LIST_MARGIN + list_width) * scale
        total = size / 2 + reach
        left = (width - total) / 2
        list_y = (height - list_height * scale) / 2
        if opened.prefer_right:
            paint_dial(cr, view, left, 0, size, draw_icon=draw_icon)
            paint_submenu(cr, view, left + size / 2 + (DIAL_R - LIST_MARGIN) * scale, list_y, scale, "right",
                          draw_icon=draw_icon)
        else:
            paint_submenu(cr, view, left, list_y, scale, "left", draw_icon=draw_icon)
            paint_dial(cr, view, left + total - size, 0, size, draw_icon=draw_icon)

    # --- function library -------------------------------------------------------

    def _show_function_library(self, profile, group=None, on_added=None):
        """Functions to add to the menu, or to group's list (one level: no
        groups in a group)."""
        title = "Add a function" if group is None else f"Add to “{group.title}”"
        dialog = Adw.Dialog(title=title, content_width=560, content_height=680)
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        view.add_top_bar(header)
        search = Gtk.SearchEntry()
        search.set_placeholder_text("Search functions")
        search.set_margin_start(12)
        search.set_margin_end(12)
        search.set_margin_bottom(6)
        view.add_top_bar(search)

        page = Adw.PreferencesPage()
        view.set_content(page)
        dialog.set_child(view)
        groups = []

        def add(segment):
            segment.title = self._unique_title(profile, segment.title, group=group)
            if group is None:
                profile.segments.append(segment)
                self._preview_title = segment.title
            else:
                group.items.append(segment)
            dialog.close()
            self._changed()
            if on_added is not None:
                on_added()
            self._toast(f"Added “{segment.title}”")
            if segment.is_group:
                # A new group is empty: open it to add its functions.
                GLib.idle_add(lambda: self._show_function_editor(profile, segment) and False)

        def move_here(segment):
            profile.segments.remove(segment)
            if profile.single_function == segment.title:
                profile.single_function = None
            segment.title = self._unique_title(profile, segment.title, group=group)
            group.items.append(segment)
            dialog.close()
            self._changed()
            if on_added is not None:
                on_added()
            self._toast(f"Moved “{segment.title}” into “{group.title}”")

        def rebuild():
            for section in groups:
                page.remove(section)
            groups.clear()
            query = search.get_text().strip().lower()

            pack = pack_for_app(profile.app_id)
            movable = [segment for segment in profile.segments if not segment.is_group] if group is not None else []
            if movable and not query:
                section = Adw.PreferencesGroup(
                    title="From the dial",
                    description="Moves a function of the radial menu into this group.",
                )
                for segment in movable:
                    row = Adw.ActionRow(title=GLib.markup_escape_text(segment.title))
                    row.add_prefix(_icon_image(segment_icon(segment)))
                    row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
                    row.set_activatable(True)
                    row.connect("activated", lambda _row, segment=segment: move_here(segment))
                    section.add(row)
                page.add(section)
                groups.append(section)

            if pack is not None and not query and group is None:
                section = Adw.PreferencesGroup(title="Pre-programmed")
                variants = [(False, f"Basic {pack.name} functions")]
                if pack.advanced:
                    variants.append((True, f"Advanced {pack.name} functions"))
                for advanced, title in variants:
                    row = Adw.ActionRow(
                        title=title,
                        subtitle=f"{variant_summary(pack, advanced)}: "
                                 + ", ".join(segment.title for segment in pack.segments(advanced)),
                    )
                    row.add_prefix(Gtk.Image.new_from_icon_name("starred-symbolic"))
                    row.set_activatable(True)
                    row.connect("activated", lambda _row, advanced=advanced: (
                        dialog.close(), self._apply_pack(profile, pack, advanced),
                        on_added() if on_added is not None else None))
                    section.add(row)
                page.add(section)
                groups.append(section)

            extras = []
            if profile.app_id == DARKTABLE_APP_ID:
                extras.append(("Darktable", "Colour labels", "bookmark-new-symbolic", "F1 / F2 / F3 colour labels", darktable_colour_labels_segment))
            entries = [
                (preset.category, preset.title, preset.icon_name, preset.description, preset.factory)
                for preset in FUNCTION_LIBRARY
                # One level: no group inside a group.
                if group is None or not preset.factory().is_group
            ] + extras
            categories = list(LIBRARY_CATEGORIES) + (["Darktable"] if extras else [])
            for category in categories:
                rows = [
                    entry for entry in entries
                    if entry[0] == category and (
                        not query or query in entry[1].lower() or query in entry[3].lower()
                    )
                ]
                if not rows:
                    continue
                section = Adw.PreferencesGroup(title=category)
                for _category, title, icon_name, description, factory in rows:
                    row = Adw.ActionRow(title=title, subtitle=description)
                    row.add_prefix(Gtk.Image.new_from_icon_name(icon_name))
                    row.add_suffix(Gtk.Image.new_from_icon_name("list-add-symbolic"))
                    row.set_activatable(True)
                    row.connect("activated", lambda _row, factory=factory: add(factory()))
                    section.add(row)
                page.add(section)
                groups.append(section)

            section = Adw.PreferencesGroup(title="Custom")
            row = Adw.ActionRow(title="Blank function", subtitle="Assign your own shortcuts, scroll or commands")
            row.add_prefix(Gtk.Image.new_from_icon_name("document-new-symbolic"))
            row.set_activatable(True)
            row.connect("activated", lambda _row: add(Segment(title="Custom")))
            section.add(row)
            if group is None:
                row = Adw.ActionRow(
                    title="Group",
                    subtitle="A folder on the dial: choosing it opens a list of more functions beside the dial",
                )
                row.add_prefix(Gtk.Image.new_from_icon_name(GROUP_ICON))
                row.set_activatable(True)
                row.connect("activated", lambda _row: add(Segment(title="Group", items=[])))
                section.add(row)
            page.add(section)
            groups.append(section)

        search.connect("search-changed", lambda _entry: rebuild())
        rebuild()
        dialog.present(self._window)

    # --- function editor ----------------------------------------------------------

    def _show_function_editor(self, profile, segment, group=None, on_close=None):
        """Edit a function, a group (its functions instead of actions) or,
        with group, a function in that group's list."""
        dialog = Adw.Dialog(title=segment.title, content_width=600, content_height=720)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        view.set_content(page)
        dialog.set_child(view)
        state = {"groups": [], "item_rows": []}
        siblings = group.items if group is not None else profile.segments

        def changed(rebuild_lists=False):
            self._schedule_save()
            if rebuild_lists:
                build_triggers()
            if segment.is_group:
                group_preview.queue_draw()

        general = Adw.PreferencesGroup(title="Group" if segment.is_group else "Function")
        name = Adw.EntryRow(title="Name")
        name.set_text(segment.title)

        def rename(entry):
            text = entry.get_text().strip()
            if not text or text == segment.title:
                return
            unique = self._unique_title(profile, text, ignore=segment, group=group)
            if group is None and profile.single_function == segment.title:
                profile.single_function = unique
            segment.title = unique
            dialog.set_title(unique)
            show_icon()
            changed()

        name.connect("changed", rename)
        general.add(name)

        colour_row = Adw.ActionRow(title="Colour", subtitle="Identifies the function on the dial")
        colour_button = Gtk.ColorDialogButton.new(Gtk.ColorDialog(with_alpha=False))
        colour_button.set_valign(Gtk.Align.CENTER)
        rgba = Gdk.RGBA()
        position = siblings.index(segment) if segment in siblings else 0
        rgba.parse(segment_color(segment, position))
        colour_button.set_rgba(rgba)

        def colour_changed(button, _param):
            segment.color = _rgba_to_hex(button.get_rgba())
            changed()

        colour_button.connect("notify::rgba", colour_changed)
        colour_row.add_suffix(colour_button)
        general.add(colour_row)

        icon_row = Adw.ActionRow(title="Icon")
        icon_preview = Gtk.Image(pixel_size=20)
        icon_row.add_prefix(icon_preview)
        icon_choices = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=8,
                                   min_children_per_line=8, homogeneous=True)
        icon_popover = Gtk.Popover()
        icon_menu = Gtk.MenuButton(label="Choose…", popover=icon_popover, valign=Gtk.Align.CENTER)
        icon_row.add_suffix(icon_menu)
        icon_row.set_activatable_widget(icon_menu)

        def show_icon():
            icon = segment_icon(segment)
            _set_icon_image(icon_preview, icon)
            automatic = "Automatic: a list" if segment.is_group else "Automatic: matches the function name"
            icon_row.set_subtitle(
                GLib.markup_escape_text(Path(icon).name if icon.startswith("/") else "Your choice")
                if segment.icon_url else automatic
            )

        def choose_icon(icon):
            segment.icon_url = icon
            icon_popover.popdown()
            show_icon()
            changed()

        def choose_file(_button):
            icon_popover.popdown()
            images = Gtk.FileFilter(name="Icons (SVG, PNG)")
            images.add_suffix("svg")
            images.add_suffix("png")
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(images)
            chooser = Gtk.FileDialog(title="Choose an icon", filters=filters)

            def opened(source, result):
                try:
                    file = source.open_finish(result)
                except GLib.Error:
                    return
                if file is not None and file.get_path():
                    choose_icon(file.get_path())

            chooser.open(self._window, None, opened)

        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        for icon_name in ICON_CHOICES:
            icon_name = f"{icon_name}-symbolic"
            if not theme.has_icon(icon_name):
                continue
            button = Gtk.Button(icon_name=icon_name, tooltip_text=icon_name.removesuffix("-symbolic"))
            button.add_css_class("flat")
            button.connect("clicked", lambda _button, name=icon_name: choose_icon(name))
            icon_choices.append(button)
        icon_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        automatic = Gtk.Button(label="Automatic (from the name)")
        automatic.connect("clicked", lambda _button: choose_icon(None))
        from_file = Gtk.Button(label="Picture file…")
        from_file.connect("clicked", choose_file)
        icon_box.append(automatic)
        icon_box.append(icon_choices)
        icon_box.append(from_file)
        icon_popover.set_child(icon_box)
        show_icon()
        general.add(icon_row)

        if not segment.is_group:
            general.add(_spin_row(
                "Rotation step",
                f"Degrees per action; 0 uses the global default ({self.config.gesture.default_threshold}°)",
                segment.treshold or 0, 0, 360, 5, 0,
                lambda value: (setattr(segment, "treshold", int(value) or None), changed()),
            ))
        general.add(_switch_row(
            "Show on the dial" if group is None else "Show in the list", None, segment.enabled,
            lambda active: (setattr(segment, "enabled", active), changed()),
        ))

        def set_look(key, value):
            if value is None:
                segment.overlay.pop(key, None)
            else:
                segment.overlay[key] = value
            changed()

        def inherited(key):
            value = profile.overlay.get(key, getattr(self.config.overlay, key))
            return group.overlay.get(key, value) if group is not None else value

        general.add(self._dial_look_expander(
            "Where and how the dial shows while this group's list is open or one of its functions is selected"
            if segment.is_group else "Where and how the dial shows while this function is selected",
            segment.overlay.get, set_look, "As group" if group is not None else "As profile", inherited,
        ))

        if segment.is_group:
            # The dial as it shows with this group's list open.
            group_preview = Gtk.DrawingArea(content_height=230)
            group_preview.set_margin_bottom(6)
            group_preview.set_draw_func(lambda _area, cr, width, height:
                                        self._draw_group_preview(profile, segment, cr, width, height))
            preview_group = Adw.PreferencesGroup()
            preview_group.add(group_preview)
            page.add(preview_group)

            items = Adw.PreferencesGroup(
                title="Functions in the group",
                description="Choosing the group on the dial opens them as a list beside it: turn to move the "
                "highlight, tap the centre to choose, hold it to go back.",
            )
            add = Gtk.Button()
            add.add_css_class("suggested-action")
            add.set_child(Adw.ButtonContent(icon_name="list-add-symbolic", label="Add function"))
            add.set_valign(Gtk.Align.CENTER)
            add.connect("clicked", lambda _button: self._show_function_library(profile, segment, build_items))
            items.set_header_suffix(add)

            def build_items():
                for row in state["item_rows"]:
                    items.remove(row)
                rows = [
                    self._function_row(profile, item, index, len(segment.items) - 1, segment, build_items)
                    for index, item in enumerate(segment.items)
                ]
                if not rows:
                    empty = Adw.ActionRow(
                        title="No functions yet",
                        subtitle="Add functions from the library or move them here from the dial.",
                    )
                    empty.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
                    rows.append(empty)
                for row in rows:
                    items.add(row)
                state["item_rows"] = rows
                group_preview.queue_draw()

            build_items()
            # The functions first: they are what a group is for.
            page.add(items)
            page.add(general)
            dialog.connect("closed", lambda _dialog: self._changed())
            dialog.present(self._window)
            return

        page.add(general)

        value_group = Adw.PreferencesGroup(
            title="Live value",
            description="Optional shell command whose output is shown in the dial centre, e.g. the volume level.",
        )
        value_row = Adw.EntryRow(title="Value command")
        value_row.set_text(segment.value_command or "")
        value_row.connect("changed", lambda entry: (setattr(segment, "value_command", entry.get_text().strip() or None), changed()))
        value_group.add(value_row)
        unit_row = Adw.EntryRow(title="Unit (use % for a level gauge)")
        unit_row.set_text(segment.unit or "")
        unit_row.connect("changed", lambda entry: (setattr(segment, "unit", entry.get_text().strip() or None), changed()))
        value_group.add(unit_row)
        page.add(value_group)

        # The trigger groups below show the plain actions or those while a
        # keyboard key is held (Segment.with_modifier).
        held_keys = [None, *MODIFIER_KEYS]
        held_group = Adw.PreferencesGroup(
            title="Keyboard keys",
            description="Holding Ctrl, Shift or Alt on the keyboard while turning or tapping can run other "
            "actions. The key stays held, so the application sees it too.",
        )
        held_row = _combo_row(
            "Actions for", None, ["No key held", *(f"{name} held" for name in MODIFIER_KEYS)], 0,
            lambda index: (state.update(held=held_keys[index]), build_triggers()),
        )
        held_group.add(held_row)
        page.add(held_group)

        def held_summary():
            names = [name for name, actions in segment.with_modifier.items() if not actions.is_empty()]
            held_row.set_subtitle(f"Own actions with {' and '.join(names)}" if names else "Only actions without a key")

        def build_triggers():
            for group in state["groups"]:
                page.remove(group)
            state["groups"] = []
            held_summary()
            held = state.get("held")
            holder = segment if held is None else segment.with_modifier.setdefault(held, ModifierActions())
            title = segment.title if held is None else f"{segment.title} with {held}"
            # The driver runs a function with nothing to turn as soon as it
            # is chosen in the menu (select_multifunction_segment).
            if held is None and not segment.turns():
                kind = Adw.PreferencesGroup()
                row = Adw.ActionRow(
                    title="Launcher",
                    subtitle="Nothing to turn: choosing this function in the radial menu runs its tap action "
                    "at once and the dial stays in the menu. Add a turning action to make it a dial function.",
                )
                row.add_prefix(Gtk.Image.new_from_icon_name("system-run-symbolic"))
                kind.add(row)
                page.add(kind)
                state["groups"].append(kind)
            for field, label, icon_name, description in TRIGGERS:
                if held is not None:
                    label = f"{label} with {held}"
                    description = f"Instead of the actions above while {held} is held"
                group = Adw.PreferencesGroup(title=label, description=description)
                add = _icon_button(
                    "list-add-symbolic", "Add action",
                    lambda field=field: self._show_step_dialog(
                        holder, field, None, lambda: changed(True), title
                    ),
                )
                record = _icon_button(
                    "media-record-symbolic", "Record the keys you press as actions",
                    lambda holder=holder, field=field: self._record_macro(holder, field, lambda: changed(True)),
                )
                tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
                tools.append(record)
                tools.append(add)
                group.set_header_suffix(tools)
                steps = getattr(holder, field)
                if not steps:
                    empty = Adw.ActionRow(
                        title="No action",
                        subtitle="Press + to add one" if held is None
                        else "Press + to add one; without one the usual action runs",
                    )
                    empty.add_css_class("dim-label")
                    group.add(empty)
                for index, step in enumerate(steps):
                    group.add(self._step_row(holder, field, index, step, lambda: changed(True), title))
                page.add(group)
                state["groups"].append(group)

        build_triggers()

        def closed(_dialog):
            self._changed()
            if on_close is not None:
                on_close()

        dialog.connect("closed", closed)
        dialog.present(self._window)

    def _record_macro(self, holder, field, changed):
        """Record keys from the keyboard into holder's macro field (append or replace)."""
        steps = getattr(holder, field)

        def done(recorded, replace):
            if replace:
                steps[:] = recorded
            else:
                steps.extend(recorded)
            changed()

        MacroRecorderDialog(self._window, len(steps), done).present()

    def _step_row(self, segment, field, index, step, changed, title=None):
        steps = getattr(segment, field)
        row = Adw.ActionRow(title=GLib.markup_escape_text(describe_step(step)))
        row.set_subtitle(GLib.markup_escape_text(self._step_hint(step)))
        row.add_prefix(Gtk.Image.new_from_icon_name(self._icon_for_step(step)))
        row.set_activatable(True)
        row.set_tooltip_text("Click to edit")
        row.connect("activated", lambda _row: self._show_step_dialog(segment, field, index, changed, title))

        def move(offset):
            target = index + offset
            if 0 <= target < len(steps):
                steps[index], steps[target] = steps[target], steps[index]
                changed()

        def remove():
            if 0 <= index < len(steps):
                del steps[index]
                changed()

        if len(steps) > 1:
            up = _icon_button("go-up-symbolic", "Run earlier", lambda: move(-1))
            up.set_sensitive(index > 0)
            row.add_suffix(up)
            down = _icon_button("go-down-symbolic", "Run later", lambda: move(1))
            down.set_sensitive(index < len(steps) - 1)
            row.add_suffix(down)
        row.add_suffix(_icon_button("user-trash-symbolic", "Remove action", remove, "error"))
        return row

    @staticmethod
    def _icon_for_step(step):
        icons = {
            StepType.KEYSTROKE: "input-keyboard-symbolic",
            StepType.SCROLL: "view-continuous-symbolic",
            StepType.MEDIA: "media-playback-start-symbolic",
            StepType.BRIGHTNESS: "display-brightness-symbolic",
            StepType.VOLUME: "audio-volume-high-symbolic",
            StepType.COMMAND: "utilities-terminal-symbolic",
            StepType.SHELL: "utilities-terminal-symbolic",
            StepType.DELAY: "alarm-symbolic",
        }
        return icons.get(step.type, "emblem-system-symbolic")

    @staticmethod
    def _step_hint(step):
        if step.type in (StepType.KEYSTROKE, StepType.MEDIA):
            return f"Shortcut: {format_keys(step.keys)}"
        if step.type is StepType.SCROLL:
            axis = "Horizontal" if step.axis == "horizontal" else "Vertical"
            held = f" with {format_keys(step.modifiers)} held" if step.modifiers else ""
            return f"{axis} scroll, {step.value or 120} units{held}"
        if step.type is StepType.VOLUME:
            return "System audio level"
        if step.type is StepType.BRIGHTNESS:
            return "Display brightness"
        if step.type is StepType.COMMAND:
            return " ".join([step.program or "", *(step.args or [])]).strip() or "Choose a program"
        if step.type is StepType.SHELL:
            return step.script or "Enter a command"
        if step.type is StepType.DELAY:
            return "Pause between steps"
        return step.type.value

    @staticmethod
    def _event_keys(keyval, keycode, state):
        """Translate a captured key press into the evdev names the driver uses.

        Returns [] for a modifier on its own (the rest of the shortcut is
        still to come) and None for a key the driver cannot send. The
        hardware keycode (evdev code + 8) names the physical key, which is
        what the driver presses; the key symbol is only the fallback.
        """
        modifiers = []
        if state & Gdk.ModifierType.CONTROL_MASK:
            modifiers.append("KEY_LEFTCTRL")
        if state & Gdk.ModifierType.SHIFT_MASK:
            modifiers.append("KEY_LEFTSHIFT")
        if state & Gdk.ModifierType.ALT_MASK:
            modifiers.append("KEY_LEFTALT")
        if state & Gdk.ModifierType.SUPER_MASK:
            modifiers.append("KEY_LEFTMETA")

        key = EVDEV_CODE_NAMES.get(keycode - 8)
        if key in MODIFIER_KEYS:
            return []
        if key and key != "KEY_RESERVED":
            return [*modifiers, key]

        name = Gdk.keyval_name(Gdk.keyval_to_lower(keyval)) or ""
        special = {
            "Return": "KEY_ENTER",
            "KP_Enter": "KEY_ENTER",
            "Escape": "KEY_ESC",
            "BackSpace": "KEY_BACKSPACE",
            "Tab": "KEY_TAB",
            "ISO_Left_Tab": "KEY_TAB",
            "space": "KEY_SPACE",
            "Delete": "KEY_DELETE",
            "Insert": "KEY_INSERT",
            "Home": "KEY_HOME",
            "End": "KEY_END",
            "Page_Up": "KEY_PAGEUP",
            "Page_Down": "KEY_PAGEDOWN",
            "Left": "KEY_LEFT",
            "Right": "KEY_RIGHT",
            "Up": "KEY_UP",
            "Down": "KEY_DOWN",
            "bracketleft": "KEY_LEFTBRACE",
            "bracketright": "KEY_RIGHTBRACE",
            "braceleft": "KEY_LEFTBRACE",
            "braceright": "KEY_RIGHTBRACE",
            "minus": "KEY_MINUS",
            "underscore": "KEY_MINUS",
            "equal": "KEY_EQUAL",
            "plus": "KEY_EQUAL",
            "comma": "KEY_COMMA",
            "less": "KEY_COMMA",
            "period": "KEY_DOT",
            "greater": "KEY_DOT",
            "slash": "KEY_SLASH",
            "semicolon": "KEY_SEMICOLON",
            "apostrophe": "KEY_APOSTROPHE",
            "grave": "KEY_GRAVE",
            "backslash": "KEY_BACKSLASH",
            "KP_Add": "KEY_KPPLUS",
            "KP_Subtract": "KEY_KPMINUS",
            "XF86AudioMute": "KEY_MUTE",
            "XF86AudioPlay": "KEY_PLAYPAUSE",
            "XF86AudioNext": "KEY_NEXTSONG",
            "XF86AudioPrev": "KEY_PREVIOUSSONG",
            "XF86AudioRaiseVolume": "KEY_VOLUMEUP",
            "XF86AudioLowerVolume": "KEY_VOLUMEDOWN",
        }
        if len(name) == 1 and name.isalpha():
            key = f"KEY_{name.upper()}"
        elif len(name) == 1 and name.isdigit():
            key = f"KEY_{name}"
        elif name.startswith("F") and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
            key = f"KEY_{name}"
        else:
            key = special.get(name)
        if not key:
            return None
        return [*modifiers, key]

    @staticmethod
    def _is_laptop_hotkey(key):
        """Volume, brightness and similar keys that laptops put on the F keys."""
        code = next((code for code, name in EVDEV_CODE_NAMES.items() if name == key), 0)
        return code >= 113 and not 183 <= code <= 194 and key not in MODIFIER_KEYS

    @staticmethod
    def _boxed_list(row):
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        rows.add_css_class("boxed-list")
        rows.append(row)
        return rows

    @staticmethod
    def _shortcut_help_button():
        text = (
            "<b>One action presses one shortcut.</b> All its keys go down together "
            "(Ctrl, Shift, Alt and Super first) and are released again.\n\n"
            "<b>Three ways to set it</b>\n"
            "• <b>Record:</b> click the field and press the whole shortcut, "
            "e.g. hold Ctrl and press F12.\n"
            "• <b>Click:</b> toggle keys below. Every highlighted key is part of the shortcut; "
            "F1–F12 and more are under “More keys”.\n"
            "• <b>Type:</b> key names joined with +, e.g. <tt>F12</tt>, <tt>Ctrl+F12</tt>, "
            "<tt>Ctrl+Shift+Z</tt>, <tt>Alt+Left</tt>.\n\n"
            "<b>Several shortcuts in a row</b> (e.g. Ctrl+C, then Ctrl+V): save this action, "
            "then press + next to the same trigger to add the next one. They run from top to bottom; "
            "put a Wait action between them if the application needs time.\n\n"
            "<b>Laptop F keys</b> often send volume or brightness instead. If recording F12 gives "
            "something else, hold Fn while pressing it, click F12 under “More keys” or type F12."
        )
        label = Gtk.Label(label=text, use_markup=True, wrap=True, xalign=0, max_width_chars=48)
        for side in ("top", "bottom", "start", "end"):
            getattr(label, f"set_margin_{side}")(12)
        button = Gtk.MenuButton(icon_name="help-about-symbolic", popover=Gtk.Popover(child=label))
        button.set_tooltip_text("How shortcuts work")
        button.set_valign(Gtk.Align.CENTER)
        button.add_css_class("flat")
        return button

    def _show_step_dialog(self, segment, field, index, changed, title=None):
        """Edit a step of segment's field (or of a ModifierActions; title names it)."""
        steps = getattr(segment, field)
        original = steps[index] if index is not None and 0 <= index < len(steps) else None
        step = copy.deepcopy(original) if original else MacroStep(type=StepType.KEYSTROKE, keys=[])
        trigger = {"macro_cw": "turning clockwise", "macro_ccw": "turning counter-clockwise",
                   "macro_center": "tapping"}[field]

        dialog = Adw.Dialog(title="Edit action" if original else "New action", content_width=560, content_height=640)
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _button: dialog.close())
        header.pack_start(cancel)
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        header.pack_end(save)
        view.add_top_bar(header)
        page = Adw.PreferencesPage()
        view.set_content(page)
        dialog.set_child(view)

        basics = Adw.PreferencesGroup(
            title=f"{title or segment.title} — {trigger}",
            description="The label appears as a hint below the dial.",
        )
        shown_type = step.type
        # Older volume/brightness steps press a key combination (for example
        # Shift + Volume up for fine steps); edit them as shortcuts so the
        # combination survives saving.
        if step.type in (StepType.VOLUME, StepType.BRIGHTNESS) and step.keys and step.direction is None:
            shown_type = StepType.KEYSTROKE
        selected_type = next(
            (position for position, (_label, kind) in enumerate(ACTION_TYPES) if kind is shown_type), 0
        )
        type_row = Adw.ComboRow(title="Action", model=Gtk.StringList.new([label for label, _kind in ACTION_TYPES]))
        type_row.set_selected(selected_type)
        basics.add(type_row)
        label_row = Adw.EntryRow(title="Label (optional)")
        label_row.set_text(step.title or "")
        basics.add(label_row)
        page.add(basics)

        # Keyboard shortcut ---------------------------------------------------------
        key_state = {"keys": list(step.keys or []), "listening": False, "note": None}
        shortcut_group = Adw.PreferencesGroup(
            title="Shortcut",
            description="Keys pressed together. Record them, click them below or type them, e.g. Ctrl+F12.",
        )
        shortcut_group.set_header_suffix(self._shortcut_help_button())
        # One box keeps the order; a group puts its rows before other widgets.
        shortcut_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        shortcut_group.add(shortcut_box)
        capture = Gtk.Button()
        capture.add_css_class("proart-key-capture")
        capture.set_tooltip_text("Click here, then press the whole shortcut, e.g. hold Ctrl and press F12")
        shortcut_box.append(capture)
        capture_hint = Gtk.Label(xalign=0, wrap=True)
        capture_hint.add_css_class("caption")
        capture_hint.add_css_class("dim-label")
        capture_hint.set_margin_top(6)
        capture_hint.set_margin_bottom(6)
        shortcut_box.append(capture_hint)
        key_names = Adw.EntryRow(title="Or type keys joined by +, e.g. F12, Ctrl+F12, Alt+Left")
        shortcut_box.append(self._boxed_list(key_names))
        key_buttons = []
        media_buttons = []
        syncing = {"active": False}

        def refresh_shortcut(update_entry=True):
            syncing["active"] = True
            keys = key_state["keys"]
            if key_state["listening"]:
                capture.set_label("Press the keys now…")
                capture.add_css_class("listening")
            else:
                capture.set_label(format_keys(keys) if keys else "Click to record a shortcut")
                capture.remove_css_class("listening")
            if key_state["note"]:
                capture_hint.set_text(key_state["note"])
            elif key_state["listening"]:
                capture_hint.set_text("Recording: press the whole shortcut, e.g. hold Ctrl and press F12. "
                                      "Desktop shortcuts are paused meanwhile; click elsewhere to stop.")
            else:
                capture_hint.set_text("Click the field above to record a shortcut from your keyboard.")
            for code, button in key_buttons:
                button.set_active(code in keys)
            for codes, button in media_buttons:
                button.set_active(keys == codes)
            media_group.set_description(f"Chosen: {format_keys(keys)}" if keys else "Choose one key.")
            if update_entry:
                key_names.set_text(keys_to_text(keys))
                key_names.remove_css_class("error")
            syncing["active"] = False

        def set_listening(listening):
            key_state["listening"] = listening
            if listening:
                key_state["note"] = None
            # Desktop shortcuts (a drop-down terminal on F12, screenshots)
            # would take the keys first; GNOME asks once for permission.
            native = capture.get_native()
            surface = native.get_surface() if native else None
            if isinstance(surface, Gdk.Toplevel):
                if listening:
                    surface.inhibit_system_shortcuts(None)
                else:
                    surface.restore_system_shortcuts()
            refresh_shortcut(update_entry=False)

        controller = Gtk.EventControllerKey.new()

        def capture_key(_controller, keyval, keycode, state):
            captured = self._event_keys(keyval, keycode, state)
            if captured == []:
                return True
            if captured is None:
                key_state["note"] = "This key can't be recorded. Type its name in the field below, e.g. F12."
            else:
                key_state["keys"] = captured
                key_state["note"] = (
                    "Recorded a special key. If you meant an F key, hold Fn while pressing it, "
                    "or type it below, e.g. F12."
                    if self._is_laptop_hotkey(captured[-1])
                    else "Recorded. Press other keys to replace it, or click elsewhere."
                )
            key_state["listening"] = False
            refresh_shortcut()
            return True

        controller.connect("key-pressed", capture_key)
        capture.add_controller(controller)
        focus = Gtk.EventControllerFocus.new()
        focus.connect("enter", lambda _controller: set_listening(True))
        focus.connect("leave", lambda _controller: set_listening(False))
        capture.add_controller(focus)
        capture.connect("clicked", lambda _button: set_listening(True))
        capture.connect("unmap", lambda _button: set_listening(False))

        def names_changed(entry):
            if syncing["active"]:
                return
            keys, unknown = parse_keys(entry.get_text())
            key_state["keys"] = keys
            if unknown:
                entry.add_css_class("error")
                key_state["note"] = (
                    f"Unknown key: {', '.join(unknown)}. Use names such as Ctrl, Shift, Alt, Super, "
                    "A–Z, 0–9, F1–F24, Left, Up, Home, End, PgUp, PgDn, Del, Esc, Enter, Space, Tab."
                )
            else:
                entry.remove_css_class("error")
                key_state["note"] = None
            refresh_shortcut(update_entry=False)

        key_names.connect("changed", names_changed)

        def key_board(choices):
            board = Gtk.FlowBox()
            board.set_selection_mode(Gtk.SelectionMode.NONE)
            board.set_max_children_per_line(10)
            board.set_homogeneous(True)
            board.set_margin_top(8)
            for label, code in choices:
                button = Gtk.ToggleButton(label=label)
                button.add_css_class("proart-key-choice")
                button.set_tooltip_text(f"Part of the shortcut while highlighted ({keys_to_text([code])})")
                key_buttons.append((code, button))

                def toggle_key(widget, key=code):
                    if syncing["active"]:
                        return
                    keys = [entry for entry in key_state["keys"] if entry != key]
                    if widget.get_active():
                        keys.append(key)
                    key_state["keys"] = order_keys(keys)
                    key_state["note"] = None
                    refresh_shortcut()

                button.connect("toggled", toggle_key)
                board.append(button)
            return board

        shortcut_box.append(key_board((
            ("Ctrl", "KEY_LEFTCTRL"), ("Shift", "KEY_LEFTSHIFT"), ("Alt", "KEY_LEFTALT"),
            ("Super", "KEY_LEFTMETA"), ("[", "KEY_LEFTBRACE"), ("]", "KEY_RIGHTBRACE"),
            ("=", "KEY_EQUAL"), ("-", "KEY_MINUS"), ("Z", "KEY_Z"), ("Y", "KEY_Y"),
            ("←", "KEY_LEFT"), ("→", "KEY_RIGHT"), ("↑", "KEY_UP"), ("↓", "KEY_DOWN"),
            ("PgUp", "KEY_PAGEUP"), ("PgDn", "KEY_PAGEDOWN"), ("Space", "KEY_SPACE"),
            ("Enter", "KEY_ENTER"), ("Tab", "KEY_TAB"), ("Esc", "KEY_ESC"),
        )))
        more_choices = (
            *((f"F{number}", f"KEY_F{number}") for number in range(1, 13)),
            ("Home", "KEY_HOME"), ("End", "KEY_END"), ("Ins", "KEY_INSERT"), ("Del", "KEY_DELETE"),
            ("Bksp", "KEY_BACKSPACE"), ("Print", "KEY_SYSRQ"), (",", "KEY_COMMA"), (".", "KEY_DOT"),
            ("/", "KEY_SLASH"), (";", "KEY_SEMICOLON"), ("'", "KEY_APOSTROPHE"), ("\\", "KEY_BACKSLASH"),
        )
        more_keys = Adw.ExpanderRow(title="More keys", subtitle="F1–F12, Home, End, Delete, Backspace, Print…")
        more_keys.add_row(key_board(more_choices))
        more_keys.set_expanded(any(code in key_state["keys"] for _label, code in more_choices))
        more_list = self._boxed_list(more_keys)
        more_list.set_margin_top(8)
        shortcut_box.append(more_list)
        page.add(shortcut_group)

        media_group = Adw.PreferencesGroup(title="Media keys")
        media_box = Gtk.FlowBox()
        media_box.set_selection_mode(Gtk.SelectionMode.NONE)
        media_box.set_max_children_per_line(4)
        for label, keys in (
            ("Mute", ["KEY_MUTE"]),
            ("Play / Pause", ["KEY_PLAYPAUSE"]),
            ("Next track", ["KEY_NEXTSONG"]),
            ("Previous track", ["KEY_PREVIOUSSONG"]),
            ("Stop", ["KEY_STOPCD"]),
            ("Mic mute", ["KEY_MICMUTE"]),
            ("Kbd light +", ["KEY_KBDILLUMUP"]),
            ("Kbd light −", ["KEY_KBDILLUMDOWN"]),
        ):
            button = Gtk.ToggleButton(label=label)
            button.add_css_class("proart-key-choice")
            media_buttons.append((keys, button))

            def choose_media(widget, shortcut=keys):
                if syncing["active"]:
                    return
                key_state["keys"] = list(shortcut) if widget.get_active() else []
                refresh_shortcut()

            button.connect("toggled", choose_media)
            media_box.append(button)
        media_group.add(media_box)
        page.add(media_group)

        # Direction / amount ----------------------------------------------------------
        amount_group = Adw.PreferencesGroup(title="Direction")
        direction_names = Gtk.StringList.new(["Up", "Down"])
        direction_row = Adw.ComboRow(title="Direction", model=direction_names)
        default_down = field == "macro_ccw"
        if step.direction is not None:
            default_down = step.direction in ("down", "counterclockwise", "left")
        direction_row.set_selected(1 if default_down else 0)
        amount_group.add(direction_row)
        amount_row = Adw.SpinRow.new_with_range(1, 2000, 1)
        amount_row.set_title("Amount")
        amount_row.set_value(step.value if isinstance(step.value, int) and step.value > 0 else 1)
        amount_group.add(amount_row)
        axis_row = Adw.ComboRow(title="Scroll axis", model=Gtk.StringList.new(["Vertical", "Horizontal"]))
        axis_row.set_selected(1 if step.axis == "horizontal" else 0)
        amount_group.add(axis_row)
        held_row = Adw.ActionRow(title="Hold while scrolling", subtitle="Ctrl turns scrolling into zoom in most apps")
        held_buttons = {}
        held_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        held_box.set_valign(Gtk.Align.CENTER)
        for label, code in (("Ctrl", "KEY_LEFTCTRL"), ("Shift", "KEY_LEFTSHIFT"), ("Alt", "KEY_LEFTALT")):
            button = Gtk.ToggleButton(label=label)
            button.add_css_class("proart-key-choice")
            button.set_active(code in (step.modifiers or []))
            held_buttons[code] = button
            held_box.append(button)
        held_row.add_suffix(held_box)
        amount_group.add(held_row)
        page.add(amount_group)

        command_group = Adw.PreferencesGroup(
            title="Command",
            description="Runs through the shell. Prefer a shortcut when one exists.",
        )
        command_row = Adw.EntryRow(title="Command, e.g. gnome-calculator or ~/bin/my-script.sh")
        command_row.set_text(step.script or " ".join([step.program or "", *(step.args or [])]).strip())
        command_group.add(command_row)
        page.add(command_group)

        delay_group = Adw.PreferencesGroup(title="Wait")
        delay_row = Adw.SpinRow.new_with_range(0, 10, 0.05)
        delay_row.set_title("Seconds")
        delay_row.set_digits(2)
        delay_row.set_value(step.duration or 0.1)
        delay_group.add(delay_row)
        page.add(delay_group)

        def current_type():
            return ACTION_TYPES[type_row.get_selected()][1]

        def refresh_fields(*_args):
            action = current_type()
            shortcut_group.set_visible(action is StepType.KEYSTROKE)
            media_group.set_visible(action is StepType.MEDIA)
            amount_group.set_visible(action in (StepType.VOLUME, StepType.BRIGHTNESS, StepType.SCROLL))
            amount_row.set_title("Scroll units (120 = one notch)" if action is StepType.SCROLL else "Repeat")
            amount_row.set_subtitle("" if action is StepType.SCROLL else "Times per rotation step")
            horizontal = axis_row.get_selected() == 1
            names = {
                StepType.VOLUME: ["Louder", "Quieter"],
                StepType.BRIGHTNESS: ["Brighter", "Dimmer"],
            }.get(action, ["Right", "Left"] if horizontal else ["Up", "Down"])
            selected = direction_row.get_selected()
            direction_names.splice(0, direction_names.get_n_items(), names)
            direction_row.set_selected(selected)
            axis_row.set_visible(action is StepType.SCROLL)
            held_row.set_visible(action is StepType.SCROLL)
            command_group.set_visible(action in (StepType.SHELL, StepType.COMMAND))
            delay_group.set_visible(action is StepType.DELAY)
            if action is StepType.SCROLL and amount_row.get_value() < 10:
                amount_row.set_value(120)

        type_row.connect("notify::selected", refresh_fields)
        axis_row.connect("notify::selected", refresh_fields)
        refresh_shortcut()
        refresh_fields()

        def accept(_button):
            action = current_type()
            keys = [key for key in key_state["keys"] if key]
            if action in (StepType.KEYSTROKE, StepType.MEDIA) and not keys:
                self._toast("Press or choose a shortcut first")
                return
            if action in (StepType.SHELL, StepType.COMMAND) and not command_row.get_text().strip():
                self._toast("Enter a command first")
                return
            down = direction_row.get_selected() == 1
            result = MacroStep(type=StepType.SHELL if action is StepType.COMMAND else action)
            result.title = label_row.get_text().strip() or None
            if action in (StepType.KEYSTROKE, StepType.MEDIA):
                result.keys = keys
            elif action in (StepType.VOLUME, StepType.BRIGHTNESS):
                result.direction = "down" if down else "up"
                result.value = amount_row.get_value_as_int()
            elif action is StepType.SCROLL:
                result.direction = "counterclockwise" if down else "clockwise"
                result.value = amount_row.get_value_as_int()
                result.axis = "horizontal" if axis_row.get_selected() == 1 else None
                held = [code for code, button in held_buttons.items() if button.get_active()]
                result.modifiers = held or None
            elif action in (StepType.SHELL, StepType.COMMAND):
                result.script = command_row.get_text().strip()
            elif action is StepType.DELAY:
                result.duration = round(delay_row.get_value(), 2)
            if original is not None and original in steps:
                steps[steps.index(original)] = result
            else:
                steps.append(result)
            dialog.close()
            changed()

        save.connect("clicked", accept)
        dialog.present(self._window)

    # --- Behaviour ------------------------------------------------------------------

    def _build_behaviour_page(self):
        content = self._page_box()
        activation = Adw.PreferencesGroup(
            title="Activation",
            description="Hold the illuminated corner icon of the touchpad to switch the DialPad on or off.",
        )
        activation.add(_spin_row(
            "Hold time", "Seconds the corner icon must be held",
            self.config.activation.activation_time, 0.1, 5.0, 0.1, 1,
            lambda value: self._set_config("activation", "activation_time", round(value, 2)),
        ))
        coactivator = Adw.ActionRow(
            title="Co-activator keys",
            subtitle="Hold these too while pressing the icon, so it cannot switch on by accident; none = icon alone",
        )
        coactivator_box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)

        def coactivator_toggled(button, name):
            keys = [key for key in self.config.activation.coactivator_keys if key not in (name, "Ctrl")]
            if button.get_active():
                keys.append(name)
            self._set_config("activation", "coactivator_keys", keys)

        held_keys = self.config.activation.coactivator_keys
        # The driver takes modifier names (Control, not Ctrl) and tracks only these three.
        for label, name in (("Ctrl", "Control"), ("Shift", "Shift"), ("Alt", "Alt")):
            button = Gtk.ToggleButton(label=label)
            button.add_css_class("proart-key-choice")
            button.set_active(name in held_keys or (name == "Control" and "Ctrl" in held_keys))
            button.connect("toggled", coactivator_toggled, name)
            coactivator_box.append(button)
        coactivator.add_suffix(coactivator_box)
        activation.add(coactivator)
        activation.add(_spin_row(
            "Switch off after inactivity", "Minutes without touch; 0 keeps the DialPad on",
            self.config.gesture.disable_inactivity_time / 60, 0, 120, 1, 0,
            lambda value: self._set_config("gesture", "disable_inactivity_time", int(value * 60)),
        ))
        content.append(activation)
        content.append(self._light_group())

        gesture = Adw.PreferencesGroup(
            title="Rotation",
            description="Global defaults; profiles can scale them with their own sensitivity.",
        )
        gesture.add(_spin_row(
            "Default rotation step", "Degrees of rotation per action",
            self.config.gesture.default_threshold, 5, 360, 5, 0,
            lambda value: self._set_config("gesture", "default_threshold", int(value)),
        ))
        gesture.add(_spin_row(
            "Jitter filter", "Ignore finger movements smaller than this many degrees",
            self.config.gesture.hysteresis, 0, 30, 1, 0,
            lambda value: self._set_config("gesture", "hysteresis", int(value)),
        ))
        gesture.add(_spin_row(
            "Centre hold for menu", "Seconds to hold the centre to return to the radial menu",
            self.config.gesture.center_hold_time, 0.2, 2.0, 0.05, 2,
            lambda value: self._set_config("gesture", "center_hold_time", round(value, 2)),
        ))
        gesture.add(_spin_row(
            "Minimum menu sectors", "Smaller menus are padded so sectors stay comfortable",
            self.config.gesture.slices_minimum_count, 1, 12, 1, 0,
            lambda value: self._set_config("gesture", "slices_minimum_count", int(value)),
        ))
        content.append(gesture)
        return self._scrolled_content(content)

    # --- Skins and the on-screen dial --------------------------------------------------

    def _build_look_page(self):
        content = self._page_box()
        content.append(self._skin_group())
        content.append(self._style_group())

        overlay = Adw.PreferencesGroup(
            title="On-screen dial",
            description="Applied by the GNOME Shell extension immediately.",
        )
        overlay.add(_spin_row(
            "Size", "Scale of the dial overlay",
            self.config.overlay.scale, 0.6, 1.6, 0.05, 2,
            lambda value: self._set_config("overlay", "scale", round(value, 2)),
        ))
        self._dial_look_rows(
            overlay.add,
            lambda key: getattr(self.config.overlay, key),
            lambda key, value: self._set_config("overlay", key, value),
        )
        overlay.add(_spin_row(
            "Stay visible after release", "Milliseconds, so the final value can be read",
            self.config.overlay.hide_delay_ms, 0, 5000, 100, 0,
            lambda value: self._set_config("overlay", "hide_delay_ms", int(value)),
        ))
        overlay.add(_switch_row(
            "Show action hints", "Labels for both rotation directions and the tap action",
            self.config.overlay.show_hints,
            lambda active: self._set_config("overlay", "show_hints", active),
        ))
        overlay.add(_spin_row(
            "Progress arc from", "Show a progress arc while turning functions whose step is at least this many degrees",
            self.config.overlay.progress_above_degrees, 5, 360, 5, 0,
            lambda value: self._set_config("overlay", "progress_above_degrees", int(value)),
        ))
        overlay.add(_switch_row(
            "Show finger position", "A dot follows the finger around the ring",
            self.config.overlay.show_pointer,
            lambda active: self._set_config("overlay", "show_pointer", active),
        ))
        accent_row = Adw.ActionRow(title="Accent colour", subtitle="Used for functions without their own colour")
        accent = Gtk.ColorDialogButton.new(Gtk.ColorDialog(with_alpha=False))
        accent.set_valign(Gtk.Align.CENTER)
        rgba = Gdk.RGBA()
        rgba.parse(self.config.overlay.accent or DEFAULT_ACCENT)
        accent.set_rgba(rgba)
        accent.connect("notify::rgba", lambda button, _param: self._set_accent(_rgba_to_hex(button.get_rgba())))
        accent_row.add_suffix(accent)
        overlay.add(accent_row)
        content.append(overlay)
        return self._scrolled_content(content)

    def _dial_look_expander(self, tooltip, get, set_value, inherit_label, inherited):
        """The dial look rows of a profile or function, folded."""
        expander = Adw.ExpanderRow(title="On-screen dial")
        expander.set_tooltip_text(tooltip)

        def summary():
            parts = []
            if get("position") is not None:
                parts.append(POSITION_LABELS[get("position")])
            if get("visibility") is not None:
                parts.append(VISIBILITY_LABELS[get("visibility")])
            expander.set_subtitle(" · ".join(parts) or inherit_label)

        def changed(key, value):
            set_value(key, value)
            summary()

        self._dial_look_rows(expander.add_row, get, changed, inherit_label, inherited)
        summary()
        return expander

    def _dial_look_rows(self, add, get, set_value, inherit_label=None, inherited=None):
        """Position and visibility of the on-screen dial.

        get(key) is this level's value and set_value(key, value) stores one.
        A profile or function also gets inherit_label ("As default"), a
        choice that removes its own value (set_value(key, None)), and
        inherited(key), the value from the level above.
        """

        def current(key):
            value = get(key)
            return inherited(key) if value is None and inherited else value

        def choices_row(title, key, labels, extra_keys):
            """extra_keys: {option: keys} of the details an option shows."""
            options = ([None] if inherit_label else []) + list(labels)
            names = [
                f"{inherit_label} ({labels[inherited(key)]})" if option is None else labels[option]
                for option in options
            ]

            def chosen(index):
                option = options[index]
                if option is None:
                    for extra in (key, *(extra for keys in extra_keys.values() for extra in keys)):
                        set_value(extra, None)
                else:
                    set_value(key, option)
                    # Keep the inherited details so the choice looks the same.
                    for extra in extra_keys.get(option, ()):
                        if get(extra) is None:
                            set_value(extra, current(extra))
                update()

            selected = get(key)
            return _combo_row(title, None, names, options.index(selected) if selected in options else 0, chosen)

        position = choices_row("Position", "position", POSITION_LABELS, {
            "custom": ("custom_x", "custom_y"),
            "pointer": ("pointer_side", "pointer_gap"),
        })
        add(position)
        picker = self._screen_point_picker(
            lambda: (current("custom_x"), current("custom_y")),
            lambda x, y: (set_value("custom_x", x), set_value("custom_y", y)),
        )
        add(picker)
        sides = list(POINTER_SIDE_LABELS)
        side = _combo_row(
            "Side of the pointer", "Another side when there is no room on this one",
            list(POINTER_SIDE_LABELS.values()),
            sides.index(current("pointer_side")) if current("pointer_side") in sides else 0,
            lambda index: set_value("pointer_side", sides[index]),
        )
        add(side)
        gap = _spin_row(
            "Distance from the pointer", "Pixels between the pointer and the dial",
            current("pointer_gap"), 0, POINTER_GAP_MAX, 4, 0,
            lambda value: set_value("pointer_gap", int(value)),
        )
        add(gap)
        visibility = choices_row("Visibility", "visibility", VISIBILITY_LABELS, {"fade": ("fade_opacity",)})
        add(visibility)
        fade = _spin_row(
            "Fade to", "Percent of full opacity, reached a second after the finger lands",
            current("fade_opacity"), 0, 100, 5, 0,
            lambda value: set_value("fade_opacity", int(value)),
        )
        add(fade)

        def update():
            picker.set_visible(get("position") == "custom")
            side.set_visible(get("position") == "pointer")
            gap.set_visible(get("position") == "pointer")
            fade.set_visible(get("visibility") == "fade")

        update()

    def _monitor_size(self):
        """Logical size of the first monitor, as GNOME places the dial on it."""
        display = Gdk.Display.get_default()
        monitors = display.get_monitors() if display else None
        if monitors is not None and monitors.get_n_items():
            geometry = monitors.get_item(0).get_geometry()
            if geometry.width and geometry.height:
                return geometry.width, geometry.height
        return 1440, 900

    def _screen_point_picker(self, get_point, set_point):
        """A miniature screen; dragging the dial on it picks its centre."""
        screen_width, screen_height = self._monitor_size()
        area = Gtk.DrawingArea(hexpand=True, margin_top=10, margin_bottom=10, margin_start=12, margin_end=12)
        area.set_content_height(160)
        area.set_cursor_from_name("grab")
        area.set_tooltip_text("Drag the dial to where it should appear")
        dragging = {"start": None, "point": None}

        def screen(width, height):
            ratio = screen_width / screen_height
            w, h = (height - 4) * ratio, height - 4
            if w > width - 4:
                w, h = width - 4, (width - 4) / ratio
            return (width - w) / 2, (height - h) / 2, w, h

        def draw(_area, cr, width, height):
            x0, y0, w, h = screen(width, height)
            cr.rectangle(x0, y0, w, h)
            cr.set_source_rgba(1, 1, 1, 0.05)
            cr.fill_preserve()
            cr.set_source_rgba(1, 1, 1, 0.3)
            cr.set_line_width(1)
            cr.stroke()
            # GNOME's top bar.
            cr.rectangle(x0, y0, w, max(2, h * 0.03))
            cr.set_source_rgba(1, 1, 1, 0.14)
            cr.fill()
            px, py = dragging["point"] or get_point()
            radius = 180 * self.config.overlay.scale / screen_height * h
            # The extension keeps the dial on the screen.
            cx = min(max(x0 + px * w, x0 + radius), x0 + w - radius)
            cy = min(max(y0 + py * h, y0 + radius), y0 + h - radius)
            red, green, blue = _hex_to_rgb(self.config.overlay.accent or DEFAULT_ACCENT)
            cr.arc(cx, cy, radius, 0, 2 * math.pi)
            cr.set_source_rgba(red, green, blue, 0.35)
            cr.fill_preserve()
            cr.set_source_rgba(red, green, blue, 0.9)
            cr.set_line_width(1.5)
            cr.stroke()
            cr.arc(x0 + px * w, y0 + py * h, 2.5, 0, 2 * math.pi)
            cr.set_source_rgba(1, 1, 1, 0.9)
            cr.fill()

        def point_at(x, y):
            x0, y0, w, h = screen(area.get_width(), area.get_height())
            return (round(min(max((x - x0) / w, 0.0), 1.0), 3), round(min(max((y - y0) / h, 0.0), 1.0), 3))

        def begin(_gesture, x, y):
            dragging["start"] = (x, y)
            dragging["point"] = point_at(x, y)
            area.queue_draw()

        def update(_gesture, dx, dy):
            x, y = dragging["start"]
            dragging["point"] = point_at(x + dx, y + dy)
            area.queue_draw()

        def end(gesture, dx, dy):
            update(gesture, dx, dy)
            set_point(*dragging["point"])
            dragging["point"] = None
            area.queue_draw()

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", begin)
        drag.connect("drag-update", update)
        drag.connect("drag-end", end)
        area.add_controller(drag)
        area.set_draw_func(draw)
        row = Adw.PreferencesRow(activatable=False)
        row.set_child(area)
        return row

    def _set_accent(self, value):
        self._set_config("overlay", "accent", value)
        self._on_skins_changed()

    # --- skins ------------------------------------------------------------------

    def _light_group(self):
        group = Adw.PreferencesGroup(
            title="DialPad light",
            description="Brightness of the DialPad while it is on.",
        )
        backlight = self.config.backlight
        level = _spin_row(
            "Brightness", "0 keeps the light off; the dial still works",
            backlight.level, 0, 8, 1, 0,
            lambda value: self._set_config("backlight", "level", int(value)),
        )
        level.set_sensitive(not backlight.follow_keyboard)

        def follow_changed(active):
            level.set_sensitive(not active)
            self._set_config("backlight", "follow_keyboard", active)

        group.add(_switch_row(
            "Follow keyboard backlight",
            "The keyboard brightness key sets the DialPad light too, including off",
            backlight.follow_keyboard, follow_changed,
        ))
        group.add(level)
        return group

    def _skin_group(self):
        group = Adw.PreferencesGroup(
            title="Skin",
            description="How the on-screen dial looks. Customise a skin to make your own; "
            "skins are small JSON files that you or an AI assistant can also write by hand.",
        )
        self._skin_gallery = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
            column_spacing=12, row_spacing=12, min_children_per_line=2, max_children_per_line=3,
        )
        self._skin_gallery.add_css_class("proart-skin-gallery")
        group.add(self._skin_gallery)

        # Clear entry points; the pencil on each card does the same.
        customise = _button_row("Customise the selected skin…", "document-edit-symbolic")
        customise.connect("activated", lambda _row: self._customise_current_skin())
        group.add(customise)
        new = _button_row("New skin…", "list-add-symbolic")
        new.connect("activated", lambda _row: self._show_skin_editor(None, self._new_skin_draft(self._current_skin_id())))
        group.add(new)
        self._refresh_skin_gallery()
        return group

    def _current_skin_id(self):
        current = getattr(self.config.overlay, self._skin_attribute())
        return current if current in self._skins else "glass"

    def _customise_current_skin(self):
        skin_id = self._current_skin_id()
        if is_builtin(skin_id):
            self._show_skin_editor(None, self._new_skin_draft(skin_id))
        else:
            self._show_skin_editor(skin_id, copy.deepcopy(self._skins[skin_id]))

    def _style_group(self):
        group = Adw.PreferencesGroup()
        follow = _switch_row(
            "Match system style", "Use one skin while GNOME is light and another while it is dark",
            self.config.overlay.follow_system_style,
            lambda active: (self._set_config("overlay", "follow_system_style", active), self._on_skins_changed()),
        )
        group.add(follow)
        for attribute, title in (("skin", "Dark style"), ("skin_light", "Light style")):
            row = Adw.ComboRow(title=title)
            handler = row.connect(
                "notify::selected",
                lambda row, _param, attribute=attribute: self._choose_style_skin(attribute, row.get_selected()),
            )
            self._style_rows[attribute] = (row, handler)
            group.add(row)
        self._sync_style_rows()
        return group

    def _sync_style_rows(self):
        ids = list(self._skins)
        for attribute, (row, handler) in self._style_rows.items():
            row.handler_block(handler)
            row.set_model(Gtk.StringList.new([skin_name(self._skins, key) for key in ids]))
            current = getattr(self.config.overlay, attribute)
            row.set_selected(ids.index(current) if current in ids else 0)
            row.set_visible(self.config.overlay.follow_system_style)
            row.handler_unblock(handler)

    def _choose_style_skin(self, attribute, index):
        ids = list(self._skins)
        if 0 <= index < len(ids):
            self._set_config("overlay", attribute, ids[index])
            self._on_skins_changed()

    def _on_skins_changed(self):
        self._refresh_skin_gallery()
        self._sync_style_rows()
        if self._dial_preview is not None:
            self._dial_preview.queue_draw()

    def _sample_view(self, skin, selected=False):
        accent = self.config.overlay.accent or DEFAULT_ACCENT
        if selected:
            return DialView(
                skin, SAMPLE_TITLES, SAMPLE_COLORS, accent, title="Volume",
                selected=True, value=64, unit="%", pointer=118,
            )
        return DialView(skin, SAMPLE_TITLES, SAMPLE_COLORS, accent, title="Zoom", profile="Default", pointer=228)

    def _skin_preview_area(self, view_for, size):
        area = Gtk.DrawingArea()
        area.set_content_width(size)
        area.set_content_height(size)
        area.set_draw_func(lambda _area, cr, width, height: paint_dial(cr, view_for(), *self._preview_square(width, height), draw_icon=draw_icon))
        return area

    def _refresh_skin_gallery(self):
        if self._skin_gallery is None:
            return
        _clear_children(self._skin_gallery)
        overlay = self.config.overlay
        current = getattr(overlay, self._skin_attribute())
        current = current if current in self._skins else "glass"
        for skin_id in self._skins:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            card.add_css_class("proart-skin-card")
            if skin_id == current:
                card.add_css_class("selected")
            resolved = self._skin(skin_id)
            preview = self._skin_preview_area(lambda resolved=resolved: self._sample_view(resolved), 116)
            # The badges sit on the preview: in the footer they widened the
            # card they moved to, and with it every card (the gallery is
            # homogeneous), so a click changed the number of columns.
            preview_overlay = Gtk.Overlay(child=preview)
            badges = Gtk.Box(spacing=4, halign=Gtk.Align.START, valign=Gtk.Align.START)
            if overlay.follow_system_style:
                for attribute, badge_text in (("skin_light", "Light"), ("skin", "Dark")):
                    if getattr(overlay, attribute) == skin_id:
                        badge = Gtk.Label(label=badge_text)
                        badge.add_css_class("proart-badge")
                        badges.append(badge)
            preview_overlay.add_overlay(badges)
            badges.set_can_target(False)
            card.append(preview_overlay)

            footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            name = skin_name(self._skins, skin_id)
            label = Gtk.Label(label=name, xalign=0, hexpand=True)
            label.set_ellipsize(3)
            # A long name must not widen the card either; it shortens instead.
            label.set_max_width_chars(12)
            label.set_tooltip_text(
                name if is_builtin(skin_id) else f"{name} · your skin · {SKINS_DIR / (skin_id + '.json')}"
            )
            footer.append(label)
            edit = Gtk.Button.new_from_icon_name("document-edit-symbolic")
            edit.add_css_class("flat")
            edit.add_css_class("circular")
            if is_builtin(skin_id):
                edit.set_tooltip_text("Customise a copy")
                edit.connect("clicked", lambda _b, skin_id=skin_id: self._show_skin_editor(None, self._new_skin_draft(skin_id)))
            else:
                edit.set_tooltip_text("Edit")
                edit.connect("clicked", lambda _b, skin_id=skin_id: self._show_skin_editor(skin_id, copy.deepcopy(self._skins[skin_id])))
            footer.append(edit)
            if not is_builtin(skin_id):
                delete = Gtk.Button.new_from_icon_name("user-trash-symbolic")
                delete.add_css_class("flat")
                delete.add_css_class("circular")
                delete.set_tooltip_text("Delete")
                delete.connect("clicked", lambda _b, skin_id=skin_id: self._confirm_delete_skin(skin_id))
                footer.append(delete)
            card.append(footer)

            click = Gtk.GestureClick.new()
            click.connect("released", lambda *_args, skin_id=skin_id: self._select_skin(skin_id))
            preview.add_controller(click)
            preview.set_cursor_from_name("pointer")
            preview.set_tooltip_text(self._skins[skin_id].get("description") or "Use this skin")
            self._skin_gallery.append(card)

    def _confirm_delete_skin(self, skin_id, deleted=None):
        """Ask, then delete a user skin and its picture; deleted() runs afterwards."""
        skin = self._skins.get(skin_id) or {}
        background = skin.get("background")
        picture = background.get("image") if isinstance(background, dict) else None
        dialog = Adw.AlertDialog(
            heading=f"Delete “{skin_name(self._skins, skin_id)}”?",
            body="The skin file and its background picture are removed from the skins folder."
            if picture else "The skin file is removed from the skins folder.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def respond(_dialog, response):
            if response != "delete":
                return
            delete_user_skin(skin_id)
            self._delete_unused_picture(picture)
            fallback = skin.get("base") if is_builtin(skin.get("base") or "") else None
            for attribute, default in (("skin", "glass"), ("skin_light", "frost")):
                if getattr(self.config.overlay, attribute) == skin_id:
                    self._set_config("overlay", attribute, fallback or default)
            self._reload_skins()
            if deleted:
                deleted()
            self._toast("Skin deleted")

        dialog.connect("response", respond)
        dialog.present(self._window)

    def _select_skin(self, skin_id):
        """Use a skin for the style the system is in now."""
        attribute = self._skin_attribute()
        if getattr(self.config.overlay, attribute) == skin_id:
            return
        self._set_config("overlay", attribute, skin_id)
        self._on_skins_changed()

    def _new_skin_draft(self, base_id):
        base_id = base_id if base_id in self._skins else "glass"
        return {"name": f"My {skin_name(self._skins, base_id)}", "base": base_id, "palette": {}, DIR_KEY: str(SKINS_DIR)}

    def _show_skin_editor(self, skin_id, draft):
        """Edit a user skin (skin_id) or create one (skin_id None) from a draft.

        The draft keeps only what differs from its base skin, so saved files
        stay short and readable.
        """
        draft.setdefault("palette", {})
        if not isinstance(draft["palette"], dict):
            draft["palette"] = {}
        draft.setdefault(DIR_KEY, str(SKINS_DIR))
        # Pictures imported in this editor; the unused ones go when it closes.
        created_images = []
        saved = {"done": False}
        dialog = Adw.Dialog(
            title="Edit skin" if skin_id else "New skin", content_width=640, content_height=820,
        )
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        header.pack_end(save)
        view.add_top_bar(header)
        dialog.set_child(view)

        def bases():
            return [key for key in self._skins if key != skin_id]

        def resolved(without_override=None):
            palette = dict(draft["palette"])
            if without_override:
                palette.pop(without_override, None)
            skins = dict(self._skins)
            skins["\0draft"] = {**draft, "palette": palette}
            return resolve_skin(skins, "\0draft")

        previews = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, halign=Gtk.Align.CENTER)
        previews.set_margin_top(6)
        previews.set_margin_bottom(6)
        menu_preview = self._skin_preview_area(lambda: self._sample_view(resolved()), 230)
        gauge_preview = self._skin_preview_area(lambda: self._sample_view(resolved(), selected=True), 230)
        previews.append(menu_preview)
        previews.append(gauge_preview)
        view.add_top_bar(previews)

        def redraw():
            menu_preview.queue_draw()
            gauge_preview.queue_draw()

        page = Adw.PreferencesPage()
        view.set_content(page)

        general = Adw.PreferencesGroup(title="Skin")
        name = Adw.EntryRow(title="Name")
        name.set_text(str(draft.get("name") or ""))
        name.connect("changed", lambda entry: draft.__setitem__("name", entry.get_text().strip()))
        general.add(name)
        base_ids = bases()
        base_row = _combo_row(
            "Based on", "Everything you do not change comes from this skin",
            [skin_name(self._skins, key) for key in base_ids],
            base_ids.index(draft.get("base")) if draft.get("base") in base_ids else 0,
            lambda index: (draft.__setitem__("base", base_ids[index]), sync_rows(), sync_shape(), redraw()),
        )
        if draft.get("base") not in base_ids and base_ids:
            draft["base"] = base_ids[0]
        general.add(base_row)
        page.add(general)

        # --- shape -------------------------------------------------------
        shape_group = Adw.PreferencesGroup(title="Shape")

        def choice_row(title, subtitle, key, values, labels):
            """A combo whose first entry keeps the base skin's value."""
            options = [None, *values]
            row = _combo_row(
                title, subtitle,
                ["Same as the base skin", *(labels[value] for value in values)],
                options.index(draft.get(key)) if draft.get(key) in values else 0,
                lambda index: (
                    draft.__setitem__(key, options[index]) if index else draft.pop(key, None),
                    sync_shape(), redraw(),
                ),
            )
            shape_group.add(row)
            return row

        choice_row("Dial shape", None, "shape", DIAL_SHAPES, SHAPE_LABELS)
        choice_row("Menu style", None, "menu", MENU_STYLES, MENU_STYLE_LABELS)
        tile_row = choice_row("Function tiles", "Shape of each function in the bubble menu", "tile", TILE_SHAPES, TILE_LABELS)
        tile_size = _spin_row(
            "Tile size", "1 is the standard size",
            float(draft.get("tileSize", 1)), TILE_SIZE_MIN, TILE_SIZE_MAX, 0.05, 2,
            lambda value: (draft.__setitem__("tileSize", round(value, 2)), redraw()),
        )
        shape_group.add(tile_size)
        page.add(shape_group)

        # --- custom outline -----------------------------------------------
        outline_group = Adw.PreferencesGroup(
            title="Custom outline",
            description="Drag the blue points towards or away from the centre; they snap to the rings "
            "and stay inside the dashed square. Start from a preset and change it.",
        )
        outline_options = Adw.PreferencesGroup()
        outline = draft.get("outline") if isinstance(draft.get("outline"), dict) else None
        outline = outline or {"points": list(PRESETS[1][1]), "smooth": PRESETS[1][2]}

        def store_outline(points=None):
            draft["outline"] = {"points": points if points is not None else editor.points, "smooth": smooth.get_active()}
            redraw()

        editor = OutlineEditor(lambda: self._sample_view(resolved()), outline, lambda points: store_outline(points),
                               draw_icon=draw_icon)
        editor_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        editor_box.append(editor)
        presets = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, halign=Gtk.Align.CENTER)

        def use_preset(points, is_smooth):
            editor.set_points(points)
            count.set_value(len(points))
            smooth.set_active(is_smooth)
            store_outline(list(points))

        for label, points, is_smooth in PRESETS:
            button = Gtk.Button(label=label)
            button.add_css_class("pill")
            button.connect("clicked", lambda _button, points=points, is_smooth=is_smooth: use_preset(list(points), is_smooth))
            presets.append(button)
        editor_box.append(presets)
        outline_group.add(editor_box)

        count = _spin_row(
            "Points", "More points allow finer shapes",
            len(outline["points"]), 3, 24, 1, 0,
            lambda value: (editor.set_points(resample_outline(editor.points, int(value))), store_outline())
            if int(value) != len(editor.points) else None,
        )
        outline_options.add(count)
        smooth = _switch_row("Smooth", "Curves through the points instead of straight lines",
                             outline["smooth"], lambda _active: store_outline())
        outline_options.add(smooth)
        outline_options.add(_switch_row("Mirror", "The left half follows the right one",
                                        True, lambda active: setattr(editor, "mirror", active)))
        page.add(outline_group)
        page.add(outline_options)

        def sync_shape():
            effective = resolved()
            custom = draft.get("shape") == "custom" or ("shape" not in draft and effective["shape"] == "custom")
            outline_group.set_visible(custom)
            outline_options.set_visible(custom)
            tile_row.set_sensitive(effective["menu"] == "bubbles")
            tile_size.set_sensitive(effective["menu"] == "bubbles")

        # --- background picture -------------------------------------------
        background_group = Adw.PreferencesGroup(
            title="Background picture",
            description="Any picture; it covers the disc and is cut to its shape.",
        )
        picture_row = Adw.ActionRow(title="Picture")
        choose = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        remove_picture = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        remove_picture.set_valign(Gtk.Align.CENTER)
        remove_picture.add_css_class("flat")
        remove_picture.set_tooltip_text("Remove the picture")
        picture_row.add_suffix(remove_picture)
        picture_row.add_suffix(choose)
        background_group.add(picture_row)
        opacity = _spin_row(
            "Picture opacity", None,
            float((draft.get("background") or {}).get("opacity", 0.6)), 0.05, 1, 0.05, 2,
            lambda value: set_background_opacity(round(value, 2)),
        )
        background_group.add(opacity)
        page.add(background_group)

        def sync_background():
            background = draft.get("background") if isinstance(draft.get("background"), dict) else None
            picture_row.set_subtitle(background["image"] if background else "None")
            remove_picture.set_visible(bool(background))
            opacity.set_sensitive(bool(background))

        def set_background_opacity(value):
            if isinstance(draft.get("background"), dict):
                draft["background"]["opacity"] = value
                redraw()

        def picked(dialog_, result):
            try:
                chosen = dialog_.open_finish(result)
            except GLib.Error:
                return
            try:
                image_name = self._import_background(chosen.get_path())
            except GLib.Error as error:
                self._toast(f"Could not read the picture: {error.message}")
                return
            created_images.append(image_name)
            draft["background"] = {"image": image_name, "opacity": round(opacity.get_value(), 2)}
            sync_background()
            redraw()

        def choose_picture(_button):
            images = Gtk.FileFilter()
            images.set_name("Pictures")
            images.add_pixbuf_formats()
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(images)
            Gtk.FileDialog(title="Background picture", filters=filters).open(self._window, None, picked)

        def drop_picture(_button):
            draft["background"] = None
            sync_background()
            redraw()

        choose.connect("clicked", choose_picture)
        remove_picture.connect("clicked", drop_picture)
        sync_background()

        # --- colours --------------------------------------------------------
        colours = Adw.PreferencesGroup(
            title="Colours",
            description="Many parts are see-through on purpose; raise the opacity to make a colour stand out. "
            "Changed colours get a reset button.",
        )
        rows = {}
        updating = {"active": False}

        def set_value(key, value):
            if updating["active"]:
                return
            draft["palette"][key] = value
            rows[key][1].set_visible(True)
            redraw()

        def reset(key):
            draft["palette"].pop(key, None)
            sync_rows()
            redraw()

        def sync_rows():
            updating["active"] = True
            effective = resolved()["palette"]
            for key, (widget, reset_button, alpha) in rows.items():
                if key == "shadow":
                    widget.set_value(effective["shadow"])
                else:
                    rgba = Gdk.RGBA()
                    rgba.red, rgba.green, rgba.blue, rgba.alpha = effective[key]
                    widget.set_rgba(rgba)
                    alpha.set_value(round(effective[key][3] * 100))
                reset_button.set_visible(key in draft["palette"])
            updating["active"] = False

        def colour_from(button, alpha_percent=None):
            rgba = button.get_rgba()
            alpha = rgba.alpha if alpha_percent is None else alpha_percent / 100
            return color_to_hex((rgba.red, rgba.green, rgba.blue, alpha))

        for key, label, help_text in PALETTE_KEYS:
            reset_button = Gtk.Button.new_from_icon_name("edit-undo-symbolic")
            reset_button.set_tooltip_text("Use the base skin's value")
            reset_button.set_valign(Gtk.Align.CENTER)
            reset_button.add_css_class("flat")
            reset_button.connect("clicked", lambda _button, key=key: reset(key))
            if key == "shadow":
                row = _spin_row(label, help_text, 0.45, 0, 1, 0.05, 2,
                                lambda value: set_value("shadow", round(value, 2)))
                row.add_suffix(reset_button)
                rows[key] = (row, reset_button, None)
            else:
                row = Adw.ActionRow(title=label, subtitle=help_text)
                button = Gtk.ColorDialogButton.new(Gtk.ColorDialog(with_alpha=True))
                button.set_valign(Gtk.Align.CENTER)
                alpha = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
                alpha.set_size_request(110, -1)
                alpha.set_valign(Gtk.Align.CENTER)
                alpha.set_draw_value(False)
                alpha.set_tooltip_text("Opacity")
                button.connect(
                    "notify::rgba",
                    lambda button, _param, key=key, alpha=alpha: (
                        None if updating["active"] else (
                            alpha.set_value(round(button.get_rgba().alpha * 100)),
                            set_value(key, colour_from(button)),
                        )
                    ),
                )
                alpha.connect(
                    "value-changed",
                    lambda alpha, key=key, button=button: set_value(key, colour_from(button, alpha.get_value())),
                )
                row.add_suffix(reset_button)
                row.add_suffix(alpha)
                row.add_suffix(button)
                rows[key] = (button, reset_button, alpha)
            colours.add(row)
        page.add(colours)
        sync_rows()
        sync_shape()

        sharing = Adw.PreferencesGroup(
            title="Share and AI",
            description="Ask an assistant for a skin: copy the prompt, describe your idea, "
            "copy its JSON answer and paste it here.",
        )

        def clipboard():
            return self._window.get_display().get_clipboard()

        def snapshot():
            # "background": null is kept: it removes the base skin's picture.
            return {
                key: value for key, value in without_private_keys(draft).items()
                if value not in (None, "") or key == "background"
            }

        def action_row(title, subtitle, icon, callback):
            row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
            row.add_suffix(Gtk.Image.new_from_icon_name(icon))
            row.connect("activated", lambda _row: callback())
            return row

        def copy_prompt():
            clipboard().set(ai_prompt(snapshot()))
            self._toast("Prompt copied — paste it into your AI assistant")

        def copy_json():
            clipboard().set(json.dumps(snapshot(), indent=4))
            self._toast("Skin copied as JSON")

        def paste_json():
            def done(source, result):
                try:
                    text = source.read_text_finish(result) or ""
                except GLib.Error:
                    text = ""
                start, end = text.find("{"), text.rfind("}")
                try:
                    pasted = json.loads(text[start:end + 1]) if start >= 0 else None
                except ValueError:
                    pasted = None
                if not isinstance(pasted, dict):
                    self._toast("The clipboard holds no skin JSON")
                    return
                dialog.close()
                self._show_skin_editor(skin_id, pasted)
                self._toast("Skin pasted — check it and save")

            clipboard().read_text_async(None, done)

        sharing.add(action_row("Copy prompt for AI", "Format description and this skin, ready for any assistant",
                               "edit-copy-symbolic", copy_prompt))
        sharing.add(action_row("Copy JSON", "Share the skin or edit it elsewhere", "edit-copy-symbolic", copy_json))
        sharing.add(action_row("Paste JSON", "Replace this draft with a skin from the clipboard",
                               "edit-paste-symbolic", paste_json))
        sharing.add(action_row(
            "Open skins folder", str(SKINS_DIR), "folder-open-symbolic",
            lambda: (SKINS_DIR.mkdir(parents=True, exist_ok=True),
                     Gtk.FileLauncher.new(Gio.File.new_for_path(str(SKINS_DIR))).launch(self._window, None, None)),
        ))
        page.add(sharing)

        if skin_id:
            danger = Adw.PreferencesGroup()
            delete = Gtk.Button(label="Delete skin", halign=Gtk.Align.START)
            delete.add_css_class("destructive-action")

            delete.connect("clicked", lambda _b: self._confirm_delete_skin(skin_id, dialog.close))
            danger.add(delete)
            page.add(danger)

        def store(_button):
            draft["name"] = draft.get("name") or "My skin"
            target = skin_id or new_skin_id(draft["name"], self._skins)
            save_user_skin(target, snapshot())
            saved["done"] = True
            self._reload_skins()
            self._select_skin(target)
            dialog.close()
            self._toast(f"Saved “{draft['name']}”")

        def closed(_dialog):
            kept = (draft.get("background") or {}).get("image") if saved["done"] and isinstance(draft.get("background"), dict) else None
            for picture in created_images:
                if picture != kept:
                    self._delete_unused_picture(picture)

        save.connect("clicked", store)
        dialog.connect("closed", closed)
        dialog.present(self._window)

    def _import_background(self, path):
        """Store a picture of any format as a PNG in the skins folder; returns its name."""
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
        width, height = pixbuf.get_width(), pixbuf.get_height()
        # Large photos are scaled so the shorter side covers the disc.
        scale = min(1.0, BACKGROUND_SIZE / min(width, height))
        if scale < 1:
            pixbuf = pixbuf.scale_simple(max(1, round(width * scale)), max(1, round(height * scale)),
                                         GdkPixbuf.InterpType.BILINEAR)
        SKINS_DIR.mkdir(parents=True, exist_ok=True)
        stem = new_skin_id(Path(path).stem, {item.stem for item in SKINS_DIR.glob("*.png")})
        pixbuf.savev(str(SKINS_DIR / f"{stem}.png"), "png", [], [])
        return f"{stem}.png"

    def _delete_unused_picture(self, picture):
        """Delete a picture in the skins folder unless a saved skin uses it."""
        if not picture:
            return
        for skin in user_skins().values():
            background = skin.get("background")
            if isinstance(background, dict) and background.get("image") == picture:
                return
        path = SKINS_DIR / picture
        if path.parent == SKINS_DIR and path.suffix.lower() == ".png" and path.exists():
            path.unlink()

    def _reload_skins(self):
        self._skins = load_skins()
        self._on_skins_changed()

    def _set_config(self, section, attribute, value):
        target = getattr(self.config, section)
        if getattr(target, attribute) != value:
            setattr(target, attribute, value)
            if section == "geometry" and self._calibration_preview is not None:
                self._calibration_preview.queue_draw()
            self._schedule_save()

    # --- Calibration ------------------------------------------------------------------

    def _build_calibration_page(self):
        content = self._page_box()

        guided = Adw.PreferencesGroup(
            title="Guided calibration",
            description=(
                "Measures the dial on any DialPad touchpad: trace the printed outer ring "
                "with one finger, lift it, then trace the edge of the centre button."
            ),
        )
        self._calibration_row = Adw.ActionRow(title="Ready", subtitle="The DialPad pauses while calibrating")
        self._calibration_row.add_prefix(Gtk.Image.new_from_icon_name("find-location-symbolic"))
        self._calibration_start = Gtk.Button(label="Start")
        self._calibration_start.add_css_class("suggested-action")
        self._calibration_start.set_valign(Gtk.Align.CENTER)
        self._calibration_start.connect("clicked", lambda _button: self._start_calibration())
        self._calibration_cancel = Gtk.Button(label="Cancel")
        self._calibration_cancel.set_valign(Gtk.Align.CENTER)
        self._calibration_cancel.set_visible(False)
        self._calibration_cancel.connect("clicked", lambda _button: self._call_driver("CancelCalibration"))
        self._calibration_revert = Gtk.Button(label="Revert")
        self._calibration_revert.set_valign(Gtk.Align.CENTER)
        self._calibration_revert.set_visible(False)
        self._calibration_revert.connect("clicked", lambda _button: self._finish_calibration_review(keep=False))
        self._calibration_keep = Gtk.Button(label="Keep")
        self._calibration_keep.add_css_class("suggested-action")
        self._calibration_keep.set_valign(Gtk.Align.CENTER)
        self._calibration_keep.set_visible(False)
        self._calibration_keep.connect("clicked", lambda _button: self._finish_calibration_review(keep=True))
        for button in (self._calibration_cancel, self._calibration_start,
                       self._calibration_revert, self._calibration_keep):
            self._calibration_row.add_suffix(button)
        guided.add(self._calibration_row)
        content.append(guided)

        connection = self._get_session_bus()
        if connection is not None:
            self._calibration_signal = connection.signal_subscribe(
                DBUS_NAME, DBUS_INTERFACE, "CalibrationProgress", DBUS_PATH, None,
                Gio.DBusSignalFlags.NONE, self._on_calibration_progress,
            )

        intro = Gtk.Label(
            label="Or match the virtual dial by hand. Changes apply live, so test with a finger while adjusting.",
            xalign=0, wrap=True,
        )
        intro.add_css_class("dim-label")
        content.append(intro)

        self._calibration_preview = Gtk.DrawingArea()
        self._calibration_preview.set_content_height(260)
        self._calibration_preview.set_hexpand(True)
        self._calibration_preview.add_css_class("proart-card")
        self._calibration_preview.set_draw_func(self._draw_calibration)
        content.append(self._calibration_preview)

        geometry = Adw.PreferencesGroup(title="Dial geometry", description="Touchpad units")
        for title, attribute in (
            ("Ring diameter", "circle_diameter"),
            ("Centre button diameter", "center_button_diameter"),
            ("Centre X", "center_x"),
            ("Centre Y", "center_y"),
        ):
            row = _spin_row(
                title, None, getattr(self.config.geometry, attribute), 0, 5000, 5, 0,
                lambda value, attribute=attribute: self._set_config("geometry", attribute, int(value)),
            )
            self._geometry_rows[attribute] = row
            geometry.add(row)
        content.append(geometry)

        icon = Adw.PreferencesGroup(title="Corner activation icon", description="Area in the top-right corner, touchpad units")
        for title, attribute in (("Width", "width"), ("Height", "height")):
            row = _spin_row(
                title, None, getattr(self.config.geometry, attribute), 1, 2000, 5, 0,
                lambda value, attribute=attribute: self._set_config("geometry", attribute, int(value)),
            )
            self._geometry_rows[attribute] = row
            icon.add(row)
        content.append(icon)

        reset = Adw.PreferencesGroup()
        reset_row = Adw.ActionRow(
            title="Reset geometry",
            subtitle="Ring, centre button and corner icon as the driver's layout file defines them",
        )
        reset_row.add_prefix(Gtk.Image.new_from_icon_name("edit-undo-symbolic"))
        reset_button = Gtk.Button(label="Reset")
        reset_button.add_css_class("destructive-action")
        reset_button.set_valign(Gtk.Align.CENTER)
        reset_button.connect("clicked", lambda _button: self._reset_geometry())
        reset_row.add_suffix(reset_button)
        reset.add(reset_row)
        content.append(reset)
        return self._scrolled_content(content)

    def _restore_geometry(self, geometry, device_name):
        """Put a whole geometry into the config and the rows, then save."""
        self.config.geometry = copy.copy(geometry)
        self.config.device_name = device_name
        for attribute, row in self._geometry_rows.items():
            row.set_value(getattr(geometry, attribute))
        self._calibration_points = []
        if self._calibration_preview is not None:
            self._calibration_preview.queue_draw()
        self._schedule_save()

    def _reset_geometry(self):
        layout = self._detect_layout()
        geometry = layout_geometry(layout, PROJECT_ROOT / "layouts")
        if geometry is None:
            self._toast(f"Layout {layout} has no geometry to reset to")
            return
        if self._calibration_backup is not None:
            self._finish_calibration_review(keep=True, quiet=True)
        previous = (copy.copy(self.config.geometry), self.config.device_name)
        self._restore_geometry(geometry, layout)
        self._calibration_row.set_title("Ready")
        self._calibration_row.set_subtitle("The DialPad pauses while calibrating")

        toast = Adw.Toast.new(f"Geometry reset to layout {layout}")
        toast.set_button_label("Undo")
        toast.connect("button-clicked", lambda _toast: self._restore_geometry(*previous))
        if self._toasts is not None:
            self._toasts.add_toast(toast)

    def _start_calibration(self):
        if self.config.emulation.enabled:
            self._calibration_row.set_title("Not needed in Touchpad mode")
            self._calibration_row.set_subtitle("It uses the whole touchpad; its centre size is on the Driver page")
            return

        def started(connection, result):
            try:
                if connection is None:
                    raise RuntimeError
                accepted = connection.call_finish(result).unpack()[0]
            except (GLib.Error, RuntimeError):
                accepted = False
            if not accepted:
                self._calibration_row.set_title("Driver not reachable")
                self._calibration_row.set_subtitle("Calibration needs the running driver (D-Bus)")

        self._calibration_points = []
        self._call_driver("StartCalibration", callback=started)

    def _on_calibration_progress(self, _connection, _sender, _path, _interface, _signal, parameters):
        try:
            state = json.loads(parameters.unpack()[0])
        except (ValueError, TypeError, IndexError):
            return
        max_x, max_y = int(state.get("touchpad_max_x") or 0), int(state.get("touchpad_max_y") or 0)
        if max_x > 0 and max_y > 0:
            self._touchpad_size = (max_x, max_y)
        if state.get("point"):
            self._calibration_points.append(tuple(state["point"]))
            self._calibration_points = self._calibration_points[-400:]
        elif state.get("points") == 0:
            # A stroke was evaluated; the next one starts a fresh trace.
            self._calibration_points = []

        active = bool(state.get("active"))
        self._show_calibration_buttons("cancel" if active else "start")
        step = f"Step {min(state.get('step_index', 0) + 1, state.get('steps', 2))} of {state.get('steps', 2)}"
        if active:
            self._calibration_row.set_title(f"{step}: {state.get('message', '')}")
            self._calibration_row.set_subtitle(state.get("error") or "Lift the finger when the circle is closed")
        elif state.get("done") and state.get("result"):
            self._apply_calibration(state["result"], state.get("layout") or "")
        else:
            self._calibration_row.set_title(state.get("error") or state.get("message") or "Calibration stopped")
            self._calibration_row.set_subtitle("Press Start to try again")
        if self._calibration_preview is not None:
            self._calibration_preview.queue_draw()

    def _show_calibration_buttons(self, which):
        """Show the buttons of one calibration stage: start, cancel or review."""
        self._calibration_start.set_visible(which == "start")
        self._calibration_cancel.set_visible(which == "cancel")
        self._calibration_keep.set_visible(which == "review")
        self._calibration_revert.set_visible(which == "review")

    def _apply_calibration(self, result, layout):
        """Use the measured geometry live and ask whether to keep it."""
        if self._calibration_backup is None:
            self._calibration_backup = (copy.copy(self.config.geometry), self.config.device_name)
        geometry = copy.copy(self.config.geometry)
        for attribute in ("circle_diameter", "center_button_diameter", "center_x", "center_y"):
            setattr(geometry, attribute, int(result[attribute]))
        self._restore_geometry(geometry, layout or self.config.device_name)
        self._show_calibration_buttons("review")
        self._calibration_row.set_title(
            f"Measured: ring {result['circle_diameter']}, centre {result['center_button_diameter']} "
            f"at {result['center_x']} × {result['center_y']}"
        )
        self._calibration_row.set_subtitle(
            "Already in use — try the dial with a finger, then keep it or go back to the previous geometry (dashed)"
        )

    def _finish_calibration_review(self, keep, quiet=False):
        backup, self._calibration_backup = self._calibration_backup, None
        self._show_calibration_buttons("start")
        if backup is None:
            return
        if keep:
            self._calibration_row.set_title("Calibrated")
            self._calibration_row.set_subtitle("Press Start to measure again")
            if not quiet:
                self._toast("New dial geometry kept")
        else:
            self._restore_geometry(*backup)
            self._calibration_row.set_title("Previous geometry restored")
            self._calibration_row.set_subtitle("Press Start to measure again")
            if not quiet:
                self._toast("Calibration discarded")

    def _draw_calibration(self, _area, cr, width, height):
        max_x, max_y = self._touchpad_size
        margin = 14
        scale = min((width - 2 * margin) / max_x, (height - 2 * margin) / max_y)
        pad_w, pad_h = max_x * scale, max_y * scale
        left = (width - pad_w) / 2
        top = (height - pad_h) / 2
        geometry = self.config.geometry

        cr.save()
        radius = 14
        cr.new_sub_path()
        cr.arc(left + pad_w - radius, top + radius, radius, -math.pi / 2, 0)
        cr.arc(left + pad_w - radius, top + pad_h - radius, radius, 0, math.pi / 2)
        cr.arc(left + radius, top + pad_h - radius, radius, math.pi / 2, math.pi)
        cr.arc(left + radius, top + radius, radius, math.pi, 1.5 * math.pi)
        cr.close_path()
        cr.set_source_rgba(0.17, 0.175, 0.2, 1)
        cr.fill_preserve()
        cr.set_source_rgba(0.45, 0.42, 0.41, 1)
        cr.set_line_width(1)
        cr.stroke()

        cr.rectangle(left + pad_w - geometry.width * scale, top, geometry.width * scale, geometry.height * scale)
        cr.set_source_rgba(0.91, 0.73, 0.55, 0.35)
        cr.fill()

        cx = left + geometry.center_x * scale
        cy = top + geometry.center_y * scale
        cr.arc(cx, cy, geometry.circle_diameter / 2 * scale, 0, 2 * math.pi)
        cr.set_source_rgba(0.91, 0.73, 0.55, 0.18)
        cr.fill_preserve()
        cr.set_source_rgba(0.91, 0.73, 0.55, 0.9)
        cr.set_line_width(2)
        cr.stroke()
        cr.arc(cx, cy, geometry.center_button_diameter / 2 * scale, 0, 2 * math.pi)
        cr.set_source_rgba(0.97, 0.86, 0.75, 0.85)
        cr.set_line_width(1.5)
        cr.stroke()
        cr.move_to(cx - 6, cy)
        cr.line_to(cx + 6, cy)
        cr.move_to(cx, cy - 6)
        cr.line_to(cx, cy + 6)
        cr.stroke()

        if self._calibration_backup is not None:
            previous = self._calibration_backup[0]
            px = left + previous.center_x * scale
            py = top + previous.center_y * scale
            cr.set_dash([5, 4])
            cr.set_line_width(1.5)
            cr.set_source_rgba(0.73, 0.70, 0.67, 0.8)
            for diameter in (previous.circle_diameter, previous.center_button_diameter):
                cr.new_sub_path()
                cr.arc(px, py, diameter / 2 * scale, 0, 2 * math.pi)
            cr.stroke()
            cr.set_dash([])

        if self._calibration_points:
            cr.set_source_rgba(0.42, 0.71, 1.0, 0.95)
            for point_x, point_y in self._calibration_points:
                cr.arc(left + point_x * scale, top + point_y * scale, 2, 0, 2 * math.pi)
                cr.fill()

        cr.set_font_size(10)
        cr.set_source_rgba(0.73, 0.70, 0.67, 1)
        cr.move_to(left + 8, top + pad_h - 8)
        cr.show_text(f"Touchpad {max_x} × {max_y}")
        cr.restore()

    # --- Driver -----------------------------------------------------------------------

    # --- SpaceMouse -------------------------------------------------------------------

    def _build_spacemouse_page(self):
        content = self._page_box()
        content.append(self._spacemouse_group())
        self._sm_group = Adw.PreferencesGroup(
            title="Buttons",
            description=(
                "What each key of the SpaceMouse does. A key you leave alone passes through to "
                "other applications. Click a key to change it."
            ),
        )
        self._sm_scope_row = Adw.ComboRow(
            title="Applies to", subtitle="A profile's own binding replaces the one of All applications",
            model=Gtk.StringList.new(["All applications"]),
        )
        self._sm_scope_handler = self._sm_scope_row.connect("notify::selected", self._on_spacemouse_scope)
        self._sm_group.add(self._sm_scope_row)
        self._sm_group.add(self._spacemouse_layer_row())
        self._sm_identify_row = _button_row("Press a SpaceMouse button…", "input-gaming-symbolic")
        self._sm_identify_row.connect("activated", lambda _row: self._toggle_spacemouse_identify())
        self._sm_group.add(self._sm_identify_row)
        content.append(self._sm_group)
        self._sm_keys_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.append(self._sm_keys_box)
        content.append(self._spacemouse_motion_group())
        content.append(self._spacemouse_blender_group())
        content.append(self._spacemouse_cap_group())
        self._refresh_layer_row()
        self._start_spacemouse_poll()
        return self._scrolled_content(content)

    def _spacemouse_group(self):
        """Switch, status and the few settings that are not about a button."""
        settings = self.config.spacemouse
        group = Adw.PreferencesGroup(
            title="SpaceMouse mode",
            description=(
                "A 3Dconnexion SpaceMouse (through spacenavd) drives the dial: its buttons open the dial, "
                "tilting the cap picks, twisting turns, pushing the cap is the centre button."
            ),
        )
        options = []

        def enabled_changed(active):
            for row in options:
                row.set_sensitive(active)
            if settings.enabled != active:
                settings.enabled = active
                self._update_openers_hint()
                # The driver opens spacenavd at start.
                self._restart_driver()

        group.add(_switch_row(
            "SpaceMouse mode", "A SpaceMouse is the dial; restarts the driver", settings.enabled, enabled_changed,
        ))
        self._sm_status_row = Adw.ActionRow(title="Status", subtitle="Unknown")
        self._sm_status_row.set_subtitle_lines(0)
        group.add(self._sm_status_row)
        options.append(_switch_row(
            "Mute other applications",
            "While the dial is on, CAD programs get no motion from the cap",
            settings.mute_others, lambda active: self._apply_spacemouse_setting("mute_others", active),
        ))
        options.append(_switch_row(
            "Hide Turnpad's buttons from other applications",
            "Experimental: the buttons Turnpad uses do not reach other programs",
            settings.hide_buttons, lambda active: self._apply_spacemouse_setting("hide_buttons", active),
        ))
        options.append(_spin_row(
            "Long-press time", "Milliseconds a button is held to run its hold action",
            settings.hold_ms, 100, 3000, 50, 0,
            lambda value: self._apply_spacemouse_setting("hold_ms", int(value)),
        ))
        for row in options:
            row.set_sensitive(settings.enabled)
            group.add(row)
        return group

    def _apply_spacemouse_setting(self, attribute, value):
        """A setting the driver reads at start: restart it once the changes stop."""
        settings = self.config.spacemouse
        if getattr(settings, attribute) == value:
            return
        setattr(settings, attribute, value)
        if not settings.enabled:
            self._schedule_save()
            return
        if self._sm_restart_source:
            GLib.source_remove(self._sm_restart_source)

        def restart():
            self._sm_restart_source = 0
            self._restart_driver()
            return GLib.SOURCE_REMOVE

        self._sm_restart_source = GLib.timeout_add(900, restart)

    def _update_spacemouse_status(self, status):
        row = self._sm_status_row
        if row is None:
            return
        if status is None:
            row.set_subtitle("The driver is not running")
        elif status.get("spacemouse"):
            device = status.get("spacemouse_device")
            row.set_subtitle(f"Connected · {device}" if device else "Connected")
            if device and device != self._sm_device:
                # The key list follows the model of the device.
                self._sm_device = device
                self._rebuild_spacemouse_rows()
        elif not sm.socket_exists(self.config.spacemouse.socket):
            row.set_subtitle(
                "spacenavd is not running. Install the spacenavd package of your distribution "
                "(Arch: from the AUR) and start it: systemctl enable --now spacenavd"
            )
        elif self.config.spacemouse.enabled:
            row.set_subtitle("Not connected: spacenavd is running but no SpaceMouse answers")
        else:
            row.set_subtitle("Off")

    def _spacemouse_cap_group(self):
        """Which raw axis is tilt, twist and push (advanced)."""
        settings = self.config.spacemouse
        group = Adw.PreferencesGroup()
        expander = Adw.ExpanderRow(
            title="Cap and axes", subtitle="Advanced: which axis of the cap does what",
        )
        axes = list(sm.AXIS_LABELS)

        def axis_row(title, subtitle, get, set_value):
            row = _combo_row(title, subtitle, axes, min(max(int(get()), 0), len(axes) - 1), set_value)
            expander.add_row(row)

        def invert_row(title, get, set_value):
            expander.add_row(_switch_row(title, "", bool(get()), set_value))

        def set_pair(attribute, position, value):
            pair = list(getattr(settings, attribute))
            pair[position] = value
            self._apply_spacemouse_setting(attribute, pair)

        axis_row("Tilt left / right", "The axis that grows when the cap tilts right",
                 lambda: settings.tilt_axes[0], lambda index: set_pair("tilt_axes", 0, index))
        invert_row("Invert tilt left / right", lambda: settings.tilt_invert[0],
                   lambda active: set_pair("tilt_invert", 0, active))
        axis_row("Tilt forward / back", "The axis that grows when the cap tilts towards you",
                 lambda: settings.tilt_axes[1], lambda index: set_pair("tilt_axes", 1, index))
        invert_row("Invert tilt forward / back", lambda: settings.tilt_invert[1],
                   lambda active: set_pair("tilt_invert", 1, active))
        axis_row("Twist", "The axis that grows when the cap turns clockwise",
                 lambda: settings.twist_axis, lambda index: self._apply_spacemouse_setting("twist_axis", index))
        invert_row("Invert twist", lambda: settings.twist_invert,
                   lambda active: self._apply_spacemouse_setting("twist_invert", active))
        expander.add_row(_spin_row(
            "Twist speed", "Degrees per second at full twist",
            settings.twist_speed, 30, 1440, 30, 0,
            lambda value: self._apply_spacemouse_setting("twist_speed", int(value)),
        ))
        axis_row("Push", "The axis that is the centre button when the cap is pushed down",
                 lambda: settings.push_axis, lambda index: self._apply_spacemouse_setting("push_axis", index))
        invert_row("Invert push", lambda: settings.push_invert,
                   lambda active: self._apply_spacemouse_setting("push_invert", active))
        expander.add_row(_spin_row(
            "Full deflection", "Raw value of an axis pushed as far as it goes",
            settings.axis_max, 50, 5000, 50, 0,
            lambda value: self._apply_spacemouse_setting("axis_max", int(value)),
        ))
        group.add(expander)
        return group

    # The button list ---------------------------------------------------------------

    def _refresh_spacemouse_page(self):
        """Follow profiles that were added, renamed or removed."""
        if self._sm_group is None or self.config is None:
            return
        if not cx.scope_alive(self.config, self._sm_scope):
            self._sm_scope = None
        # All applications, then every profile followed by its contexts.
        entries = cx.scope_entries(self.config)
        labels = ["All applications"] + [label for label, _scope in entries]
        scopes = [scope for _label, scope in entries]
        self._sm_scope_row.handler_block(self._sm_scope_handler)
        self._sm_scope_row.set_model(Gtk.StringList.new(labels))
        self._sm_scope_row.set_selected(0 if self._sm_scope is None else scopes.index(self._sm_scope) + 1)
        self._sm_scope_row.handler_unblock(self._sm_scope_handler)
        self._refresh_layer_row()
        self._fill_motion()
        self._refresh_blender_group()
        self._rebuild_spacemouse_rows()

    def _on_spacemouse_scope(self, row, _param):
        index = row.get_selected()
        scopes = [scope for _label, scope in cx.scope_entries(self.config)] if self.config is not None else []
        scope = scopes[index - 1] if 1 <= index <= len(scopes) else None
        if scope != self._sm_scope:
            self._sm_scope = scope
            self._refresh_layer_row()
            self._fill_motion()
            self._rebuild_spacemouse_rows()

    def spacemouse_page_available(self):
        """Whether the SpaceMouse page is shown (it hides on a computer without a SpaceMouse)."""
        page = self._view_pages.get("spacemouse")
        return page is not None and page.get_visible()

    def edit_context_buttons(self, scope):
        """Open the SpaceMouse page with ``scope`` (a contexts.ContextScope) chosen."""
        self._sm_scope = scope
        self._refresh_spacemouse_page()
        if self._view_stack is not None:
            self._view_stack.set_visible_child_name("spacemouse")

    def _spacemouse_changed(self):
        """A button was edited: show it in the list and save."""
        self._rebuild_spacemouse_rows()
        self._schedule_save()

    def _spacemouse_maps(self):
        return sx.all_maps(self.config)

    def _spacemouse_model(self, maps):
        return sm.model_for_device(self._sm_device, sm.button_count(maps))

    def _spacemouse_key_rows(self):
        maps = self._spacemouse_maps()
        model = self._spacemouse_model(maps)
        return model, sm.key_rows(model, maps, self._sm_extra_buttons)

    def _spacemouse_row_text(self, key_row):
        """(subtitle, tooltip, dim) of a key's row under the chosen scope and layer."""
        return sx.row_info(self.config, self._sm_scope, self._sm_layer, key_row, self._sm_titles())

    def _rebuild_spacemouse_rows(self):
        box = self._sm_keys_box
        if box is None or self.config is None:
            return
        child = box.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            box.remove(child)
            child = following
        self._sm_rows = {}
        model, key_rows = self._spacemouse_key_rows()
        groups = {}
        for key_row in key_rows:
            if key_row.group not in groups:
                groups[key_row.group] = Adw.PreferencesGroup(title=sm.group_title(model, key_row.group))
                box.append(groups[key_row.group])
            text, tooltip, dim = self._spacemouse_row_text(key_row)
            row = Adw.ActionRow(title=GLib.markup_escape_text(key_row.title))
            row.set_title_lines(1)
            row.set_subtitle_lines(1)
            printed = Gtk.Label(label=key_row.label, width_chars=6, max_width_chars=8)
            printed.set_ellipsize(3)  # Pango.EllipsizeMode.END
            printed.add_css_class("dim-label")
            printed.add_css_class("caption")
            row.add_prefix(printed)
            row.set_subtitle(GLib.markup_escape_text(text))
            row.set_tooltip_text(tooltip)
            if dim:
                row.add_css_class("dim-label")
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            row.set_activatable(True)
            row.connect("activated", lambda _row, number=key_row.indexes[0]: self._show_spacemouse_button_dialog(number))
            groups[key_row.group].add(row)
            for index in key_row.indexes:
                self._sm_rows[index] = row

    # Identify by pressing ----------------------------------------------------------

    def _toggle_spacemouse_identify(self):
        if self._sm_probe is not None:
            self._stop_spacemouse_probe()
            return
        probe = sm.ButtonProbe(self.config.spacemouse.socket, self._on_spacemouse_pressed)
        self._sm_probe = probe
        self._sm_identify_row.set_title("Connecting to spacenavd… (click to cancel)")

        def started(ok):
            if self._sm_probe is not probe:
                return
            if not ok:
                self._stop_spacemouse_probe()
                self._toast("spacenavd is not reachable: install and start it first")
                return
            self._sm_identify_row.set_title("Press a button now… (click to cancel)")

        probe.start(started)

    def _stop_spacemouse_probe(self):
        if self._sm_probe is not None:
            self._sm_probe.stop()
            self._sm_probe = None
        if self._sm_identify_row is not None:
            self._sm_identify_row.set_title("Press a SpaceMouse button…")

    def _on_spacemouse_pressed(self, index):
        if index is None:
            self._stop_spacemouse_probe()
            self._toast("The connection to spacenavd was lost")
            return
        self._stop_spacemouse_probe()
        if not 0 <= index < sm.SPACEMOUSE_BUTTONS:
            return
        if index not in self._sm_rows:
            self._sm_extra_buttons.add(index)
            self._rebuild_spacemouse_rows()
        row = self._sm_rows[index]
        row.grab_focus()
        row.add_css_class("proart-pressed")
        GLib.timeout_add(1800, lambda: row.remove_css_class("proart-pressed") or GLib.SOURCE_REMOVE)
        self._toast(f"{sm.button_name(index, self._spacemouse_model(self._spacemouse_maps()))} · button {index}")

    # The editor of one button --------------------------------------------------------

    def _show_spacemouse_button_dialog(self, index):
        """Actions of one key, for all applications or one profile. A key the
        device itself tells a short from a long press (function keys, T, R…)
        has two raw buttons: a press action for each, no hold."""
        profile = self._sm_scope
        layer = self._sm_layer
        config = self.config
        model, key_rows = self._spacemouse_key_rows()
        key_row = sm.row_for_index(key_rows, index)
        indexes = key_row.indexes if key_row is not None else (index,)
        title = key_row.title if key_row is not None else f"Button {index}"
        if key_row is not None and key_row.dual:
            title = f"{title} · buttons {indexes[0]} and {indexes[1]}"
        elif key_row is not None and key_row.key is not None:
            title = f"{title} · button {index}"
        if layer is not None:
            title = f"{title} · layer “{layer}”"
        dialog = Adw.Dialog(title=title, content_width=560, content_height=700)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        view.set_content(page)
        dialog.set_child(view)

        profiles = [profile] if profile is not None else list(config.profiles)
        groups = sm.group_titles(profiles)
        functions = sm.function_titles(profiles)
        # Per raw button: the actions being edited, what was last saved (an
        # action that is not valid yet, hold keys without keys, is not saved,
        # the previous one stays) and the label.
        states = {}
        for number in indexes:
            current, _source = sx.current_binding(config, profile, layer, number)
            working = {
                "press": copy.deepcopy(current.press) if current else None,
                "hold": copy.deepcopy(current.hold) if current else None,
            }
            states[number] = {
                "working": working,
                "stored": {slot: copy.deepcopy(working[slot]) for slot in working},
                "label": current.label if current else "",
            }
        sections = []

        def commit(number):
            state = states[number]
            for slot in state["working"]:
                if sm.is_valid(state["working"][slot]):
                    state["stored"][slot] = copy.deepcopy(state["working"][slot])
            button = SpaceMouseButton(
                press=copy.deepcopy(state["stored"]["press"]), hold=copy.deepcopy(state["stored"]["hold"]),
                label=state["label"],
            )
            sx.store_binding(config, profile, layer, number, button)
            self._spacemouse_changed()

        if profile is not None or layer is not None:
            scope_group = Adw.PreferencesGroup()

            def override_toggled(active):
                for section in sections:
                    section.set_sensitive(active)
                for number in indexes:
                    if active:
                        commit(number)
                    else:
                        sx.drop_binding(config, profile, layer, number)
                self._spacemouse_changed()

            owned = any(sx.owns(config, profile, layer, number) for number in indexes)
            if layer is not None:
                where = f"the layer “{layer}”" + (f" for {profile.name}" if profile is not None else "")
                below = "what All applications or the base map says" if profile is not None else "the base map says"
                scope_group.add(_switch_row(
                    f"Own binding in {where}", f"Off: the key does what {below}", owned, override_toggled,
                ))
            else:
                scope_group.add(_switch_row(
                    f"Own binding for {profile.name}",
                    "Off: the key does what All applications says",
                    owned, override_toggled,
                ))
            page.add(scope_group)

        def add_section(number, slot, section_title, description, with_label):
            section = self._spacemouse_action_section(
                states[number]["working"], slot, section_title, description, groups, functions, number,
                lambda number=number: commit(number),
            )
            if with_label:
                label_row = Adw.EntryRow(title="Label on the screen (empty: automatic)")
                label_row.set_show_apply_button(True)
                label_row.set_text(states[number]["label"])

                def label_applied(row, number=number):
                    states[number]["label"] = row.get_text().strip()[:SPACEMOUSE_LABEL_MAX]
                    row.set_text(states[number]["label"])
                    commit(number)

                label_row.connect("apply", label_applied)
                section.add(label_row)
            sections.append(section)
            page.add(section)

        if key_row is not None and key_row.dual:
            add_section(indexes[0], "press", "Short press", "Runs when the key is tapped.", True)
            add_section(
                indexes[1], "press", "Long press",
                "The device decides: hold the key for about 1 s. Runs when it reports the long press.", True,
            )
        else:
            number = indexes[0]
            add_section(
                number, "press", "Press",
                "Runs when the button goes down; a modifier stays held until it is released.", True,
            )
            add_section(
                number, "hold", "Hold", "Runs instead when the button stays down for the long-press time.", False,
            )
        if (profile is not None or layer is not None) and not owned:
            for section in sections:
                section.set_sensitive(False)

        if profile is None and layer is None:
            reset_group = Adw.PreferencesGroup()
            reset = _button_row("Reset to default", "edit-undo-symbolic")

            def reset_button(_row):
                for number in indexes:
                    sm.reset_global_button(config, number)
                self._spacemouse_changed()
                dialog.close()

            reset.connect("activated", reset_button)
            reset_group.add(reset)
            page.add(reset_group)
        dialog.present(self._window)

    def _spacemouse_action_section(self, working, slot, title, description, groups, functions, index, commit):
        """One of Press / Hold: the action type and the fields of the chosen type."""
        group = Adw.PreferencesGroup(title=title, description=description)
        choices = [item for item in sm.supported_choices() if slot == "press" or item[0] not in sm.PRESS_ONLY]
        choice_keys = [item[0] for item in choices]
        type_row = Adw.ComboRow(title="Action", model=Gtk.StringList.new([label for _key, label in choices]))
        group.add(type_row)
        syncing = {"active": True}
        holder = type("StepList", (), {"macro_center": [], "title": title})()

        keys_row = Adw.EntryRow(title="Keys, e.g. Ctrl+Tab")
        keys_row.set_show_apply_button(True)
        group.add(keys_row)

        steps_row = Adw.ExpanderRow(title="Sequence of actions", subtitle="Runs instead of the keys above")
        group.add(steps_row)
        step_rows = []

        def rebuild_steps():
            for row in step_rows:
                steps_row.remove(row)
            step_rows.clear()
            for position, step in enumerate(holder.macro_center):
                row = self._step_row(holder, "macro_center", position, step, steps_changed, f"Button {index} · {title}")
                steps_row.add_row(row)
                step_rows.append(row)
            add = _button_row("Add an action", "list-add-symbolic")
            add.connect("activated", lambda _row: self._show_step_dialog(
                holder, "macro_center", None, steps_changed, f"Button {index} · {title}"))
            steps_row.add_row(add)
            step_rows.append(add)
            steps_row.set_subtitle(
                f"{len(holder.macro_center)} action{'s' if len(holder.macro_center) != 1 else ''}, run instead of the keys"
                if holder.macro_center else "Optional: several actions in a row instead of one shortcut"
            )

        def steps_changed():
            action = working[slot]
            if action is not None and action.type == "macro":
                action.macro = holder.macro_center
            rebuild_steps()
            commit()

        group_row = Adw.ComboRow(title="Group")
        function_by_row = Adw.ComboRow(title="Choose by", model=Gtk.StringList.new(["Name", "Position in the menu"]))
        function_row = Adw.ComboRow(title="Function")
        slot_row = Adw.SpinRow.new_with_range(1, 12, 1)
        slot_row.set_title("Position in the menu")
        slot_row.set_subtitle("1 is the first entry of the dial's menu; a group opens its list")
        for row in (group_row, function_by_row, function_row, slot_row):
            group.add(row)
        extras = self._sm_extra_action_rows(group, working, slot, commit, syncing)
        group_names = [""] + groups
        function_names = list(functions)

        def sync_rows():
            """Show the working action in the rows without committing."""
            syncing["active"] = True
            action = working[slot]
            type_row.set_selected(max(choice_keys.index(sm.choice_of(action)) if sm.choice_of(action) in choice_keys else 0, 0))
            keys_row.set_text(keys_to_text(action.keys) if action is not None and action.keys else "")
            keys_row.remove_css_class("error")
            holder.macro_center = action.macro if action is not None and action.macro is not None else []
            rebuild_steps()
            if action is not None and action.type == "macro":
                steps_row.set_expanded(bool(holder.macro_center))
            named = action.group if action is not None and action.type == "group" else None
            names = list(group_names)
            if named and named not in names:
                names.append(named)
            group_row.set_model(Gtk.StringList.new(["First group of the profile"] + names[1:]))
            group_row.set_selected(names.index(named) if named else 0)
            group_row._names = names
            by_name = action is not None and action.type == "function" and action.function is not None
            function_by_row.set_selected(0 if by_name or action is None or action.slot is None else 1)
            chosen = action.function if by_name else None
            fnames = list(function_names)
            if chosen and chosen not in fnames:
                fnames.append(chosen)
            function_row.set_model(Gtk.StringList.new(fnames))
            function_row.set_selected(fnames.index(chosen) if chosen else 0)
            function_row._names = fnames
            slot_row.set_value((action.slot if action is not None and action.slot is not None else 0) + 1)
            extras.sync()
            show_fields()
            syncing["active"] = False

        def show_fields():
            action = working[slot]
            kind = action.type if action is not None else "none"
            keys_row.set_visible(kind in ("macro", "key_hold", "app"))
            extras.show(kind)
            steps_row.set_visible(kind == "macro")
            group_row.set_visible(kind == "group")
            function_by_row.set_visible(kind == "function")
            by_slot = function_by_row.get_selected() == 1
            function_row.set_visible(kind == "function" and not by_slot)
            slot_row.set_visible(kind == "function" and by_slot)
            if kind == "key_hold" and not sm.is_valid(action):
                # Not saved until it has keys: the previous action stays.
                keys_row.set_title("Type the keys to hold, then Apply (not saved yet)")
                keys_row.add_css_class("error")
            else:
                keys_row.set_title(
                    "Keys held while the button is down, e.g. Ctrl+Shift" if kind == "key_hold"
                    else "Keys to press when the application is not linked, e.g. E" if kind == "app"
                    else "Keys, e.g. Ctrl+Tab"
                )
                keys_row.remove_css_class("error")

        def type_changed(row, _param):
            if syncing["active"]:
                return
            choice = choice_keys[row.get_selected()]
            action = sm.new_action(choice, working[slot], sx.action_layer_names(self.config, self._sm_scope))
            if action is not None and action.type == "function" and action.function is None and action.slot is None:
                action.slot = 0
            if action is not None and action.type == "macro" and action.macro is None:
                action.macro = []
            working[slot] = action
            sync_rows()
            if sm.is_valid(action):
                commit()

        def keys_applied(row):
            action = working[slot]
            if action is None or syncing["active"]:
                return
            keys, unknown = parse_keys(row.get_text())
            if unknown:
                row.add_css_class("error")
                self._toast(f"Unknown key: {', '.join(unknown)}")
                return
            row.remove_css_class("error")
            if action.type == "key_hold" and not keys:
                # The previous keys stay; the row says so.
                row.add_css_class("error")
                self._toast("Hold keys needs at least one key")
                return
            action.keys = keys or None
            show_fields()
            commit()

        def group_changed(row, _param):
            action = working[slot]
            if action is None or action.type != "group" or syncing["active"]:
                return
            names = group_row._names
            position = row.get_selected()
            action.group = names[position] or None if 0 <= position < len(names) else None
            commit()

        def function_changed(*_args):
            action = working[slot]
            if action is None or action.type != "function" or syncing["active"]:
                return
            if function_by_row.get_selected() == 1:
                action.function, action.slot = None, int(slot_row.get_value()) - 1
            else:
                names = function_row._names
                position = function_row.get_selected()
                action.function = names[position] if 0 <= position < len(names) else None
                action.slot = None if action.function else 0
            show_fields()
            commit()

        type_row.connect("notify::selected", type_changed)
        keys_row.connect("apply", keys_applied)
        group_row.connect("notify::selected", group_changed)
        function_by_row.connect("notify::selected", function_changed)
        function_row.connect("notify::selected", function_changed)
        slot_row.connect("notify::value", function_changed)
        extras.resync = sync_rows
        sync_rows()
        return group

    # --- Screen -----------------------------------------------------------------------

    def _build_screen_page(self):
        content = self._page_box()
        content.append(self._screen_status_group())
        content.append(self._screen_preview_group())
        self._screen_look_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.append(self._screen_look_box)
        self._screen_pages_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.append(self._screen_pages_box)
        content.append(self._screen_app_group())
        content.append(self._screen_keys_group())
        self._fill_screen_look()
        self._fill_screen_pages()
        self._fill_screen_app()
        return self._scrolled_content(content)

    def _screen_titles(self):
        """Names of the pages: the preview module's when the screen package has one."""
        try:
            from screen.preview import PAGE_TITLES
            return PAGE_TITLES
        except Exception:  # the package or its libraries may be missing
            return sp.PAGE_TITLES

    def _screen_model(self):
        return self._screen_present[0].model if self._screen_present else SPACEPILOT_PRO

    # Visibility and status ----------------------------------------------------------

    def _refresh_screen_presence(self):
        """The page is shown while a supported screen is plugged in, or settings
        for it exist (they stay reachable while it is unplugged)."""
        if self.config is None or self._screen_page is None:
            return
        self._screen_present = screens_present()
        show = (
            bool(self._screen_present) or sp.settings_customised(self.config)
            or self._app_settings["show_all_pages"]
        )
        if self._screen_page.get_visible() != show:
            self._screen_page.set_visible(show)

    def _screen_status_group(self):
        settings = self.config.screen
        group = Adw.PreferencesGroup(
            title="Screen",
            description=(
                "The colour screen of a 3Dconnexion SpacePilot Pro shows Turnpad: what the function keys do, "
                "the live dial, the buttons and the motion of the cap. The settings apply at once."
            ),
        )
        rows = self._screen_status_rows
        rows["device"] = Adw.ActionRow(title="SpaceMouse", subtitle="Unknown")
        rows["service"] = Adw.ActionRow(title="Screen service", subtitle="Unknown")
        for row in (rows["device"], rows["service"]):
            row.set_subtitle_lines(0)
            group.add(row)
        rows["usb"] = Adw.ActionRow(
            title="pyusb is not installed",
            subtitle=(
                "The screen service needs the Python USB library: python-pyusb (Arch), python3-usb "
                "(Debian, Ubuntu) or python3-pyusb (Fedora)."
            ),
        )
        rows["usb"].set_subtitle_lines(0)
        rows["usb"].add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
        rows["usb"].set_visible(sp.pyusb_missing())
        group.add(rows["usb"])
        rows["enabled"] = _switch_row(
            "Show Turnpad on the screen", "Starts or stops the screen service", settings.enabled, self._on_screen_enabled,
        )
        group.add(rows["enabled"])
        return group

    def _refresh_screen_status(self):
        rows = self._screen_status_rows
        if not rows:
            return
        rows["device"].set_subtitle(sp.connection_text(self._screen_present))
        rows["usb"].set_visible(sp.pyusb_missing())
        rows["service"].set_subtitle("Checking…")
        self._screen_service_state(lambda output: rows["service"].set_subtitle(sp.service_state_text(output)))

    @staticmethod
    def _screen_service_state(done):
        """``done(text)`` with what ``systemctl --user is-active`` says ("" = could not ask); never blocks."""
        try:
            process = Gio.Subprocess.new(
                ["systemctl", "--user", "is-active", SCREEN_SERVICE],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE,
            )
        except GLib.Error:
            done("")
            return

        def finished(process, result):
            try:
                _ok, output, _errors = process.communicate_utf8_finish(result)
            except GLib.Error:
                output = ""
            done(output or "")

        process.communicate_utf8_async(None, None, finished)

    def _on_screen_enabled(self, active):
        settings = self.config.screen
        if settings.enabled == active:
            return
        settings.enabled = active
        self._screen_sync.mark("enabled")
        # The service reads the flag when it starts: the file must be written first.
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_config()
        if active and not self._screen_present:
            return
        verb = "start" if active else "stop"

        def finished(ok):
            if not ok:
                self._toast(f"Could not {verb} {SCREEN_SERVICE}")
            GLib.timeout_add(1200, lambda: self._refresh_screen_status() or GLib.SOURCE_REMOVE)

        self._spawn(["systemctl", "--user", verb, SCREEN_SERVICE], f"Could not {verb} {SCREEN_SERVICE}", finished)

    # Preview ------------------------------------------------------------------------

    def _screen_preview_group(self):
        group = Adw.PreferencesGroup(title="Preview", description="What the screen shows, for the application chosen below.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        navigation = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.CENTER)
        navigation.append(_icon_button("go-previous-symbolic", "Previous page", lambda: self._step_screen_preview(-1)))
        self._screen_preview_title = Gtk.Label(label="", width_chars=18)
        self._screen_preview_title.add_css_class("heading")
        navigation.append(self._screen_preview_title)
        navigation.append(_icon_button("go-next-symbolic", "Next page", lambda: self._step_screen_preview(1)))
        box.append(navigation)
        self._screen_preview = Gtk.Picture()
        self._screen_preview.set_can_shrink(True)
        self._screen_preview.set_content_fit(Gtk.ContentFit.CONTAIN)
        self._screen_preview.set_hexpand(True)
        # The clamp stops the picture at its own size (640 px); narrower, it shrinks.
        self._screen_preview_frame = _ScreenPicture(self._screen_preview)
        self._screen_preview_frame.set_visible(False)
        clamp = Adw.Clamp(maximum_size=640, tightening_threshold=640)
        clamp.set_child(self._screen_preview_frame)
        box.append(clamp)
        self._screen_preview_note = Gtk.Label(label="", wrap=True, xalign=0)
        self._screen_preview_note.add_css_class("dim-label")
        box.append(self._screen_preview_note)
        group.add(box)
        return group

    def _step_screen_preview(self, delta):
        order = sp.page_order(sp.effective_pages(self.config, self._screen_scope))
        if self._screen_preview_page in order:
            position = order.index(self._screen_preview_page)
        else:
            position = 0
        self._screen_preview_page = order[(position + delta) % len(order)]
        self._queue_screen_preview(immediately=True)

    def _queue_screen_preview(self, immediately=False):
        """Render the preview again, a moment after the last change."""
        if self._screen_preview is None:
            return
        if self._screen_preview_source:
            GLib.source_remove(self._screen_preview_source)
            self._screen_preview_source = 0
        if immediately:
            self._render_screen_preview()
        else:
            self._screen_preview_source = GLib.timeout_add(150, self._render_screen_preview)

    def _render_screen_preview(self):
        self._screen_preview_source = 0
        picture, note = self._screen_preview, self._screen_preview_note
        if picture is None or self.config is None:
            return GLib.SOURCE_REMOVE
        # Nobody sees it: it is rendered when the page comes up.
        if self._view_stack is not None and self._view_stack.get_visible_child_name() != "screen":
            return GLib.SOURCE_REMOVE
        order = sp.page_order(sp.effective_pages(self.config, self._screen_scope))
        if self._screen_preview_page not in order:
            self._screen_preview_page = order[0]
        page = self._screen_preview_page
        title = sp.page_title(page, self._screen_titles())
        self._screen_preview_title.set_label(title if page in sp.effective_pages(self.config, self._screen_scope)
                                             else f"{title} (hidden)")
        try:
            from screen.preview import render
            profile = self._screen_scope
            surface = render(self.config, profile.name if profile is not None else None, page, scale=2)
            surface.flush()
            texture = Gdk.MemoryTexture.new(
                surface.get_width(), surface.get_height(), Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                GLib.Bytes.new(bytes(surface.get_data())), surface.get_stride(),
            )
        except ImportError:
            self._screen_preview_frame.set_visible(False)
            note.set_label("The preview is not available: the screen package is not installed.")
            note.set_visible(True)
            return GLib.SOURCE_REMOVE
        except Exception as error:  # a page that fails to draw must not break the settings
            self._screen_preview_frame.set_visible(False)
            note.set_label(f"The preview failed: {error}")
            note.set_visible(True)
            return GLib.SOURCE_REMOVE
        picture.set_paintable(texture)
        self._screen_preview_frame.set_visible(True)
        note.set_visible(False)
        return GLib.SOURCE_REMOVE

    # Look and power -----------------------------------------------------------------

    def _set_screen(self, attribute, value):
        """Change a setting of config.screen: saved, and the screen service takes it from the file."""
        settings = self.config.screen
        if getattr(settings, attribute) == value:
            return
        setattr(settings, attribute, value)
        self._screen_sync.mark(attribute)
        self._schedule_save()
        self._queue_screen_preview()

    def _screen_choice_row(self, title, subtitle, choices, attribute):
        value = getattr(self.config.screen, attribute)
        choices = sp.choices_with(choices, value)
        return _combo_row(
            title, subtitle, [label for _value, label in choices], sp.choice_index(choices, value),
            lambda index: self._set_screen(attribute, sp.choice_value(choices, index)),
        )

    def _fill_screen_look(self):
        box = self._screen_look_box
        _clear_children(box)
        settings = self.config.screen
        look = Adw.PreferencesGroup(title="Look")
        look.add(_scale_row("Brightness", "", settings.brightness, lambda value: self._set_screen("brightness", value)))
        look.add(self._screen_choice_row("Theme", "Dial skin uses the colours of the dial", sp.THEME_CHOICES, "theme"))
        look.add(self._screen_choice_row("Text size", "", sp.TEXT_SIZE_CHOICES, "text_size"))
        look.add(_switch_row(
            "Long-press labels", "Show what a long press does on the keys page", settings.show_long,
            lambda active: self._set_screen("show_long", active),
        ))
        look.add(_switch_row(
            "24-hour clock", "", settings.clock_24h, lambda active: self._set_screen("clock_24h", active),
        ))
        look.add(_switch_row(
            "Press feedback", "Show the action of a pressed button for a moment", settings.press_feedback,
            lambda active: self._set_screen("press_feedback", active),
        ))
        box.append(look)

        power = Adw.PreferencesGroup(title="Power", description="Pressing any button wakes the screen.")
        dim_brightness = _scale_row(
            "Dimmed brightness", "", settings.dim_brightness, lambda value: self._set_screen("dim_brightness", value),
        )
        dim_brightness.set_sensitive(settings.dim_after_s > 0)

        def dim_after_changed(seconds):
            self._set_screen("dim_after_s", seconds)
            dim_brightness.set_sensitive(seconds > 0)

        dim_choices = sp.choices_with(sp.DIM_AFTER_CHOICES, settings.dim_after_s)
        power.add(_combo_row(
            "Dim after", "When nothing is pressed", [label for _value, label in dim_choices],
            sp.choice_index(dim_choices, settings.dim_after_s),
            lambda index: dim_after_changed(sp.choice_value(dim_choices, index)),
        ))
        power.add(dim_brightness)
        power.add(self._screen_choice_row("Screen off after", "", sp.OFF_AFTER_CHOICES, "off_after_s"))
        power.add(_switch_row(
            "Wake on cap motion", "Moving the cap wakes the screen too", settings.wake_on_motion,
            lambda active: self._set_screen("wake_on_motion", active),
        ))
        box.append(power)

    # Pages --------------------------------------------------------------------------

    def _fill_screen_pages(self):
        box = self._screen_pages_box
        _clear_children(box)
        settings = self.config.screen
        titles = self._screen_titles()
        pages = sp.shown_pages(settings.pages) or list(SCREEN_PAGES)
        group = Adw.PreferencesGroup(
            title="Pages", description="Which pages the screen shows and in what order. The Menu key of the screen lists them.",
        )
        self._screen_page_buttons = {}
        order = sp.page_order(pages)
        for position, page in enumerate(order):
            shown = page in pages
            row = Adw.ActionRow(title=sp.page_title(page, titles), subtitle=sp.PAGE_HINTS.get(page, ""))
            row.set_subtitle_lines(1)
            switch = Gtk.Switch(active=shown, valign=Gtk.Align.CENTER)
            switch.connect("notify::active", lambda switch, _param, page=page: self._on_screen_page_toggled(page, switch))
            up = _icon_button("go-up-symbolic", "Move up", lambda page=page: self._move_screen_page(page, -1))
            down = _icon_button("go-down-symbolic", "Move down", lambda page=page: self._move_screen_page(page, 1))
            up.set_sensitive(shown and pages.index(page) > 0)
            down.set_sensitive(shown and pages.index(page) < len(pages) - 1)
            self._screen_page_buttons[(page, -1)] = up
            self._screen_page_buttons[(page, 1)] = down
            row.add_suffix(up)
            row.add_suffix(down)
            row.add_suffix(switch)
            row.set_activatable_widget(switch)
            group.add(row)
        start_labels = [sp.page_title(page, titles) for page in pages]
        group.add(_combo_row(
            "Start page", "Shown first and after the screen wakes", start_labels,
            pages.index(sp.fix_start_page(pages, settings.start_page)),
            lambda index: self._set_screen("start_page", pages[index]) if index < len(pages) else None,
        ))
        group.add(_switch_row(
            "Show the dial while it is on", "The dial page appears when the dial turns on, then the previous page",
            settings.follow_dial, lambda active: self._set_screen("follow_dial", active),
        ))
        box.append(group)

    def _set_screen_pages(self, pages, focus=None):
        settings = self.config.screen
        pages = sp.shown_pages(pages)
        if not pages:
            return
        settings.pages = pages
        settings.start_page = sp.fix_start_page(pages, settings.start_page)
        self._screen_sync.mark("pages", "start_page")
        self._schedule_save()

        def refill():
            self._fill_screen_pages()
            self._fill_screen_app()
            if focus in self._screen_page_buttons:
                self._screen_page_buttons[focus].grab_focus()
            return GLib.SOURCE_REMOVE

        # Not inside the signal of a widget that is replaced.
        GLib.idle_add(refill)
        self._queue_screen_preview()

    def _on_screen_page_toggled(self, page, switch):
        settings = self.config.screen
        pages = sp.shown_pages(settings.pages) or list(SCREEN_PAGES)
        changed = sp.toggle_page(pages, page, switch.get_active())
        if changed == pages:
            if (page in pages) != switch.get_active():
                # The last page cannot be hidden.
                GLib.idle_add(lambda: self._fill_screen_pages() or GLib.SOURCE_REMOVE)
            return
        self._set_screen_pages(changed)

    def _move_screen_page(self, page, delta):
        pages = sp.shown_pages(self.config.screen.pages) or list(SCREEN_PAGES)
        moved = sp.move_page(pages, page, delta)
        if moved != pages:
            self._set_screen_pages(moved, focus=(page, delta))

    # Per application ----------------------------------------------------------------

    def _screen_app_group(self):
        """The "Applies to" row (kept) and the box under it that is filled again per scope."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        group = Adw.PreferencesGroup(
            title="Per application",
            description="Labels of the function keys and, for an application, its own start page, pages and colour.",
        )
        self._screen_scope_row = Adw.ComboRow(
            title="Applies to", subtitle="Pages and colour follow the focused application",
            model=Gtk.StringList.new(["All applications"]),
        )
        self._screen_scope_handler = self._screen_scope_row.connect("notify::selected", self._on_screen_scope)
        group.add(self._screen_scope_row)
        box.append(group)
        self._screen_app_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.append(self._screen_app_box)
        return box

    def _refresh_screen_page(self):
        """Follow profiles that were added, renamed or removed."""
        if self._screen_app_box is None or self.config is None:
            return
        if self._screen_scope is not None and self._screen_scope not in self.config.profiles:
            self._screen_scope = None
        labels = ["All applications"] + [
            profile.name if profile.app_id is None else f"{profile.name}  ·  app" for profile in self.config.profiles
        ]
        row = self._screen_scope_row
        row.handler_block(self._screen_scope_handler)
        row.set_model(Gtk.StringList.new(labels))
        row.set_selected(0 if self._screen_scope is None else self.config.profiles.index(self._screen_scope) + 1)
        row.handler_unblock(self._screen_scope_handler)
        self._fill_screen_app()
        self._queue_screen_preview()

    def _on_screen_scope(self, row, _param):
        index = row.get_selected()
        profiles = self.config.profiles if self.config is not None else []
        scope = profiles[index - 1] if 1 <= index <= len(profiles) else None
        if scope is not self._screen_scope:
            self._screen_scope = scope
            self._fill_screen_app()
            self._queue_screen_preview()

    def _screen_override_changed(self):
        # Not inside the signal of a widget that is replaced.
        GLib.idle_add(lambda: self._fill_screen_app() or GLib.SOURCE_REMOVE)
        self._schedule_save()
        self._queue_screen_preview()

    def _fill_screen_app(self):
        box = self._screen_app_box
        if box is None or self.config is None:
            return
        _clear_children(box)
        profile = self._screen_scope
        titles = self._screen_titles()
        if profile is not None:
            group = Adw.PreferencesGroup(title=f"Screen in {profile.name}")
            effective = sp.effective_pages(self.config, profile)
            # Start page: the profile's own, or the one of all applications.
            current = profile.screen.get("start_page")
            options = list(effective) + ([current] if current and current not in effective else [])
            group.add(_combo_row(
                "Start page", "Shown first when this application is active",
                ["Same as all applications"] + [sp.page_title(page, titles) for page in options],
                0 if current is None else 1 + options.index(current),
                lambda index: self._set_screen_override(profile, "start_page", options[index - 1] if index else None),
            ))
            own = "pages" in profile.screen
            group.add(_switch_row(
                "Own pages", "Off: the pages of All applications", own,
                lambda active: self._set_screen_override(
                    profile, "pages", sp.shown_pages(self.config.screen.pages) or list(SCREEN_PAGES) if active else None),
            ))
            if own:
                for page in SCREEN_PAGES:
                    group.add(_switch_row(
                        sp.page_title(page, titles), "", page in profile.screen["pages"],
                        lambda active, page=page: self._toggle_screen_override_page(profile, page, active),
                    ))
            group.add(self._screen_accent_row(profile))
            box.append(group)
        box.append(self._screen_labels_group(profile))

    def _set_screen_override(self, profile, key, value):
        before = dict(profile.screen)
        sp.set_override(profile, key, value)
        if profile.screen != before:
            self._screen_override_changed()

    def _toggle_screen_override_page(self, profile, page, active):
        pages = sp.shown_pages(profile.screen.get("pages"))
        changed = sp.toggle_page(pages, page, active, reference=self.config.screen.pages)
        if changed == pages:
            # The last page cannot be hidden: show the switch as it was.
            GLib.idle_add(lambda: self._fill_screen_app() or GLib.SOURCE_REMOVE)
            return
        sp.set_override(profile, "pages", changed)
        self._screen_override_changed()

    @staticmethod
    def _colour_button(rgba, changed):
        """A colour chooser button (Gtk.ColorDialogButton, else Gtk.ColorButton); returns
        (button, set_colour) where set_colour changes it without calling ``changed(hex)``."""
        quiet = {"on": False}
        if hasattr(Gtk, "ColorDialogButton"):
            button = Gtk.ColorDialogButton.new(Gtk.ColorDialog(with_alpha=False))
            button.set_rgba(rgba)
            button.connect("notify::rgba", lambda button, _param: None if quiet["on"] else changed(_rgba_to_hex(button.get_rgba())))
        else:
            button = Gtk.ColorButton.new_with_rgba(rgba)
            button.connect("color-set", lambda button: None if quiet["on"] else changed(_rgba_to_hex(button.get_rgba())))
        button.set_valign(Gtk.Align.CENTER)

        def set_colour(colour):
            quiet["on"] = True
            new = Gdk.RGBA()
            new.parse(colour)
            button.set_rgba(new)
            quiet["on"] = False

        return button, set_colour

    def _screen_accent_row(self, profile):
        row = Adw.ActionRow(title="Accent colour", subtitle="Colours the screen while this application is active")
        accent = profile.screen.get("accent")
        rgba = Gdk.RGBA()
        rgba.parse(accent or self.config.overlay.accent or DEFAULT_ACCENT)
        reset = _icon_button("edit-undo-symbolic", "Back to the dial's accent colour", lambda: None)
        reset.set_sensitive(accent is not None)

        def chosen(colour):
            sp.set_override(profile, "accent", colour)
            reset.set_sensitive(True)
            self._schedule_save()
            self._queue_screen_preview()

        button, set_colour = self._colour_button(rgba, chosen)

        def reset_clicked():
            sp.set_override(profile, "accent", None)
            set_colour(self.config.overlay.accent or DEFAULT_ACCENT)
            reset.set_sensitive(False)
            self._schedule_save()
            self._queue_screen_preview()

        reset.connect("clicked", lambda _button: reset_clicked())
        row.add_suffix(reset)
        row.add_suffix(button)
        return row

    def _screen_labels_group(self, profile):
        """Text for the function keys on the screen instead of the automatic one."""
        group = Adw.PreferencesGroup()
        expander = Adw.ExpanderRow(
            title="Function key labels",
            subtitle="Text under each key on the screen. Empty: automatic, from what the key does.",
        )
        model = self._screen_model()
        for key in sp.function_keys(model):
            for index, kind in ((key.index, "short press"), (key.long_index, "long press")):
                if index is None:
                    continue
                entry = Adw.EntryRow(title=f"{key.name} · {kind}")
                entry.set_show_apply_button(True)
                entry.set_text(sp.label_of(self.config, profile, index))
                automatic = sp.auto_text(self.config, profile, index)
                entry.set_tooltip_text(f"Automatic: {automatic}")
                try:
                    entry.get_delegate().set_placeholder_text(automatic)
                except (AttributeError, TypeError):
                    pass
                entry.connect("apply", lambda entry, index=index: self._apply_screen_label(entry, profile, index))
                expander.add_row(entry)
        group.add(expander)
        return group

    def _apply_screen_label(self, entry, profile, index):
        text = sp.clean_label(entry.get_text())
        entry.set_text(text)
        if sp.set_label(self.config, profile, index, text):
            self._rebuild_spacemouse_rows()
            self._schedule_save()
            self._queue_screen_preview()

    def _screen_keys_group(self):
        group = Adw.PreferencesGroup(title="Keys around the screen")
        for key, text in sp.SCREEN_KEY_HELP:
            row = Adw.ActionRow(title=key, subtitle=text)
            row.set_subtitle_lines(1)
            group.add(row)
        return group

    # An external change (the screen service saves quick settings) ----------------------

    def _watch_config_file(self):
        try:
            self._screen_monitor = Gio.File.new_for_path(str(CONFIG_FILE)).monitor_file(Gio.FileMonitorFlags.NONE, None)
        except GLib.Error:
            self._screen_monitor = None
            return
        self._screen_monitor.connect("changed", self._on_config_file_changed)

    def _on_config_file_changed(self, _monitor, _file, _other, event):
        if event not in (
            Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.RENAMED, Gio.FileMonitorEvent.MOVED_IN,
        ):
            return
        if self._screen_reload_source:
            GLib.source_remove(self._screen_reload_source)
        self._screen_reload_source = GLib.timeout_add(400, self._reload_screen_from_disk)

    def _reload_screen_from_disk(self):
        """Take the ``screen`` section another program saved. Fields changed in this
        window and not saved yet stay as they are (they are written with the next save)."""
        self._screen_reload_source = 0
        self._adopt_screen_from_disk()
        return GLib.SOURCE_REMOVE

    def _adopt_screen_from_disk(self):
        """Read ``config.screen`` from the file again, keeping what this window changed.
        Returns whether the settings changed (the page is filled again then)."""
        if self.config is None:
            return False
        stored = sp.read_screen(CONFIG_FILE)
        if stored is None:
            return False
        adopted = self._screen_sync.adopt(self.config.screen, stored)
        if adopted is None:
            return False
        self.config.screen = adopted
        self._fill_screen_look()
        self._fill_screen_pages()
        self._fill_screen_app()
        self._queue_screen_preview()
        self._refresh_screen_presence()
        return True

    def _on_window_active(self, window, _param):
        if window.is_active():
            self._adopt_screen_from_disk()

    def _build_diagnostics_page(self):
        content = self._page_box()
        status = Adw.PreferencesGroup(title="Driver status")
        rows = {}
        for key, title in (
            ("service", "D-Bus service"),
            ("enabled", "DialPad"),
            ("application", "Focused application"),
            ("profile", "Active profile"),
            ("function", "Selected function"),
            ("environment", "Environment"),
            ("touchpad", "Mode"),
            ("configuration", "Configuration"),
        ):
            row = Adw.ActionRow(title=title, subtitle="Unknown")
            status.add(row)
            rows[key] = row
        self._diagnostics_rows = rows

        refresh = _icon_button("view-refresh-symbolic", "Refresh", self._refresh_diagnostics)
        status.set_header_suffix(refresh)
        content.append(status)
        content.append(self._devices_group())
        content.append(self._emulation_group())
        content.append(self._mouse_group())
        self._openers_group = Adw.PreferencesGroup()
        hint = Adw.ActionRow(title=hw.TWO_OPENERS_HINT)
        hint.set_title_lines(0)
        hint.add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
        self._openers_group.add(hint)
        content.append(self._openers_group)
        self._update_openers_hint()

        tools = Adw.PreferencesGroup(title="Tools")
        reload_row = Adw.ActionRow(title="Reload configuration", subtitle="The driver normally reloads after every change")
        reload_button = Gtk.Button(label="Reload")
        reload_button.set_valign(Gtk.Align.CENTER)
        reload_button.connect("clicked", lambda _button: self._reload_driver())
        reload_row.add_suffix(reload_button)
        tools.add(reload_row)
        folder_row = Adw.ActionRow(title="Configuration file", subtitle=str(CONFIG_FILE))
        open_button = Gtk.Button(label="Open folder")
        open_button.set_valign(Gtk.Align.CENTER)
        open_button.connect(
            "clicked",
            lambda _button: Gtk.FileLauncher.new(Gio.File.new_for_path(str(CONFIG_DIR))).launch(self._window, None, None),
        )
        folder_row.add_suffix(open_button)
        tools.add(folder_row)
        export_row = Adw.ActionRow(
            title="Export all settings",
            subtitle="Profiles, dial look, gesture feel and SpaceMouse buttons as a file for another computer; "
            "the touchpad, devices and modes of this computer are not included",
        )
        export_row.set_subtitle_lines(0)
        export_button = Gtk.Button(label="Export…")
        export_button.set_valign(Gtk.Align.CENTER)
        export_button.connect("clicked", lambda _button: self._export_settings())
        export_row.add_suffix(export_button)
        tools.add(export_row)
        import_row = Adw.ActionRow(
            title="Import settings",
            subtitle="Replaces the profiles and looks with the ones of an exported file; "
            "this computer's own devices and modes stay",
        )
        import_row.set_subtitle_lines(0)
        import_button = Gtk.Button(label="Import…")
        import_button.set_valign(Gtk.Align.CENTER)
        import_button.connect("clicked", lambda _button: self._import_settings())
        import_row.add_suffix(import_button)
        tools.add(import_row)
        content.append(tools)
        return self._scrolled_content(content)

    def _devices_group(self):
        """Detecting the hardware again and which pages to show."""
        group = Adw.PreferencesGroup(title="This computer")
        row = Adw.ActionRow(
            title="Set up devices again",
            subtitle="Looks for a DialPad, touchpad, mouse and SpaceMouse and suggests the modes",
        )
        row.set_subtitle_lines(0)
        button = Gtk.Button(label="Set up…")
        button.set_valign(Gtk.Align.CENTER)
        button.connect("clicked", lambda _button: self._show_setup_assistant())
        row.add_suffix(button)
        group.add(row)
        group.add(_switch_row(
            "Show all pages",
            "Also show the pages for hardware this computer does not seem to have (Calibration, SpaceMouse, Screen)",
            self._app_settings["show_all_pages"], self._on_show_all_pages,
        ))
        return group

    def _on_show_all_pages(self, active):
        if self._app_settings["show_all_pages"] == active:
            return
        self._app_settings["show_all_pages"] = active
        if not hw.save_app_settings(self._app_settings):
            self._toast("Could not save this preference")
        self._update_page_visibility()

    def _update_page_visibility(self):
        """Show the pages that can do something with this computer's hardware
        (see hardware.page_visibility); the page being looked at stays."""
        if self.config is None or not self._view_pages:
            return
        shown = hw.page_visibility(
            self.config, self._found, self._driver_status, self._app_settings["show_all_pages"],
        )
        current = self._view_stack.get_visible_child_name() if self._view_stack is not None else None
        for name, show in shown.items():
            page = self._view_pages.get(name)
            if page is not None and page.get_visible() != show and (show or name != current):
                page.set_visible(show)
        self._refresh_screen_presence()

    def _update_openers_hint(self):
        if self._openers_group is not None and self.config is not None:
            self._openers_group.set_visible(hw.two_openers(self.config))

    def _rescan_hardware(self):
        self._found = hw.detect(status=self._driver_status)
        self._update_page_visibility()
        return self._found

    # --- Pages built again ------------------------------------------------------------------

    def _rebuild_page(self, name):
        """Build a page again from the configuration: after something other than
        the page changed what its widgets show."""
        bin_ = self._page_bins.get(name)
        builder = next((entry[3] for entry in self.PAGES if entry[0] == name), None)
        if bin_ is None or builder is None:
            return
        if name == "spacemouse":
            self._stop_spacemouse_probe()
        bin_.set_child(getattr(self, builder)())

    def _rebuild_all_pages(self):
        """All pages, after ``self.config`` was replaced."""
        self._stop_spacemouse_probe()
        self._selected_profile = self._default_profile()
        self._sm_scope = None
        self._sm_layer = None
        self._screen_scope = None
        self._preview_title = None
        self._sm_extra_buttons = set()
        for entry in self.PAGES:
            self._rebuild_page(entry[0])
        self._refresh_all()
        self._refresh_diagnostics()
        self._update_page_visibility()

    # --- Setup assistant -----------------------------------------------------------------------

    def _show_setup_assistant(self):
        if self.config is None or self._window is None:
            return
        self._rescan_hardware()
        assistant = SetupAssistant(
            self.config, self._found, self._rescan_hardware, self._finish_setup, self._dismiss_setup, self._toast,
        )
        self._setup_assistant = assistant
        assistant.present(self._window)

    def _finish_setup(self, main, spacemouse, device, toggle_key):
        config = self.config
        changed = hw.apply_modes(config, main, spacemouse)
        if config.mouse.enabled and (config.mouse.device, config.mouse.toggle_key) != (device, toggle_key):
            changed = True
        config.mouse.device, config.mouse.toggle_key = device, toggle_key
        config.setup_done = True
        self._rebuild_page("diagnostics")
        self._rebuild_page("spacemouse")
        self._refresh_diagnostics()
        self._update_page_visibility()
        if changed:
            self._restart_driver()
        else:
            self._schedule_save()

    def _dismiss_setup(self):
        """The assistant was closed without applying: it does not come back by itself."""
        if self.config is not None and not self.config.setup_done:
            self.config.setup_done = True
            self._schedule_save()

    # --- All settings: export and import ---------------------------------------------------------

    def _export_settings(self):
        dialog = Gtk.FileDialog(title="Export all settings", initial_name="turnpad-settings.json")

        def finish(file_dialog, result):
            try:
                file = file_dialog.save_finish(result)
            except GLib.Error:
                return
            try:
                Path(file.get_path()).write_text(json.dumps(settings_document(self.config), indent=2) + "\n")
            except OSError as error:
                self._toast(f"Export failed: {error.strerror}")
                return
            self._toast("Exported the settings")

        dialog.save(self._window, None, finish)

    def _import_settings(self):
        dialog = Gtk.FileDialog(title="Import settings")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        json_filter = Gtk.FileFilter()
        json_filter.set_name("JSON")
        json_filter.add_suffix("json")
        filters.append(json_filter)
        dialog.set_filters(filters)

        def finish(file_dialog, result):
            try:
                file = file_dialog.open_finish(result)
            except GLib.Error:
                return
            try:
                text = Path(file.get_path()).read_text()
            except (OSError, UnicodeDecodeError) as error:
                self._toast(f"Import failed: {getattr(error, 'strerror', None) or error}")
                return
            self._offer_settings(text)

        dialog.open(self._window, None, finish)

    def _offer_settings(self, text):
        """Check an exported settings file, show what it changes and ask."""
        try:
            merged = merge_portable(self.config, settings_from_text(text))
        except ConfigError as error:
            alert = Adw.AlertDialog(heading="Not a usable settings file", body=str(error))
            alert.add_response("close", "Close")
            alert.present(self._window)
            return
        changes = describe_changes(self.config, merged)
        if not changes:
            self._toast("Nothing to import: these settings are the same")
            return
        body = (
            "This replaces:\n" + "\n".join(f"• {line}" for line in changes)
            + "\n\nThe touchpad, devices and modes of this computer stay as they are."
        )
        # Commands already in the settings here are no news; the new ones are asked about.
        known = set(shell_commands(self.config.profiles, self.config.spacemouse.buttons, self.config.spacemouse.layers))
        self._confirm_profiles(
            merged.profiles, lambda _response: self._apply_settings(merged),
            heading="Import these settings?", body=body, responses={"import": "Import"},
            spacemouse_buttons=merged.spacemouse.buttons, known_commands=known,
            spacemouse_layers=merged.spacemouse.layers,
        )

    def _apply_settings(self, merged):
        before = portable_config(self.config)["spacemouse"]
        self.config = merged
        self._screen_sync.mark(*sp.SCREEN_FIELDS)
        self._rebuild_all_pages()
        self._toast("Imported the settings")
        if before != portable_config(merged)["spacemouse"] and merged.spacemouse.enabled:
            # The driver reads the SpaceMouse settings when it starts.
            self._restart_driver()
        else:
            self._reload_driver()

    def _emulation_group(self):
        """Emulated dial for touchpads without a DialPad, and the toggle shortcut."""
        emulation = self.config.emulation
        group = Adw.PreferencesGroup(
            title="Touchpad mode",
            description=(
                "Without a DialPad (no printed dial, no light) the driver can emulate it: "
                "while the dial is on, the whole touchpad is the dial and the pointer stands still."
            ),
        )
        options = []

        def emulate_changed(active):
            for row in options:
                row.set_sensitive(active)
            if emulation.enabled != active:
                emulation.enabled = active
                # The driver picks its touchpad at start.
                self._restart_driver()

        group.add(_switch_row(
            "Touchpad mode", "Emulate the DialPad on any touchpad; restarts the driver",
            emulation.enabled, emulate_changed,
        ))
        options.append(_switch_row(
            "Switch with the top-right corner",
            "Hold the corner as long as the hold time on the Behaviour page; off: only the shortcut",
            emulation.corner, lambda active: self._set_config("emulation", "corner", active),
        ))
        options.append(_spin_row(
            "Centre button size", "Percent of the touchpad's height",
            emulation.center_size, 10, 60, 5, 0,
            lambda value: self._set_config("emulation", "center_size", int(value)),
        ))
        for row in options:
            row.set_sensitive(emulation.enabled)
            group.add(row)

        shortcut = Adw.ActionRow(
            title="Keyboard shortcut",
            subtitle="Switches the dial on or off: add a custom shortcut with the copied command",
        )

        def copy_command():
            self._window.get_display().get_clipboard().set(TOGGLE_COMMAND)
            self._toast("Command copied; paste it into a custom shortcut")

        shortcut.add_suffix(_icon_button("edit-copy-symbolic", "Copy the command", copy_command))
        shortcut.add_suffix(_icon_button(
            "preferences-desktop-keyboard-shortcuts-symbolic", "Open the keyboard settings",
            lambda: self._spawn(["gnome-control-center", "keyboard"], "GNOME Settings is not available"),
        ))
        group.add(shortcut)
        return group

    def _mouse_group(self):
        """A mouse as the dial for a computer without a touchpad."""
        mouse = self.config.mouse
        group = Adw.PreferencesGroup(
            title="Mouse-only mode",
            description=(
                "Without a touchpad a mouse can be the dial: the wheel turns it, the middle button is its "
                "centre, a left click selects and a right click goes back. While the dial is on, the "
                "pointer stands still (the driver grabs the mouse)."
            ),
        )
        options = []

        def enabled_changed(active):
            for row in options:
                row.set_sensitive(active)
            if mouse.enabled != active:
                mouse.enabled = active
                self._update_openers_hint()
                # The driver opens its devices at start.
                self._restart_driver()

        group.add(_switch_row(
            "Mouse-only mode", "A mouse is the dial, no touchpad needed; restarts the driver",
            mouse.enabled, enabled_changed,
        ))

        def picked(_attribute):
            # The driver opens its devices at start.
            self._restart_driver() if mouse.enabled else self._schedule_save()

        self._mouse_picker = MousePicker(mouse, picked, self._window, self._toast)
        options.extend(self._mouse_picker.rows)

        def apply_mouse_setting(attribute, value):
            if getattr(mouse, attribute) != value:
                setattr(mouse, attribute, value)
                # Read when the driver starts.
                self._restart_driver() if mouse.enabled else self._schedule_save()

        options.append(_switch_row(
            "Move the mouse around the dial",
            "The direction you move the mouse is the dial's angle: move towards a tile, circle to turn. "
            "The wheel keeps working",
            mouse.motion, lambda active: apply_mouse_setting("motion", active),
        ))
        options.append(_spin_row(
            "Size of the movement", "Mouse counts the hand travels before the dial follows; smaller is more sensitive",
            mouse.motion_radius, 50, 2000, 50, 0,
            lambda value: apply_mouse_setting("motion_radius", int(value)),
        ))

        tip = Adw.ActionRow(
            title="Which key?",
            subtitle=(
                "The key must come from the mouse itself: map a side button to F13 with Piper (Logitech G "
                "series) or the mouse's own software. A key the desktop knows (like 3) also types while "
                "the dial is off."
            ),
        )
        tip.set_subtitle_lines(0)
        options.append(tip)
        for row in options:
            row.set_sensitive(mouse.enabled)
            group.add(row)
        return group

    def _spawn(self, argv, failed, finished=None):
        """Run a program without waiting; ``finished(ok)`` when it ends."""
        try:
            process = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            self._toast(failed)
            return

        def done(process, result):
            try:
                ok = process.wait_check_finish(result)
            except GLib.Error:
                ok = False
            if finished is not None:
                finished(ok)

        process.wait_check_async(None, done)

    def _restart_driver(self):
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        save_config(self.config)

        def finished(ok):
            if not ok:
                self._toast(f"Restart {DRIVER_SERVICE} to apply the change")
                return
            self._toast("Driver restarted")
            GLib.timeout_add(1500, lambda: self._refresh_diagnostics() or GLib.SOURCE_REMOVE)

        failed = f"Restart {DRIVER_SERVICE} to apply the change"
        # Without a DialPad touchpad the driver kept failing until now; a
        # unit past its start limit only starts again after reset-failed.
        self._spawn(
            ["systemctl", "--user", "reset-failed", DRIVER_SERVICE], failed,
            lambda _ok: self._spawn(["systemctl", "--user", "restart", DRIVER_SERVICE], failed, finished),
        )

    def _reload_driver(self):
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        save_config(self.config)

        def done(connection, result):
            self._on_reload_reply(
                connection, result,
                success="The driver takes the configuration with the next touch", unreachable="Driver is not reachable over D-Bus",
            )

        self._call_driver("ReloadConfig", callback=done)

    @staticmethod
    def _configuration_state(status):
        """What the driver runs from config.json (GetStatus), in words."""
        if "config_revision" not in status:
            return "Unknown (older driver)"
        revision = str(status.get("config_revision") or "")
        parts = [f"Applied ({revision})" if revision else "No config.json: the layout's shortcuts"]
        if status.get("config_pending"):
            # The driver applies it between gestures, on the next touchpad event.
            parts.append("the newest settings apply with the next touch")
        if status.get("config_error"):
            parts.append(f"not applied: {status['config_error']}")
        return " · ".join(parts)

    @staticmethod
    def _unpack_dbus_value(value):
        return value.unpack() if isinstance(value, GLib.Variant) else value

    def _refresh_diagnostics(self):
        self._refresh_screen_presence()
        if self._mouse_picker is not None:
            # A mouse plugged in since the page was built.
            self._mouse_picker.refresh()
        self._rescan_hardware()
        rows = self._diagnostics_rows
        if not rows:
            return
        rows["service"].set_subtitle("Checking…")
        self._call_driver("GetStatus", "(a{sv})", self._on_diagnostics_status)

    def _on_diagnostics_status(self, connection, result, *_user_data):
        rows = self._diagnostics_rows
        if not rows:
            return
        try:
            if connection is None:
                raise RuntimeError("session bus not found")
            response = connection.call_finish(result)
            unpacked = response.unpack()
            status = unpacked[0] if unpacked else {}
            status = {key: self._unpack_dbus_value(value) for key, value in status.items()}
        except (GLib.Error, RuntimeError) as error:
            rows["service"].set_subtitle(f"Unavailable: {getattr(error, 'message', error)}")
            self._driver_status = None
            self._update_page_visibility()
            self._update_spacemouse_status(None)
            for key in ("enabled", "application", "profile", "function", "environment", "touchpad", "configuration"):
                rows[key].set_subtitle("Unknown")
            return

        rows["service"].set_subtitle("Connected")
        self._driver_status = status
        self._update_page_visibility()
        self._update_spacemouse_status(status)
        rows["enabled"].set_subtitle("On" if status.get("enabled") else "Off")
        rows["application"].set_subtitle(str(status.get("application") or "None"))
        mode = "single function" if status.get("mode") == "single" else "radial menu"
        rows["profile"].set_subtitle(f"{status.get('profile') or 'Legacy layout'} · {mode}")
        rows["function"].set_subtitle(
            str(status.get("selected_function") or ("Menu" if status.get("menu_active") else "—"))
        )
        rows["environment"].set_subtitle(
            f"{status.get('session_type') or 'unknown session'} · {status.get('layout') or 'unknown layout'}"
        )
        if status.get("mouse"):
            dial_mode = f"Mouse-only mode · {status.get('mouse_device')}" if status.get("mouse_device") else "Mouse-only mode"
        else:
            dial_mode = f"{'Touchpad mode' if status.get('emulation') else 'DialPad mode'} · {status.get('touchpad') or 'unknown'}"
        if self.config.spacemouse.enabled or status.get("spacemouse"):
            device = status.get("spacemouse_device")
            if status.get("spacemouse"):
                extra = f"SpaceMouse mode · {device}" if device else "SpaceMouse mode"
            elif status.get("emulation") and not status.get("mouse"):
                extra = "SpaceMouse mode (ignored next to Touchpad mode)"
            elif status.get("touchpad") and not status.get("mouse"):
                extra = "SpaceMouse mode (not connected, or ignored next to a touchpad)"
            else:
                extra = "SpaceMouse mode (not connected)"
            alone = not status.get("mouse") and not status.get("emulation") and (
                status.get("spacemouse") or not status.get("touchpad"))
            dial_mode = extra if alone else f"{dial_mode} + {extra}"
        rows["touchpad"].set_subtitle(dial_mode)
        rows["configuration"].set_subtitle(self._configuration_state(status))
        max_x, max_y = int(status.get("touchpad_max_x") or 0), int(status.get("touchpad_max_y") or 0)
        if max_x > 0 and max_y > 0:
            self._touchpad_size = (max_x, max_y)
            if self._calibration_preview is not None:
                self._calibration_preview.queue_draw()


def main():
    app = Application()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
