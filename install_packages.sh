#!/usr/bin/env bash

# Distribution packages Turnpad needs. Everything comes from the package
# manager and runs with the system Python ($PYTHON): no Python environment,
# no pip, no compiler. Sourced by install.sh, install_service.sh,
# install_user_interface.sh, install_layer_shell_overlay.sh and install_screen.sh.
#
#   detect_package_manager      sets PACKAGE_MANAGER (empty when unknown)
#   packages <group>            package names of a group for this manager
#   install_packages <names>    installs all of them; fails if one fails
#   install_optional <names>    installs each one it can; a package missing
#                               from this release only turns its feature off
#
# Groups (PACKAGES_<names>_<group> below):
#   required   driver: python-libevdev, PyGObject (Gio, GLib), libxkbcommon,
#              i2c-tools; settings app: GTK 4, Libadwaita, pycairo, librsvg;
#              installer: curl and xmllint (layout lookup), envsubst (services).
#              GLib's typelibs (GLibUnix for overlay/layer_shell.py) come
#              with PyGObject everywhere.
#   x11       xinput (touchpad on/off follows the X11 device state)
#   plasma     qdbus (focused window on KDE Plasma)
#   optional   pyatspi (focused window fallback), python-periphery (I2C
#              fallback when i2ctransfer fails; Debian and Ubuntu only),
#              the Rsvg typelib on Debian and Ubuntu (SVG icons in the
#              settings app, overlay/gtk_icons.py; elsewhere librsvg has it)
#   wayland    pywayland (keyboard layout from the Wayland compositor)
#   x11libs    python-xlib and xcffib (keyboard layout and keys on X11)
#   systemd    python-systemd (logs to the journal), see install_service.sh
#   ui         PySide6 for the legacy on-screen dial window (dialpad_ui.py, X11)
#   layershell gtk4-layer-shell and its typelib: the on-screen dial on Wayland
#              desktops other than GNOME (overlay/layer_shell.py, see
#              install_layer_shell_overlay.sh); Ubuntu 24.04 has none
#   screen     pyusb: the screen of a 3Dconnexion SpacePilot Pro (screen/,
#              see install_screen.sh); install.sh installs it with the
#              optional packages
#
# Names for apt, dnf and pacman are tested in CI (tests/install_in_container.sh,
# packaging/arch); zypper names were checked on openSUSE Tumbleweed only;
# xbps, portage and eopkg names are not tested.

# Major Plasma version for the qdbus package name.
# https://github.com/asus-linux-drivers/asus-numberpad-driver/pull/255
if command -v kinfo >/dev/null 2>&1; then
    PLASMA_VER=$(kinfo 2>/dev/null | awk -F': ' '/KDE Plasma Version/ {print $2}' | cut -d. -f1)
fi
PLASMA_VER=${PLASMA_VER:-6}  # default to plasma 6 for modern systems

# Debian, Ubuntu
PACKAGES_apt_required="python3 python3-libevdev python3-gi python3-gi-cairo python3-cairo libxkbcommon0 i2c-tools
    gir1.2-gtk-4.0 gir1.2-adw-1 curl libxml2-utils gettext-base"
PACKAGES_apt_x11="xinput"
PACKAGES_apt_plasma="qdbus-qt$PLASMA_VER"
PACKAGES_apt_optional="python3-pyatspi python3-periphery gir1.2-rsvg-2.0"
PACKAGES_apt_wayland="python3-pywayland"
PACKAGES_apt_x11libs="python3-xlib python3-xcffib"
PACKAGES_apt_systemd="python3-systemd"
PACKAGES_apt_ui="python3-pyside6.qtcore python3-pyside6.qtgui python3-pyside6.qtwidgets python3-pyside6.qtsvg"
PACKAGES_apt_layershell="gir1.2-gtk4layershell-1.0 libgtk4-layer-shell0"
PACKAGES_apt_screen="python3-usb"

