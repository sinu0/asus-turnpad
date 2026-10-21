---
name: app-pack
description: Add or change a ready-made application pack in config/presets.py (basic and advanced dial menus, SpaceMouse buttons, app commands with key fallbacks), with shortcuts checked against the application's source and the tests that guard packs. Use when asked for a profile, pack or preset for an application, or to fix one's shortcuts or buttons.
---

# Application packs

A pack is an `AppPack` in `APP_PACKS` (`config/presets.py`). It has a name, hints (lower-case words of the desktop id or binary that `pack_for_app()` matches), the basic factory, `app_id` (the desktop id, used when the app is not installed), a category (`Photo & graphics`, `3D`, `Electronics`, `Video`, `Audio`, `Everyday`), an optional note, `advanced`, `advanced_look` and `spacemouse` / `spacemouse_advanced`.

## Steps

1. **Read the shortcuts from the app's source or default keymap file** (clone into the scratchpad). Use no forum lists unless nothing else exists (DaVinci Resolve), and say so in the comment. Put a comment above the pack: the app version and the files the keys come from, as the other packs do.
2. **Basic variant**: at most 6 functions and no groups. It holds the functions people turn most: view and zoom, the main size or time control, undo/redo.
3. **Advanced variant**: at most 8 sectors. Groups follow the stages of the work (View, Sketch, Model, Inspect), go one level deep, hold at least 2 items each, and every title is unique (a group never shares a title with one of its items). Turning functions get a look that keeps the work visible: `VIEW` (fade) for the view, `CANVAS` (pill) for brush sizes, `TIMELINE` (top) for timelines; timeline editors set `advanced_look=TIMELINE_MENU`.
4. **Steps**: `keys(*evdev names, title=)`, `scroll(direction, modifiers=, axis=)`, `_sequence()` for "V, F", and `app_step(command, *fallback keys, title=)` when the app has a plugin (see the `app-plugin` skill; the fallback is the app's own key for the same thing, or none). Helpers: `_turn`, `_tap`, `_group`, `_look`, `_app_turn`, `_app_tap`. Thresholds: precise controls 45–90, fast ones 20. Every turning function has both directions. Colours come from `COLORS`, icons are symbolic icon names used elsewhere in the file.
5. **SpaceMouse buttons** (`spacemouse=`): indexes are SpacePilot Pro raw indexes (`spacemouse_devices.py`). 1 is Fit, 2/6 T and T·long, 4/3 R and R·long, 5/7 F and F·long, 8/9 Roll, 10/11 ISO, and the function keys are 12–16 short and 17–21 long. Keys 2–21 are dual-function, so they get press actions only (no `hold`). Leave 0 (Menu = dial), 22–25 (Esc, Alt, Shift, Ctrl) and 26–30 (motion) to the global map, unless the app reads them itself: then use `_pass_through()`, as Blender does. Bind nothing the app reads from spacenavd itself. Labels are at most 20 characters. Use `_press`, `_hold`, `_presses`, and `_app_press(label, command, *keys)` for plugin commands.
6. **Tests** (`tests/test_presets.py`): add the desktop id to `PackMatchTest.test_desktop_ids`, and add creator packs with buttons to `test_creator_packs_have_buttons`. For a pack with app commands, add a check of every id and its fallback keys against the app's registry, as `SolidCraftPackTest` does.
7. **Review it as a table** and paste the table into the PR description:

   ```bash
   python3 .claude/skills/app-pack/pack_report.py NAME          # both variants and the buttons
   python3 .claude/skills/app-pack/pack_report.py               # every pack, one line each
   ```

8. **Docs**: the pack lists in `README.md` (features, settings app bullets, ready maps), `CHANGELOG.md`, and the presets bullet in `AGENTS.md` when something is new. Settings pictures may show pack lists: they are re-rendered locally (`tools/update_previews.py`), not in CI.

## Checks

`python3 -m unittest tests.test_presets -v` checks sizes, unique titles, evdev names (with python-libevdev installed), looks, round trips through the schema and `profile_share`, button indexes and labels, and desktop ids. Then run `.claude/skills/turnpad-checks/checks.py --baseline`.
