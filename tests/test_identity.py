"""Every identifier in config/identity.py has twins outside Python; a rename
must change them together."""

import json
import re
import unittest
from pathlib import Path

from config import identity

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text()


def shell_value(name):
    """Value of NAME="..." or the default of : "${NAME:=...}" in install_paths.sh."""
    text = read("install_paths.sh")
    match = re.search(rf'^{name}="([^"]+)"', text, re.M) or re.search(rf'\$\{{{name}:=(.*)\}}"$', text, re.M)
    if match is None:
        raise AssertionError(f"{name} not found in install_paths.sh")
    return match.group(1).replace("$SLUG", identity.SLUG)


class IdentityTest(unittest.TestCase):
    def test_extension_uses_the_same_bus_and_config_dir(self):
        source = read("gnome/extension/extension.js")
        self.assertIn(f"const BUS_NAME = '{identity.DBUS_NAME}';", source)
        self.assertIn(f"const BUS_PATH = '{identity.DBUS_PATH}';", source)
        self.assertIn(f"GLib.get_user_config_dir(), '{identity.CONFIG_DIR_NAME}'", source)

    def test_extension_uuid(self):
        metadata = json.loads(read("gnome/extension/metadata.json"))
        self.assertEqual(metadata["uuid"], identity.GNOME_EXTENSION_UUID)

    def test_install_paths(self):
        self.assertEqual(shell_value("SLUG"), identity.SLUG)
        self.assertEqual(shell_value("APP_ID"), identity.APP_ID)
        self.assertEqual(shell_value("GNOME_EXTENSION_UUID"), identity.GNOME_EXTENSION_UUID)
        self.assertEqual(shell_value("DRIVER_SERVICE"), identity.DRIVER_SERVICE)
        self.assertEqual(shell_value("INSTALL_DIR_PATH"), identity.INSTALL_DIR)
        self.assertTrue(shell_value("CONFIG_FILE_DIR_PATH").endswith(f"/{identity.CONFIG_DIR_NAME}"))
        self.assertEqual(shell_value("OVERLAY_SERVICE"), identity.OVERLAY_SERVICE)
        self.assertEqual(shell_value("SCREEN_SERVICE"), identity.SCREEN_SERVICE)

    def test_app_link_socket_twin_in_the_blender_add_on(self):
        text = read("integrations/blender/turnpad/protocol.py")
        self.assertIn(f'SOCKET_NAME = "{identity.APP_LINK_SOCKET_NAME}"', text)
        self.assertEqual(identity.app_link_socket_path({"XDG_RUNTIME_DIR": "/run/user/7"}), f"/run/user/7/{identity.APP_LINK_SOCKET_NAME}")
        self.assertIsNone(identity.app_link_socket_path({}))

    def test_no_script_overrides_the_paths(self):
        # Defaults live in install_paths.sh only, so they cannot drift apart.
        for script in ROOT.glob("*install*.sh"):
            if script.name == "install_paths.sh":
                continue
            with self.subTest(script=script.name):
                self.assertNotRegex(script.read_text(), r'(?m)^\s*(INSTALL_DIR_PATH|CONFIG_FILE_DIR_PATH|LOGS_DIR_PATH)="/')

    def test_launcher(self):
        launcher = ROOT / "config_app" / f"{identity.APP_ID}.desktop"
        self.assertTrue(launcher.exists(), launcher)
        text = launcher.read_text()
        self.assertIn(f"Icon={identity.APP_ID}", text)
        self.assertIn(f"Name={identity.APP_NAME}", text)

    def test_packaging(self):
        pkgbuild = read("packaging/arch/PKGBUILD")
        self.assertIn(f"_pkgname={identity.SLUG}\n", pkgbuild)
        self.assertIn(f'url="{identity.REPOSITORY_URL}"', pkgbuild)
        # The file list of every package; its names come from install_paths.sh (test_install_paths).
        files = read("packaging/install-files.sh")
        self.assertIn("source ./install_paths.sh\n", files)
        self.assertIn('PACKAGE_DIR="/usr/share/$SLUG"\n', files)
        self.assertIn('/usr/share/gnome-shell/extensions/$GNOME_EXTENSION_UUID"', files)
        self.assertIn('"config_app/$APP_ID.desktop"', files)
        self.assertIn('/apps/$APP_ID.svg"', files)
        self.assertEqual(identity.PACKAGE_DIR, f"/usr/share/{identity.SLUG}")
        service = read(f"packaging/{identity.SLUG}.service")
        self.assertIn(f"{identity.PACKAGE_DIR}/dialpad.py auto %E/{identity.CONFIG_DIR_NAME}/", service)
        setup = read(f"packaging/{identity.SLUG}-setup")
        self.assertIn(f'GNOME_EXTENSION_UUID="{identity.GNOME_EXTENSION_UUID}"', setup)
        self.assertIn(f'SERVICE="{identity.DRIVER_SERVICE}"', setup)
        # The layer-shell dial's unit: the same name installed and packaged.
        self.assertIn(f'OVERLAY_SERVICE="{identity.OVERLAY_SERVICE}"', setup)
        self.assertTrue((ROOT / identity.OVERLAY_SERVICE).exists())
        self.assertIn(f"{identity.PACKAGE_DIR}/overlay/layer_shell.py", read(f"packaging/{identity.OVERLAY_SERVICE}"))
        self.assertIn('"packaging/$OVERLAY_SERVICE" "$ROOT/usr/lib/systemd/user/$OVERLAY_SERVICE"', files)
        # The SpacePilot Pro's screen: started by the udev rule, which names the same unit.
        self.assertTrue((ROOT / identity.SCREEN_SERVICE).exists())
        self.assertIn(f"{identity.PACKAGE_DIR}/screen/service.py", read(f"packaging/{identity.SCREEN_SERVICE}"))
        self.assertIn('"packaging/$SCREEN_SERVICE" "$ROOT/usr/lib/systemd/user/$SCREEN_SERVICE"', files)
        self.assertIn(f'SYSTEMD_USER_WANTS}}+="{identity.SCREEN_SERVICE}"', read(f"packaging/70-{identity.SLUG}.rules"))
        self.assertIn(f'UDEV_RULE_SCREEN="$INSTALL_UDEV_DIR_PATH/rules.d/70-$SLUG-screen.rules"', read("install_paths.sh"))
        self.assertIn(identity.INSTALL_DIR, setup)
        self.assertIn(f"[ -d {identity.PACKAGE_DIR} ]", read("install.sh"))

    def test_deb_and_rpm_packages(self):
        control = read("packaging/deb/debian/control")
        self.assertIn(f"Source: {identity.SLUG}\n", control)
        self.assertIn(f"Package: {identity.SLUG}\n", control)
        self.assertIn(f"Homepage: {identity.REPOSITORY_URL}\n", control)
        self.assertIn(f"$(CURDIR)/debian/{identity.SLUG} ", read("packaging/deb/debian/rules"))
        self.assertIn(f"systemd-sysusers {identity.SLUG}.conf", read("packaging/deb/debian/postinst"))
        spec = read(f"packaging/rpm/{identity.SLUG}.spec")
        self.assertRegex(spec, rf"(?m)^Name: +{re.escape(identity.SLUG)}$")
        self.assertRegex(spec, rf"(?m)^URL: +{re.escape(identity.REPOSITORY_URL)}$")
        self.assertIn(f"%global extension_uuid {identity.GNOME_EXTENSION_UUID}\n", spec)
        self.assertIn(f"%global app_id {identity.APP_ID}\n", spec)
        for path in ("packaging/deb/build.sh", "packaging/rpm/build.sh"):
            with self.subTest(path=path):
                self.assertIn(f"/{identity.SLUG}/", read(path))
        # How to remove each package, where install.sh refuses to run beside it.
        installer = read("install.sh")
        for command in ("pacman -R asus-turnpad-git", f"apt remove {identity.SLUG}", f"dnf remove {identity.SLUG}"):
            with self.subTest(command=command):
                self.assertIn(command, installer)

    def test_repository_links(self):
        # The repository was renamed; GitHub redirects, but links should not rely on it.
        for path in ROOT.rglob("*"):
            # .claude holds git worktrees of coding assistants, not this checkout.
            if path == Path(__file__).resolve() or ".git" in path.parts or ".claude" in path.parts:
                continue
            if path.is_file() and path.suffix in {".md", ".sh", ".py", ".yml", ".json", ".js", ""}:
                with self.subTest(path=str(path.relative_to(ROOT))):
                    self.assertNotIn("sinu0/asus-linux-dialpad", path.read_text(errors="ignore"))

    def test_service_templates_for_every_session(self):
        for kind in ("wayland", "x11", "plasma-wayland"):
            with self.subTest(service=f"{identity.SLUG}.{kind}.service"):
                self.assertTrue((ROOT / f"{identity.SLUG}.{kind}.service").exists())
        # The legacy window on X11; Wayland desktops have their own dial.
        self.assertTrue((ROOT / f"{identity.SLUG}-ui.x11.service").exists())


if __name__ == "__main__":
    unittest.main()
