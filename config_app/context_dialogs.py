"""The contexts editor of the settings app: the "Contexts" group of a profile and
the dialog that edits one context (what makes it apply and what it changes).

Pure helpers are in ``config_app/contexts.py`` (unit tested); this file draws
and wires them. ``app`` is the settings Application: the dialog reuses its
function rows, function library and editor for a context's own dial menu, and
sends the SpaceMouse page to the context's buttons."""

from gi.repository import Adw, Gio, GLib, Gtk

from config.identity import DBUS_INTERFACE, DBUS_NAME, DBUS_PATH
from config.schema import SCREEN_PAGES, context_title_error
from config_app import contexts as cx
from config_app import screen_page as sp

STATUS_CSS = {"match": "success", "nomatch": "warning", "invalid": "error"}


def _flat_button(icon_name, tooltip, callback, *css):
    button = Gtk.Button.new_from_icon_name(icon_name)
    button.set_tooltip_text(tooltip)
    button.set_valign(Gtk.Align.CENTER)
    button.add_css_class("flat")
    for name in css:
        button.add_css_class(name)
    button.connect("clicked", lambda _button: callback())
    return button


def _text_button(label, callback, *css):
    button = Gtk.Button(label=label)
    button.set_valign(Gtk.Align.CENTER)
    for name in css:
        button.add_css_class(name)
    button.connect("clicked", lambda _button: callback())
    return button


# --- the group in the profile editor ----------------------------------------------------

def contexts_group(app, profile):
    """The "Contexts" group of ``profile``: its contexts in priority order."""
    group = Adw.PreferencesGroup(
        title="Contexts",
        description=(
            "Variants of this profile for a window title or a mode of the application "
            "(Blender's Edit Mesh, for example): another dial menu, other buttons or screen page. "
            "The first context that matches wins, so put the most specific first."
        ),
    )
    add = Gtk.Button()
    add.add_css_class("suggested-action")
    add.set_child(Adw.ButtonContent(icon_name="list-add-symbolic", label="Add context"))
    add.set_valign(Gtk.Align.CENTER)
    add.set_sensitive(cx.can_add(profile))

    def create():
        context = cx.new_context(profile)
        app._changed()
        ContextEditor(app, profile, context).present()

    add.connect("clicked", lambda _button: create())
    group.set_header_suffix(add)

    if not profile.contexts:
        empty = Adw.ActionRow(
            title="No contexts",
            subtitle="This profile does the same in every window and mode of the application.",
        )
        empty.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
        group.add(empty)
    last = len(profile.contexts) - 1
    for index, context in enumerate(profile.contexts):
        group.add(_context_row(app, profile, context, index, last))
    return group


def _context_row(app, profile, context, index, last):
    row = Adw.ActionRow(title=GLib.markup_escape_text(context.name))
    row.set_subtitle(GLib.markup_escape_text(cx.context_summary(context)))
    row.set_subtitle_lines(2)
    row.add_prefix(Gtk.Image.new_from_icon_name("view-list-symbolic"))
    if not context.has_conditions():
        row.add_css_class("dim-label")
    row.set_activatable(True)
    row.connect("activated", lambda _row: ContextEditor(app, profile, context).present())

    def move(offset):
        if cx.move_context(profile, context, offset):
            app._changed()

    up = _flat_button("go-up-symbolic", "Check this context earlier", lambda: move(-1))
    up.set_sensitive(index > 0)
    row.add_suffix(up)
    down = _flat_button("go-down-symbolic", "Check this context later", lambda: move(1))
    down.set_sensitive(index < last)
    row.add_suffix(down)

    def remove():
        if context in profile.contexts:
            profile.contexts.remove(context)
            app._changed()
            app._toast(f"Removed “{context.name}”")

    row.add_suffix(_flat_button("user-trash-symbolic", "Remove context", remove, "error"))
    return row


# --- the dialog ---------------------------------------------------------------------------

