"""screen/pages.py and screen/preview.py: every page paints, content stays
off the bezel, overlays and the public preview API work."""

import itertools
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cairo

from config.schema import (
    SCREEN_TEXT_SIZES, SCREEN_THEMES, MacroStep, Profile, ProfileContext, Segment, SpaceMouseAction, SpaceMouseButton,
    StepType, _default_config,
)
from screen import content, pages, preview
from screen.canvas import HEIGHT, WIDTH, device_surface, frame_bytes, preview_surface
from screen.ui import ScreenUI

SAFE_ROWS = 2


def demo_config(theme="dark", text_size="normal"):
    config = _default_config()
    blender = Profile(
        name="Blender", app_id="org.blender.Blender.desktop",
        segments=[
            Segment("Zoom view with a really long name", macro_cw=[MacroStep(type=StepType.KEYSTROKE, keys=["KEY_EQUAL"])]),
            Segment("Tools", items=[Segment(f"Tool {n}", macro_cw=[MacroStep(type=StepType.KEYSTROKE, keys=["KEY_G"])])
                                    for n in range(9)]),
            Segment("Brush", macro_cw=[MacroStep(type=StepType.KEYSTROKE, keys=["KEY_B"])]),
        ],
        spacemouse_buttons={
            "12": SpaceMouseButton(press=SpaceMouseAction("macro", keys=["KEY_TAB"]), label="Edit mode with a very long label"),
            "17": SpaceMouseButton(press=SpaceMouseAction("key_hold", keys=["KEY_LEFTCTRL", "KEY_TAB"])),
        },
        screen={"accent": "#44aaff"},
    )
    config.profiles.append(blender)
    config.screen.theme = theme
    config.screen.text_size = text_size
    return config


def row_pixels(surface, y):
    stride = surface.get_stride()
    return bytes(surface.get_data())[y * stride:(y + 1) * stride]


def background_row(surface):
    """The picture's top left pixel row is background only at y=0? Use the row below the content."""
    return row_pixels(surface, surface.get_height() - 1)


class PaintTests(unittest.TestCase):
    def test_every_page_theme_and_size_paints_off_the_bezel(self):
        for theme, size, page in itertools.product(SCREEN_THEMES, SCREEN_TEXT_SIZES, preview.PAGE_TITLES):
            with self.subTest(theme=theme, size=size, page=page):
                config = demo_config(theme, size)
                surface = preview.render(config, "Blender", page, scale=1)
                self.assertEqual((surface.get_width(), surface.get_height()), (WIDTH, HEIGHT))
                surface.flush()
                background = row_pixels(surface, HEIGHT - 1)
                self.assertEqual(len(set(background[i:i + 4] for i in range(0, len(background), 4))), 1,
                                 "the last row must be plain background")
                second = row_pixels(surface, HEIGHT - 2)
                self.assertEqual(second[:4] * (WIDTH), second, "the second last row must be plain background")

    def test_unknown_profile_and_page_do_not_raise(self):
        config = demo_config()
        self.assertEqual(preview.render(config, "No such profile", "keys", scale=1).get_width(), WIDTH)
        self.assertEqual(preview.render(config, None, "no-page", scale=1).get_width(), WIDTH)

    def test_scale_and_error_note(self):
        config = demo_config()
        self.assertEqual(preview.render(config, None, "keys", scale=2).get_width(), 640)

        class Broken:
            pass

        surface = preview.render(Broken(), None, "keys", scale=1)  # never raises
        self.assertEqual(surface.get_height(), HEIGHT)

    def test_titles(self):
        self.assertEqual(preview.PAGE_TITLES["device"], "All buttons")
        self.assertEqual(set(preview.PAGE_TITLES), {"keys", "dial", "device", "motion", "clock"})

    def test_driver_state_chips_and_live_dial(self):
        config = demo_config()
        state = {"enabled": True, "title": "Tools", "titles": ["Zoom", "Tools", "Brush"], "icons": ["", "", ""],
                 "menuTitles": ["Zoom", "Tools", "Brush"], "menuIcons": ["", "", ""], "value": 0,
                 "valueAngleStart": 0, "unit": "", "centerPressed": False, "valueShowOnlyProgress": False,
                 "touching": True}
        details = {"profile": "Blender", "mode": "multi", "colors": ["#ff0000", "#00ff00", "#0000ff"], "color": "",
                   "hints": {}, "hold_returns": True, "pointer": -1, "look": {}, "icon": "", "parent": "",
                   "submenu": None}
        driver = {"modifiers": ["Ctrl", "Shift"], "keys_held": ["KEY_Z"], "dial": True,
                  "overlay_state": state, "overlay_details": details}
        for page in ("dial", "keys"):
            surface = preview.render(config, "Blender", page, scale=1, driver_state=driver)
            self.assertEqual(surface.get_width(), WIDTH)
        # a function selected, with a value
        state = dict(state, title="Zoom", titles=[""] * 3, value=40, unit="%")
        details = dict(details, hints={"cw": "Zoom in", "ccw": "Zoom out", "center": "Reset"}, color="#ff0000")
        driver.update(overlay_state=state, overlay_details=details)
        surface = preview.render(config, "Blender", "dial", scale=1, driver_state=driver)
        self.assertEqual(surface.get_width(), WIDTH)

    def test_dial_off_page(self):
        config = demo_config()
        driver = {"overlay_state": {"enabled": False, "title": "", "titles": [], "icons": [], "menuTitles": [],
                                    "menuIcons": [], "value": 0, "valueAngleStart": 0, "unit": "",
                                    "centerPressed": False, "valueShowOnlyProgress": False, "touching": False},
                  "overlay_details": {}}
        self.assertEqual(preview.render(config, None, "dial", scale=1, driver_state=driver).get_width(), WIDTH)

    def test_highlight_changes_the_picture(self):
        config = demo_config()
        plain = bytes(preview.render(config, "Blender", "keys", scale=1).get_data())
        marked = bytes(preview.render(config, "Blender", "keys", scale=1, highlight=3).get_data())
        self.assertNotEqual(plain, marked)


