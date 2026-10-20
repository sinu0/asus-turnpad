"""The installers copy files one by one; a module or extension file missing
there only fails on the next start or login. Libraries come from
distribution packages only (install_packages.sh); a library imported
without a package there only fails on the user's laptop."""

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "gnome" / "extension"
PACKAGES_SCRIPT = ROOT / "install_packages.sh"

# Package managers whose names are tested in containers (CI or by hand).
MANAGERS = ("apt", "dnf", "pacman", "zypper")

# Every library a shipped module imports: its group in install_packages.sh
# and the package per manager (None: not packaged there, the import is
# optional).
LIBRARIES = {
    "libevdev": ("required", {"apt": "python3-libevdev", "dnf": "python3-libevdev", "pacman": "python-libevdev", "zypper": "python3-libevdev"}),
    "gi": ("required", {"apt": "python3-gi", "dnf": "python3-gobject", "pacman": "python-gobject", "zypper": "python3-gobject"}),
    "cairo": ("required", {"apt": "python3-gi-cairo", "dnf": "python3-cairo", "pacman": "python-cairo", "zypper": "python3-gobject-cairo"}),
    "pyatspi": ("optional", {"apt": "python3-pyatspi", "dnf": "python3-pyatspi", "pacman": "python-atspi", "zypper": "python3-atspi"}),
    "periphery": ("optional", {"apt": "python3-periphery", "dnf": None, "pacman": None, "zypper": None}),
    "pywayland": ("wayland", {"apt": "python3-pywayland", "dnf": "python3-pywayland", "pacman": "python-pywayland", "zypper": "python3-pywayland"}),
    "Xlib": ("x11libs", {"apt": "python3-xlib", "dnf": "python3-xlib", "pacman": "python-xlib", "zypper": "python3-python-xlib"}),
    "xcffib": ("x11libs", {"apt": "python3-xcffib", "dnf": "python3-xcffib", "pacman": "python-xcffib", "zypper": "python3-xcffib"}),
    "systemd": ("systemd", {"apt": "python3-systemd", "dnf": "python3-systemd", "pacman": "python-systemd", "zypper": "python3-systemd"}),
    # The screen of a SpacePilot Pro (screen/); optional.
    "usb": ("screen", {"apt": "python3-usb", "dnf": "python3-pyusb", "pacman": "python-pyusb", "zypper": "python3-pyusb"}),
    "PySide6": ("ui", {"apt": "python3-pyside6.qtwidgets", "dnf": "python3-pyside6", "pacman": "pyside6", "zypper": "python3-pyside6"}),
}

# GObject introspection namespaces (gi.require_version): their group in
# install_packages.sh and the package that brings the typelib (itself or as
# a dependency) per manager.
TYPELIBS = {
    "GLib": ("required", {"apt": "python3-gi", "dnf": "python3-gobject", "pacman": "python-gobject", "zypper": "python3-gobject"}),
    "Gio": ("required", {"apt": "python3-gi", "dnf": "python3-gobject", "pacman": "python-gobject", "zypper": "python3-gobject"}),
    # GLib 2.80 and newer (overlay/layer_shell.py falls back to GLib before);
    # gir1.2-glib-2.0, glib2 and typelib-1_0-GLibUnix-2_0 come with PyGObject.
    "GLibUnix": ("required", {"apt": "python3-gi", "dnf": "python3-gobject", "pacman": "python-gobject", "zypper": "python3-gobject"}),
    "Gtk": ("required", {"apt": "gir1.2-gtk-4.0", "dnf": "gtk4", "pacman": "gtk4", "zypper": "typelib-1_0-Gtk-4_0"}),
    "Gdk": ("required", {"apt": "gir1.2-gtk-4.0", "dnf": "gtk4", "pacman": "gtk4", "zypper": "typelib-1_0-Gtk-4_0"}),
    "GdkPixbuf": ("required", {"apt": "gir1.2-gtk-4.0", "dnf": "gtk4", "pacman": "gtk4", "zypper": "typelib-1_0-Gtk-4_0"}),
    "Adw": ("required", {"apt": "gir1.2-adw-1", "dnf": "libadwaita", "pacman": "libadwaita", "zypper": "typelib-1_0-Adw-1"}),
    # Optional on Debian and Ubuntu (overlay/gtk_icons.py works without it);
    # elsewhere the required librsvg has it.
    "Rsvg": ("optional", {"apt": "gir1.2-rsvg-2.0", "dnf": "librsvg2", "pacman": "librsvg", "zypper": "typelib-1_0-Rsvg-2_0"}),
    # The on-screen dial on Wayland desktops other than GNOME; not packaged
    # by Ubuntu 24.04 (the dial is then left out).
    "Gtk4LayerShell": ("layershell", {"apt": "gir1.2-gtk4layershell-1.0", "dnf": "gtk4-layer-shell",
                                      "pacman": "gtk4-layer-shell", "zypper": "typelib-1_0-Gtk4LayerShell-1_0"}),
}


