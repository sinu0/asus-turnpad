#!/usr/bin/env python3
"""Run the checks a Turnpad change needs, picked from the files it touches.

    python3 .claude/skills/turnpad-checks/checks.py [--base REF] [--baseline] [--list]

Always: the unit suite (python3 -m unittest discover -s tests). By the
changed files (committed since REF, default origin/master, staged, unstaged
and untracked): Python syntax, bash -n, dialpad.py CRLF line endings,
whitespace (git diff --check without the CRs of dialpad.py), .desktop files,
the overlay previews, and reminders of what only runs elsewhere (containers,
a desktop). --baseline also runs the suite on REF in a temporary worktree and
reports only failures that REF does not have, which separates regressions
from a container that lacks GTK, gjs or libevdev; --python runs the suite with
another interpreter (the one the system's PyGObject is built for). --list only
prints the plan.
Exit status 1 when a check (or a new failure) fails."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                           check=True).stdout.strip())
PREVIEW_INPUTS = ("gnome/extension/", "overlay/painter.py", "overlay/look.py", "config/skins.py",
                  "tools/render_overlay_preview.js", "examples/skins/")
SETTINGS_INPUTS = ("config_app/", "config/presets.py", "tools/screenshot_settings.py")
PACKAGING_INPUTS = ("packaging/", "install", "uninstall.sh", "asus-turnpad")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout


def changed_files(base: str) -> list[str]:
    names = set(git("diff", "--name-only", f"{base}...HEAD").split())
    names |= set(git("diff", "--name-only", "HEAD").split())
    names |= set(git("ls-files", "--others", "--exclude-standard").split())
    return sorted(name for name in names if (ROOT / name).exists())


def run(title: str, command: list[str], cwd: Path = ROOT, timeout: int = 900) -> tuple[bool, str]:
    try:
        done = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return True, f"skipped: {command[0]} is not installed"
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout} s"
    output = (done.stdout + done.stderr).strip()
    return done.returncode == 0, output


# The interpreter of the unit suite (--python): the one PyGObject was built for.
PYTHON = sys.executable


def suite(cwd: Path) -> tuple[set[str], str]:
    """Failing test ids of the unit suite in ``cwd`` and its last line."""
    _ok, output = run("unit tests", [PYTHON, "-m", "unittest", "discover", "-s", "tests"], cwd=cwd)
    failures = set(re.findall(r"^(?:ERROR|FAIL): (\S+ \([^)]*\))", output, re.M))
    summary = output.splitlines()[-1] if output else ""
    return failures, summary


def crlf_problems(path: Path) -> list[int]:
    lines = path.read_bytes().split(b"\n")
    return [number for number, line in enumerate(lines[:-1], 1) if not line.endswith(b"\r")]


def plan(files: list[str]) -> list[tuple[str, object]]:
    steps: list[tuple[str, object]] = []
    python = [name for name in files if name.endswith(".py")]
    if python:
        steps.append(("Python syntax", [sys.executable, "-m", "py_compile", *python]))
    shell = [name for name in files if name.endswith(".sh") or name in ("packaging/asus-turnpad-setup",)]
    if shell:
        steps.append(("bash -n", ["bash", "-n", *shell]) if len(shell) == 1 else
                     ("bash -n", ["bash", "-c", "for f; do bash -n \"$f\" || exit 1; done", "_", *shell]))
    if "dialpad.py" in files:
        steps.append(("dialpad.py CRLF", "crlf"))
    steps.append(("whitespace", "whitespace"))
    desktop = [name for name in files if name.endswith(".desktop")]
    if desktop:
        steps.append(("desktop files", ["desktop-file-validate", *desktop]))
    if any(name.startswith(PREVIEW_INPUTS) for name in files):
        steps.append(("overlay previews", [sys.executable, "tools/update_previews.py", "--check", "--only", "overlay"]))
    return steps


def reminders(files: list[str]) -> list[str]:
    notes = []
    if any(name.startswith(SETTINGS_INPUTS) for name in files):
        notes.append("settings pictures: run `python3 tools/update_previews.py` on a desktop that has the apps "
                     "(Krita, Firefox, darktable icons) and commit changed pictures; the layout test needs mutter")
    if any(name.startswith(PACKAGING_INPUTS) for name in files):
        notes.append("packages and installs run in CI containers: packaging/{arch,deb,rpm}/test-package.sh, "
                     "tests/install_in_container.sh (podman run --rm -v \"$PWD:/src:ro\" fedora:latest …)")
    if any(name.startswith("gnome/extension/") for name in files):
        notes.append("GNOME Shell reloads extension code only after logout/login")
    if any(name.startswith("overlay/") for name in files):
        notes.append("layer-shell dial: tests/layer_shell_in_container.sh /src sway OUT (CI job layer-shell-dial)")
    if any(name.startswith("integrations/") for name in files):
        notes.append("bridges/add-ons: .claude/skills/app-plugin/SKILL.md steps 5 and 8")
    return notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="origin/master")
    parser.add_argument("--baseline", action="store_true", help="tell new failures from ones REF has too")
    parser.add_argument("--list", action="store_true", help="print the plan only")
    parser.add_argument("--python", default=sys.executable,
                        help="interpreter of the unit suite (e.g. /usr/bin/python3.12 where gi is built for it)")
    args = parser.parse_args()
    global PYTHON
    PYTHON = args.python

    files = changed_files(args.base)
    steps = plan(files)
    print(f"{len(files)} changed file(s) since {args.base}")
    for title, _command in steps:
        print(f"  - {title}")
    print("  - unit tests" + (" (and the same on the base)" if args.baseline else ""))
    if args.list:
        for note in reminders(files):
            print(f"note: {note}")
        return 0

    failed = False
    for title, command in steps:
        if command == "crlf":
            bad = crlf_problems(ROOT / "dialpad.py")
            ok, output = not bad, f"LF-only lines: {bad[:20]}" if bad else ""
        elif command == "whitespace":
            output = git("diff", "--check", args.base, "--", ".", ":(exclude)dialpad.py")
            ok = not output.strip()
        else:
            ok, output = run(title, command)
        failed |= not ok
        print(f"[{'ok' if ok else 'FAIL'}] {title}")
        if output and (not ok or output.startswith("skipped")):
            print("    " + "\n    ".join(output.splitlines()[-25:]))

    failures, summary = suite(ROOT)
    if args.baseline:
        workdir = Path(tempfile.mkdtemp(prefix="turnpad-base-"))
        try:
            subprocess.run(["git", "worktree", "add", "--detach", str(workdir / "tree"), args.base],
                           cwd=ROOT, capture_output=True, check=True)
            before, base_summary = suite(workdir / "tree")
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(workdir / "tree")], cwd=ROOT, capture_output=True)
            shutil.rmtree(workdir, ignore_errors=True)
        new = sorted(failures - before)
        print(f"[{'ok' if not new else 'FAIL'}] unit tests: {summary} (base: {base_summary})")
        for test in new:
            print(f"    new: {test}")
        fixed = sorted(before - failures)
        if fixed:
            print(f"    no longer failing: {len(fixed)}")
        failed |= bool(new)
    else:
        print(f"[{'ok' if not failures else 'FAIL'}] unit tests: {summary}")
        for test in sorted(failures)[:40]:
            print(f"    {test}")
        failed |= bool(failures)

    for note in reminders(files):
        print(f"note: {note}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
