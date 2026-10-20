#!/usr/bin/env bash

# Names and locations of an installed Turnpad, sourced by every install and
# uninstall script. Twins in config/identity.py; tests/test_identity.py
# checks that both agree. Each path can be overridden from the environment.
#
# Layout (Filesystem Hierarchy Standard for software installed outside the
# package manager):
#   /opt/asus-turnpad                  program (root)
#   ~/.config/asus-turnpad             config.json, skins/, driver state (user)
#   ~/.config/systemd/user             user services
#   /etc/udev/rules.d, /etc/modules-load.d   device access, kernel modules
#   /var/log/asus-turnpad              installer logs

SLUG="asus-turnpad"
APP_ID="io.github.sinu0.AsusTurnpad"
GNOME_EXTENSION_UUID="asus-turnpad@sinu0.github.io"

# User services: the driver, the legacy dial window (X11), the dial for
# layer-shell compositors (Wayland desktops other than GNOME) and the screen
# of a SpacePilot Pro (started by udev, see UDEV_RULE_SCREEN).
DRIVER_SERVICE="$SLUG.service"
UI_SERVICE="$SLUG-ui.service"
OVERLAY_SERVICE="$SLUG-overlay.service"
SCREEN_SERVICE="$SLUG-screen.service"

: "${INSTALL_DIR_PATH:=/opt/asus-turnpad}"
: "${CONFIG_FILE_DIR_PATH:=${XDG_CONFIG_HOME:-$HOME/.config}/$SLUG}"
: "${CONFIG_FILE_NAME:=dialpad_dev}"
: "${CONFIG_FILE_PATH:=$CONFIG_FILE_DIR_PATH/$CONFIG_FILE_NAME}"
: "${LOGS_DIR_PATH:=/var/log/$SLUG}"
: "${SERVICE_INSTALL_DIR_PATH:=$HOME/.config/systemd/user}"
: "${INSTALL_UDEV_DIR_PATH:=/etc/udev}"
: "${MODULES_LOAD_FILE_PATH:=/etc/modules-load.d/$SLUG.conf}"

GNOME_EXTENSION_DIR="$HOME/.local/share/gnome-shell/extensions/$GNOME_EXTENSION_UUID"
LAUNCHER_PATH="$HOME/.local/share/applications/$APP_ID.desktop"
ICON_PATH="$HOME/.local/share/icons/hicolor/scalable/apps/$APP_ID.svg"

# udev rules sort before 73-seat-late.rules so their uaccess tags apply.
UDEV_RULE_UINPUT="$INSTALL_UDEV_DIR_PATH/rules.d/70-$SLUG-uinput.rules"
UDEV_RULE_I2C="$INSTALL_UDEV_DIR_PATH/rules.d/70-$SLUG-i2c-dev.rules"
UDEV_RULE_HIDRAW="$INSTALL_UDEV_DIR_PATH/rules.d/70-$SLUG-hidraw.rules"
UDEV_RULE_SCREEN="$INSTALL_UDEV_DIR_PATH/rules.d/70-$SLUG-screen.rules"

# The program runs with the distribution's Python and the libraries from
# its packages (install_packages.sh): no Python environment of its own, and
# never a Python found in PATH (conda, pyenv).
PYTHON="/usr/bin/python3"
