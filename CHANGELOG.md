# Changelog

## Fork (unreleased)

### Feature

- SpaceMouse mode (config `spacemouse`; needs spacenavd, optional): a 3Dconnexion SpaceMouse drives the dial: buttons with press and hold actions (macro, modifier, dial, group, function, select, back; a SpacePilot Pro default map), tilt picks a tile or steps through a list, twist turns, a push on the cap is the centre, release a button to select; other spacenavd clients are muted while the dial is on; works alone or together with Mouse-only mode; the full guide is in [SPACEMOUSE.md](SPACEMOUSE.md)
- SpaceMouse button layers (`layer` action: hold, latch or toggle; global and per application) and the cap's motion per application (sensitivity, per-axis sensitivity, inverted axes, rotation and translation off; set in spacenavd while the application has focus and given back on exit, with session buttons Rotation, Pan/Zoom, + and −)
- SpaceMouse buttons by physical key: the settings page lists the keys of the device (SpacePilot Pro: the function keys, T, R, F, Roll and ISO are dual-function keys whose long press the device reports as another button, so the default map binds 12–16 short and 17–21 long), buttons can carry a `label` for a device screen, and the config has a `screen` section (and per-profile `screen` overrides) for the SpacePilot Pro's screen
- SpaceMouse buttons: `key_hold` (keys held while the button is down, e.g. a Blender pie menu) and `none` (leave the button to the application, overriding the global map in a profile); `hide_buttons` hides exactly what the focused application's profile binds
- SpacePilot Pro screen: the colour screen of the device shows Turnpad (user service `asus-turnpad-screen.service`, started by udev when the device is plugged in, needs the optional `pyusb`): pages for the keys with their short and long actions, the dial, all buttons, the cap's motion and a clock; the screen's own keys steer the dial (OK, Up, Down) and open quick settings (brightness, dimming, theme, text size, start page, …) and a toast shows notices; it dims and switches off by itself, can follow the dial, has its pages, start page and accent per application and shows the label of each function key; the *Screen* page of the settings app sets all this with a live preview of every page
- D-Bus for programs that follow or steer the driver: `Control` (`toggle`, `dial-on`, `dial-off`, `rotate:N`, `select`, `back`, `function:N`, `group`, `button:INDEX` and `mute:MS`, which silences other spacenavd clients for a while), the `DriverState` signal and `GetDriverState()` (profile, application, dial, function, held keys, layer, motion, context, app link, offers and notices); the SpacePilot Pro screen is built on them
- Ready application packs carry SpaceMouse buttons too (the five function keys short and long, Fit and the view keys where the application has shortcuts, `none` where it reads the 3D mouse itself, a label per button for the device screen); new packs for FreeCAD (category "3D") and KiCad ("Electronics")
- Contexts editor (Dial Controls): a profile can have contexts that switch the dial menu, the SpaceMouse buttons and the screen's start page by a word in the window title or by the application's mode (for Blender: Object, Edit Mesh, Sculpt, …); the editor checks the title against a test text, lists the modes the add-on reports and lets a context keep its own menu and buttons
- Macro recorder (function editor › *Record…* next to *+*): press the keys after a 3-2-1 countdown and they become macro steps, with or without the pauses between them; it reads every real keyboard, or the window's own key events when the keyboards cannot be read
- App link: applications with an add-on (Blender) tell Turnpad their commands and mode through a private socket in the runtime directory; dial and button actions can run an application's own commands (`app` steps and actions, with the shortcut as fallback); profile contexts switch the dial menu and buttons by window title or application mode; the window title follows KDE (KWin script) and GNOME (extension); a ready-made profile is offered once per application with a notification (its button *Create profile* opens the settings app with `--add-profile <application id>`, which a running window takes over); the Blender add-on (`integrations/blender/turnpad`) is installed from the SpaceMouse page of the settings app, and the commands of a linked application are listed by the D-Bus method `GetAppCommands`
- SolidCraft: a bridge in the driver (`integrations/bridges.py`, `integrations/solidcraft/`) links SolidCraft through its control port (`SOLIDCRAFT_CONTROL_PORT`, token-protected) like an add-on: dial steps and buttons run its toolbar commands by id and its view in 5° steps, rolls and standard views it has no keys for, and contexts follow its ribbon tab; a SolidCraft pack (basic, advanced, SpaceMouse buttons) works with its keys alone too; `GetStatus` reports the bridges ([SOLIDCRAFT.md](SOLIDCRAFT.md))
- Settings app for any computer: a first-run setup assistant detects the DialPad, touchpads, mice with a wheel, a SpaceMouse (spacenavd, USB) and its screen and suggests the modes (also from Driver › Set up devices again); the Mouse-only rows are a mouse picker and a "Press the key…" capture that reads the mouse's own events (side buttons, G-keys), the typed key name stays as the advanced entry; Calibration and SpaceMouse pages show only when the hardware can use them (Driver › Show all pages); *Export all settings* / *Import settings* move profiles, looks, gesture feel and SpaceMouse buttons between computers and keep each computer's own touchpad, devices and modes; a hint appears when a mouse key and a SpaceMouse both open the dial
- JSON configuration with per-application profiles, single-function and radial-menu modes, per-profile sensitivity, scroll speed and remembered last function
- GTK4/libadwaita settings application with function library, pre-programmed application packs, colours, ordering, import/export
- GNOME Shell overlay extension (45–51) with colours, value gauge, rotation hints and finger pointer; session D-Bus interface `io.github.asuslinuxdrivers.DialPad`
- Tap the centre for the function's own action, hold it to return to the menu
- Horizontal scrolling and scrolling with held modifiers (Ctrl + wheel zoom)
- Guided calibration that fits the ring and centre button from finger traces
- Skins for the on-screen dial (Dark glass, Midnight, Frost, ProArt bronze, Classic segments) and a skin editor with live preview, JSON import/export and an AI prompt; own skins live in `~/.config/asus-dialpad/skins/`

