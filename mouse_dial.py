"""A mouse as the dial, without hardware: finding the device and the gestures.

For a computer without a touchpad. A key the mouse sends switches the dial on
and off, the wheel turns it (one notch = one step), the middle or left button
is the centre button, the right button goes back, and optionally moving the
mouse turns it by its direction of travel (MousePointer).
``dialpad.py`` reads the evdev events and feeds them to ``MouseDial``; the
actions that come back are what the touch loop does with a finger.
"""

import math
import re
import struct
from collections import deque, namedtuple

from dial_gesture import touch_angle

REL_WHEEL = 8

CLOCKWISE = "clockwise"
COUNTERCLOCKWISE = "counterclockwise"

# Words of the bitmasks in /proc/bus/input/devices are unsigned longs.
LONG_BITS = struct.calcsize("l") * 8

# One thing for the driver to do. kind: "toggle", "rotate" (direction),
# "center_press", "center_hold", "center_release" (duration, hold_fired),
# "back".
MouseAction = namedtuple("MouseAction", "kind direction duration hold_fired", defaults=(None, 0.0, False))


def has_bit(mask_text, bit, word_bits=LONG_BITS):
    """Bit ``bit`` of a "B: KEY=" style mask: hex words, most significant first."""
    try:
        words = [int(word, 16) for word in mask_text.split()]
    except ValueError:
        return False
    index, offset = divmod(bit, word_bits)
    if index >= len(words):
        return False
    return bool(words[len(words) - 1 - index] >> offset & 1)


def find_mouse(devices_text, name, key_code, word_bits=LONG_BITS):
    """Event node ("event2") of the first mouse that has the key and a wheel.

    ``devices_text`` is /proc/bus/input/devices. ``name`` is part of the
    device name (any case); "" takes any. The driver's own virtual devices
    (their names end in "DialPad") never match.
    """
    for block in re.split(r"\n\s*\n", devices_text):
        device_name, handlers, key_mask, rel_mask = "", "", "", ""
        for line in block.splitlines():
            if line.startswith("N: Name="):
                device_name = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("H: Handlers="):
                handlers = line.split("=", 1)[1]
            elif line.startswith("B: KEY="):
                key_mask = line.split("=", 1)[1]
            elif line.startswith("B: REL="):
                rel_mask = line.split("=", 1)[1]
        node = re.search(r"\bevent\d+\b", handlers)
        if not node or "DialPad" in device_name:
            continue
        if name and name.lower() not in device_name.lower():
            continue
        if has_bit(key_mask, key_code, word_bits) and has_bit(rel_mask, REL_WHEEL, word_bits):
            return node.group(0)
    return None


class MousePointer:
    """The mouse's direction of travel as the dial's angle.

    The angle is not a position but the direction the hand is moving: the
    displacement from the trail point ``window`` counts of travel back (half
    of ``radius``) to now, as the touch loop's angle (up = 0, right = 90,
    clockwise). Circling clockwise on the desk turns the angle clockwise, one
    full dial turn per lap whatever the size of the circle, and moving towards
    a sector picks it.

    After ``reset()`` there is no angle (``in_dead_zone``) until the hand has
    travelled DEAD_ZONE * radius counts; from then on there is always one, so
    the dot never vanishes. A short net displacement over the window (a
    back-and-forth wiggle) keeps the previous angle. ``jumped`` says the last
    ``move()`` turned the angle by more than JUMP_DEGREES (the hand reversed):
    a rotation tracker must start again then instead of counting a turn.
    """

    DEAD_ZONE = 0.35
    # Net displacement below this share of the window's travel is a wiggle.
    WIGGLE = 0.25
    JUMP_DEGREES = 100

    def __init__(self, radius=300):
        self.radius = max(1, radius)
        self.window = self.radius / 2
        self.reset()

    def reset(self):
        """No angle until the hand has travelled again."""
        self.x = 0.0
        self.y = 0.0
        self.travel = 0.0
        self.has_angle = False
        self.jumped = False
        self._angle = 0.0
        # (x, y, travel so far): the path of the last `window` counts, with
        # one point at or before the window's start.
        self._trail = deque([(0.0, 0.0, 0.0)])

    def move(self, dx, dy):
        self.jumped = False
        step = math.hypot(dx, dy)
        if step == 0:
            return
        self.x += dx
        self.y += dy
        self.travel += step
        self._trail.append((self.x, self.y, self.travel))
        # Keep one point at or before the window's start.
        while len(self._trail) > 1 and self._trail[1][2] <= self.travel - self.window:
            self._trail.popleft()
        if not self.has_angle and self.travel < self.DEAD_ZONE * self.radius:
            return

        start_x, start_y, start_travel = self._trail[0]
        net_x, net_y = self.x - start_x, self.y - start_y
        net = math.hypot(net_x, net_y)
        if net == 0 or (self.has_angle and net < self.WIGGLE * (self.travel - start_travel)):
            return
        angle = touch_angle(net_x, net_y)
        if self.has_angle:
            turned = (angle - self._angle + 180) % 360 - 180
            self.jumped = abs(turned) > self.JUMP_DEGREES
        self.has_angle = True
        self._angle = angle

    @property
    def in_dead_zone(self):
        return not self.has_angle

    def angle(self):
        return self._angle


