"""The settings app's command line (config_app/commandline.py)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config_app.commandline import parse, resolve_app

APPS = [
    ("org.blender.Blender.desktop", "Blender"),
    ("org.freecad.FreeCAD.desktop", "FreeCAD"),
    ("firefox.desktop", "Firefox"),
    ("gimp.desktop", "GIMP"),
]


class ParseTest(unittest.TestCase):
    def test_add_profile_in_both_spellings(self):
        self.assertEqual(parse(["--add-profile", "org.freecad.FreeCAD.desktop"]), ("org.freecad.FreeCAD.desktop", None))
        self.assertEqual(parse(["--add-profile=blender"]), ("blender", None))

    def test_no_arguments(self):
        self.assertEqual(parse([]), ("", None))

    def test_missing_value_is_an_error_not_an_exit(self):
        add, error = parse(["--add-profile"])
        self.assertEqual(add, "")
        self.assertTrue(error)

    def test_other_arguments_are_left_to_gtk(self):
        self.assertEqual(parse(["--gapplication-service", "--add-profile", "x"]), ("x", None))

    def test_the_id_is_trimmed_and_bounded(self):
        self.assertEqual(parse(["--add-profile", "  blender "])[0], "blender")
        self.assertEqual(len(parse(["--add-profile", "x" * 1000])[0]), 300)


class ResolveTest(unittest.TestCase):
    def test_exact_id(self):
        self.assertEqual(resolve_app("org.blender.Blender.desktop", APPS), ("org.blender.Blender.desktop", "Blender", True))

    def test_id_without_desktop_and_any_case(self):
        self.assertEqual(resolve_app("org.freecad.freecad", APPS), ("org.freecad.FreeCAD.desktop", "FreeCAD", True))
        self.assertEqual(resolve_app("Firefox", APPS), ("firefox.desktop", "Firefox", True))

    def test_a_program_name_finds_the_application(self):
        self.assertEqual(resolve_app("blender", APPS), ("org.blender.Blender.desktop", "Blender", True))
        self.assertEqual(resolve_app("freecad", APPS)[0], "org.freecad.FreeCAD.desktop")

    def test_part_of_a_word_is_not_enough(self):
        self.assertFalse(resolve_app("blend", APPS)[2])
        self.assertFalse(resolve_app("fire", APPS)[2])

    def test_unknown_keeps_the_id_and_uses_the_pack_name(self):
        self.assertEqual(resolve_app("org.kicad.KiCad.desktop", APPS, "KiCad"), ("org.kicad.KiCad.desktop", "KiCad", False))
        self.assertEqual(resolve_app("kicad", APPS), ("kicad", "kicad", False))


if __name__ == "__main__":
    unittest.main()