class ContextEditor:
    def __init__(self, app, profile, context):
        self.app = app
        self.profile = profile
        self.context = context
        self.closed = False
        self.catalog = {}
        self.mode_rows = []
        self.menu_rows = []
        self.title_error = None
        self._build()

    def present(self):
        self.dialog.present(self.app._window)
        self._ask_driver()

    def _save(self):
        self.app._schedule_save()

    # --- layout ------------------------------------------------------------------------

    def _build(self):
        context = self.context
        self.dialog = Adw.Dialog(title=context.name, content_width=620, content_height=760)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.page = Adw.PreferencesPage()
        view.set_content(self.page)
        self.dialog.set_child(view)

        self._build_general()
        self._build_when()
        self._build_modes()
        self._build_menu()
        self._build_buttons()
        self._build_screen()
        self._update_status()
        self.dialog.connect("closed", self._on_closed)

    def _build_general(self):
        group = Adw.PreferencesGroup(title="Context")
        name = Adw.EntryRow(title="Name")
        name.set_text(self.context.name)
        name.set_show_apply_button(True)

        def apply(entry):
            text = cx.unique_name(self.profile, entry.get_text(), ignore=self.context)
            self.context.name = text
            entry.set_text(text)
            self.dialog.set_title(text)
            self._save()

        name.connect("apply", apply)
        group.add(name)
        self.status_row = Adw.ActionRow(title="Status")
        self.status_row.set_title_lines(1)
        self.status_row.set_subtitle_lines(3)
        self.status_row.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
        group.add(self.status_row)
        self.page.add(group)

    def _build_when(self):
        group = Adw.PreferencesGroup(
            title="When",
            description=(
                "Every condition you give must hold. The window title condition is plain text without "
                "regard to case; separate alternatives with |, e.g. YouTube | Vimeo."
            ),
        )
        self.title_entry = Adw.EntryRow(title="Window title contains")
        self.title_entry.set_text(self.context.title)
        self.title_entry.connect("changed", self._on_title_changed)
        group.add(self.title_entry)

        self.try_entry = Adw.EntryRow(title="Try a window title (not saved)")
        self.try_entry.connect("changed", lambda _entry: self._update_check())
        group.add(self.try_entry)
        self.check_row = Adw.ActionRow(title="")
        self.check_row.set_title_lines(2)
        self.check_icon = Gtk.Image()
        self.check_row.add_prefix(self.check_icon)
        group.add(self.check_row)
        self.page.add(group)
        self._update_check()

    def _build_modes(self):
        self.modes_group = Adw.PreferencesGroup(title="Application mode")
        self.live_row = Adw.ActionRow(title="Right now")
        self.live_row.set_title_lines(1)
        self.live_row.set_subtitle_lines(2)
        self.live_row.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
        self.use_mode = _text_button("Use this mode", self._use_current_mode)
        self.use_mode.set_visible(False)
        self.live_row.add_suffix(self.use_mode)
        self.live_row.set_subtitle("Not connected: the modes are known once the application's add-on is running.")
        self.modes_group.add(self.live_row)

        self.modes_expander = Adw.ExpanderRow(title="Modes this context is for")
        self.modes_expander.set_expanded(True)
        self.modes_group.add(self.modes_expander)
        self.other_mode = Adw.EntryRow(title="Another mode (the application's own name for it)")
        self.other_mode.set_show_apply_button(True)
        self.other_mode.connect("apply", self._on_other_mode)
        self.modes_group.add(self.other_mode)
        self.page.add(self.modes_group)
        self._refill_modes()

    def _build_menu(self):
        self.menu_switch = Adw.SwitchRow(
            title="Own dial menu",
            subtitle="Off: the profile's functions. On: starts as a copy of them, edit it here.",
        )
        self.menu_switch.set_active(self.context.segments is not None)
        switch_group = Adw.PreferencesGroup(title="Dial menu")
        switch_group.add(self.menu_switch)
        self.page.add(switch_group)

        self.menu_group = Adw.PreferencesGroup(
            title="Functions of this context",
            description="Shown clockwise from the top of the dial, like the profile's own.",
        )
        add = Gtk.Button()
        add.add_css_class("suggested-action")
        add.set_child(Adw.ButtonContent(icon_name="list-add-symbolic", label="Add function"))
        add.set_valign(Gtk.Align.CENTER)
        add.connect("clicked", lambda _button: self.app._show_function_library(
            cx.menu_view(self.profile, self.context), None, self._refill_menu))
        self.menu_group.set_header_suffix(add)
        self.page.add(self.menu_group)
        self.menu_switch.connect("notify::active", self._on_menu_switch)
        self._refill_menu()

    def _build_buttons(self):
        group = Adw.PreferencesGroup(
            title="SpaceMouse buttons",
            description="Buttons you change here replace the profile's for this context only.",
        )
        self.buttons_row = Adw.ActionRow(title="Own buttons")
        self.buttons_row.set_subtitle_lines(2)
        self.edit_buttons = _text_button("Edit buttons…", self._edit_buttons, "suggested-action")
        self.buttons_row.add_suffix(self.edit_buttons)
        self.clear_buttons = _flat_button("edit-clear-symbolic", "Back to the profile's buttons", self._clear_buttons)
        self.buttons_row.add_suffix(self.clear_buttons)
        group.add(self.buttons_row)
        self.page.add(group)
        self._refresh_buttons_row()

    def _build_screen(self):
        group = Adw.PreferencesGroup(title="SpaceMouse screen")
        pages = ["Same as the profile", *(sp.page_title(page) for page in SCREEN_PAGES)]
        current = self.context.screen.get("start_page")
        row = Adw.ComboRow(title="Start page", model=Gtk.StringList.new(pages))
        row.set_subtitle("The page the screen shows when this context comes into force")
        row.set_selected(SCREEN_PAGES.index(current) + 1 if current in SCREEN_PAGES else 0)

        def chosen(combo, _param):
            index = combo.get_selected()
            if index <= 0:
                self.context.screen.pop("start_page", None)
            else:
                self.context.screen["start_page"] = SCREEN_PAGES[index - 1]
            self._save()

        row.connect("notify::selected", chosen)
        group.add(row)
        self.page.add(group)

    # --- the title ---------------------------------------------------------------------

    def _on_title_changed(self, entry):
        text = entry.get_text().strip()
        error = context_title_error(text) if text else None
        self.title_error = error[:1].upper() + error[1:] if error else None
        if self.title_error is None:
            entry.remove_css_class("error")
            if text != self.context.title:
                self.context.title = text
                self._save()
        else:
            entry.add_css_class("error")
        self._update_check()
        self._update_status()

    def _update_check(self):
        state, message = cx.title_check(self.title_entry.get_text().strip(), self.try_entry.get_text())
        self.check_row.set_title(GLib.markup_escape_text(message))
        for css in STATUS_CSS.values():
            self.check_icon.remove_css_class(css)
        if state in STATUS_CSS:
            self.check_icon.add_css_class(STATUS_CSS[state])
        self.check_icon.set_from_icon_name({
            "match": "emblem-ok-symbolic", "nomatch": "dialog-warning-symbolic", "invalid": "dialog-error-symbolic",
        }.get(state, "dialog-information-symbolic"))

    def _update_status(self):
        context = self.context
        if self.title_error:
            self.status_row.set_subtitle(GLib.markup_escape_text(
                f"{self.title_error}. The last valid text stays in force."))
        elif not context.has_conditions():
            self.status_row.set_subtitle("No condition yet: this context is never used. Give it a title or a mode.")
        else:
            self.status_row.set_subtitle(GLib.markup_escape_text(cx.conditions_text(context)))

    # --- the modes ---------------------------------------------------------------------

    def _refill_modes(self):
        for row in self.mode_rows:
            self.modes_expander.remove(row)
        self.mode_rows = []
        modes = cx.known_modes(self.catalog, "", self.context.app_modes)
        for mode in modes:
            row = Adw.SwitchRow(title=GLib.markup_escape_text(cx.mode_label(mode)))
            row.set_subtitle(GLib.markup_escape_text(mode) if cx.mode_label(mode) != mode else "")
            row.set_active(mode in self.context.app_modes)
            row.connect("notify::active", lambda switch, _param, mode=mode: self._toggle_mode(mode, switch.get_active()))
            self.modes_expander.add_row(row)
            self.mode_rows.append(row)
        self._modes_summary()

    def _modes_summary(self):
        chosen = [cx.mode_label(mode) for mode in self.context.app_modes]
        self.modes_expander.set_subtitle(
            ", ".join(chosen) if chosen else "Any mode (no condition on the mode)"
        )

    def _toggle_mode(self, mode, active):
        modes = self.context.app_modes
        if active and mode not in modes:
            modes.append(mode)
        elif not active and mode in modes:
            modes.remove(mode)
        self._modes_summary()
        self._update_status()
        self._save()

    def _on_other_mode(self, entry):
        mode = cx.normalize_mode(entry.get_text(), cx.known_modes(self.catalog, "", self.context.app_modes))
        entry.set_text("")
        if mode and mode not in self.context.app_modes:
            self.context.app_modes.append(mode)
            self._refill_modes()
            self._update_status()
            self._save()

    def _use_current_mode(self):
        mode = (self.catalog.get("context") or {}).get("mode")
        if mode and mode not in self.context.app_modes:
            self.context.app_modes.append(mode)
            self._refill_modes()
            self._update_status()
            self._save()

    # --- the driver --------------------------------------------------------------------

    def _ask_driver(self):
        app = self.app
        bus = app._get_session_bus()
        if bus is None or not self.profile.app_id:
            return

        def done(connection, result):
            if self.closed:
                return
            try:
                text = connection.call_finish(result).unpack()[0]
            except (GLib.Error, AttributeError):
                return
            self.catalog = cx.parse_app_commands(text)
            self._refill_modes()
            self._show_live()

        bus.call(
            DBUS_NAME, DBUS_PATH, DBUS_INTERFACE, "GetAppCommands",
            GLib.Variant("(s)", (self.profile.app_id,)), GLib.VariantType.new("(s)"),
            Gio.DBusCallFlags.NONE, 1500, None, done,
        )

    def _show_live(self):
        data = self.catalog
        context = data.get("context") if isinstance(data.get("context"), dict) else {}
        name = data.get("name") or self.profile.name
        if data.get("connected") and context.get("mode"):
            label = context.get("label") or cx.mode_label(context["mode"])
            detail = f" · {context['detail']}" if context.get("detail") else ""
            self.live_row.set_subtitle(GLib.markup_escape_text(f"{name} is in {label}{detail}"))
            self.use_mode.set_visible(context["mode"] not in self.context.app_modes)
        elif data.get("connected"):
            self.live_row.set_subtitle(GLib.markup_escape_text(f"{name} is connected but reports no mode yet"))
            self.use_mode.set_visible(False)
        elif data.get("commands"):
            self.live_row.set_subtitle(GLib.markup_escape_text(
                f"{name} is not running; its modes are from the last time it was"))
        else:
            self.live_row.set_subtitle("Not connected: the modes are known once the application's add-on is running.")

    # --- the menu ----------------------------------------------------------------------

    def _on_menu_switch(self, switch, _param):
        cx.set_own_menu(self.profile, self.context, switch.get_active())
        self._refill_menu()
        self.app._changed()

    def _refill_menu(self):
        for row in self.menu_rows:
            self.menu_group.remove(row)
        self.menu_rows = []
        own = self.context.segments is not None
        self.menu_group.set_visible(own)
        if not own:
            return
        view = cx.menu_view(self.profile, self.context)
        rows = []
        if not view.segments:
            empty = Adw.ActionRow(title="No functions", subtitle="Add some, or switch the own menu off.")
            empty.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
            rows.append(empty)
        last = len(view.segments) - 1
        for index, segment in enumerate(view.segments):
            rows.append(self.app._function_row(view, segment, index, last, None, self._refill_menu))
        reset = Adw.ActionRow(title="Start again from the profile's functions")
        reset.set_activatable(True)
        reset.add_prefix(Gtk.Image.new_from_icon_name("edit-undo-symbolic"))
        reset.connect("activated", lambda _row: self._copy_profile_menu())
        rows.append(reset)
        for row in rows:
            self.menu_group.add(row)
        self.menu_rows = rows

    def _copy_profile_menu(self):
        self.context.segments = None
        cx.set_own_menu(self.profile, self.context, True)
        self._refill_menu()
        self.app._changed()

    # --- the buttons -------------------------------------------------------------------

    def _refresh_buttons_row(self):
        context = self.context
        count = len(context.spacemouse_buttons)
        layers = len(context.spacemouse_layers)
        if count or layers:
            text = f"{count} button{'s' if count != 1 else ''} differ from the profile's"
            if layers:
                text += f", {layers} layer{'s' if layers != 1 else ''}"
        else:
            text = "The profile's buttons are used"
        available = self.app.spacemouse_page_available()
        if not available:
            text += ". The SpaceMouse page shows once a SpaceMouse is in use (Driver page: Show all pages)."
        self.buttons_row.set_subtitle(GLib.markup_escape_text(text))
        self.edit_buttons.set_sensitive(available)
        self.clear_buttons.set_visible(bool(count or layers))

    def _edit_buttons(self):
        scope = cx.ContextScope(self.profile, self.context)
        self.dialog.close()
        self.app.edit_context_buttons(scope)

    def _clear_buttons(self):
        self.context.spacemouse_buttons = {}
        self.context.spacemouse_layers = {}
        self._refresh_buttons_row()
        self.app._changed()

    # --- closing -----------------------------------------------------------------------

    def _on_closed(self, _dialog):
        self.closed = True
        if self.title_error:
            self.app._toast(f"The window title condition was not saved: {self.title_error}")
        self.app._changed()
