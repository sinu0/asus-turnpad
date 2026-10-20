"""Hardware detection of the settings app (config_app/hardware.py): the mouse
list, key capture helpers, what is found, the suggested modes and which pages
are useful, all from texts and temporary folders (no hardware)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from config.schema import _default_config  # noqa: E402
from config_app import hardware as hw  # noqa: E402
from test_mouse_dial import DEVICES, OWN_DEVICE  # noqa: E402

TOUCHPAD_ASUS = '''I: Bus=0018 Vendor=2808 Product=0220 Version=0100
N: Name="ASCF1A00:00 2808:0220 Touchpad"
H: Handlers=mouse3 event7
B: EV=b
B: KEY=e520 10000 0 0 0 0
B: ABS=2e0800000000000

'''
TOUCHPAD_PLAIN = '''I: Bus=0018 Vendor=06cb Product=cd7e Version=0100
N: Name="SYNA8004:00 06CB:CD8B Touchpad"
H: Handlers=mouse4 event8
B: EV=b
B: KEY=e520 10000 0 0 0 0
B: ABS=2e0800000000000

'''
TOUCHPAD_ELAN = TOUCHPAD_PLAIN.replace("SYNA8004:00 06CB:CD8B", "ELAN1200:00 04F3:3000")
ROWS = [{"Laptop": "ProArt PX13", "Vendor ID": "2808", "Product ID": "0220", "Base layout": "proartp16", "Event count": "5"}]


def sysfs(root, entries):
    """Fake /sys/bus/usb/devices: entries are (name, vendor, product, product name)."""
    for name, vendor, product, title in entries:
        folder = Path(root) / name
        folder.mkdir(parents=True)
        (folder / "idVendor").write_text(vendor + "\n")
        (folder / "idProduct").write_text(product + "\n")
        (folder / "busnum").write_text("3\n")
        (folder / "devnum").write_text("4\n")
        if title:
            (folder / "product").write_text(title + "\n")


class MouseListTest(unittest.TestCase):
    def test_wheel_pointers_only(self):
        names = [mouse.name for mouse in hw.list_mice(DEVICES)]
        self.assertEqual(names, ["Logitech G604 ", "Plain Mouse"])

    def test_labels_collapse_padding_and_the_value_keeps_it(self):
        mouse = hw.list_mice(DEVICES)[0]
        self.assertEqual(mouse.label, "Logitech G604")
        self.assertEqual(mouse.name, "Logitech G604 ")
        self.assertEqual(mouse.nodes, ("event2",))

    def test_own_virtual_devices_are_left_out(self):
        self.assertEqual(len(hw.list_mice(OWN_DEVICE + "\n" + DEVICES)), 2)

    def test_same_name_is_one_entry_with_all_nodes(self):
        twin = DEVICES.split("\n\n")[2].replace("event2", "event9")
        mice = hw.list_mice(DEVICES + "\n" + twin + "\n")
        self.assertEqual([mouse.nodes for mouse in mice if mouse.label == "Logitech G604"], [("event2", "event9")])

    def test_no_text_no_mice(self):
        self.assertEqual(hw.list_mice(""), [])
        self.assertEqual(hw.list_mice(None), [])


class MouseChoicesTest(unittest.TestCase):
    def setUp(self):
        self.mice = hw.list_mice(DEVICES)

    def test_empty_value_is_automatic(self):
        labels, values, selected = hw.mouse_choices(self.mice, "")
        self.assertEqual((selected, values[selected]), (0, ""))
        self.assertTrue(labels[0].startswith("Automatic"))
        self.assertEqual(values[1:], ["Logitech G604 ", "Plain Mouse"])

    def test_stored_name_selects_its_mouse(self):
        _labels, values, selected = hw.mouse_choices(self.mice, "Plain Mouse")
        self.assertEqual(values[selected], "Plain Mouse")

    def test_a_part_of_the_name_selects_like_the_driver_searches(self):
        _labels, values, selected = hw.mouse_choices(self.mice, "g604")
        self.assertEqual(values[selected], "Logitech G604 ")
        self.assertEqual(len(values), 3)

    def test_unknown_value_stays_selectable(self):
        labels, values, selected = hw.mouse_choices(self.mice, "Razer Viper")
        self.assertEqual(values[selected], "Razer Viper")
        self.assertEqual(labels[selected], "Razer Viper (not connected)")

    def test_nodes_of_the_selected_mouse(self):
        self.assertEqual(hw.mouse_nodes(self.mice, "G604"), ["event2"])
        self.assertEqual(hw.mouse_nodes(self.mice, ""), ["event2", "event3"])
        self.assertEqual(hw.mouse_nodes(self.mice, "nothing"), [])


class KeyNamesTest(unittest.TestCase):
    def test_codes_and_names_round_trip(self):
        self.assertEqual(hw.code_name(183), "KEY_F13")
        self.assertEqual(hw.code_name(0x113), "BTN_SIDE")
        self.assertEqual(hw.code_for_name("KEY_3"), 4)
        self.assertEqual(hw.code_for_name("BTN_EXTRA"), 0x114)
        self.assertIsNone(hw.code_for_name("KEY_NOPE"))
        self.assertIsNone(hw.code_name(99999))

    def test_only_presses_are_captured(self):
        self.assertEqual(hw.key_event_name(1, 183, 1), "KEY_F13")
        self.assertIsNone(hw.key_event_name(1, 183, 0))  # release
        self.assertIsNone(hw.key_event_name(1, 183, 2))  # auto-repeat
        self.assertIsNone(hw.key_event_name(2, 8, 1))  # REL_WHEEL is not a key

    def test_gtk_keys_are_the_evdev_code_plus_eight(self):
        self.assertEqual(hw.gtk_key_name(8 + 183), "KEY_F13")
        self.assertEqual(hw.gtk_key_name(8 + 4), "KEY_3")
        self.assertIsNone(hw.gtk_key_name(5))
        self.assertIsNone(hw.gtk_key_name(8 + 0x113))  # a button code is not a key

    def test_gtk_buttons(self):
        self.assertEqual(hw.gtk_button_name(2), ("BTN_MIDDLE", ""))
        self.assertEqual(hw.gtk_button_name(8), ("BTN_SIDE", ""))
        self.assertEqual(hw.gtk_button_name(9), ("BTN_EXTRA", ""))
        for plain in (1, 3):
            name, hint = hw.gtk_button_name(plain)
            self.assertIsNone(name)
            self.assertIn("side button", hint)
        self.assertIsNone(hw.gtk_button_name(12)[0])

    def test_plain_clicks_are_refused_from_a_device_too(self):
        self.assertEqual(hw.captured_key("BTN_SIDE"), ("BTN_SIDE", ""))
        self.assertEqual(hw.captured_key("KEY_3"), ("KEY_3", ""))
        for refused in ("BTN_LEFT", "BTN_RIGHT"):
            self.assertIsNone(hw.captured_key(refused)[0])
        self.assertIsNone(hw.captured_key(None)[0])
        self.assertIsNone(hw.captured_key("ABS_X")[0])

    def test_captured_names_are_valid_config_values(self):
        from config.schema import normalize_key_name
        for name in ("KEY_F13", "BTN_SIDE", "KEY_3"):
            self.assertEqual(normalize_key_name(name, ""), name)

    def test_descriptions(self):
        self.assertEqual(hw.describe_key("BTN_SIDE"), "Side button · BTN_SIDE")
        self.assertEqual(hw.describe_key("KEY_F13"), "F13 · KEY_F13")
        self.assertEqual(hw.describe_key(""), "Not set")

    def test_key_available_asks_the_driver_search(self):
        self.assertTrue(hw.key_available(DEVICES, "G604", "KEY_3"))
        self.assertFalse(hw.key_available(DEVICES, "Plain Mouse", "KEY_F13"))
        self.assertIsNone(hw.key_available(DEVICES, "", "KEY_NOPE"))


class DetectTest(unittest.TestCase):
    def detect(self, devices="", usb=(), product="Laptop", vendor="Vendor", socket=None, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            root, dmi = Path(directory) / "usb", Path(directory) / "dmi"
            root.mkdir()
            dmi.mkdir()
            (dmi / "product_name").write_text(product + "\n")
            (dmi / "sys_vendor").write_text(vendor + "\n")
            sysfs(root, usb)
            return hw.detect(
                devices_text=devices, usb_root=str(root), dmi_root=str(dmi), environ={},
                spnavrc_text="", socket_exists=lambda path: path == socket,
                table_rows=kwargs.pop("table_rows", ROWS), layouts={"proartp16"}, **kwargs,
            )

    def test_a_listed_dialpad_laptop(self):
        found = self.detect(TOUCHPAD_ASUS + DEVICES, product="ProArt PX13", vendor="ASUSTeK COMPUTER INC.")
        self.assertEqual(found.dialpad, "ASCF1A00:00 2808:0220 Touchpad")
        self.assertEqual(found.layout, "proartp16")
        self.assertEqual(found.touchpads, ())
        self.assertEqual(hw.suggest_modes(found).main, hw.MODE_DIALPAD)

    def test_an_unlisted_asus_touchpad_is_a_dialpad_to_calibrate(self):
        found = self.detect(TOUCHPAD_ASUS, product="Other", vendor="ASUSTeK COMPUTER INC.", table_rows=[])
        self.assertTrue(found.dialpad)
        self.assertEqual(found.layout, "")
        self.assertIn("Calibration", hw.findings(found)[0].detail)

    def test_an_elan_touchpad_of_another_make_is_a_plain_touchpad(self):
        found = self.detect(TOUCHPAD_ELAN, vendor="Lenovo", table_rows=[])
        self.assertEqual(found.dialpad, "")
        self.assertEqual(found.touchpads, ("ELAN1200:00 04F3:3000 Touchpad",))
        self.assertEqual(hw.suggest_modes(found).main, hw.MODE_TOUCHPAD)

    def test_the_drivers_report_makes_a_dialpad(self):
        status = {"touchpad": "ELAN1200:00 04F3:3000 Touchpad", "emulation": False, "mouse": False}
        found = self.detect(TOUCHPAD_ELAN, vendor="Lenovo", table_rows=[], status=status)
        self.assertEqual(found.dialpad, "ELAN1200:00 04F3:3000 Touchpad")
        self.assertEqual(found.touchpads, ())
        emulated = self.detect(TOUCHPAD_ELAN, vendor="Lenovo", table_rows=[], status={**status, "emulation": True})
        self.assertEqual(emulated.dialpad, "")

    def test_the_drivers_own_devices_are_not_touchpads(self):
        own = TOUCHPAD_PLAIN.replace("SYNA8004:00 06CB:CD8B Touchpad", "SYNA8004:00 DialPad")
        self.assertEqual(self.detect(own).touchpads, ())

    def test_a_desktop_with_a_mouse_and_a_spacemouse(self):
        found = self.detect(
            DEVICES, usb=[("3-1.2", "046d", "c629", "SpacePilot PRO "), ("1-1", "8087", "0029", "")],
            socket="/var/run/spnav.sock",
        )
        self.assertEqual([mouse.label for mouse in found.mice], ["Logitech G604", "Plain Mouse"])
        self.assertEqual(found.spacemice, ((0x046D, 0xC629, "SpacePilot PRO"),))
        self.assertEqual(found.spacenav_socket, "/var/run/spnav.sock")
        self.assertEqual(found.screens, ("SpacePilot Pro",))
        suggestion = hw.suggest_modes(found)
        self.assertEqual((suggestion.main, suggestion.spacemouse), (hw.MODE_MOUSE, True))

    def test_spacemouse_next_to_a_touchpad_is_a_note(self):
        found = self.detect(TOUCHPAD_PLAIN, usb=[("1-2", "256f", "c635", "")])
        suggestion = hw.suggest_modes(found)
        self.assertEqual((suggestion.main, suggestion.spacemouse), (hw.MODE_TOUCHPAD, False))
        self.assertIn("ignores it", suggestion.notes[0])

    def test_nothing_found(self):
        found = self.detect()
        self.assertEqual(hw.suggest_modes(found), hw.Suggestion("", False, ()))
        self.assertFalse(any(finding.found for finding in hw.findings(found)))

    def test_findings_cover_every_kind(self):
        kinds = [finding.kind for finding in hw.findings(self.detect())]
        self.assertEqual(kinds, ["dialpad", "touchpad", "mouse", "spacemouse"])
        found = self.detect(usb=[("3-1", "046d", "c629", "")])
        spacemouse = hw.findings(found)[3]
        self.assertIn("spacenavd is not running", spacemouse.detail)
        self.assertEqual(hw.findings(found)[-1].kind, "screen")

    def test_spacenavd_without_a_device(self):
        found = self.detect(socket="/var/run/spnav.sock")
        self.assertTrue(hw.findings(found)[3].found)
        self.assertEqual(found.spacemice, ())

    def test_the_real_machine_does_not_crash(self):
        self.assertIsInstance(hw.detect(), hw.Detected)


class ModeNotesTest(unittest.TestCase):
    def test_notes_follow_the_choice(self):
        with_spacemouse = hw.Detected(spacemice=((0x256F, 1, "SpaceMouse"),), mice=())
        self.assertEqual(hw.mode_notes(with_spacemouse, hw.MODE_MOUSE, True)[0].split()[:3], ["No", "mouse", "with"])
        self.assertIn("connected", hw.mode_notes(with_spacemouse, hw.MODE_TOUCHPAD, False)[0])
        self.assertIn("use Mouse-only mode", hw.mode_notes(with_spacemouse, hw.MODE_DIALPAD, True)[0])
        mouse = hw.Detected(mice=(hw.Mouse("m", "m"),))
        self.assertEqual(hw.mode_notes(mouse, hw.MODE_MOUSE, True), ())
        self.assertEqual(hw.mode_notes(mouse, "", False), ())


class ModesTest(unittest.TestCase):
    def test_apply_modes(self):
        config = _default_config()
        self.assertEqual(hw.current_main_mode(config), hw.MODE_DIALPAD)
        self.assertTrue(hw.apply_modes(config, hw.MODE_MOUSE, True))
        self.assertEqual(
            (config.mouse.enabled, config.emulation.enabled, config.spacemouse.enabled), (True, False, True))
        self.assertEqual(hw.current_main_mode(config), hw.MODE_MOUSE)
        self.assertFalse(hw.apply_modes(config, hw.MODE_MOUSE, True))
        self.assertTrue(hw.apply_modes(config, hw.MODE_TOUCHPAD, False))
        self.assertEqual(
            (config.mouse.enabled, config.emulation.enabled, config.spacemouse.enabled), (False, True, False))

    def test_no_main_mode_keeps_the_current_one(self):
        config = _default_config()
        config.emulation.enabled = True
        self.assertFalse(hw.apply_modes(config, "", False))
        self.assertTrue(config.emulation.enabled)


class PageVisibilityTest(unittest.TestCase):
    def found(self, **kwargs):
        return hw.Detected(**kwargs)

    def test_calibration_needs_a_dialpad(self):
        config = _default_config()
        self.assertFalse(hw.calibration_available(config, self.found()))
        self.assertTrue(hw.calibration_available(config, self.found(dialpad="ASCF Touchpad")))

    def test_calibration_hides_in_the_other_modes(self):
        found = self.found(dialpad="ASCF Touchpad")
        config = _default_config()
        config.emulation.enabled = True
        self.assertFalse(hw.calibration_available(config, found))
        config.emulation.enabled = False
        config.mouse.enabled = True
        self.assertFalse(hw.calibration_available(config, found))

    def test_the_drivers_report_wins_over_the_configuration(self):
        config = _default_config()
        self.assertTrue(hw.calibration_available(config, self.found(), {"touchpad": "ASCF", "emulation": False, "mouse": False}))
        self.assertFalse(hw.calibration_available(config, self.found(dialpad="x"), {"touchpad": "t", "emulation": True, "mouse": False}))
        self.assertFalse(hw.calibration_available(config, self.found(dialpad="x"), {"touchpad": "", "emulation": False, "mouse": True}))

    def test_spacemouse_page(self):
        config = _default_config()
        self.assertFalse(hw.spacemouse_available(config, self.found()))
        self.assertTrue(hw.spacemouse_available(config, self.found(spacenav_socket="/var/run/spnav.sock")))
        self.assertTrue(hw.spacemouse_available(config, self.found(spacemice=((0x256F, 1, "x"),))))
        config.spacemouse.enabled = True
        self.assertTrue(hw.spacemouse_available(config, self.found()))

    def test_show_all_pages(self):
        config = _default_config()
        self.assertEqual(hw.page_visibility(config, self.found()), {"calibration": False, "spacemouse": False})
        self.assertEqual(hw.page_visibility(config, self.found(), show_all=True), {"calibration": True, "spacemouse": True})


class UsbTest(unittest.TestCase):
    def test_which_usb_ids_are_spacemice(self):
        from spacemouse_devices import is_spacemouse_usb
        self.assertTrue(is_spacemouse_usb(0x046D, 0xC629))
        self.assertTrue(is_spacemouse_usb(0x046D, 0xC626))
        self.assertTrue(is_spacemouse_usb(0x256F, 0x1234))
        self.assertFalse(is_spacemouse_usb(0x046D, 0xC539))  # a Logitech receiver
        self.assertFalse(is_spacemouse_usb(0x1234, 0xC629))

    def test_missing_sysfs_is_empty(self):
        from spacemouse_devices import spacemice_present
        self.assertEqual(spacemice_present("/nonexistent/usb"), [])


class TwoOpenersTest(unittest.TestCase):
    def test_both_modes_on(self):
        config = _default_config()
        self.assertFalse(hw.two_openers(config))
        config.mouse.enabled = True
        self.assertFalse(hw.two_openers(config))
        config.spacemouse.enabled = True
        self.assertTrue(hw.two_openers(config))
        config.mouse.enabled = False
        self.assertFalse(hw.two_openers(config))

    def test_hint_text(self):
        self.assertIn("switch one off", hw.TWO_OPENERS_HINT)


class AppSettingsTest(unittest.TestCase):
    def test_round_trip_and_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sub" / "app.json"
            self.assertEqual(hw.load_app_settings(path), {"show_all_pages": False})
            self.assertTrue(hw.save_app_settings({"show_all_pages": True}, path))
            self.assertEqual(hw.load_app_settings(path), {"show_all_pages": True})

    def test_bad_files_give_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.json"
            for text in ("{", "[]", '{"show_all_pages": "yes"}'):
                path.write_text(text)
                self.assertEqual(hw.load_app_settings(path), {"show_all_pages": False})

    def test_unwritable_place(self):
        self.assertFalse(hw.save_app_settings({}, "/proc/nope/app.json"))


if __name__ == "__main__":
    unittest.main()
