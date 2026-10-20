# Installing Turnpad

The short version is in the [README](README.md#install). This page has the details of both ways to install.

| | Arch package | .deb / .rpm package | `install.sh` |
| --- | --- | --- | --- |
| Distributions | Arch Linux and derivatives (CachyOS, EndeavourOS, Manjaro) | Debian 13, Ubuntu 24.04 and newer; Fedora | Any distribution with systemd: Fedora, Ubuntu, Debian, openSUSE, Void, Gentoo, Solus, immutable Fedora |
| Program | `/usr/share/asus-turnpad`, managed by pacman | `/usr/share/asus-turnpad`, managed by apt or dnf | `/opt/asus-turnpad` |
| Python libraries | Distribution packages | Distribution packages | Distribution packages, installed by `install.sh` (no pip) |
| Layout of your laptop | Picked when the driver starts (`auto`) | Picked when the driver starts (`auto`) | Picked during installation |
| Update | `git pull`, then `bash packaging/arch/build.sh` | `git pull`, build again, install the new file | `git pull`, then `bash install.sh` again |
| Remove | `sudo pacman -R asus-turnpad-git` | `sudo apt remove asus-turnpad` / `sudo dnf remove asus-turnpad` | `bash uninstall.sh` |

Use one of them, not both: `install.sh` refuses to run while a package is installed, and `asus-turnpad-setup` refuses while an `install.sh` install is present. Your settings in `~/.config/asus-turnpad` are kept by both and work with both.

| Distribution | Tested | Result |
| --- | --- | --- |
| CachyOS (Arch), GNOME 51 | On a ProArt PX13 | Everything |
| Arch Linux (package) | CI, every push | Package builds, installs, imports, removes |
| Ubuntu 26.04 and 24.04 LTS, Debian 13 (.deb) | CI, every push | Package builds, installs, imports, removes |
| Fedora 44 (.rpm) | CI, every push | Package builds, installs, imports, removes |
| Fedora 44 (`install.sh`) | CI, every push | Installs, imports, uninstalls |
| Ubuntu 26.04 LTS (`install.sh`) | CI, every push | Installs, imports, uninstalls |
| Ubuntu 24.04 LTS (`install.sh`) | CI, every push | Installs, imports, uninstalls |
| Debian, openSUSE, Void, Gentoo, Solus, immutable Fedora | No | The installer has their package names from the original project |

The CI tests run in containers without a DialPad, so they cover packages, the libraries the driver and the settings app import, files and the uninstaller, not the touchpad itself. The settings app needs Libadwaita 1.5 (GNOME 46, Ubuntu 24.04, Debian 13 or newer); the installer warns when it is older.

### The on-screen dial on your desktop

The driver works on every desktop. The dial on the screen comes from a different part per desktop; the installer and `asus-turnpad-setup` pick it from the session they run in (`XDG_CURRENT_DESKTOP`, `XDG_SESSION_TYPE`), so run them from a terminal of that desktop.

| Desktop | On-screen dial | Packages | Tested |
| --- | --- | --- | --- |
| GNOME 45–51 | GNOME Shell extension | none extra | On a ProArt PX13 (GNOME 51) |
| sway | Layer-shell dial, user service `asus-turnpad-overlay.service` | `gtk4-layer-shell` (Arch, Fedora), `gir1.2-gtk4layershell-1.0` (Debian 13, Ubuntu after 24.04), `typelib-1_0-Gtk4LayerShell-1_0` (openSUSE) | CI, every push: headless sway, screenshots compared with the GNOME dial, at 100 % and 200 % |
| KDE Plasma 6 | Layer-shell dial, as for sway | as for sway | By hand: the same test on headless KWin 6.8 at 100 % and 200 % (KWin needs a GPU render node, which the CI machines do not have) |
| Hyprland, niri, COSMIC | Layer-shell dial, as for sway | as for sway | Not tested |
| X11 sessions | The legacy window (`dialpad_ui.py`, PySide6) | PySide6 | Not tested |

Ubuntu 24.04 does not package gtk4-layer-shell: there the installer leaves the dial out on non-GNOME desktops and the driver works without it. The layer-shell dial starts with the graphical session (`graphical-session.target`), which Plasma, COSMIC, niri (`niri-session`) and Hyprland with uwsm start for you. When sway or Hyprland is started without such a session, start the dial from its config instead:

```
exec systemctl --user import-environment WAYLAND_DISPLAY && systemctl --user start asus-turnpad-overlay
```

Logs: `journalctl --user -u asus-turnpad-overlay`. It shows on the output that has focus; `/usr/bin/python3 /opt/asus-turnpad/overlay/layer_shell.py --output eDP-1` (or `/usr/share/asus-turnpad/...` for the packages) pins it to one.

### SpaceMouse mode

A 3Dconnexion SpaceMouse (tested: SpacePilot Pro) needs `spacenavd`, the free driver for these devices. Turnpad is one more client of it and needs no library, so it is optional and none of the packages or `install.sh` pulls it in. Install it from your distribution and start it once:

| Distribution | Package |
| --- | --- |
| Arch, CachyOS | `spacenavd` from the AUR (`yay -S spacenavd`) |
| Fedora, Debian, Ubuntu | `spacenavd` (`sudo dnf install spacenavd`, `sudo apt install spacenavd`) |

```bash
sudo systemctl enable --now spacenavd
```

Then switch SpaceMouse mode on in the app (the first-run assistant does it when it finds the device, or the *SpaceMouse* page) and let it restart the driver. The mode works on a computer without a touchpad, alone or together with Mouse-only mode; with a DialPad or Touchpad mode it is ignored for now. Everything about the buttons, the cap and the screen is in [SPACEMOUSE.md](SPACEMOUSE.md).

**Blender:** for Turnpad to run Blender's own commands (and to read its mode), Blender needs the Turnpad add-on. The *SpaceMouse* page of the settings app has a *Blender* group with *Install the Blender add-on*: it builds the add-on from the program directory (`integrations/blender/turnpad`) and installs it into your Blender with `blender --command extension`, or, without Blender on the `PATH` or when that fails, writes a zip and shows how to install it from Edit › Preferences › Get Extensions › Install from Disk. Nothing is installed system-wide.

### The screen of a SpacePilot Pro

A 3Dconnexion SpacePilot Pro (USB `046d:c629`) has a small screen that shows Turnpad's keys, dial and clock. It needs the optional package `pyusb` (`python-pyusb` on Arch, `python3-usb` on Debian and Ubuntu, `python3-pyusb` on Fedora and openSUSE); `install.sh` installs it when the distribution has it, the Arch, Debian and Fedora packages list it as optional or recommended. The user service `asus-turnpad-screen.service` starts by itself when the device is plugged in (the udev rule for USB `046d:c629` is installed with the other rules, by `install.sh` and by every package), needs no enabling, and stops quietly when pyusb or the device is missing. Logs: `journalctl --user -u asus-turnpad-screen`.

## Arch package

```bash
git clone https://github.com/sinu0/asus-turnpad.git
cd asus-turnpad
bash packaging/arch/build.sh   # builds the latest commit on GitHub and installs it with pacman
asus-turnpad-setup             # once, as your user
```

`build.sh` runs `makepkg -si` in `~/.cache/asus-turnpad/makepkg` rather than in the clone (makepkg writes the version into the PKGBUILD it builds from, which would get in the way of `git pull`); arguments are passed on to makepkg. Running `makepkg -si` in `packaging/arch` yourself works too.

Log out and back in. `asus-turnpad-setup` adds you to the groups `input`, `i2c` and `uinput` (asks for sudo) and enables the driver's user service and the on-screen dial: the GNOME extension, or on other Wayland desktops the layer-shell dial's user service (install `gtk4-layer-shell` first, see [the dial on your desktop](#the-on-screen-dial-on-your-desktop)). It changes nothing for other users; each user who wants the dial runs it once.

What the package installs:

| Path | Content |
| --- | --- |
| `/usr/share/asus-turnpad` | Driver and settings app |
| `/usr/share/gnome-shell/extensions/asus-turnpad@sinu0.github.io` | The on-screen dial for GNOME |
| `/usr/lib/systemd/user/asus-turnpad.service` | The driver as a user service (enabled per user by `asus-turnpad-setup`) |
| `/usr/lib/systemd/user/asus-turnpad-overlay.service` | The on-screen dial on Wayland desktops other than GNOME (enabled per user by `asus-turnpad-setup`) |
| `/usr/lib/systemd/user/asus-turnpad-screen.service` | The screen of a SpacePilot Pro (started by udev when the device is plugged in; needs `python-pyusb`) |
| `/usr/lib/udev/rules.d/70-asus-turnpad.rules` | Access to uinput, the I2C buses, the DialPad touchpad's hidraw node and a SpacePilot Pro for the logged-in user |
| `/usr/lib/modules-load.d/asus-turnpad.conf`, `/usr/lib/sysusers.d/asus-turnpad.conf` | Kernel modules `uinput` and `i2c-dev`; groups `i2c` and `uinput` |
| `/usr/bin/asus-turnpad-setup` | The per-user setup above |

**Update:** `git pull`, then `bash packaging/arch/build.sh` (it always builds the latest commit on GitHub and restarts the driver). A changed GNOME extension loads at the next login.

**Remove:**

```bash
systemctl --user disable --now asus-turnpad asus-turnpad-overlay
sudo pacman -R asus-turnpad-git
```

**Coming from `install.sh`:** run `bash uninstall.sh` in the old clone first (it keeps your settings), then install the package.

## .deb and .rpm packages (Debian, Ubuntu, Fedora)

There is no PPA or COPR repository yet; build the package from a clone:

```bash
git clone https://github.com/sinu0/asus-turnpad.git
cd asus-turnpad

# Debian 13, Ubuntu 24.04 or newer
sudo apt install build-essential debhelper dh-python git
bash packaging/deb/build.sh
sudo apt install ~/.cache/asus-turnpad/deb/asus-turnpad_*_all.deb

# Fedora
sudo dnf install rpm-build python3-devel systemd-rpm-macros desktop-file-utils git
bash packaging/rpm/build.sh
sudo dnf install ~/.cache/asus-turnpad/rpm/asus-turnpad-*.noarch.rpm

asus-turnpad-setup             # once, as your user
```

`build.sh` builds the checked-out commit (or the one given as argument; `--worktree` adds uncommitted changes) from a copy made with `git archive` in a temporary directory, so the clone stays untouched, and puts the package in `~/.cache/asus-turnpad/deb` or `rpm` (`-o <dir>` for another place). The version is `0.r<commits>.<hash>`, like the Arch package's. apt or dnf installs the libraries from the distribution; `python3-pywayland` is only recommended (Ubuntu 24.04 has none).

The package installs the same files as the Arch package (one list for all three, `packaging/install-files.sh`), and `asus-turnpad-setup` works the same way: log out and back in afterwards.

**Update:** `git pull`, build again and install the new file, then `systemctl --user restart asus-turnpad`. A changed GNOME extension loads at the next login.

**Remove:**

```bash
systemctl --user disable --now asus-turnpad asus-turnpad-overlay
sudo apt remove asus-turnpad     # or: sudo dnf remove asus-turnpad
```

## install.sh (all distributions)

```bash
git clone https://github.com/sinu0/asus-turnpad.git
cd asus-turnpad
bash install.sh
```

The installer needs `sudo`. It installs the required system packages with your package manager (apt, dnf, pacman, zypper, xbps, emerge, eopkg, rpm-ostree), the Python libraries included (see [Python](#python)), copies the program to `/opt/asus-turnpad`, adds you to the groups `input`, `i2c` and `uinput`, picks the layout of your laptop, and sets up the user service and the on-screen dial of your desktop (see [above](#the-on-screen-dial-on-your-desktop)). It asks no questions; `bash install.sh --interactive` lets you choose the layout, a co-activator key and the services yourself. Log out and back in afterwards.

When your laptop is not in `laptop_touchpad_dialpad_layouts.csv`, the installer looks up the touchpad's vendor and product id on linux-hardware.org to find laptops with the same touchpad; if that also fails it uses `proartp16`, and you measure the dial with *Calibration → Guided calibration* in the app.

**Update:** `git pull`, then `bash install.sh` again.

### Uninstall

```bash
bash uninstall.sh                  # keeps your settings in ~/.config/asus-turnpad
bash uninstall.sh --interactive    # asks whether to remove them too
```

It removes the services (the dial's too), the GNOME extension, `/opt/asus-turnpad`, the udev rules and module list, and anything left from a version before the rename. It uses the same paths as the installer (`install_paths.sh`), so pass the same environment variables if you changed them. Group memberships (`input`, `uinput`, `i2c`) stay.

### Custom paths

The installer and the uninstaller read these environment variables (defaults in `install_paths.sh`):

```
# ENV VARS (with the defaults, see install_paths.sh)
INSTALL_DIR_PATH="/opt/asus-turnpad"                 # program, owned by root
CONFIG_FILE_DIR_PATH="$HOME/.config/asus-turnpad"    # settings, skins and driver state
LOGS_DIR_PATH="/var/log/asus-turnpad"                # only for install and uninstall logs
INSTALL_UDEV_DIR_PATH="/etc/udev"
```

Where things go follows the Filesystem Hierarchy Standard for software installed outside the package manager:

| Path | Content |
| --- | --- |
| `/opt/asus-turnpad` | Driver, settings app, GNOME extension source (read-only for users) |
| `~/.config/asus-turnpad` | `config.json`, `skins/`, driver state `dialpad_dev` |
| `~/.config/systemd/user/asus-turnpad.service` | The driver as a user service, started with the graphical session |
| `~/.local/share/gnome-shell/extensions/asus-turnpad@sinu0.github.io` | The on-screen dial for GNOME |
| `~/.config/systemd/user/asus-turnpad-screen.service` | The screen of a SpacePilot Pro, started by its udev rule |
| `/etc/udev/rules.d/70-asus-turnpad-*.rules`, `/etc/modules-load.d/asus-turnpad.conf` | Access to the touchpad's I2C and hidraw nodes, to uinput and to a SpacePilot Pro; kernel modules loaded at boot |
| `/var/log/asus-turnpad` | Logs of the installer and uninstaller |

### Running parts of the installer

Check that a touchpad with a DialPad is found:

```bash
$ bash install_device_check.sh
```

Add your user to the groups `i2c`, `input` and `uinput`:

```bash
$ bash install_user_groups.sh
```

Start the driver now and at every login (run as your user, not with `sudo`; it uses `systemctl --user`):

```bash
# with user interface
$ bash install_user_interface.sh
$ bash install_service.sh

# with no user interface
$ USER_INTERFACE=0 \
 bash install_service.sh
```

### Immutable systems (BazziteOS, Fedora Silverblue, Kinoite)

The default paths are writable on immutable distributions (`/opt` and `/etc`); expect two reboots:

```bash
# First run - installs system packages
$ bash install.sh

# Reboot when prompted (required for package layering)
$ systemctl reboot

# Second run - completes driver installation
$ bash install.sh

# Final reboot (required for group membership)
$ systemctl reboot
```

**Why two reboots?** Immutable systems require a reboot after layering packages and another after group membership changes.

### Without systemd

Install with `bash install.sh --interactive` and answer no to the question about `systemd`, then start the driver yourself. The first argument is the layout (`auto` picks the one of this laptop), the second the directory for the driver state `dialpad_dev`:

```
/usr/bin/python3 /opt/asus-turnpad/dialpad.py <auto|asusvivobook16x|proartp16|..> ~/.config/asus-turnpad/
```

### Python

Turnpad runs with the distribution's Python, `/usr/bin/python3` (3.10 or newer), and the Python libraries from its packages; `install_packages.sh` lists them per package manager. Nothing is downloaded with pip and nothing is compiled. A Python from conda or pyenv in your `PATH` is not used. Installs from before kept a Python environment in `/opt/asus-turnpad/.env`; `install.sh` and `uninstall.sh` remove it.

Some libraries are optional: when your release does not package one, the installer says so and the feature it serves stays off (for example `python3-pywayland` on Ubuntu 24.04; on Ubuntu 26.04 it is installed but does not load with Python 3.14; `python3-periphery` outside Debian and Ubuntu, PySide6 for the legacy on-screen dial window on X11, and gtk4-layer-shell for the dial on other Wayland desktops on Ubuntu 24.04).
