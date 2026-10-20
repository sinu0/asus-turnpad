---
name: app-plugin
description: Build a Turnpad plugin for an application — an app link add-on that runs inside the app (like integrations/blender) or a bridge the driver runs for an app with a control channel (like integrations/solidcraft) — plus its ready-made pack, tests, packaging and docs. Use when asked to add, extend or debug integration with an application ("plugin do X", "add-on", "bridge", "app link", "integracja z X"), or when an app pack should run the app's own commands instead of key presses.
---

# Turnpad application plugins

The driver's **app link** (`app_link.py`) is a private Unix socket. A client says `hello` (`app`, `name`, `version`, `pid`, `protocol` 1), sends its **catalog** (≤ 500 entries: `id` `[A-Za-z0-9_.:-]{1,128}`, `title`, `category`, `icon`, `keys`, `modes`) and its **context** (`mode`, `label`, `detail`) whenever it changes. The driver sends only `run` (`id`, `seq`) for ids of that client's own catalog, and the client answers with `result` (`seq`, `ok`, `error`). The driver never executes anything it receives. The link name must match the app's desktop id under `app_link.app_matches()`: `blender` matches `org.blender.Blender.desktop`. Dial `app` steps (`config/presets.py` `app_step()`) and SpaceMouse `app` actions run catalog ids. Their `keys` are pressed when no client is linked.

## 1. Pick the shape

| The application has… | Build | Example |
|---|---|---|
| a scripting/add-on API that can open a Unix socket (Python, Lua, JS…) | **add-on** inside the app: twin of `integrations/applink/` + catalog, runner, context, timer glue | `integrations/blender/turnpad/` |
| a control channel reachable from outside (localhost TCP/JSON, a Unix socket, D-Bus, a WebSocket) | **bridge** in the driver: subclass `integrations/bridge_base.LinkedBridge` | `integrations/solidcraft/` |
| neither | keys only: a pack in `config/presets.py` (see the `app-pack` skill) | GIMP, Krita |

A bridge needs nothing installed in the app. Prefer it when the channel exists and is safe (authenticated, local).

## 2. Research the app from its source (never from memory)

Clone it into the scratchpad (`git clone --depth 1 …`) and write down, with the file and the commit:

- **Command registry**: ids, labels, categories, default shortcuts, which ones need parameters or a selection, and which open a dialog. SolidCraft: `CommandSpec` in `crates/engine/src/cmd/*.rs`. A throwaway parser of the registry into JSON is worth it, because the pack and the tests use the same facts.
- **Keys the window handles itself** (arrows, F-keys, search): these are the fallbacks when the plugin is absent.
- **Control channel**: transport, authentication (token files, permissions), limits, timeouts, threading (SolidCraft answers on its UI thread between frames). Find "run like a toolbar click" (SolidCraft `ui.start`) and "read the state" (SolidCraft `ui.inspect`).
- **Desktop id** (`StartupWMClass`, Wayland `app_id`) and Flatpak/Snap data folders.
- **3D mouse**: does the app read the cap or the buttons itself? Bind only what it leaves free (`none` passes a button through).

## 3. Scaffold (bridge)

```bash
python3 .claude/skills/app-plugin/scaffold_bridge.py NAME --title "App" \
    --desktop-id org.example.App.desktop [--link-app WORD] --wire
```

The script writes `integrations/NAME/{__init__,catalog,control}.py` and `tests/test_NAME_bridge.py` (these tests pass from the start and carry TODOs). With `--wire` it registers the bridge in `integrations/bridges.py` `BRIDGES`, `install.sh` (folder and install line), the `for dir in` line of `packaging/install-files.sh`, `tests/test_install.py` `BRIDGE_PACKAGES` and `packaging/check-imports.py`. It checks every edit before writing anything, and it refuses a link name that does not match the desktop id.

## 4. Implement