- Services run with `NoNewPrivileges=yes`, so nothing started from the dial can gain root (sudo, pkexec), and importing profiles lists their shell commands and asks first
- Launcher functions in the library (Calculator, Terminal, Files, Screenshot, Lock screen, My script): chosen in the radial menu, they run at once
- Skins choose the dial's shape (circle, square, octagon, knurled or wavy edge, or an outline drawn in the editor by dragging points on a polar grid), the shape and size of the function tiles and a background picture; the skin editor got clear entry points and an opacity slider per colour
- Brightness of the DialPad light: follows the keyboard backlight (its brightness key dims or switches off the DialPad light too) or a fixed level 0–8, where 0 keeps the light off
- Unattended installer (`bash install.sh`; `--interactive` for the original questions)
- On-screen dial position and visibility for all apps, per profile and per function: a custom place on the screen, fading while turning (to a chosen opacity) or a small label instead of the dial
- On-screen dial position "Near the pointer": the dial appears on a chosen side of the mouse pointer at a chosen distance, never over it, and stays put while visible (GNOME; the layer-shell dial on Hyprland, elsewhere it falls back to slightly below centre)
- Arch package (`bash packaging/arch/build.sh` + `asus-turnpad-setup`): system Python libraries instead of a pip environment, layout picked when the driver starts (`auto`), tested in CI; `INSTALL.md` explains both ways to install
- `install.sh` is tested on Fedora and Ubuntu in CI; it now installs the libraries the settings app and the pip builds need there, stops when pip fails instead of reporting success, and warns when Libadwaita is too old for the settings app
- Every application pack comes in a basic variant (a short menu) and an advanced one (more functions in groups, ordered by the stages of the work, with the dial kept out of the way: a small label for brush controls, fading for the view, the top of the screen for timelines); the app asks which when it adds a profile. New Files pack; the library got play/pause, microphone, overview, pages, up/down, copy, cut, paste, select all, find, save and full screen, and ready groups (Clipboard, Media controls, System, Navigation, Documents)
- Groups: a function of the radial menu can be a group whose functions open as a straight list beside the dial (turn to move, tap the centre to choose, hold to go back one level); the library has an *Apps* group with every launcher, the settings app edits a group with a live preview, and groups work in profile import, export and the AI prompt
- Guided calibration asks to keep or revert the measured geometry (it is live meanwhile, so the dial can be tried); the Calibration page can reset the geometry to the layout's values
- KDE Plasma 6: the focused application is reported by a KWin script (Plasma 6 dropped the D-Bus call the driver used), so per-application profiles switch there
- Mouse-only mode for computers without a touchpad (*Driver → Mouse-only mode*): a key from the mouse switches the dial (F13, e.g. a side button mapped with Piper), the wheel turns it (one notch = one step), the middle button is the centre (tap and hold like on the touchpad), a left click selects, a right click goes back, and optionally the direction the mouse moves is the dial's angle (move towards a tile, circle to turn); the driver grabs the mouse while the dial is on and looks for it again when it is unplugged
- Touchpad mode for touchpads without a DialPad (*Driver → Touchpad mode*, emulates the DialPad): any touchpad, no commands to its hardware; while on, the whole touchpad is the dial and the pointer stands still; switched by holding the top-right corner or with the new D-Bus method `Toggle` (a keyboard shortcut); `install.sh` no longer stops on such touchpads
- Functions can do something else while Ctrl, Shift or Alt is held on the keyboard (turning and tapping, per function; "Actions for" in the function editor)
- Saved settings reach the driver only between gestures; a `config.json` it cannot use is refused with the reason (Driver page, `GetStatus()`), and the running settings stay