class MouseDial:
    """Turns mouse events into dial actions. ``active`` is whether the dial is on.

    The driver sets ``active`` from the real state before it feeds events (the
    dial can also be switched over D-Bus); wheel and buttons do nothing while
    it is off. The middle and the left button are both the centre button (one
    at a time); only the middle one can be held to go back, and a click of the
    left one is always a tap. The right button goes back.
    """

    def __init__(self, hold_time=0.5):
        self.hold_time = hold_time
        self.active = False
        # The toggle key is held; the driver grabs the mouse only after its
        # release so the desktop never sees a key that stays down.
        self.toggle_down = False
        # The button that is the centre button right now ("middle", "left").
        self.center_button = None
        self.center_since = None
        self.hold_fired = False

    def set_active(self, active):
        active = bool(active)
        if active == self.active:
            return
        self.active = active
        if not active:
            # A button held while the dial went off is not a gesture.
            self.center_button = None
            self.center_since = None
            self.hold_fired = False

    def toggle_key(self, value):
        """The toggle key: 1 press, 0 release, 2 auto-repeat (ignored)."""
        if value == 1:
            self.toggle_down = True
            self.set_active(not self.active)
            return [MouseAction("toggle")]
        if value == 0:
            self.toggle_down = False
        return []

    def wheel(self, value):
        """REL_WHEEL: negative (wheel towards you) is clockwise, one step per notch."""
        if not self.active or not value:
            return []
        direction = CLOCKWISE if value < 0 else COUNTERCLOCKWISE
        return [MouseAction("rotate", direction)] * abs(int(value))

    def _center(self, button, value, now):
        if value == 1:
            if not self.active or self.center_button is not None:
                return []
            self.center_button = button
            self.center_since = now
            self.hold_fired = False
            return [MouseAction("center_press")]
        if value == 0 and self.center_button == button:
            duration = now - self.center_since if button == "middle" else 0.0
            action = MouseAction("center_release", duration=duration, hold_fired=self.hold_fired)
            self.center_button = None
            self.center_since = None
            self.hold_fired = False
            return [action]
        return []

    def middle(self, value, now):
        """BTN_MIDDLE: 1 press, 0 release, 2 auto-repeat (ignored)."""
        return self._center("middle", value, now)

    def left(self, value, now):
        """BTN_LEFT: select, the same as a tap on the centre button."""
        return self._center("left", value, now)

    def right(self, value):
        """BTN_RIGHT: back, on the press."""
        if value == 1 and self.active:
            return [MouseAction("back")]
        return []

    @property
    def center_held(self):
        return self.center_button is not None

    def tick(self, now):
        """Call regularly: the middle button held long enough fires the hold once."""
        if (
            self.active
            and self.center_button == "middle"
            and not self.hold_fired
            and now - self.center_since >= self.hold_time
        ):
            self.hold_fired = True
            return [MouseAction("center_hold")]
        return []
