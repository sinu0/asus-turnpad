"""SpaceMouse logic without hardware: the button state machine, the cap's
tilt, twist and push, the list auto-repeat and the configuration."""

import json
import math
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config.schema import (  # noqa: E402
    DialPadConfig, Profile, SpaceMouseAction, SpaceMouseButton, SpaceMouseConfig,
    _decoder, _encoder, default_spacemouse_buttons, merge_spacemouse_buttons, shell_commands, spacemouse_binds, spacemouse_buttons_for, spacemouse_hold_keys, spacemouse_macros, spacemouse_modifier_keys,
)
from config.schema import MacroStep, StepType  # noqa: E402
from spacemouse_dial import ButtonMachine, ListStepper, MuteLease, SpaceCap  # noqa: E402


def test_driver_state_volume():
    from config.presets import volume
    return volume()


def action(kind, **kwargs):
    return types.SimpleNamespace(type=kind, **kwargs)


def binding(press=None, hold=None):
    return types.SimpleNamespace(press=press, hold=hold)


def kinds(actions):
    return [(a.kind, a.index, getattr(a.action, "type", None)) for a in actions]


class ButtonTest(unittest.TestCase):
    def setUp(self):
        self.machine = ButtonMachine(hold_ms=400)

    def test_press_without_hold_runs_at_once(self):
        out = self.machine.press(5, 1.0, binding(press=action("macro")), dial_on=False)
        self.assertEqual(kinds(out), [("action", 5, "macro")])
        self.assertEqual(self.machine.release(5, 1.1, False), [])

    def test_press_with_hold_runs_on_release_before_the_hold_time(self):
        b = binding(press=action("macro"), hold=action("group"))
        self.assertEqual(self.machine.press(5, 1.0, b, False), [])
        self.assertEqual(self.machine.tick(1.3, False), [])
        self.assertEqual(kinds(self.machine.release(5, 1.3, False)), [("action", 5, "macro")])

    def test_hold_runs_once_at_the_hold_time_and_not_the_press(self):
        b = binding(press=action("macro"), hold=action("function"))
        self.machine.press(5, 1.0, b, False)
        self.assertEqual(kinds(self.machine.tick(1.5, False)), [("action", 5, "function")])
        self.assertEqual(self.machine.tick(2.0, False), [])
        self.assertEqual(self.machine.release(5, 2.1, False), [])

    def test_hold_only_button_does_nothing_when_tapped(self):
        self.machine.press(5, 1.0, binding(hold=action("group")), False)
        self.assertEqual(self.machine.release(5, 1.1, False), [])

    def test_modifier_is_down_while_the_button_is_down_even_with_a_hold(self):
        b = binding(press=action("modifier", modifier="Ctrl"), hold=action("group"))
        self.assertEqual(kinds(self.machine.press(25, 1.0, b, False)), [("hold_down", 25, "modifier")])
        self.assertEqual(self.machine.tick(2.0, False), [])
        self.assertEqual(kinds(self.machine.release(25, 2.5, False)), [("hold_up", 25, "modifier")])

    def test_release_all_lets_go_of_modifiers(self):
        self.machine.press(25, 1.0, binding(press=action("modifier", modifier="Ctrl")), False)
        self.machine.press(0, 1.0, binding(press=action("macro")), False)
        self.assertEqual(kinds(self.machine.release_all()), [("hold_up", 25, "modifier")])
        self.assertFalse(self.machine.held)

    def test_second_press_of_a_held_button_is_ignored(self):
        b = binding(press=action("macro"))
        self.machine.press(5, 1.0, b, False)
        self.assertEqual(self.machine.press(5, 1.1, b, False), [])

    # --- release to select -------------------------------------------------------------

    def test_menu_button_released_after_a_tilt_selects(self):
        b = binding(press=action("dial"))
        self.assertEqual(kinds(self.machine.press(0, 1.0, b, dial_on=False)), [("action", 0, "dial")])
        self.machine.note_highlight()
        self.assertEqual(kinds(self.machine.release(0, 1.5, True)), [("select", 0, None)])
        self.assertIsNone(self.machine.opener)

    def test_menu_button_released_without_a_tilt_stays_open_and_the_next_press_closes(self):
        b = binding(press=action("dial"))
        self.machine.press(0, 1.0, b, False)
        self.assertEqual(self.machine.release(0, 1.2, True), [])  # sticky
        self.assertEqual(kinds(self.machine.press(0, 3.0, b, True)), [("close", 0, None)])
        self.assertEqual(self.machine.release(0, 3.1, False), [])
        self.assertIsNone(self.machine.opener)

    def test_sticky_menu_and_a_tilt_then_the_same_button_selects(self):
        b = binding(press=action("dial"))
        self.machine.press(0, 1.0, b, False)
        self.machine.release(0, 1.2, True)
        self.machine.note_highlight()
        self.assertEqual(kinds(self.machine.press(0, 3.0, b, True)), [("select", 0, None)])
        self.assertEqual(self.machine.release(0, 3.1, True), [])

    def test_dial_button_with_the_dial_already_on_just_toggles(self):
        b = binding(press=action("dial"))
        self.assertEqual(kinds(self.machine.press(0, 1.0, b, dial_on=True)), [("action", 0, "dial")])
        self.machine.note_highlight()
        self.assertEqual(self.machine.release(0, 1.1, True), [])  # not an opener

    def test_group_button_opens_a_list_and_selects_on_release(self):
        b = binding(press=action("group"))
        self.assertEqual(kinds(self.machine.press(18, 1.0, b, dial_on=True)), [("action", 18, "group")])
        self.machine.note_highlight()
        self.assertEqual(kinds(self.machine.release(18, 1.6, True)), [("select", 18, None)])

    def test_hold_action_that_opens_a_group_selects_on_release(self):
        b = binding(press=action("function"), hold=action("group"))
        self.machine.press(18, 1.0, b, True)
        self.assertEqual(kinds(self.machine.tick(1.5, True)), [("action", 18, "group")])
        self.machine.note_highlight()
        self.assertEqual(kinds(self.machine.release(18, 2.0, True)), [("select", 18, None)])

    def test_hold_group_released_without_a_tilt_is_sticky(self):
        b = binding(press=action("function"), hold=action("group"))
        self.machine.press(18, 1.0, b, True)
        self.machine.tick(1.5, True)
        self.assertEqual(self.machine.release(18, 2.0, True), [])
        self.assertEqual(self.machine.opener, 18)
        self.assertEqual(kinds(self.machine.press(18, 4.0, b, True)), [("close", 18, None)])

    def test_another_button_ends_the_sticky_state(self):
        self.machine.press(0, 1.0, binding(press=action("dial")), False)
        self.machine.release(0, 1.1, True)
        self.machine.press(22, 2.0, binding(press=action("back")), True)
        self.assertIsNone(self.machine.opener)

    def test_tap_on_press_action_with_hold_that_opens_is_sticky(self):
        b = binding(press=action("group"), hold=action("macro"))
        self.machine.press(18, 1.0, b, True)
        self.assertEqual(kinds(self.machine.release(18, 1.1, True)), [("action", 18, "group")])
        self.assertEqual(self.machine.opener, 18)
        self.assertFalse(self.machine.opener_down)

    def test_dial_off_forgets_the_opener(self):
        self.machine.press(0, 1.0, binding(press=action("dial")), False)
        self.machine.release(0, 1.1, True)
        self.machine.dial_off()
        self.assertIsNone(self.machine.opener)

    def test_note_highlight_without_an_opener_is_ignored(self):
        self.machine.note_highlight()
        self.machine.press(0, 1.0, binding(press=action("dial")), False)
        self.assertFalse(self.machine.highlighted)


