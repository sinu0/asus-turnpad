from __future__ import annotations

from numbers import Real
from typing import Callable, Literal

from config.schema import DialPadConfig, MacroStep, Profile, Segment, StepType


def select_profile(config: DialPadConfig, app_id: str | None) -> Profile | None:
    """Select the most specific profile available for an application."""
    for profile in config.profiles:
        if profile.app_id == app_id and profile.segments:
            return profile
    for profile in config.profiles:
        if profile.app_id is None and profile.segments:
            return profile
    return None


def get_segment(profile: Profile | None, title: str | None) -> Segment | None:
    """Select a named segment, falling back to the first available segment."""
    if profile is None:
        return None
    for segment in profile.segments:
        if segment.title == title:
            return segment
    return profile.segments[0] if profile.segments else None


def macro_for(
    segment: Segment | None,
    trigger: Literal["clockwise", "counterclockwise", "center"],
) -> list[MacroStep]:
    """Return a copy of the macro associated with a dial trigger."""
    if segment is None:
        return []
    macros = {
        "clockwise": segment.macro_cw,
        "counterclockwise": segment.macro_ccw,
        "center": segment.macro_center,
    }
    return list(macros[trigger])


class MacroExecutionError(Exception):
    """Raised when a macro step cannot be executed safely."""


class MacroExecutor:
    def __init__(
        self,
        send_keys: Callable[[list[str]], None],
        scroll: Callable[[str, int], None],
        media: Callable[[str | list[str]], None],
        brightness: Callable[[str, int], None],
        volume: Callable[[str, int], None],
        run_program: Callable[[str, list[str]], None],
        run_shell: Callable[[str], None],
        sleep: Callable[[float], None],
        run_app: Callable[[str, list[str]], None] | None = None,
    ) -> None:
        self.send_keys = send_keys
        self.scroll = scroll
        self.media = media
        self.brightness = brightness
        self.volume = volume
        self.run_program = run_program
        self.run_shell = run_shell
        self.sleep = sleep
        # run_app(command id, fallback keys): through the app link; without it
        # an app step presses its fallback keys.
        self.run_app = run_app

    def execute(self, steps: list[MacroStep]) -> None:
        for step in steps:
            self._execute_step(step)

    def _execute_step(self, step: MacroStep) -> None:
        if not isinstance(step, MacroStep):
            raise MacroExecutionError("Macro entries must be MacroStep instances")
        if not isinstance(step.type, StepType):
            raise MacroExecutionError(f"Unknown macro step type: {step.type!r}")

        if step.type is StepType.KEYSTROKE:
            self.send_keys(self._keys(step))
        elif step.type is StepType.SCROLL:
            extra = self._scroll_options(step)
            if step.direction is None:
                value = self._value(step)
                self.scroll(
                    "clockwise" if value >= 0 else "counterclockwise",
                    abs(value),
                    *extra,
                )
            else:
                self.scroll(self._direction(step), self._value(step), *extra)
        elif step.type is StepType.MEDIA:
            self.media(self._direction(step) if step.direction is not None else self._keys(step))
        elif step.type is StepType.BRIGHTNESS:
            if step.keys is not None and step.direction is None and step.value is None:
                self.send_keys(self._keys(step))
            else:
                self.brightness(self._direction(step), self._value(step))
        elif step.type is StepType.VOLUME:
            if step.keys is not None and step.direction is None and step.value is None:
                self.send_keys(self._keys(step))
            else:
                self.volume(self._direction(step), self._value(step))
        elif step.type is StepType.DELAY:
            self.sleep(self._duration(step))
        elif step.type is StepType.COMMAND:
            self.run_program(self._program(step), self._args(step))
        elif step.type is StepType.SHELL:
            self.run_shell(self._script(step))
        elif step.type is StepType.APP:
            command = self._command(step)
            keys = list(step.keys) if step.keys else []
            if self.run_app is not None:
                self.run_app(command, keys)
            elif keys:
                self.send_keys(self._keys(step))
        else:
            raise MacroExecutionError(f"Unsupported macro step type: {step.type!r}")

    @staticmethod
    def _scroll_options(step: MacroStep) -> tuple:
        """Axis and held keys; omitted for plain scrolls to keep old callbacks."""
        if step.axis in (None, "vertical") and not step.modifiers:
            return ()
        if step.axis not in (None, "vertical", "horizontal"):
            raise MacroExecutionError(f"Unknown scroll axis: {step.axis!r}")
        modifiers = step.modifiers or []
        if not all(isinstance(key, str) and key for key in modifiers):
            raise MacroExecutionError("scroll modifiers must be key names")
        return (step.axis or "vertical", list(modifiers))

    @staticmethod
    def _keys(step: MacroStep) -> list[str]:
        if (
            not isinstance(step.keys, list)
            or not step.keys
            or not all(isinstance(key, str) and key for key in step.keys)
        ):
            raise MacroExecutionError(f"{step.type.value} requires non-empty keys")
        return step.keys

    @staticmethod
    def _direction(step: MacroStep) -> str:
        if not isinstance(step.direction, str) or not step.direction:
            raise MacroExecutionError(f"{step.type.value} requires a direction")
        return step.direction

    @staticmethod
    def _value(step: MacroStep) -> int:
        if isinstance(step.value, bool) or not isinstance(step.value, int):
            raise MacroExecutionError(f"{step.type.value} requires an integer value")
        return step.value

    @staticmethod
    def _duration(step: MacroStep) -> float:
        if isinstance(step.duration, bool) or not isinstance(step.duration, Real):
            raise MacroExecutionError("delay requires a duration")
        duration = float(step.duration)
        if duration < 0:
            raise MacroExecutionError("delay duration cannot be negative")
        return duration

    @staticmethod
    def _program(step: MacroStep) -> str:
        if not isinstance(step.program, str) or not step.program:
            raise MacroExecutionError("command requires a program")
        return step.program

    @staticmethod
    def _args(step: MacroStep) -> list[str]:
        if step.args is None:
            return []
        if not isinstance(step.args, list) or not all(
            isinstance(arg, str) for arg in step.args
        ):
            raise MacroExecutionError("command requires string args")
        return step.args

    @staticmethod
    def _command(step: MacroStep) -> str:
        if not isinstance(step.command, str) or not step.command:
            raise MacroExecutionError("app requires a command")
        return step.command

    @staticmethod
    def _script(step: MacroStep) -> str:
        if not isinstance(step.script, str) or not step.script:
            raise MacroExecutionError("shell requires a script")
        return step.script


