"""A 3Dconnexion SpaceMouse as the dial, without hardware or sockets.

``ButtonMachine`` turns the buttons into actions (a press action and a hold
action per button, the dial's "open it while the button is down, release to
select" behaviour); ``SpaceCap`` reads the cap's six raw axes (tilt, twist,
push); ``ListStepper`` repeats a list step while the cap stays tilted.
``dialpad.py`` feeds them from spacenavd's raw events (spacenav_client.py) and
runs what comes out; this module knows nothing about the dial itself.
"""

import math
from collections import namedtuple

from dial_gesture import touch_angle

# kind: "action" (run `action`), "hold_down" / "hold_up" (hold or
# release the keys of `action`: a modifier or key_hold), "select" (a centre tap on the highlighted
# entry), "close" (switch the dial off).
ButtonAction = namedtuple("ButtonAction", "kind index action")

# Actions that switch the dial (or a group's list) on and wait for a tilt.
OPENING_TYPES = ("dial", "group")
# Actions that keep keys down while the button is down.
HOLDING_TYPES = ("modifier", "key_hold")


def _real(action):
    """An action that does something: ``none`` (let the button through) is nothing."""
    return None if action is None or action.type == "none" else action


class _Down:
    def __init__(self, start, binding):
        self.start = start
        self.press = _real(binding.press)
        self.hold = _real(binding.hold)
        self.hold_fired = False
        # The action whose keys this button keeps down right now.
        self.holding = None
        self.consumed = False


class ButtonMachine:
    """Per button: a press action and a hold action.

    The press action runs at once when the button has no hold action (the
    lowest latency; actions that keep keys down - a modifier, ``key_hold`` -
    always do, the keys must be down while the button is), else when it is
    released before ``hold_ms``. The hold action runs once at ``hold_ms``
    while the button is still down; as ``key_hold`` its keys go down then and
    up on release. An action of type ``none`` is nothing at all: the button
    is left to other programs.

    Release to select: an action that switches the dial (or a group's list)
    on makes its button the "opener". If a tilt or twist highlights something
    (``note_highlight()``) and the opener is released, that entry is selected.
    Released without it, the menu stays open (sticky): pressing the opener
    again selects what has been highlighted since, or closes the dial when
    nothing was.
    """

    def __init__(self, hold_ms=400):
        self.hold_time = hold_ms / 1000
        self.down = {}
        self.opener = None
        self.opener_down = False
        self.highlighted = False

    # --- what the driver tells -------------------------------------------------

    def note_highlight(self):
        """Something was highlighted by the cap."""
        if self.opener is not None:
            self.highlighted = True

    def clear_opener(self):
        """The menu was left or an entry chosen: nothing is waiting for the opener."""
        self.opener = None
        self.opener_down = False
        self.highlighted = False

    def dial_off(self):
        self.clear_opener()

    def release_all(self):
        """The connection is gone: let go of held keys, forget everything."""
        out = [
            ButtonAction("hold_up", index, state.holding)
            for index, state in self.down.items() if state.holding is not None
        ]
        self.down.clear()
        self.clear_opener()
        return out

    # --- buttons -------------------------------------------------------------------

    def _opened(self, index, action, dial_on):
        """``action`` just ran for ``index``: does the button now wait to select?"""
        if action.type == "group" or (action.type == "dial" and not dial_on):
            self.opener = index
            self.opener_down = True
            self.highlighted = False

    def press(self, index, now, binding, dial_on):
        """The button went down. ``binding`` has ``press`` and ``hold`` (either may be None)."""
        if index in self.down:
            return []
        state = self.down[index] = _Down(now, binding)
        press, hold = state.press, state.hold

        if self.opener == index and not self.opener_down and dial_on:
            # The sticky menu's opener again: select or close.
            state.consumed = True
            out = [ButtonAction("select" if self.highlighted else "close", index, None)]
            self.clear_opener()
            return out
        # Only a gesture button ends a menu that waits for its opener. A button that
        # keeps keys down (Ctrl while the menu button is held) or that is left to
        # other programs must not.
        if press is not None:
            gesture = press.type not in HOLDING_TYPES
        else:
            gesture = hold is not None and hold.type not in HOLDING_TYPES
        if gesture:
            self.clear_opener()

        if press is not None and press.type in HOLDING_TYPES:
            state.holding = press
            return [ButtonAction("hold_down", index, press)]
        if press is not None and hold is None:
            out = [ButtonAction("action", index, press)]
            self._opened(index, press, dial_on)
            return out
        return []

    def tick(self, now, dial_on):
        """Hold actions that are due."""
        out = []
        for index, state in list(self.down.items()):
            hold = state.hold
            if hold is None or state.hold_fired or state.holding is not None or state.consumed:
                continue
            if now - state.start >= self.hold_time:
                state.hold_fired = True
                if hold.type in HOLDING_TYPES:
                    state.holding = hold
                    out.append(ButtonAction("hold_down", index, hold))
                else:
                    out.append(ButtonAction("action", index, hold))
                    self._opened(index, hold, dial_on)
        return out

    def release(self, index, now, dial_on):
        state = self.down.pop(index, None)
        if state is None or state.consumed:
            return []
        out = []
        press, hold = state.press, state.hold
        if state.holding is not None:
            return [ButtonAction("hold_up", index, state.holding)]
        if hold is not None and not state.hold_fired and press is not None:
            out.append(ButtonAction("action", index, press))
            self._opened(index, press, dial_on)
            if self.opener == index:
                self.opener_down = False  # released already: sticky
            return out
        if self.opener == index and self.opener_down:
            self.opener_down = False
            if self.highlighted:
                out.append(ButtonAction("select", index, None))
                self.clear_opener()
        return out

    @property
    def held(self):
        return bool(self.down)

    @property
    def gesture_active(self):
        """A button is down that makes a gesture: not a modifier (Ctrl held for
        a while must not freeze configuration and focus changes), but any other
        button, and an opener waiting to select."""
        return (self.opener is not None and self.opener_down) or any(
            state.holding is None and (state.press is not None or state.hold is not None) for state in self.down.values()
        )