class HoldKeysAndNoneTest(unittest.TestCase):
    def setUp(self):
        self.machine = ButtonMachine(hold_ms=400)

    def test_key_hold_press_is_down_at_once_and_up_on_release(self):
        b = binding(press=action("key_hold", keys=["KEY_Z"]))
        self.assertEqual(kinds(self.machine.press(5, 1.0, b, True)), [("hold_down", 5, "key_hold")])
        self.assertEqual(self.machine.tick(3.0, True), [])
        self.assertEqual(kinds(self.machine.release(5, 3.5, True)), [("hold_up", 5, "key_hold")])

    def test_key_hold_wins_over_a_hold_action_that_is_also_there(self):
        b = binding(press=action("key_hold", keys=["KEY_Z"]), hold=action("group"))
        self.machine.press(5, 1.0, b, True)
        self.assertEqual(self.machine.tick(3.0, True), [])  # the keys are already down
        self.assertEqual(kinds(self.machine.release(5, 3.5, True)), [("hold_up", 5, "key_hold")])

    def test_key_hold_as_the_hold_action_goes_down_at_the_hold_time(self):
        b = binding(press=action("macro"), hold=action("key_hold", keys=["KEY_LEFTCTRL", "KEY_TAB"]))
        self.assertEqual(self.machine.press(5, 1.0, b, True), [])
        self.assertEqual(self.machine.tick(1.3, True), [])
        self.assertEqual(kinds(self.machine.tick(1.5, True)), [("hold_down", 5, "key_hold")])
        self.assertEqual(self.machine.tick(2.5, True), [])
        self.assertEqual(kinds(self.machine.release(5, 3.0, True)), [("hold_up", 5, "key_hold")])

    def test_tapping_a_button_with_a_key_hold_hold_action_runs_the_press(self):
        b = binding(press=action("macro"), hold=action("key_hold", keys=["KEY_Z"]))
        self.machine.press(5, 1.0, b, True)
        self.assertEqual(kinds(self.machine.release(5, 1.2, True)), [("action", 5, "macro")])

    def test_release_all_lets_go_of_held_keys(self):
        self.machine.press(5, 1.0, binding(press=action("key_hold", keys=["KEY_Z"])), True)
        self.machine.press(6, 1.0, binding(press=action("modifier", modifier="Ctrl")), True)
        self.assertEqual(sorted(kinds(self.machine.release_all())), [("hold_up", 5, "key_hold"), ("hold_up", 6, "modifier")])

    def test_none_is_nothing_and_a_hold_alone_still_works(self):
        self.assertEqual(self.machine.press(5, 1.0, binding(press=action("none"), hold=action("none")), True), [])
        self.assertEqual(self.machine.tick(2.0, True), [])
        self.assertEqual(self.machine.release(5, 2.1, True), [])
        self.assertFalse(self.machine.gesture_active)
        # A none press does not defer anything; a real hold still fires.
        b = binding(press=action("none"), hold=action("group"))
        self.machine.press(6, 3.0, b, True)
        self.assertEqual(kinds(self.machine.tick(3.5, True)), [("action", 6, "group")])
        self.machine.release(6, 3.6, True)
        # A real press next to a none hold acts at once.
        out = self.machine.press(7, 4.0, binding(press=action("macro"), hold=action("none")), True)
        self.assertEqual(kinds(out), [("action", 7, "macro")])

    def test_a_button_holding_keys_is_no_gesture(self):
        self.machine.press(5, 1.0, binding(press=action("key_hold", keys=["KEY_Z"])), True)
        self.assertFalse(self.machine.gesture_active)


