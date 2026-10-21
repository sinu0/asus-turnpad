#!/usr/bin/env bash

source non_sudo_check.sh

source install_common.sh "$@"

source install_paths.sh

source install_packages.sh

source install_migrate.sh

source install_security_check.sh

# A distribution package (packaging/arch, deb, rpm) installs to /usr/share;
# two installs would start two drivers.
if [ -d /usr/share/asus-turnpad ]; then
    echo "Turnpad is installed as a package (/usr/share/asus-turnpad)."
    echo "Update it with your package manager, or remove it first:"
    echo "  Arch: sudo pacman -R asus-turnpad-git"
    echo "  Debian, Ubuntu: sudo apt remove asus-turnpad"
    echo "  Fedora: sudo dnf remove asus-turnpad"
    exit 1
fi

echo

START_TIME=${EPOCHREALTIME::-7}

migrate_legacy_logs

source install_logs.sh

echo

# log output from every installing attempt aswell
LOGS_INSTALL_LOG_FILE_NAME=install-"$(date +"%d-%m-%Y-%H-%M-%S")".log
LOGS_INSTALL_LOG_FILE_PATH="$LOGS_DIR_PATH/$LOGS_INSTALL_LOG_FILE_NAME"

# The installer's exit status, not the one of tee below.
set -o pipefail


