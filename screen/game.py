"""A hidden mini-game for the SpacePilot Pro's screen ("Space Pilot").

Pure: no hardware, no GTK, nothing but Cairo for ``paint()``. Everything is
driven from outside and deterministic for a seed: the cap (tilt steers, a push
fires, a hard twist is the smart bomb) arrives through ``Game.set_input()``,
the screen keys through ``Game.press()`` / ``Game.release()``, time through
``Game.step(dt)``.

``Konami`` watches the screen keys for the secret code that starts it
(screen.ui); ``CapReader`` turns the cap's raw axes into the game's input.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field

import cairo

WIDTH, HEIGHT = 320, 240
# The bezel hides the two bottom rows of the panel: nothing is drawn there.
BOTTOM_MARGIN = 2

# Up Up Down Down Left Right Left Right Back OK (key ids of spacemouse_devices).
KONAMI = ("up", "up", "down", "down", "left", "right", "left", "right", "back", "ok")

FONT = "Adwaita Sans"

SHIP_SPEED = 200.0
TILT_DEAD = 0.08
SHIP_RADIUS = 9.0
SHIP_BOTTOM_GAP = 8
FIRE_INTERVAL = 0.2
BULLET_SPEED = 340.0
TRIPLE_SECONDS = 10.0
TRIPLE_SPREAD = 55.0
LEVEL_POINTS = 400
START_LIVES = 3
INVULNERABLE_SECONDS = 2.0
SHIELD_GRACE_SECONDS = 0.8
BOMB_ON = 0.7
BOMB_OFF = 0.4
DYING_SECONDS = 1.3
POWERUP_CHANCE = 0.09
POWERUP_KINDS = ("shield", "triple")
MAX_ROCKS = 16
MAX_PARTICLES = 220
MAX_STEP = 0.1
ROCK_RADIUS = {3: 24.0, 2: 15.0, 1: 9.0}
ROCK_SCORE = {3: 20, 2: 50, 1: 100}
# Faster the smaller.
ROCK_SPEED = {3: 0.85, 2: 1.0, 1: 1.2}

SPACE = (0.03, 0.035, 0.075)
TEXT = (0.95, 0.96, 0.99)
MUTED = (0.6, 0.64, 0.74)
ROCK_FILL = (0.27, 0.25, 0.3)
ROCK_EDGE = (0.62, 0.58, 0.68)
SHIELD_COLOR = (0.35, 0.8, 1.0)
TRIPLE_COLOR = (1.0, 0.72, 0.25)
DEFAULT_ACCENT = (0.4, 0.62, 1.0)


# --- the secret code ---------------------------------------------------------

class Konami:
    """Feed it screen key ids; ``feed()`` is True when the last keys typed are
    the code. A wrong key just continues from wherever the typed keys still
    fit (Up Up Up Down ... works)."""

    def __init__(self, code=KONAMI):
        self.code = tuple(code)
        self.typed = deque(maxlen=len(self.code))

    def reset(self):
        self.typed.clear()

    def feed(self, key: str) -> bool:
        self.typed.append(key)
        if tuple(self.typed) == self.code:
            self.typed.clear()
            return True
        return False


# --- the cap -------------------------------------------------------------------

class CapReader:
    """The cap's raw axes as game input: ``read(axes)`` gives (tilt, twist,
    fire), tilt and twist from -1 to 1 (right and clockwise are positive), fire
    while the cap is pushed down (hysteresis like SpaceCap: on at 50 %, off
    below 35 % of axis_max)."""

    PUSH_ON = 0.5
    PUSH_OFF = 0.35

    def __init__(self, axis_max=400, tilt_axis=5, tilt_invert=False, twist_axis=4, twist_invert=True,
                 push_axis=1, push_invert=True):
        self.axis_max = max(1, int(axis_max))
        self.tilt_axis, self.tilt_invert = tilt_axis, tilt_invert
        self.twist_axis, self.twist_invert = twist_axis, twist_invert
        self.push_axis, self.push_invert = push_axis, push_invert
        self.pushed = False

    @classmethod
    def from_config(cls, spacemouse):
        """From a SpaceMouseConfig."""
        return cls(
            spacemouse.axis_max, spacemouse.tilt_axes[0], spacemouse.tilt_invert[0], spacemouse.twist_axis,
            spacemouse.twist_invert, spacemouse.push_axis, spacemouse.push_invert,
        )

    def _axis(self, axes, index, invert):
        value = axes[index] if 0 <= index < len(axes) else 0
        return (-value if invert else value) / self.axis_max

    def read(self, axes):
        tilt = max(-1.0, min(1.0, self._axis(axes, self.tilt_axis, self.tilt_invert)))
        twist = max(-1.0, min(1.0, self._axis(axes, self.twist_axis, self.twist_invert)))
        push = self._axis(axes, self.push_axis, self.push_invert)
        if not self.pushed and push >= self.PUSH_ON:
            self.pushed = True
        elif self.pushed and push < self.PUSH_OFF:
            self.pushed = False
        return tilt, twist, self.pushed


# --- things in the game ----------------------------------------------------------

@dataclass
class Rock:
    x: float
    y: float
    vx: float
    vy: float
    size: int
    angle: float
    spin: float
    # Radius multiplier per corner.
    shape: list = field(default_factory=list)

    @property
    def radius(self) -> float:
        return ROCK_RADIUS[self.size]


@dataclass
class Bullet:
    x: float
    y: float
    vx: float = 0.0
    vy: float = -BULLET_SPEED


@dataclass
class Powerup:
    x: float
    y: float
    kind: str
    age: float = 0.0


@dataclass
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    life: float
    span: float
    color: tuple
    size: float = 1.6


class Game:
    """Space Pilot. ``state``: title, playing, paused, dying (the last
    explosion), over."""

    def __init__(self, seed=None, width=WIDTH, height=HEIGHT, high_score=0):
        self.rng = random.Random(seed)
        self.width, self.height = width, height
        self.floor = height - BOTTOM_MARGIN
        self.high_score = int(high_score)
        self.new_high = False
        self.state = "title"
        self.stars = self._make_stars()
        self.reset()

    # --- set up

    def _make_stars(self):
        # [x, y, layer]: the farther, the slower and dimmer.
        return [[self.rng.uniform(0, self.width), self.rng.uniform(0, self.floor), layer]
                for layer, count in enumerate((34, 22, 12)) for _ in range(count)]

    def reset(self):
        """A fresh game (the state is left to the caller)."""
        self.time = 0.0
        self.score = 0
        self.level = 1
        self.lives = START_LIVES
        self.bomb_ready = True
        self.shield = False
        self.triple_left = 0.0
        self.invulnerable = 0.0
        self.ship_x = self.width / 2
        self.ship_y = self.floor - SHIP_BOTTOM_GAP - 8
        self.rocks: list[Rock] = []
        self.bullets: list[Bullet] = []
        self.powerups: list[Powerup] = []
        self.particles: list[Particle] = []
        self.flash = 0.0
        self.banner = 0.0
        self.fire_timer = 0.0
        self.spawn_timer = 0.6
        self.dying_left = 0.0
        self.new_high = False
        self.tilt = 0.0
        self.twist = 0.0
        self.cap_fire = False
        self.ok_held = False
        self.left = False
        self.right = False
        self.twist_high = False

    @property
    def running(self) -> bool:
        """The game needs steps (a frame every 1/30 s)."""
        return self.state in ("playing", "dying")

    # --- input

    def set_input(self, tilt=None, twist=None, fire=None, left=None, right=None):
        """The cap (tilt, twist from -1 to 1; fire = pushed down) and the
        digital left and right; None keeps the value."""
        if tilt is not None:
            self.tilt = max(-1.0, min(1.0, float(tilt)))
        if twist is not None:
            self.twist = max(-1.0, min(1.0, float(twist)))
        if fire is not None:
            self.cap_fire = bool(fire)
        if left is not None:
            self.left = bool(left)
        if right is not None:
            self.right = bool(right)

    def press(self, key: str):
        """A screen key went down: "exit" when the game should end, else None."""
        if key == "menu":
            return "exit"
        if key == "left":
            self.left = True
        elif key == "right":
            self.right = True
        if self.state in ("title", "over"):
            if key == "ok":
                self.start()
            elif key == "back":
                return "exit"
        elif self.state == "playing":
            if key == "ok":
                self.ok_held = True
            elif key == "back":
                self.state = "paused"
                self.ok_held = self.left = self.right = False
        elif self.state == "paused":
            if key == "ok":
                self.state = "playing"
            elif key == "back":
                return "exit"
        return None

    def release(self, key: str):
        if key == "ok":
            self.ok_held = False
        elif key == "left":
            self.left = False
        elif key == "right":
            self.right = False

    def start(self):
        high = self.high_score
        self.reset()
        self.high_score = high
        self.state = "playing"

    @property
    def firing(self) -> bool:
        return self.cap_fire or self.ok_held

    # --- the world

    def spawn_rock(self, x, y, size, vx=0.0, vy=None):
        """Put a rock in the game (the spawner and the splits use it too)."""
        rng = self.rng
        if vy is None:
            vy = (38 + rng.random() * 34) * self._speed_factor() * ROCK_SPEED[size]
        corners = rng.randint(8, 11)
        rock = Rock(x, y, vx, vy, size, rng.uniform(0, math.tau), rng.uniform(-1.6, 1.6),
                    [rng.uniform(0.72, 1.1) for _ in range(corners)])
        self.rocks.append(rock)
        return rock

    def _speed_factor(self) -> float:
        return 1 + 0.1 * (self.level - 1)

    def _spawn_interval(self) -> float:
        return max(0.4, 1.5 - 0.11 * (self.level - 1))

    def _spawn(self, dt):
        self.spawn_timer -= dt
        if self.spawn_timer > 0:
            return
        self.spawn_timer = self._spawn_interval() * self.rng.uniform(0.7, 1.3)
        if len(self.rocks) >= MAX_ROCKS:
            return
        roll = self.rng.random()
        size = 3 if roll < 0.45 else 2 if roll < 0.8 else 1
        radius = ROCK_RADIUS[size]
        self.spawn_rock(self.rng.uniform(radius, self.width - radius), -radius, size,
                        vx=self.rng.uniform(-24, 24))

    def _burst(self, x, y, count, colors, speed=90.0, size=1.6):
        rng = self.rng
        for _ in range(count):
            if len(self.particles) >= MAX_PARTICLES:
                return
            angle = rng.uniform(0, math.tau)
            fast = speed * rng.uniform(0.25, 1.0)
            life = rng.uniform(0.35, 0.9)
            self.particles.append(Particle(x, y, math.cos(angle) * fast, math.sin(angle) * fast, life, life,
                                           rng.choice(colors), size * rng.uniform(0.7, 1.4)))

    def _explode(self, rock):
        self._burst(rock.x, rock.y, 6 + rock.size * 6, ((1.0, 0.75, 0.35), (1.0, 0.5, 0.25), ROCK_EDGE, TEXT),
                    80 + rock.size * 20)

    def _add_score(self, points):
        self.score += points
        level = 1 + self.score // LEVEL_POINTS
        if level > self.level:
            self.level = level
            self.bomb_ready = True
            self.banner = 1.6

    def _destroy_rock(self, rock, split=True, score=True):
        """Remove a rock: score, particles, the two halves, now and then a power-up."""
        if rock in self.rocks:
            self.rocks.remove(rock)
        self._explode(rock)
        if score:
            self._add_score(ROCK_SCORE[rock.size])
            if self.rng.random() < POWERUP_CHANCE:
                self.powerups.append(Powerup(rock.x, rock.y, self.rng.choice(POWERUP_KINDS)))
        if split and rock.size > 1:
            for direction in (-1, 1):
                self.spawn_rock(rock.x + direction * rock.radius * 0.5, rock.y, rock.size - 1,
                                vx=direction * self.rng.uniform(35, 70) + rock.vx * 0.3,
                                vy=rock.vy * self.rng.uniform(0.9, 1.25))

    def fire_bomb(self):
        """The smart bomb: every rock on the screen goes (scoring, no halves)."""
        self.bomb_ready = False
        self.flash = 1.0
        for rock in list(self.rocks):
            self._destroy_rock(rock, split=False)

    # --- one step

    def step(self, dt: float):
        if self.state not in ("playing", "dying"):
            return
        dt = max(0.0, min(float(dt), MAX_STEP))
        self.time += dt
        self._move_stars(dt)
        self._update_particles(dt)
        self.flash = max(0.0, self.flash - dt * 2.5)
        self.banner = max(0.0, self.banner - dt)
        if self.state == "dying":
            self._move_rocks(dt)
            self.dying_left -= dt
            if self.dying_left <= 0:
                self._finish()
            return
        self._move_ship(dt)
        self._check_bomb()
        self._fire(dt)
        self._spawn(dt)
        self._move_bullets(dt)
        self._move_rocks(dt)
        self._move_powerups(dt)
        self._collide_bullets()
        self._collide_ship()
        self.invulnerable = max(0.0, self.invulnerable - dt)
        self.triple_left = max(0.0, self.triple_left - dt)

    def _move_stars(self, dt):
        for star in self.stars:
            star[1] += (10, 26, 54)[star[2]] * dt
            if star[1] > self.floor:
                star[1] -= self.floor
                star[0] = self.rng.uniform(0, self.width)

    def _update_particles(self, dt):
        for particle in self.particles:
            particle.life -= dt
            particle.x += particle.vx * dt
            particle.y += particle.vy * dt
            particle.vx *= 1 - 1.2 * dt
            particle.vy *= 1 - 1.2 * dt
        self.particles = [particle for particle in self.particles if particle.life > 0]

    def _move_ship(self, dt):
        tilt = self.tilt
        magnitude = abs(tilt)
        analog = 0.0 if magnitude < TILT_DEAD else math.copysign((magnitude - TILT_DEAD) / (1 - TILT_DEAD), tilt)
        digital = (1.0 if self.right else 0.0) - (1.0 if self.left else 0.0)
        speed = max(-1.0, min(1.0, analog + digital)) * SHIP_SPEED
        margin = SHIP_RADIUS + 2
        self.ship_x = max(margin, min(self.width - margin, self.ship_x + speed * dt))

    def _check_bomb(self):
        strength = abs(self.twist)
        if not self.twist_high and strength >= BOMB_ON:
            self.twist_high = True
            if self.bomb_ready:
                self.fire_bomb()
        elif self.twist_high and strength < BOMB_OFF:
            self.twist_high = False

    def _fire(self, dt):
        self.fire_timer = max(0.0, self.fire_timer - dt)
        if not self.firing or self.fire_timer > 0:
            return
        self.fire_timer = FIRE_INTERVAL
        top = self.ship_y - 12
        self.bullets.append(Bullet(self.ship_x, top))
        if self.triple_left > 0:
            self.bullets.append(Bullet(self.ship_x - 6, top + 3, -TRIPLE_SPREAD))
            self.bullets.append(Bullet(self.ship_x + 6, top + 3, TRIPLE_SPREAD))

    def _move_bullets(self, dt):
        for bullet in self.bullets:
            bullet.x += bullet.vx * dt
            bullet.y += bullet.vy * dt
        self.bullets = [b for b in self.bullets if b.y > -10 and -10 < b.x < self.width + 10]

    def _move_rocks(self, dt):
        for rock in self.rocks:
            rock.x += rock.vx * dt
            rock.y += rock.vy * dt
            rock.angle += rock.spin * dt
            if (rock.x < rock.radius and rock.vx < 0) or (rock.x > self.width - rock.radius and rock.vx > 0):
                rock.vx = -rock.vx
        self.rocks = [rock for rock in self.rocks if rock.y - rock.radius <= self.floor]

    def _move_powerups(self, dt):
        for powerup in self.powerups:
            powerup.age += dt
            powerup.y += 48 * dt
        self.powerups = [p for p in self.powerups if p.y - 10 <= self.floor]
        for powerup in list(self.powerups):
            if math.hypot(powerup.x - self.ship_x, powerup.y - self.ship_y) <= SHIP_RADIUS + 10:
                self.powerups.remove(powerup)
                if powerup.kind == "shield":
                    self.shield = True
                else:
                    self.triple_left = TRIPLE_SECONDS
                self._burst(powerup.x, powerup.y, 10, (SHIELD_COLOR if powerup.kind == "shield" else TRIPLE_COLOR,), 60)

    def _collide_bullets(self):
        for bullet in list(self.bullets):
            for rock in self.rocks:
                if math.hypot(bullet.x - rock.x, bullet.y - rock.y) <= rock.radius * 0.9 + 1.5:
                    self.bullets.remove(bullet)
                    self._destroy_rock(rock)
                    break

    def _collide_ship(self):
        if self.invulnerable > 0:
            return
        for rock in self.rocks:
            if math.hypot(rock.x - self.ship_x, rock.y - self.ship_y) <= rock.radius * 0.85 + SHIP_RADIUS * 0.8:
                self._hit_ship(rock)
                return

    def _hit_ship(self, rock):
        self._destroy_rock(rock, split=False, score=False)
        if self.shield:
            self.shield = False
            self.invulnerable = SHIELD_GRACE_SECONDS
            self._burst(self.ship_x, self.ship_y, 14, (SHIELD_COLOR, TEXT), 110)
            return
        self.lives -= 1
        self.invulnerable = INVULNERABLE_SECONDS
        self.triple_left = 0.0
        self._burst(self.ship_x, self.ship_y, 26, (TEXT, (1.0, 0.6, 0.3), (1.0, 0.35, 0.25)), 130, 2.0)
        if self.lives <= 0:
            self.state = "dying"
            self.dying_left = DYING_SECONDS
            self.ok_held = False

    def _finish(self):
        self.state = "over"
        self.new_high = self.score > self.high_score
        if self.new_high:
            self.high_score = self.score


# --- painting ------------------------------------------------------------------------

def _text(cr, text, x, y, size, color, bold=False, align="l", alpha=1.0):
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size)
    width = cr.text_extents(text).x_advance
    if align == "c":
        x -= width / 2
    elif align == "r":
        x -= width
    cr.set_source_rgba(*color[:3], alpha)
    cr.move_to(x, y)
    cr.show_text(text)


def _ship_path(cr, x, y, scale=1.0):
    cr.move_to(x, y - 12 * scale)
    cr.line_to(x + 9 * scale, y + 9 * scale)
    cr.line_to(x, y + 5 * scale)
    cr.line_to(x - 9 * scale, y + 9 * scale)
    cr.close_path()


def _paint_stars(cr, game):
    for x, y, layer in game.stars:
        cr.set_source_rgba(*TEXT, (0.28, 0.5, 0.85)[layer])
        size = (1, 1, 1.6)[layer]
        cr.rectangle(round(x), round(y), size, size)
        cr.fill()


def _paint_rock(cr, rock):
    cr.save()
    cr.translate(rock.x, rock.y)
    cr.rotate(rock.angle)
    corners = len(rock.shape)
    for index, factor in enumerate(rock.shape):
        angle = math.tau * index / corners
        point = (math.cos(angle) * rock.radius * factor, math.sin(angle) * rock.radius * factor)
        (cr.move_to if index == 0 else cr.line_to)(*point)
    cr.close_path()
    cr.set_source_rgb(*ROCK_FILL)
    cr.fill_preserve()
    cr.set_source_rgb(*ROCK_EDGE)
    cr.set_line_width(1.4)
    cr.stroke()
    cr.restore()


def _paint_powerup(cr, powerup):
    color = SHIELD_COLOR if powerup.kind == "shield" else TRIPLE_COLOR
    pulse = 1 + 0.12 * math.sin(powerup.age * 9)
    cr.new_sub_path()  # no line from the text's end
    cr.arc(powerup.x, powerup.y, 9 * pulse, 0, math.tau)
    cr.set_source_rgba(*color, 0.25)
    cr.fill_preserve()
    cr.set_source_rgb(*color)
    cr.set_line_width(1.6)
    cr.stroke()
    _text(cr, "S" if powerup.kind == "shield" else "3", powerup.x, powerup.y + 4, 11, color, True, "c")


def _paint_ship(cr, game, accent):
    blinking = game.invulnerable > 0 and int(game.time * 12) % 2 == 0
    if blinking or game.state in ("dying", "over"):
        return
    x, y = game.ship_x, game.ship_y
    flame = 6 + 3 * math.sin(game.time * 40)
    cr.move_to(x - 3.5, y + 6)
    cr.line_to(x, y + 6 + flame)
    cr.line_to(x + 3.5, y + 6)
    cr.set_source_rgba(1.0, 0.65, 0.25, 0.9)
    cr.fill()
    _ship_path(cr, x, y)
    cr.set_source_rgb(*accent)
    cr.fill_preserve()
    cr.set_source_rgb(*TEXT)
    cr.set_line_width(1.2)
    cr.stroke()
    if game.shield:
        cr.new_sub_path()
        cr.arc(x, y, 17, 0, math.tau)
        cr.set_source_rgba(*SHIELD_COLOR, 0.18)
        cr.fill_preserve()
        cr.set_source_rgba(*SHIELD_COLOR, 0.9)
        cr.set_line_width(1.6)
        cr.stroke()


def _paint_hud(cr, game, accent):
    _text(cr, f"SCORE {game.score}", 6, 15, 13, TEXT, True)
    _text(cr, f"HI {game.high_score}", 128, 15, 12, MUTED)
    for index in range(game.lives):
        _ship_path(cr, 310 - index * 20, 11, 0.62)
        cr.set_source_rgb(*accent)
        cr.fill()
    _text(cr, f"LEVEL {game.level}", 6, 30, 11, MUTED)
    if game.bomb_ready:
        _text(cr, "BOMB READY: TWIST", 74, 30, 11, TRIPLE_COLOR, True)
    else:
        _text(cr, "bomb next level", 74, 30, 11, MUTED, False, alpha=0.6)
    if game.triple_left > 0:
        width = 44 * game.triple_left / TRIPLE_SECONDS
        cr.rectangle(314 - width, 34, width, 3)
        cr.set_source_rgb(*TRIPLE_COLOR)
        cr.fill()
        _text(cr, "3x", 314 - 48, 38, 9, TRIPLE_COLOR, True, "r")


def _paint_world(cr, game, accent, overlay=False):
    _paint_stars(cr, game)
    for powerup in game.powerups:
        _paint_powerup(cr, powerup)
    for rock in game.rocks:
        _paint_rock(cr, rock)
    for bullet in game.bullets:
        cr.rectangle(bullet.x - 1, bullet.y - 4, 2, 8)
        cr.set_source_rgb(*(TRIPLE_COLOR if game.triple_left > 0 else TEXT))
        cr.fill()
    _paint_ship(cr, game, accent)
    for particle in game.particles:
        fade = max(0.0, particle.life / particle.span)
        cr.rectangle(particle.x - particle.size / 2, particle.y - particle.size / 2, particle.size, particle.size)
        cr.set_source_rgba(*particle.color[:3], fade)
        cr.fill()
    _paint_hud(cr, game, accent)
    # Not under the paused / game over panels: the texts would read through each other.
    if game.banner > 0 and not overlay:
        _text(cr, f"LEVEL {game.level}", game.width / 2, 112, 26, TEXT, True, "c", min(1.0, game.banner))
    if game.flash > 0:
        cr.rectangle(0, 0, game.width, game.floor)
        cr.set_source_rgba(1, 1, 1, 0.7 * game.flash)
        cr.fill()


def _veil(cr, game, alpha=0.62):
    cr.rectangle(0, 0, game.width, game.floor)
    cr.set_source_rgba(*SPACE, alpha)
    cr.fill()


def _panel(cr, game, top, bottom):
    """A solid plate behind the text of an overlay, so rocks and pick-ups do not read through it."""
    left, right = 24, game.width - 24
    radius = 10
    cr.new_sub_path()
    cr.arc(right - radius, top + radius, radius, -math.pi / 2, 0)
    cr.arc(right - radius, bottom - radius, radius, 0, math.pi / 2)
    cr.arc(left + radius, bottom - radius, radius, math.pi / 2, math.pi)
    cr.arc(left + radius, top + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()
    cr.set_source_rgba(*SPACE, 0.94)
    cr.fill_preserve()
    cr.set_source_rgba(*MUTED[:3], 0.35)
    cr.set_line_width(1)
    cr.stroke()


def _paint_title(cr, game, accent):
    _paint_stars(cr, game)
    _ship_path(cr, game.width / 2, 78, 1.9)
    cr.set_source_rgb(*accent)
    cr.fill()
    _text(cr, "SPACE PILOT", game.width / 2, 130, 30, TEXT, True, "c")
    _text(cr, "press OK", game.width / 2, 152, 15, accent, True, "c")
    _text(cr, "Tilt: steer    Push or OK: fire", game.width / 2, 178, 11, MUTED, False, "c")
    _text(cr, "Twist: smart bomb    Back: pause    Menu: exit", game.width / 2, 194, 11, MUTED, False, "c")
    if game.high_score:
        _text(cr, f"HIGH SCORE {game.high_score}", game.width / 2, 218, 12, TRIPLE_COLOR, True, "c")


def _paint_paused(cr, game, accent):
    _veil(cr, game)
    _panel(cr, game, 84, 164)
    _text(cr, "PAUSED", game.width / 2, 116, 28, TEXT, True, "c")
    _text(cr, "OK: resume    Back: leave", game.width / 2, 146, 13, MUTED, False, "c")


def _paint_over(cr, game, accent):
    _veil(cr, game, 0.72)
    _panel(cr, game, 56, 196)
    _text(cr, "GAME OVER", game.width / 2, 88, 28, TEXT, True, "c")
    _text(cr, f"Score {game.score}", game.width / 2, 120, 20, accent, True, "c")
    if game.new_high:
        _text(cr, "NEW HIGH SCORE!", game.width / 2, 146, 15, TRIPLE_COLOR, True, "c")
    else:
        _text(cr, f"High score {game.high_score}", game.width / 2, 146, 13, MUTED, False, "c")
    _text(cr, "OK: again    Back: leave", game.width / 2, 180, 13, MUTED, False, "c")


def paint(cr, game: Game, theme=None) -> None:
    """Paint the game's current state on a 320 x 240 landscape context. The
    two bottom rows (the bezel) stay empty. ``theme`` only gives the accent."""
    accent = tuple(getattr(theme, "accent", DEFAULT_ACCENT)[:3])
    cr.save()
    cr.set_source_rgb(*SPACE)
    cr.paint()
    cr.rectangle(0, 0, game.width, game.floor)
    cr.clip()
    try:
        if game.state == "title":
            _paint_title(cr, game, accent)
        else:
            _paint_world(cr, game, accent, overlay=game.state in ("paused", "over"))
            if game.state == "paused":
                _paint_paused(cr, game, accent)
            elif game.state == "over":
                _paint_over(cr, game, accent)
    finally:
        cr.restore()