class OpenerIsKeptTest(unittest.TestCase):
    """Only a gesture button ends a menu that waits for its opener."""

    def setUp(self):
        self.machine = ButtonMachine(hold_ms=400)
        self.machine.press(0, 1.0, binding(press=action("dial")), False)

    def test_a_modifier_pressed_while_the_menu_button_is_held_leaves_the_opener(self):
        self.machine.press(25, 1.2, binding(press=action("modifier", modifier="Ctrl")), True)
        self.machine.note_highlight()
        self.machine.release(25, 1.4, True)
        self.assertEqual(kinds(self.machine.release(0, 1.5, True)), [("select", 0, None)])

    def test_key_hold_and_a_hold_only_key_hold_leave_the_opener(self):
        self.machine.press(5, 1.2, binding(press=action("key_hold", keys=["KEY_Z"])), True)
        self.machine.press(6, 1.2, binding(hold=action("key_hold", keys=["KEY_X"])), True)
        self.assertEqual(self.machine.opener, 0)
        self.assertTrue(self.machine.opener_down)

    def test_a_button_left_to_other_programs_leaves_the_opener(self):
        self.machine.release(0, 1.1, True)  # sticky now
        self.machine.press(7, 2.0, binding(press=action("none"), hold=action("none")), True)
        self.machine.release(7, 2.1, True)
        self.machine.note_highlight()
        # The next press of the Menu button selects; it does not switch the dial off.
        self.assertEqual(kinds(self.machine.press(0, 3.0, binding(press=action("dial")), True)), [("select", 0, None)])

    def test_a_gesture_button_still_ends_the_sticky_menu(self):
        self.machine.release(0, 1.1, True)
        self.machine.press(22, 2.0, binding(press=action("back")), True)
        self.assertIsNone(self.machine.opener)

    def test_a_hold_action_that_is_not_keys_counts_as_a_gesture(self):
        self.machine.press(6, 1.2, binding(press=action("key_hold", keys=["KEY_X"]), hold=action("group")), True)
        self.assertEqual(self.machine.opener, 0)  # its press holds keys (the hold never fires): not a gesture
        self.machine.press(8, 1.3, binding(hold=action("group")), True)
        self.assertIsNone(self.machine.opener)


