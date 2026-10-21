"""Widgets of the SpaceMouse page for layers, cap motion, app commands and the
Blender add-on. ``SpaceMouseExtras`` is a mixin of the settings app's
``Application``; what it decides comes from ``config_app/spacemouse_extra.py``
and ``config_app/blender_addon.py`` (GTK-free, unit tested)."""

import threading

from gi.repository import Adw, Gio, GLib, Gtk

from config.identity import DBUS_INTERFACE, DBUS_NAME, DBUS_PATH
from config_app import blender_addon
from config_app import spacemouse as sm
from config_app import spacemouse_extra as sx

POLL_MS = 2000
NEW_LAYER = "New layer…"
BASE_LAYER = "Base (no layer)"
LAYER_HINT = "Turn a layer on with a button set to Switch layer"


def _clear(widget):
    child = widget.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        widget.remove(child)
        child = following


def _switch_row(title, subtitle, active, changed):
    row = Adw.SwitchRow(title=title)
    if subtitle:
        row.set_subtitle(subtitle)
    row.set_active(active)
    row.connect("notify::active", lambda row, _param: changed(row.get_active()))
    return row


def _button_row(title, icon_name):
    if hasattr(Adw, "ButtonRow"):
        return Adw.ButtonRow(title=title, start_icon_name=icon_name)
    row = Adw.ActionRow(title=title, activatable=True)
    row.add_prefix(Gtk.Image.new_from_icon_name(icon_name))
    return row


