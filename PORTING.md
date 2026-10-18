# Running the DialPad on another laptop model

Turnpad has three modes (the table is in the [README](README.md#modes)):
**DialPad mode** for an ASUS laptop with the DialPad printed on the touchpad
(this page: another laptop model, calibration), **Touchpad mode** for any
other touchpad (below) and **Mouse-only mode** for a computer without a
touchpad, where a mouse is the dial (below).

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
on **Mouse-only mode**; the app restarts the driver (it then needs no
touchpad). While the dial is on, the driver grabs the mouse: the pointer and
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

## Sharing a working model

Please open an issue with:

- the touchpad line from `/proc/bus/input/devices` and the laptop model,
- the `geometry` section of your `config.json` after calibration,
- whether the corner LED and activation work.

That is enough to add a row to `laptop_touchpad_dialpad_layouts.csv` and,
if needed, a layout file in `layouts/`.
