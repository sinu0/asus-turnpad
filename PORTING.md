# Running the DialPad on another laptop model

Turnpad has four modes (the table is in the [README](README.md#modes)):
**DialPad mode** for an ASUS laptop with the DialPad printed on the touchpad
(this page: another laptop model, calibration), **Touchpad mode** for any
other touchpad (below) and **Mouse-only mode** for a computer without a
touchpad, where a mouse is the dial (below), and **SpaceMouse mode**, where a
3Dconnexion SpaceMouse is the dial (last section).

The driver detects ASUS touchpads by name (`ASUE`, `ELAN`, `ASUP`, `ASUF`,
`ASCE`, `ASCF`, `ASCP` + `Touchpad` in `/proc/bus/input/devices`). What
differs between models is the dial geometry and, rarely, the I2C address of
the corner LED. Both can be handled without code changes:

1. **Install** as described in the [README](README.md#install). The layout
   (`proartp16`, `asusvivobook16x` or `zenbookpro14`) is picked from
   `laptop_touchpad_dialpad_layouts.csv` by laptop name or touchpad id; an
   unknown laptop gets `proartp16` (`bash install.sh --interactive` lets you
   choose).
2. **Calibrate.** Open *Turnpad → Calibration → Guided calibration*,
   press *Start*, trace the printed outer ring once with one finger, lift it,
   then trace the edge of the centre button. The measured geometry is used
   right away so you can try the dial; press *Keep* to save it to
   `~/.config/asus-turnpad/config.json` together with the layout name, or
   *Revert* to go back (closing the app without choosing also reverts).
   *Reset geometry* at the bottom of the page restores the values of the
   layout file.
3. **Corner icon.** If holding the illuminated corner does not toggle the
   DialPad, adjust the activation area (width / height, top-right corner) on
   the same page.
4. **Overlay.** On GNOME 45–51 the Shell extension draws the dial (tested on
   GNOME 51 only). Other desktops use the original PySide overlay service
   `asus-turnpad-ui.service`.

If the corner LED does not light up, the touchpad probably needs the other
I2C address: the driver uses `0x38` for `ASUF1416`, `ASUF1205` and
`ASUF1204` and `0x15` otherwise (see `device_addr` in `dialpad.py`).

## Touchpad mode: touchpads without a DialPad

Any other touchpad (Synaptics, ELAN, Apple and others, also on laptops of
other makes) can emulate the DialPad: open *Turnpad → Driver* and switch on
**Touchpad mode**; the app restarts the driver. Then:

- **Switching on and off:** hold the top-right corner of the touchpad (about
  1.5 cm, the hold time and co-activator keys of the *Behaviour* page apply),
  or use a keyboard shortcut: *Keyboard shortcut → copy* puts the command in
  the clipboard; add it as a custom shortcut in GNOME Settings (*Keyboard →
  View and Customise Shortcuts → Custom Shortcuts*). The corner can be
  switched off so that only the shortcut switches. After the inactivity time
  of the *Behaviour* page the dial switches off by itself.
- **While the dial is on** the whole touchpad is the dial: the pointer stands
  still, a touch near the middle is the centre button (its size is set on the
  *Driver* page), anywhere else is the ring. The on-screen dial shows where
  you are, so keep its visibility at *Always visible* or *Fade*.
- Nothing is sent to the touchpad (there is no light and nothing to unlock),
  and calibration is not needed.

`install.sh` continues on a touchpad without a DialPad. The driver starts
with the dial switched off, and gives the pointer back when it stops.

## Mouse-only mode: computers without a touchpad

On a desktop PC a mouse can be the dial: open *Turnpad → Driver* and switch
on **Mouse-only mode** (the first-run assistant, or Driver → *Set up…*, does
this for you when it finds a mouse); the app restarts the driver (it then needs
no touchpad). Pick the mouse in the *Mouse* list and press *Press the key…* to
set the toggle key by pressing the mouse button; the typed key name is the
advanced way. While the dial is on, the driver grabs the mouse: the pointer and
the clicks stand still and the wheel does not scroll the window.

- **Switching on and off:** a key the mouse sends. Map a side button of the
  mouse to **F13** (Logitech G mice: Piper; others: the manufacturer's
  software or the mouse's onboard memory); F13 is the default *Toggle key*. A
  button that already sends a key such as `3` works too (enter `KEY_3`), but
  that key still types in the focused window when the dial is switched on,
  because the mouse is grabbed only after it. A mouse button name such as
  `BTN_SIDE` is accepted as well. The D-Bus method `Toggle` and the
  inactivity time of the *Behaviour* page switch the dial like everywhere else.
- **Wheel:** down turns the dial clockwise, up counterclockwise, one notch is
  one step (one sector of the menu, one item of a list, one step of the
  selected function).
- **Left click:** choose, the same as a tap on the centre button.
- **Right click:** back, out of a function or a list (nothing in the plain
  menu).
- **Middle button:** the centre button. Tap it to choose or to run the
  function's own centre action; hold it (the hold time of the *Behaviour*
  page) to go back.
- **Moving the mouse** (optional, *Move the mouse around the dial*): the
  direction you move the mouse is the dial's angle, like a finger on the ring
  (up = top, right = 90 degrees). In the menu, move towards the tile you want
  to highlight it, then click. In a list or a function, move the mouse in
  circles to turn the dial: one lap of the hand is one turn of the dial,
  whatever the size of the circle, clockwise on the desk is clockwise on the
  dial. Reversing a straight stroke does not turn anything. Nothing happens
  until the mouse has travelled 35 % of *Size of the movement* (mouse counts;
  a smaller size reacts to shorter strokes). It starts afresh each time the
  dial is switched on and after you enter or leave a function or a list. The
  wheel keeps working.
- **Which mouse:** by default the first input device that has the toggle key
  and a wheel. To pick one, enter a part of its name (`grep -A1 Name
  /proc/bus/input/devices`), e.g. `G604`. A mouse that is not plugged in
  yet (a wireless receiver) is looked for every 2 seconds.

Nothing is sent to a touchpad and calibration is not available. If Touchpad
mode is switched on as well, Mouse-only mode wins.

## SpaceMouse mode: a 3Dconnexion SpaceMouse

This is the short reference; [SPACEMOUSE.md](SPACEMOUSE.md) is the full guide
(setup, buttons, layers, cap motion, the screen, Blender).

A SpaceMouse (tested: SpacePilot Pro) can drive the dial on a computer without
a touchpad, alone or together with Mouse-only mode. It needs `spacenavd` (the
free driver for these devices; AUR `spacenavd` on Arch, packages of the same
name elsewhere); it is optional and not a dependency of the packages. Turnpad
is one more client of it, speaking its socket protocol; no library is needed.
Enable it on the *SpaceMouse* page of the settings app (a switch, the device
status, the buttons with their press and hold actions, per-application
overrides, and the cap's axes) or in `~/.config/asus-turnpad/config.json`
(`"spacemouse": {"enabled": true}`), then restart the driver. With a touchpad
mode on the computer (a DialPad, or Touchpad mode) SpaceMouse mode is not supported yet and is ignored with a warning. The
driver takes any touchpad named like an ASUS one (`ASUE`, `ELAN`, `ASUP`,
`ASUF`, `ASCE`, `ASCF`, `ASCP` with `Touchpad`) for a DialPad, so on such a
laptop (an ELAN touchpad included) SpaceMouse mode is ignored too, even if the
touchpad has no printed dial; that limit goes away when the modes can be
combined.

- **Buttons:** each button (numbered as the device reports them: 0 = Menu,
  12..16 = function keys 1..5 pressed briefly, 17..21 = the same keys held
  for about a second, 22 = Esc, 23/24/25 = Alt/Shift/Ctrl on a SpacePilot Pro;
  the settings page lists the physical keys) has a *press* action and a *hold*
  action (after `hold_ms`, 400). The function keys, T, R, F, Roll and ISO
  decide short and long press themselves (the device sends another button
  number for the long press): bind each number a press action, there is no
  hold on them. A button can have a `label`, the text a device screen shows.
  A button with only a press action acts at once. Actions: `macro`
  (keys or steps), `modifier` (Ctrl/Shift/Alt held while the button is
  down), `dial` (switch the dial on or off), `group` (open a group's list of
  the current profile), `function` (enter a function by name or menu place),
  `select`, `back`, `key_hold` (any `keys` held while the button is down,
  as the press or, after `hold_ms`, as the hold action: hold `Z` to open a
  Blender pie menu and let go to choose) and `none` (leave the button to the
  application; in a profile it overrides the global binding, e.g. `Esc` and
  `Ctrl` in Blender), `layer` (switch to another set of buttons, below),
  `motion` (change how the cap moves a 3D program, below) and `app` (run a
  command of the focused application through the app link, with `keys` as the
  fallback). Without a configured map the SpacePilot Pro default is
  used: Menu opens the dial, the function keys enter the first five functions
  (held for a second: the next five), Esc goes back, Alt/Shift/Ctrl are held,
  Rotation and Pan/Zoom switch those motions of the cap off and on, + and −
  make it faster and slower. A
  profile can bind buttons itself (`spacemouse_buttons`), which replaces the
  global binding of that button; a profile with no functions but buttons keeps
  the default profile's dial menu and applies its buttons. Other buttons are
  left to other programs. Pressing a button that only holds keys (Ctrl) or is
  left to other programs while the menu button is held does not cancel the
  release-to-select.
- **Release to select:** hold the button that opened the dial or a list,
  tilt the cap towards a tile and let go of the button: it is chosen. Let go
  without tilting and the menu stays open; tilt, then push the cap (or press
  the same button) to choose, or press the same button again to close.
- **Tilt** (away from you = top, right = 90 degrees) highlights the tile in
  that direction; in a list, tilting forward or back steps through it (and
  repeats while held, faster at full tilt). **Twist** turns the dial: in the
  menu a step per sector, in a list a step per item, in a function like the
  touchpad ring (threshold, sensitivity and scroll speed apply). Tilt and
  twist ignore each other when one is clearly stronger. **Pushing the cap
  down** is the centre button (tap; hold to go back).
- **Other programs:** while the dial is on, Turnpad sets spacenavd's global
  sensitivity to 0 so CAD programs stand still (`mute_others`), and puts it
  back when the dial is off. A value that was active at that moment may linger
  for other programs until the cap moves again, so switch the dial on with the
  cap at rest. The old value is also saved in
  `~/.config/asus-turnpad/spacenav-restore.json` and restored at the next
  start if the driver was killed. Another program can do the same for a while
  with the D-Bus command `Control` `mute:MS` (at most 5 s, renewed by the
  sender; it works with `mute_others` off too). `hide_buttons` (off by
  default) can additionally hide the buttons Turnpad binds (in the focused
  application's profile and the global map, and in their layers) from other
  programs (not the ones set to `none`, which pass through); it follows the focus and gives the buttons back when you switch
  (a button that is held down at that moment changes when it is let go).
  Blender 5.2 itself binds the Menu button (its 3D-mouse panel), Fit, the view
  axes, Roll and +/−, and leaves ISO, 1–12 and V1–V3 unbound, so turn this on
  when Menu is the dial.
- **Button layers:** a `layer` action (`layer` = name, `layer_mode`) switches
  the buttons to another map laid over the base map; buttons the layer does not
  bind keep their base binding. Modes: `hold` (while the button is down),
  `latch` (the next press of any other button uses the layer, then it ends)
  and `toggle` (until the button is pressed again). Layers are global
  (`spacemouse.layers`) and per profile (`spacemouse_layers`). Hold layers
  cannot be run with `Control` `button:INDEX`.
- **Cap motion per application:** `spacemouse.motion` and a profile's
  `spacemouse_motion` change how the cap moves the 3D program: `sensitivity`
  (0.1–4), `axis_sensitivity` (six values, 0 locks an axis), `invert`
  (six flags), `rotation` and `translation` (off = that group is silent).
  They are factors on spacenavd's own settings, applied while the application
  has focus and given back afterwards (also at the next start from the restore
  file after a crash); the default sends nothing. The buttons Rotation,
  Pan/Zoom, + and − (`motion` actions) change them for the running session
  only.
- **App commands and the Blender add-on:** an application with an add-on
  (Blender) tells the driver its commands through a private socket; the `app`
  action runs one of them (and the screen can show them). Install the add-on
  from the *Blender* group of the SpaceMouse page. Profile contexts can switch
  menus and buttons by the application's mode.
- **Screen:** a SpacePilot Pro's screen is drawn by its own service,
  `asus-turnpad-screen.service` (needs `pyusb`); see the *Screen* page of the
  settings app and [SPACEMOUSE.md](SPACEMOUSE.md).
- **Settings:** `socket` (empty: `$SPNAV_SOCKET`, `/etc/spnavrc`, the runtime
  directory, `/var/run/spnav.sock`), `axis_max` (400), `tilt_axes`
  (`[horizontal, vertical]`, default `[5, 3]`, with `tilt_invert`),
  `twist_axis` (4) with `twist_invert` and `twist_speed` (360 degrees per
  second at full twist), `push_axis` (1) with `push_invert`. Turnpad reads the
  device's raw axes (0 1 2 = X Y Z, 3 4 5 = RX RY RZ, before spacenavd's own
  axis swap), so spacenavd's dead zones and button mapping do not matter. The
  defaults are measured on a SpacePilot Pro: tilt right = axis 5 positive,
  tilt towards you = axis 3 positive (away = the top of the dial), twist
  clockwise = axis 4 negative, push down = axis 1 negative; the translation
  axes (they move a little while tilting) are not read. Another model may
  need `*_invert` or other axes: look at the raw values with `spnav_ex` or
  `spacenavd -d`.
- **If the driver was killed** while the dial was on or buttons were hidden,
  spacenavd may be left muted (sensitivity 0) or with remapped buttons
  (index 63). The next driver start gives it back from the restore file, even
  with SpaceMouse mode off. Do not save spacenavd's configuration (spnavcfg)
  while the dial is on or `hide_buttons` is active: it would keep sensitivity 0
  or the mapping. `hide_buttons` maps buttons away from other programs; it
  does not hide buttons that spacenavd itself acts on through its own
  `bnact` or `kbmap` settings.

## Sharing a working model

Please open an issue with:

- the touchpad line from `/proc/bus/input/devices` and the laptop model,
- the `geometry` section of your `config.json` after calibration,
- whether the corner LED and activation work.

That is enough to add a row to `laptop_touchpad_dialpad_layouts.csv` and,
if needed, a layout file in `layouts/`.