# Fedora, also immutable Fedora (rpm-ostree) and yum; gobject-introspection
# has the cairo and DBus typelibs that GTK, Rsvg and pyatspi load.
PACKAGES_dnf_required="python3 python3-libevdev python3-gobject python3-cairo gobject-introspection libxkbcommon i2c-tools
    gtk4 libadwaita librsvg2 curl libxml2 gettext-envsubst"
PACKAGES_dnf_x11="xinput"
PACKAGES_dnf_plasma="qt$PLASMA_VER-qttools"
PACKAGES_dnf_optional="python3-pyatspi"
PACKAGES_dnf_wayland="python3-pywayland"
PACKAGES_dnf_x11libs="python3-xlib python3-xcffib"
PACKAGES_dnf_systemd="python3-systemd"
PACKAGES_dnf_ui="python3-pyside6"
PACKAGES_dnf_layershell="gtk4-layer-shell"
PACKAGES_dnf_screen="python3-pyusb"

# Arch Linux (the Arch package in packaging/arch is the better way there)
PACKAGES_pacman_required="python python-libevdev python-gobject python-cairo libxkbcommon i2c-tools
    gtk4 libadwaita librsvg curl libxml2 gettext"
PACKAGES_pacman_x11="xorg-xinput"
PACKAGES_pacman_plasma="qt$PLASMA_VER-tools"
PACKAGES_pacman_optional="python-atspi"
PACKAGES_pacman_wayland="python-pywayland"
PACKAGES_pacman_x11libs="python-xlib python-xcffib"
PACKAGES_pacman_systemd="python-systemd"
PACKAGES_pacman_ui="pyside6"
PACKAGES_pacman_layershell="gtk4-layer-shell"
PACKAGES_pacman_screen="python-pyusb"

# openSUSE
PACKAGES_zypper_required="python3 python3-libevdev python3-gobject python3-gobject-cairo python3-cairo libxkbcommon0 i2c-tools
    typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1 typelib-1_0-Rsvg-2_0 curl libxml2-tools envsubst"
PACKAGES_zypper_x11="xinput"
PACKAGES_zypper_plasma="qt$PLASMA_VER-tools-qdbus"
PACKAGES_zypper_optional="python3-atspi"
PACKAGES_zypper_wayland="python3-pywayland"
PACKAGES_zypper_x11libs="python3-python-xlib python3-xcffib"
PACKAGES_zypper_systemd="python3-systemd"
PACKAGES_zypper_ui="python3-pyside6"
PACKAGES_zypper_layershell="typelib-1_0-Gtk4LayerShell-1_0 libgtk4-layer-shell0"
PACKAGES_zypper_screen="python3-pyusb"

# Void Linux (not tested)
PACKAGES_xbps_required="python3 python3-libevdev python3-gobject python3-cairo libxkbcommon i2c-tools
    gtk4 libadwaita librsvg curl libxml2 gettext"
PACKAGES_xbps_x11="xinput"
PACKAGES_xbps_plasma="qt$PLASMA_VER-tools"
PACKAGES_xbps_optional="python3-atspi"
PACKAGES_xbps_wayland="python3-pywayland"
PACKAGES_xbps_x11libs="python3-xlib python3-xcffib"
PACKAGES_xbps_systemd=""
PACKAGES_xbps_ui="python3-pyside6"
PACKAGES_xbps_layershell="gtk4-layer-shell"
PACKAGES_xbps_screen="python3-pyusb"

# Gentoo (not tested)
PACKAGES_portage_required="dev-lang/python dev-python/libevdev dev-python/pygobject dev-python/pycairo x11-libs/libxkbcommon sys-apps/i2c-tools
    gui-libs/gtk gui-libs/libadwaita gnome-base/librsvg net-misc/curl dev-libs/libxml2 sys-devel/gettext"
PACKAGES_portage_x11="x11-apps/xinput"
PACKAGES_portage_plasma="dev-qt/qdbus"
PACKAGES_portage_optional="dev-python/pyatspi"
PACKAGES_portage_wayland="dev-python/pywayland"
PACKAGES_portage_x11libs="dev-python/python-xlib dev-python/xcffib"
PACKAGES_portage_systemd="dev-python/python-systemd"
PACKAGES_portage_ui="dev-python/pyside"
PACKAGES_portage_layershell="gui-libs/gtk4-layer-shell"
PACKAGES_portage_screen="dev-python/pyusb"