- `control.py`: one connection, and `call()` with a **timeout** (3 s). On a timeout, close the connection and raise `ConnectionLost`, because a late reply would answer the next request. App errors raise `ControlError` and leave the connection open. Use localhost only. Read tokens or credentials with `O_NOFOLLOW` and only from private files of this uid. Refuse a port that another uid listens on (`solidcraft.control.port_owner`).
- `Bridge.candidates()` must be cheap (no connection): token files, sockets, bus names. A candidate's key should change when the app restarts (path + mtime), so that a new launch is tried at once. A failed candidate rests `REFUSE_S`.
- `connect()` reads the version and the registry and returns a `Session`. The catalog comes from **the app's registry** plus the bridge's own commands (`turnpad.*`) for things the app has no command for but a dial needs (camera steps without animation, standard views). Leave out what must never run from a dial: file writes without a dialog, quit, settings.
- `Session.run(id)` takes the app's "as the user would" path, never raises, and returns `(ok, error)`. `poll()` returns `{mode, label, detail}`; keep modes lowercase words such as `sketch` or `sheet_metal`, because profile contexts match them. Raise `BridgeGone` when the app has gone away.
- Everything runs on the bridge host's single thread, so no call may block for longer than its timeout. Use no third-party libraries in bridges: standard library only, nothing to add to `install_packages.sh`.

## 5. Test

- Write `tests/fake_NAME.py`: a fake control channel that speaks the documented protocol, including authentication and an option to stop answering (`tests/fake_solidcraft.py`).
- Unit tests: credentials and their discovery (permissions, symlinks, other uid), catalog mapping (it must survive `app_link.clean_catalog()` unchanged and fit `protocol.encode`), runner math and parameters, context parsing.
- End to end, as in `tests/test_bridges.py` `BridgeTest`: a real `app_link.AppLink` in a private temp directory, plus the fake and the bridge ticking on a thread. Check that `AppLink.run(desktop_id, id)` reaches the fake, that the context arrives, that an id outside the catalog gets `UNKNOWN_COMMAND`, and that the app quitting and coming back works.
- Run `python3 -m unittest tests.test_NAME_bridge tests.test_bridges tests.test_install tests.test_presets`, then the whole suite (see the `turnpad-checks` skill).

## 6. Pack

Add an `AppPack` in `config/presets.py` (see the `app-pack` skill). Use `app_step(command, *fallback_keys, title=)` in dial macros and `_app_press(label, command, *keys)` for SpaceMouse buttons. The fallback keys must be the app's own default keys for the same thing, so that the pack works without the plugin. Add a test like `SolidCraftPackTest` that checks every id and its fallback keys against the registry facts from step 2.

## 7. Add-on instead of a bridge

Mirror `integrations/blender/turnpad/`: copy `protocol.py` and `client.py` from `integrations/applink/` (twins; extend `TwinsTest` in `tests/test_bridges.py`), and add `catalog.py` (an allowlist of the app's commands), `runner.py`, `context.py` and the app's timer glue. Fakes of the app's API go in `tests/fake_<app>.py`. Installing the add-on from the settings app is `config_app/blender_addon.py` (in a thread, never as root). Ship the folder in `install.sh` and `packaging/install-files.sh`.

## 8. Document and ship

- `<APP>.md` (user guide: how to turn the channel on, what works with and without the plugin, troubleshooting). Add it to the doc list in `packaging/install-files.sh`.
- README (feature bullets and pack lists), `CHANGELOG.md`, `ARCHITECTURE.md` (table and diagram), and an `AGENTS.md` bullet with the facts and the app version they were read at.
- Commit the source facts in comments next to the pack (`# SolidCraft 0.13 (…, checked at 41a62d2)`).

## Rules that never bend

- The driver never executes received data. Bridges send the app only ids from the catalog they built.
- Every network or IPC request has a timeout and a size limit. A bridge exception rests that bridge and never stops the others or the driver.
- Never weaken `NoNewPrivileges`, and never add root steps to the plugin's path.
