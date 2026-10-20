"""SpaceMouse button layers and the cap's motion per application: the pure
parts (layer state, button machine, motion session, schema, client requests)
and the driver loop against a fake spacenavd."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import spacenav_client as proto  # noqa: E402
from config.legacy_adapter import shortcuts_from_config  # noqa: E402
from config.profile_share import ProfileTextError, profiles_from_text  # noqa: E402
from config.schema import (  # noqa: E402
    DialPadConfig, Profile, SpaceMouseAction, SpaceMouseButton, SpaceMouseConfig, _decoder, _encoder,
    default_spacemouse_buttons, effective_motion, merge_spacemouse_layers, motion_rule, portable_config,
    Segment, shell_commands, spacemouse_hold_keys, spacemouse_macros, merge_portable, MacroStep, StepType,
)
from fake_spacenavd import FakeSpacenavd  # noqa: E402
from spacemouse_dial import (  # noqa: E402
    ButtonMachine, LayerState, MotionSession, motion_neutral, motion_values,
)
from test_spacemouse_loop import SpaceMouseTestCase, axis, button  # noqa: E402


def macro(*keys):
    return SpaceMouseButton(press=SpaceMouseAction("macro", keys=list(keys)))


def layer_button(name, mode="hold"):
    return SpaceMouseButton(press=SpaceMouseAction("layer", layer=name, layer_mode=mode))


def motion_button(name):
    return SpaceMouseButton(press=SpaceMouseAction("motion", motion=name))


class LayerStateTest(unittest.TestCase):
    def test_hold_is_active_while_the_button_is_down(self):
        layers = LayerState()
        self.assertEqual(layers.active, "")
        layers.hold_down(3, "Shift")
        self.assertEqual(layers.active, "Shift")
        layers.hold_up(3)
        self.assertEqual(layers.active, "")

    def test_the_latest_one_wins_and_the_one_before_returns(self):
        layers = LayerState()
        layers.activate(5, "A", "toggle")
        layers.hold_down(3, "B")
        self.assertEqual(layers.active, "B")
        layers.hold_up(3)
        self.assertEqual(layers.active, "A")

    def test_latch_is_used_up_by_the_next_press(self):
        layers = LayerState()
        self.assertTrue(layers.activate(5, "A", "latch"))
        self.assertEqual(layers.active, "A")
        layers.consume()
        self.assertEqual(layers.active, "")

    def test_latch_is_cancelled_by_its_button_or_the_same_layer_again(self):
        layers = LayerState()
        layers.activate(5, "A", "latch")
        self.assertTrue(layers.preempt(5))
        self.assertEqual(layers.active, "")
        self.assertFalse(layers.preempt(5))  # nothing to end
        layers.activate(5, "A", "latch")
        self.assertFalse(layers.activate(6, "A", "latch"))  # another button of the same layer
        self.assertEqual(layers.active, "")

    def test_toggle_stays_until_its_button_is_pressed_again(self):
        layers = LayerState()
        layers.activate(5, "A", "toggle")
        layers.consume()  # other presses do not end a toggle
        self.assertEqual(layers.active, "A")
        self.assertTrue(layers.preempt(5))
        self.assertEqual(layers.active, "")

    def test_a_second_toggle_replaces_the_first(self):
        layers = LayerState()
        layers.activate(5, "A", "toggle")
        layers.activate(6, "B", "toggle")
        self.assertEqual(layers.active, "B")
        layers.preempt(6)
        self.assertEqual(layers.active, "")

    def test_a_hold_is_not_preempted(self):
        layers = LayerState()
        layers.hold_down(3, "A")
        self.assertFalse(layers.preempt(3))
        self.assertEqual(layers.active, "A")

    def test_clear_sticky_keeps_the_holds(self):
        layers = LayerState()
        layers.activate(5, "A", "toggle")
        layers.activate(6, "B", "latch")
        layers.hold_down(3, "C")
        layers.clear_sticky()
        self.assertEqual(layers.entries, [["hold", "C", 3]])
        layers.clear()
        self.assertEqual(layers.active, "")


class LayerButtonMachineTest(unittest.TestCase):
    def test_hold_layer_goes_down_and_up_with_the_button(self):
        machine = ButtonMachine()
        action = SpaceMouseAction("layer", layer="Shift", layer_mode="hold")
        out = machine.press(3, 0.0, SpaceMouseButton(press=action), False)
        self.assertEqual([(a.kind, a.index, a.action) for a in out], [("layer_down", 3, action)])
        out = machine.release(3, 1.0, False)
        self.assertEqual([(a.kind, a.index) for a in out], [("layer_up", 3)])

    def test_latch_and_toggle_run_as_plain_actions_and_release_does_nothing(self):
        for mode in ("latch", "toggle"):
            machine = ButtonMachine()
            action = SpaceMouseAction("layer", layer="Shift", layer_mode=mode)
            out = machine.press(3, 0.0, SpaceMouseButton(press=action), False)
            self.assertEqual([(a.kind, a.action) for a in out], [("action", action)])
            self.assertEqual(machine.release(3, 0.1, False), [])

    def test_a_hold_layer_as_a_hold_action_starts_after_the_hold_time(self):
        machine = ButtonMachine(hold_ms=400)
        layer = SpaceMouseAction("layer", layer="Shift", layer_mode="hold")
        binding = SpaceMouseButton(press=SpaceMouseAction("dial"), hold=layer)
        self.assertEqual(machine.press(3, 0.0, binding, False), [])
        self.assertEqual(machine.tick(0.2, False), [])
        self.assertEqual([a.kind for a in machine.tick(0.5, False)], ["layer_down"])
        self.assertEqual([a.kind for a in machine.release(3, 0.9, False)], ["layer_up"])

    def test_losing_the_connection_ends_hold_layers(self):
        machine = ButtonMachine()
        machine.press(3, 0.0, layer_button("Shift"), False)
        self.assertEqual([a.kind for a in machine.release_all()], ["layer_up"])

    def test_layer_and_motion_buttons_do_not_end_a_menu_waiting_for_its_opener(self):
        for binding in (layer_button("Shift", "hold"), layer_button("Shift", "toggle"), motion_button("faster")):
            machine = ButtonMachine()
            machine.press(0, 0.0, SpaceMouseButton(press=SpaceMouseAction("dial")), False)
            machine.release(0, 0.1, True)  # sticky
            self.assertEqual(machine.opener, 0)
            machine.press(3, 0.5, binding, True)
            self.assertEqual(machine.opener, 0)

    def test_a_layer_button_is_no_gesture_while_held(self):
        machine = ButtonMachine()
        machine.press(3, 0.0, layer_button("Shift"), False)
        self.assertFalse(machine.gesture_active)


class MotionSessionTest(unittest.TestCase):
    def base(self, **kwargs):
        return {**effective_motion(), **kwargs}

    def test_rotation_and_translation_toggle(self):
        session = MotionSession()
        base = self.base()
        self.assertTrue(session.act("rotation", base))
        self.assertFalse(session.apply(base)["rotation"])
        self.assertTrue(session.apply(base)["translation"])
        session.act("rotation", base)
        self.assertTrue(session.apply(base)["rotation"])
        session.act("translation", base)
        self.assertFalse(session.apply(base)["translation"])

    def test_the_toggle_starts_from_the_configured_value(self):
        session = MotionSession()
        base = self.base(rotation=False)
        session.act("rotation", base)
        self.assertTrue(session.apply(base)["rotation"])

    def test_faster_and_slower_step_and_stop_at_the_ends(self):
        session = MotionSession()
        base = self.base()
        session.act("faster", base)
        self.assertEqual(session.apply(base)["sensitivity"], 1.25)
        session.act("slower", base)
        session.act("slower", base)
        self.assertEqual(session.apply(base)["sensitivity"], 0.75)
        for _ in range(20):
            session.act("faster", base)
        self.assertEqual(session.apply(base)["sensitivity"], 4.0)
        self.assertFalse(session.act("faster", base))
        for _ in range(20):
            session.act("slower", base)
        self.assertEqual(session.apply(base)["sensitivity"], 0.1)

    def test_faster_goes_on_from_a_configured_value_between_the_steps(self):
        session = MotionSession()
        base = self.base(sensitivity=0.9)
        session.act("faster", base)
        self.assertEqual(session.apply(base)["sensitivity"], 1.0)

    def test_reset_undoes_the_session_only(self):
        session = MotionSession()
        base = self.base(sensitivity=2.0)
        session.act("faster", base)
        session.act("rotation", base)
        self.assertTrue(session.changed)
        self.assertTrue(session.act("reset", base))
        self.assertEqual(session.apply(base), base)
        self.assertFalse(session.changed)
        self.assertFalse(session.act("reset", base))

    def test_neutral_and_values(self):
        self.assertTrue(motion_neutral(effective_motion()))
        self.assertFalse(motion_neutral(effective_motion({"sensitivity": 1.2})))
        self.assertFalse(motion_neutral(effective_motion({"invert": [False, True] + [False] * 4})))
        self.assertFalse(motion_neutral(effective_motion({"rotation": False})))
        original = {"sens": 1.5, "axis": [1, 1, 2, 1, 1, 0.5], "invert": [0, 0, 0, 1, 0, 0]}
        motion = effective_motion({
            "sensitivity": 2.0, "axis_sensitivity": [1, 2, 1, 1, 1, 1], "invert": [True, False, False, True, False, False],
            "rotation": False,
        })
        sens, axes, invert = motion_values(original, motion)
        self.assertEqual(sens, 3.0)
        self.assertEqual(axes, [1, 2, 2, 0, 0, 0])  # rotation off: those three are 0
        self.assertEqual(invert, [True, False, False, False, False, False])  # flips the original
        sens, axes, invert = motion_values(original, effective_motion({"translation": False}))
        self.assertEqual(axes, [0, 0, 0, 1, 1, 0.5])
        # Neutral motion gives the original values back.
        self.assertEqual(motion_values(original, effective_motion()), (1.5, [1, 1, 2, 1, 1, 0.5], [False, False, False, True, False, False]))


class SchemaTest(unittest.TestCase):
    def config(self):
        config = DialPadConfig()
        config.spacemouse = SpaceMouseConfig(
            enabled=True,
            buttons={"3": layer_button("Shift layer", "latch"), "4": macro("KEY_F5")},
            layers={"Shift layer": {"4": macro("KEY_F6"), "9": SpaceMouseButton(label="Nine")}},
            motion={"sensitivity": 1.5, "rotation": False},
        )
        config.profiles = [Profile(
            name="Blender", app_id="blender.desktop",
            spacemouse_layers={"Shift layer": {"4": macro("KEY_F7")}, "Other": {"5": motion_button("reset")}},
            spacemouse_motion={"axis_sensitivity": [1, 1, 1, 0.5, 0.5, 0.5], "invert": [True] + [False] * 5},
        )]
        return config

    def test_round_trip(self):
        config = self.config()
        decoded = _decoder(_encoder(config), DialPadConfig)
        self.assertEqual(decoded.spacemouse, config.spacemouse)
        self.assertEqual(decoded.profiles[0].spacemouse_layers, config.profiles[0].spacemouse_layers)
        self.assertEqual(decoded.profiles[0].spacemouse_motion, config.profiles[0].spacemouse_motion)
        action = decoded.spacemouse.buttons["3"].press
        self.assertEqual((action.type, action.layer, action.layer_mode), ("layer", "Shift layer", "latch"))

    def test_old_files_have_no_layers_and_no_motion(self):
        data = _encoder(DialPadConfig())
        del data["spacemouse"]["layers"], data["spacemouse"]["motion"]
        decoded = _decoder(data, DialPadConfig)
        self.assertEqual((decoded.spacemouse.layers, decoded.spacemouse.motion), ({}, {}))
        profile = _decoder({"name": "P", "segments": []}, Profile)
        self.assertEqual((profile.spacemouse_layers, profile.spacemouse_motion), ({}, {}))
        self.assertNotIn("spacemouse_layers", _encoder(profile))
        self.assertNotIn("spacemouse_motion", _encoder(profile))

    def test_layer_and_motion_actions_are_repaired_or_dropped(self):
        data = {"name": "P", "segments": [], "spacemouse_buttons": {
            "1": {"press": {"type": "layer"}},                                   # no name: dropped
            "2": {"press": {"type": "layer", "layer": " Fn ", "layer_mode": "x"}},  # mode repaired
            "3": {"press": {"type": "motion", "motion": "sideways"}},            # unknown: dropped
            "4": {"press": {"type": "motion", "motion": "faster", "layer": "X"}},  # stray field not kept
        }, "spacemouse_layers": {"": {"1": {"press": {"type": "dial"}}}, "Fn": "junk", "Ok": {"1": {"press": {"type": "dial"}}}}}
        profile = _decoder(data, Profile)
        self.assertEqual(sorted(profile.spacemouse_buttons), ["2", "4"])
        self.assertEqual(profile.spacemouse_buttons["2"].press.layer, "Fn")
        self.assertEqual(profile.spacemouse_buttons["2"].press.layer_mode, "hold")
        self.assertIsNone(profile.spacemouse_buttons["4"].press.layer)
        self.assertEqual(sorted(profile.spacemouse_layers), ["Fn", "Ok"])
        self.assertEqual(profile.spacemouse_layers["Fn"], {})

    def test_motion_rule_clamps_and_drops(self):
        self.assertEqual(motion_rule(None), {})
        self.assertEqual(motion_rule({"sensitivity": 99, "junk": 1}), {"sensitivity": 4.0})
        self.assertEqual(motion_rule({"sensitivity": 0}), {"sensitivity": 0.1})
        self.assertEqual(motion_rule({"sensitivity": "x"}), {"sensitivity": 1.0})
        self.assertEqual(motion_rule({"axis_sensitivity": [1, 2, 3]}), {})  # not six values
        self.assertEqual(motion_rule({"axis_sensitivity": [-1, 9, 0, 1, 1, 1]})["axis_sensitivity"], [0.0, 4.0, 0.0, 1.0, 1.0, 1.0])
        self.assertEqual(motion_rule({"invert": [1, 0, 0, 0, 0, 0]})["invert"], [True] + [False] * 5)
        self.assertEqual(motion_rule({"rotation": "no", "translation": False}), {"translation": False})

    def test_effective_motion_lays_the_profile_over_the_global(self):
        motion = effective_motion({"sensitivity": 2.0, "rotation": False}, {"sensitivity": 0.5})
        self.assertEqual((motion["sensitivity"], motion["rotation"], motion["translation"]), (0.5, False, True))
        self.assertEqual(motion["axis_sensitivity"], [1.0] * 6)
        self.assertTrue(motion_neutral(effective_motion({}, None)))

    def test_layers_merge_per_layer(self):
        base = {"A": {"1": macro("KEY_A"), "2": macro("KEY_B")}, "B": {"1": macro("KEY_C")}}
        over = {"A": {"1": macro("KEY_X"), "2": SpaceMouseButton(label="Two")}, "C": {"1": macro("KEY_D")}}
        merged = merge_spacemouse_layers(base, over)
        self.assertEqual(merged["A"]["1"].press.keys, ["KEY_X"])
        self.assertEqual((merged["A"]["2"].press.keys, merged["A"]["2"].label), (["KEY_B"], "Two"))
        self.assertEqual(sorted(merged), ["A", "B", "C"])
        self.assertEqual(base["A"]["1"].press.keys, ["KEY_A"])  # the inputs are not changed

    def test_layer_bindings_count_for_macros_and_held_keys(self):
        config = self.config()
        config.spacemouse.layers["Shift layer"]["7"] = SpaceMouseButton(press=SpaceMouseAction("key_hold", keys=["KEY_Z"]))
        config.profiles[0].spacemouse_layers["Other"]["8"] = SpaceMouseButton(press=SpaceMouseAction(
            "macro", macro=[MacroStep(type=StepType.KEYSTROKE, keys=["KEY_F9"])],
        ))
        self.assertIn("KEY_Z", spacemouse_hold_keys(config))
        self.assertIn([MacroStep(type=StepType.KEYSTROKE, keys=["KEY_F9"])], spacemouse_macros(config))

    def test_shell_steps_in_layers_are_listed(self):
        shell = SpaceMouseAction("macro", macro=[MacroStep(type=StepType.SHELL, script="touch x")])
        profile = Profile(name="P", spacemouse_layers={"Fn": {"4": SpaceMouseButton(press=shell)}})
        self.assertEqual(shell_commands([profile]), [("P", "SpaceMouse Fn layer, button 4", "touch x")])
        self.assertEqual(
            shell_commands([], None, {"Fn": {"4": SpaceMouseButton(press=shell)}}),
            [("SpaceMouse", "SpaceMouse Fn layer, button 4", "touch x")],
        )

    def test_layers_and_motion_travel_with_the_settings(self):
        config = self.config()
        portable = portable_config(config)
        self.assertIn("layers", portable["spacemouse"])
        self.assertEqual(portable["spacemouse"]["motion"], {"sensitivity": 1.5, "rotation": False})
        merged = merge_portable(DialPadConfig(), portable)
        self.assertEqual(merged.spacemouse.layers, config.spacemouse.layers)
        self.assertEqual(merged.spacemouse.motion, config.spacemouse.motion)

    def test_the_default_map_binds_no_layer_but_the_motion_keys(self):
        for button_ in default_spacemouse_buttons().values():
            for action in (button_.press, button_.hold):
                self.assertTrue(action is None or action.type != "layer")
        self.assertEqual(default_spacemouse_buttons()["26"].press.motion, "rotation")


class ProfileTextTest(unittest.TestCase):
    def profile(self, **extra):
        import json
        return json.dumps({"name": "P", "segments": [{"title": "A", "macro_center": [{"type": "keystroke", "keys": ["KEY_A"]}]}], **extra})

    def test_layers_and_motion_are_read(self):
        profiles = profiles_from_text(self.profile(
            spacemouse_buttons={"3": {"press": {"type": "layer", "layer": "Fn", "layer_mode": "toggle"}}},
            spacemouse_layers={"Fn": {"4": {"press": {"type": "macro", "keys": ["z"]}}}},
            spacemouse_motion={"sensitivity": 2, "invert": [True, False, False, False, False, False]},
        ))
        profile = profiles[0]
        self.assertEqual(profile.spacemouse_buttons["3"].press.layer_mode, "toggle")
        self.assertEqual(profile.spacemouse_layers["Fn"]["4"].press.keys, ["KEY_Z"])  # key names normalised
        self.assertEqual(profile.spacemouse_motion["sensitivity"], 2.0)

    def test_bad_layer_and_motion_text_is_explained(self):
        cases = [
            {"spacemouse_buttons": {"3": {"press": {"type": "layer"}}}},
            {"spacemouse_buttons": {"3": {"press": {"type": "layer", "layer": "A", "layer_mode": "x"}}}},
            {"spacemouse_buttons": {"3": {"press": {"type": "motion", "motion": "x"}}}},
            {"spacemouse_layers": [1]},
            {"spacemouse_layers": {"A": {"3": {"press": {"type": "nope"}}}}},
            {"spacemouse_motion": {"invert": [True]}},
            {"spacemouse_motion": 3},
        ]
        for extra in cases:
            with self.subTest(extra=extra), self.assertRaises(ProfileTextError):
                profiles_from_text(self.profile(**extra))


class ClientRequestsTest(unittest.TestCase):
    def test_request_payloads(self):
        import struct
        axes = [1.0, 2.0, 0.0, 0.5, 4.0, 0.25]
        request = struct.unpack("=8i", proto.pack_request(proto.REQ_SCFG_SENS_AXIS, [proto.float_to_bits(v) for v in axes]))
        self.assertEqual(request[0], 0x7FAA3002)
        self.assertEqual([proto.bits_to_float(bits) for bits in request[1:7]], axes)
        self.assertEqual(request[7], 0)
        request = struct.unpack("=8i", proto.pack_request(proto.REQ_SCFG_INVERT, [1, 0, 0, 1, 0, 1]))
        self.assertEqual(request[0], 0x7FAA3006)
        self.assertEqual(request[1:7], (1, 0, 0, 1, 0, 1))
        self.assertEqual(proto.REQ_GCFG_SENS_AXIS, 0x3003)
        self.assertEqual(proto.REQ_GCFG_INVERT, 0x3007)

    def test_get_and_set_against_the_fake_daemon(self):
        with tempfile.TemporaryDirectory() as directory:
            server = FakeSpacenavd(directory)
            try:
                client = proto.SpacenavClient()
                self.assertTrue(client.connect([server.path]))
                self.assertEqual(client.get_axis_sensitivity(), [1.0] * 6)
                self.assertEqual(client.get_invert(), [False] * 6)
                self.assertTrue(client.set_axis_sensitivity([1, 2, 3, 0, 0.5, 4]))
                self.assertTrue(client.set_invert([True, False, False, True, False, False]))
                self.assertEqual(client.get_axis_sensitivity(), [1, 2, 3, 0, 0.5, 4])
                self.assertEqual(client.get_invert(), [True, False, False, True, False, False])
                # Never the daemon's save, restore or reset requests.
                self.assertTrue(all(request < 0x3ffe for request, _data in server.requests))
                client.close()
            finally:
                server.stop()


class LayerLoopTest(SpaceMouseTestCase):
    """Button layers in the driver loop."""

    autorun = True

    def start(self, global_buttons=None, layers=None, krita=None, krita_layers=None, files=None, **kwargs):
        server = self.start_server()
        config = self.config(server, buttons=global_buttons or {}, layers=layers or {}, **kwargs)
        config.profiles[1].spacemouse_buttons = krita or {}
        config.profiles[1].spacemouse_layers = krita_layers or {}
        config.profiles[2].spacemouse_buttons = files or {}
        self.load(config)
        self.server = server
        return server

    def macros(self):
        self.run_queue()
        return [steps[0].keys for steps in self.state.macros]

    def base_buttons(self, mode):
        return {
            "3": layer_button("Fn", mode),
            "4": macro("KEY_F5"),
            "12": macro("KEY_F1"),
        }

    LAYERS = {"Fn": {"4": macro("KEY_F6"), "12": macro("KEY_F2"), "9": macro("KEY_F9")}}

    def test_hold_layer_chord_and_no_leak_of_the_layer_button(self):
        server = self.start(self.base_buttons("hold"), self.LAYERS)
        self.run_steps(
            lambda: self.deliver(server, button(4, True)),        # base
            lambda: self.deliver(server, button(4, False)),
            lambda: self.deliver(server, button(3, True)),        # the layer button down
            lambda: self.assertEqual(self.source().layers.active, "Fn"),
            lambda: self.deliver(server, button(4, True)),        # in the layer
            lambda: self.deliver(server, button(4, False)),
            lambda: self.deliver(server, button(9, True)),        # only in the layer
            lambda: self.deliver(server, button(9, False)),
            lambda: self.deliver(server, button(3, False)),
            lambda: self.assertEqual(self.source().layers.active, ""),
            lambda: self.deliver(server, button(9, True)),        # unbound again
            lambda: self.deliver(server, button(4, True)),
            lambda: None,
        )
        self.assertEqual(self.macros(), [["KEY_F5"], ["KEY_F6"], ["KEY_F9"], ["KEY_F5"]])

    def test_a_dual_function_short_press_arrives_whole_while_the_layer_is_held(self):
        server = self.start(self.base_buttons("hold"), self.LAYERS)
        self.run_steps(
            lambda: self.deliver(server, button(3, True)),
            # A short press of a dual-function key: down and up at its release, in one go.
            lambda: self.deliver(server, button(12, True), button(12, False)),
            lambda: self.deliver(server, button(3, False)),
            lambda: self.deliver(server, button(12, True), button(12, False)),
            lambda: None,
        )
        self.assertEqual(self.macros(), [["KEY_F2"], ["KEY_F1"]])

    def test_a_button_pressed_in_the_layer_keeps_its_action_when_the_layer_ends(self):
        layers = {"Fn": {"4": SpaceMouseButton(press=SpaceMouseAction("key_hold", keys=["KEY_Z"]))}}
        server = self.start(self.base_buttons("hold"), layers)
        self.run_steps(
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(4, True)),
            lambda: self.deliver(server, button(3, False)),
            lambda: None,
            lambda: self.assertEqual(self.driver["spacemouse_target_keys"][0].name, "KEY_Z"),
            lambda: self.deliver(server, button(4, False)),     # its release is the layer one's, not the base F5
            lambda: None,
            lambda: self.assertEqual(self.driver["spacemouse_target_keys"], []),
        )
        self.assertEqual(self.macros(), [])

    def test_latch_is_for_the_next_press_only(self):
        server = self.start(self.base_buttons("latch"), self.LAYERS)
        self.run_steps(
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(3, False)),
            lambda: self.assertEqual(self.source().layers.active, "Fn"),
            lambda: self.deliver(server, button(4, True)),
            lambda: self.deliver(server, button(4, False)),
            lambda: self.assertEqual(self.source().layers.active, ""),
            lambda: self.deliver(server, button(4, True)),
            lambda: None,
        )
        self.assertEqual(self.macros(), [["KEY_F6"], ["KEY_F5"]])

    def test_latch_pressed_again_is_cancelled_without_running_anything(self):
        server = self.start(self.base_buttons("latch"), self.LAYERS)
        self.run_steps(
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(3, False)),
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(3, False)),
            lambda: self.assertEqual(self.source().layers.active, ""),
            lambda: self.deliver(server, button(4, True)),
            lambda: None,
        )
        self.assertEqual(self.macros(), [["KEY_F5"]])

    def test_an_unbound_press_also_uses_up_the_latch(self):
        server = self.start(self.base_buttons("latch"), self.LAYERS)
        self.run_steps(
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(3, False)),
            lambda: self.deliver(server, button(40, True)),   # bound nowhere
            lambda: self.assertEqual(self.source().layers.active, ""),
        )

    def test_toggle_stays_until_pressed_again(self):
        server = self.start(self.base_buttons("toggle"), self.LAYERS)
        self.run_steps(
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(3, False)),
            lambda: self.deliver(server, button(4, True)),
            lambda: self.deliver(server, button(4, False)),
            lambda: self.deliver(server, button(4, True)),
            lambda: self.deliver(server, button(4, False)),
            lambda: self.assertEqual(self.source().layers.active, "Fn"),
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(3, False)),
            lambda: self.assertEqual(self.source().layers.active, ""),
            lambda: self.deliver(server, button(4, True)),
            lambda: None,
        )
        self.assertEqual(self.macros(), [["KEY_F6"], ["KEY_F6"], ["KEY_F5"]])

    def test_the_layer_button_ends_a_toggle_even_when_the_layer_rebinds_it(self):
        layers = {"Fn": {"3": SpaceMouseButton(press=SpaceMouseAction("macro", keys=["KEY_F10"]))}}
        server = self.start(self.base_buttons("toggle"), layers)
        self.run_steps(
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(3, False)),
            lambda: self.assertEqual(self.source().layers.active, "Fn"),
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(3, False)),
            lambda: self.assertEqual(self.source().layers.active, ""),
        )
        self.assertEqual(self.macros(), [])

    def test_toggle_ends_when_the_application_changes_its_profile(self):
        server = self.start(self.base_buttons("toggle"), self.LAYERS)
        self.run_steps(
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(3, False)),
            lambda: self.assertEqual(self.source().layers.active, "Fn"),
            lambda: self.driver["window_was_changed"]("org.gnome.Nautilus.desktop"),
            lambda: None,
            lambda: self.assertEqual(self.source().layers.active, ""),
        )

    def test_toggle_stays_when_two_applications_share_the_default_profile(self):
        server = self.start(self.base_buttons("toggle"), self.LAYERS)
        self.run_steps(
            lambda: self.driver["window_was_changed"]("org.example.One.desktop"),
            lambda: None,
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(3, False)),
            lambda: self.driver["window_was_changed"]("org.example.Two.desktop"),
            lambda: None,
            lambda: self.assertEqual(self.source().layers.active, "Fn"),
        )

    def test_a_hold_layer_survives_a_profile_change_until_its_button_is_up(self):
        server = self.start(self.base_buttons("hold"), self.LAYERS)
        self.run_steps(
            lambda: self.deliver(server, button(3, True)),
            lambda: self.driver["window_was_changed"]("org.gnome.Nautilus.desktop"),
            lambda: None,
            lambda: self.assertEqual(self.source().layers.active, "Fn"),
            lambda: self.deliver(server, button(3, False)),
            lambda: self.assertEqual(self.source().layers.active, ""),
        )

    def test_a_profile_layer_lies_over_the_global_one_per_button(self):
        krita_layers = {"Fn": {"4": macro("KEY_F7")}}
        server = self.start(self.base_buttons("hold"), self.LAYERS, krita_layers=krita_layers)
        self.run_steps(
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(4, True)),     # the profile's
            lambda: self.deliver(server, button(12, True)),    # the global layer's, no profile entry
            lambda: None,
        )
        self.assertEqual(self.macros(), [["KEY_F7"], ["KEY_F2"]])

    def test_a_layer_defined_only_in_the_profile(self):
        server = self.start(
            {"3": layer_button("Local")}, krita_layers={"Local": {"4": macro("KEY_F8")}},
        )
        self.run_steps(
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(4, True)),
            lambda: None,
        )
        self.assertEqual(self.macros(), [["KEY_F8"]])

    def test_a_profile_none_in_a_layer_leaves_the_button_alone(self):
        krita_layers = {"Fn": {"4": SpaceMouseButton(press=SpaceMouseAction("none"))}}
        server = self.start(self.base_buttons("hold"), self.LAYERS, krita_layers=krita_layers)
        self.run_steps(
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(4, True)),
            lambda: None,
        )
        self.assertEqual(self.macros(), [])

    def test_the_layer_does_not_end_a_menu_waiting_for_its_opener(self):
        server = self.start({"3": layer_button("Fn"), "4": macro("KEY_F5")}, self.LAYERS, **{})
        # The global map without the default: the menu button is bound here.
        self.driver["gui_config"].spacemouse.buttons["0"] = SpaceMouseButton(press=SpaceMouseAction("dial"))
        self.run_steps(
            lambda: self.deliver(server, button(0, True)),
            lambda: self.deliver(server, button(0, False)),      # sticky menu
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(3, False)),
            lambda: self.assertTrue(self.driver["dialpad"]),
            lambda: self.assertEqual(self.source().buttons.opener, 0),
        )

    def test_losing_the_connection_ends_every_layer(self):
        server = self.start(self.base_buttons("toggle"), self.LAYERS)
        self.run_steps(
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(3, False)),
            lambda: self.assertEqual(self.source().layers.active, "Fn"),
            lambda: server.drop_client(),
            lambda: None,
            lambda: None,
            lambda: self.assertEqual(self.source().layers.active, ""),
        )

    def test_control_can_latch_a_layer_and_press_a_button_in_it(self):
        server = self.start(self.base_buttons("latch"), self.LAYERS)
        self.run_steps(
            lambda: None,
            lambda: self.driver["apply_control"](("button", 3)),
            lambda: self.assertEqual(self.source().layers.active, "Fn"),
            lambda: self.driver["apply_control"](("button", 4)),     # runs the layer's action
            lambda: None,
        )
        self.assertEqual(self.macros(), [["KEY_F6"]])

    def test_control_cannot_hold_a_layer(self):
        server = self.start(self.base_buttons("hold"), self.LAYERS)
        self.run_steps(
            lambda: None,
            lambda: self.driver["apply_control"](("button", 3)),
            lambda: self.assertEqual(self.source().layers.active, ""),
        )

    def test_hide_buttons_covers_layer_bindings(self):
        server = self.start(
            {"3": layer_button("Fn")}, {"Fn": {"40": macro("KEY_F9")}},
            krita_layers={"Local": {"41": macro("KEY_F9")}}, hide_buttons=True,
        )
        hidden = lambda: {b for b, m in server.button_map.items() if m == 63}
        self.run_steps(
            lambda: None,
            lambda: self.assertTrue({3, 40, 41} <= hidden()),
            lambda: self.driver["window_was_changed"]("org.gnome.Nautilus.desktop"),
            lambda: None,
            lambda: self.assertIn(40, hidden()),
            lambda: self.assertNotIn(41, hidden()),     # Krita's layer is not Nautilus'
            lambda: self.assertEqual(server.button_map.get(41, 41), 41),
        )

    def test_a_layer_binding_may_stay_unhidden_when_nothing_binds_it(self):
        server = self.start(
            {"3": layer_button("Fn")}, {"Fn": {"40": SpaceMouseButton(press=SpaceMouseAction("none"))}}, hide_buttons=True,
        )
        hidden = lambda: {b for b, m in server.button_map.items() if m == 63}
        self.run_steps(lambda: None, lambda: self.assertNotIn(40, hidden()))

    def test_driver_state_names_the_layer(self):
        server = self.start(self.base_buttons("toggle"), self.LAYERS)
        self.run_steps(
            lambda: self.deliver(server, button(3, True)),
            lambda: self.deliver(server, button(3, False)),
            lambda: self.assertEqual(self.source().layers.active, "Fn"),
        )
        driver = self.driver
        driver.update(
            config_status={"revision": "r"}, driver_state_publish_lock=__import__("threading").Lock(),
        )
        state = driver["driver_state"]()
        self.assertEqual(state["layer"], "Fn")
        self.assertEqual(state["motion"], {"sensitivity": 1.0, "rotation": True, "translation": True})


class MotionLoopTest(SpaceMouseTestCase):
    """The cap's motion settings in spacenavd, per application."""

    autorun = True

    def start(self, motion=None, krita=None, files=None, daemon=None, **kwargs):
        server = self.start_server()
        for name, value in (daemon or {}).items():
            setattr(server, name, value)
        config = self.config(server, motion=motion or {}, **kwargs)
        config.profiles[1].spacemouse_motion = krita or {}
        config.profiles[2].spacemouse_motion = files or {}
        self.load(config)
        self.server = server
        return server

    def saved(self):
        return proto.RestoreFile(self.restore_path).load()

    def assertAxes(self, server, expected):
        for got, want in zip(server.axis_sensitivity, expected):
            self.assertAlmostEqual(got, want, places=5)

    def test_nothing_is_touched_while_the_motion_is_spacenavds_own(self):
        server = self.start(daemon={"sensitivity": 1.5})
        self.run_steps(*self.pump(3))
        self.assertFalse([request for request, _ in server.requests if request in (
            proto.REQ_GCFG_SENS_AXIS, proto.REQ_SCFG_SENS_AXIS, proto.REQ_GCFG_INVERT, proto.REQ_SCFG_INVERT,
            proto.REQ_SCFG_SENS,
        )])
        self.assertIsNone(self.saved())
        self.assertEqual(server.sensitivity, 1.5)

    def test_the_global_motion_is_applied_on_connect_and_given_back_on_exit(self):
        server = self.start(
            motion={"sensitivity": 2.0, "rotation": False, "invert": [True, False, False, False, False, False]},
            daemon={"sensitivity": 1.5, "axis_sensitivity": [1, 1, 2, 1, 1, 0.5], "invert": [0, 0, 0, 0, 1, 0]},
        )
        self.run_steps(*self.pump(3))
        self.assertAlmostEqual(server.sensitivity, 3.0, places=5)
        self.assertAxes(server, [1, 1, 2, 0, 0, 0])
        self.assertEqual(server.invert, [1, 0, 0, 0, 1, 0])
        saved = self.saved()
        self.assertEqual(saved["motion"]["sens"], 1.5)
        self.assertEqual(saved["motion"]["axis"], [1, 1, 2, 1, 1, 0.5])
        self.assertEqual(saved["motion"]["invert"], [0, 0, 0, 0, 1, 0])
        self.assertEqual(self.source().motion_shown, {"sensitivity": 2.0, "rotation": False, "translation": True})
        self.source().close()
        self.assertAlmostEqual(server.sensitivity, 1.5, places=5)
        self.assertAxes(server, [1, 1, 2, 1, 1, 0.5])
        self.assertEqual(server.invert, [0, 0, 0, 0, 1, 0])
        self.assertIsNone(self.saved())
        # Never the save, restore or reset requests of the daemon.
        self.assertTrue(all(request < 0x3ffe for request, _ in server.requests))

    def test_the_original_is_saved_before_the_first_change(self):
        server = self.start(motion={"sensitivity": 2.0})
        seen = []
        original = server.request

        def spy(client, request, data, strings):
            if request in (proto.REQ_SCFG_SENS, proto.REQ_SCFG_SENS_AXIS, proto.REQ_SCFG_INVERT):
                seen.append((request, self.saved()))
            return original(client, request, data, strings)

        server.request = spy
        self.run_steps(*self.pump(3))
        self.assertTrue(seen)
        self.assertTrue(all(saved is not None and "motion" in saved for _request, saved in seen))

    def test_each_application_has_its_own_motion(self):
        server = self.start(
            motion={"sensitivity": 1.5}, krita={"translation": False}, files={"sensitivity": 0.5},
        )
        self.run_steps(
            lambda: None,
            lambda: None,
            lambda: (self.assertAlmostEqual(server.sensitivity, 1.5, places=5), self.assertAxes(server, [0, 0, 0, 1, 1, 1])),
            lambda: self.driver["window_was_changed"]("org.gnome.Nautilus.desktop"),
            lambda: None,
            lambda: (self.assertAlmostEqual(server.sensitivity, 0.5, places=5), self.assertAxes(server, [1] * 6)),
            lambda: self.driver["window_was_changed"]("org.example.Other.desktop"),    # the default profile: global only
            lambda: None,
            lambda: (self.assertAlmostEqual(server.sensitivity, 1.5, places=5), self.assertAxes(server, [1] * 6)),
        )

    def test_a_neutral_application_gives_the_daemon_its_own_values_back(self):
        server = self.start(
            krita={"sensitivity": 2.0, "rotation": False}, daemon={"sensitivity": 1.25},
        )
        self.run_steps(
            lambda: None,
            lambda: None,
            lambda: self.assertAlmostEqual(server.sensitivity, 2.5, places=5),
            lambda: self.assertIsNotNone(self.saved()),
            lambda: self.driver["window_was_changed"]("org.gnome.Nautilus.desktop"),
            lambda: None,
            lambda: None,
            lambda: (
                self.assertAlmostEqual(server.sensitivity, 1.25, places=5),
                self.assertAxes(server, [1] * 6),
                self.assertIsNone(self.saved()),
            ),
        )

    def test_the_profile_overrides_the_global_motion_per_key(self):
        server = self.start(motion={"sensitivity": 2.0, "rotation": False}, krita={"rotation": True})
        self.run_steps(
            lambda: None, lambda: None,
            lambda: (self.assertAlmostEqual(server.sensitivity, 2.0, places=5), self.assertAxes(server, [1] * 6)),
        )

    def test_the_mute_gives_back_the_profile_sensitivity_not_the_original(self):
        server = self.start(motion={"sensitivity": 2.0}, daemon={"sensitivity": 1.0}, files={"sensitivity": 3.0})
        self.run_steps(
            lambda: None, lambda: None,
            lambda: self.assertAlmostEqual(server.sensitivity, 2.0, places=5),
            lambda: self.deliver(server, button(0, True)),     # the dial on: muted
            lambda: self.deliver(server, button(0, False)),
            lambda: None,
            lambda: (self.assertEqual(server.sensitivity, 0.0), self.assertAlmostEqual(self.saved()["sensitivity"], 2.0, places=5)),
            lambda: self.driver["window_was_changed"]("org.gnome.Nautilus.desktop"),   # the profile changes while muted
            lambda: None,
            lambda: (self.assertEqual(server.sensitivity, 0.0), self.assertAlmostEqual(self.saved()["sensitivity"], 3.0, places=5)),
            lambda: self.deliver(server, button(0, True)),     # the dial off
            lambda: self.deliver(server, button(0, False)),
            lambda: None,
            lambda: self.assertAlmostEqual(server.sensitivity, 3.0, places=5),
        )
        self.assertEqual(self.saved()["motion"]["sens"], 1.0)

    def test_the_original_is_read_from_the_saved_value_when_first_applied_while_muted(self):
        server = self.start(files={"sensitivity": 2.0}, daemon={"sensitivity": 1.5})
        self.run_steps(
            lambda: None,
            lambda: self.deliver(server, button(0, True)),     # muted in Krita (neutral)
            lambda: self.deliver(server, button(0, False)),
            lambda: None,
            lambda: self.assertEqual(server.sensitivity, 0.0),
            lambda: self.driver["window_was_changed"]("org.gnome.Nautilus.desktop"),
            lambda: None,
            lambda: None,
            lambda: self.assertEqual(self.saved()["motion"]["sens"], 1.5),
            lambda: self.deliver(server, button(0, True)),
            lambda: self.deliver(server, button(0, False)),
            lambda: None,
            lambda: self.assertAlmostEqual(server.sensitivity, 3.0, places=5),
        )
        self.source().close()
        self.assertAlmostEqual(server.sensitivity, 1.5, places=5)
        self.assertIsNone(self.saved())

    def test_exit_while_muted_gives_the_original_back(self):
        server = self.start(motion={"sensitivity": 2.0}, daemon={"sensitivity": 1.5})
        self.run_steps(lambda: self.deliver(server, button(0, True)), *self.pump(3))
        self.assertEqual(server.sensitivity, 0.0)
        self.source().close()
        self.assertAlmostEqual(server.sensitivity, 1.5, places=5)
        self.assertIsNone(self.saved())

    def test_a_crashed_run_is_given_back_at_the_next_connection(self):
        server = self.start(daemon={"sensitivity": 9.0, "axis_sensitivity": [0] * 6, "invert": [1] * 6})
        proto.RestoreFile(self.restore_path).save({"motion": {"sens": 1.25, "axis": [1, 1, 1, 2, 2, 2], "invert": [0, 1, 0, 0, 0, 0]}})
        self.run_steps(*self.pump(2))
        self.assertAlmostEqual(server.sensitivity, 1.25, places=5)
        self.assertAxes(server, [1, 1, 1, 2, 2, 2])
        self.assertEqual(server.invert, [0, 1, 0, 0, 0, 0])
        self.assertIsNone(self.saved())

    def test_a_crashed_run_with_a_broken_motion_entry_does_no_harm(self):
        server = self.start(daemon={"sensitivity": 9.0})
        proto.RestoreFile(self.restore_path).save({"motion": {"sens": "x", "axis": [1], "invert": 3}})
        self.run_steps(*self.pump(2))
        self.assertEqual(server.sensitivity, 9.0)
        self.assertIsNone(self.saved())

    def test_a_lost_connection_keeps_the_file_and_reconnecting_restores_then_reapplies(self):
        server = self.start(motion={"sensitivity": 2.0}, daemon={"sensitivity": 1.5})
        self.run_steps(
            lambda: None, lambda: None,
            lambda: self.assertAlmostEqual(server.sensitivity, 3.0, places=5),
            lambda: server.drop_client(),
            lambda: None,
            lambda: self.assertIsNone(self.source().motion_original),
            lambda: self.assertEqual(self.saved()["motion"]["sens"], 1.5),
        )
        self.run_steps(*self.pump(3))
        self.assertAlmostEqual(server.sensitivity, 3.0, places=5)
        self.assertEqual(self.saved()["motion"]["sens"], 1.5)

    def test_a_reload_applies_the_new_motion(self):
        import copy
        server = self.start(motion={"sensitivity": 2.0}, daemon={"sensitivity": 1.0})

        def reload():
            config = copy.copy(self.driver["gui_config"])
            config.spacemouse = copy.copy(config.spacemouse)
            config.spacemouse.motion = {"sensitivity": 0.5}
            self.driver["gui_config"] = config

        self.run_steps(
            lambda: None, lambda: None,
            lambda: self.assertAlmostEqual(server.sensitivity, 2.0, places=5),
            reload,
            lambda: None,
            lambda: self.assertAlmostEqual(server.sensitivity, 0.5, places=5),
        )

    def press(self, server, index):
        return [
            lambda: self.deliver(server, button(index, True)),
            lambda: self.deliver(server, button(index, False)),
            lambda: None,
        ]

    def test_the_rotation_and_pan_buttons_switch_the_axis_groups(self):
        server = self.start()
        self.run_steps(
            lambda: None,
            *self.press(server, 26),
            lambda: (self.assertAxes(server, [1, 1, 1, 0, 0, 0]), self.assertIsNotNone(self.saved())),
            *self.press(server, 27),
            lambda: self.assertAxes(server, [0] * 6),
            lambda: self.assertEqual(self.source().motion_shown, {"sensitivity": 1.0, "rotation": False, "translation": False}),
            *self.press(server, 26),
            *self.press(server, 27),
            lambda: self.assertAxes(server, [1] * 6),
            lambda: self.assertIsNone(self.saved()),   # neutral again: nothing left to give back
        )

    def test_plus_and_minus_step_the_sensitivity_and_the_motion_shows_it(self):
        server = self.start(daemon={"sensitivity": 2.0})
        self.run_steps(
            lambda: None,
            *self.press(server, 29),
            lambda: self.assertAlmostEqual(server.sensitivity, 2.5, places=5),     # 1.25 times the daemon's own
            lambda: self.assertEqual(self.source().motion_shown["sensitivity"], 1.25),
            *self.press(server, 29),
            lambda: self.assertAlmostEqual(server.sensitivity, 3.0, places=5),     # 1.5
            *self.press(server, 30),
            *self.press(server, 30),
            *self.press(server, 30),
            lambda: self.assertAlmostEqual(server.sensitivity, 1.5, places=5),     # 0.75
        )

    def test_the_session_belongs_to_the_application_and_reset_undoes_it(self):
        server = self.start()
        self.run_steps(
            lambda: None,
            *self.press(server, 29),
            lambda: self.assertAlmostEqual(server.sensitivity, 1.25, places=5),
            lambda: self.driver["window_was_changed"]("org.gnome.Nautilus.desktop"),
            lambda: None,
            lambda: self.assertAlmostEqual(server.sensitivity, 1.0, places=5),     # another application
            lambda: self.driver["window_was_changed"]("org.kde.krita.desktop"),
            lambda: None,
            lambda: self.assertAlmostEqual(server.sensitivity, 1.25, places=5),    # back: remembered
            lambda: self.driver["gui_config"].spacemouse.buttons.update({"28": motion_button("reset")}),
            lambda: self.driver["gui_config"].spacemouse.buttons.update(default_spacemouse_buttons()),
            *self.press(server, 28),
            lambda: self.assertAlmostEqual(server.sensitivity, 1.0, places=5),
            lambda: self.assertIsNone(self.saved()),
        )

    def test_session_changes_are_not_saved_in_the_configuration(self):
        server = self.start()
        self.run_steps(lambda: None, *self.press(server, 26))
        self.assertEqual(self.driver["gui_config"].spacemouse.motion, {})

    def test_control_can_press_a_motion_button(self):
        server = self.start()
        self.run_steps(
            lambda: None,
            lambda: self.driver["apply_control"](("button", 27)),
            lambda: None,
            lambda: self.assertAxes(server, [0, 0, 0, 1, 1, 1]),
        )

    def test_motion_is_not_asked_of_an_old_daemon(self):
        server = self.start_server(version=None)
        config = self.config(server, motion={"sensitivity": 2.0})
        self.load(config)
        self.run_steps(*self.pump(3))
        self.assertFalse(self.source().motion_original)


class LegacyAdapterTest(unittest.TestCase):
    def test_layers_and_motion_reach_the_profile_meta(self):
        config = DialPadConfig()
        config.profiles = [Profile(
            name="P", app_id="p.desktop",
            segments=[Segment(title="A", macro_center=[MacroStep(type=StepType.KEYSTROKE, keys=["KEY_A"])])],
            spacemouse_layers={"Fn": {"4": macro("KEY_F5")}},
            spacemouse_motion={"sensitivity": 2.0},
        ), Profile(name="Default", segments=[Segment(title="B", macro_center=[MacroStep(type=StepType.KEYSTROKE, keys=["KEY_B"])])])]
        shortcuts = shortcuts_from_config(config)
        meta = shortcuts["p.desktop"]["_meta"]
        self.assertEqual(meta["spacemouse_motion"], {"sensitivity": 2.0})
        self.assertEqual(sorted(meta["spacemouse_layers"]), ["Fn"])
        self.assertNotIn("spacemouse_layers", shortcuts["none"]["_meta"])


if __name__ == "__main__":
    unittest.main()
