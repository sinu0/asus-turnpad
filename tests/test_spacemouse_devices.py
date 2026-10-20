import os
import tempfile
import unittest

from spacemouse_devices import (
    GROUPS,
    MODELS,
    SPACEPILOT_PRO,
    generic_model,
    index_label,
    key_for_index,
    model_for_name,
    model_for_usb,
    screens_present,
)


class ModelTableTests(unittest.TestCase):
    def test_ids_and_indexes_are_unique(self):
        for model in MODELS:
            ids = [key.id for key in model.keys]
            self.assertEqual(len(ids), len(set(ids)), model.id)
            indexes = [key.index for key in model.keys]
            longs = [key.long_index for key in model.keys if key.long_index is not None]
            both = indexes + longs
            self.assertEqual(len(both), len(set(both)), f"{model.id}: an index is used twice")

    def test_every_index_is_below_the_button_count(self):
        for model in MODELS:
            for key in model.keys:
                self.assertLess(key.index, model.button_count)
                if key.long_index is not None:
                    self.assertLess(key.long_index, model.button_count)
                    self.assertTrue(key.long_name, key.id)

    def test_spacepilot_has_all_31_indexes(self):
        indexes = {key.index for key in SPACEPILOT_PRO.keys}
        indexes |= {key.long_index for key in SPACEPILOT_PRO.keys if key.long_index is not None}
        self.assertEqual(indexes, set(range(31)))
        self.assertEqual(len(SPACEPILOT_PRO.keys), 21)

    def test_groups_are_known(self):
        for key in SPACEPILOT_PRO.keys:
            self.assertIn(key.group, GROUPS)

    def test_function_keys_short_12_to_16_long_17_to_21(self):
        for number in range(1, 6):
            key = next(item for item in SPACEPILOT_PRO.keys if item.id == str(number))
            self.assertEqual((key.index, key.long_index), (11 + number, 16 + number))

    def test_screen_keys_are_distinct_bits(self):
        screen = SPACEPILOT_PRO.screen
        bits = [key.bit for key in screen.keys]
        self.assertEqual(len(bits), len(set(bits)))
        self.assertLess(screen.safe_bottom, screen.height)


class LookupTests(unittest.TestCase):
    def test_name_matching_ignores_case_and_padding(self):
        self.assertIs(model_for_name("3Dconnexion  SpacePilot PRO "), SPACEPILOT_PRO)
        self.assertIs(model_for_name("spacepilot pro"), SPACEPILOT_PRO)
        self.assertIsNone(model_for_name("3Dconnexion SpaceNavigator"))
        self.assertIsNone(model_for_name(""))
        self.assertIsNone(model_for_name(None))

    def test_usb_ids(self):
        self.assertIs(model_for_usb(0x046D, 0xC629), SPACEPILOT_PRO)
        self.assertIsNone(model_for_usb(0x046D, 0xC62B))

    def test_key_for_index(self):
        key, is_long = key_for_index(SPACEPILOT_PRO, 12)
        self.assertEqual((key.id, is_long), ("1", False))
        key, is_long = key_for_index(SPACEPILOT_PRO, 17)
        self.assertEqual((key.id, is_long), ("1", True))
        key, is_long = key_for_index(SPACEPILOT_PRO, 6)
        self.assertEqual((key.id, is_long), ("t", True))
        self.assertIsNone(key_for_index(SPACEPILOT_PRO, 40))

    def test_index_label(self):
        self.assertEqual(index_label(SPACEPILOT_PRO, 0), "Menu")
        self.assertEqual(index_label(SPACEPILOT_PRO, 12), "1")
        self.assertEqual(index_label(SPACEPILOT_PRO, 17), "1 · long")
        self.assertEqual(index_label(SPACEPILOT_PRO, 2), "T")
        self.assertEqual(index_label(SPACEPILOT_PRO, 6), "T · long")
        self.assertEqual(index_label(SPACEPILOT_PRO, 50), "Button 50")
        self.assertEqual(index_label(None, 12), "Button 12")

    def test_generic_model(self):
        model = generic_model(4)
        self.assertEqual([key.index for key in model.keys], [0, 1, 2, 3])
        self.assertTrue(all(key.long_index is None for key in model.keys))
        self.assertEqual(index_label(model, 2), "2")
        self.assertIsNone(model.screen)
        self.assertEqual(generic_model(0).keys, ())


class ScreensPresentTests(unittest.TestCase):
    def device(self, root, name, vendor, product, bus="1", dev="4"):
        folder = os.path.join(root, name)
        os.makedirs(folder)
        for file, text in (("idVendor", vendor), ("idProduct", product), ("busnum", bus), ("devnum", dev)):
            if text is not None:
                with open(os.path.join(folder, file), "w", encoding="ascii") as handle:
                    handle.write(text + "\n")
        return folder

    def test_scan(self):
        with tempfile.TemporaryDirectory() as root:
            folder = self.device(root, "3-2", "046d", "c629", "3", "7")
            self.device(root, "1-1", "1d6b", "0002")           # a hub
            self.device(root, "2-1", "046d", "c629", None)     # no bus number: skipped
            os.makedirs(os.path.join(root, "usb1"))            # no id files
            found = screens_present(root)
            self.assertEqual(len(found), 1)
            self.assertIs(found[0].model, SPACEPILOT_PRO)
            self.assertEqual((found[0].busnum, found[0].devnum, found[0].path), (3, 7, folder))

    def test_missing_root(self):
        self.assertEqual(screens_present("/nonexistent/sysfs"), [])


if __name__ == "__main__":
    unittest.main()
