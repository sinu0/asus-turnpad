#!/usr/bin/env python3
"""Take the README screenshots of the settings app.

    python3 tools/screenshot_settings.py [output directory]

Opens the settings app on a demo configuration (default profile plus a few
application profiles), walks through its pages and saves each one as a PNG.
Nothing is written to ~/.config: saving is disabled and only built-in skins
are shown. Run directly, the window briefly appears on screen;
tools/update_previews.py runs it in a headless mutter instead.
"""

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import importlib

import gi

gi.require_version("Graphene", "1.0")
# config_app exports main(); import the module itself, which also pins the
# GTK and Adwaita versions.
app_main = importlib.import_module("config_app.main")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402

from config.presets import apps_group, pack_for_app, profile_for_pack  # noqa: E402
from config.schema import _default_config  # noqa: E402
from config_app import key_capture  # noqa: E402
from config.skins import builtin_skins  # noqa: E402

OUTPUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "preview"
WIDTH, HEIGHT = 1080, 760
SETTLE_MS = 700
# A slow machine without a GPU (CI) sometimes loses the resize of a freshly
# shown window or has not drawn it yet: ticks to wait before asking again,
# how often to ask, and how long to wait for a drawn window.
RESIZE_TICKS = 15
RESIZE_TRIES = 6
CAPTURE_TICKS = 20


def demo_config():
    config = _default_config()
    default = config.profiles[0]
    profiles = [default]
    for app_id in ("org.kde.krita.desktop", "firefox.desktop", "org.darktable.darktable.desktop"):
        pack = pack_for_app(app_id)
        profile = profile_for_pack(pack)
        profile.app_id = app_id
        profiles.append(profile)
    spotify = profile_for_pack(pack_for_app("spotify.desktop"))
    spotify.mode = "single"
    spotify.single_function = next(
        (segment.title for segment in spotify.segments if "volume" in segment.title.lower()), None
    )
    profiles.append(spotify)
    config.profiles = profiles
    # Shows the light and dark badges in the skin gallery.
    config.overlay.follow_system_style = True
    # No setup assistant over the pictures.
    config.setup_done = True
    return config


# /proc/bus/input/devices of the demo laptop: two mice with a wheel.
DEMO_DEVICES = """I: Bus=0003 Vendor=046d Product=4085 Version=0111
N: Name="Logitech G604 "
H: Handlers=sysrq kbd leds event2 mouse0
B: EV=12001f
B: KEY=3f00733fff 0 0 483ffff17aff32d bfd4444600000000 ffff0001 130ff38b17d007 ffff7bfad9415fff ffbeffdfffefffff fffffffffffffffe
B: REL=1943

I: Bus=0003 Vendor=1234 Product=0001 Version=0111
N: Name="USB Optical Mouse"
H: Handlers=mouse1 event3
B: EV=17
B: KEY=1f0000 0 0 0 0
B: REL=903
"""


def demo_hardware():
    """A computer that has everything (a DialPad, mice, a SpacePilot Pro), whatever
    machine the pictures or the layout probe run on, and no files written."""
    hardware = app_main.hw
    hardware.input_devices_text = lambda: DEMO_DEVICES
    hardware.detect = lambda **_arguments: hardware.Detected(
        product="ProArt PX13", vendor="ASUSTeK COMPUTER INC.", dialpad="ASCF1A00:00 2808:0220 Touchpad",
        layout="proartp16", mice=tuple(hardware.list_mice(DEMO_DEVICES)), spacenav_socket="/var/run/spnav.sock",
        spacemice=((0x046D, 0xC629, "SpacePilot PRO"),), screens=("SpacePilot Pro",),
    )
    hardware.load_app_settings = lambda path=None: {"show_all_pages": True}
    # The mouse's event nodes cannot be read: the key capture listens to its own window.
    def unreadable(node):
        raise PermissionError(node)

    key_capture.open_node = unreadable
    hardware.save_app_settings = lambda settings, path=None: True


class ScreenshotApp(app_main.Application):
    def _save_config(self):
        self._save_source = 0
        return GLib.SOURCE_REMOVE

    # A dark desktop in every picture, whatever the machine running this uses.
    def _watch_system_style(self):
        pass

    def _system_is_light(self):
        return False


def resize(window, width, height):
    """Resize the window; yields until it has the size."""
    for attempt in range(RESIZE_TRIES):
        if attempt:
            # Asking for the same size again would be ignored.
            window.set_default_size(width - 1, height)
            yield
        window.set_default_size(width, height)
        for _ in range(RESIZE_TICKS):
            yield
            if abs(window.get_width() - width) <= 1 and abs(window.get_height() - height) <= 1:
                return


def capture(window, name):
    """Save the window as name.png; yields until it has been drawn."""
    # No keyboard focus ring in the pictures.
    window.set_focus_visible(False)
    for _ in range(CAPTURE_TICKS):
        width, height = window.get_width(), window.get_height()
        snapshot = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(window).snapshot(snapshot, width, height)
        node = snapshot.to_node()
        if node is not None:
            break
        yield
    else:
        raise RuntimeError(f"{name}: the window was never drawn")
    bounds = Graphene.Rect().init(0, 0, width, height)
    texture = window.get_renderer().render_texture(node, bounds)
    path = OUTPUT / f"{name}.png"
    texture.save_to_png(str(path))
    print(f"wrote {path}")