class CapTest(unittest.TestCase):
    """The raw axes of a SpacePilot Pro as measured with spacenavd: tilt right =
    axis 5 +, tilt towards you = axis 3 +, twist clockwise = axis 4 -, push
    down = axis 1 -."""

    def cap(self, **kwargs):
        return SpaceCap(**{"axis_max": 400, **kwargs})

    def tilt(self, cap, away_negative, right):
        """away_negative: axis 3 (negative = away from you = top); right: axis 5."""
        cap.set_axis(3, away_negative)
        cap.set_axis(5, right)

    def test_the_defaults_are_the_measured_axes(self):
        cap = self.cap()
        self.assertEqual((cap.tilt_axes, cap.twist_axis, cap.push_axis), ((5, 3), 4, 1))
        config = SpaceMouseConfig()
        self.assertEqual((config.tilt_axes, config.twist_axis, config.push_axis), ([5, 3], 4, 1))
        self.assertEqual((config.tilt_invert, config.twist_invert, config.push_invert), ([False, False], True, True))

    def test_tilt_directions_in_the_touch_convention(self):
        cap = self.cap()
        for away_negative, right, angle in ((-300, 0, 0), (0, 300, 90), (300, 0, 180), (0, -300, 270), (-300, 300, 45)):
            with self.subTest(angle=angle):
                self.tilt(cap, away_negative, right)
                self.assertAlmostEqual(cap.tilt_angle(), angle)

    def test_translation_axes_are_not_read_as_tilt(self):
        cap = self.cap()
        cap.set_axis(0, 185)   # the cross-talk of a tilt on X and Z
        cap.set_axis(2, -185)
        cap.set_axis(1, 0)
        self.assertIsNone(cap.tilt_angle())
        self.assertFalse(cap.twist_active())

    def test_tilt_inversion(self):
        cap = self.cap(tilt_invert=(False, True))
        self.tilt(cap, 300, 0)
        self.assertAlmostEqual(cap.tilt_angle(), 0)
        cap = self.cap(tilt_invert=(True, False))
        self.tilt(cap, 0, 300)
        self.assertAlmostEqual(cap.tilt_angle(), 270)

    def test_configurable_tilt_axes(self):
        cap = self.cap(tilt_axes=(0, 1))   # horizontal, vertical
        cap.set_axis(1, -300)
        self.assertAlmostEqual(cap.tilt_angle(), 0)
        cap.set_axis(1, 0)
        cap.set_axis(0, 300)
        self.assertAlmostEqual(cap.tilt_angle(), 90)

    def test_tilt_dead_zone_is_35_percent(self):
        cap = self.cap()
        self.tilt(cap, -139, 0)
        self.assertIsNone(cap.tilt_angle())
        self.tilt(cap, -141, 0)
        self.assertAlmostEqual(cap.tilt_angle(), 0)
        self.tilt(cap, 0, 0)
        self.assertIsNone(cap.tilt_angle())

    def test_twist_clockwise_is_negative_and_integrates_a_rate(self):
        cap = self.cap()
        cap.set_axis(4, -400)  # full twist clockwise
        cap.tick(0.0)
        self.assertTrue(cap.tick(0.1))
        # (400 - 60) / 400 * 360 deg/s = 306 deg/s
        self.assertAlmostEqual(cap.twist_rate, 306)
        self.assertAlmostEqual(cap.twist_angle, 30.6)
        cap.set_axis(4, 400)  # counterclockwise
        cap.tick(0.2)
        self.assertAlmostEqual(cap.twist_angle, 0.0, places=6)
        self.assertLess(cap.twist_rate, 0)

    def test_twist_dead_zone_and_speed(self):
        cap = self.cap(twist_speed=180)
        cap.set_axis(4, -59)
        cap.tick(0.0)
        self.assertFalse(cap.tick(0.1))
        self.assertEqual(cap.twist_angle, 0)
        cap.set_axis(4, -230)  # (230-60)/400 * 180 = 76.5 deg/s
        self.assertTrue(cap.tick(0.2))
        self.assertAlmostEqual(cap.twist_rate, 76.5)

    def test_twist_invert_and_long_pauses(self):
        cap = self.cap(twist_invert=False)
        cap.set_axis(4, -400)
        cap.tick(0.0)
        cap.tick(10.0)  # a long pause counts as at most MAX_TICK
        self.assertAlmostEqual(cap.twist_angle, (360 - 306 * SpaceCap.MAX_TICK) % 360)

    def test_tilt_dominates_twist_and_the_other_way_round(self):
        cap = self.cap()
        self.tilt(cap, -300, 0)
        cap.set_axis(4, -150)
        self.assertIsNotNone(cap.tilt_angle())
        self.assertFalse(cap.twist_active())  # |tilt| 300 > 1.5 * 150
        cap.set_axis(4, -250)
        self.assertTrue(cap.twist_active())
        self.assertIsNotNone(cap.tilt_angle())
        cap.set_axis(4, -400)
        self.assertIsNotNone(cap.tilt_angle())  # tilt 300 against twist 400: neither is 1.5 times the other
        self.assertTrue(cap.twist_active())

    def test_clearly_stronger_twist_hides_the_tilt(self):
        cap = self.cap()
        self.tilt(cap, -150, 0)
        cap.set_axis(4, -300)
        self.assertIsNone(cap.tilt_angle())
        self.assertTrue(cap.twist_active())

    def test_push_is_negative_with_hysteresis(self):
        cap = self.cap()
        self.assertIsNone(cap.set_axis(1, -199))
        self.assertEqual(cap.set_axis(1, -200), "push")
        self.assertIsNone(cap.set_axis(1, -180))  # still down
        self.assertIsNone(cap.set_axis(1, -150))
        self.assertTrue(cap.pushed)
        self.assertEqual(cap.set_axis(1, -139), "release")
        self.assertIsNone(cap.set_axis(1, -100))
        self.assertEqual(cap.set_axis(1, -260), "push")
        self.assertEqual(cap.set_axis(1, 400), "release")  # pulling up lets go and is no push
        self.assertFalse(cap.pushed)
        self.assertIsNone(cap.set_axis(1, 400))

    def test_push_invert(self):
        cap = self.cap(push_invert=False)
        self.assertIsNone(cap.set_axis(1, -250))
        self.assertEqual(cap.set_axis(1, 250), "push")

    def test_other_axes_do_not_push(self):
        cap = self.cap()
        self.assertIsNone(cap.set_axis(2, -400))
        self.assertIsNone(cap.set_axis(0, -400))

    def test_list_direction(self):
        cap = self.cap()
        self.tilt(cap, 300, 0)          # towards you: down the list
        self.assertEqual(cap.list_direction(), (1, 0.75))
        self.tilt(cap, -400, 0)         # away: up
        self.assertEqual(cap.list_direction(), (-1, 1.0))
        self.tilt(cap, 100, 0)
        self.assertEqual(cap.list_direction()[0], 0)
        self.tilt(cap, 200, 300)  # mostly sideways
        self.assertEqual(cap.list_direction()[0], 0)

    def test_reset(self):
        cap = self.cap()
        self.tilt(cap, -300, 0)
        cap.set_axis(1, -300)
        cap.reset()
        self.assertIsNone(cap.tilt_angle())
        self.assertFalse(cap.pushed)


