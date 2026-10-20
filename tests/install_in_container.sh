#!/usr/bin/env bash

# Installs Turnpad with install.sh in a clean distribution container, checks
# the result and uninstalls it again. CI runs it for Fedora and Ubuntu:
#
#   docker run --rm -v "$PWD:/src:ro" fedora:latest bash /src/tests/install_in_container.sh /src
#
# A container has no DialPad, kernel modules or user services, so install.sh
# runs with INSTALL_IN_CONTAINER=1 (see install_common.sh); everything that
# differs between distributions is tested: package names, that only
# distribution packages are used (no compiler installed, no Python
# environment; an old one is removed), the installed files, the user service
# file running the system Python, and the libraries the driver and the
# settings app import with /usr/bin/python3. It installs for a GNOME session
# first, then for sway: there the layer-shell dial's service replaces the
# legacy window (where the distribution packages gtk4-layer-shell).

set -euo pipefail

SRC=$(realpath "${1:?usage: install_in_container.sh <clone of asus-turnpad>}")
TESTER=tester
APP=/opt/asus-turnpad
PY=/usr/bin/python3
COMPILERS="gcc cc c++ clang"

echo "== prepare $(. /etc/os-release && echo "$PRETTY_NAME")"
if command -v apt-get >/dev/null; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq sudo python3 >/dev/null
elif command -v dnf >/dev/null; then
    dnf -y -q install sudo python3 >/dev/null
elif command -v pacman >/dev/null; then
    pacman -Syu --noconfirm --needed sudo python >/dev/null
fi
id "$TESTER" >/dev/null 2>&1 || useradd -m "$TESTER"
echo "$TESTER ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/"$TESTER"
HOME_DIR=$(getent passwd "$TESTER" | cut -d: -f6)
rm -rf "$HOME_DIR/turnpad"
cp -a "$SRC" "$HOME_DIR/turnpad"
rm -rf "$HOME_DIR/turnpad/.git"
chown -R "$TESTER:" "$HOME_DIR/turnpad"

DESKTOP=GNOME
as_tester() {
    # A Wayland session of $DESKTOP as install.sh sees it; the layout is
    # given so the installer does not look the laptop up online.
    sudo -u "$TESTER" -H env INSTALL_IN_CONTAINER=1 LAYOUT_NAME=proartp16 \
        XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP="$DESKTOP" XDG_RUNTIME_DIR=/tmp \
        bash -c "cd ~/turnpad && $1"
}
UNITS="$HOME_DIR/.config/systemd/user"

echo "== an install from before distribution packages"
# Its Python environment and a service that ran it; install.sh removes both
# (the legacy window's service is not placed again on GNOME).
mkdir -p "$APP/.env/bin" "$HOME_DIR/.config/systemd/user"
touch "$APP/.env/bin/python3"
printf '[Service]\nExecStart=%s/.env/bin/python3 %s/dialpad_ui.py\n' "$APP" "$APP" \
    > "$HOME_DIR/.config/systemd/user/asus-turnpad-ui.service"
chown -R "$TESTER:" "$HOME_DIR/.config"
COMPILERS_BEFORE=$(for c in $COMPILERS; do command -v "$c" || true; done)

echo "== install.sh"
as_tester "bash install.sh" > /tmp/install.log 2>&1 || { tail -40 /tmp/install.log; echo "install.sh failed"; exit 1; }
grep -q "Installation finished successfully" /tmp/install.log || { tail -40 /tmp/install.log; exit 1; }
grep "Optional package" /tmp/install.log || true

echo "== distribution packages only"
COMPILERS_AFTER=$(for c in $COMPILERS; do command -v "$c" || true; done)
test "$COMPILERS_BEFORE" = "$COMPILERS_AFTER" || { echo "install.sh installed a compiler: $COMPILERS_AFTER"; exit 1; }
for path in "$APP/.env" "$HOME_DIR/.config/systemd/user/asus-turnpad-ui.service"; do
    test ! -e "$path" || { echo "left from the old install: $path"; exit 1; }
done
! grep -E "pip install|virtualenv" /tmp/install.log || { echo "install.sh ran pip"; exit 1; }

