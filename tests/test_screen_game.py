"""screen/game.py: the hidden Space Pilot game, without hardware, and how the
screen's UI starts and leaves it."""

import sys
import unittest
from pathlib import Path

import cairo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.schema import ScreenConfig, SpaceMouseConfig
from screen import game as space
from screen.canvas import HEIGHT, WIDTH, device_surface, frame_bytes, preview_surface
from screen.game import CapReader, Game, Konami
from screen.pages import Theme
from screen.ui import GAME_IDLE_SECONDS, ScreenUI

STEP = 1 / 30
CODE = ["up", "up", "down", "down", "left", "right", "left", "right", "back", "ok"]


def steps(game, seconds):
    for _ in range(round(seconds / STEP)):
        game.step(STEP)


def playing(seed=1, **kwargs):
    game = Game(seed=seed, **kwargs)
    game.press("ok")
    # Nothing spawns on its own unless a test wants it.
    game.spawn_timer = 1e9
    return game


class KonamiTests(unittest.TestCase):
    def feed(self, keys, detector=None):
        detector = detector or Konami()
        return [detector.feed(key) for key in keys]

    def test_the_code_is_detected_on_the_last_key_only(self):
        self.assertEqual(self.feed(CODE), [False] * 9 + [True])

    def test_wrong_sequences(self):
        for keys in (CODE[:-1], CODE[:-2] + ["ok", "back"], ["down"] * 10, CODE[1:], CODE[::-1],
                     CODE[:4] + ["right", "left", "right", "left", "back", "ok"]):
            self.assertNotIn(True, self.feed(keys), keys)

    def test_a_mistake_resets_but_the_code_may_start_inside_it(self):
        detector = Konami()
        self.assertNotIn(True, self.feed(["up", "up", "down", "left"], detector))
        self.assertEqual(self.feed(CODE, detector)[-1], True)
        # An extra Up in front still ends in the code.
        self.assertEqual(self.feed(["up"] + CODE, Konami())[-1], True)

    def test_can_be_typed_again(self):
        detector = Konami()
        self.assertTrue(self.feed(CODE, detector)[-1])
        self.assertEqual(self.feed(CODE, detector), [False] * 9 + [True])

    def test_reset(self):
        detector = Konami()
        self.feed(CODE[:9], detector)
        detector.reset()
        self.assertFalse(detector.feed("ok"))


class CapReaderTests(unittest.TestCase):
    def test_defaults_follow_the_config(self):
        reader = CapReader.from_config(SpaceMouseConfig())
        axes = [0] * 6
        axes[5] = 200  # tilt right
        self.assertEqual(reader.read(axes)[0], 0.5)
        axes[5] = -800
        self.assertEqual(reader.read(axes)[0], -1.0)  # clamped
        self.assertEqual(reader.read([0] * 6), (0.0, 0.0, False))

    def test_invert_and_axis_max(self):
        config = SpaceMouseConfig(axis_max=100, tilt_axes=[2, 3], tilt_invert=[True, False], twist_axis=1, twist_invert=False)
        reader = CapReader.from_config(config)
        axes = [0, 50, 40, 0, 0, 0]
        tilt, twist, _fire = reader.read(axes)
        self.assertEqual((tilt, twist), (-0.4, 0.5))

    def test_twist_default_is_inverted(self):
        axes = [0] * 6
        axes[4] = -300
        self.assertEqual(CapReader.from_config(SpaceMouseConfig()).read(axes)[1], 0.75)

    def test_push_hysteresis(self):
        reader = CapReader(axis_max=400)
        fire = []
        for value in (0, -150, -199, -200, -150, -141, -139, 0):  # push down = axis 1 negative
            axes = [0, value, 0, 0, 0, 0]
            fire.append(reader.read(axes)[2])
        # on at 50 % (200), off below 35 % (140)
        self.assertEqual(fire, [False, False, False, True, True, True, False, False])

    def test_a_missing_axis_is_zero(self):
        self.assertEqual(CapReader(tilt_axis=9).read([1, 2, 3])[0], 0.0)