class ListStepperTest(unittest.TestCase):
    def test_first_step_at_once_then_repeats(self):
        stepper = ListStepper()
        self.assertEqual(stepper.update(1, 0.4, 0.0), 1)
        self.assertEqual(stepper.update(1, 0.4, 0.1), 0)
        self.assertEqual(stepper.update(1, 0.4, 0.2), 0)
        self.assertEqual(stepper.update(1, 0.4, 0.26), 1)
        self.assertEqual(stepper.update(1, 0.4, 0.3), 0)

    def test_faster_at_full_deflection(self):
        stepper = ListStepper()
        self.assertAlmostEqual(stepper.interval(0.35), 0.25)
        self.assertAlmostEqual(stepper.interval(1.0), 0.08)
        self.assertAlmostEqual(stepper.interval(0.1), 0.25)
        self.assertLess(stepper.interval(0.8), stepper.interval(0.5))
        stepper.update(1, 1.0, 0.0)
        self.assertEqual(stepper.update(1, 1.0, 0.09), 1)

    def test_release_and_reverse(self):
        stepper = ListStepper()
        stepper.update(1, 0.5, 0.0)
        self.assertEqual(stepper.update(0, 0.0, 0.05), 0)
        self.assertEqual(stepper.update(1, 0.5, 0.06), 1)  # a new tilt steps at once
        self.assertEqual(stepper.update(-1, 0.5, 0.07), -1)