### Removed

- Nix packaging (it still used the old names and installed neither the settings app nor the extension), the empty `laptop_dialpad_layouts` list and the VS Code launch file
- `numpy` and the hard requirement of `python-periphery` (only an I2C fallback)
- Anonymous installation reports and DSDT upload of the original installer

### Fixed

- Saving settings while turning could leave Ctrl held (zoom scrolling) on a replaced virtual keyboard, the replaced DialPad keyboard stayed until Python's garbage collector removed it, and the dial stopped for half a second while the new one was created
- A broken `config.json` made the driver fall back to the layout's shortcuts on the next reload
- Holding a tracked keyboard key (Ctrl, Shift, Alt) blocked every action without one
- Commands and shell scripts of dial actions blocked every further dial action until they ended (a calculator until it was closed), and programs started from the dial were killed with the driver service
- Centre actions of rotating functions were unreachable in the radial menu
- Switching application kept the previous application's selected function
- GUI configuration overridden after `dialpad_dev` reloads
- Socket overlay parsing of partial and nested messages
- Configuration written atomically

### Renamed to Turnpad (asus-turnpad), standard install layout

Run `bash install.sh` to update; it moves settings and removes the old install.

| | Before | Now |
| --- | --- | --- |
| Program | `/usr/share/asus-dialpad-driver`, owned by the user | `/opt/asus-turnpad`, owned by root |
| Settings, skins, driver state | `~/.config/asus-dialpad`, `dialpad_dev` in the program directory | `~/.config/asus-turnpad` |
| User services | `asus_dialpad_driver@$USER`, `asus_dialpad_driver_ui@$USER` | `asus-turnpad`, `asus-turnpad-ui` |
| Session D-Bus | `io.github.asuslinuxdrivers.DialPad` | `io.github.sinu0.AsusTurnpad.Driver` |
| Settings app id | `io.github.asuslinuxdrivers.DialPadControl` | `io.github.sinu0.AsusTurnpad` |
| GNOME extension | `dialpad@asus-linux-drivers` | `asus-turnpad@sinu0.github.io` |
| udev rules, modules | `/usr/lib/udev/rules.d/99-…`, two files in `/etc/modules-load.d` | `/etc/udev/rules.d/70-asus-turnpad-*.rules`, `/etc/modules-load.d/asus-turnpad.conf` |
| Legacy overlay socket | `/tmp/dialpad.sock` | `$XDG_RUNTIME_DIR/asus-turnpad.sock` |
| Logs | `/var/log/asus-dialpad-driver` | `/var/log/asus-turnpad` |

- The I2C rule now sorts before `73-seat-late.rules`, so its `uaccess` tag takes effect
- `uninstall.sh` is one script that also removes pre-rename leftovers and keeps your settings unless asked
- Supported desktops: GNOME; other desktops via a desktop support request

### Taken over from the original project (2.4.0–2.6.0)

The fork stops following the original driver here; these fixes were reviewed and ported once.

- DialPad backlight switched through the touchpad's `hidraw` node, I2C stays as a fallback (credits @lordyoyi, @ldrahnik); the node is now actually found from the touchpad's sysfs entry
- I2C tries `i2ctransfer` before python-periphery
- Separate user services for KDE Plasma Wayland that start with the Plasma session (fixes login crashes, credits @siddolo); `PATH` set in the services so `i2ctransfer` is found
- X11 libraries (`python-xlib`, `xcffib`, `xinput`) only installed and imported in X11 sessions (credits @Robin-Everaars)
- `pyinotify` without `asyncore`, which Python 3.12 removed
- Fedora Qt 6 `qdbus-qt6` detection (credits @pesoiq); no `qdbus` calls when it is missing
- `AltGr` and `Alt` modifiers on X11, active keyboard layout on Wayland (credits @bub4z0r), only key events count as modifier presses
- Events with a valid code but no known name (credits @md2z34)
- udev: `static_node` for uinput, `uaccess` for i2c-dev and hidraw (credits @Robin-Everaars)
- `PyGObject<3.52` and `xkbcommon<1.1` only pinned on systems that need them; the virtualenv sees system site packages (pyatspi)
- Settings of the legacy user interface written without `sudo`
- NixOS: `x11Support`, environment option, default config, `qttools` and `i2c-tools` in the path

