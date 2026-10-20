"""The "Record…" dialog of the macro editor: counts 3-2-1, then records the keys
pressed on the keyboards (read-only, nothing is grabbed) and shows the steps as
they come. What the keys make (``recorder.convert``) is pure and tested; this
file draws the dialog and wires the keyboard reader to it."""

import time

from gi.repository import Adw, GLib, Gtk

from config.labels import describe_step
from config_app import hardware as hw
from config_app import recorder as rec

COUNTDOWN = 3


class MacroRecorderDialog:
    """``on_done(steps, replace)`` gets the recorded steps; ``existing`` is how many
    steps the macro has (with some, the dialog offers Append and Replace).
    ``nodes`` are the keyboards to read (default: the ones /proc lists)."""

    def __init__(self, window, existing, on_done, nodes=None):
        self.window = window
        self.existing = existing
        self.on_done = on_done
        self.state = "countdown"
        self.recording = rec.Recording()
        self.remaining = COUNTDOWN
        self.timer = 0
        self.closed = False
        if nodes is None:
            nodes = rec.keyboard_nodes(hw.input_devices_text())
        self.reader = rec.KeyReader(nodes, self._from_device)
        self.reading = self.reader.start() > 0
        self._build()
        self._show_state()
        self.timer = GLib.timeout_add(250, self._tick)
        self._next_second = time.monotonic() + 1

    # --- the dialog ------------------------------------------------------------------

    def _build(self):
        self.dialog = Adw.Dialog(title="Record keys", content_width=480, content_height=560)
        self.view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _button: self.dialog.close())
        header.pack_start(cancel)
        self.view.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for setter in (box.set_margin_top, box.set_margin_bottom, box.set_margin_start, box.set_margin_end):
            setter(18)

        self.big = Gtk.Label(label="3")
        self.big.add_css_class("title-1")
        self.big.set_margin_top(6)
        box.append(self.big)
        self.note = Gtk.Label(xalign=0.5, wrap=True, justify=Gtk.Justification.CENTER)
        self.note.add_css_class("dim-label")
        box.append(self.note)

        self.steps_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.steps_list.add_css_class("boxed-list")
        self.steps_list.set_placeholder(self._placeholder())
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_min_content_height(140)
        scroll.set_child(self.steps_list)
        self.scroll = scroll
        box.append(scroll)

        pauses = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        pauses.add_css_class("boxed-list")
        self.pauses = Adw.SwitchRow(
            title="Keep pauses",
            subtitle="Waits longer than a quarter of a second between keys become Wait steps",
        )
        self.pauses.connect("notify::active", lambda *_args: self._refresh_steps())
        pauses.append(self.pauses)
        box.append(pauses)

        self.buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.buttons.set_halign(Gtk.Align.END)
        box.append(self.buttons)
        self.view.set_content(box)
        self.dialog.set_child(self.view)

        # The keys must not press this window's buttons or close it while they are
        # being recorded; the GTK fallback records them from here.
        keys = Gtk.EventControllerKey.new()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_gtk_key, True)
        keys.connect("key-released", self._on_gtk_key, False)
        self.view.add_controller(keys)
        self.dialog.connect("closed", self._on_closed)

    @staticmethod
    def _placeholder():
        label = Gtk.Label(label="Nothing recorded yet")
        label.add_css_class("dim-label")
        label.set_margin_top(24)
        label.set_margin_bottom(24)
        return label

    def present(self):
        self.dialog.present(self.window)

    # --- states ------------------------------------------------------------------------

    def _set_buttons(self, specs):
        child = self.buttons.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.buttons.remove(child)
            child = following
        for label, css, callback in specs:
            button = Gtk.Button(label=label)
            if css:
                button.add_css_class(css)
            button.connect("clicked", lambda _button, callback=callback: callback())
            self.buttons.append(button)

    def _access_note(self):
        if self.reading:
            return "Switch to the program you want to record in. Every keyboard is read; nothing is blocked."
        return ("Turnpad cannot read the keyboards (your user needs to be in the “input” group: "
                "sudo usermod -aG input $USER, then log in again). Until then it records the keys "
                "pressed while this window has the focus.")

    def _show_state(self):
        if self.state == "countdown":
            self.big.set_label(str(max(self.remaining, 1)))
            self.note.set_label(f"Recording starts in {max(self.remaining, 1)}…\n{self._access_note()}")
            self._set_buttons([("Stop", None, self._stop)])
        elif self.state == "recording":
            self.big.set_label("Recording")
            self.note.set_label(
                f"Press the keys now. Stops by itself after {rec.IDLE_STOP_SECONDS} s without a key; "
                "Esc is recorded as a key, use Stop to finish."
            )
            self._set_buttons([("Stop", "suggested-action", self._stop)])
        else:
            count = len(self.recording.steps())
            self.big.set_label("Recorded" if count else "No keys recorded")
            self.note.set_label("Check the steps below, then use them." if count else "Nothing was pressed.")
            specs = [("Record again", None, self._again)]
            if count and self.existing:
                specs += [("Append", None, lambda: self._finish(False)),
                          ("Replace", "suggested-action", lambda: self._finish(True))]
            elif count:
                specs += [("Use these steps", "suggested-action", lambda: self._finish(True))]
            self._set_buttons(specs)
        self._refresh_steps()

    def _refresh_steps(self):
        child = self.steps_list.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.steps_list.remove(child)
            child = following
        steps = self.recording.steps(self.pauses.get_active())
        for step in steps:
            row = Adw.ActionRow(title=GLib.markup_escape_text(describe_step(step)))
            self.steps_list.append(row)
        if steps and self.state == "recording":
            adjustment = self.scroll.get_vadjustment()
            GLib.idle_add(lambda: adjustment.set_value(adjustment.get_upper()) or GLib.SOURCE_REMOVE)

    # --- events ------------------------------------------------------------------------

    def _tick(self):
        if self.closed:
            self.timer = 0
            return GLib.SOURCE_REMOVE
        now = time.monotonic()
        if self.state == "countdown" and now >= self._next_second:
            self.remaining -= 1
            self._next_second += 1
            if self.remaining <= 0:
                self.recording = rec.Recording()
                self.state = "recording"
            self._show_state()
        elif self.state == "recording" and self.recording.idle(now) >= rec.IDLE_STOP_SECONDS:
            self._stop()
        return GLib.SOURCE_CONTINUE

    def _from_device(self, name, value, when):
        if self.state == "recording":
            self.recording.add(name, value, when)
            if value == 1:
                self._refresh_steps()

    def _on_gtk_key(self, _controller, _keyval, keycode, _state, pressed):
        if self.state not in ("countdown", "recording"):
            return False
        # Without a readable keyboard the keys of this window are the recording.
        if self.state == "recording" and not self.reading:
            event = rec.gtk_event(keycode, pressed)
            if event is not None:
                self._from_device(*event)
        return True

    def _stop(self):
        if self.state == "stopped":
            return
        self.state = "stopped"
        self.reader.stop()
        self._show_state()

    def _again(self):
        self.recording = rec.Recording()
        self.remaining = COUNTDOWN
        self._next_second = time.monotonic() + 1
        self.state = "countdown"
        self.reading = self.reader.start() > 0
        self._show_state()

    def _finish(self, replace):
        steps = self.recording.steps(self.pauses.get_active())
        self.dialog.close()
        if steps:
            self.on_done(steps, replace)

    def _on_closed(self, _dialog):
        self.closed = True
        self.reader.stop()
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