# Solus (not tested)
PACKAGES_eopkg_required="python3 python-libevdev python-gobject python-cairo libxkbcommon i2c-tools
    libgtk-4 libadwaita librsvg curl libxml2 gettext"
PACKAGES_eopkg_x11="xinput"
PACKAGES_eopkg_plasma="qt$PLASMA_VER-tools"
PACKAGES_eopkg_optional="python-atspi"
PACKAGES_eopkg_wayland="python-pywayland"
PACKAGES_eopkg_x11libs="python-xlib python-xcffib"
PACKAGES_eopkg_systemd="python-systemd"
PACKAGES_eopkg_ui="pyside6"
PACKAGES_eopkg_layershell="gtk4-layer-shell"
PACKAGES_eopkg_screen="python-pyusb"

detect_package_manager() {
    PACKAGE_MANAGER=""
    # check rpm-ostree FIRST before dnf/yum (for BazziteOS/Fedora Atomic/Silverblue)
    # because dnf may exist as a wrapper that blocks usage on immutable systems
    # https://github.com/asus-linux-drivers/asus-numberpad-driver/pull/280
    if command -v rpm-ostree >/dev/null 2>&1 && grep -qi "ostree" /etc/os-release 2>/dev/null; then
        PACKAGE_MANAGER="rpm-ostree"
    elif command -v apt-get >/dev/null 2>&1; then
        PACKAGE_MANAGER="apt"
    elif command -v pacman >/dev/null 2>&1; then
        PACKAGE_MANAGER="pacman"
    elif command -v dnf >/dev/null 2>&1; then
        PACKAGE_MANAGER="dnf"
    elif command -v yum >/dev/null 2>&1; then
        PACKAGE_MANAGER="yum"
    elif command -v zypper >/dev/null 2>&1; then
        PACKAGE_MANAGER="zypper"
    elif command -v xbps-install >/dev/null 2>&1; then
        PACKAGE_MANAGER="xbps"
    elif command -v emerge >/dev/null 2>&1; then
        PACKAGE_MANAGER="portage"
    elif command -v eopkg >/dev/null 2>&1; then
        PACKAGE_MANAGER="eopkg"
    fi
}

# packages <group>: the names of a group for PACKAGE_MANAGER (word list).
packages() {
    local names=$PACKAGE_MANAGER
    case "$names" in
        rpm-ostree|yum) names=dnf ;;
    esac
    [ -n "$names" ] || return 0
    local var="PACKAGES_${names}_$1"
    echo ${!var}
}

# install_packages <names...>: status 0 when all are installed. rpm-ostree
# packages only work after a reboot, so the missing ones are collected in
# MISSING_PACKAGES instead; install.sh layers them at once.
install_packages() {
    [ $# -gt 0 ] || return 0
    case "$PACKAGE_MANAGER" in
        apt) sudo apt-get -y install "$@" ;;
        pacman) sudo pacman --noconfirm --needed -S "$@" ;;
        dnf) sudo dnf -y install "$@" ;;
        yum) sudo yum -y install "$@" ;;
        zypper) sudo zypper --non-interactive install "$@" ;;
        xbps) sudo xbps-install -Sy "$@" ;;
        portage) sudo emerge --noreplace "$@" ;;
        eopkg) sudo eopkg install -y "$@" ;;
        rpm-ostree)
            local pkg
            for pkg in "$@"; do
                rpm -q "$pkg" >/dev/null 2>&1 || MISSING_PACKAGES="$MISSING_PACKAGES $pkg"
            done
            ;;
        *) return 1 ;;
    esac
}

# install_optional <names...>: tries all at once, then one by one, and names
# the packages this distribution does not have. Never fails.
install_optional() {
    [ $# -gt 0 ] || return 0
    install_packages "$@" && return 0
    local pkg
    for pkg in "$@"; do
        install_packages "$pkg" >/dev/null 2>&1 || echo "Optional package $pkg is not available here; its feature stays off."
    done
    return 0
}
