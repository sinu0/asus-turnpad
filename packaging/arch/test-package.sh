#!/usr/bin/env bash

# Builds the Arch package from a local clone, installs it, checks it and
# removes it again. Runs as root in a clean archlinux container (CI does
# this on every push):
#
#   docker run --rm -v "$PWD:/src:ro" archlinux:latest bash /src/packaging/arch/test-package.sh /src
#
# The package is built from the commit checked out in the clone (HEAD), not
# from GitHub, so uncommitted changes are not part of it.

set -euo pipefail

SRC=$(realpath "${1:?usage: test-package.sh <clone of asus-turnpad>}")
APP=/usr/share/asus-turnpad
UUID=asus-turnpad@sinu0.github.io

pacman -Syu --noconfirm --needed base-devel git >/dev/null

# makepkg refuses to run as root.
id builder >/dev/null 2>&1 || useradd -m builder
echo "builder ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/builder
BUILD=$(mktemp -d)
cp "$SRC"/packaging/arch/{PKGBUILD,asus-turnpad.install} "$BUILD/"
COMMIT=$(git -c safe.directory='*' -C "$SRC" rev-parse HEAD)
sed -i "s|^source=.*|source=(\"\$_pkgname::git+file://$SRC#commit=$COMMIT\")|" "$BUILD/PKGBUILD"
chown -R builder "$BUILD"
sudo -u builder git config --global --add safe.directory '*'

echo "== build (makepkg -s installs the dependencies)"
(cd "$BUILD" && sudo -u builder makepkg -s --noconfirm)
PACKAGE=$(ls "$BUILD"/asus-turnpad-git-*.pkg.tar.zst)

echo "== install"
pacman -U --noconfirm "$PACKAGE"

echo "== files"
for path in \
    "$APP/dialpad.py" "$APP/layout_detect.py" "$APP/laptop_touchpad_dialpad_layouts.csv" \
    "$APP/config_app/main.py" "$APP/layouts/proartp16.py" "$APP/gnome/extension/skins.json" \
    "/usr/share/gnome-shell/extensions/$UUID/metadata.json" \
    "/usr/share/gnome-shell/extensions/$UUID/extension.js" \
    /usr/share/applications/io.github.sinu0.AsusTurnpad.desktop \
    /usr/share/icons/hicolor/scalable/apps/io.github.sinu0.AsusTurnpad.svg \
    /usr/lib/systemd/user/asus-turnpad.service \
    /usr/lib/systemd/user/asus-turnpad-overlay.service \
    /usr/lib/systemd/user/asus-turnpad-screen.service "$APP/screen/__init__.py" \
    "$APP/app_link.py" "$APP/integrations/blender/turnpad/blender_manifest.toml" "$APP/integrations/blender/turnpad/__init__.py" \
    /usr/lib/udev/rules.d/70-asus-turnpad.rules \
    /usr/lib/modules-load.d/asus-turnpad.conf \
    /usr/lib/sysusers.d/asus-turnpad.conf \
    /usr/bin/asus-turnpad-setup; do
    test -e "$path" || { echo "missing: $path"; exit 1; }
done
grep -q "Exec=python3 $APP/config_app/main.py" /usr/share/applications/io.github.sinu0.AsusTurnpad.desktop
grep -qx "ExecStart=/usr/bin/python3 $APP/overlay/layer_shell.py" /usr/lib/systemd/user/asus-turnpad-overlay.service
grep -qx "ExecStart=/usr/bin/python3 $APP/screen/service.py" /usr/lib/systemd/user/asus-turnpad-screen.service
# No [Install] section: the udev rule starts the screen service when the SpacePilot Pro is plugged in.
if grep -q "^\[Install\]" /usr/lib/systemd/user/asus-turnpad-screen.service; then echo "screen unit has an [Install] section"; exit 1; fi
grep -q "SYSTEMD_USER_WANTS.*asus-turnpad-screen.service" /usr/lib/udev/rules.d/70-asus-turnpad.rules
getent group i2c >/dev/null && getent group uinput >/dev/null
test -x "$APP/dialpad.py" && test -x /usr/bin/asus-turnpad-setup
if find "$APP" -newer "$PACKAGE" -name '*.pyc' | grep -q .; then
    echo "bytecode written after installing (not compiled in the package)"; exit 1
fi

echo "== the libraries the driver and the settings app import"
(cd /tmp && python3 -I "$SRC/packaging/check-imports.py" "$APP")
python3 -m py_compile "$APP/config_app/main.py"
bash -n /usr/bin/asus-turnpad-setup
# The setup helper refuses root.
if /usr/bin/asus-turnpad-setup >/dev/null 2>&1; then echo "setup ran as root"; exit 1; fi

echo "== remove"
pacman -R --noconfirm asus-turnpad-git
test ! -e "$APP" && test ! -e "/usr/share/gnome-shell/extensions/$UUID" && test ! -e /usr/bin/asus-turnpad-setup
echo "== package test passed"
