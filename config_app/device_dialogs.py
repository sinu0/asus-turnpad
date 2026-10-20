"""Widgets of the settings app for the hardware: the mouse and toggle key rows
(Driver page and setup assistant), the dialog that waits for a key, and the
first-run setup assistant. What they decide comes from ``config_app/hardware.py``
(pure, unit tested); this file only draws and wires it."""

import dataclasses

from gi.repository import Adw, Gdk, GLib, Gtk

from config.labels import KNOWN_KEYS
from config.schema import normalize_key_name
from config_app import hardware as hw
from config_app.key_capture import EvdevWatcher

FINDING_ICONS = {
    "dialpad": "input-dialpad-symbolic",
    "touchpad": "input-touchpad-symbolic",
    "mouse": "input-mouse-symbolic",
    "spacemouse": "input-gaming-symbolic",
    "screen": "video-display-symbolic",
}


def _clear_children(widget):
    child = widget.get_first_child()
    while child is not None:
        next_child = child.get_next_sibling()
        widget.remove(child)
        child = next_child


def _wrapped_label(text, *css):
    label = Gtk.Label(label=text, xalign=0, wrap=True)
    for name in css:
        label.add_css_class(name)
    return label


class KeyCaptureDialog:
    """Waits for the next key or mouse button and hands its evdev name to
    ``on_key``. The mouse's own events are read when its device can be opened
    (the key exactly as the driver sees it); otherwise this window's key and
    button presses count. Esc and Cancel leave without a result."""

    def __init__(self, nodes, mouse_label, on_key):
        self.on_key = on_key
        self.finished = False
        self.watcher = EvdevWatcher(nodes, self._from_device)
        self.reading = self.watcher.start() > 0

        self.dialog = Adw.Dialog(title="Toggle key", content_width=420)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        pad = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for setter in (pad.set_margin_top, pad.set_margin_bottom, pad.set_margin_start, pad.set_margin_end):
            setter(24)
        pad.set_halign(Gtk.Align.FILL)
        icon = Gtk.Image.new_from_icon_name("input-mouse-symbolic")
        icon.set_pixel_size(64)
        icon.add_css_class("dim-label")
        pad.append(icon)
        heading = Gtk.Label(label="Press the key or button now")
        heading.add_css_class("title-3")
        heading.set_wrap(True)
        pad.append(heading)
        if self.reading:
            body = (f"Listening to {mouse_label}: press the side button, or the key a button of the mouse sends "
                    "(set up in its own software). Nothing is grabbed: the desktop gets the key as well.")
        else:
            body = ("The mouse cannot be read directly here (you may not be in the “input” group, or it is not "
                    "connected), so this window listens instead: press a key, or click a side or the middle "
                    "button of the mouse on this window. The mouse itself has to send that key.")
        body_label = _wrapped_label(body, "dim-label")
        body_label.set_justify(Gtk.Justification.CENTER)
        body_label.set_xalign(0.5)
        pad.append(body_label)
        self.hint = _wrapped_label("", "error")
        self.hint.set_xalign(0.5)
        self.hint.set_justify(Gtk.Justification.CENTER)
        self.hint.set_visible(False)
        pad.append(self.hint)
        self.cancel = Gtk.Button(label="Cancel", halign=Gtk.Align.CENTER)
        self.cancel.connect("clicked", lambda _button: self.dialog.close())
        pad.append(self.cancel)
        self.pad = pad
        view.set_content(pad)
        self.dialog.set_child(view)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key_pressed)
        view.add_controller(keys)
        # Clicks anywhere but on Cancel; the gesture does not claim them.
        click = Gtk.GestureClick()
        click.set_button(0)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._clicked)
        pad.add_controller(click)
        self.dialog.connect("closed", lambda _dialog: self.watcher.stop())

    def present(self, parent):
        self.dialog.present(parent)

    def _say(self, hint):
        self.hint.set_label(hint)
        self.hint.set_visible(bool(hint))

    def _accept(self, name, hint=""):
        if self.finished:
            return
        name, reason = hw.captured_key(name) if name else (None, hint)
        if name is None:
            self._say(reason or hint)
            return
        self.finished = True
        self.watcher.stop()
        self.dialog.close()
        self.on_key(name)

    def _from_device(self, name):
        self._accept(name)

    def _key_pressed(self, _controller, keyval, keycode, _state):
        if keyval == Gdk.KEY_Escape:
            return False  # the dialog closes
        if self.reading:
            return True
        name = hw.gtk_key_name(keycode)
        self._accept(name, "That key is not one the driver knows")
        return True

    def _clicked(self, gesture, _presses, x, y):
        if self.reading:
            return
        target = self.pad.pick(x, y, Gtk.PickFlags.DEFAULT)
        if target is not None and (target is self.cancel or target.is_ancestor(self.cancel)):
            return
        name, hint = hw.gtk_button_name(gesture.get_current_button())
        self._accept(name, hint)


