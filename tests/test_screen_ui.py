"""screen/ui.py: pages, keys, overlays, idle timers."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.schema import ScreenConfig
from screen import ui as screen_ui
from screen.ui import REPEAT_DELAY, REPEAT_INTERVAL, TOAST_SECONDS, ScreenUI

FACES = [12, 17, 13, 18, 14, 19, 15, 20, 16, 21]


def make(**overrides):
    settings = ScreenConfig(**overrides)
    machine = ScreenUI(settings, 0.0)
    machine.set_context(FACES, device_steps=3)
    machine.start(0.0)
    return machine


def controls(effects):
    return [effect[1] for effect in effects if effect[0] == "control"]


class PageTests(unittest.TestCase):
    def test_starts_on_start_page(self):
        self.assertEqual(make(start_page="clock").page, "clock")

    def test_left_right_cycle_pages(self):
        machine = make()
        machine.key_down("right", 1)
        self.assertEqual(machine.page, "dial")
        machine.key_down("left", 2)
        machine.key_down("left", 3)
        self.assertEqual(machine.page, "clock")

    def test_back_goes_to_start_page_or_dial_back(self):
        machine = make()
        machine.go_page("device")
        machine.key_down("back", 1)
        self.assertEqual(machine.page, "keys")
        machine.go_page("dial")
        machine.set_dial(True, 2)
        self.assertEqual(controls(machine.key_down("back", 3)), ["back"])
        self.assertEqual(machine.page, "dial")

    def test_follow_dial_returns_to_previous_page(self):
        machine = make()
        machine.go_page("device")
        machine.set_dial(True, 1)
        self.assertEqual(machine.page, "dial")
        machine.set_dial(False, 2)
        self.assertEqual(machine.page, "device")

    def test_follow_dial_off_or_page_hidden(self):
        machine = make(follow_dial=False)
        machine.set_dial(True, 1)
        self.assertEqual(machine.page, "keys")
        machine = make(pages=["keys", "clock"])
        machine.set_dial(True, 1)
        self.assertEqual(machine.page, "keys")

    def test_user_navigation_while_dial_is_on_forgets_previous(self):
        machine = make()
        machine.set_dial(True, 1)
        machine.key_down("right", 2)
        machine.set_dial(False, 3)
        self.assertEqual(machine.page, "device")

    def test_apply_config_keeps_page_unless_hidden(self):
        machine = make()
        machine.go_page("motion")
        machine.apply_config(ScreenConfig(pages=["keys", "motion"]))
        self.assertEqual(machine.page, "motion")
        machine.apply_config(ScreenConfig(pages=["keys", "clock"], start_page="clock"))
        self.assertEqual(machine.page, "clock")

    def test_apply_config_keeps_pending_fields(self):
        machine = make()
        machine.settings.theme = "light"
        machine.apply_config(ScreenConfig(theme="dark"), keep=("theme",))
        self.assertEqual(machine.settings.theme, "light")


class KeysPageTests(unittest.TestCase):
    def test_up_down_move_highlight_and_ok_runs_button(self):
        machine = make()
        self.assertIsNone(machine.view().highlight)
        machine.key_down("down", 1)
        self.assertEqual(machine.highlight, 0)
        machine.key_down("down", 2)
        self.assertEqual(controls(machine.key_down("ok", 3)), ["button:17"])
        machine.key_down("up", 4)
        self.assertEqual(controls(machine.key_down("ok", 5)), ["button:12"])
        machine.key_down("up", 6)
        self.assertEqual(machine.highlight, 0)

    def test_highlight_stops_at_end(self):
        machine = make()
        for step in range(15):
            machine.key_down("down", step + 1)
            machine.key_up("down", step + 1)
        self.assertEqual(machine.highlight, 9)

    def test_ok_without_highlight_does_nothing(self):
        self.assertEqual(controls(make().key_down("ok", 1)), [])


class DialAndDeviceTests(unittest.TestCase):
    def test_dial_page_keys(self):
        machine = make()
        machine.go_page("dial")
        self.assertEqual(controls(machine.key_down("up", 1)), [])  # dial off: nothing to turn
        self.assertEqual(controls(machine.key_down("ok", 2)), ["dial-on"])
        machine.set_dial(True, 3)
        self.assertEqual(controls(machine.key_down("up", 4)), ["rotate:-1"])
        machine.key_up("up", 4.1)
        self.assertEqual(controls(machine.key_down("down", 5)), ["rotate:1"])
        machine.key_up("down", 5.1)
        self.assertEqual(controls(machine.key_down("ok", 6)), ["select"])

    def test_device_page_scrolls_within_range(self):
        machine = make()
        machine.go_page("device")
        for step in range(6):
            machine.key_down("down", step)
            machine.key_up("down", step)
        self.assertEqual(machine.scroll, 3)
        machine.key_down("up", 10)
        self.assertEqual(machine.scroll, 2)


class OverlayTests(unittest.TestCase):
    def test_picker_chooses_page(self):
        machine = make()
        machine.key_down("menu", 1)
        self.assertEqual(machine.overlay, "picker")
        self.assertEqual(machine.overlay_index, 0)
        machine.key_down("down", 2)
        machine.key_down("down", 3)
        machine.key_down("ok", 4)
        self.assertIsNone(machine.overlay)
        self.assertEqual(machine.page, "device")

    def test_picker_closes_with_menu_and_back_and_ignores_left_right(self):
        machine = make()
        machine.key_down("menu", 1)
        machine.key_down("right", 2)
        self.assertEqual(machine.page, "keys")
        machine.key_down("back", 3)
        self.assertIsNone(machine.overlay)
        machine.key_down("menu", 4)
        machine.key_down("menu", 5)
        self.assertIsNone(machine.overlay)

    def test_settings_change_values_and_save(self):
        machine = make(brightness=70)
        machine.key_down("settings", 1)
        self.assertEqual(machine.overlay, "settings")
        effects = machine.key_down("right", 2)
        self.assertIn(("save", {"brightness": 80}), effects)
        self.assertIn(("brightness", 80), effects)
        machine.key_down("left", 3)
        machine.key_down("left", 4)
        self.assertEqual(machine.settings.brightness, 60)
        machine.key_down("down", 5)  # dim after
        effects = machine.key_down("right", 6)
        self.assertEqual(effects[0][0], "save")
        self.assertEqual(effects[0][1]["dim_after_s"], 600)

    def test_settings_toggle_and_enum_rows(self):
        machine = make()
        machine.key_down("settings", 1)
        for _ in range(2):
            machine.key_down("down", 2)
        effects = machine.key_down("right", 3)
        self.assertEqual(effects[0], ("save", {"theme": "dark"}))
        machine.key_down("down", 4)
        machine.key_down("ok", 5)
        self.assertEqual(machine.settings.text_size, "large")
        machine.key_down("down", 6)
        machine.key_down("ok", 7)
        self.assertFalse(machine.settings.show_long)
        machine.key_down("down", 8)
        machine.key_down("down", 9)
        effects = machine.key_down("right", 10)
        self.assertEqual(effects[0], ("save", {"start_page": "dial"}))
        rows = dict(machine.view().settings_rows)
        self.assertEqual(rows["Start page"], "Dial")
        self.assertEqual(rows["Long-press labels"], "Off")
        self.assertEqual(rows["Text size"], "Large")

    def test_settings_row_labels(self):
        rows = dict(make(brightness=70, dim_after_s=300).view().settings_rows)
        self.assertEqual(rows["Brightness"], "70 %")
        self.assertEqual(rows["Dim after"], "5 min")
        self.assertEqual(rows["Follow the dial"], "On")
        self.assertEqual(screen_ui.format_seconds(0), "Never")
        self.assertEqual(screen_ui.format_seconds(30), "30 s")

    def test_settings_and_picker_replace_each_other(self):
        machine = make()
        machine.key_down("menu", 1)
        machine.key_down("settings", 2)
        self.assertEqual(machine.overlay, "settings")
        machine.key_down("settings", 3)
        self.assertIsNone(machine.overlay)

    def test_dial_on_closes_overlay(self):
        machine = make()
        machine.key_down("menu", 1)
        machine.set_dial(True, 2)
        self.assertIsNone(machine.overlay)


class BrightnessTests(unittest.TestCase):
    def test_light_key_cycles_and_saves(self):
        machine = make(brightness=100)
        seen = []
        for step in range(5):
            effects = machine.key_down("light", step + 1)
            saved = [e[1]["brightness"] for e in effects if e[0] == "save"]
            seen.append(saved[0])
        self.assertEqual(seen, [70, 40, 15, 0, 100])
        self.assertIn(("brightness", 70), make(brightness=100).key_down("light", 1))

    def test_light_from_odd_value_goes_to_next_lower_step(self):
        machine = make(brightness=55)
        machine.key_down("light", 1)
        self.assertEqual(machine.settings.brightness, 40)

    def test_start_sets_brightness_once(self):
        machine = ScreenUI(ScreenConfig(brightness=33), 0)
        self.assertIn(("brightness", 33), machine.start(0))


class IdleTests(unittest.TestCase):
    def test_dim_then_off_then_wake(self):
        machine = make(dim_after_s=10, dim_brightness=5, off_after_s=30, brightness=70)
        self.assertEqual(machine.tick(5), [])
        self.assertIn(("brightness", 5), machine.tick(10))
        self.assertEqual(machine.power, "dim")
        self.assertIn(("brightness", 0), machine.tick(30))
        self.assertEqual(machine.power, "off")
        effects = machine.activity(31, "button")
        self.assertIn(("brightness", 70), effects)
        self.assertEqual(machine.power, "awake")

    def test_never_with_zero(self):
        machine = make(dim_after_s=0, off_after_s=0)
        self.assertEqual(machine.tick(100000), [])
        self.assertIsNone(machine.next_deadline(0))

    def test_dim_never_brightens(self):
        machine = make(dim_after_s=10, dim_brightness=50, brightness=20)
        machine.tick(10)
        self.assertEqual(machine.power, "dim")
        self.assertEqual(machine.target_level(), 20)

    def test_first_key_press_only_wakes(self):
        machine = make(off_after_s=10, dim_after_s=0)
        machine.tick(10)
        self.assertEqual(machine.power, "off")
        machine.key_down("right", 11)
        self.assertEqual(machine.page, "keys")
        self.assertEqual(machine.power, "awake")
        machine.key_down("right", 12)
        self.assertEqual(machine.page, "dial")

    def test_dim_screen_key_acts_and_wakes(self):
        machine = make(dim_after_s=10)
        machine.tick(10)
        machine.key_down("right", 11)
        self.assertEqual(machine.page, "dial")
        self.assertEqual(machine.power, "awake")

    def test_motion_wake_is_optional(self):
        machine = make(dim_after_s=10, wake_on_motion=False)
        machine.tick(10)
        self.assertEqual(machine.activity(11, "motion"), [])
        self.assertEqual(machine.power, "dim")
        machine = make(dim_after_s=10, wake_on_motion=True)
        machine.tick(10)
        machine.activity(11, "motion")
        self.assertEqual(machine.power, "awake")

    def test_activity_postpones_dimming(self):
        machine = make(dim_after_s=10)
        machine.activity(8, "button")
        machine.tick(12)
        self.assertEqual(machine.power, "awake")
        self.assertEqual(machine.next_deadline(12), 18)


class ToastAndRepeatTests(unittest.TestCase):
    def test_feedback_toast_expires(self):
        machine = make()
        machine.feedback(1.0, 12, "Zoom", "Ctrl+Z", "#ffffff")
        view = machine.view(1.2)
        self.assertEqual(view.toast.title, "Zoom")
        self.assertIn(12, view.flashes)
        self.assertNotIn(12, machine.view(1.5).flashes)
        self.assertIsNone(machine.view(1.0 + TOAST_SECONDS + 0.1).toast)
        self.assertIn(("redraw",), machine.tick(1.0 + TOAST_SECONDS + 0.1))
        self.assertIsNone(machine.toast)

    def test_feedback_off(self):
        machine = make(press_feedback=False)
        self.assertEqual(machine.feedback(1, 12, "Zoom"), [])
        self.assertIsNone(machine.toast)

    def test_auto_repeat_up_down(self):
        machine = make()
        machine.go_page("dial")
        machine.set_dial(True, 0.5)
        machine.key_down("down", 1.0)
        self.assertEqual(controls(machine.tick(1.0 + REPEAT_DELAY - 0.05)), [])
        self.assertEqual(controls(machine.tick(1.0 + REPEAT_DELAY)), ["rotate:1"])
        self.assertEqual(controls(machine.tick(1.0 + REPEAT_DELAY + REPEAT_INTERVAL)), ["rotate:1"])
        machine.key_up("down", 1.7)
        self.assertEqual(controls(machine.tick(5.0)), [])

    def test_late_tick_repeats_once(self):
        machine = make()
        machine.go_page("dial")
        machine.set_dial(True, 0.5)
        machine.key_down("down", 1.0)
        self.assertEqual(len(controls(machine.tick(10.0))), 1)

    def test_keys_changed_from_masks(self):
        machine = make()
        bits = {"right": 0x10, "left": 0x20}
        machine.keys_changed(0, 0x10, bits, 1)
        self.assertEqual(machine.page, "dial")
        machine.keys_changed(0x10, 0, bits, 2)
        machine.keys_changed(0, 0x20, bits, 3)
        self.assertEqual(machine.page, "keys")

    def test_unknown_key_ignored(self):
        self.assertEqual(make().key_down("bogus", 1), [])


class NotifyTests(unittest.TestCase):
    def test_a_notice_shows_whatever_press_feedback_says(self):
        machine = make(press_feedback=False)
        effects = machine.notify(1.0, "Rotation off")
        self.assertEqual(machine.view(1.1).toast.title, "Rotation off")
        self.assertIn(("redraw",), effects)
        self.assertIsNone(machine.view(1.0 + screen_ui.NOTICE_SECONDS + 0.1).toast)
        self.assertGreater(screen_ui.NOTICE_SECONDS, TOAST_SECONDS)

    def test_it_wakes_a_dim_screen_and_waits_for_the_game(self):
        machine = make(dim_after_s=30)
        machine.tick(31.0)
        self.assertEqual(machine.power, "dim")
        effects = machine.notify(32.0, "Hello")
        self.assertEqual(machine.power, "awake")
        self.assertTrue(any(effect[0] == "brightness" for effect in effects))
        self.assertEqual(machine.notify(33.0, ""), [])

    def test_a_running_game_is_left_alone(self):
        machine = make()
        machine.game = object()
        self.assertEqual(machine.notify(1.0, "Hello"), [])
        self.assertIsNone(machine.toast)


if __name__ == "__main__":
    unittest.main()