def scroll_to(widget):
    scrolled = widget.get_ancestor(Gtk.ScrolledWindow)
    ok, point = widget.compute_point(scrolled.get_child(), Graphene.Point().init(0, 0))
    if ok:
        scrolled.get_vadjustment().set_value(max(0, point.y - 12))


def close_dialogs(app):
    dialog = app._window.get_visible_dialog()
    if dialog is not None:
        dialog.force_close()


def steps(app):
    window = app._window
    config = app.config
    default, krita, _firefox, darktable = config.profiles[:4]

    def page(name, size=(WIDTH, HEIGHT)):
        close_dialogs(app)
        app._view_stack.set_visible_child_name(name)
        yield from resize(window, *size)

    yield from page("actions", (WIDTH, 1168))
    app._select_profile(default)
    yield
    yield from capture(window, "settings-dial-controls")

    yield from page("profiles")
    yield
    yield from capture(window, "settings-profiles")

    yield from page("actions")
    app._select_profile(krita)
    yield
    app._show_function_library(krita)
    yield
    yield from capture(window, "settings-library")

    close_dialogs(app)
    app._select_profile(darktable)
    yield
    zoom = next(segment for segment in darktable.segments if segment.title == "Zoom")
    app._show_function_editor(darktable, zoom)
    yield
    yield from capture(window, "settings-function-editor")

    # A group on the dial; added only now so the pictures before stay as
    # they were.
    yield from page("actions")
    app._select_profile(default)
    group = apps_group()
    default.segments.insert(4, group)
    app._changed()
    yield
    app._show_function_editor(default, group)
    yield
    yield from capture(window, "settings-group-editor")

    yield from page("calibration")
    yield
    yield from capture(window, "settings-calibration")

    yield from page("device")
    yield
    yield from capture(window, "settings-behaviour")

    yield from page("look")
    yield
    yield from capture(window, "settings-skins")

    app._show_skin_editor(None, {"name": "My ProArt bronze", "base": "proart",
                                 "palette": {"rim": "#f5d1aa80", "pointer": "#ffe2bf"}})
    yield
    yield from capture(window, "settings-skin-editor")

    close_dialogs(app)
    app._show_skin_editor(None, {
        "name": "Square bronze", "base": "proart", "shape": "custom", "tile": "square",
        "outline": {"points": [1.0, 1.41] * 4, "smooth": False},
    })
    yield
    dialog = window.get_visible_dialog()
    outline = next(
        group for group in _descendants(dialog, Adw.PreferencesGroup)
        if group.get_title() == "Custom outline"
    )
    scroll_to(outline)
    yield
    yield from capture(window, "settings-skin-shape")

    # The pictures of the SpaceMouse features, added last so the ones before
    # stay as they were.
    blender = _blender_profile()
    config.profiles.append(blender)
    app._changed()
    yield
    app._refresh_spacemouse_page()
    app.edit_context_buttons(blender)
    yield from page("spacemouse", (WIDTH, 1168))
    yield
    yield from capture(window, "settings-spacemouse")

    app._screen_scope = blender
    app._refresh_screen_page()
    yield from page("screen", (WIDTH, 1000))
    yield
    yield
    yield from capture(window, "settings-screen")

    yield from page("actions")
    app._select_profile(default)
    config.setup_done = False
    app._show_setup_assistant()
    yield
    yield
    yield from capture(window, "settings-setup-assistant")

    close_dialogs(app)
    app.quit()


def _blender_profile():
    """The advanced Blender pack with the labelled buttons of the screen pictures."""
    sys.path.insert(0, str(ROOT / "tools"))
    import render_screen_preview

    return render_screen_preview.demo_config()[1]


def _descendants(widget, kind):
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, kind):
            yield child
        yield from _descendants(child, kind)
        child = child.get_next_sibling()


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    demo = demo_config()
    # Never touch the user's configuration or skins.
    app_main.CONFIG_FILE = Path(__file__)
    app_main.load_config = lambda: demo
    app_main.load_skins = builtin_skins
    demo_hardware()
    Adw.init()
    app = ScreenshotApp()
    # Run beside an open settings app instead of activating it.
    app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)

    def start(_app):
        # Overlay scrollbars fade in and out and the focus ring depends on
        # timing, so they would make otherwise equal pictures differ between
        # runs (tools/update_previews.py).
        css = Gtk.CssProvider()
        css.load_from_string("scrollbar { opacity: 0; } * { outline: none; }")
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        runner = steps(app)

        def tick():
            try:
                next(runner)
            except StopIteration:
                return GLib.SOURCE_REMOVE
            except Exception:
                # Without this the loop stops but the app keeps running.
                traceback.print_exc()
                failed.append(True)
                app.quit()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        GLib.timeout_add(SETTLE_MS, tick)

    failed = []
    app.connect("activate", start)
    app.run([])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
