# Architecture

Turnpad is a set of separately running parts that talk over the session D-Bus and share one JSON file. Only the outer layer depends on the desktop.

```
 touchpad (evdev) ──────┐
 mouse (evdev) ─────────┼─► dialpad.py ──────► uinput keys, scroll, media keys
 SpaceMouse (spacenavd)─┘    │   ▲   ▲   ▲
                             │   │   │   └── app link socket ◄── add-on in Blender
                             │   │                           ◄── bridges (integrations/) ◄─► SolidCraft control port
     focused window ─────────┘   │   └── config.json  ◄── config_app/ (settings, GTK4)
     desktop/active_window       │                         │
                          dialpad_dbus.py  ◄───────────────┘ ReloadConfig
                             │  OverlayState / OverlayDetails / DriverState signals
                             ├──────────────────────────────┐
                             ▼                              ▼
          on-screen dial frontend (per desktop)     screen/ (SpacePilot Pro, USB)
          · GNOME: gnome/extension/ (dial.js)
          · layer-shell compositors: overlay/layer_shell.py (painter.py)
          · X11: dialpad_ui.py (legacy window)
```

| Part | Desktop-specific? | Where |
|---|---|---|
| Touch input, gestures, actions | No | `dialpad.py`, `dial_gesture.py` |
| Mouse-only mode (the mouse as the dial) | No (evdev) | `mouse_dial.py` (pure state machines), `MouseSource` in `dialpad.py` |
| SpaceMouse mode | No | `spacemouse_dial.py` (pure: buttons, layers, cap axes, motion), `spacenav_client.py` (spacenavd's socket protocol, standard library only), `spacemouse_devices.py` (the device's physical keys and raw indexes), `SpaceMouseSource` in `dialpad.py`; the settings page is `config_app/spacemouse*.py` |
| Application link, profile offers | No (Unix socket, D-Bus) | `app_link.py` (the driver is the server; add-on for Blender in `integrations/blender/turnpad/`; bridges for applications with a control channel in `integrations/bridges.py`, SolidCraft in `integrations/solidcraft/`), `app_suggest.py`, `desktop/notifications.py` |
| Configuration model, presets, skins | No | `config/` |
| D-Bus service | No (session bus) | `dialpad_dbus.py` |
| Settings app | No (GTK4 runs everywhere) | `config_app/` |
| What hardware the settings app sees and offers (setup assistant, pages that fit, key capture, mice list) | No (reads `/proc` and sysfs) | `config_app/hardware.py` (pure, unit tested), `config_app/key_capture.py`, `config_app/device_dialogs.py` |
| Contexts, macro recorder, `--add-profile` | No | `config_app/contexts.py` + `context_dialogs.py`, `recorder.py` + `recorder_dialog.py`, `commandline.py` |
| Focused application | **Yes**, one backend each; KDE Plasma pushes it from a KWin script | `desktop/active_window.py`, `desktop/kwin_script.py` |
| Light/dark preference | No (XDG portal, gsettings fallback) | `desktop/color_scheme.py` |
| On-screen dial | **Yes**, one frontend each | `gnome/extension/`, `overlay/layer_shell.py`, `dialpad_ui.py` |
| What the dial shows, where and when | No (pure Python twins of the JavaScript) | `overlay/look.py`, `overlay/visibility.py` |
| Dial painting | No (Cairo) | `gnome/extension/dial.js`, `overlay/painter.py` |
| Installing the frontend | **Yes**, one case each | `install_overlay.sh` |
| SpaceMouse screen (SpacePilot Pro) | No (own process, USB) | `screen/` (`service.py`; pages in `pages.py` painted with Cairo, behaviour in `ui.py`, USB in `device.py`) |

## SpaceMouse and the application link

SpaceMouse mode reads the device through `spacenavd`: `spacenav_client.py` speaks its socket protocol (raw axes and buttons, which arrive before the daemon's dead zones and button map; a few requests change the cap's sensitivity for muting, per-application motion and hidden buttons). Whatever it changes is written to `~/.config/asus-turnpad/spacenav-restore.json` first and given back on exit and at the next start, so a crash leaves nothing behind. `spacemouse_dial.py` has no hardware imports: `ButtonMachine` turns raw button events into press and hold actions, `LayerState` keeps the active button layer, `SpaceCap` turns raw axes into a tilt angle, a twist and a push. `SpaceMouseSource` in `dialpad.py` runs them in the same input loop as the mouse (`listen_input_sources()`) and calls the state functions the touch loop uses, so a menu, a list or a function behaves the same with any input.