if __name__ == "__main__":
    events: list[tuple[str, object]] = []

    def record(name: str) -> Callable[..., None]:
        def callback(*args: object) -> None:
            events.append((name, args))

        return callback

    executor = MacroExecutor(
        send_keys=record("send_keys"),
        scroll=record("scroll"),
        media=record("media"),
        brightness=record("brightness"),
        volume=record("volume"),
        run_program=record("run_program"),
        run_shell=record("run_shell"),
        sleep=record("sleep"),
    )
    executor.execute(
        [
            MacroStep(type=StepType.KEYSTROKE, keys=["ctrl", "z"]),
            MacroStep(type=StepType.SCROLL, direction="clockwise", value=120),
            MacroStep(type=StepType.SCROLL, value=-2),
            MacroStep(type=StepType.SCROLL, direction="clockwise", value=60, axis="horizontal", modifiers=["KEY_LEFTCTRL"]),
            MacroStep(type=StepType.MEDIA, keys=["play-pause"]),
            MacroStep(type=StepType.BRIGHTNESS, direction="up", value=2),
            MacroStep(type=StepType.BRIGHTNESS, keys=["brightness-down"]),
            MacroStep(type=StepType.VOLUME, direction="down", value=3),
            MacroStep(type=StepType.VOLUME, keys=["mute"]),
            MacroStep(type=StepType.DELAY, duration=0.25),
            MacroStep(type=StepType.COMMAND, program="example"),
            MacroStep(type=StepType.SHELL, script="example --shell"),
        ]
    )
    assert events == [
        ("send_keys", (["ctrl", "z"],)),
        ("scroll", ("clockwise", 120)),
        ("scroll", ("counterclockwise", 2)),
        ("scroll", ("clockwise", 60, "horizontal", ["KEY_LEFTCTRL"])),
        ("media", (["play-pause"],)),
        ("brightness", ("up", 2)),
        ("send_keys", (["brightness-down"],)),
        ("volume", ("down", 3)),
        ("send_keys", (["mute"],)),
        ("sleep", (0.25,)),
        ("run_program", ("example", [])),
        ("run_shell", ("example --shell",)),
    ]
    print("Smoke test passed")
