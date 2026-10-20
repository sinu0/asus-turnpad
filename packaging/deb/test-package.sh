#!/usr/bin/env bash

# Builds the .deb from a local clone, installs it with apt, checks it and
# removes it again. Runs as root in a clean Debian or Ubuntu container (CI
# does this on every push):
#
#   podman run --rm -v "$PWD:/src:ro" ubuntu:latest bash /src/packaging/deb/test-package.sh /src
#
# The package is built from the commit checked out in the clone (HEAD), not
# from uncommitted changes.

set -euo pipefail

SRC=$(realpath "${1:?usage: test-package.sh <clone of asus-turnpad>}")
APP=/usr/share/asus-turnpad
UUID=asus-turnpad@sinu0.github.io
TESTER=tester

echo "== prepare $(. /etc/os-release && echo "$PRETTY_NAME")"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends ca-certificates git build-essential dpkg-dev debhelper dh-python python3 sudo > /tmp/prepare.log 2>&1 || { tail -40 /tmp/prepare.log; exit 1; }
git config --global --add safe.directory '*'

echo "== build"
OUT=$(mktemp -d)
bash "$SRC/packaging/deb/build.sh" -o "$OUT" > /tmp/build.log 2>&1 || { tail -40 /tmp/build.log; echo "build failed"; exit 1; }
PACKAGE=$(ls "$OUT"/asus-turnpad_*_all.deb)
dpkg-deb --info "$PACKAGE" | sed -n '/Package:/,$p'
if dpkg-deb --contents "$PACKAGE" | grep -q '\.pyc$'; then
    echo "bytecode in the package (py3compile writes it at installation)"; exit 1
fi

echo "== install (apt resolves the dependencies; recommended packages left out)"
apt-get install -y -qq --no-install-recommends "$PACKAGE" > /tmp/install.log 2>&1 || { tail -40 /tmp/install.log; exit 1; }
grep -A4 'Finish the setup' /tmp/install.log

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
    /usr/bin/asus-turnpad-setup \
    /usr/share/doc/asus-turnpad/copyright; do
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
# The user services must not be enabled for every user (asus-turnpad-setup does it per user).
test ! -e /etc/systemd/user/graphical-session.target.wants/asus-turnpad.service
test ! -e /etc/systemd/user/graphical-session.target.wants/asus-turnpad-overlay.service

echo "== the libraries the driver and the settings app import"
find "$APP" -name '*.pyc' | sort > /tmp/pyc-installed
test -s /tmp/pyc-installed || { echo "no bytecode compiled at installation"; exit 1; }
(cd /tmp && python3 -I "$SRC/packaging/check-imports.py" "$APP")
find "$APP" -name '*.pyc' | sort | diff /tmp/pyc-installed - || { echo "bytecode missing from the installation"; exit 1; }
bash -n /usr/bin/asus-turnpad-setup

echo "== asus-turnpad-setup"
# The setup helper refuses root.
if /usr/bin/asus-turnpad-setup >/dev/null 2>&1; then echo "setup ran as root"; exit 1; fi
# As a user: groups (no user service manager in a container, so enabling the service only warns).
id "$TESTER" >/dev/null 2>&1 || useradd -m "$TESTER"
echo "$TESTER ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/"$TESTER"
# Run from a terminal of a GNOME session (gnome-extensions is not installed here).
sudo -u "$TESTER" -H env XDG_CURRENT_DESKTOP=GNOME asus-turnpad-setup
for group in input i2c uinput; do
    id -nG "$TESTER" | tr ' ' '\n' | grep -qx "$group" || { echo "$TESTER not in $group"; exit 1; }
done
# From a sway session: the layer-shell dial needs gtk4-layer-shell (recommended, left out above).
sudo -u "$TESTER" -H env XDG_CURRENT_DESKTOP=sway XDG_SESSION_TYPE=wayland asus-turnpad-setup > /tmp/setup-sway.log 2>&1
grep -q "needs gtk4-layer-shell" /tmp/setup-sway.log || { cat /tmp/setup-sway.log; echo "setup did not ask for gtk4-layer-shell"; exit 1; }
# Ubuntu 24.04 only knows the name (from the Recommends), without a candidate.
# (grep -q in a pipe would end apt-cache early, a failure under pipefail)
if [[ $(apt-cache policy gir1.2-gtk4layershell-1.0) == *"Candidate: "[0-9]* ]]; then
    apt-get install -y -qq gir1.2-gtk4layershell-1.0 >/dev/null
    # Now it enables the dial's service (only warns: no user service manager here).
    sudo -u "$TESTER" -H env XDG_CURRENT_DESKTOP=sway XDG_SESSION_TYPE=wayland asus-turnpad-setup > /tmp/setup-sway.log 2>&1
    grep -qE "asus-turnpad-overlay.service|On-screen dial enabled" /tmp/setup-sway.log || { cat /tmp/setup-sway.log; echo "setup did not enable the dial"; exit 1; }
    apt-get remove -y -qq gir1.2-gtk4layershell-1.0 >/dev/null
else
    echo "gtk4-layer-shell is not packaged here: the dial is left out"
fi

echo "== remove"
apt-get remove -y -qq asus-turnpad >/dev/null
test ! -e "$APP" && test ! -e "/usr/share/gnome-shell/extensions/$UUID" && test ! -e /usr/bin/asus-turnpad-setup
test ! -e /usr/lib/udev/rules.d/70-asus-turnpad.rules
apt-get purge -y -qq asus-turnpad >/dev/null
if dpkg -S asus-turnpad 2>/dev/null; then echo "files of the package left"; exit 1; fi
echo "== package test passed"