The application link (`app_link.py`, a thread of the driver) is a Unix socket in the runtime directory that add-ons connect to (today the Blender add-on). Applications without add-ons but with a control channel get a bridge instead: `integrations/bridges.py` runs the bridges on one more thread of the driver, and each joins the socket like an add-on through the shared client `integrations/applink/` (the Blender add-on's twin), turning `run` requests into the application's own requests (SolidCraft: JSON lines on its token-protected localhost port, `integrations/solidcraft/`). Add-ons send their command catalog and mode; the driver sends only `run` for an id from that catalog and never executes anything it receives. `GetAppCommands` and `DriverState` give the settings app and the screen the catalog and the mode; profile contexts (`select_profile_context()`) switch menus and buttons by window title or mode. `app_suggest.py` offers a ready profile for an application without one, as a notification from `desktop/notifications.py` whose button starts the settings app with `--add-profile`.

Programs that only follow or steer the driver (the screen service, a script) use the D-Bus signal `DriverState` and the `Control` method; the contract is in `AGENTS.md`.

## Names and identifiers

`config/identity.py` holds the display name and every technical identifier (D-Bus name and path, settings app id, config directory, extension UUID, install directory); `install_paths.sh` is its shell twin and also holds every installed path. Each identifier has twins in JavaScript, JSON and `.desktop` files; `tests/test_identity.py` fails when they drift apart. Changing a technical identifier breaks existing installs (their config directory and running extension), so it also needs a step in `install_migrate.sh`, like the one from the old `asus-dialpad-driver` names.

## Adding a desktop environment

Turnpad supports GNOME (Shell extension) and the Wayland compositors with layer-shell: KDE Plasma 6, Hyprland, sway, niri and COSMIC (`overlay/layer_shell.py`; sway is tested in CI, KWin by hand (it needs a GPU render node), the others are not). A new desktop starts with a [desktop support request](https://github.com/sinu0/asus-turnpad/issues/new?template=desktop-support.yml), ideally from someone who can test on it.

Three integration points, all optional. The driver already works everywhere evdev and uinput do.

### 1. Focused application

Add a function to `desktop/active_window.py`:

```python
# "example-ctl" stands for the compositor's own command-line tool.
@backend("example", ("wayland",))
def example(context: Context) -> WindowInfo:
    window = json.loads(_output(["example-ctl", "focused-window", "--json"]))
    return binary_from_pid(window["pid"]), window["title"]
```

It returns `(binary path, title)` or raises. Backends are tried in registration order and dropped after `max_failures` errors. A frontend that knows the focused app itself can push it instead, like the GNOME extension: call `SetActiveApplication(desktop_id)` on the D-Bus service. KDE Plasma 6 has no call that names the active window, so `desktop/kwin_script.py` writes a small KWin script to `$XDG_RUNTIME_DIR/asus-turnpad/kwin-active-window.js`, loads it through `org.kde.KWin /Scripting` (`unloadScript`, `loadScript`, `start`) when the driver's D-Bus service is up, and unloads it on exit (also when loading `start` fails or the driver stops meanwhile). It is for Plasma on Wayland with an `XDG_RUNTIME_DIR` only: on KDE X11 the `x11` polling backend answers (binary path), and a second source with desktop ids would fight it. The script calls `SetActiveApplication(window.desktopFileName || window.resourceClass)` on every activation and once at load.

### 2. On-screen dial

A frontend is a transparent, click-through window or layer above all others that does three things:

1. Subscribes to `OverlayState` and `OverlayDetails` (programs that follow the driver rather than draw its dial, like the SpaceMouse screen, also use `DriverState`, `GetDriverState()` and `Control()`) on `io.github.sinu0.AsusTurnpad.Driver`. Call `GetOverlayDetails()` once at start. The signal contract is in `AGENTS.md`.
2. Reads the `overlay` section of `~/.config/asus-turnpad/config.json` (scale, position, hide delay, hints, pointer, accent, skin, skin_light, follow_system_style) and reloads it when the file changes. Position `pointer` needs the pointer's position when the dial appears; `overlayPlacement()` / `overlay.look.overlay_placement()` take it and fall back to `lower` without it.
3. Paints with `overlay.painter.paint_dial()` (Python and Cairo; icons through `overlay.gtk_icons.draw_icon` in GTK) or with `gnome/extension/dial.js` (GJS). Skins then work without extra code.

On wlroots compositors, KDE Plasma and COSMIC, `gtk4-layer-shell` gives such a surface without stealing focus or appearing in Alt-Tab; `overlay/layer_shell.py` is that frontend (below). GNOME needs the Shell extension, because Mutter has no layer-shell.

Show the dial while `touching` is true and fade it out `hide_delay_ms` after release. With `follow_system_style`, pick `skin_light` when `desktop.color_scheme.ColorScheme().prefers_dark()` is false.

#### The layer-shell frontend

`overlay/layer_shell.py` (run with the system `python3`, as a script or `python3 -m overlay.layer_shell`) is the dial for every compositor with layer-shell: KDE Plasma 6, Hyprland, sway, niri, COSMIC. It restarts itself once with `gtk4-layer-shell` in `LD_PRELOAD` (the library must load before `libwayland-client`) and exits with status 3 where there is no layer-shell (GNOME, X11). `overlay/frontend.py` holds its GTK 4 windows: surfaces on the overlay layer (the dial, a group's list beside it and the pill), no keyboard interactivity, an empty input region so clicks reach the window below, placed against the whole output with `overlayPlacement()`'s margins. Wayland does not say where the pointer is, so the compositor chooses the output (the focused one); the dial is unmapped between touches, so each touch chooses again, and `--output NAME` pins it. For position `pointer` the frontend asks the compositor through `desktop/pointer.py` when a fully hidden dial shows (`Visibility`'s `follow_pointer`): Hyprland answers `cursorpos` on its IPC socket (global logical pixels; the frontend finds the output and pins both windows to it); sway, niri, KDE Plasma and COSMIC have no such query, so the dial shows at `lower` there. A new compositor needs one function in `pointer.BACKENDS`.

It reproduces the extension piece by piece, so both desktops show the same dial:

| Extension (JavaScript) | Layer-shell frontend (Python) |
|---|---|
| `overlayPlacement()`, `effectiveLook()`, `pillContent()`, `pillPlacement()`, `iconLayout()`, `pointerPlacement()`, `contentKey()`, and for a group's list `openSubmenu()`, `submenuSize()`, `submenuPlacement()`, `submenuLayout()`, `submenuKey()` in `dial.js` | `overlay/look.py` |
| appearance, `OverlayState` and `OverlayDetails` handling in `extension.js` | `overlay/look.py` (`load_appearance()`, `apply_overlay_state()`, `merge_details()`) |
| `_updateVisibility()`, `_show()`, `_hide()`, fade timers in `extension.js` | `overlay/visibility.py` |
| `paintBackground()`, `paintContent()`, `paintPointer()` and the icon actors | `paint_background()`, `paint_content()`, `paint_pointer()` in `overlay/painter.py` |
| `paintSubmenu()` and the list's icon actors; the list actor, a child of the dial (`_updateSubmenu()`, `_closeSubmenu()`) | `paint_live_submenu()` in `overlay/painter.py`; `GroupList` in `overlay/frontend.py`: a third layer surface on the dial's output whose opacity is the dial's times its own, so it shows, fades and hides with the dial, sliding out from it like the actor |
| `layers.js` | `DialLayers` in `overlay/frontend.py`: each layer keeps its picture as a Cairo image until its key changes |

`tests/test_overlay_look.py` runs the `dial.js` functions in gjs and compares them with `look.py`; `tests/test_live_painter.py` compares the painting with `dial.js` pixel by pixel. `tests/layer_shell_in_container.sh` runs the frontend on headless sway in a container with a fake driver on a private bus and compares screenshots with `dial.js` (also at output scale 2; a group's list beside the dial too), clicks and types through the dial and checks hiding (the list with the dial), the pill and the fade. `kwin` instead of `sway` runs it on headless KWin (`kwin_wayland --virtual`; screenshots through `org.kde.KWin.ScreenShot2`, clicks and keys through fake input, `tests/kwin_fake_input.py`); KWin 6.8 needs a DRM render node even headless, so it runs by hand with `podman run --device /dev/dri/renderD128`, not in CI.

### 3. Installer

Add a case to `install_overlay.sh` that sources an `install_<desktop>_overlay.sh`, and the matching removal to `uninstall.sh`. Distribution packages (`packaging/`) install the frontend's files system-wide, and `packaging/asus-turnpad-setup` enables it per user.

Today `install_overlay.sh` has three cases:

| Session | Frontend | Installed by | Unit |
|---|---|---|---|
| GNOME | Shell extension | `install_gnome_extension.sh`; packages put it in `/usr/share/gnome-shell/extensions` | none (GNOME Shell loads it) |
| Any other Wayland session (KDE Plasma 6, Hyprland, sway, niri, COSMIC) | `overlay/layer_shell.py` | `install_layer_shell_overlay.sh`: optional packages `layershell` from `install_packages.sh`, then the unit, only when `Gtk4LayerShell` imports | `asus-turnpad-overlay.service` (template in the repository root; `packaging/asus-turnpad-overlay.service` for the packages) |
| X11 | `dialpad_ui.py` | `install_user_interface.sh` (PySide6) | `asus-turnpad-ui.service` from `asus-turnpad-ui.x11.service` |

The layer-shell dial's unit belongs to `graphical-session.target` (the session must also hand `WAYLAND_DISPLAY` to the user's service manager) and is not restarted after exit status 3, so a unit enabled for a user who also logs in to GNOME or X11 stays quiet there. A compositor without layer-shell needs a frontend of its own; one with layer-shell should work with the existing one, and adding it to the CI job `layer-shell-dial` needs a class next to `Sway` in `tests/layer_shell_check.py` (start headless, screenshot, click, type) and a case in `tests/layer_shell_in_container.sh`.

## Keeping the two painters in sync

`gnome/extension/dial.js` (GJS, inside GNOME Shell) and `overlay/painter.py` (Python, everywhere else and in the settings app) draw the same dial. Change both together, then compare the results:

```
python3 tools/update_previews.py   # preview/overlay-*.png from dial.js, settings pages (dial from painter.py)
```

Skin resolution has the same pairing: `resolveSkin()` in `dial.js` and `resolve_skin()` in `config/skins.py`.

## Rendering cost

The dial has to stay smooth on battery without measurable CPU or GPU use. The arithmetic per driver signal (a sine and cosine for the finger dot, comparing the shown state) takes microseconds; what costs is rasterising with Cairo. A full `paintDial()` takes about 6 ms per frame at 2x pixel density (GNOME renders 150 % scaling at 2x), mostly the shadow and gradients that never change while the dial is shown.

The GNOME extension therefore paints in three layers that the compositor puts together (`gnome/extension/layers.js`, described at the top of the file):

| Layer | Painted when | Cost |
|---|---|---|
| Background: shadow, disc, rim | skin, scale or size changes | once |
| Content: menu or gauge, centre, hints | `contentKey()` changes (value, function, menu) | about 0.6 ms; the centre disc is a cached image |
| Finger dot | its colour changes | moving it paints nothing |

A group's list is one more actor beside the dial, painted by `paintSubmenu()` only when the list, its highlight or the skin changes; it is a child of the dial, so it fades and hides with it.

The layer-shell frontend has the same three layers (`DialLayers` in `overlay/frontend.py`) and repaints its list window only when `submenuKey()`, the list's scale or its size change. The overlay actor (or layer surface) is only as large as the dial, so its fade does not composite the whole screen. The driver sends the finger angle in `POINTER_STEP_DEGREES` steps (at most once per touch frame). `paintDial()` remains the one-call version for the README pictures and produces the same pixels.

## SpaceMouse screen

`screen/service.py` is a separate user service (`asus-turnpad-screen.service`, started by udev when a SpacePilot Pro is plugged in) that draws Turnpad on the device's 320×240 colour screen. It reads the driver like the layer-shell dial does (`OverlayState`, `OverlayDetails`, `DriverState`; `Control` carries what the screen's OK, Up and Down keys do), watches `config.json`, listens to spacenavd as one more client (raw buttons and axes; it changes nothing there) and writes frames through pyusb (`screen/device.py`, interface 0 only). Painting (`pages.py`) and behaviour (`ui.py`, `content.py`) are pure, so the settings app renders the same pages with `screen.preview.render()`. It works without the driver (configuration only). Exit status 3 means nothing to do (switched off, no pyusb, no screen); the unit is not restarted then.
