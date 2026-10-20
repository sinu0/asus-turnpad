#!/usr/bin/env bash

# Removes Turnpad and any leftover install under the old names. Your
# settings (~/.config/asus-turnpad) stay unless you agree to remove them
# with --interactive. Group memberships (input, uinput, i2c) stay as well.

source non_sudo_check.sh

source install_common.sh "$@"

source install_paths.sh

source install_migrate.sh

source install_logs.sh

echo

LOGS_UNINSTALL_LOG_FILE_PATH="$LOGS_DIR_PATH/uninstall-$(date +"%d-%m-%Y-%H-%M-%S").log"

{
    for unit in "$SCREEN_SERVICE" "$OVERLAY_SERVICE" "$UI_SERVICE" "$DRIVER_SERVICE"; do
        if systemctl --user cat "$unit" >/dev/null 2>&1; then
            systemctl --user disable --now "$unit" && echo "Stopped and disabled $unit"
        fi
        rm -f "$SERVICE_INSTALL_DIR_PATH/$unit"
    done
    systemctl --user daemon-reload

    if command -v gnome-extensions >/dev/null 2>&1; then
        gnome-extensions disable "$GNOME_EXTENSION_UUID" >/dev/null 2>&1
    fi
    rm -rf "$GNOME_EXTENSION_DIR"
    rm -f "$LAUNCHER_PATH" "$ICON_PATH"

    sudo rm -rf "$INSTALL_DIR_PATH" && echo "Removed $INSTALL_DIR_PATH"

    sudo rm -f "$UDEV_RULE_UINPUT" "$UDEV_RULE_I2C" "$UDEV_RULE_HIDRAW" "$UDEV_RULE_SCREEN" "$MODULES_LOAD_FILE_PATH"

    remove_legacy_install

    if sudo udevadm control --reload-rules && sudo udevadm trigger --sysname-match=uinput && sudo udevadm trigger --subsystem-match=hidraw && sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=046d --attr-match=idProduct=c629; then
        echo "Udev rules reloaded and triggered"
    else
        echo "Something went wrong when reloading or triggering udev rules"
    fi

    echo

    if ask "Remove your settings, profiles and skins in $CONFIG_FILE_DIR_PATH?" n; then
        rm -rf "$CONFIG_FILE_DIR_PATH" && echo "Removed $CONFIG_FILE_DIR_PATH"
    else
        echo "Your settings remain in $CONFIG_FILE_DIR_PATH (remove it by hand if not needed)"
    fi

    echo
    echo "Uninstallation finished successfully"
    echo "Log out and back in to unload the GNOME extension."

    if [ "$INTERACTIVE" = "1" ] && ask "Reboot now?" n; then
        sudo /sbin/reboot
    fi

    exit 0
} 2>&1 | sudo tee "$LOGS_UNINSTALL_LOG_FILE_PATH"