def shipped_modules():
    """Python files install.sh copies to the program directory."""
    files = list(ROOT.glob("*.py"))
    for package in ("config", "config_app", "desktop", "overlay", "layouts", "screen"):
        files += (ROOT / package).glob("*.py")
    return files


def package_groups():
    """{(manager, group): [names]} from PACKAGES_<manager>_<group>="..." lines."""
    text = PACKAGES_SCRIPT.read_text()
    groups = {}
    for manager, group, names in re.findall(r'^PACKAGES_([a-z]+)_([a-z0-9]+)="([^"]*)"', text, re.M):
        groups[(manager, group)] = names.split()
    return groups


class InstallTest(unittest.TestCase):
    def test_installers_copy_every_extension_file(self):
        files = sorted(path.name for path in EXTENSION.iterdir() if path.suffix in {".js", ".json", ".css"})
        for script in ("install.sh", "install_gnome_extension.sh"):
            text = (ROOT / script).read_text()
            for name in files:
                with self.subTest(script=script, file=name):
                    self.assertIn(f"gnome/extension/{name}", text)

    def test_installer_copies_modules_the_driver_imports(self):
        tree = ast.parse((ROOT / "dialpad.py").read_text())
        names = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        names |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        text = (ROOT / "install.sh").read_text()
        for name in sorted(names):
            if (ROOT / f"{name}.py").exists():
                with self.subTest(module=name):
                    self.assertRegex(text, rf"install .*\b{name}\.py")
            elif (ROOT / name / "__init__.py").exists():
                with self.subTest(package=name):
                    self.assertIn(f"{name}/*.py", text)

    def test_package_copies_what_the_installer_copies(self):
        packaged = (ROOT / "packaging" / "install-files.sh").read_text()  # shared by all packages
        installer = (ROOT / "install.sh").read_text()
        for path in sorted(ROOT.glob("*.py")):
            if path.name == "dialpad_ui.py":
                # Legacy PySide overlay for desktops without the Shell extension.
                continue
            with self.subTest(module=path.name):
                self.assertIn(path.name, installer)
        # The packages take every module next to the driver but that one.
        self.assertIn("for module in *.py; do", packaged)
        self.assertIn("$module == dialpad_ui.py ]] || MODULES+=", packaged)
        for package in ("config", "config_app", "desktop", "layouts", "overlay", "screen"):
            with self.subTest(package=package):
                if package != "config_app":  # listed one by one, see below
                    self.assertIn(f"{package}/*.py", installer)
                self.assertRegex(packaged, rf"for dir in [^;]*\b{package}\b")
        for name in sorted(path.name for path in EXTENSION.iterdir() if path.suffix in {".js", ".json", ".css"}):
            with self.subTest(file=name):
                self.assertRegex(packaged, rf"gnome/extension/\{{[^}}]*\b{re.escape(name)}\b")
        # The Blender add-on, py and toml, in the installer and the packages.
        self.assertIn("integrations/blender/turnpad/*.py integrations/blender/turnpad/*.toml", installer)
        self.assertIn("integrations/blender/turnpad/*.py integrations/blender/turnpad/*.toml", packaged)
        for name in ("laptop_touchpad_dialpad_layouts.csv", "config_app/icon.svg", "config_app/style.css"):
            with self.subTest(file=name):
                self.assertIn(name, installer)
                self.assertIn(name, packaged)

    def test_every_package_installs_the_shared_file_list(self):
        # One list for the Arch, Debian and RPM packages, so a new file
        # cannot be missing from one of them.
        for recipe in ("arch/PKGBUILD", "deb/debian/rules", "rpm/asus-turnpad.spec"):
            text = (ROOT / "packaging" / recipe).read_text()
            with self.subTest(recipe=recipe):
                self.assertRegex(text, r"(?m)^\s*bash packaging/install-files\.sh ")
                self.assertNotRegex(text, r"(?m)^\s*install -")

    def test_rpm_lists_what_install_files_installs(self):
        # rpmbuild fails on installed but unlisted files only in a Fedora
        # container; every directory install-files.sh writes needs a line.
        spec = (ROOT / "packaging" / "rpm" / "asus-turnpad.spec").read_text()
        files = spec.split("\n%files\n", 1)[1]
        for path in ("%{_datadir}/%{name}\n", "%{_datadir}/gnome-shell/extensions/%{extension_uuid}\n",
                     "%{_datadir}/applications/", "%{_datadir}/icons/", "%{_userunitdir}/", "%{_udevrulesdir}/",
                     "%{_modulesloaddir}/", "%{_sysusersdir}/", "%{_bindir}/", "%{_docdir}/%{name}\n"):
            with self.subTest(path=path):
                self.assertIn(path, files)

    def test_debian_scripts_keep_the_debhelper_snippets(self):
        # dh_python3 adds py3compile/py3clean there; without them the
        # package has no bytecode.
        for script in ("postinst", "prerm", "postrm"):
            path = ROOT / "packaging" / "deb" / "debian" / script
            with self.subTest(script=script):
                self.assertIn("\n#DEBHELPER#\n", path.read_text())
                self.assertTrue(path.stat().st_mode & 0o111, "not executable")

    def test_installer_copies_every_settings_app_module(self):
        text = (ROOT / "install.sh").read_text()
        for path in sorted((ROOT / "config_app").glob("*.py")):
            with self.subTest(module=path.name):
                self.assertIn(f"config_app/{path.name}", text)


