# Turnpad — a dial for ASUS DialPads, any touchpad or a mouse

Turnpad turns the DialPad printed on the touchpad of ASUS laptops — or any other touchpad, or a mouse — into an ASUS Dial-style controller on Linux: a radial menu of functions, per-application profiles, an on-screen dial with skins and a settings app.

> **Based on [asus-linux-drivers/asus-dialpad-driver](https://github.com/asus-linux-drivers/asus-dialpad-driver)**
> by Lukáš Drahník (GPL-2.0). Turnpad started as a personal fork of it: the touchpad handling,
> layouts and installer come from that driver, and Turnpad adds features modelled on the Windows
> [ASUS Dial & Control Panel](https://www.asus.com/proart/software-solutions/asus-dial-and-control-panel/).
> It was written with substantial help from an AI assistant and has been tested
> only on a ProArt PX13 (HN7306WV) with GNOME 51 on Wayland. Expect rough edges.

What Turnpad adds to the original driver:

- **Settings app** (GTK4/libadwaita): per-application profiles, a function library, pre-programmed packs in a basic and an advanced variant (browsers, GIMP, Krita, Inkscape, darktable, Blender, Kdenlive, DaVinci Resolve, Shotcut, Audacity, OBS Studio, LibreOffice, code editors, file managers, Spotify, video players), ready groups, function colours and order, import/export.
- **ASUS Dial behaviour**: radial menu or single-function mode, per-app sensitivity and scroll speed, remembered last function, tap centre for the function's action, hold centre for the menu, horizontal scroll and Ctrl+wheel zoom.
- **Groups**: a sector that opens a straight list of more functions beside the dial; turn to move along it, tap the centre to choose. More functions fit without smaller sectors.
- **GNOME Shell overlay**: native radial dial with value gauge, rotation hints and finger position; size, position (anywhere on the screen or next to the mouse pointer) and visibility configurable: always visible, fading while you turn, or a small label instead of the dial — for all apps, per profile or per function.
- **Guided calibration** that measures the dial on any DialPad touchpad — see [PORTING.md](PORTING.md).
- **Three modes**: the dial works with the DialPad printed on an ASUS touchpad, with any other touchpad, or with just a mouse — see [Modes](#modes).

## Modes

| Mode | For | Switch on / off | Turn | Choose / centre | Back | Enable |
| --- | --- | --- | --- | --- | --- | --- |
| **DialPad mode** | ASUS laptop with the DialPad printed on the touchpad | hold the illuminated top-right corner | circle a finger on the printed ring | tap the centre button | hold the centre button | default; calibrate once on other models ([details](PORTING.md#running-the-dialpad-on-another-laptop-model)) |
| **Touchpad mode** | any other touchpad (also on laptops of other makes) | hold the touchpad's top-right corner, or a keyboard shortcut | circle a finger anywhere on the touchpad | tap the middle of the touchpad | hold the middle | *Driver → Touchpad mode* ([details](PORTING.md#touchpad-mode-touchpads-without-a-dialpad)) |
| **Mouse-only mode** | computer without a touchpad (desktop PC) | a key the mouse sends (F13, e.g. a side button mapped with Piper) | mouse wheel, or move the mouse in circles | left click, or the middle button | right click, or hold the middle button | *Driver → Mouse-only mode* ([details](PORTING.md#mouse-only-mode-computers-without-a-touchpad)) |

In Touchpad mode the whole touchpad is the dial and the pointer stands still while it is on; in Mouse-only mode the driver grabs the mouse while the dial is on. Everything else (profiles, functions, on-screen dial, skins) is the same in all three.

### Install

**Arch Linux, CachyOS, EndeavourOS, Manjaro** — as a package:

```bash
git clone https://github.com/sinu0/asus-turnpad.git
cd asus-turnpad
bash packaging/arch/build.sh
asus-turnpad-setup
```

**Other distributions** (Fedora, Ubuntu, Debian, openSUSE, …):

```bash
git clone https://github.com/sinu0/asus-turnpad.git
cd asus-turnpad
bash install.sh
```

Then log out and back in, switch the dial on (DialPad mode: hold the illuminated corner of the touchpad; for Touchpad mode and Mouse-only mode see [Modes](#modes)), and open **Turnpad** from the applications menu. On a laptop that is not listed, run *Calibration → Guided calibration* there once.

| | Arch package | `install.sh` |
| --- | --- | --- |
| Update | `git pull`, then `bash packaging/arch/build.sh` | `git pull`, then `bash install.sh` |
| Remove | `systemctl --user disable --now asus-turnpad`, then `sudo pacman -R asus-turnpad-git` | `bash uninstall.sh` |

Use one of the two, not both; to switch from `install.sh` to the package, run `bash uninstall.sh` first. Your settings in `~/.config/asus-turnpad` stay either way. Details, custom paths and immutable systems: [INSTALL.md](INSTALL.md). The original project's anonymous installation reports are removed; only for a laptop missing from the layout table does `install.sh` look up its touchpad id on linux-hardware.org.

Configuration lives in `~/.config/asus-turnpad/config.json`; the original `dialpad_dev` + `layouts/*.py` setup keeps working as a fallback. Updating from a version before the rename (`asus-dialpad-driver`) moves your settings over and removes the old files and services.

### Supported desktops

Turnpad currently supports **GNOME** (Shell 45–51). KDE Plasma 6, Hyprland, sway, niri and COSMIC get the same on-screen dial and skins too, drawn with gtk4-layer-shell (tested in CI on sway; see [INSTALL.md](INSTALL.md#the-on-screen-dial-on-your-desktop)). The driver itself also runs on other desktops; X11 sessions get the original PySide window. Support for another desktop starts with an issue: [request your desktop](https://github.com/sinu0/asus-turnpad/issues/new?template=desktop-support.yml). [ARCHITECTURE.md](ARCHITECTURE.md#adding-a-desktop-environment) describes what a port involves.

---

[![License: GPL v2](https://img.shields.io/badge/License-GPLv2-blue.svg)](https://www.gnu.org/licenses/old-licenses/gpl-2.0.en.html)

## Screenshots

**On-screen dial** (GNOME Shell): radial menu with colour-coded functions, a level gauge for volume and brightness, rotation hints and the finger position.

![On-screen dial](./preview/overlay-states.png)

**Settings app**: every function, shortcut, profile and skin is set up here, no config files to edit.

![Dial controls](./preview/settings-dial-controls.png)

**Skins can be silly too.** [Gertie the Dinosaur](https://en.wikipedia.org/wiki/Gertie_the_Dinosaur) (Winsor McCay, 1914, public domain) pokes her tongue out through a see-through centre on a spiky outline drawn point by point. It is one JSON file and one picture: copy both files from [`examples/skins/`](examples/skins/) to `~/.config/asus-turnpad/skins/` and pick “Gertie the Dinosaur” on the Skins page.

![Gertie the Dinosaur skin](./preview/overlay-example.png)

More pictures, with the built-in skins, dial shapes and every page of the settings app: [GALLERY.md](GALLERY.md).

How the parts fit together and how to support another desktop: [ARCHITECTURE.md](ARCHITECTURE.md).

The pictures are generated from the code: `python3 tools/update_previews.py` paints the dial with the extension's own drawing code (`tools/render_overlay_preview.js`), opens the settings app on a demo configuration and saves each page (`tools/screenshot_settings.py`), and replaces only the pictures that really changed. To check them before every push: `git config core.hooksPath tools/git-hooks`.

The sections below come from the original project and describe the base driver.

## Changelog

[CHANGELOG.md](CHANGELOG.md)

## Frequently Asked Questions

[FAQ](#faq)

## Features

Everything is set up in the **Turnpad** app (applications menu); changes apply immediately, no restart or file editing needed.

**Profiles**

- A default profile plus profiles per application, switched automatically with the focused window (desktop ID on GNOME, window title or binary elsewhere)
- Radial-menu mode with several functions, or single-function mode where the dial always controls one function
- Per-profile sensitivity, scroll speed and "remember last function"
- Pre-programmed packs, basic or advanced, for browsers, GIMP, Krita, Inkscape, darktable, Blender, Kdenlive, DaVinci Resolve, Shotcut, Audacity, OBS Studio, LibreOffice, code editors, file managers, Spotify and video players; import/export of profiles
- Launchers: a function with only a tap action runs as soon as it is chosen in the radial menu, like the radial menus of 3D mice. The library has Calculator, Terminal, Files, Screenshot, Lock screen and My script (any command or bash script); programs keep running without blocking the dial
- Groups (folders): a sector of the menu that opens a straight list of more functions beside the dial, one level deep. The library's *Apps* group holds all the launchers; *Group* under Custom starts an empty one, and functions already on the dial can be moved into it

**Functions**

- Library of ready functions: volume, brightness, keyboard light, mute, workspaces, vertical/horizontal scroll, zoom, tabs, back/forward, arrow keys, media tracks, seek, undo/redo, font size, brush size, frame stepping, timeline zoom
- Own colour, order on the dial, show/hide, rotation step and an optional live value (e.g. volume in % with a level gauge)
- Separate actions for turning clockwise, counter-clockwise and tapping; each action can be a keyboard shortcut (recorded from the keyboard, clicked together from key buttons or typed as `Ctrl+F12`; the ⓘ button in the action editor explains it), media key, volume, brightness, vertical or horizontal scroll (optionally with Ctrl/Shift/Alt held, e.g. Ctrl + wheel zoom), a shell command or a wait — several steps run in order
- Other actions while Ctrl, Shift or Alt is held on the keyboard, e.g. Volume: tap mutes, Ctrl + tap opens the mixer; Brush size: Shift + turn changes the hardness. Without its own action for a held key a function does what it always does

**Using the dial**

- Hold the illuminated corner icon to switch the DialPad on or off (optionally only together with a co-activator key)
- In the menu, touch a sector and release to choose a function; a function that only has a tap action runs straight away
- In a function, turn around the ring; tap the centre for the function's tap action (or to go back when it has none), hold the centre to return to the menu
- Choosing a group opens its list beside the dial, on the side of its sector: turning moves the highlight one function per step (like an iPod wheel), a tap on the centre chooses (a launcher runs and the list stays), holding the centre goes one level back — from a function of the group to its list, from the list to the menu
- Fast spins accelerate scrolling; the DialPad switches itself off after a period of inactivity

**On-screen dial**

- GNOME Shell 45–51: native overlay with colours, level gauge, rotation hints and finger position; size, position and accent colour are configurable
- Position: centre, slightly below centre, top, bottom, or a custom place picked on a screen miniature
- Visibility: *Always visible*; *Fade while turning* (full when the finger lands, after a second it fades to a chosen opacity, 0 % hides it) so the dial does not cover what you are changing; or *Small label instead of the dial* (only the function name and value)
- Position and visibility are defaults on the Skins page; every profile and every function can override them (folded *On-screen dial* row), e.g. a small label for brush size in Krita but the full dial in the menu
- Skins: Dark glass, Midnight (OLED black), Frost (light), ProArt bronze and Classic segments, optionally following the system light/dark style; the skin editor creates your own with a live preview, and its "Copy prompt for AI" / "Paste JSON" pair lets any AI assistant design one ([SKINS.md](SKINS.md))
- KDE Plasma 6, Hyprland, sway, niri, COSMIC: the same dial on the compositor's overlay layer (gtk4-layer-shell), with the same skins and settings
- X11 sessions: the original PySide overlay (own `.svg` icons; double-click to unlock its position and move it)

**Base driver** (from the original project)

- Runs as a user service, on Wayland and X11
- Automatic layout detection for known laptops, plus guided calibration for others ([PORTING.md](PORTING.md))

## Installation

See [Install](#install) above and [INSTALL.md](INSTALL.md).

## Layouts

Layouts below are named by laptop models, but the name is not important. What is important is their visual appearance because they are repeated on multiple laptop models across series. The install script should recognize the correct one automatically for your laptop. If yours was not recognized, please create issue.

| Name | Description                                                                                                  | Image                                                                                               |
| ------------ | ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| <a id="asusvivobook16x"></a><br><br><br><br><br>asusvivobook16x<br><br><br><br><br><br><br> | not nested                                                                | ![](images/Asus-Vivobook-16-x.png)                                             |
| <a id="proartp16"></a><br><br><br><br><br>proartp16<br><br><br><br><br><br><br> | nested                                                               | ![](images/Asus-ProArt-P16.png)   |
| <a id="zenbookpro14"></a><br><br><br><br><br>zenbookpro14<br><br><br><br><br><br><br> | nested                                                               | ![](images/Asus-Zenbook-Pro-14.jpg)   |


### FAQ ###

**How can DialPad be activated via CLI?**

Normally you hold the illuminated corner icon. From a terminal, change `enabled` in the driver's state file; the running driver picks it up:

```
# enabling DialPad via command line
sed -i "s/^enabled = 0/enabled = 1/" ~/.config/asus-turnpad/dialpad_dev
# disabling
sed -i "s/^enabled = 1/enabled = 0/" ~/.config/asus-turnpad/dialpad_dev
```

## Configuration

Use the **Turnpad** app for every setting:

| App page | What it configures |
| --- | --- |
| **Dial Controls** | Functions of the selected profile (add from the library, order, colour, icon, show/hide, actions for turning and tapping, other actions with Ctrl/Shift/Alt held, live value, on-screen dial position and visibility), dial mode, sensitivity, scroll speed, remembering the last function, building a profile with an AI assistant |
| **Apps & Profiles** | Default and application profiles, pre-programmed packs, duplicate/rename/remove, import and export, on-screen dial position and visibility per profile |
| **Skins** | Skin gallery and editor, separate skins for light and dark style, on-screen dial size, position (also a custom place), visibility (always, fade while turning to a chosen opacity, small label), visibility time, hints, finger position, progress arc and accent colour |
| **Behaviour** | Corner icon hold time, co-activator keys, switching off after inactivity, DialPad light (follows the keyboard backlight including off, or a fixed level 0–8 where 0 keeps it off), default rotation step, jitter filter, centre hold time, minimum menu sectors |
| **Calibration** | Guided calibration (trace the ring and the centre button, then keep or revert the result), manual dial geometry and corner icon area, reset to the layout's geometry |
| **Driver** | Driver status, active profile and function, the configuration the driver runs (and why it refused a newer one), reload, configuration folder |

**Building a profile with an AI assistant.** At the bottom of Dial Controls, *Copy prompt for AI* copies a description of the profile format together with the current profile. Paste it into ChatGPT, Claude or any other assistant, add what you want (“Inkscape: zoom, rotate canvas, layers, undo”), copy the whole answer and choose *Paste from AI*. The app reads the JSON out of the answer, accepts short key names such as `Ctrl`, explains what is wrong if something is not usable, lists every shell command before you accept, and then replaces the profile or adds it as a new one.

The layout (touchpad family) is chosen automatically during installation; to force one, run `LAYOUT_NAME=proartp16 bash install.sh` (or `bash install.sh --interactive`). Known layouts are listed in [Layouts](#layouts).

### Where the settings are stored

You do not need to edit these files; they are described for backups and troubleshooting.

| File | Content |
| --- | --- |
| `~/.config/asus-turnpad/config.json` | Everything set in the app: profiles, functions, behaviour, on-screen dial and dial geometry. Written atomically; the driver and the GNOME extension reload it after every change. The driver checks the file first and keeps its running settings when it cannot use it (the **Driver** page says why), and applies a change only between two touches of the dial. Use *Import/Export* to move profiles between computers. |
| `~/.config/asus-turnpad/dialpad_dev` | Runtime state of the driver: `enabled` (DialPad on/off) and `socket_enabled` (messages for the PySide overlay on other desktops; the GNOME overlay does not need it). Its other keys come from the original driver and are ignored while `config.json` exists. |
| `/opt/asus-turnpad/layouts/*.py` | Default geometry of each touchpad family and the original Python format of shortcuts. Used only until `config.json` exists — the first start of the app converts them. |

If `config.json` is deleted, the app recreates it from the layout on its next start.

### Security

- The driver runs as your user service, never as root. Commands and scripts of dial actions run with your rights only.
- The services set `NoNewPrivileges=yes`: nothing started from the dial can gain privileges, so `sudo`, `pkexec` and other setuid helpers fail there even with passwordless sudo. A dial script that needs root is better served by a narrow polkit rule or a system service of its own.
- Skins only describe looks (colours, shapes, a PNG next to the skin file); they cannot run anything.
- Importing profiles lists every command they would run and asks first, since shared profiles can contain shell commands.
- The program in `/opt/asus-turnpad` belongs to root, so other programs running as you cannot change the driver's code.

## Debugging

- The driver can be installed like `LOG=DEBUG bash install.sh` to extend the logs to a debug level (**recommended only for short-term debugging purpose**)

The **Driver** page of the app shows whether the driver is reachable, the focused application, the active profile and function.

Then check the service logs:

```
$ journalctl --user -u asus-turnpad.service
$ systemctl --user restart asus-turnpad.service
```

The driver state and the messages for the on-screen dial are available on the session D-Bus:

```
$ gdbus call --session --dest io.github.sinu0.AsusTurnpad.Driver --object-path /io/github/sinu0/AsusTurnpad/Driver --method io.github.sinu0.AsusTurnpad.Driver.GetStatus
$ gdbus monitor --session --dest io.github.sinu0.AsusTurnpad.Driver
```

GNOME extension errors (the extension reloads only after logging out and back in):

```
$ gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell --method org.gnome.Shell.Extensions.GetExtensionErrors asus-turnpad@sinu0.github.io
```

For further debugging stop the installed services:

```
systemctl --user stop asus-turnpad.service
systemctl --user stop asus-turnpad-ui.service
```

And listen on socket using `socat` (e.g. `$ sudo apt install socat`):

**Is necessary to have enabled in config `socket_enabled`.**

```
$ socat - UNIX-RECV:$XDG_RUNTIME_DIR/asus-turnpad.sock
{"ts": 1767791846.2734825, "enabled": true}
{"ts": 1767791846.3734825, "input": "center", "value": 1}
{"ts": 1767791846.3737416, "input": "center", "value": 0}
{"ts": 1767791846.461517, "input": "counterclockwise", "value": "62", "title": "Volume"}
{"ts": 1767791846.6073422, "input": "counterclockwise", "value": "58", "title": "Volume"}
{"ts": 1767791846.698195, "input": "counterclockwise", "value": "56", "title": "Volume"}
{"ts": 1767791846.814567, "input": "center", "value": 1}
{"ts": 1767791846.8146207, "input": "center", "value": 0}
{"ts": 1767791846.8976963, "input": "counterclockwise", "value": "54", "title": "Volume"}
{"ts": 1767791846.9604836, "input": "counterclockwise", "value": "52", "title": "Volume"}
{"ts": 1767791847.161212, "input": "counterclockwise", "value": "50", "title": "Volume"}
{"ts": 1767791847.3800304, "input": "clockwise", "value": "52", "title": "Volume"}
{"ts": 1767791847.5570402, "input": "clockwise", "value": "54", "title": "Volume"}
{"ts": 1767791847.6351395, "input": "center", "value": 1}
{"ts": 1767791847.6357095, "input": "center", "value": 0}
{"ts": 1767791847.7126362, "input": "clockwise", "value": "56", "title": "Volume"}
{"ts": 1767791847.789433, "input": "clockwise", "value": "58", "title": "Volume"}
{"ts": 1767791846.2734825, "enabled": false}
```

or use log level `DEBUG`:

```
$ cd /opt/asus-turnpad
$ LOG=DEBUG /usr/bin/python3 dialpad_ui.py
Listening on /run/user/1000/asus-turnpad.sock
2026-01-15 12:34:23,980 DEBUG {'ts': 1768476870.9540725, 'enabled': True}
2026-01-15 12:34:26,281 DEBUG {'ts': 1768476866.2752297, 'titles': ['Volume', 'Scroll', 'Brightness'], 'icons': ['/usr/share/icons/elementary/status/symbolic/audio-volume-medium-symbolic.svg', '/usr/share/icons/elementary/status/symbolic/rotation-allowed-symbolic.svg', '/usr/share/icons/elementary/status/symbolic/display-brightness-symbolic.svg'], 'title': None}
2026-01-15 12:34:27,481 DEBUG {'ts': 1768476867.4463465, 'enabled': False}
```

## Similar existing

- I do not know any

## Existing related projects

- [c++] Set of tools for handling ASUS Dial and similar designware hardware under Linux (https://github.com/fredaime/openwheel)
- [c++] Continuing development of the project above for the ASUS Dial (https://github.com/FrancisChung/asus-dial-driver)

**Why was this project created?** Because linux does not support integration of DialPad into a Touchpad

## Credits

The example skin uses a frame of *Gertie the Dinosaur* by Winsor McCay (1914, public domain), from [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Gertie_the_Dinosaur_with_her_mouth_open.jpg).

The driver this fork builds on — touchpad handling, layouts, installer and the original DialPad support — is the work of [Lukáš Drahník](https://github.com/ldrahnik) and the contributors of [asus-linux-drivers/asus-dialpad-driver](https://github.com/asus-linux-drivers/asus-dialpad-driver).
