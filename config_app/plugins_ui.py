"""The Plugins page of the settings app: Turnpad's links to applications in one
place. ``PluginsPage`` is a mixin of ``Application``; what it decides comes from
``config_app/plugins.py`` (GTK-free, unit tested) and, for Blender, from
``config_app/blender_addon.py`` and ``spacemouse_extra.describe_catalog``."""

from gi.repository import Adw, GLib, Gtk

from config_app import plugins as pl
from config_app import spacemouse_extra as sx
from integrations.solidcraft import plugin

SOLIDCRAFT_INTRO = (
    "SolidCraft has no add-ons yet; Turnpad joins it as a plugin of its own. The Turnpad driver "
    "talks to SolidCraft through its control port, so the dial and SpaceMouse buttons run SolidCraft's "
    "commands and turn its view, and contexts follow its ribbon tab. Turn the plugin off in SolidCraft "
    "(Preferences › Plugins) to unlink it. Guide: SOLIDCRAFT.md."
)
BLENDER_INTRO = (
    "The Turnpad add-on lets the dial and buttons run Blender commands by name instead of guessing "
    "shortcuts, and tells Turnpad which mode Blender is in. Restart Blender after installing it."
)


def _suffix_button(row, label, callback, sensitive=True):
    button = Gtk.Button(label=label)
    button.set_valign(Gtk.Align.CENTER)
    button.set_sensitive(sensitive)
    button.connect("clicked", lambda _button: callback())
    row.add_suffix(button)
    return button


class PluginsPage:
    """Builds and refreshes the Plugins page."""

    def _init_plugins_page(self):
        self._pl_link_row = None
        self._pl_blender_row = None

    def _build_plugins_page(self):
        content = self._page_box()
        content.append(self._plugins_solidcraft_group())
        content.append(self._plugins_blender_group())
        return self._scrolled_content(content)

    # --- SolidCraft ----------------------------------------------------------------------

    def _plugins_solidcraft_group(self):
        group = Adw.PreferencesGroup(title="SolidCraft", description=SOLIDCRAFT_INTRO)
        if not pl.solidcraft_present():
            group.set_description(SOLIDCRAFT_INTRO + " SolidCraft does not seem to be installed here.")
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh")
        refresh.add_css_class("flat")
        refresh.set_valign(Gtk.Align.CENTER)
        refresh.connect("clicked", lambda _button: self._refresh_plugins_page())
        group.set_header_suffix(refresh)

        setting = pl.port_setting()
        folders = pl.plugin_folders()
        self._pl_link_row = Adw.ActionRow(title="Link", subtitle="Asking the driver…")
        self._pl_link_row.set_subtitle_lines(0)
        group.add(self._pl_link_row)
        self._pl_setting, self._pl_folders = setting, folders

        port_row = Adw.ActionRow(title="Control port", subtitle=pl.describe_port(setting))
        port_row.set_subtitle_lines(0)
        if setting.port is None:
            _suffix_button(port_row, "Turn on", self._plugins_turn_port_on)
        elif setting.ours:
            _suffix_button(port_row, "Turn off", self._plugins_turn_port_off)
        group.add(port_row)

        for folder in folders:
            row = Adw.ActionRow(title=f"Turnpad plugin · {folder.kind}", subtitle=pl.describe_folder(folder))
            row.set_subtitle_lines(0)
            action = pl.plugin_action(folder.state)
            if action is not None:
                _suffix_button(row, action, lambda root=folder.root, action=action: self._plugins_change(root, action))
            group.add(row)

        profile = next((profile for profile in self.config.profiles if "solidcraft" in (profile.app_id or "").lower()), None)
        profile_row = Adw.ActionRow(
            title="Profile",
            subtitle=(f"“{profile.name}” runs for SolidCraft" if profile is not None
                      else "No SolidCraft profile yet: the ready one turns the view and starts SolidCraft's tools"),
        )
        profile_row.set_subtitle_lines(0)
        if profile is None:
            _suffix_button(profile_row, "Add profile", lambda: self._add_profile_for_app_id(pl.SOLIDCRAFT_DESKTOP_ID))
        group.add(profile_row)
        return group

    def _plugins_turn_port_on(self):
        try:
            path = pl.write_port()
        except (OSError, ValueError) as error:
            self._toast(f"Could not write the port setting: {error}")
            return
        self._toast(f"Port {pl.DEFAULT_PORT} set in {path}; log out and in once")
        self._refresh_plugins_page()

    def _plugins_turn_port_off(self):
        try:
            pl.remove_port()
        except OSError as error:
            self._toast(f"Could not remove the port setting: {error}")
            return
        self._toast("SolidCraft starts without a control port from the next login")
        self._refresh_plugins_page()

    def _plugins_change(self, root, action):
        try:
            if action == "Remove":
                plugin.remove(root)
                message = "Turnpad removed from SolidCraft's plugins"
            else:
                plugin.install(root)
                message = "Turnpad added to SolidCraft's plugins; the driver links SolidCraft within seconds"
        except OSError as error:
            self._toast(f"Could not change the plugin: {error}")
            return
        self._toast(message)
        self._refresh_plugins_page()

    def _on_plugins_status(self, connection, result, *_user_data):
        row = self._pl_link_row
        if row is None:
            return
        status = None
        try:
            if connection is not None:
                unpacked = connection.call_finish(result).unpack()
                status = {key: self._unpack_dbus_value(value) for key, value in (unpacked[0] if unpacked else {}).items()}
        except (GLib.Error, AttributeError, IndexError):
            status = None
        row.set_subtitle(pl.describe_link(pl.bridge_status(status), self._pl_setting, self._pl_folders))

    # --- Blender -------------------------------------------------------------------------

    def _plugins_blender_group(self):
        group = Adw.PreferencesGroup(title="Blender", description=BLENDER_INTRO)
        self._pl_blender_row = Adw.ActionRow(title="Add-on", subtitle="Not asked yet")
        self._pl_blender_row.set_subtitle_lines(0)
        group.add(self._pl_blender_row)
        install_row = Adw.ActionRow(title="Install the Blender add-on",
                                    subtitle="Builds it from the copy next to Turnpad and installs it into Blender")
        install_row.set_subtitle_lines(0)
        button = Gtk.Button(label="Install")
        button.set_valign(Gtk.Align.CENTER)
        button.connect("clicked", lambda button: self._install_blender_addon(button, "Install"))
        install_row.add_suffix(button)
        group.add(install_row)
        return group

    def _on_plugins_blender(self, catalog):
        if self._pl_blender_row is not None:
            self._pl_blender_row.set_subtitle(sx.describe_catalog(catalog))

    # --- refresh -------------------------------------------------------------------------

    def _refresh_plugins_page(self):
        """Build the page again from the files, then ask the driver (both answers arrive later)."""
        self._rebuild_page("plugins")
        self._call_driver("GetStatus", "(a{sv})", self._on_plugins_status)
        self._sm_catalog("blender", self._on_plugins_blender)