class SpaceMouseConfigTest(unittest.TestCase):
    def test_defaults_and_old_files(self):
        config = DialPadConfig()
        self.assertEqual(config.spacemouse, SpaceMouseConfig())
        self.assertFalse(config.spacemouse.enabled)
        self.assertEqual((config.spacemouse.hold_ms, config.spacemouse.axis_max), (400, 400))
        self.assertEqual((config.spacemouse.tilt_axes, config.spacemouse.twist_axis, config.spacemouse.push_axis), ([5, 3], 4, 1))
        self.assertTrue(config.spacemouse.mute_others)
        self.assertFalse(config.spacemouse.hide_buttons)
        data = _encoder(config)
        del data["spacemouse"]
        self.assertEqual(_decoder(data, DialPadConfig).spacemouse, SpaceMouseConfig())

    def test_round_trip_with_buttons_and_profile_overrides(self):
        config = DialPadConfig()
        config.spacemouse = SpaceMouseConfig(
            enabled=True, socket="/run/x.sock", hold_ms=300, tilt_invert=[True, False], twist_invert=True,
            buttons={
                "3": SpaceMouseButton(
                    press=SpaceMouseAction("macro", keys=["KEY_LEFTCTRL", "KEY_Z"]),
                    hold=SpaceMouseAction("group", group="Display"),
                ),
                "5": SpaceMouseButton(press=SpaceMouseAction("macro", macro=[MacroStep(type=StepType.KEYSTROKE, keys=["KEY_F5"])])),
                "17": SpaceMouseButton(press=SpaceMouseAction("function", function="Volume")),
                "25": SpaceMouseButton(press=SpaceMouseAction("modifier", modifier="Ctrl")),
            },
        )
        config.profiles = [Profile(name="Krita", app_id="k.desktop", spacemouse_buttons={
            "0": SpaceMouseButton(press=SpaceMouseAction("function", slot=1)),
        })]
        decoded = _decoder(_encoder(config), DialPadConfig)
        self.assertEqual(decoded.spacemouse, config.spacemouse)
        self.assertEqual(decoded.profiles[0].spacemouse_buttons, config.profiles[0].spacemouse_buttons)
        self.assertEqual(len(spacemouse_macros(decoded)), 2)
        self.assertEqual(spacemouse_modifier_keys(decoded), {"KEY_LEFTCTRL"})

    def test_bad_values_are_repaired(self):
        data = _encoder(DialPadConfig())
        data["spacemouse"] = {
            "enabled": 1, "hold_ms": 5, "axis_max": 0, "tilt_axes": [9, "x"], "tilt_invert": [1],
            "twist_axis": 99, "twist_speed": 100000, "socket": None,
            "buttons": {
                "x": {"press": {"type": "dial"}}, "99": {"press": {"type": "dial"}},
                "4": {"press": {"type": "teleport"}}, "6": "junk",
                "7": {"press": {"type": "modifier", "modifier": "Hyper"}, "hold": {"type": "back"}},
                "8": {"press": {"type": "function", "slot": -1, "function": ""}},
            },
        }
        decoded = _decoder(data, DialPadConfig).spacemouse
        self.assertTrue(decoded.enabled)
        self.assertEqual((decoded.hold_ms, decoded.axis_max, decoded.twist_speed, decoded.twist_axis), (100, 50, 1440, 7))
        self.assertEqual((decoded.tilt_axes, decoded.tilt_invert, decoded.socket), ([7, 3], [False, False], ""))
        self.assertEqual(sorted(decoded.buttons), ["7", "8"])
        self.assertIsNone(decoded.buttons["7"].press.modifier)
        self.assertEqual(decoded.buttons["7"].hold.type, "back")
        self.assertIsNone(decoded.buttons["8"].press.slot)
        self.assertIsNone(decoded.buttons["8"].press.function)

    def test_default_button_map_of_the_spacepilot_pro(self):
        buttons = default_spacemouse_buttons()
        self.assertEqual(buttons["0"].press.type, "dial")
        # The function keys are dual: a short press is 12..16, the device sends 17..21 for a long one.
        for number in range(5):
            short, long = buttons[str(12 + number)], buttons[str(17 + number)]
            self.assertEqual((short.press.type, short.press.slot), ("function", number))
            self.assertEqual((long.press.type, long.press.slot), ("function", 5 + number))
            self.assertIsNone(short.hold)
            self.assertIsNone(long.hold)
        self.assertEqual(buttons["22"].press.type, "back")
        self.assertEqual([buttons[str(index)].press.modifier for index in (23, 24, 25)], ["Alt", "Shift", "Ctrl"])
        # The cap's motion for other programs: Rotation, Pan/Zoom, +, -.
        self.assertEqual(
            [(buttons[str(index)].press.type, buttons[str(index)].press.motion) for index in (26, 27, 29, 30)],
            [("motion", "rotation"), ("motion", "translation"), ("motion", "faster"), ("motion", "slower")],
        )
        self.assertNotIn("28", buttons)
        self.assertEqual(len(buttons), 19)
        # No layer is bound by default.
        self.assertFalse(any(
            action is not None and action.type == "layer"
            for button in buttons.values() for action in (button.press, button.hold)
        ))

    def test_imported_shell_steps_of_spacemouse_buttons_are_listed(self):
        shell = SpaceMouseAction("macro", macro=[MacroStep(type=StepType.SHELL, script="rm -rf ~/x")])
        program = SpaceMouseAction("macro", macro=[MacroStep(type=StepType.COMMAND, program="kitty", args=["-e", "top"])])
        keys = SpaceMouseAction("macro", keys=["KEY_F5"])
        profile = Profile(name="Shared", spacemouse_buttons={
            "3": SpaceMouseButton(press=shell, hold=program),
            "4": SpaceMouseButton(press=keys),
        })
        self.assertEqual(shell_commands([profile]), [
            ("Shared", "SpaceMouse button 3", "rm -rf ~/x"),
            ("Shared", "SpaceMouse button 3 (hold)", "kitty -e top"),
        ])
        # The global buttons too, where a whole configuration is checked.
        self.assertEqual(
            shell_commands([], {"9": SpaceMouseButton(hold=shell)}),
            [("SpaceMouse", "SpaceMouse button 9 (hold)", "rm -rf ~/x")],
        )
        self.assertEqual(shell_commands([Profile(name="Plain")]), [])

    def test_a_shared_profile_with_spacemouse_buttons_is_read_and_checked(self):
        from config.profile_share import PROFILE_FORMAT, profiles_from_text
        text = json.dumps({
            "name": "Shared",
            "segments": [{"title": "Zoom", "macro_cw": [{"type": "keystroke", "keys": ["KEY_EQUAL"]}]}],
            "spacemouse_buttons": {"5": {"press": {"type": "macro", "macro": [{"type": "shell", "script": "id"}]}}},
        })
        (profile,) = profiles_from_text(text)
        self.assertEqual(shell_commands([profile]), [("Shared", "SpaceMouse button 5", "id")])
        self.assertIn("spacemouse_buttons", PROFILE_FORMAT)
        for action in ("macro", "modifier", "dial", "group", "function", "select", "back"):
            self.assertIn(f'"type": "{action}"', PROFILE_FORMAT)

    def test_key_hold_and_none_round_trip_and_bad_ones_are_dropped(self):
        config = DialPadConfig()
        config.spacemouse.buttons = {
            "3": SpaceMouseButton(press=SpaceMouseAction("key_hold", keys=["KEY_LEFTCTRL", "KEY_TAB"])),
            "4": SpaceMouseButton(press=SpaceMouseAction("macro", keys=["KEY_F5"]), hold=SpaceMouseAction("key_hold", keys=["KEY_Z"])),
        }
        config.profiles = [Profile(name="Blender", spacemouse_buttons={
            "22": SpaceMouseButton(press=SpaceMouseAction("none")),
            "5": SpaceMouseButton(press=SpaceMouseAction("none"), hold=SpaceMouseAction("none")),
        })]
        decoded = _decoder(_encoder(config), DialPadConfig)
        self.assertEqual(decoded.spacemouse.buttons, config.spacemouse.buttons)
        self.assertEqual(decoded.profiles[0].spacemouse_buttons, config.profiles[0].spacemouse_buttons)
        self.assertEqual(spacemouse_hold_keys(decoded), {"KEY_LEFTCTRL", "KEY_TAB", "KEY_Z"})
        data = _encoder(DialPadConfig())
        data["spacemouse"] = {"buttons": {"2": {"press": {"type": "key_hold"}}, "3": {"press": {"type": "key_hold", "keys": []}}}}
        self.assertEqual(_decoder(data, DialPadConfig).spacemouse.buttons, {})

    def test_which_buttons_turnpad_acts_on(self):
        self.assertFalse(spacemouse_binds(None))
        self.assertFalse(spacemouse_binds(SpaceMouseButton(press=SpaceMouseAction("none"), hold=SpaceMouseAction("none"))))
        self.assertTrue(spacemouse_binds(SpaceMouseButton(press=SpaceMouseAction("none"), hold=SpaceMouseAction("group"))))
        self.assertTrue(spacemouse_binds(SpaceMouseButton(press=SpaceMouseAction("key_hold", keys=["KEY_Z"]))))

    def test_a_label_only_entry_names_the_binding_below_it(self):
        base = {
            "1": SpaceMouseButton(press=SpaceMouseAction("dial"), label="Old"),
            "2": SpaceMouseButton(press=SpaceMouseAction("back"), hold=SpaceMouseAction("select")),
        }
        over = {
            "1": SpaceMouseButton(label="Extrude"),
            "2": SpaceMouseButton(press=SpaceMouseAction("none")),
            "3": SpaceMouseButton(label="Nothing below"),
            "4": SpaceMouseButton(press=SpaceMouseAction("dial")),
        }
        merged = merge_spacemouse_buttons(base, over)
        self.assertEqual(merged["1"], SpaceMouseButton(press=SpaceMouseAction("dial"), label="Extrude"))
        self.assertEqual(merged["2"], over["2"])  # actions replace the binding below, the hold too
        self.assertEqual(merged["3"], over["3"])  # no binding below: stays label-only, unbound
        self.assertFalse(spacemouse_binds(merged["3"]))
        self.assertEqual(merged["4"], over["4"])
        # A label-only entry without a label of its own keeps the one below.
        self.assertEqual(merge_spacemouse_buttons(base, {"1": SpaceMouseButton()})["1"].label, "Old")
        self.assertEqual(base["1"].label, "Old")  # the inputs are not changed

    def test_global_labels_do_not_drop_the_default_map(self):
        defaults = default_spacemouse_buttons()
        config = DialPadConfig()
        config.spacemouse.buttons = {"12": SpaceMouseButton(label="Extrude")}
        buttons = spacemouse_buttons_for(config)
        self.assertEqual(set(buttons), set(defaults))
        self.assertEqual(buttons["12"].press, defaults["12"].press)
        self.assertEqual(buttons["12"].label, "Extrude")
        self.assertEqual({k: b for k, b in buttons.items() if k != "12"}, {k: b for k, b in defaults.items() if k != "12"})
        # A label on a button the defaults leave unbound stays unbound.
        config.spacemouse.buttons = {"28": SpaceMouseButton(label="Dominant")}
        self.assertFalse(spacemouse_binds(spacemouse_buttons_for(config)["28"]))
        # One entry with an action replaces the defaults; labels are laid over that map.
        config.spacemouse.buttons = {"5": SpaceMouseButton(press=SpaceMouseAction("dial")), "12": SpaceMouseButton(label="Extrude")}
        buttons = spacemouse_buttons_for(config)
        self.assertEqual(set(buttons), {"5", "12"})
        self.assertFalse(spacemouse_binds(buttons["12"]))
        config.spacemouse.buttons = {}
        self.assertEqual(spacemouse_buttons_for(config), defaults)

    def test_a_shared_profile_may_just_label_a_button(self):
        from config.profile_share import PROFILE_FORMAT, profiles_from_text

        (profile,) = profiles_from_text(json.dumps({
            "name": "P", "segments": [], "spacemouse_buttons": {"12": {"label": "Extrude"}},
        }))
        self.assertEqual(profile.spacemouse_buttons, {"12": SpaceMouseButton(label="Extrude")})
        self.assertEqual(shell_commands([profile]), [])
        self.assertIn("with only a \"label\" just names the existing binding", PROFILE_FORMAT)

    def test_key_hold_and_none_have_no_commands_to_ask_about(self):
        profile = Profile(name="P", spacemouse_buttons={
            "1": SpaceMouseButton(press=SpaceMouseAction("key_hold", keys=["KEY_Z"]), hold=SpaceMouseAction("none")),
        })
        self.assertEqual(shell_commands([profile]), [])

    def test_spacemouse_keys_of_a_shared_profile_are_normalised_or_rejected(self):
        from config.profile_share import ProfileTextError, profiles_from_text

        def profile(**buttons):
            return json.dumps({"name": "P", "segments": [], "spacemouse_buttons": buttons})

        (read,) = profiles_from_text(profile(**{
            "3": {"press": {"type": "key_hold", "keys": ["Z"]}, "hold": {"type": "macro", "keys": ["Ctrl", "f5"]}},
        }))
        self.assertEqual(read.spacemouse_buttons["3"].press.keys, ["KEY_Z"])
        self.assertEqual(read.spacemouse_buttons["3"].hold.keys, ["KEY_LEFTCTRL", "KEY_F5"])
        for bad in (
            {"3": {"press": {"type": "key_hold", "keys": ["Nope"]}}},
            {"3": {"press": {"type": "key_hold"}}},
            {"3": {"press": {"type": "teleport"}}},
            {"3": {"press": {"type": "macro", "macro": [{"type": "keystroke", "keys": ["Nope"]}]}}},
            {"3": "junk"},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ProfileTextError):
                    profiles_from_text(profile(**bad))

    def test_a_profile_made_for_spacemouse_buttons_alone_stays_active(self):
        from config.legacy_adapter import META_KEY, shortcuts_from_config

        config = DialPadConfig(profiles=[
            Profile(name="Default", segments=[test_driver_state_volume()]),
            Profile(name="Blender", app_id="org.blender.Blender.desktop", segments=[], spacemouse_buttons={
                "22": SpaceMouseButton(press=SpaceMouseAction("none")),
            }),
            Profile(name="Empty", app_id="empty.desktop", segments=[]),
        ])
        shortcuts = shortcuts_from_config(config)
        blender = shortcuts["org.blender.Blender.desktop"]
        self.assertEqual(blender[META_KEY]["name"], "Blender")
        self.assertEqual(blender[META_KEY]["spacemouse_buttons"], config.profiles[1].spacemouse_buttons)
        self.assertIn("Volume", blender)  # the dial keeps the default menu
        self.assertNotIn("spacemouse_buttons", shortcuts["none"][META_KEY])
        self.assertNotIn("empty.desktop", shortcuts)  # an unfinished profile without buttons still falls back

    def test_a_default_profile_with_only_buttons_has_an_empty_dial(self):
        from config.legacy_adapter import META_KEY, shortcuts_from_config

        config = DialPadConfig(profiles=[
            Profile(name="Buttons", segments=[], spacemouse_buttons={"1": SpaceMouseButton(press=SpaceMouseAction("dial"))}),
        ])
        none = shortcuts_from_config(config)["none"]
        self.assertEqual(none[META_KEY]["name"], "Buttons")
        self.assertIn("1", none[META_KEY]["spacemouse_buttons"])

    def test_key_chord_is_a_keystroke(self):
        steps = SpaceMouseAction("macro", keys=["KEY_LEFTCTRL", "KEY_Z"]).steps()
        self.assertEqual([(step.type, step.keys) for step in steps], [(StepType.KEYSTROKE, ["KEY_LEFTCTRL", "KEY_Z"])])
        self.assertEqual(SpaceMouseAction("macro").steps(), [])


