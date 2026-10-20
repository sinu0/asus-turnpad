---
name: turnpad-checks
description: Pick and run the checks a Turnpad change needs before a commit or push (unit suite, syntax, bash -n, dialpad.py CRLF, whitespace, previews), separate new failures from ones a container without GTK/gjs/libevdev always has, and list what only CI or a desktop can check. Use before every commit or push in this repository, after a merge, and when tests fail in a cloud container.
---

# Checks for a Turnpad change

```bash
python3 .claude/skills/turnpad-checks/checks.py --list          # the plan for the changed files
python3 .claude/skills/turnpad-checks/checks.py                 # run it
python3 .claude/skills/turnpad-checks/checks.py --baseline      # and tell new failures from old ones
```

The script finds the changed files: commits since `--base` (default `origin/master`), plus staged, unstaged and untracked files. It always runs the unit suite. Depending on the files it adds:

| Changed | Check |
|---|---|
| any `*.py` | `py_compile` |
| `*.sh`, `packaging/asus-turnpad-setup` | `bash -n` |
| `dialpad.py` | every line ends in CRLF (the file is DOS format in git) |
| everything but `dialpad.py` | `git diff --check` (dialpad.py's CRs would all show) |
| `*.desktop` | `desktop-file-validate` |
| extension, painter, look, skins | `tools/update_previews.py --check --only overlay` |

It also prints reminders for what it cannot do here: settings pictures (rendered locally, because they show the apps installed on the desktop), package and install containers, the layer-shell dial on headless sway, and Shell reloads.

## In a cloud container

The CI image is Arch (`archlinux:latest`). Ubuntu containers lack or have older GI, GTK 4, Libadwaita and libevdev. Install what the suite can use:

```bash
sudo apt-get update && sudo apt-get install -y python3-cairo python3-libevdev python3-gi gjs
```

About 15 tests still fail here for the environment (`test_commandline`, `test_hardware`, `test_screen_*`, `test_dir_watch`, `test_mouse_dial` F13…). Use `--baseline`: it runs the same suite on the base commit in a temporary `git worktree` and fails only on failures the base does not have.

## Fast loops while working

- One module: `python3 -m unittest tests.test_presets -v`
- Bridges and app link: `python3 -m unittest tests.test_bridges tests.test_app_link tests.test_blender_addon`
- Packaging lists: `python3 -m unittest tests.test_install tests.test_identity`
- Driver state functions (after renaming one in `dialpad.py`): `tests/test_driver_state.py` `STATE_FUNCTIONS`

## Before pushing

1. `checks.py --baseline` is clean (no new failures).
2. Pictures: if the extension, painters, skins or settings app changed, say in the PR that `tools/update_previews.py` needs a run on a desktop (or run it when one is available). Commit only pictures that really changed.
3. Read `git diff` once more as a reviewer, looking for: identifiers in their twins (`config/identity.py` ↔ `install_paths.sh`, `dial.js` ↔ `overlay/`), new files listed in every installer (see the `ship-files` skill), and docs (`AGENTS.md`, `CHANGELOG.md`).