def states_config(theme="dark", text_size="normal"):
    """demo_config() plus a layer, a context, motion and app buttons."""
    config = demo_config(theme, text_size)
    blender = content.find_profile(config, "Blender")
    blender.spacemouse_buttons.update({
        "13": SpaceMouseButton(press=SpaceMouseAction("layer", layer="A rather long layer name", layer_mode="latch")),
        "14": SpaceMouseButton(press=SpaceMouseAction("motion", motion="rotation")),
        "15": SpaceMouseButton(press=SpaceMouseAction("motion", motion="faster")),
        "16": SpaceMouseButton(press=SpaceMouseAction("app", command="mesh.extrude_region_move", keys=["KEY_E"])),
        "18": SpaceMouseButton(press=SpaceMouseAction("app", command="view.some_unknown_command_with_a_long_id")),
    })
    blender.spacemouse_layers = {"A rather long layer name": {
        "12": SpaceMouseButton(press=SpaceMouseAction("macro", keys=["KEY_F1"]), label="Layer one"),
        "19": SpaceMouseButton(press=SpaceMouseAction("macro", keys=["KEY_F2"])),
    }}
    blender.contexts = [ProfileContext("Edit Mesh", app_modes=["EDIT_MESH"], spacemouse_buttons={
        "12": SpaceMouseButton(press=SpaceMouseAction("macro", keys=["KEY_X"]), label="Context key")})]
    return config


STATES = {
    "plain": {},
    "layer": {"layer": "A rather long layer name"},
    "context": {"context": "Edit Mesh", "app_context": {"mode": "EDIT_MESH", "label": "Edit Mesh", "detail": "Vertex"}},
    "motion": {"motion": {"sensitivity": 1.75, "rotation": False, "translation": False}},
    "linked": {"app_link": True, "app": "blender", "app_context": {"mode": "OBJECT", "label": "Object Mode"}},
    "everything": {
        "layer": "A rather long layer name", "context": "Edit Mesh", "app_link": True, "app": "blender",
        "app_context": {"mode": "EDIT_MESH", "label": "Edit Mesh", "detail": "Vertex"},
        "motion": {"sensitivity": 0.5, "rotation": False, "translation": True}, "modifiers": ["Ctrl", "Shift"],
        "keys_held": ["KEY_Z"],
    },
}