{
    # Distribution packages only (install_packages.sh): no Python
    # environment, no pip, no compiler.
    detect_package_manager

    REQUIRED_PACKAGES=$(packages required)
    OPTIONAL_PACKAGES=$(packages optional)
    if [ "$XDG_SESSION_TYPE" == "x11" ]; then
        REQUIRED_PACKAGES="$REQUIRED_PACKAGES $(packages x11)"
        OPTIONAL_PACKAGES="$OPTIONAL_PACKAGES $(packages x11libs)"
    fi
    if [ "$XDG_SESSION_TYPE" == "wayland" ]; then
        OPTIONAL_PACKAGES="$OPTIONAL_PACKAGES $(packages wayland)"
    fi
    # pyusb for the screen of a SpacePilot Pro (install_screen.sh)
    OPTIONAL_PACKAGES="$OPTIONAL_PACKAGES $(packages screen)"
    if [[ "$DESKTOP_SESSION" == plasma* ]]; then
        REQUIRED_PACKAGES="$REQUIRED_PACKAGES $(packages plasma)"
    fi

    PACKAGES_FAILED=0
    MISSING_PACKAGES=""
    if [ -z "$PACKAGE_MANAGER" ]; then
        echo "WARNING: Not detected package manager. Driver may not work properly because required packages have not been installed. Please create an issue (https://github.com/sinu0/asus-turnpad/issues)."
    elif [ "$PACKAGE_MANAGER" == "rpm-ostree" ]; then
        # Layered packages only work after a reboot, so everything goes in
        # one layer, the journal library of install_service.sh included.
        install_packages $REQUIRED_PACKAGES $OPTIONAL_PACKAGES $(packages systemd)
        if [ -n "$MISSING_PACKAGES" ]; then
            sudo rpm-ostree install --allow-inactive $MISSING_PACKAGES || PACKAGES_FAILED=1
        fi
    else
        install_packages $REQUIRED_PACKAGES || PACKAGES_FAILED=1
        install_optional $OPTIONAL_PACKAGES
    fi

    if [[ $PACKAGES_FAILED != 0 ]]; then
        echo "Error: Something went wrong during installing packages"

        if [[ "$PACKAGE_MANAGER" == "rpm-ostree" ]]; then
            echo
            echo "Common issue:"
            echo "  - Not enough disk space (run: sudo rpm-ostree cleanup -b && sudo ostree admin cleanup)"
            echo
        fi

        if ! ask "Do you want to continue anyway?" n; then
            exit 1
        fi
    elif [[ "$PACKAGE_MANAGER" == "rpm-ostree" && -n "$MISSING_PACKAGES" ]]; then
        echo
        echo "You must reboot before continuing the installation. After reboot run this script again."
        echo
        if ask "Do you want to reboot now?" n; then
            sudo /sbin/reboot
        fi
        exit 0
    fi

    # https://github.com/asus-linux-drivers/asus-numberpad-driver/pull/255
    if [[ "$DESKTOP_SESSION" == plasma* ]] && \
   ! command -v qdbus >/dev/null 2>&1 && \
   ! command -v qdbus6 >/dev/null 2>&1; then
        echo
        echo "WARNING: You are using Plasma desktop environment and qdbus is not available after installation. Manual intervention is required for the driver to function properly." >&2
    fi

    echo

    source install_user_groups.sh

    echo

    if [ "$INSTALL_IN_CONTAINER" = "1" ]; then
        echo "Container install: skipping the touchpad check"
    else
        source install_device_check.sh
    fi

    echo

    # do not install __pycache__
    if [[ -d layouts/__pycache__ ]]; then
        sudo rm -rf layouts/__pycache__
    fi

    # Settings and driver state of an install from before the rename.
    migrate_legacy_config
    # The program now runs with the system Python.
    remove_legacy_venv

    sudo mkdir -p "$INSTALL_DIR_PATH/layouts" "$INSTALL_DIR_PATH/config" "$INSTALL_DIR_PATH/config_app" "$INSTALL_DIR_PATH/desktop" "$INSTALL_DIR_PATH/overlay" "$INSTALL_DIR_PATH/screen" "$INSTALL_DIR_PATH/integrations/blender/turnpad" "$INSTALL_DIR_PATH/integrations/applink" "$INSTALL_DIR_PATH/integrations/solidcraft" "$INSTALL_DIR_PATH/gnome/extension"
    sudo install dialpad.py "$INSTALL_DIR_PATH"
    sudo install dialpad_dbus.py "$INSTALL_DIR_PATH"
    sudo install dial_gesture.py "$INSTALL_DIR_PATH"
    sudo install -m 644 app_link.py app_suggest.py backlight.py dir_watch.py launch.py layout_detect.py mouse_dial.py spacemouse_dial.py spacemouse_devices.py spacenav_client.py xkb.py laptop_touchpad_dialpad_layouts.csv "$INSTALL_DIR_PATH"
    sudo install -t "$INSTALL_DIR_PATH/layouts" layouts/*.py
    sudo install -m 644 -t "$INSTALL_DIR_PATH/config" config/*.py
    sudo install -m 644 -t "$INSTALL_DIR_PATH/desktop" desktop/*.py
    sudo install -m 644 -t "$INSTALL_DIR_PATH/overlay" overlay/*.py
    sudo install -m 644 -t "$INSTALL_DIR_PATH/screen" screen/*.py
    # The Blender add-on (the settings app builds its zip from here).
    sudo install -m 644 -t "$INSTALL_DIR_PATH/integrations/blender/turnpad" integrations/blender/turnpad/*.py integrations/blender/turnpad/*.toml
    # Bridges the driver runs for applications with a control channel (SolidCraft).
    sudo install -m 644 -t "$INSTALL_DIR_PATH/integrations" integrations/*.py
    sudo install -m 644 -t "$INSTALL_DIR_PATH/integrations/applink" integrations/applink/*.py
    sudo install -m 644 -t "$INSTALL_DIR_PATH/integrations/solidcraft" integrations/solidcraft/*.py
    sudo install -m 755 config_app/main.py "$INSTALL_DIR_PATH/config_app/main.py"
    sudo install -m 644 config_app/__init__.py config_app/blender_addon.py config_app/commandline.py config_app/context_dialogs.py config_app/contexts.py config_app/device_dialogs.py config_app/hardware.py config_app/key_capture.py config_app/outline_editor.py config_app/plugins.py config_app/plugins_ui.py config_app/recorder.py config_app/recorder_dialog.py config_app/screen_page.py config_app/spacemouse.py config_app/spacemouse_extra.py config_app/spacemouse_ui.py config_app/icon.svg config_app/style.css "config_app/$APP_ID.desktop" "$INSTALL_DIR_PATH/config_app/"
    sudo install -m 644 gnome/extension/extension.js gnome/extension/dial.js gnome/extension/layers.js gnome/extension/skins.json gnome/extension/metadata.json gnome/extension/stylesheet.css "$INSTALL_DIR_PATH/gnome/extension/"

    mkdir -p "$(dirname "$LAUNCHER_PATH")" "$(dirname "$ICON_PATH")"
    sed "s|@INSTALL_DIR@|$INSTALL_DIR_PATH|g" "config_app/$APP_ID.desktop" > "$LAUNCHER_PATH"
    install -m 644 config_app/icon.svg "$ICON_PATH"

    if [[ -f "$CONFIG_FILE_PATH" ]]; then
        if ask "In system remains config file from previous installation. Do you want replace that config with default config?" n; then
            # default will be autocreated, that is why is removed
            rm -f "$CONFIG_FILE_PATH"
            if [[ $? != 0 ]]; then
                echo "$CONFIG_FILE_PATH cannot be removed correctly..."
                exit 1
            fi
        fi
    else
        echo "Default config will be autocreated during the first run and available for futher modifications here:"
        echo "$CONFIG_FILE_PATH"
    fi

    echo

    if ! "$PYTHON" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"; then
        echo "Turnpad needs Python 3.10 or newer as $PYTHON (found: $("$PYTHON" --version 2>&1))."
        exit 1
    fi

    # The libraries come from the distribution packages installed above.
    if ! "$PYTHON" -c "import libevdev, gi, cairo"; then
        echo
        echo "Error: $PYTHON cannot import the libraries from the distribution packages (see above)."
        echo "Please create an issue with this log: https://github.com/sinu0/asus-turnpad/issues"
        exit 1
    fi

    # The program directory is read-only for the user, so compile it once.
    sudo "$PYTHON" -m compileall -q "$INSTALL_DIR_PATH"

    echo

    if [ -z "$LAYOUT_NAME" ]; then

      source install_layout_auto_suggestion.sh

      echo

      if [ -z "$LAYOUT_NAME" ] && [ "$INTERACTIVE" != "1" ]; then
        LAYOUT_NAME="proartp16"
        echo "No known layout for this laptop; using $LAYOUT_NAME."
        echo "Run Calibration -> Guided calibration in the Turnpad app after installing."
      fi

      if [ -z "$LAYOUT_NAME" ]; then

        source install_layout_select.sh

        echo
      fi
    fi

    source install_coactivator_select.sh

    echo

    source install_user_interface.sh

    echo

    # The old services, extension and files go just before the new ones start.
    remove_legacy_install

    echo

    source install_service.sh

    echo

    source install_overlay.sh

    echo

    source install_screen.sh

    echo

    # The settings app needs Libadwaita 1.5 (GNOME 46, Ubuntu 24.04, Debian 13).
    if ! python3 -c 'import gi, sys; gi.require_version("Adw", "1"); from gi.repository import Adw; sys.exit(0 if (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 5) else 1)' 2>/dev/null; then
        echo "WARNING: the Turnpad settings app needs Libadwaita 1.5 or newer (GNOME 46 or newer); it will not start on this system."
        echo "The driver works; its settings are then kept in $CONFIG_FILE_DIR_PATH/config.json."
        echo
    fi

    echo "Installation finished successfully"
    echo
    echo "Next steps:"
    echo "  1. Log out and back in (new user groups and the GNOME extension load at login)."
    echo "  2. Hold the illuminated corner icon of the touchpad to switch the DialPad on."
    echo "  3. Open \"Turnpad\" from the applications menu to set up functions;"
    echo "     on a laptop that is not listed, run Calibration -> Guided calibration."

    echo

    if [ "$INTERACTIVE" = "1" ] && ask "Reboot now instead of logging out?" n; then
        sudo /sbin/reboot
    fi

    echo

    exit 0
} 2>&1 | sudo tee "$LOGS_INSTALL_LOG_FILE_PATH"