echo "== files"
for path in "$APP/dialpad.py" "$APP/layout_detect.py" "$APP/config_app/main.py" "$APP/gnome/extension/skins.json" \
    "$HOME_DIR/.config/systemd/user/asus-turnpad.service" "$HOME_DIR/.config/systemd/user/asus-turnpad-screen.service" \
    "$APP/screen/__init__.py" "$APP/spacemouse_devices.py" "$APP/app_link.py" \
    "$APP/integrations/blender/turnpad/blender_manifest.toml" \
    "$HOME_DIR/.local/share/applications/io.github.sinu0.AsusTurnpad.desktop" \
    /etc/udev/rules.d/70-asus-turnpad-i2c-dev.rules /etc/udev/rules.d/70-asus-turnpad-screen.rules /etc/modules-load.d/asus-turnpad.conf; do
    test -e "$path" || { echo "missing: $path"; exit 1; }
done
grep -q "ExecStart=$PY $APP/dialpad.py proartp16 $HOME_DIR/.config/asus-turnpad/" \
    "$HOME_DIR/.config/systemd/user/asus-turnpad.service" \
    || { grep ExecStart "$HOME_DIR/.config/systemd/user/asus-turnpad.service"; echo "the service does not run $PY"; exit 1; }
id -nG "$TESTER" | tr ' ' '\n' | grep -qx input
# The SpacePilot Pro's screen: a unit with no [Install] section, started by the udev rule.
grep -qx "ExecStart=$PY $APP/screen/service.py" "$HOME_DIR/.config/systemd/user/asus-turnpad-screen.service" \
    || { cat "$HOME_DIR/.config/systemd/user/asus-turnpad-screen.service"; echo "the screen's service is wrong"; exit 1; }
! grep -q "^\[Install\]" "$HOME_DIR/.config/systemd/user/asus-turnpad-screen.service" || { echo "the screen's service has an [Install] section"; exit 1; }
grep -q 'ATTR{idProduct}=="c629".*SYSTEMD_USER_WANTS}+="asus-turnpad-screen.service"' /etc/udev/rules.d/70-asus-turnpad-screen.rules \
    || { cat /etc/udev/rules.d/70-asus-turnpad-screen.rules; echo "the screen's udev rule is wrong"; exit 1; }
# GNOME has the Shell extension; no layer-shell dial.
test ! -e "$UNITS/asus-turnpad-overlay.service" || { echo "layer-shell dial installed on GNOME"; exit 1; }

echo "== the driver's libraries with $PY"
"$PY" - <<'EOF'
import ast, importlib, pathlib, sys
app = pathlib.Path("/opt/asus-turnpad")
sys.path.insert(0, str(app))
# What dialpad.py imports outside try blocks is required.
tree = ast.parse((app / "dialpad.py").read_text())
required = set()
for node in tree.body:
    if isinstance(node, ast.Import):
        required |= {alias.name for alias in node.names}
    elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
        required.add(node.module)
for name in sorted(required):
    importlib.import_module(name)
# The project's own modules (dialpad.py and the legacy window run on import).
for path in sorted(app.glob("*.py")):
    if path.stem not in ("dialpad", "dialpad_ui"):
        importlib.import_module(path.stem)
import config.schema, config.legacy_adapter, config.runtime, desktop.active_window
from gi.repository import Gio, GLib
print("driver imports ok:", ", ".join(sorted(required)))
optional = []
for name in ("pywayland.client", "systemd.journal", "periphery", "pyatspi"):
    try:
        importlib.import_module(name)
        optional.append(name)
    except Exception:
        optional.append(name + " (not importable)")
print("optional:", ", ".join(optional))
EOF

echo "== the settings app's libraries with $PY"
"$PY" - <<'EOF'
import sys
sys.path.insert(0, "/opt/asus-turnpad")
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Rsvg", "2.0")
from gi.repository import Adw, Gtk, Rsvg
import cairo
version = (Adw.get_major_version(), Adw.get_minor_version())
print("GTK %d.%d, Libadwaita %d.%d" % (Gtk.get_major_version(), Gtk.get_minor_version(), *version))
assert version >= (1, 5), "the settings app needs Libadwaita 1.5"
import config_app.main, overlay.painter, config.skins
assert config.skins.load_skins()
print("settings app imports ok")
EOF