class MousePicker:
    """The rows for a mouse dial: which mouse, which key switches the dial, and
    the key name typed (advanced). ``mouse`` is the MouseConfig edited;
    ``changed(attribute)`` runs after the user changed ``device`` or
    ``toggle_key``; ``parent`` is a widget of the window dialogs open over."""

    def __init__(self, mouse, changed, parent, toast):
        self.mouse = mouse
        self.changed = changed
        self.parent = parent
        self.toast = toast
        self.mice = []
        self.devices_text = ""
        self.values = [""]
        self._updating = False

        self.combo = Adw.ComboRow(title="Mouse", subtitle="The mouse that sends the toggle key and turns the dial")
        self.combo.set_model(Gtk.StringList.new([""]))
        self.combo.connect("notify::selected", self._on_selected)
        self.key_row = Adw.ActionRow(title="Toggle key")
        self.key_row.set_subtitle_lines(0)
        self.capture_button = Gtk.Button(label="Press the key…", valign=Gtk.Align.CENTER)
        self.capture_button.set_tooltip_text("Press the mouse button or key that should switch the dial on and off")
        self.capture_button.connect("clicked", lambda _button: self.capture())
        self.key_row.add_suffix(self.capture_button)
        self.entry = Adw.EntryRow(title="Key name (advanced), e.g. F13, KEY_3 or BTN_SIDE")
        self.entry.set_show_apply_button(True)
        self.entry.connect("apply", self._on_entry_applied)
        self.rows = [self.combo, self.key_row, self.entry]
        self.refresh()

    def refresh(self):
        """Read the mice again (the page was shown, or a mouse was plugged in)."""
        self.devices_text = hw.input_devices_text()
        self.mice = hw.list_mice(self.devices_text)
        labels, self.values, selected = hw.mouse_choices(self.mice, self.mouse.device)
        self._updating = True
        self.combo.set_model(Gtk.StringList.new(labels))
        self.combo.set_selected(selected)
        self.entry.set_text(self.mouse.toggle_key)
        self._updating = False
        self._show_key()

    def _show_key(self):
        text = hw.describe_key(self.mouse.toggle_key)
        if hw.mouse_nodes(self.mice, self.mouse.device) and hw.key_available(
            self.devices_text, self.mouse.device, self.mouse.toggle_key
        ) is False:
            text += "\nThe selected mouse does not report this key: choose the key it sends."
        self.key_row.set_subtitle(GLib.markup_escape_text(text))

    def _on_selected(self, combo, _param):
        if self._updating:
            return
        index = combo.get_selected()
        if index >= len(self.values):
            return
        value = self.values[index]
        if value != self.mouse.device:
            self.mouse.device = value
            self._show_key()
            self.changed("device")

    def capture(self):
        nodes = hw.mouse_nodes(self.mice, self.mouse.device)
        labels = [mouse.label for mouse in self.mice if hw.device_matches(self.mouse.device, mouse.name)]
        label = labels[0] if len(labels) == 1 else "the mouse"
        KeyCaptureDialog(nodes, label, self.set_key).present(self.parent)

    def set_key(self, name):
        """Use ``name`` (an evdev KEY_/BTN_ name) as the toggle key."""
        self._updating = True
        self.entry.set_text(name)
        self.entry.remove_css_class("error")
        self._updating = False
        if self.mouse.toggle_key != name:
            self.mouse.toggle_key = name
            self._show_key()
            self.changed("toggle_key")
        else:
            self._show_key()
        self.toast(f"Toggle key: {hw.describe_key(name)}")

    def _on_entry_applied(self, row):
        if self._updating:
            return
        name = normalize_key_name(row.get_text(), "")
        # Keys come from the evdev table; mouse buttons cannot be checked here.
        if name not in KNOWN_KEYS and not name.startswith("BTN_") and hw.code_for_name(name) is None:
            row.add_css_class("error")
            self.toast(f"“{row.get_text().strip()}” is not a key name")
            return
        self.set_key(name)


