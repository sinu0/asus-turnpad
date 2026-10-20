#!/usr/bin/env bash

# Puts Turnpad's files where distribution packages install them, below a
# package root. One list for every package: the PKGBUILD (packaging/arch),
# debian/rules (packaging/deb) and the RPM spec (packaging/rpm) call it from
# the root of the source tree:
#
#   bash packaging/install-files.sh <package root> [--no-bytecode]
#
# --no-bytecode leaves compiling to the package's own tools (Debian compiles
# at installation with py3compile, the RPM spec with %py_byte_compile).
# Names come from install_paths.sh (twins in config/identity.py).

set -euo pipefail

ROOT=${1:?usage: install-files.sh <package root> [--no-bytecode]}
BYTECODE=1
[[ ${2:-} == --no-bytecode ]] && BYTECODE=0

source ./install_paths.sh

PACKAGE_DIR="/usr/share/$SLUG"
APP="$ROOT$PACKAGE_DIR"
EXTENSION_FILES=(gnome/extension/{extension.js,dial.js,layers.js,skins.json,metadata.json,stylesheet.css})

# Driver and settings app (the same files install.sh puts in /opt/asus-turnpad).
install -Dm755 dialpad.py "$APP/dialpad.py"
# Every module next to the driver, except the legacy PySide overlay
# (dialpad_ui.py) that the packages do not ship.
MODULES=()
for module in *.py; do
    [[ $module == dialpad.py || $module == dialpad_ui.py ]] || MODULES+=("$module")
done
install -Dm644 -t "$APP" "${MODULES[@]}" laptop_touchpad_dialpad_layouts.csv
for dir in config config_app desktop layouts overlay screen; do
    install -Dm644 -t "$APP/$dir" "$dir"/*.py
done
chmod 755 "$APP/config_app/main.py"
# The Blender add-on (the settings app builds its zip from here).
install -Dm644 -t "$APP/integrations/blender/turnpad" integrations/blender/turnpad/*.py integrations/blender/turnpad/*.toml
install -Dm644 -t "$APP/config_app" config_app/icon.svg config_app/style.css
# The settings app reads the built-in skins next to itself.
install -Dm644 -t "$APP/gnome/extension" "${EXTENSION_FILES[@]}"
if (( BYTECODE )); then
    "${PYTHON:-python3}" -m compileall -q -d "$PACKAGE_DIR" "$APP"
fi

# On-screen dial for GNOME Shell.
install -Dm644 -t "$ROOT/usr/share/gnome-shell/extensions/$GNOME_EXTENSION_UUID" "${EXTENSION_FILES[@]}"

# Launcher and icon of the settings app.
install -dm755 "$ROOT/usr/share/applications"
sed "s|@INSTALL_DIR@|$PACKAGE_DIR|g" "config_app/$APP_ID.desktop" > "$ROOT/usr/share/applications/$APP_ID.desktop"
chmod 644 "$ROOT/usr/share/applications/$APP_ID.desktop"
install -Dm644 config_app/icon.svg "$ROOT/usr/share/icons/hicolor/scalable/apps/$APP_ID.svg"

# System integration: user services, device access, kernel modules, groups.
install -Dm644 "packaging/$SLUG.service" "$ROOT/usr/lib/systemd/user/$SLUG.service"
# On-screen dial for layer-shell compositors (Wayland desktops but GNOME).
install -Dm644 "packaging/$OVERLAY_SERVICE" "$ROOT/usr/lib/systemd/user/$OVERLAY_SERVICE"
# Screen of a SpacePilot Pro: no [Install] section, the udev rule starts it.
install -Dm644 "packaging/$SCREEN_SERVICE" "$ROOT/usr/lib/systemd/user/$SCREEN_SERVICE"
install -Dm644 "packaging/70-$SLUG.rules" "$ROOT/usr/lib/udev/rules.d/70-$SLUG.rules"
install -Dm644 "packaging/$SLUG.modules-load" "$ROOT/usr/lib/modules-load.d/$SLUG.conf"
install -Dm644 "packaging/$SLUG.sysusers" "$ROOT/usr/lib/sysusers.d/$SLUG.conf"
install -Dm755 "packaging/$SLUG-setup" "$ROOT/usr/bin/$SLUG-setup"

install -Dm644 -t "$ROOT/usr/share/doc/$SLUG" README.md CHANGELOG.md PORTING.md SKINS.md SPACEMOUSE.md