class MuteLeaseTest(unittest.TestCase):
    def test_no_lease_by_default(self):
        lease = MuteLease()
        self.assertFalse(lease.active(0))
        self.assertFalse(lease.active(1e9))

    def test_runs_for_the_asked_time(self):
        lease = MuteLease()
        lease.grant(3000, 100.0)
        for now, active in ((100.0, True), (102.9, True), (103.0, False), (200.0, False)):
            self.assertEqual(lease.active(now), active, now)

    def test_zero_ends_it_and_a_new_grant_replaces_it(self):
        lease = MuteLease()
        lease.grant(3000, 10.0)
        lease.grant(0, 11.0)
        self.assertFalse(lease.active(11.0))
        lease.grant(1000, 20.0)
        lease.grant(3000, 20.5)
        self.assertTrue(lease.active(23.0))

    def test_never_longer_than_the_maximum(self):
        lease = MuteLease()
        lease.grant(10 ** 9, 0.0)
        self.assertTrue(lease.active(4.9))
        self.assertFalse(lease.active(5.1))
        lease.grant(-5, 0.0)
        self.assertFalse(lease.active(0.0))

    def test_a_clock_that_jumped_back_does_not_stretch_it(self):
        lease = MuteLease()
        lease.grant(3000, 1000.0)
        self.assertFalse(lease.active(100.0))  # 900 s "ahead": gone, not a 15 minute mute

    def test_wanted_by_the_dial_or_a_lease(self):
        lease = MuteLease()
        self.assertFalse(lease.wanted(False, True, 0))
        self.assertFalse(lease.wanted(True, False, 0))  # the dial alone, but the setting says no
        self.assertTrue(lease.wanted(True, True, 0))
        lease.grant(2000, 5.0)
        self.assertTrue(lease.wanted(False, False, 6.0))  # the lease needs neither
        self.assertTrue(lease.wanted(True, True, 6.0))
        self.assertFalse(lease.wanted(False, True, 7.5))
        self.assertTrue(lease.wanted(True, True, 7.5))


if __name__ == "__main__":
    unittest.main()
