# SolidCraft

[SolidCraft](https://github.com/bherbruck/solidcraft) is an open-source parametric CAD program. Turnpad drives it two ways:

- **With its keys**, out of the box: the *SolidCraft* pack (Dial Controls › Add a profile › 3D) turns the view with the arrow keys, fits with F6, opens command search with S and starts Extrude, Fillet, Line and other tools with their keys.
- **As a SolidCraft plugin**, once SolidCraft listens on a control port: every dial step and button runs SolidCraft's own command by id, in the SolidCraft window that has the focus (keys reach the window under the pointer). The view turns in 5° steps without the arrow keys' animation. Roll, the standard views (Front, Top, Right, …) and Home work too, although SolidCraft has no keys for them, and profile contexts can follow the ribbon tab (Model, Sketch, Render…).

Turnpad shows up in SolidCraft's own *Preferences › Plugins* list, where you can untick it to unlink Turnpad. SolidCraft runs no plugin code yet, so the plugin is a folder with a `plugin.json` that names it; the work is done by the SolidCraft bridge inside the Turnpad driver, which links a SolidCraft only while the plugin is there and ticked.

## Set it up

The **Plugins** page of the Turnpad settings app does both steps with a button each and shows whether SolidCraft is linked:

1. **Control port**: *Turn on* writes `~/.config/environment.d/solidcraft.conf`; log out and in once.
2. **Turnpad plugin**: *Add* puts `turnpad/plugin.json` into SolidCraft's plugins folder (the native build's, and the Flatpak's or Snap's when you have them). The driver links SolidCraft within seconds.

The page can also add the ready-made SolidCraft profile. By hand, the same steps are:

```bash
mkdir -p ~/.config/environment.d
echo 'SOLIDCRAFT_CONTROL_PORT=7878' > ~/.config/environment.d/solidcraft.conf
mkdir -p ~/.local/share/solidcraft/plugins/turnpad
python3 -c 'import json, sys; sys.path.insert(0, "/usr/share/asus-turnpad"); sys.path.insert(0, "/opt/asus-turnpad")
from integrations.solidcraft import plugin; print(json.dumps(plugin.MANIFEST, indent=2))' \
    > ~/.local/share/solidcraft/plugins/turnpad/plugin.json
```

## SolidCraft's control port

SolidCraft opens its control channel only when asked to. The port is set once for your session:

```bash
mkdir -p ~/.config/environment.d
echo 'SOLIDCRAFT_CONTROL_PORT=7878' > ~/.config/environment.d/solidcraft.conf
```

Log out and in again. Every SolidCraft you start from the desktop then listens on `127.0.0.1:7878` (or run `solidcraft --control 7878` from a terminal). Any free port works, but only one SolidCraft window can use a given port.

For the Flatpak, give the port to the sandbox as well: `flatpak override --user --env=SOLIDCRAFT_CONTROL_PORT=7878 ai.storyteller.solidcraft`.

The channel is protected: each launch makes a new random token, which SolidCraft writes to a file only you can read (`~/.local/share/solidcraft/control/7878.token`; the Flatpak and the Snap keep it in their own data folders, which the bridge also checks). The bridge connects only with that token, only to a port that your own user listens on, and it sends SolidCraft only the commands of SolidCraft's own toolbar plus the view commands listed below.

## What the bridge adds

| Command id | What it does | Key without the bridge |
|---|---|---|
| `turnpad.orbit_left` / `_right` | Turns the view 5° (like ← / →, which turn 15°) | ← / → |
| `turnpad.orbit_up` / `_down` | Tilts the view 5° | ↑ / ↓ |
| `turnpad.roll_cw` / `_ccw` | Rolls the view 5° | — |
| `turnpad.roll_quarter_cw` / `_ccw` | Rolls 90°, like the navigation cube's arrows | — |
| `turnpad.zoom_in` / `_out` | Zooms on the view's centre | (the wheel zooms at the pointer) |
| `turnpad.pan_left` / `_right` / `_up` / `_down` | Pans by 4 % of the view | Ctrl+arrows |
| `turnpad.view_fit` | Fit | F6 |
| `turnpad.view_home`, `_iso`, `_front`, `_back`, `_top`, `_bottom`, `_left`, `_right` | The standard views, animated | — |
| `turnpad.search` | Command search | S |
| every toolbar command (`solid.extrude`, `sketch.line`, …), `edit.undo`, `edit.redo` | What its toolbar button does | the command's key, if it has one |

A sketch tool (Line, Rectangle, …) chosen outside a sketch is sent as its key, so SolidCraft opens *Create Sketch* first and then starts the tool, just as when you press the key yourself.

The *App command* action of the SpaceMouse page lists these commands, grouped by toolbar tab, once the bridge has linked SolidCraft (the driver's `GetAppCommands("solidcraft")`); a dial function runs one with an `app` step.

## The SolidCraft pack

- **Basic**: Orbit (tap: Fit), Orbit up / down (tap: Home view), Zoom (the wheel; tap: Fit), Pan (tap: look at the selection, Ctrl+8), Undo / Redo, Command search.
- **Advanced**: groups *View* (orbit, tilt, roll, pan), *Views* (Fit, Home, Isometric, Front, Top, Right), *Sketch* (Create sketch, Line, Rectangle, Circle, Dimension, Trim, Finish sketch), *Model* (Extrude, Revolve, Fillet, Chamfer, Hole, Shell, Press pull), *Inspect* (Measure, Section, Parameters, Appearance), plus Zoom, Undo / Redo and Command search.
- **SpaceMouse buttons**: SolidCraft reads the cap itself (through spacenavd) but no buttons, so the pack binds them: Fit, T/R/F and their long presses for the six views, Roll for 90° rolls, ISO for Isometric and Home, and the function keys for Extrude/Revolve, Fillet/Chamfer, Hole/Shell, Create/Finish sketch and Measure/Search.

Without the bridge, roll, the standard views and Home show a notice ("No Turnpad add-on connected") instead, because SolidCraft has no keys for them.

## Contexts

While linked, the bridge reports the ribbon tab as the application mode: `model`, `sketch` (while a sketch is edited), `surface`, `assemble`, `sheet_metal`, `inspect`, `manage`, `render`, …, with the open tool or dialog as detail. In Dial Controls › Contexts, a context with the mode `sketch` can give the dial a sketching menu that shows only while you sketch.

## Troubleshooting

- The Plugins page's *Link* line says what is missing: the driver, the plugin, the port, or SolidCraft itself.
- "turned off in SolidCraft's Preferences › Plugins": tick Turnpad there again; the link comes back within seconds.
- `busctl --user call io.github.sinu0.AsusTurnpad.Driver /io/github/sinu0/AsusTurnpad/Driver io.github.sinu0.AsusTurnpad.Driver GetStatus` shows `bridges` with the bridge's `state` (`waiting`, `linked`), its `port` and the last `detail` (why a token file did not lead to SolidCraft).
- `journalctl --user -u asus-turnpad.service | grep SolidCraft` shows when the bridge linked and why it let go.
- No token file in `~/.local/share/solidcraft/control/`: SolidCraft was started without a control port (see above; the environment.d file needs a new login).
- SolidCraft busy with a long operation (a big fillet) does not answer for a moment; the bridge reconnects by itself afterwards.
