"""Names and identifiers of the project, in one place.

The display name is free to change. The technical identifiers below are a
contract between separately installed parts (driver, settings app, Shell
extension, installer) and with existing user configuration; changing one
means changing its twin outside Python too. tests/test_identity.py checks
every twin, so a rename that misses one fails the tests.

Installs from before the rename (asus-dialpad-driver) are moved over by
install_migrate.sh.
"""

# Shown to people: window titles, launcher, installer messages.
APP_NAME = "Turnpad"
APP_SUMMARY = "Touchpad dial for ASUS laptops with a DialPad"
REPOSITORY_URL = "https://github.com/sinu0/asus-turnpad"
ISSUES_URL = f"{REPOSITORY_URL}/issues"

# Technical name: install and config directories, services, log names.
# Twin: install_paths.sh.
SLUG = "asus-turnpad"

# The driver's systemd user unit. Twin: install_paths.sh.
DRIVER_SERVICE = f"{SLUG}.service"

# Settings app (GApplication id, launcher and icon name).
# Twins: config_app/<APP_ID>.desktop, install_paths.sh.
APP_ID = "io.github.sinu0.AsusTurnpad"

# Session D-Bus service of the driver.
# Twins: BUS_NAME/BUS_PATH in gnome/extension/extension.js.
DBUS_NAME = "io.github.sinu0.AsusTurnpad.Driver"
DBUS_PATH = "/io/github/sinu0/AsusTurnpad/Driver"
DBUS_INTERFACE = DBUS_NAME

# Directory under ~/.config with config.json, skins/ and the driver state.
# Twins: CONFIG_DIR in gnome/extension/extension.js, install_paths.sh.
CONFIG_DIR_NAME = SLUG

# Twins: uuid in gnome/extension/metadata.json, install_paths.sh.
GNOME_EXTENSION_UUID = "asus-turnpad@sinu0.github.io"

# User service of the on-screen dial on layer-shell compositors
# (overlay/layer_shell.py). Twins: install_paths.sh,
# packaging/asus-turnpad-setup, the unit files <OVERLAY_SERVICE>.
OVERLAY_SERVICE = f"{SLUG}-overlay.service"

# User service of the SpaceMouse screen (screen/service.py): started by the
# udev rule of the device, not enabled. Twins: install_paths.sh, the unit
# files <SCREEN_SERVICE>, UDEV_RULE_SCREEN in install_user_groups.sh and
# packaging/70-asus-turnpad.rules.
SCREEN_SERVICE = f"{SLUG}-screen.service"

# Twin: install_paths.sh.
INSTALL_DIR = "/opt/asus-turnpad"

# Where distribution packages put the program (packaging/).
# Twins: packaging/asus-turnpad.service, packaging/install-files.sh, install.sh.
PACKAGE_DIR = f"/usr/share/{SLUG}"

# Socket for the legacy PySide overlay, in $XDG_RUNTIME_DIR.
SOCKET_NAME = f"{SLUG}.sock"


# Socket of the app link in $XDG_RUNTIME_DIR: applications (the Blender add-on)
# tell the driver their commands and context and run commands on request.
# Twin: SOCKET_NAME in integrations/blender/turnpad/protocol.py.
APP_LINK_SOCKET_NAME = f"{SLUG}-apps.sock"


def app_link_socket_path(environ=None):
    """$XDG_RUNTIME_DIR/asus-turnpad-apps.sock; None without a runtime directory
    (no fallback to /tmp: the socket must live in a private directory)."""
    import os

    environ = os.environ if environ is None else environ
    runtime_dir = environ.get("XDG_RUNTIME_DIR")
    return os.path.join(runtime_dir, APP_LINK_SOCKET_NAME) if runtime_dir else None


def socket_path() -> str:
    import os
    import tempfile

    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return os.path.join(runtime_dir, SOCKET_NAME)