class ActionExtras:
    """The rows of the action editor for Switch layer, Cap motion and App
    command. ``show(kind)`` makes the rows of the chosen type visible,
    ``sync()`` shows the working action in them. The editor owns the working
    action and ``commit``; ``resync`` redraws the whole section."""

    def __init__(self, app, group, working, slot, commit, syncing, resync=None):
        self.app = app
        self.working = working
        self.slot = slot
        self.commit = commit
        self.syncing = syncing
        self.resync = resync
        self.profile = app._sm_scope
        self.app_name = sx.app_name_for(self.profile)
        self.catalog = app._sm_catalogs.get(self.app_name, sx.Catalog()) if self.app_name else sx.Catalog()
        self.layer_names = []

        self.layer_row = Adw.ComboRow(title="Layer")
        self.mode_row = Adw.ComboRow(
            title="Mode", model=Gtk.StringList.new([label for _key, label, _text in sm.LAYER_MODES]),
        )
        self.motion_row = Adw.ComboRow(
            title="Cap motion", subtitle="For other programs, until you reset it or leave the application",
            model=Gtk.StringList.new([label for _key, label in sm.MOTION_CHOICES]),
        )
        self.command_row = Adw.ActionRow(title="Command", activatable=False)
        self.command_row.set_subtitle_lines(0)
        self.pick_button = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        self.command_row.add_suffix(self.pick_button)
        self.id_row = Adw.EntryRow(title="Command id, as the application's add-on names it")
        self.id_row.set_show_apply_button(True)
        for row in (self.layer_row, self.mode_row, self.motion_row, self.command_row, self.id_row):
            group.add(row)
        self.layer_row.connect("notify::selected", self._layer_changed)
        self.mode_row.connect("notify::selected", self._mode_changed)
        self.motion_row.connect("notify::selected", self._motion_changed)
        self.pick_button.connect("clicked", lambda _button: self._pick())
        self.id_row.connect("apply", self._id_applied)
        if self.app_name:
            app._sm_catalog(self.app_name, self._catalog_arrived)

    # What is shown ---------------------------------------------------------------------

    def show(self, kind):
        self.layer_row.set_visible(kind == "layer")
        self.mode_row.set_visible(kind == "layer")
        self.motion_row.set_visible(kind == "motion")
        self.command_row.set_visible(kind == "app")
        self.id_row.set_visible(kind == "app" and not self.catalog.known)

    def sync(self):
        action = self.working[self.slot]
        self.layer_names = sx.action_layer_names(self.app.config, self.profile)
        if action is not None and action.type == "layer" and action.layer and action.layer not in self.layer_names:
            self.layer_names.append(action.layer)
        choices = ["Choose a layer…", *self.layer_names, NEW_LAYER]
        self.layer_row.set_model(Gtk.StringList.new(choices))
        if action is not None and action.type == "layer" and action.layer in self.layer_names:
            self.layer_row.set_selected(1 + self.layer_names.index(action.layer))
        else:
            self.layer_row.set_selected(0)
        if action is not None and action.type == "layer":
            keys = [key for key, _label, _text in sm.LAYER_MODES]
            mode = action.layer_mode if action.layer_mode in keys else "hold"
            self.mode_row.set_selected(keys.index(mode))
            self.mode_row.set_subtitle(sm.LAYER_MODES[keys.index(mode)][2])
            self.layer_row.set_subtitle("" if self.layer_names else f"No layer yet: choose “{NEW_LAYER}”")
        if action is not None and action.type == "motion":
            keys = [key for key, _label in sm.MOTION_CHOICES]
            self.motion_row.set_selected(keys.index(action.motion) if action.motion in keys else 0)
        if action is not None and action.type == "app":
            self.id_row.set_text(action.command or "")
            self._show_command()

    def _show_command(self):
        action = self.working[self.slot]
        command_id = action.command if action is not None and action.type == "app" else None
        found = self.catalog.find(command_id) if command_id else None
        if found is not None:
            keys = f" · default {found.keys}" if found.keys else ""
            subtitle = f"{found.category} · {found.id}{keys}" if found.category else f"{found.id}{keys}"
            self.command_row.set_title(found.title)
        elif command_id:
            self.command_row.set_title(command_id)
            subtitle = "Not in the application's list" if self.catalog.known else sx.describe_catalog(self.catalog)
        else:
            self.command_row.set_title("Command")
            if self.catalog.known:
                subtitle = sx.describe_catalog(self.catalog)
            elif self.app_name:
                subtitle = sx.describe_catalog(self.catalog) + ". Type the command id below."
            else:
                subtitle = ("Commands belong to an application: choose a profile under “Applies to” "
                            "to pick from its list, or type a command id below.")
        self.command_row.set_subtitle(subtitle)
        self.command_row.set_activatable(self.catalog.known)
        self.pick_button.set_visible(self.catalog.known)
        self.id_row.set_visible(self.working[self.slot] is not None and self.working[self.slot].type == "app"
                                and not self.catalog.known)

    def _catalog_arrived(self, catalog):
        self.catalog = catalog
        action = self.working[self.slot]
        if action is not None and action.type == "app":
            self._show_command()

    # Edits ----------------------------------------------------------------------------

    def _action(self, kind):
        action = self.working[self.slot]
        if self.syncing["active"] or action is None or action.type != kind:
            return None
        return action

    def _layer_changed(self, row, _param):
        action = self._action("layer")
        if action is None:
            return
        position = row.get_selected()
        if position == len(self.layer_names) + 1:
            # New layer…: ask for the name; the choice returns to the old one meanwhile.
            self.syncing["active"] = True
            row.set_selected(1 + self.layer_names.index(action.layer) if action.layer in self.layer_names else 0)
            self.syncing["active"] = False
            self.app._ask_new_layer(self._layer_created)
            return
        action.layer = self.layer_names[position - 1] if position >= 1 else None
        if sm.is_valid(action):
            self.commit()

    def _layer_created(self, name):
        action = self.working[self.slot]
        if action is not None and action.type == "layer":
            action.layer = name
            self.commit()
            if self.resync:
                self.resync()

    def _mode_changed(self, row, _param):
        action = self._action("layer")
        if action is None:
            return
        keys = [key for key, _label, _text in sm.LAYER_MODES]
        position = row.get_selected()
        action.layer_mode = keys[position] if 0 <= position < len(keys) else "hold"
        row.set_subtitle(sm.LAYER_MODES[keys.index(action.layer_mode)][2])
        if sm.is_valid(action):
            self.commit()

    def _motion_changed(self, row, _param):
        action = self._action("motion")
        if action is None:
            return
        keys = [key for key, _label in sm.MOTION_CHOICES]
        action.motion = keys[row.get_selected()] if 0 <= row.get_selected() < len(keys) else "rotation"
        self.commit()

    def _id_applied(self, row):
        action = self._action("app")
        if action is None:
            return
        action.command = row.get_text().strip()[:128] or None
        self._show_command()
        if self.resync:
            self.resync()
        if sm.is_valid(action):
            self.commit()

    def _pick(self):
        self.app._show_command_picker(self.app_name, self.catalog, self._picked)

    def _picked(self, command):
        action = self.working[self.slot]
        if action is None or action.type != "app":
            return
        action.command = command.id
        if not action.keys:
            action.keys = sx.fallback_keys(command)
        self._show_command()
        if self.resync:
            self.resync()
        self.commit()