class PlayTests(unittest.TestCase):
    def test_title_ok_starts_and_back_leaves(self):
        game = Game(seed=1)
        self.assertEqual(game.state, "title")
        self.assertIsNone(game.press("left"))
        self.assertEqual(game.state, "title")
        self.assertIsNone(game.press("ok"))
        self.assertEqual(game.state, "playing")
        self.assertEqual(Game(seed=1).press("back"), "exit")
        self.assertEqual(Game(seed=1).press("menu"), "exit")

    def test_pause_resume_and_leave(self):
        game = playing()
        self.assertIsNone(game.press("back"))
        self.assertEqual(game.state, "paused")
        before = game.time
        steps(game, 1)
        self.assertEqual(game.time, before)  # time does not run
        self.assertIsNone(game.press("ok"))
        self.assertEqual(game.state, "playing")
        game.press("back")
        self.assertEqual(game.press("back"), "exit")
        game = playing()
        self.assertEqual(game.press("menu"), "exit")

    def test_menu_always_leaves(self):
        for state in ("title", "playing", "paused", "dying", "over"):
            game = Game(seed=1)
            game.state = state
            self.assertEqual(game.press("menu"), "exit", state)

    def test_over_ok_again_back_leave(self):
        game = playing()
        game.state = "over"
        game.score = 90
        self.assertEqual(game.press("back"), "exit")
        self.assertIsNone(game.press("ok"))
        self.assertEqual((game.state, game.score, game.lives), ("playing", 0, 3))

    def test_ship_steers_with_tilt_and_keys_and_stays_on_screen(self):
        game = playing()
        start = game.ship_x
        game.set_input(tilt=1.0)
        steps(game, 0.2)
        self.assertAlmostEqual(game.ship_x - start, space.SHIP_SPEED * 0.2, delta=2)
        game.set_input(tilt=0.5)
        before = game.ship_x
        steps(game, 0.2)
        moved_half = game.ship_x - before
        self.assertTrue(0 < moved_half < space.SHIP_SPEED * 0.2 * 0.6)  # speed follows the tilt
        game.set_input(tilt=0.05)  # the dead zone
        before = game.ship_x
        steps(game, 0.5)
        self.assertEqual(game.ship_x, before)
        game.set_input(tilt=0)
        game.press("left")
        steps(game, 0.1)
        self.assertLess(game.ship_x, before)
        game.release("left")
        game.press("right")
        steps(game, 5)
        self.assertEqual(game.ship_x, game.width - space.SHIP_RADIUS - 2)
        game.release("right")
        game.press("left")
        steps(game, 5)
        self.assertEqual(game.ship_x, space.SHIP_RADIUS + 2)

    def test_fire_with_push_or_ok_at_a_fixed_rate(self):
        game = playing()
        steps(game, 1)
        self.assertEqual(game.bullets, [])
        game.set_input(fire=True)
        shots = 0
        for _ in range(30):
            game.step(STEP)
            shots += game.fire_timer == space.FIRE_INTERVAL
        self.assertTrue(4 <= shots <= 5, shots)  # one every 0.2 s
        game.set_input(fire=False)
        game.bullets.clear()
        game.press("ok")
        steps(game, 0.5)
        self.assertGreaterEqual(len(game.bullets), 2)
        game.release("ok")
        game.bullets.clear()
        steps(game, 0.5)
        self.assertEqual(game.bullets, [])

    def test_shooting_a_rock_scores_and_splits_it(self):
        game = playing()
        game.spawn_rock(game.ship_x, 100, 3, vx=0, vy=0)
        game.set_input(fire=True)
        steps(game, 1.0)
        self.assertEqual(game.score, space.ROCK_SCORE[3])
        self.assertEqual(sorted(rock.size for rock in game.rocks), [2, 2])
        self.assertTrue(game.particles)

    def test_the_smallest_rock_just_goes_and_scores_more(self):
        game = playing()
        game.spawn_rock(game.ship_x, 100, 1, vx=0, vy=0)
        game.set_input(fire=True)
        steps(game, 1.0)
        self.assertEqual(game.score, space.ROCK_SCORE[1])
        self.assertEqual(game.rocks, [])

    def test_a_collision_costs_a_life_and_blinks(self):
        game = playing()
        game.spawn_rock(game.ship_x, game.ship_y - 5, 2, vx=0, vy=0)
        steps(game, STEP * 2)
        self.assertEqual(game.lives, 2)
        self.assertGreater(game.invulnerable, 1.5)
        self.assertEqual(game.rocks, [])
        # Invulnerable: a rock through the ship does nothing.
        game.spawn_rock(game.ship_x, game.ship_y, 2, vx=0, vy=0)
        steps(game, STEP * 2)
        self.assertEqual(game.lives, 2)
        steps(game, 2.5)
        game.spawn_rock(game.ship_x, game.ship_y, 2, vx=0, vy=0)
        steps(game, STEP * 2)
        self.assertEqual(game.lives, 1)

    def test_shield_absorbs_one_hit(self):
        game = playing()
        game.shield = True
        game.spawn_rock(game.ship_x, game.ship_y, 3, vx=0, vy=0)
        steps(game, STEP * 2)
        self.assertEqual(game.lives, 3)
        self.assertFalse(game.shield)
        steps(game, 1.0)
        game.spawn_rock(game.ship_x, game.ship_y, 3, vx=0, vy=0)
        steps(game, STEP * 2)
        self.assertEqual(game.lives, 2)

    def test_powerups_are_caught(self):
        game = playing()
        game.powerups.append(space.Powerup(game.ship_x, game.ship_y - 4, "shield"))
        game.powerups.append(space.Powerup(game.ship_x, game.ship_y - 4, "triple"))
        steps(game, STEP * 2)
        self.assertTrue(game.shield)
        self.assertGreater(game.triple_left, 9)
        self.assertEqual(game.powerups, [])
        # Triple shot fires three bullets, then wears off after 10 s.
        game.set_input(fire=True)
        steps(game, STEP * 2)
        self.assertEqual(len(game.bullets), 3)
        steps(game, 10.5)
        self.assertEqual(game.triple_left, 0)
        game.bullets.clear()
        game.fire_timer = 0
        steps(game, STEP)
        self.assertEqual(len(game.bullets), 1)

    def test_destroyed_rocks_sometimes_drop_powerups(self):
        game = playing(seed=3)
        for _ in range(200):
            game.spawn_rock(100, 50, 1, vx=0, vy=0)
            game._destroy_rock(game.rocks[-1])
        self.assertTrue(game.powerups)
        self.assertTrue({p.kind for p in game.powerups} <= set(space.POWERUP_KINDS))

    def test_level_up_raises_the_pace_and_rearms_the_bomb(self):
        game = playing()
        slow_interval = game._spawn_interval()
        slow_speed = game._speed_factor()
        game.bomb_ready = False
        game._add_score(space.LEVEL_POINTS)
        self.assertEqual(game.level, 2)
        self.assertTrue(game.bomb_ready)
        self.assertGreater(game.banner, 0)
        self.assertLess(game._spawn_interval(), slow_interval)
        self.assertGreater(game._speed_factor(), slow_speed)

    def test_bomb_clears_the_screen_once_per_level(self):
        game = playing()
        for x in (60, 120, 180):
            game.spawn_rock(x, 80, 3, vx=0, vy=0)
        self.assertTrue(game.bomb_ready)
        game.set_input(twist=0.8)
        steps(game, STEP * 2)
        self.assertEqual(game.rocks, [])
        self.assertFalse(game.bomb_ready)
        self.assertEqual(game.score, 3 * space.ROCK_SCORE[3])
        self.assertGreater(game.flash, 0)
        # Again in the same level: nothing happens, whatever the twist does.
        game.set_input(twist=0)
        steps(game, STEP * 2)
        game.spawn_rock(120, 80, 3, vx=0, vy=0)
        game.set_input(twist=-1.0)
        steps(game, STEP * 2)
        self.assertEqual(len(game.rocks), 1)
        # A new level arms it again.
        game.set_input(twist=0)
        steps(game, STEP)
        game._add_score(space.LEVEL_POINTS)
        game.set_input(twist=-1.0)
        steps(game, STEP * 2)
        self.assertEqual(game.rocks, [])

    def test_a_gentle_twist_is_not_a_bomb(self):
        game = playing()
        game.spawn_rock(120, 80, 3, vx=0, vy=0)
        game.set_input(twist=0.5)
        steps(game, STEP * 3)
        self.assertEqual(len(game.rocks), 1)
        self.assertTrue(game.bomb_ready)

    def test_three_lives_then_game_over_with_a_high_score(self):
        game = playing(high_score=100)
        game.score = 250
        for life in range(3):
            steps(game, 2.5)  # the blinking is over
            game.spawn_rock(game.ship_x, game.ship_y, 2, vx=0, vy=0)
            steps(game, STEP * 2)
        self.assertEqual(game.lives, 0)
        self.assertEqual(game.state, "dying")
        self.assertTrue(game.running)
        steps(game, space.DYING_SECONDS + 0.2)
        self.assertEqual(game.state, "over")
        self.assertFalse(game.running)
        self.assertTrue(game.new_high)
        self.assertEqual(game.high_score, 250)
        game.press("ok")
        self.assertEqual((game.state, game.high_score, game.new_high), ("playing", 250, False))

    def test_game_over_below_the_record(self):
        game = playing(high_score=1000)
        game.score = 10
        game.lives = 1
        game.spawn_rock(game.ship_x, game.ship_y, 2, vx=0, vy=0)
        steps(game, 3)
        self.assertEqual(game.state, "over")
        self.assertFalse(game.new_high)
        self.assertEqual(game.high_score, 1000)

    def test_a_zero_score_is_no_high_score(self):
        game = playing()
        game.lives = 1
        game.spawn_rock(game.ship_x, game.ship_y, 2, vx=0, vy=0)
        steps(game, 3)
        self.assertFalse(game.new_high)

    def test_rocks_fall_off_the_bottom_and_spawn_by_themselves(self):
        game = Game(seed=5)
        game.press("ok")
        steps(game, 3)
        self.assertGreater(len(game.rocks) + game.score, 0)
        game.rocks = [rock for rock in game.rocks if rock.y > 0]
        game.spawn_rock(10, game.floor + 100, 1)
        steps(game, STEP)
        self.assertTrue(all(rock.y - rock.radius <= game.floor for rock in game.rocks))

    def test_deterministic_for_a_seed(self):
        def run(seed):
            game = Game(seed=seed)
            game.press("ok")
            game.set_input(fire=True)
            for index in range(600):
                game.set_input(tilt=((index // 40) % 3 - 1) * 0.7)
                game.step(STEP)
            return (game.score, game.lives, round(game.ship_x, 6), [(round(r.x, 6), round(r.y, 6), r.size) for r in game.rocks])

        self.assertEqual(run(11), run(11))
        self.assertNotEqual(run(11), run(12))

    def test_a_long_pause_in_time_does_not_skip_through_things(self):
        game = playing()
        game.spawn_rock(game.ship_x, game.ship_y - 60, 2, vx=0, vy=300)
        game.step(10)  # clamped to a short step
        self.assertLess(game.time, 0.2)


def empty_space():
    """One pixel of the game's background as the surface stores it."""
    surface, cr = preview_surface(1)
    cr.set_source_rgb(*space.SPACE)
    cr.paint()
    surface.flush()
    return bytes(surface.get_data())[:4]


class PaintTests(unittest.TestCase):
    THEME = Theme((0, 0, 0), (0, 0, 0), (0, 0, 0), (1, 1, 1), (0.5, 0.5, 0.5), (0.3, 0.3, 0.3), (0.9, 0.4, 0.2))

    def states(self):
        game = Game(seed=2, high_score=500)
        yield "title", game
        game.press("ok")
        game.set_input(fire=True, tilt=0.4)
        game.shield = True
        game.triple_left = 6
        game.powerups.append(space.Powerup(100, 100, "shield"))
        game.powerups.append(space.Powerup(200, 90, "triple"))
        game.spawn_rock(160, 80, 3)
        steps(game, 2)
        game.invulnerable = 1
        game.flash = 0.5
        game.banner = 1
        yield "playing", game
        game.press("back")
        yield "paused", game
        game.press("ok")
        game.score = 900
        game.lives = 1
        game.spawn_rock(game.ship_x, game.ship_y, 2, vx=0, vy=0)
        steps(game, 0.5)
        yield "dying", game
        steps(game, 2)
        yield "over", game
        game.state = "over"
        game.new_high = False
        yield "over without a record", game

    def test_every_state_paints_without_exceptions_and_nothing_in_the_bottom_rows(self):
        for name, game in self.states():
            surface, cr = preview_surface(1)
            space.paint(cr, game, self.THEME)
            surface.flush()
            width, stride, data = surface.get_width(), surface.get_stride(), bytes(surface.get_data())
            for row in range(HEIGHT - 2, HEIGHT):
                line = data[row * stride:row * stride + width * 4]
                self.assertEqual(line, empty_space() * width, f"{name}: row {row}")
            # Something was drawn above.
            self.assertTrue(data[:(HEIGHT - 2) * stride] != empty_space() * (width * (HEIGHT - 2)), name)

    def test_paints_on_the_panel_and_a_theme_is_optional(self):
        game = playing()
        surface, cr = device_surface()
        space.paint(cr, game)
        self.assertEqual(len(frame_bytes(surface)), len(frame_bytes(device_surface()[0])))

    def test_the_level_banner_is_not_drawn_under_the_paused_and_game_over_panels(self):
        for state in ("paused", "over"):
            frames = []
            for banner in (0, 1):
                game = playing()
                game.banner = banner
                game.state = state
                surface, cr = preview_surface(1)
                space.paint(cr, game, self.THEME)
                surface.flush()
                frames.append(bytes(surface.get_data()))
            self.assertEqual(frames[0], frames[1], state)
        # While playing it is drawn.
        frames = []
        for banner in (0, 1):
            game = playing()
            game.banner = banner
            surface, cr = preview_surface(1)
            space.paint(cr, game, self.THEME)
            surface.flush()
            frames.append(bytes(surface.get_data()))
        self.assertNotEqual(frames[0], frames[1])

    def test_the_title_and_the_end_say_what_to_do(self):
        # The words matter to the player; paint() must not raise on any score size.
        game = Game(seed=1, high_score=10 ** 9)
        game.state, game.score, game.new_high = "over", 10 ** 9, True
        surface, cr = preview_surface(1)
        space.paint(cr, game, self.THEME)


def make_ui(**overrides):
    machine = ScreenUI(ScreenConfig(**overrides), 0.0)
    machine.set_context([12, 17, 13, 18, 14, 19, 15, 20, 16, 21], device_steps=3)
    machine.start(0.0)
    return machine


def type_code(machine, now=1.0, keys=CODE):
    effects = []
    for key in keys:
        effects += machine.key_down(key, now)
        machine.key_up(key, now + 0.01)
        now += 0.1
    return effects


class GameModeTests(unittest.TestCase):
    def test_the_code_starts_the_game_and_the_last_ok_does_not_press_the_page(self):
        machine = make_ui()
        machine.key_down("down", 0.5)  # highlight a face on the keys page: OK would press its button
        machine.key_up("down", 0.6)
        effects = type_code(machine)
        self.assertIsNotNone(machine.game)
        self.assertIn(("game", "start"), effects)
        self.assertEqual(machine.game.state, "title")
        self.assertEqual([e for e in effects if e[0] == "control" and e[1].startswith("button")], [])

    def test_keys_of_the_code_still_do_their_job_while_typed(self):
        machine = make_ui()
        type_code(machine, keys=CODE[:5])  # Left moves to the last page
        self.assertEqual(machine.page, "clock")

    def test_wrong_code_does_not_start_it(self):
        machine = make_ui()
        type_code(machine, keys=CODE[:-2] + ["ok"])
        self.assertIsNone(machine.game)

    def test_works_on_any_page_and_with_an_overlay(self):
        machine = make_ui()
        machine.go_page("clock")
        machine.key_down("settings", 0.5)
        self.assertEqual(machine.overlay, "settings")
        machine.key_down("back", 0.6)
        type_code(machine, now=1)
        self.assertIsNotNone(machine.game)
        self.assertIsNone(machine.overlay)

    def test_not_while_the_dial_is_on(self):
        machine = make_ui()
        machine.set_dial(True, 0.5)
        type_code(machine)
        self.assertIsNone(machine.game)

    def test_a_sleeping_screen_does_not_count_the_first_key(self):
        machine = make_ui(off_after_s=10, dim_after_s=0)
        machine.tick(11)
        self.assertEqual(machine.power, "off")
        type_code(machine, now=12)  # the first Up only wakes the screen
        self.assertIsNone(machine.game)

    def test_the_game_draws_instead_of_the_pages_and_takes_the_keys(self):
        machine = make_ui()
        type_code(machine)
        self.assertIs(machine.view().game, machine.game)
        self.assertEqual(machine.key_down("ok", 5), [("redraw",)])  # starts the round, no control, no page change
        self.assertEqual(machine.game.state, "playing")
        for key in ("up", "down", "left", "right", "settings"):
            effects = machine.key_down(key, 6)
            self.assertEqual([e for e in effects if e[0] in ("control", "save")], [], key)
        self.assertEqual(machine.page, "keys")
        self.assertIsNone(machine.overlay)
        self.assertEqual(machine.held, {})  # no auto-repeat of Up/Down

    def test_left_and_right_are_held_while_down(self):
        machine = make_ui()
        type_code(machine)
        machine.key_down("ok", 5)
        machine.key_up("ok", 5.1)
        machine.key_down("left", 6)
        self.assertTrue(machine.game.left)
        machine.key_up("left", 6.2)
        self.assertFalse(machine.game.left)

    def test_it_does_not_dim_or_turn_off(self):
        machine = make_ui(dim_after_s=30, off_after_s=60)
        type_code(machine, now=1)
        machine.key_down("ok", 2)
        for now in (40, 100, 500):
            machine.game_step(0.03, 0, 0, False, now)
            machine.tick(now)
            self.assertEqual(machine.power, "awake", now)
        self.assertIsNone(machine.next_deadline(500))

    def test_toasts_are_not_shown_while_playing(self):
        machine = make_ui()
        type_code(machine)
        self.assertEqual(machine.feedback(5, 12, "Undo"), [])
        self.assertIsNone(machine.view(5).toast)

    def test_menu_leaves_and_the_pages_come_back_with_the_old_page(self):
        machine = make_ui(dim_after_s=30)
        machine.go_page("device")
        type_code(machine, now=1)
        effects = machine.key_down("menu", 5)
        self.assertIn(("game", "end"), effects)
        self.assertIsNone(machine.game)
        self.assertIsNone(machine.view().game)
        self.assertEqual(machine.page, "keys")  # Back of the code went to the start page, as it always does
        # The idle clock starts again from leaving the game.
        machine.tick(5 + 29)
        self.assertEqual(machine.power, "awake")
        machine.tick(5 + 31)
        self.assertEqual(machine.power, "dim")

    def test_back_at_the_title_leaves(self):
        machine = make_ui()
        type_code(machine)
        self.assertIn(("game", "end"), machine.key_down("back", 5))
        self.assertIsNone(machine.game)

    def test_the_dial_turning_on_ends_the_game(self):
        machine = make_ui()
        type_code(machine)
        machine.key_down("ok", 5)
        effects = machine.set_dial(True, 6)
        self.assertIn(("game", "end"), effects)
        self.assertIsNone(machine.game)

    def test_a_game_waiting_for_input_ends_after_a_minute(self):
        machine = make_ui()
        type_code(machine, now=1)
        self.assertEqual(machine.next_deadline(1.5), 1.0 + 0.9 + GAME_IDLE_SECONDS)  # from the last key
        self.assertEqual(machine.tick(GAME_IDLE_SECONDS - 1), [])
        self.assertIsNotNone(machine.game)
        effects = machine.tick(2 + GAME_IDLE_SECONDS)
        self.assertIn(("game", "end"), effects)
        self.assertIsNone(machine.game)

    def test_idle_applies_when_paused_and_over_but_not_while_playing(self):
        machine = make_ui()
        type_code(machine, now=1)
        machine.key_down("ok", 2)
        machine.tick(2 + 2 * GAME_IDLE_SECONDS)  # playing: the game's own clock rules
        self.assertIsNotNone(machine.game)
        machine.key_down("back", 200)  # pause
        machine.tick(200 + GAME_IDLE_SECONDS - 1)
        self.assertIsNotNone(machine.game)
        machine.tick(200 + GAME_IDLE_SECONDS)
        self.assertIsNone(machine.game)

    def test_cap_motion_keeps_a_paused_game_alive(self):
        machine = make_ui()
        type_code(machine, now=1)
        machine.key_down("ok", 2)
        machine.key_down("back", 3)
        machine.activity(50, "motion")
        machine.tick(3 + GAME_IDLE_SECONDS + 1)
        self.assertIsNotNone(machine.game)

    def test_a_new_high_score_is_an_effect_once(self):
        machine = make_ui()
        machine.high_score = 100
        type_code(machine)
        machine.key_down("ok", 2)
        game = machine.game
        game.score, game.lives = 340, 1
        game.spawn_rock(game.ship_x, game.ship_y, 2, vx=0, vy=0)
        found = []
        for index in range(120):
            found += [e for e in machine.game_step(STEP, 0, 0, False, 3 + index * STEP) if e[0] == "high_score"]
        self.assertEqual(found, [("high_score", 340)])
        self.assertEqual(machine.high_score, 340)
        # The next round starts with the new record.
        machine.key_down("ok", 10)
        self.assertEqual(machine.game.high_score, 340)

    def test_game_step_only_runs_a_running_game(self):
        machine = make_ui()
        self.assertEqual(machine.game_step(STEP, 0, 0, False, 1), [])
        type_code(machine)
        self.assertEqual(machine.game_step(STEP, 0, 0, False, 5), [])  # the title
        machine.key_down("ok", 6)
        self.assertEqual(machine.game_step(STEP, 1.0, 0, False, 7), [("redraw",)])
        self.assertGreater(machine.game.ship_x, machine.game.width / 2)

    def test_light_still_works(self):
        machine = make_ui(brightness=70)
        type_code(machine)
        effects = machine.key_down("light", 5)
        self.assertIn(("save", {"brightness": 40}), effects)

    def test_the_whole_screen_is_the_game(self):
        from screen import pages
        from config.schema import _default_config

        config = _default_config()
        machine = make_ui()
        type_code(machine)
        machine.key_down("ok", 5)
        surface, cr = preview_surface(1)
        scene = pages.build_scene(config, "", machine.view(5.0), connected=True)
        pages.paint(cr, scene)
        surface.flush()
        stride = surface.get_stride()
        data = bytes(surface.get_data())
        # The game's space, not the page's background, even where the top bar would be.
        row = data[(HEIGHT - 1) * stride:(HEIGHT - 1) * stride + WIDTH * 4]
        self.assertEqual(row, empty_space() * WIDTH)


if __name__ == "__main__":
    unittest.main()