class StatePaintTests(unittest.TestCase):
    """Layers, contexts, motion and app links on every page, theme and size."""

    CATALOG = {"commands": [{"id": "mesh.extrude_region_move", "title": "Extrude", "keys": "E"}]}

    def test_every_state_page_theme_and_size_paints_off_the_bezel(self):
        for (name, state), theme, size, page in itertools.product(STATES.items(), SCREEN_THEMES, SCREEN_TEXT_SIZES,
                                                                  preview.PAGE_TITLES):
            with self.subTest(state=name, theme=theme, size=size, page=page):
                config = states_config(theme, size)
                driver = dict(state, app_commands=self.CATALOG)
                surface = preview.render(config, "Blender", page, scale=1, driver_state=driver)
                surface.flush()
                self.assertEqual((surface.get_width(), surface.get_height()), (WIDTH, HEIGHT))
                for row in (HEIGHT - 1, HEIGHT - 2):
                    pixels = row_pixels(surface, row)
                    self.assertEqual(pixels[:4] * WIDTH, pixels, f"row {row} must be plain background")

    def test_states_change_the_picture(self):
        config = states_config()
        plain = bytes(preview.render(config, "Blender", "keys", scale=1).get_data())
        for name in ("layer", "context", "motion"):
            with self.subTest(state=name):
                other = bytes(preview.render(config, "Blender", "keys", scale=1, driver_state=STATES[name]).get_data())
                self.assertNotEqual(plain, other)
        plain_motion = bytes(preview.render(config, "Blender", "motion", scale=1).get_data())
        changed = bytes(preview.render(config, "Blender", "motion", scale=1, driver_state=STATES["motion"]).get_data())
        self.assertNotEqual(plain_motion, changed)
        plain_clock = bytes(preview.render(config, "Blender", "clock", scale=1).get_data())
        linked = bytes(preview.render(config, "Blender", "clock", scale=1, driver_state=STATES["linked"]).get_data())
        self.assertNotEqual(plain_clock, linked)

    def test_layer_dims_inherited_faces_only(self):
        config = states_config()
        base = preview.render(config, "Blender", "device", scale=1)
        layered = preview.render(config, "Blender", "device", scale=1, driver_state=STATES["layer"])
        self.assertNotEqual(bytes(base.get_data()), bytes(layered.get_data()))

    def test_the_effective_profile_keeps_the_name_and_shows_the_context_menu(self):
        config = states_config()
        profile = content.find_profile(config, "Blender")
        scene = pages.build_scene(config, "Blender", ScreenUI(content.effective_screen(config, profile), 0).view(0),
                                  driver=STATES["everything"], now=datetime(2026, 1, 2, 3, 4))
        self.assertEqual(scene.profile.name, "Blender")
        faces = {entry.key.id: entry.short for entry in scene.entries}
        self.assertEqual(faces["1"].title, "Layer one")  # the layer over the context over the profile
        surface, cr = preview_surface(1)
        pages.paint(cr, scene)

    def test_link_status_text(self):
        config = states_config()
        scene = pages.build_scene(config, "Blender", ScreenUI(content.effective_screen(config), 0).view(0),
                                  driver=STATES["linked"], app_name="Blender")
        self.assertEqual(pages.link_status(scene), "Blender add-on connected  ·  Object Mode")
        scene.app_name = ""
        scene.driver = {"app_link": True, "app": "org.blender.Blender"}
        self.assertEqual(pages.link_status(scene), "Blender add-on connected")

    def test_long_notices_run_over_two_lines_and_stay_on_screen(self):
        config = states_config()
        profile = content.find_profile(config, "Blender")
        for size in SCREEN_TEXT_SIZES:
            with self.subTest(size=size):
                config.screen.text_size = size
                ui = ScreenUI(content.effective_screen(config, profile), 0)
                ui.notify(0.0, "Ready profile for FreeCAD — check the notification")
                scene = pages.build_scene(config, "Blender", ui.view(0.1))
                surface, cr = preview_surface(1)
                pages.paint(cr, scene)
                surface.flush()
                pixels = row_pixels(surface, HEIGHT - 1)
                self.assertEqual(pixels[:4] * WIDTH, pixels)
                plain = ScreenUI(content.effective_screen(config, profile), 0)
                other, cr = preview_surface(1)
                pages.paint(cr, pages.build_scene(config, "Blender", plain.view(0.1)))
                other.flush()
                self.assertNotEqual(bytes(surface.get_data()), bytes(other.get_data()))