class SpaceCap:
    """The cap's raw axes as tilt, twist and push.

    ``tilt_axes`` is (horizontal, vertical): the raw axis that grows when the
    cap tilts to the right and the one that grows when it tilts towards you
    (away from you = top), unless inverted. Tilt gives a direction (touch
    convention: away from you = top = 0 degrees, right = 90) beyond a dead
    zone. The defaults are the raw axes of a SpacePilot Pro as spacenavd
    reports them before its own axis swap: tilt right = axis 5 positive, tilt
    towards you = axis 3 positive, twist clockwise = axis 4 NEGATIVE
    (``twist_invert``), push down = axis 1 NEGATIVE (``push_invert``). Twist is a rate control: its deflection beyond the dead zone
    turns a virtual angle at up to ``twist_speed`` degrees per second. Whichever
    of tilt and twist is clearly stronger (``DOMINANCE``) wins, so a tilted
    press does not turn and a twist does not move the highlight.
    """

    TILT_DEAD = 0.35
    TWIST_DEAD = 0.15
    PUSH_ON = 0.5
    PUSH_OFF = 0.35
    DOMINANCE = 1.5
    # A step in a list is not taken from more than this long a pause.
    MAX_TICK = 0.2

    def __init__(
        self, axis_max=400, tilt_axes=(5, 3), tilt_invert=(False, False), twist_axis=4,
        twist_invert=True, twist_speed=360, push_axis=1, push_invert=True,
    ):
        self.axis_max = max(1, axis_max)
        self.tilt_axes = tuple(tilt_axes)
        self.tilt_invert = tuple(tilt_invert)
        self.twist_axis = twist_axis
        self.twist_invert = twist_invert
        self.twist_speed = twist_speed
        self.push_axis = push_axis
        self.push_invert = push_invert
        self.reset()

    def reset(self):
        self.axes = {}
        self.pushed = False
        self.twist_angle = 0.0
        self.twist_rate = 0.0
        self._last_tick = None

    def raw(self, index):
        return self.axes.get(index, 0)

    def set_axis(self, index, value):
        """A raw axis event; "push" or "release" when the cap's push changed."""
        self.axes[index] = value
        if index != self.push_axis:
            return None
        push = -value if self.push_invert else value
        if not self.pushed and push >= self.PUSH_ON * self.axis_max:
            self.pushed = True
            return "push"
        if self.pushed and push < self.PUSH_OFF * self.axis_max:
            self.pushed = False
            return "release"
        return None

    # --- tilt ------------------------------------------------------------------

    def tilt_vector(self):
        """(dx, dy) of the tilt on the screen (y down)."""
        left_right = self.raw(self.tilt_axes[0]) * (-1 if self.tilt_invert[0] else 1)
        fore_aft = self.raw(self.tilt_axes[1]) * (-1 if self.tilt_invert[1] else 1)
        return left_right, fore_aft

    def _twist_value(self):
        value = self.raw(self.twist_axis)
        return -value if self.twist_invert else value

    def _tilt_size(self):
        dx, dy = self.tilt_vector()
        return math.hypot(dx, dy)

    def tilt_active(self):
        size = self._tilt_size()
        return size >= self.TILT_DEAD * self.axis_max and not abs(self._twist_value()) > self.DOMINANCE * size

    def tilt_angle(self):
        """The tilt direction in degrees, None inside the dead zone or while twisting."""
        if not self.tilt_active():
            return None
        dx, dy = self.tilt_vector()
        return touch_angle(dx, dy)

    def list_direction(self):
        """(-1 away / up, 0, +1 towards you / down) and the deflection 0..1 for a list."""
        if not self.tilt_active():
            return 0, 0.0
        dx, dy = self.tilt_vector()
        if abs(dy) < abs(dx):
            return 0, 0.0
        return (1 if dy > 0 else -1), min(1.0, abs(dy) / self.axis_max)

    # --- twist --------------------------------------------------------------------

    def twist_active(self):
        twist = abs(self._twist_value())
        return twist >= self.TWIST_DEAD * self.axis_max and not self._tilt_size() > self.DOMINANCE * twist

    def tick(self, now):
        """Integrate the twist; call every pass. True while the twist turns the dial."""
        previous, self._last_tick = self._last_tick, now
        if not self.twist_active():
            self.twist_rate = 0.0
            return False
        value = self._twist_value()
        deflection = min(abs(value), self.axis_max) - self.TWIST_DEAD * self.axis_max
        self.twist_rate = math.copysign(deflection / self.axis_max * self.twist_speed, value)
        if previous is not None:
            self.twist_angle = (self.twist_angle + self.twist_rate * min(now - previous, self.MAX_TICK)) % 360
        return True


class ListStepper:
    """Repeats a step while the cap stays tilted: the first step at once, then
    every ``slow`` seconds, down to ``fast`` at full deflection."""

    def __init__(self, dead=SpaceCap.TILT_DEAD, slow=0.25, fast=0.08):
        self.dead = dead
        self.slow = slow
        self.fast = fast
        self.reset()

    def reset(self):
        self.direction = 0
        self.next_time = 0.0

    def interval(self, fraction):
        span = max(1e-9, 1 - self.dead)
        return self.slow - (self.slow - self.fast) * max(0.0, min(1.0, (fraction - self.dead) / span))

    def update(self, direction, fraction, now):
        """+1 or -1 when a step is due, else 0."""
        if direction == 0:
            self.reset()
            return 0
        if direction != self.direction:
            self.direction = direction
            self.next_time = now + self.interval(fraction)
            return direction
        if now >= self.next_time:
            self.next_time = now + self.interval(fraction)
            return direction
        return 0