class SpaceMouseExtras:
    """Layers, cap motion, app commands and the Blender add-on of the SpaceMouse page."""

    # --- state --------------------------------------------------------------------------

    def _init_spacemouse_extras(self):
        # The layer edited (None = the base map), the cached command lists by
        # application, the page's containers and the timer that follows the driver.
        self._sm_layer = None
        self._sm_layer_row = None
        self._sm_layer_handler = 0
        self._sm_layer_rename = None
        self._sm_layer_remove = None
        self._sm_catalogs = {}
        self._sm_motion_box = None
        self._sm_motion_status_row = None
        self._sm_blender_group = None
        self._sm_blender_status = None
        self._sm_blender_button = None
        self._sm_poll_source = 0

    def _config(self):
        return self.config

    def _sm_titles(self):
        """Command id -> title of the lists seen so far, to name app actions in the rows."""
        titles = {}
        for catalog in self._sm_catalogs.values():
            titles.update(catalog.titles())
        return titles

    # --- driver calls ------------------------------------------------------------------

    def _call_driver_string(self, method, argument, callback):
        """Call a driver method returning a string; ``callback(text or None)`` runs in the main loop."""
        connection = self._get_session_bus()
        if connection is None:
            callback(None)
            return

        def done(conn, result):
            try:
                text = conn.call_finish(result).unpack()[0]
            except (GLib.Error, AttributeError, IndexError):
                text = None
            callback(text)

        connection.call(
            DBUS_NAME, DBUS_PATH, DBUS_INTERFACE, method,
            GLib.Variant("(s)", (argument,)) if argument is not None else None,
            GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE, 3000, None, done,
        )

    def _sm_catalog(self, app_name, callback):
        """Ask the driver for the command list of an application; the answer is
        remembered, and a driver that does not answer leaves the cached one."""
        def arrived(text):
            if text is None:
                catalog = self._sm_catalogs.get(app_name, sx.Catalog())
            else:
                catalog = sx.parse_catalog(text)
                self._sm_catalogs[app_name] = catalog
            callback(catalog)

        self._call_driver_string("GetAppCommands", app_name, arrived)

    def _start_spacemouse_poll(self):
        if self._sm_poll_source:
            return
        self._sm_poll_source = GLib.timeout_add(POLL_MS, self._poll_spacemouse_state)
        GLib.idle_add(lambda: self._poll_spacemouse_state() and GLib.SOURCE_REMOVE)

    def _spacemouse_page_visible(self):
        stack = getattr(self, "_view_stack", None)
        return stack is not None and stack.get_visible_child_name() == "spacemouse"

    def _poll_spacemouse_state(self):
        if self._spacemouse_page_visible():
            if self._sm_motion_status_row is not None:
                self._call_driver_string("GetDriverState", None, self._on_spacemouse_driver_state)
            if self._sm_blender_group is not None and self._sm_blender_group.get_visible():
                self._sm_catalog("blender", self._on_blender_catalog)
        return GLib.SOURCE_CONTINUE

    def _on_spacemouse_driver_state(self, text):
        row = self._sm_motion_status_row
        if row is None:
            return
        state = sx.parse_driver_state(text) if text is not None else {}
        row.set_title(sx.motion_status(state.get("motion")))

    # --- layers --------------------------------------------------------------------------

    def _spacemouse_layer_row(self):
        """The Layer selector (under “Applies to”) with rename and remove."""
        row = Adw.ComboRow(title="Layer", subtitle=LAYER_HINT, model=Gtk.StringList.new([BASE_LAYER, NEW_LAYER]))
        self._sm_layer_handler = row.connect("notify::selected", self._on_spacemouse_layer)
        rename = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Rename the layer")
        rename.add_css_class("flat")
        rename.connect("clicked", lambda _button: self._rename_layer())
        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Remove the layer")
        remove.add_css_class("flat")
        remove.connect("clicked", lambda _button: self._confirm_remove_layer())
        row.add_suffix(rename)
        row.add_suffix(remove)
        self._sm_layer_row, self._sm_layer_rename, self._sm_layer_remove = row, rename, remove
        return row

    def _refresh_layer_row(self):
        """Follow the layers of the scope (added, removed, another profile)."""
        row = self._sm_layer_row
        if row is None or self.config is None:
            return
        names = sx.layer_names(self.config, self._sm_scope)
        if self._sm_layer not in names:
            self._sm_layer = None
        labels = [BASE_LAYER]
        for name in names:
            labels.append(f"{name}  ·  from All applications" if sx.is_inherited_layer(self.config, self._sm_scope, name) else name)
        labels.append(NEW_LAYER)
        row.handler_block(self._sm_layer_handler)
        row.set_model(Gtk.StringList.new(labels))
        row.set_selected(0 if self._sm_layer is None else 1 + names.index(self._sm_layer))
        row.handler_unblock(self._sm_layer_handler)
        own = self._sm_layer is not None and self._sm_layer in sx.scope_layers(self.config, self._sm_scope)
        shared = self._sm_scope is not None and own and self._sm_layer in self.config.spacemouse.layers
        self._sm_layer_rename.set_visible(own and not shared)
        self._sm_layer_remove.set_visible(own)
        self._sm_layer_remove.set_tooltip_text(
            "Remove this application's bindings of the layer" if shared else "Remove the layer"
        )
        if self._sm_layer is None:
            row.set_subtitle(LAYER_HINT)
        elif own:
            row.set_subtitle("Keys you set here replace the base ones while the layer is on; the others keep their base binding")
        else:
            row.set_subtitle("A layer of All applications: change a key to give this application its own binding")

    def _on_spacemouse_layer(self, row, _param):
        names = sx.layer_names(self.config, self._sm_scope)
        position = row.get_selected()
        if position == len(names) + 1:
            # New layer…: back to the layer in use while the name is asked.
            self._refresh_layer_row()
            self._ask_new_layer(self._select_layer)
            return
        layer = names[position - 1] if position >= 1 else None
        if layer != self._sm_layer:
            self._sm_layer = layer
            self._refresh_layer_row()
            self._rebuild_spacemouse_rows()

    def _select_layer(self, name):
        self._sm_layer = name
        self._refresh_layer_row()
        self._rebuild_spacemouse_rows()

    def _ask_new_layer(self, done, heading="New layer"):
        """Ask for a name, make the layer in the scope and call ``done(name)``."""
        def accept(text):
            try:
                name = sx.add_layer(self.config, self._sm_scope, text)
            except ValueError as error:
                self._toast(str(error))
                return
            self._schedule_save()
            done(name)
            self._refresh_layer_row()

        self._show_name_dialog(heading, "", accept)

    def _rename_layer(self):
        old = self._sm_layer
        if old is None:
            return

        def accept(text):
            try:
                name = sx.rename_layer(self.config, self._sm_scope, old, text)
            except ValueError as error:
                self._toast(str(error))
                return
            self._sm_layer = name
            self._schedule_save()
            self._refresh_layer_row()
            self._rebuild_spacemouse_rows()

        self._show_name_dialog(f"Rename the layer “{old}”", old, accept)

    def _confirm_remove_layer(self):
        name = self._sm_layer
        if name is None:
            return
        users = sx.layer_users(self.config, self._sm_scope, name)
        body = "Its bindings are deleted."
        if users:
            body += f" {users} button{'s' if users != 1 else ''} that switched to it will pass through instead."
        dialog = Adw.AlertDialog(heading=f"Remove the layer “{name}”?", body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def respond(_dialog, response):
            if response != "remove":
                return
            sx.remove_layer(self.config, self._sm_scope, name)
            self._sm_layer = None
            self._schedule_save()
            self._refresh_layer_row()
            self._rebuild_spacemouse_rows()

        dialog.connect("response", respond)
        dialog.present(self._window)

    # --- the app command picker ----------------------------------------------------------

    def _show_command_picker(self, app_name, catalog, picked):
        """A searchable list of an application's commands by category, with their default keys."""
        title = f"Commands of {catalog.name}" if catalog.name else "Application commands"
        dialog = Adw.Dialog(title=title, content_width=520, content_height=640)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for setter in (box.set_margin_start, box.set_margin_end, box.set_margin_bottom):
            setter(12)
        search = Gtk.SearchEntry(placeholder_text="Search commands")
        box.append(search)
        note = Gtk.Label(label=sx.describe_catalog(catalog), xalign=0, wrap=True)
        note.add_css_class("dim-label")
        note.add_css_class("caption")
        box.append(note)
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(listbox)
        box.append(scroll)

        for _category, commands in sx.grouped_commands(catalog.commands):
            for command in commands:
                row = Adw.ActionRow(title=GLib.markup_escape_text(command.title), subtitle=GLib.markup_escape_text(command.id))
                row.set_title_lines(1)
                row.set_subtitle_lines(1)
                if command.keys:
                    keys = Gtk.Label(label=command.keys)
                    keys.add_css_class("dim-label")
                    keys.add_css_class("caption")
                    keys.set_valign(Gtk.Align.CENTER)
                    row.add_suffix(keys)
                row.set_activatable(True)
                row._command = command
                row.connect("activated", lambda activated: (dialog.close(), picked(activated._command)))
                listbox.append(row)

        def header(row, before):
            category = row._command.category or "Other"
            if before is None or (before._command.category or "Other") != category:
                label = Gtk.Label(label=category, xalign=0)
                label.add_css_class("heading")
                label.set_margin_top(10)
                label.set_margin_bottom(4)
                label.set_margin_start(6)
                row.set_header(label)
            else:
                row.set_header(None)

        listbox.set_header_func(header)

        def shown(row):
            return row._command in matching

        matching = list(catalog.commands)

        def search_changed(entry):
            matching[:] = sx.filter_commands(catalog.commands, entry.get_text())
            listbox.invalidate_filter()
            listbox.invalidate_headers()

        listbox.set_filter_func(shown)
        search.connect("search-changed", search_changed)
        view.set_content(box)
        dialog.set_child(view)
        dialog.present(self._window)
        search.grab_focus()

    # --- cap motion ------------------------------------------------------------------------

    def _spacemouse_motion_group(self):
        """Container of the “Cap motion” group, filled for the scope on every change."""
        self._sm_motion_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self._fill_motion()
        return self._sm_motion_box

    def _fill_motion(self):
        box = self._sm_motion_box
        if box is None or self.config is None:
            return
        _clear(box)
        config, profile = self.config, self._sm_scope
        if getattr(profile, "parent", None) is not None:
            # A context of a profile (contexts.ContextScope) shares its cap motion.
            self._sm_motion_status_row = None
            note = Adw.PreferencesGroup(title="Cap motion")
            row = Adw.ActionRow(title=f"Set per application: choose “{profile.parent.name}” under Applies to")
            row.set_title_lines(0)
            row.add_prefix(Gtk.Image.new_from_icon_name("input-gaming-symbolic"))
            note.add(row)
            box.append(note)
            return
        values = sx.motion_shown(config, profile)
        group = Adw.PreferencesGroup(
            title="Cap motion",
            description=(
                "How the cap's movement reaches other programs (CAD, 3D, games) through spacenavd, "
                "for All applications or one application. Neutral settings leave spacenavd as it is."
            ),
        )
        box.append(group)
        self._sm_motion_status_row = Adw.ActionRow(title=sx.motion_status(None))
        self._sm_motion_status_row.set_title_lines(0)
        self._sm_motion_status_row.add_prefix(Gtk.Image.new_from_icon_name("input-gaming-symbolic"))
        group.add(self._sm_motion_status_row)
        editable = True
        if profile is not None:
            def use_global_changed(active):
                sx.use_global_motion(profile, active, config)
                self._schedule_save()
                GLib.idle_add(lambda: self._fill_motion() or GLib.SOURCE_REMOVE)

            editable = sx.motion_overridden(profile)
            group.add(_switch_row(
                "Use the global settings", f"Off: {profile.name} gets the motion below",
                not editable, use_global_changed,
            ))
        rows = []

        def change(name, value, index=None):
            sx.set_motion(config, profile, name, value, index)
            self._schedule_save()

        sensitivity = Adw.ActionRow(title="Sensitivity", subtitle="Multiplies spacenavd's own; 1 leaves it as it is")
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        scale.set_value(sx.position_from_sensitivity(values["sensitivity"]))
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.set_format_value_func(lambda _scale, position: f"×{sx.sensitivity_from_position(position):g}")
        scale.add_mark(50, Gtk.PositionType.BOTTOM, None)
        scale.set_size_request(180, -1)
        scale.set_valign(Gtk.Align.CENTER)
        scale.connect("value-changed", lambda scale: change("sensitivity", sx.sensitivity_from_position(scale.get_value())))
        sensitivity.add_suffix(scale)
        rows.append(sensitivity)
        rows.append(_switch_row(
            "Rotation", "Tilting and twisting the cap reaches other programs", values["rotation"],
            lambda active: change("rotation", active),
        ))
        rows.append(_switch_row(
            "Pan and zoom", "Sliding, pushing and pulling the cap reaches other programs", values["translation"],
            lambda active: change("translation", active),
        ))
        expander = Adw.ExpanderRow(title="Axes", subtitle="Sensitivity and direction per axis")
        for index, (axis, help_text) in enumerate(zip(sx.AXIS_NAMES, sx.AXIS_HELP)):
            spin = Adw.SpinRow.new_with_range(0, 4, 0.1)
            spin.set_title(f"{axis} · {help_text}")
            spin.set_subtitle("0 turns this axis off")
            spin.set_digits(1)
            spin.set_value(values["axis_sensitivity"][index])
            spin.connect("notify::value", lambda row, _param, index=index: change("axis_sensitivity", row.get_value(), index))
            expander.add_row(spin)
            expander.add_row(_switch_row(
                f"Invert {axis}", "", values["invert"][index],
                lambda active, index=index: change("invert", active, index),
            ))
        rows.append(expander)
        reset = _button_row("Back to spacenavd's own settings", "edit-undo-symbolic")

        def reset_motion(_row):
            sx.reset_motion(config, profile)
            self._schedule_save()
            self._fill_motion()

        reset.connect("activated", reset_motion)
        rows.append(reset)
        for row in rows:
            row.set_sensitive(editable)
            group.add(row)

    # --- the Blender add-on -------------------------------------------------------------------

    def _spacemouse_blender_group(self):
        group = Adw.PreferencesGroup(
            title="Blender",
            description=(
                "The Turnpad add-on lets buttons run Blender commands by name instead of guessing shortcuts, "
                "and tells Turnpad which mode Blender is in. Restart Blender after installing it."
            ),
        )
        self._sm_blender_status = Adw.ActionRow(title="Add-on", subtitle="Not asked yet")
        self._sm_blender_status.set_subtitle_lines(0)
        group.add(self._sm_blender_status)
        self._sm_blender_button = _button_row("Install the Blender add-on", "system-software-install-symbolic")
        self._sm_blender_button.connect("activated", lambda _row: self._install_blender_addon())
        group.add(self._sm_blender_button)
        self._sm_blender_group = group
        self._refresh_blender_group()
        return group

    def _refresh_blender_group(self):
        group = self._sm_blender_group
        if group is None or self.config is None:
            return
        group.set_visible(blender_addon.blender_wanted(self.config.profiles))

    def _on_blender_catalog(self, catalog):
        if self._sm_blender_status is not None:
            self._sm_blender_status.set_subtitle(sx.describe_catalog(catalog))

    def _install_blender_addon(self, button=None, label="Install the Blender add-on"):
        """In a thread; ``button`` (a row or a button) waits meanwhile and gets ``label`` back."""
        button = button if button is not None else self._sm_blender_button
        set_text = button.set_title if hasattr(button, "set_title") else button.set_label
        button.set_sensitive(False)
        set_text("Installing the add-on…")

        def work():
            result = blender_addon.install(blender=blender_addon.find_blender())
            GLib.idle_add(finished, result)

        def finished(result):
            button.set_sensitive(True)
            set_text(label)
            self._show_blender_result(result)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, daemon=True).start()

    def _show_blender_result(self, result):
        """What the installation did: the commands and their output, and the manual steps when Blender did not."""
        dialog = Adw.Dialog(title="Blender add-on", content_width=600, content_height=520)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for setter in (box.set_margin_top, box.set_margin_bottom, box.set_margin_start, box.set_margin_end):
            setter(16)
        if result.ok:
            summary = "The add-on is installed and switched on in Blender. " + blender_addon.RESTART_HINT
        elif result.zip_path is not None:
            summary = "The add-on is built but not installed. " + result.manual
        else:
            summary = "The add-on could not be installed."
        heading = Gtk.Label(label=summary, xalign=0, wrap=True, selectable=True)
        box.append(heading)
        if result.zip_path is not None and not result.ok:
            path = Gtk.Label(label=str(result.zip_path), xalign=0, wrap=True, selectable=True)
            path.add_css_class("monospace")
            box.append(path)
        text = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        text.get_buffer().set_text(result.log)
        text.set_top_margin(8)
        text.set_bottom_margin(8)
        text.set_left_margin(8)
        text.set_right_margin(8)
        scroll = Gtk.ScrolledWindow(vexpand=True, min_content_height=120)
        scroll.add_css_class("card")
        scroll.set_child(text)
        box.append(scroll)
        if result.zip_path is not None and not result.ok:
            folder = Gtk.Button(label="Open the folder", halign=Gtk.Align.START)

            def open_folder(_button):
                try:
                    Gtk.FileLauncher.new(Gio.File.new_for_path(str(result.zip_path))).open_containing_folder(
                        self._window, None, None,
                    )
                except (GLib.Error, AttributeError):
                    self._toast(f"The zip is {result.zip_path}")

            folder.connect("clicked", open_folder)
            box.append(folder)
        view.set_content(box)
        dialog.set_child(view)
        dialog.present(self._window)

    # --- the action editor ---------------------------------------------------------------------

    def _sm_extra_action_rows(self, group, working, slot, commit, syncing):
        return ActionExtras(self, group, working, slot, commit, syncing)