## 2.3.0 (22.6.2026)

### Fixed

- Missing required dependency for `rpm-ostree` + added `allow inactive` + require reboot only when is needed (reported on `Fedora Silverblue`)
- Missing symlink for `wayland` and `xkbcommon` headers to `/usr/include` (reported on `OpenSUSE`)
- Default log level `DEBUG` decreased to `INFO`
- Reworked NixOS support (credits @toastal)
- Detection because missing `line`
- Name of `uinput` device variable
- Added missing `sudo` when removing `__pycache__`
- Re-loading `coactivator` keys on `Wayland` when was config not loaded yet and added re-loading on X11 completely

### Feature

- Added possibility to run `$ LOG=DEBUG bash install.sh`
- Added elimination of Stylus `9008` (fullname `ELAN9008:00 04F3:4631 Stylus`)
- Added DSDT probing script

## 2.2.1 (27.4.2026)

### Fixed

- Device detection during install process

## 2.2.0 (15.4.2026)

### Fixed

- Adding `uinput` group on BazziteOS
- Deprecated `xorg.xinput` to `xinput` for the next stable release of NixOS (credits @jalbstmeijer @MeeSumee)
- Default layout name and missing `INSTALL_DIR_PATH` when using `install_service.sh `independently without overriding from cmd
- Missing `SUDO_USER` on `qdbusSet` function
- Missing anti-repeating failure mechanism for `QDBUS`
- UI window from stealing focus on Wayland (credits @junjzhang)
- Duplication of `pywayland` dependency for NixOS

### Feature

- Added support for Wayland/X11 on Gnome using `pyatspi`
- Added support for wlroots-based Wayland compositors niri, sway and Hyprland (credits @junjzhang)
- Added support for immutable systems BazziteOS, Fedora Silverblue and Kinoite (credits @VictorPrado99)

## 2.1.1 (11.2.2026)

### Fixed

- Support for NixOS (credits @SamueleFacenda)
- Package names for `eopkg` / `yum` (credits @sHAKaJaada)

## 2.1.0 (28.1.2026)

### Fixed

- Previously added `i2c` group as not a system by recreating
- Missing dependency for NixOS `xcffib`
- Removed wayland dependency from the `requirements.txt` because existence of `requirements.wayland.txt`

### Feature

- Init `treshold` and possibility to see (only) progress in UI up to `treshold` using `socket_send_progress_above_treshold`
- Icons may be associated to `value` returned by command as alternation for sending keys

## 2.0.2 (16.1.2026)

### Fixed

- Added collecting of device_addresses (`0x38` or `0x15`) for kernel driver development purpose

## 2.0.1 (15.1.2026)

### Fixed

- Visual of pressing center button
- Shift of circle slices about 90 degree

## 2.0.0 (12.1.2026)

### Fixed

- Changed location of service to `$HOME/.config/systemd/user` (credits @s-badran)
- Fixed co-activator key selection for DialPad activation

### Feature

- Init of user interface including single and multi function mode

## 1.3.0 (3.1.2026)

### Fixed

- Detection of plasma environment (e.g. `plasmawayland` or `plasma-x11`)
- Missing support for `qdbus6`
- Plasma version detection using `kinfo`
- Running not under systemd service (when optional `systemd-python` pip package is not installed)
- When xauthority has in `tmp` folder multiple files
- Missing auto-installation of `qdbus` in supported distributions when using KDE Plasma

### Feature

- Co-activator key selection for DialPad activation
- By default DialPad automatically disable after 2 mins
- Support for `EV_REL` events with single event (opposite to release/press)
- Support for list of keys
- Example of scrolling

## 1.2.0 (16.12.2025)

### Fixed

- The package `smbus2` was replaced by `python-periphery` because has missing support for `python3.14` (yet) and `i2ctransfer` was added as alternative for `i2c` communication
- Nix `system` has been changed to `stdenv.hostPlatform.system` (credits @SamueleFacenda)
- Sending driver's version to GA
- `uinput`, `i2c`, `input` changed to a system groups (credits @vitaminace33)
- Setting up appropriate (not a static) `KERNEL` and `SUBSYSTEM` for `i2c` and `uinput` udev rules

### Feature

- Updated offline table for auto suggestions from gathered data (GA)

## 1.1.0 (8.10.2025)

### Fixed

- Missing python3 dependency when using `systemd`
- Installing `pip` package `pywayland` when is not required
- Layout `Asus Vivobook 16 x` dialpad coordinates (credits @cristianvasquez)

### Feature

- Added layout Zenbook Pro

## 1.0.0 (16.6.2025)

First release

## 0.0.1 (23.02.2025)

Init