class SceneTests(unittest.TestCase):
    def scene(self, **view_changes):
        config = demo_config()
        profile = content.find_profile(config, "Blender")
        settings = content.effective_screen(config, profile)
        machine = ScreenUI(settings, 0)
        entries = content.key_entries(config, profile)
        machine.set_context(content.face_buttons(entries), pages.device_steps(entries))
        return config, machine, pages.build_scene(config, "Blender", machine.view(0), now=datetime(2026, 1, 2, 3, 4))

    def paint(self, scene, scale=1):
        surface, cr = preview_surface(scale)
        pages.paint(cr, scene)
        surface.flush()
        return surface

    def test_overlays_and_toast_paint_off_the_bezel(self):
        _config, machine, _ = self.scene()
        for overlay in ("picker", "settings"):
            for size in SCREEN_TEXT_SIZES:
                with self.subTest(overlay=overlay, size=size):
                    config = demo_config(text_size=size)
                    profile = content.find_profile(config, "Blender")
                    ui = ScreenUI(content.effective_screen(config, profile), 0)
                    ui.overlay, ui.overlay_index = overlay, 7 if overlay == "settings" else 2
                    ui.feedback(0.0, 12, "A long toast title that must be cut", "Ctrl+Shift+Alt+F12", "#ff8800")
                    scene = pages.build_scene(config, "Blender", ui.view(0.1))
                    surface = self.paint(scene)
                    self.assertEqual(len(set(row_pixels(surface, HEIGHT - 1)[i:i + 4]
                                             for i in range(0, WIDTH * 4, 4))), 1)

    def test_toast_alone_draws_something(self):
        config, machine, base = self.scene()
        before = bytes(self.paint(base).get_data())
        machine.feedback(0.0, 12, "Zoom", "Ctrl+Z", "#ff0000")
        scene = pages.build_scene(config, "Blender", machine.view(0.1), now=datetime(2026, 1, 2, 3, 4))
        self.assertNotEqual(bytes(self.paint(scene).get_data()), before)

    def test_dim_and_off_veils(self):
        config, machine, base = self.scene()
        awake = bytes(self.paint(base).get_data())
        machine.power = "off"
        off = pages.build_scene(config, "Blender", machine.view(0), now=datetime(2026, 1, 2, 3, 4))
        data = bytes(self.paint(off).get_data())
        self.assertNotEqual(data, awake)
        self.assertEqual(set(data[i] for i in range(0, len(data), 4)) | set(data[i + 1] for i in range(0, len(data), 4)), {0})

    def test_device_scroll_reaches_every_row(self):
        config = demo_config()
        profile = content.find_profile(config, "Blender")
        entries = content.key_entries(config, profile)
        self.assertGreater(pages.device_steps(entries), 0)
        self.assertGreater(pages.device_steps(entries, True), pages.device_steps(entries) - 1)
        ui = ScreenUI(content.effective_screen(config, profile), 0)
        ui.page = "device"
        ui.set_context([], pages.device_steps(entries))
        top = bytes(self.paint(pages.build_scene(config, "Blender", ui.view(0))).get_data())
        ui.scroll = ui.device_steps
        end = bytes(self.paint(pages.build_scene(config, "Blender", ui.view(0))).get_data())
        self.assertNotEqual(top, end)

    def test_panel_frame_is_complete(self):
        _config, _machine, scene = self.scene()
        surface, cr = device_surface()
        pages.paint(cr, scene)
        self.assertEqual(len(frame_bytes(surface)), 512 + 153600)

    def test_clock_formats(self):
        settings = content.effective_screen(demo_config())
        self.assertEqual(pages.clock_text(datetime(2026, 1, 2, 15, 4), settings), "15:04")
        settings.clock_24h = False
        self.assertEqual(pages.clock_text(datetime(2026, 1, 2, 15, 4), settings), "3:04 PM")

    def test_theme_accent_override(self):
        config = demo_config("dark")
        profile = content.find_profile(config, "Blender")
        theme = pages.make_theme(content.effective_screen(config, profile))
        self.assertAlmostEqual(theme.accent[2], 1.0, places=2)
        plain = pages.make_theme(content.effective_screen(config, content.find_profile(config, "")))
        self.assertNotEqual(plain.accent, theme.accent)

    def test_text_fits(self):
        surface, cr = preview_surface(1)
        pen = pages.Pen(cr, pages.make_theme(content.effective_screen(demo_config())))
        text = pen.fit("A very long title that does not fit in sixty pixels", 60, 14, True)
        self.assertTrue(text.endswith("…"))
        self.assertLessEqual(pen.width(text, 14, True), 60)
        self.assertEqual(pen.fit("Short", 60, 14, True), "Short")


if __name__ == "__main__":
    unittest.main()
