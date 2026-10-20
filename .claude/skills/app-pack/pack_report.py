#!/usr/bin/env python3
"""Print a ready-made application pack of config/presets.py as Markdown, for
review, for a PR description or to compare with the application's shortcuts.

    python3 .claude/skills/app-pack/pack_report.py                 # every pack, one line each
    python3 .claude/skills/app-pack/pack_report.py SolidCraft      # both variants and the buttons
    python3 .claude/skills/app-pack/pack_report.py SolidCraft --advanced
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                           check=True).stdout.strip())
sys.path.insert(0, str(ROOT))

from config.labels import keys_to_text  # noqa: E402
from config.presets import APP_PACKS, profile_for_pack, variant_summary  # noqa: E402
from config.schema import StepType  # noqa: E402
from spacemouse_devices import SPACEPILOT_PRO, index_label  # noqa: E402


def step_text(step) -> str:
    keys = keys_to_text(step.keys) if step.keys else ""
    if step.type is StepType.APP:
        return f"`{step.command}`" + (f" ({keys})" if keys else " (no key)")
    if step.type is StepType.SCROLL:
        held = f"{keys_to_text(step.modifiers)}+" if step.modifiers else ""
        return f"{held}wheel {step.direction}" + (" horizontal" if step.axis == "horizontal" else "")
    if step.type is StepType.KEYSTROKE:
        return keys
    return step.type.value


def steps_text(steps) -> str:
    return " → ".join(step_text(step) for step in steps) or "—"


def action_text(action) -> str:
    if action is None:
        return "—"
    keys = keys_to_text(action.keys) if action.keys else ""
    if action.type == "app":
        return f"`{action.command}`" + (f" ({keys})" if keys else " (no key)")
    if action.type == "macro" and action.macro:
        return " → ".join(steps_text([step]) for step in action.macro)
    if action.type in ("macro", "key_hold"):
        return ("hold " if action.type == "key_hold" else "") + keys
    return action.type


def function_rows(segments):
    for segment in segments:
        if segment.is_group:
            yield f"| **{segment.title}** (group) | | | | |"
            for item in segment.items:
                yield from _row(item, "  ↳ ")
        else:
            yield from _row(segment, "")


def _row(segment, prefix):
    look = ", ".join(f"{key}={value}" for key, value in (segment.overlay or {}).items())
    yield (f"| {prefix}{segment.title} | {steps_text(segment.macro_cw)} | {steps_text(segment.macro_ccw)} "
           f"| {steps_text(segment.macro_center)} | {look} |")


def report(pack, advanced: bool) -> None:
    profile = profile_for_pack(pack, advanced)
    variant = "advanced" if advanced else "basic"
    print(f"## {pack.name} — {variant} ({variant_summary(pack, advanced)})\n")
    if profile.overlay:
        print(f"Dial look: {profile.overlay}\n")
    print("| Function | Clockwise | Counter-clockwise | Tap | Look |")
    print("|---|---|---|---|---|")
    for row in function_rows(profile.segments):
        print(row)
    buttons = profile.spacemouse_buttons
    if buttons:
        print(f"\n### SpaceMouse buttons ({variant})\n")
        print("| Key | Label | Press | Hold |")
        print("|---|---|---|---|")
        for key in sorted(buttons, key=int):
            button = buttons[key]
            print(f"| {index_label(SPACEPILOT_PRO, int(key))} ({key}) | {button.label or ''} "
                  f"| {action_text(button.press)} | {action_text(button.hold)} |")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pack", nargs="?", help="pack name (case does not matter)")
    parser.add_argument("--advanced", action="store_true", help="only the advanced variant")
    parser.add_argument("--basic", action="store_true", help="only the basic variant")
    args = parser.parse_args()
    if not args.pack:
        print("| Pack | Category | App id | Basic | Advanced | Buttons |")
        print("|---|---|---|---|---|---|")
        for pack in APP_PACKS:
            print(f"| {pack.name} | {pack.category} | `{pack.app_id}` | {variant_summary(pack)} "
                  f"| {variant_summary(pack, True) if pack.advanced else '—'} | {len(pack.spacemouse_buttons())} |")
        return 0
    matches = [pack for pack in APP_PACKS if pack.name.lower() == args.pack.lower()]
    if not matches:
        print(f"no pack {args.pack!r}; packs: {', '.join(pack.name for pack in APP_PACKS)}", file=sys.stderr)
        return 1
    pack = matches[0]
    if pack.note:
        print(f"> {pack.note}\n")
    for advanced in ((True,) if args.advanced else (False,) if args.basic else (False, True)):
        report(pack, advanced)
    return 0


if __name__ == "__main__":
    sys.exit(main())
