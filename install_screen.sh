#!/usr/bin/env bash

# The screen of a 3Dconnexion SpacePilot Pro (screen/service.py, see
# ARCHITECTURE.md). Sourced by install.sh after install_overlay.sh, whose
# install_unit places the user service $SCREEN_SERVICE.
#
# pyusb (group "screen" of install_packages.sh) is optional and was installed
# with the other optional packages: without it, or without the device, the
# service exits at once and the driver works as before. The unit has no
# [Install] section: the udev rule written by install_user_groups.sh starts
# it when the device is plugged in.

source install_paths.sh

source install_packages.sh

echo "Screen of a SpaceMouse (SpacePilot Pro)"
echo

detect_package_manager

if [ "$SERVICE" != "1" ]; then
    echo "Without the user services, start the screen yourself when the SpacePilot Pro is plugged in:"
    echo "  $PYTHON $INSTALL_DIR_PATH/screen/service.py"
else
    mkdir -p "$SERVICE_INSTALL_DIR_PATH"
    install_unit "$SCREEN_SERVICE" "$SCREEN_SERVICE" || { echo "Something went wrong when placing $SCREEN_SERVICE"; exit 1; }
    echo "$SCREEN_SERVICE placed"
    if ! "$PYTHON" -c 'import usb' >/dev/null 2>&1; then
        echo "pyusb is not available from the packages of this distribution ($(packages screen)); the SpacePilot Pro's screen stays off (the driver works without it)."
        if [ "$PACKAGE_MANAGER" == "rpm-ostree" ]; then
            echo "Layer it with: sudo rpm-ostree install $(packages screen), reboot and run install.sh again."
        fi
    fi
    if [ "$INSTALL_IN_CONTAINER" = "1" ]; then
        echo "Container install: $SCREEN_SERVICE placed, not started"
    else
        systemctl --user daemon-reload || { echo "Something went wrong when reloading the systemd user manager"; exit 1; }
        # The udev rule starts the service when the device is plugged in; start it
        # now for a device that is already there.
        for device in /sys/bus/usb/devices/*; do
            if [ "$(cat "$device/idVendor" 2>/dev/null)" == "046d" ] && [ "$(cat "$device/idProduct" 2>/dev/null)" == "c629" ]; then
                systemctl --user start "$SCREEN_SERVICE" && echo "$SCREEN_SERVICE started"
                break
            fi
        done
        echo "It starts by itself when a SpacePilot Pro is plugged in."
    fi
fi