echo "== uninstall.sh"
as_tester "bash uninstall.sh" > /tmp/uninstall.log 2>&1 || { tail -30 /tmp/uninstall.log; echo "uninstall.sh failed"; exit 1; }
for path in "$APP" "$HOME_DIR/.config/systemd/user/asus-turnpad.service" "$HOME_DIR/.config/systemd/user/asus-turnpad-screen.service" \
    "$HOME_DIR/.local/share/applications/io.github.sinu0.AsusTurnpad.desktop" \
    /etc/udev/rules.d/70-asus-turnpad-i2c-dev.rules /etc/udev/rules.d/70-asus-turnpad-screen.rules /etc/modules-load.d/asus-turnpad.conf; do
    test ! -e "$path" || { echo "left behind: $path"; exit 1; }
done
test -d "$HOME_DIR/.config/asus-turnpad" || { echo "settings were removed"; exit 1; }

echo "== install.sh on sway: the layer-shell dial instead of the legacy window"
DESKTOP=sway
# The legacy window's Wayland service of an earlier install goes.
mkdir -p "$UNITS"
printf '[Service]\nExecStart=%s %s/dialpad_ui.py\nEnvironment="WAYLAND_DISPLAY=wayland-0"\n' "$PY" "$APP" \
    > "$UNITS/asus-turnpad-ui.service"
chown -R "$TESTER:" "$HOME_DIR/.config"
as_tester "bash install.sh" > /tmp/install-sway.log 2>&1 || { tail -40 /tmp/install-sway.log; echo "install.sh failed"; exit 1; }
grep -q "Installation finished successfully" /tmp/install-sway.log || { tail -40 /tmp/install-sway.log; exit 1; }
test ! -e "$UNITS/asus-turnpad-ui.service" || { echo "the legacy window's service is installed on Wayland"; exit 1; }
if "$PY" -c 'import gi; gi.require_version("Gtk4LayerShell", "1.0")' 2>/dev/null; then
    grep -qx "ExecStart=$PY $APP/overlay/layer_shell.py" "$UNITS/asus-turnpad-overlay.service" \
        || { cat "$UNITS/asus-turnpad-overlay.service" 2>/dev/null; echo "the dial's service is missing or wrong"; exit 1; }
    grep -qx "RestartPreventExitStatus=3" "$UNITS/asus-turnpad-overlay.service"
    # The dial's own imports with the installed program and the system Python
    # (no Wayland display here: exit status 3 and a clear message).
    status=0
    sudo -u "$TESTER" -H env -u WAYLAND_DISPLAY "$PY" "$APP/overlay/layer_shell.py" 2>/tmp/dial.err || status=$?
    test "$status" = 3 && grep -q "layer-shell" /tmp/dial.err || { cat /tmp/dial.err; echo "dial exit $status"; exit 1; }
    "$PY" -c 'import sys; sys.path.insert(0, "/opt/asus-turnpad"); import overlay.frontend' \
        || { echo "the dial's modules do not import"; exit 1; }
    echo "layer-shell dial: service placed, modules import"
else
    test ! -e "$UNITS/asus-turnpad-overlay.service" || { echo "dial service placed without gtk4-layer-shell"; exit 1; }
    grep -q "gtk4-layer-shell is not available" /tmp/install-sway.log
    echo "gtk4-layer-shell is not packaged here: the dial is left out"
fi
as_tester "bash uninstall.sh" > /tmp/uninstall-sway.log 2>&1 || { tail -30 /tmp/uninstall-sway.log; echo "uninstall.sh failed"; exit 1; }
for path in "$APP" "$UNITS/asus-turnpad.service" "$UNITS/asus-turnpad-overlay.service" "$UNITS/asus-turnpad-screen.service"; do
    test ! -e "$path" || { echo "left behind: $path"; exit 1; }
done
echo "== install test passed"