class DistributionPackagesTest(unittest.TestCase):
    def test_no_python_environment_or_pip(self):
        # The program runs with /usr/bin/python3 and distribution packages;
        # only install_migrate.sh may name the old environment, to remove it.
        scripts = sorted(ROOT.glob("*.sh")) + sorted(ROOT.glob("*.service"))
        for script in scripts:
            text = script.read_text()
            with self.subTest(script=script.name):
                self.assertNotRegex(text, r"\bpip3?\s+install|venv_pip|virtualenv|-m venv|requirements[\w.]*\.txt")
                if script.name != "install_migrate.sh":
                    self.assertNotRegex(text, r"/\.env\b")
        self.assertEqual(sorted(ROOT.glob("requirements*.txt")), [])

    def test_no_compiler_or_headers(self):
        for (manager, group), names in sorted(package_groups().items()):
            for name in names:
                with self.subTest(manager=manager, group=group, package=name):
                    self.assertNotRegex(name, r"gcc|clang|pkg-?conf|-devel$|-dev$|virtualenv|\bpip\b")

    def test_services_run_the_system_python(self):
        paths = (ROOT / "install_paths.sh").read_text()
        self.assertIn('PYTHON="/usr/bin/python3"', paths)
        self.assertIn("$PYTHON", (ROOT / "install_service.sh").read_text())
        for template in sorted(ROOT.glob("*.service")):
            with self.subTest(service=template.name):
                self.assertRegex(template.read_text(),
                                 r"(?m)^ExecStart=\$PYTHON \$INSTALL_DIR_PATH/(dialpad(_ui)?|overlay/layer_shell|screen/service)\.py\b")
        for unit in sorted((ROOT / "packaging").glob("*.service")):
            with self.subTest(service=unit.name):
                self.assertRegex(unit.read_text(), r"(?m)^ExecStart=/usr/bin/python3 /usr/share/asus-turnpad/")

    def test_layer_shell_dial_service(self):
        # Exit status 3 = no layer-shell here (GNOME, X11): never restarted.
        for unit in (ROOT / "asus-turnpad-overlay.service", ROOT / "packaging" / "asus-turnpad-overlay.service"):
            text = unit.read_text()
            with self.subTest(service=str(unit.relative_to(ROOT))):
                self.assertRegex(text, r"(?m)^ExecStart=(\$PYTHON|/usr/bin/python3) \S+/overlay/layer_shell\.py$")
                for line in ("PartOf=graphical-session.target", "Restart=on-failure", "RestartPreventExitStatus=3",
                             "NoNewPrivileges=yes", "WantedBy=graphical-session.target"):
                    self.assertIn(f"\n{line}\n", text)
                self.assertRegex(text, r"(?m)^After=graphical-session\.target\b")

    def test_screen_service(self):
        # Started by the udev rule of the SpacePilot Pro, so no [Install]
        # section and no graphical-session.target; exit status 3 = nothing to
        # do (no screen, no pyusb, switched off): never restarted.
        rule = 'ATTR{idVendor}=="046d", ATTR{idProduct}=="c629", TAG+="uaccess", TAG+="systemd", ENV{SYSTEMD_USER_WANTS}+="asus-turnpad-screen.service"'
        for unit in (ROOT / "asus-turnpad-screen.service", ROOT / "packaging" / "asus-turnpad-screen.service"):
            text = unit.read_text()
            with self.subTest(service=str(unit.relative_to(ROOT))):
                self.assertRegex(text, r"(?m)^ExecStart=(\$PYTHON \$INSTALL_DIR_PATH|/usr/bin/python3 /usr/share/asus-turnpad)/screen/service\.py$")
                for line in ("Description=Turnpad on the SpaceMouse screen", "Restart=on-failure", "RestartSec=2",
                             "RestartPreventExitStatus=3", "NoNewPrivileges=yes"):
                    self.assertIn(f"\n{line}\n", text)
                self.assertNotRegex(text, r"(?m)^\[Install\]")
                self.assertNotRegex(text, r"(?m)^[^#\n]*graphical-session")
                # Not for the user managers of system users (the greeters of gdm and sddm).
                self.assertIn("\nConditionUser=!@system\n", text)
                self.assertLess(text.index("ConditionUser=!@system"), text.index("[Service]"))
                self.assertNotIn("Commands of dial actions", text)
        for rules in ("packaging/70-asus-turnpad.rules", "install_user_groups.sh"):
            with self.subTest(rules=rules):
                body = (ROOT / rules).read_text()
                self.assertIn('ACTION!="remove", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ' + (rule if rules.endswith(".rules")
                              else rule.replace("asus-turnpad-screen.service", "'\"$SCREEN_SERVICE\"'")), body)
        installer = (ROOT / "install.sh").read_text()
        self.assertIn("source install_screen.sh", installer)
        self.assertLess(installer.index("source install_overlay.sh"), installer.index("source install_screen.sh"))
        self.assertIn("$(packages screen)", installer)
        self.assertIn('install_unit "$SCREEN_SERVICE" "$SCREEN_SERVICE"', (ROOT / "install_screen.sh").read_text())
        self.assertIn('"$SCREEN_SERVICE"', (ROOT / "uninstall.sh").read_text())
        self.assertIn('"$UDEV_RULE_SCREEN"', (ROOT / "uninstall.sh").read_text())
        self.assertIn("screen/*.py", installer)

    def test_overlay_per_desktop(self):
        overlay = (ROOT / "install_overlay.sh").read_text()
        self.assertIn("source install_gnome_extension.sh", overlay)
        self.assertIn("source install_layer_shell_overlay.sh", overlay)
        # The layer-shell dial comes after the services, whose functions it uses.
        installer = (ROOT / "install.sh").read_text()
        self.assertLess(installer.index("source install_service.sh"), installer.index("source install_overlay.sh"))
        dial = (ROOT / "install_layer_shell_overlay.sh").read_text()
        self.assertIn('install_unit "$OVERLAY_SERVICE" "$OVERLAY_SERVICE"', dial)
        self.assertIn("install_optional $(packages layershell)", dial)
        # The legacy window only on X11.
        self.assertEqual(sorted(path.name for path in ROOT.glob("*-ui.*.service")), ["asus-turnpad-ui.x11.service"])
        self.assertIn('install_unit "$SLUG-ui.x11.service" "$UI_SERVICE"', (ROOT / "install_service.sh").read_text())
        uninstall = (ROOT / "uninstall.sh").read_text()
        self.assertIn('"$OVERLAY_SERVICE"', uninstall)

    def test_every_manager_has_every_group(self):
        groups = package_groups()
        managers = sorted({manager for manager, _ in groups})
        names = sorted({group for _, group in groups})
        self.assertEqual(names, sorted(["required", "x11", "plasma", "optional", "wayland", "x11libs", "systemd", "ui",
                                                "layershell", "screen"]))
        for manager in managers:
            for group in names:
                with self.subTest(manager=manager, group=group):
                    self.assertIn((manager, group), groups)

    def test_every_imported_library_has_a_package(self):
        local = {path.stem for path in ROOT.glob("*.py")} | {"config", "config_app", "desktop", "overlay", "layouts", "screen"}
        imported = {}
        for path in shipped_modules():
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    modules = [node.module]
                else:
                    continue
                for module in modules:
                    top = module.split(".")[0]
                    if top not in sys.stdlib_module_names and top not in local and top != "__future__":
                        imported.setdefault(top, set()).add(path.name)
        for module, users in sorted(imported.items()):
            with self.subTest(module=module, used_by=sorted(users)):
                self.assertTrue(module in LIBRARIES,
                                f"{module} has no distribution package in install_packages.sh (tests/test_install.py LIBRARIES)")
        groups = package_groups()
        for module, (group, names) in sorted(LIBRARIES.items()):
            for manager in MANAGERS:
                with self.subTest(module=module, manager=manager):
                    self.assertIn(manager, names)
                    if names[manager] is not None:
                        self.assertIn(names[manager], groups[(manager, group)])

    def test_every_typelib_has_a_package(self):
        namespaces = set()
        for path in shipped_modules():
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "require_version" and node.args:
                    namespaces.add(node.args[0].value)
        groups = package_groups()
        for namespace in sorted(namespaces):
            with self.subTest(namespace=namespace):
                self.assertIn(namespace, TYPELIBS, "no package in tests/test_install.py TYPELIBS")
                group, names = TYPELIBS[namespace]
                for manager in MANAGERS:
                    # Required groups bring required typelibs; the others
                    # turn one feature off when missing.
                    installed = groups[(manager, "required")] + groups[(manager, group)]
                    self.assertIn(names[manager], installed)

    def test_native_libraries_and_tools(self):
        # libxkbcommon (keyboard layouts), i2ctransfer, curl and xmllint
        # (layout lookup), envsubst (service files).
        groups = package_groups()
        for manager in MANAGERS:
            required = " ".join(groups[(manager, "required")])
            with self.subTest(manager=manager):
                for name in ("libxkbcommon", "i2c-tools", "curl", "libxml2"):
                    self.assertIn(name, required)
                self.assertRegex(required, r"gettext|envsubst")


if __name__ == "__main__":
    unittest.main()
