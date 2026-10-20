---
name: ship-files
description: Where a new file, Python package, user service, library dependency or doc must be listed so that install.sh, the Arch/deb/rpm packages and uninstall all ship it, and which tests guard each list. Use whenever a change adds or renames a module, folder, service, unit template, udev rule, Python library or GI typelib, or a user-facing .md file.
---

# Shipping a new file

There are two installers. `install.sh` installs to `/opt/asus-turnpad`, and `packaging/install-files.sh` is the one file list behind the PKGBUILD, `debian/rules` and the RPM spec (it installs to `/usr/share/asus-turnpad`). Code must find its files next to itself and never assume a path. Names come from `config/identity.py` and their shell twins in `install_paths.sh`. Scripts never set their own defaults (`tests/test_identity.py`).

| You add | install.sh | packaging/install-files.sh | Also | Guarding test |
|---|---|---|---|---|
| root module `foo.py` | add it to the `sudo install -m 644 … *.py` line | nothing (every root `*.py` but `dialpad_ui.py`) | `packaging/check-imports.py` if the driver needs it inside a `try` | `test_install`: `test_package_copies_what_the_installer_copies`, `test_installer_copies_modules_the_driver_imports` |
| module in `config/`, `desktop/`, `overlay/`, `screen/`, `layouts/` | nothing (`dir/*.py`) | nothing (`for dir in …`) | | same |
| `config_app/foo.py` | add it to the `config_app/…` list (listed one by one) | nothing | | `test_installer_copies_every_settings_app_module` |
| new package folder `pkg/` | `mkdir -p` folder + `install -m 644 -t … pkg/*.py` | append to `for dir in … ; do` | `shipped_modules()` / `local` in `tests/test_install.py`; `uninstall.sh` removes the program folder whole | `test_package_copies_what_the_installer_copies` |
| bridge `integrations/NAME/` | done by `.claude/skills/app-plugin/scaffold_bridge.py --wire` | same | `BRIDGE_PACKAGES`, `check-imports.py` | same |
| GNOME extension file | the `gnome/extension/…` install line **and** `install_gnome_extension.sh` | `EXTENSION_FILES` | | `test_installers_copy_every_extension_file` |
| user service | template in the repo root (for `install.sh`, `$PYTHON $INSTALL_DIR_PATH/…`) + `packaging/<unit>` (`/usr/bin/python3 /usr/share/asus-turnpad/…`); name in `identity.py` + `install_paths.sh`; an `install_*.sh` that calls `install_unit` | `install -Dm644 "packaging/$UNIT" …/systemd/user/` | `%files` in `packaging/rpm/asus-turnpad.spec`, `uninstall.sh` unit loop, `asus-turnpad-setup` if it is enabled per user, `test-package.sh` greps | `test_services_run_the_system_python`, `test_identity` `test_packaging`, `test_rpm_lists_what_install_files_installs` |
| Python library | `install_packages.sh` `PACKAGES_<manager>_<group>` for **every** manager | | PKGBUILD depends, `deb/debian/control`, the rpm spec Requires; `tests/test_install.py` `LIBRARIES` | `test_every_imported_library_has_a_package` |
| GI namespace | as above | | `TYPELIBS` in `tests/test_install.py` | `test_every_typelib_has_a_package` |
| user doc `FOO.md` | | the doc list at the end (`install -Dm644 -t …/doc/…`) | link it from README | (none: check by hand) |

## Rules

- Install from distribution packages only: no venv, no pip, no compiler or `-dev` headers (`test_no_python_environment_or_pip`, `test_no_compiler_or_headers`). A bridge or add-on uses the standard library only.
- Services keep `NoNewPrivileges=yes` and run the system Python.
- The program folder is root-owned. Never write to it at runtime; user data goes to `~/.config/asus-turnpad/` and caches to `~/.cache/asus-turnpad/`.
- `dialpad.py` is CRLF (see the `driver-edit` skill).

## Check

```bash
python3 -m unittest tests.test_install tests.test_identity -v
bash -n install*.sh uninstall.sh packaging/install-files.sh packaging/*/test-package.sh
```

Then CI runs the real package builds and installs (`arch-package`, `deb-package`, `rpm-package`, `install`). By hand, with podman: `bash packaging/arch/test-package.sh` and `tests/install_in_container.sh` (see AGENTS.md › CI).