class SetupAssistant:
    """The first-run dialog: what was found, then the modes to use.

    ``rescan()`` returns a fresh ``hardware.Detected``; ``finish(main,
    spacemouse, device, toggle_key)`` applies the choice; ``dismissed()`` runs
    when the dialog is closed without applying it."""

    def __init__(self, config, found, rescan, finish, dismissed, toast):
        self.config = config
        self.found = found
        self.rescan = rescan
        self.finish = finish
        self.dismissed = dismissed
        self.toast = toast
        self.done = False
        self._finding_rows = []
        self.mouse = dataclasses.replace(config.mouse)
        self.suggestion = hw.suggest_modes(found)
        # A first run takes the suggestion; later runs start from what is set.
        if config.setup_done:
            self.main = hw.current_main_mode(config)
            self.spacemouse = config.spacemouse.enabled
        else:
            self.main = self.suggestion.main or hw.current_main_mode(config)
            self.spacemouse = self.suggestion.spacemouse
        self.dialog = None
        self.nav = None
        self.picker = None
        self._findings_group = None

    # Layout helpers ---------------------------------------------------------------------

    @staticmethod
    def _page(title, tag, content, buttons):
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        clamp = Adw.Clamp()
        clamp.set_maximum_size(560)
        clamp.set_child(content)
        scroll.set_child(clamp)
        view.set_content(scroll)
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.END)
        for setter in (bar.set_margin_top, bar.set_margin_bottom, bar.set_margin_start, bar.set_margin_end):
            setter(12)
        for button in buttons:
            bar.append(button)
        view.add_bottom_bar(bar)
        return Adw.NavigationPage(child=view, title=title, tag=tag)

    def present(self, parent):
        self.dialog = Adw.Dialog(title="Set up devices", content_width=560, content_height=640)
        self.nav = Adw.NavigationView()
        self.nav.add(self._summary_page())
        self.dialog.set_child(self.nav)
        self.dialog.connect("closed", self._on_closed)
        self.parent = parent
        self.dialog.present(parent)

    def _on_closed(self, _dialog):
        if not self.done:
            self.dismissed()

    # Step 1: what was found --------------------------------------------------------------

    def _summary_page(self):
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.append(_wrapped_label(
            "Turnpad can drive its dial from a DialPad, any touchpad, a mouse or a SpaceMouse. "
            "This is what was found on this computer.", "dim-label",
        ))
        self._findings_group = Adw.PreferencesGroup(title="Found")
        rescan = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        rescan.set_tooltip_text("Look again")
        rescan.add_css_class("flat")
        rescan.set_valign(Gtk.Align.CENTER)
        rescan.connect("clicked", lambda _button: self._rescan())
        self._findings_group.set_header_suffix(rescan)
        content.append(self._findings_group)
        self._fill_findings()
        later = Gtk.Button(label="Not now")
        later.connect("clicked", lambda _button: self.dialog.close())
        go = Gtk.Button(label="Continue")
        go.add_css_class("suggested-action")
        go.connect("clicked", lambda _button: self._show_choice())
        return self._page("Set up devices", "found", content, [later, go])

    def _fill_findings(self):
        group = self._findings_group
        for row in self._finding_rows:
            group.remove(row)
        self._finding_rows = []
        for finding in hw.findings(self.found):
            row = Adw.ActionRow(title=GLib.markup_escape_text(finding.title))
            row.set_subtitle(GLib.markup_escape_text(finding.detail))
            row.set_subtitle_lines(0)
            row.add_prefix(Gtk.Image.new_from_icon_name(FINDING_ICONS.get(finding.kind, "input-dialpad-symbolic")))
            status = Gtk.Image.new_from_icon_name("object-select-symbolic" if finding.found else "window-close-symbolic")
            if not finding.found:
                status.add_css_class("dim-label")
            status.set_tooltip_text("Found" if finding.found else "Not found")
            row.add_suffix(status)
            group.add(row)
            self._finding_rows.append(row)

    def _rescan(self):
        self.found = self.rescan()
        self.suggestion = hw.suggest_modes(self.found)
        self._fill_findings()

    # Step 2: the modes ---------------------------------------------------------------------

    def _show_choice(self):
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.append(_wrapped_label("Choose what drives the dial. You can change this later on the Driver page.", "dim-label"))

        group = Adw.PreferencesGroup(title="Dial")
        self._radios = {}
        first = None
        options = (
            (hw.MODE_DIALPAD, "DialPad mode", "An ASUS laptop with the printed DialPad: its ring, centre and light"),
            (hw.MODE_TOUCHPAD, "Touchpad mode", "Any touchpad becomes the dial while it is on"),
            (hw.MODE_MOUSE, "Mouse-only mode", "A mouse: the wheel turns the dial, a button or key switches it on"),
        )
        for mode, title, subtitle in options:
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            row.set_subtitle_lines(0)
            check = Gtk.CheckButton()
            check.set_valign(Gtk.Align.CENTER)
            if first is None:
                first = check
            else:
                check.set_group(first)
            check.set_active(self.main == mode)
            check.connect("toggled", self._on_main_toggled, mode)
            row.add_prefix(check)
            row.set_activatable_widget(check)
            if mode == self.suggestion.main:
                badge = Gtk.Label(label="Suggested")
                badge.add_css_class("proart-badge")
                badge.set_valign(Gtk.Align.CENTER)
                row.add_suffix(badge)
            group.add(row)
            self._radios[mode] = check
        content.append(group)

        sm_group = Adw.PreferencesGroup()
        self.sm_row = Adw.SwitchRow(title="SpaceMouse mode", subtitle="A 3Dconnexion SpaceMouse through spacenavd")
        self.sm_row.set_active(self.spacemouse)
        self.sm_row.connect("notify::active", self._on_spacemouse_toggled)
        if self.suggestion.spacemouse:
            badge = Gtk.Label(label="Suggested")
            badge.add_css_class("proart-badge")
            badge.set_valign(Gtk.Align.CENTER)
            self.sm_row.add_suffix(badge)
        sm_group.add(self.sm_row)
        content.append(sm_group)

        self.mouse_group = Adw.PreferencesGroup(title="Mouse")
        self.picker = MousePicker(self.mouse, lambda _attribute: self._update(), self.dialog, self.toast)
        for row in self.picker.rows:
            self.mouse_group.add(row)
        content.append(self.mouse_group)

        self.hint_group = Adw.PreferencesGroup()
        hint = Adw.ActionRow(title=hw.TWO_OPENERS_HINT)
        hint.set_title_lines(0)
        hint.add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
        self.hint_group.add(hint)
        content.append(self.hint_group)

        self.notes = _wrapped_label("", "dim-label", "caption")
        content.append(self.notes)

        apply_button = Gtk.Button(label="Apply and restart the driver")
        apply_button.add_css_class("suggested-action")
        apply_button.connect("clicked", lambda _button: self._apply())
        page = self._page("Modes", "modes", content, [apply_button])
        self.nav.push(page)
        self._update()

    def _on_main_toggled(self, check, mode):
        if check.get_active():
            self.main = mode
            self._update()

    def _on_spacemouse_toggled(self, row, _param):
        self.spacemouse = row.get_active()
        self._update()

    def _update(self):
        self.mouse_group.set_visible(self.main == hw.MODE_MOUSE)
        self.hint_group.set_visible(self.main == hw.MODE_MOUSE and self.spacemouse)
        notes = hw.mode_notes(self.found, self.main, self.spacemouse)
        self.notes.set_label("\n".join(notes))
        self.notes.set_visible(bool(notes))

    def _apply(self):
        self.done = True
        self.dialog.close()
        self.finish(self.main, self.spacemouse, self.mouse.device, self.mouse.toggle_key)
