#!/usr/bin/env python3
"""Regenerate the README pictures in preview/, keeping files that did not change.

Renders every picture into a temporary directory (the on-screen dial with
tools/render_overlay_preview.js, the SpacePilot Pro screen with
tools/render_screen_preview.py, the settings app with
tools/screenshot_settings.py) and compares each with the committed one. The
settings app runs inside a headless mutter on its own D-Bus session, so no
window shows up, nothing steals focus and the pictures never catch an
unfocused window; without mutter (or with --visible) it opens on the desktop. Rendering is not bit-exact between runs
(blurred and dithered backgrounds differ by a few levels), so a picture only
counts as changed when enough pixels differ by more than a tolerance;
otherwise the committed file stays untouched and git sees no change.

    python3 tools/update_previews.py           # replace the changed pictures
    python3 tools/update_previews.py --check   # only report; exit 1 if any changed
    python3 tools/update_previews.py --visible # settings app on the desktop

The settings pictures show the icons of the apps installed on the machine
(Krita, Firefox, darktable), so only a desktop with them renders them as
committed. CI therefore compares the overlay pictures alone and keeps the
rest for viewing:

    python3 tools/update_previews.py --check --only overlay --output rendered/

tools/git-hooks/pre-push runs the --check form before every push once
enabled with: git config core.hooksPath tools/git-hooks
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
PREVIEW = ROOT / "preview"

# A channel difference above TOLERANCE is a real change; rendering noise
# stays below 8. MIN_PIXELS ignores a handful of anti-aliased edge pixels.
TOLERANCE = 12
MIN_PIXELS = 25


# Tall enough for the 1168 px Dial Controls picture with window shadows.
VIRTUAL_MONITOR = "2560x1600"
# Seconds; a stuck renderer fails instead of hanging the push or CI.
TIMEOUT = 180


def headless(command: list[str]) -> list[str]:
    """Run command in a headless mutter of its own, when mutter is there."""
    mutter, dbus = shutil.which("mutter"), shutil.which("dbus-run-session")
    if not (mutter and dbus):
        return command
    return [dbus, "--", mutter, "--headless", "--wayland", "--no-x11",
            "--virtual-monitor", VIRTUAL_MONITOR,
            "--wayland-display", f"turnpad-previews-{os.getpid()}", "--", *command]


def render(output: Path, visible: bool = False) -> None:
    subprocess.run(["gjs", "-m", str(ROOT / "tools" / "render_overlay_preview.js"), str(output)],
                   check=True, stdout=subprocess.DEVNULL, timeout=TIMEOUT)
    # The SpacePilot Pro screen, painted with Cairo from a demo configuration.
    subprocess.run([sys.executable, str(ROOT / "tools" / "render_screen_preview.py"), str(output)],
                   check=True, stdout=subprocess.DEVNULL, timeout=TIMEOUT)
    screenshots = [sys.executable, str(ROOT / "tools" / "screenshot_settings.py"), str(output)]
    # The private D-Bus session has no accessibility bus. GSettings defaults
    # instead of the desktop's settings, so every machine renders the same.
    environment = {**os.environ, "GTK_A11Y": "none", "NO_AT_BRIDGE": "1", "GSETTINGS_BACKEND": "memory"}
    run = subprocess.run(screenshots if visible else headless(screenshots), env=None if visible else environment,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=TIMEOUT)
    if run.returncode:
        # mutter is chatty; the end holds the traceback.
        sys.exit(f"screenshot_settings.py failed:\n{run.stderr[-4000:]}")


def changed_pixels(old: Path, new: Path) -> int | None:
    """Pixels that differ by more than TOLERANCE; None when the size differs."""
    a = Image.open(old).convert("RGB")
    b = Image.open(new).convert("RGB")
    if a.size != b.size:
        return None
    # The largest channel difference of each pixel, then a threshold.
    difference = ImageChops.difference(a, b)
    largest = ImageChops.lighter(ImageChops.lighter(*difference.split()[:2]), difference.split()[2])
    mask = largest.point(lambda value: 255 if value > TOLERANCE else 0)
    return mask.histogram()[255]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report outdated pictures without writing")
    parser.add_argument("--visible", action="store_true", help="open the settings app on the desktop")
    parser.add_argument("--only", metavar="PREFIX", default="",
                        help="compare only pictures whose name starts with PREFIX, e.g. overlay")
    parser.add_argument("--output", metavar="DIR", type=Path,
                        help="also keep every rendered picture in DIR")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="turnpad-previews-") as directory:
        output = Path(directory)
        render(output, visible=args.visible)
        if args.output:
            shutil.copytree(output, args.output, dirs_exist_ok=True)
        outdated = []
        for new in sorted(output.glob(f"{args.only}*.png")):
            old = PREVIEW / new.name
            if not old.exists():
                status, differs = "new", True
            else:
                pixels = changed_pixels(old, new)
                differs = pixels is None or pixels >= MIN_PIXELS
                status = "size changed" if pixels is None else f"{pixels} pixels differ"
            if differs:
                outdated.append(new.name)
                if not args.check:
                    shutil.copyfile(new, old)
            print(f"{'changed ' if differs else 'same    '} {new.name} ({status})")

    if args.check and outdated:
        print(f"\n{len(outdated)} picture(s) in preview/ are outdated; run: python3 tools/update_previews.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
