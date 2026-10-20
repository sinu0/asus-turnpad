# SpaceMouse and SpacePilot Pro

Turnpad works with 3Dconnexion SpaceMouse devices on Linux through `spacenavd`. It is one more client of the daemon, so it runs next to your 3D or CAD program and needs no vendor software; think of it as a replacement for 3DxWare that also gives the device a dial, button maps per application and, on the SpacePilot Pro, a use for its screen.

It was written and tested with a SpacePilot Pro (USB `046d:c629`), spacenavd 1.3.1, KDE Plasma 6 on Wayland and Blender 5.2. The physical keys and the screen are known only for that model. Another 3Dconnexion device is handled generically: one entry per button, no long presses, no screen.

## What you need

- **`spacenavd` 1.3 or newer, running.** AUR `spacenavd` on Arch, a package of the same name on most other distributions. Check with `systemctl status spacenavd`. Turnpad finds its socket by itself (see [Troubleshooting](#troubleshooting)).
- **Turnpad's driver running** as a user service, as for every mode.
- **No DialPad or Touchpad mode on the same computer.** SpaceMouse mode runs alone or together with Mouse-only mode. With a DialPad (the driver takes any ASUS-named touchpad, ELAN ones included, for one) or Touchpad mode it is ignored with a warning in the log.
- **`pyusb`, optional**, only for the SpacePilot Pro's screen (`python-pyusb` on Arch, `python3-usb` on Debian and Ubuntu, `python3-pyusb` on Fedora and openSUSE). See [INSTALL.md](INSTALL.md#the-screen-of-a-spacepilot-pro).

Open the settings app (**Turnpad**). On the first start the setup assistant offers SpaceMouse mode when it finds `spacenavd`'s socket or a SpaceMouse on USB; later, Driver → *Set up…* does it again. Or open the *SpaceMouse* page and switch the mode on. The driver restarts when the mode changes. The *SpaceMouse* page is hidden while there is no device, no `spacenavd` and the mode is off; Driver → *Show all pages* shows it anyway.

## How it works

Turnpad reads the cap's raw axes and the raw buttons, which `spacenavd` broadcasts before its own dead zones, sensitivity and button map, so its settings do not get in the way.

- **Open the dial** with the Menu button (default). The on-screen dial appears as usual.
- **Tilt** the cap towards a sector to highlight it. Away from you is the top of the dial, right is 90 degrees.
- **Release to select**: keep Menu held, tilt towards a tile and let go of the button; the function is chosen. Let go without tilting and the menu stays open: tilt, then push the cap (or press Menu) to choose, or press Menu again to close.
- **Twist** turns the dial: in the menu one step per sector, in a list one step per item, in a function like the touchpad ring (threshold, sensitivity and scroll speed of the profile apply).
- **Push the cap down** is the centre button: tap to choose or to run the function's tap action, hold to go back.
- **Esc** goes back one level.
- In a **group's list**, tilting forward or back steps through the items and repeats while held, faster at full tilt.
- Tilt and twist ignore each other when one is clearly stronger, so a twist does not move the highlight.
- **Function keys** enter functions directly, without the menu: keys 1–5 (short press) enter the first five functions of the profile, held for about a second the next five. A function that only has a tap action (a launcher) runs at once.

A function entered this way behaves as in every other mode; leave it with Esc or by holding the push.

## The default map of the SpacePilot Pro

Without a configured map (or for buttons you have not changed) the buttons do this:

| Button | Short press | Long press |
| --- | --- | --- |
| Menu | Switch the dial on or off | |
| Function keys 1–5 | Functions 1–5 of the profile | Functions 6–10 |
| Esc | Back | |
| Alt, Shift, Ctrl | Held while the button is down | |
| Rotation | Switch the cap's rotation off or on, for other programs | |
| Pan/Zoom | Switch pan and zoom off or on, for other programs | |
| + and − | Raise or lower the cap's sensitivity, for other programs | |
| T, R, F, Roll, ISO, Fit, Dom | Left to other programs | |

The last row means Turnpad does not bind them, so the application sees them as usual. A profile can bind any of them for its application.

### Dual-function keys

The function keys, T, R, F, Roll and ISO are dual-function keys: the device itself decides between a short and a long press (about a second) and sends a different button number for each. A short press sends its number with press and release together at the release; a long press sends another number, down while held. So each of the two is a button of its own with a press action and no hold action. The settings app shows such a key once and lets you edit its short and its long press. The other buttons have a press action and a hold action (after `hold_ms`, 400 ms by default).

## Binding buttons

On the *SpaceMouse* page, *Applies to* chooses where you edit: all applications, one profile, or one context of a profile. The keys are listed as on the device, grouped as function keys, modifier keys, view keys, motion keys and system keys. Pick a key and choose its press and hold action. A button without a hold action acts the moment it is pressed. Each key can have a *Label on the screen*, up to 40 characters, which the SpacePilot Pro's screen shows (empty = automatic).

| Action | What it does |
| --- | --- |
| Shortcut / macro | Keys or a list of steps (keys, text, scroll, wait, shell command) |
| Modifier | Holds Ctrl, Shift or Alt while the button is down |
| Hold keys | Holds any keys while the button is down, as the press action or, after `hold_ms`, as the hold action. Blender pie menus: hold `Z`, tilt, let go. A key two buttons hold is released with the last one |
| Dial | Switch the dial on or off |
| Group | Open a group's list of the current profile |
| Function | Enter a function by name or by its place in the menu |
| Select, Back | The dial's centre tap and back |
| Switch layer | Activate a [layer](#layers) |
| Cap motion | Rotation, Pan/Zoom, Faster, Slower or Reset (see [Cap motion](#cap-motion-per-application)) |
| App command | A command of the focused application through the [app link](#app-link-and-the-blender-add-on), with fallback keys |
| None | Leave the button to the application |

Dial, group, function, select and back are the dial's gestures: pressing one while the menu waits for its opening button ends that wait. A button that only holds keys, or is left to other programs, does not.

### Per application

A profile can bind buttons itself. Its entry for a button replaces the global binding of that button; the others stay as they are. `None` in a profile beats a global binding: use it to give a button back to one application. A profile with no functions but with buttons keeps the default profile's dial menu and applies its buttons. The packs below come with ready maps.

### Other programs: `mute_others` and `hide_buttons`

- **`mute_others`** (on): while the dial is on, Turnpad sets `spacenavd`'s sensitivity to 0 so a CAD program stands still when you steer the dial, and gives it back when the dial is off. A motion that was already running at that moment may continue until the cap moves again, so switch the dial on with the cap at rest.
- **`hide_buttons`** (off, experimental): the buttons Turnpad binds in the focused application's profile (and its layers) are hidden from other programs; buttons set to `none` pass through. It follows the focus and gives the buttons back when you switch. A button held down at that moment changes when it is let go. Turn it on when a button of the dial, such as Menu, is also bound in the 3D program.

Both put things back on exit and, if the driver was killed, at the next start, from `~/.config/asus-turnpad/spacenav-restore.json`.

## Layers

A layer is a second button map that lies over the base map while the layer is active. A button bound in the layer wins; the others keep their base binding. Bind *Switch layer* to a button and choose the mode:

- **Hold**: active while the button is down; chords work.
- **Once** (latch): the next press of any other button uses the layer, then it ends. Pressing the layer's button again cancels.
- **Toggle**: active until its button is pressed again.

Layers can be global or per profile (a profile also sees the global ones and adds its own entries to them). On the *SpaceMouse* page the layer selector under *Applies to* switches the key list between the base and each layer; an entry that the layer does not set is shown dimmed, "from base". Latches and toggles also end when the focused profile changes or the device disconnects. The SpacePilot Pro's screen shows the active layer.

## Cap motion per application

This is the cap's motion for *other* programs (the 3D view), not for the dial. Turnpad changes `spacenavd`'s own settings while a profile is in force and gives them back when you leave it. The values are factors on what `spacenavd` is configured with: the defaults change nothing and send nothing.

- **Sensitivity** 0.1–4 (1 = as configured), per-axis sensitivity for X, Y, Z, RX, RY, RZ (0 locks an axis), inverted axes, and rotation or translation switched off.
- Set globally on the *SpaceMouse* page › *Cap motion*, or per profile with *Use the global settings* switched off (the override is a complete set).
- **Buttons** (*Cap motion* action) change it for the running session only: Rotation and Pan/Zoom switch a part off and on, + and − step the sensitivity, Reset undoes it. The state is kept per application until the driver restarts and is not saved.
- The state of the focused application shows on the page and on the screen's Motion page.

`spacenavd`'s settings are global for every client and Turnpad never saves them in the daemon. The original values are written to the restore file before the first change and given back on exit or at the next start. Do not save `spacenavd`'s configuration with `spnavcfg` while a profile's motion is in force or the dial is on: it would keep the changed values.

## The SpacePilot Pro's screen

The 320×240 colour screen is driven by the user service `asus-turnpad-screen.service`. A udev rule starts it when the device is plugged in; there is nothing to enable. It follows the driver (profile, dial, layer, application) and shows Turnpad's state. Settings are on the *Screen* page of the settings app, which shows while a screen is present: a live preview, the switch, look (theme, text size, long-press labels, 24-hour clock), power (brightness, dim after, off after, wake on motion), pages (order, which are shown, start page, follow the dial) and per-application start page, accent and labels.

| Page | Shows |
| --- | --- |
| Keys | Function keys 1–5: the action of the short press and of the long press |
| Dial | The live dial, steered with the screen's keys |
| All buttons | Every button of the active application; the application's own are dimmed |
| Motion | Live cap: six axes, tilt and twist, speed and the rotation and pan state |
| Clock | Clock and status (device, app link) |

![Keys page](./preview/screen-keys.png)
![Dial page](./preview/screen-dial.png)
![All buttons](./preview/screen-device.png)
![Motion](./preview/screen-motion.png)
![Clock](./preview/screen-clock.png)

The top bar shows chips for the active layer, held keys, the context, the application's mode and a changed motion state; an active layer also draws an accent line under it. A toast shows the answer to a key press and driver messages (a command that could not run).

### The keys around the screen

| Key | Does |
| --- | --- |
| Left / Right | Previous or next page |
| Up / Down | Scroll the *All buttons* page; move along the *Keys* page; turn the dial one step on the *Dial* page |
| OK | On *Keys*, run the highlighted button's press action; on *Dial*, choose (or switch the dial on) |
| Back | Close an overlay; on *Dial*, go back in the dial; otherwise to the start page |
| Menu | The page picker |
| Settings | Quick settings: brightness, dim after, theme, text size, long-press labels, follow the dial, start page, press feedback (Left/Right change a value) |
| Light | Steps the backlight through 100, 70, 40, 15 % and off |

Up and Down repeat while held. The screen dims and switches off after the times you set; the first key press only wakes it. Quick settings are saved to `config.json` by the screen service itself (the settings app picks the change up). The screen service is a second `spacenavd` client for press feedback and the motion page; it never changes the daemon.

## App link and the Blender add-on

Applications can tell the driver what commands they have and what the user is doing. The driver listens on a Unix socket in the runtime directory (`$XDG_RUNTIME_DIR/asus-turnpad-apps.sock`, mode 0600, checked against the user). **Nothing received over it is executed by the driver**: it sends an application a command id only when the id is in the catalog that the same application sent, and the application decides what the id means. A command that cannot run falls back to the step's keys; without keys the driver logs it and shows it as a notice on the screen.

The **Blender add-on** (`integrations/blender/turnpad`, Blender 4.2 or newer) gives Blender a curated list of commands (view, selection, modes, pie menus; no code and no operator names cross the socket) and reports the mode (Object, Edit Mesh, Sculpt, …) and, in Edit Mesh, the select mode.

- **Install** from the settings app: *SpaceMouse* page › *Blender* › *Install the Blender add-on*. It builds the extension with `blender --command extension build` and installs it with `blender --command extension install-file`. Without Blender on the path, or when a command fails, it writes the zip and shows the manual steps (Edit › Preferences › Get Extensions › Install from Disk). Then enable *Turnpad* in Blender.
- **Use**: the *App command* action of a SpaceMouse button picks a command from the add-on's list, grouped by category, with Blender's default keys as the fallback (macro steps of type `app` in a profile file do the same). With Blender closed or not linked the keys are pressed instead.
- **Modes**: the mode becomes a condition of a [context](#contexts), so the dial menu and the buttons follow what you are doing in Blender.

### Blender 5.2 notes

- Blender binds the NDOF Menu button (it opens its 3D-mouse panel), Fit, the view axes, Roll and +/−. It leaves ISO, buttons 1–12 and V1–V3 unbound. When Menu is the dial, turn `hide_buttons` on.
- In the Blender profile, set Esc and Ctrl to `none`: Blender reads them itself.
- Pie menus: bind *Hold keys* with `Z` (or the key of the pie) to a button, press it, tilt with the cap or the mouse, let go.
- Blender's own NDOF settings (Preferences › Navigation) apply on top of what `spacenavd` sends; Turnpad's *Cap motion* changes what `spacenavd` sends.

## Contexts

A profile can have contexts: variants of the profile for a situation. A context applies when every condition you give matches:

- **Window title contains**: plain text, case ignored, `|` between alternatives (`Edit|Sketch`). It is not a regular expression. The settings app tests it against a title you type.
- **Application mode**: one or more modes reported by the application's add-on (Blender: Edit Mesh, Sculpt, …).

The first matching context wins; none matching leaves the plain profile. A context can have its own **dial menu**, its own **SpaceMouse buttons and layers** (laid over the profile's) and its own **screen start page**. Edit them on the Dial Controls page (*Contexts*) and, for buttons, under *Applies to* on the *SpaceMouse* page, where each context is listed after its profile. The window title comes from the desktop (the GNOME extension, a KWin script on Plasma Wayland, or polling elsewhere).

## Ready maps in the packs

When you add one of these applications on the *Apps & Profiles* page, the pack comes with SpaceMouse buttons: the five function keys short and long press, Fit and the view keys where the application has shortcuts, labels for the screen. The *advanced* variant has maps for more sectors. Shortcuts are the applications' defaults.

| Application | Notes |
| --- | --- |
| GIMP, Krita, Inkscape, darktable | The application's shortcuts on the function keys, Fit and the view keys where it has them |
| Blender | View keys, Fit, Roll, Esc, Alt/Shift/Ctrl and +/− are left to Blender (`none`); a long press can hold a pie key |
| FreeCAD | Its own 3Dconnexion button map is empty until you load one in its Customize dialog, so the view keys press FreeCAD's view shortcuts (1–6, 0, V, F) |
| KiCad | PCB editor; no view keys |
| Kdenlive, DaVinci Resolve | Timeline shortcuts (Resolve's from third-party sources only) |

When you focus an application that has no profile but has a pack, a notification offers *Create profile* (once per application). It opens the settings app on that application.

## Scripting

Another program can steer the dial through the driver's D-Bus method `Control` (for example `button:12`, `function:2`, `rotate:1`, `select`, `back`, `dial-on`, `mute:2000`); `DriverState` is a signal with the current profile, layer, motion and more. Both are described in [AGENTS.md](AGENTS.md#d-bus-contract).

## Troubleshooting

- **No SpaceMouse found**: the page shows the device and the socket. Turnpad looks for `spacenavd`'s socket in `$SPNAV_SOCKET`, `socket =` in `/etc/spnavrc`, `$XDG_RUNTIME_DIR/spnav.sock` and `/var/run/spnav.sock`; set `spacemouse.socket` in `config.json` to use another. Check that `spacenavd` runs and sees the device (`spnav_ex` or `spacenavd -d`).
- **Nothing happens when the dial is on, or tilt is wrong**: the axis defaults are measured on a SpacePilot Pro. Another model may need other `tilt_axes`, `twist_axis` or `*_invert` values in the `spacemouse` section of `config.json`.
- **The mode does not start**: a DialPad, an ELAN or ASUS-named touchpad or Touchpad mode on the same computer makes the driver ignore it (see the log: `journalctl --user -u asus-turnpad.service`). After changing the mode the driver needs a restart; the settings app does it.
- **Other programs stay still or buttons stopped working after a crash**: the driver may have left `spacenavd` muted (sensitivity 0), with changed motion or with remapped buttons (index 63). The next driver start gives everything back from `~/.config/asus-turnpad/spacenav-restore.json`, even with the mode off. Restarting the driver (`systemctl --user restart asus-turnpad.service`) is enough. Also do not save `spacenavd`'s configuration in `spnavcfg` while the dial is on.
- **The screen stays dark**: check `systemctl --user status asus-turnpad-screen.service` and `journalctl --user -u asus-turnpad-screen.service`. The service stops quietly (exit 3) when `pyusb` is missing, no screen is found or the screen is switched off in the settings; the *Screen* page shows which. The udev rule that starts it and gives your session access to the device (`uaccess`) comes with the installer and the packages; after installing it, unplug and plug the device back in or log in again.
- **Settings in `config.json`**: the `spacemouse` and `screen` sections hold everything the page shows, `buttons` and `layers` the maps. Export all settings (Driver page) moves the portable parts, such as buttons, layers, motion and hold time, to another computer; the device, socket and raw axes stay on each machine.

See also [PORTING.md](PORTING.md#spacemouse-mode-a-3dconnexion-spacemouse) for the settings and protocol details, and [GALLERY.md](GALLERY.md) for pictures.
