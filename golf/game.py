"""A round you play yourself: one ball, a shot at a time.

How much help you get is up to you (HELP). With "caddie", each shot starts
as the caddie (the made-up golfers' own thinking, sim.Player) would play it.
With "yardages", it starts aimed at the flag with the club whose still-air
carry matches the distance, and the caddie is there if you ask; judging the
wind, the roll and the trouble is yours. With "none", it starts aimed at the
flag with the club you hit last, and there's no caddie. Then the shot engine
does the rest, as it does for every golfer. Your level says which kinds of
shot you can play and how well you strike it.
"""

import copy
import dataclasses
import random

from . import engine
from .engine import MAX_STROKES, Ball, Swing
from .geo import dist, heading, step
from .sim import Player

POWER_MIN = 0.1
LENGTH_MIN, LENGTH_MAX = 0.3, 80.0   # yards a putt can be hit
HELP = ("caddie", "yardages", "none")
SHORT = 45                            # yards from the flag inside which a shot starts as a pitch


@dataclasses.dataclass
class Setup:
    """The shot as you have it set up."""
    club: str            # its key ("P": the putter)
    kind: str            # an engine.TYPES key
    bearing: float       # compass bearing it starts on
    power: float = 1.0   # how hard (not putts)
    length: float = 0.0  # yards (putts)


@dataclasses.dataclass
class HoleStats:
    putts: int = 0
    fairway: bool = None   # tee shot on the fairway (par 4s and 5s)
    green: bool = False    # on in regulation: on the green with two putts for par
    penalties: int = 0


class Game:
    def __init__(self, course, golfer, level, weather, seed, holes=None, help="yardages"):
        self.course, self.golfer, self.level, self.weather, self.seed = course, golfer, level, weather, seed
        self.help = help if help in HELP else "yardages"
        self.last_club = None
        self.holes = list(holes if holes is not None else course.holes)
        self.cards = [None] * len(self.holes)       # strokes per hole, once holed
        self.stats = [HoleStats() for _ in self.holes]
        self.hole_i = 0
        self.start_hole()

    @property
    def hole(self):
        return self.holes[self.hole_i]

    @property
    def over(self):
        return self.cards[-1] is not None

    def start_hole(self):
        hole = self.hole
        self.ball = Ball(hole.tee)
        self.shots = []
        rng = random.Random("%s:%d:%d" % (self.seed, self.hole_i, hole.number))
        self.player = Player(self.course, hole, self.golfer, 0, self.weather, rng)
        self.suggest()

    def next_hole(self):
        if self.hole_i + 1 < len(self.holes):
            self.hole_i += 1
            self.start_hole()

    # ----------------------------------------------------------------------
    # Setting up

    def suggest(self):
        """Set the shot up for the ball where it is, as much as the help goes."""
        self.suggested = self.caddie() if self.help == "caddie" else None
        self.setup = copy.copy(self.suggested) if self.suggested else self.plain()

    @property
    def caddie_on_hand(self):
        return self.help != "none"

    def take_suggestion(self):
        """The caddie's play (asked for the first time if need be)."""
        if not self.caddie_on_hand:
            return
        if self.suggested is None:
            self.suggested = self.caddie()
        self.setup = copy.copy(self.suggested)

    def plain(self):
        """A shot set up with no thought for the wind, the roll or the trouble:
        at the flag; with "yardages" the club that carries the distance in
        still air (a pitch inside SHORT yards), with "none" the club hit last. A
        putt is set to roll a little past the hole, to the foot, as a putt to drop should."""
        ball, d = self.ball, self.to_pin
        b = heading(ball.pos, self.hole.pin)
        keys = self.clubs()
        if ball.lie == "green" or (keys[-1] == "P" and d <= 10 and ball.lie != "rough"):
            return Setup("P", "putt", b, 1.0, max(LENGTH_MIN, round((d + engine.PUTT_PAST) * 3) / 3))
        keys = [k for k in keys if k != "P"]
        if self.help == "none" and self.last_club in keys:
            key = self.last_club
            kinds = engine.types_for(key, ball.lie, self.level)
            return Setup(key, kinds[0], b, 1.0, 0.0)
        if d <= SHORT and ball.lie != "tee":
            kind = "blast" if ball.lie == "bunker" and d <= 30 else "pitch"
            key = "SW" if "SW" in keys else keys[-1]
            t = engine.BY_TYPE[kind]
            full = self.player.carry(Swing(engine.club(key), b, kind, 1.0), ball.lie)
            return Setup(key, kind, b, max(POWER_MIN, min(1.0, round(d / (1 + t.roll) / max(full, 1), 2))), 0.0)
        key = keys[0]
        for k in reversed(keys):   # the shortest that carries it
            if self.player.carry(Swing(engine.club(k), b, "full", 1.0), ball.lie) >= d:
                key = k
                break
        kinds = engine.types_for(key, ball.lie, self.level)
        return Setup(key, kinds[0], b, 1.0, 0.0)

    def caddie(self):
        """The caddie's suggestion for the ball where it is."""
        swing, _ = self.player.decide(self.ball)
        key = swing.club.key
        kinds = engine.types_for(key, self.ball.lie, self.level)
        kind = swing.kind if swing.kind in kinds else kinds[0]
        length = swing.length or dist(self.ball.pos, self.hole.pin) + engine.PUTT_PAST
        return Setup(key, kind, swing.bearing, min(1.0, swing.power), length)

    def clubs(self):
        return engine.club_keys(self.ball.lie, self.to_pin)

    def kinds(self):
        return engine.types_for(self.setup.club, self.ball.lie, self.level)

    @property
    def to_pin(self):
        return dist(self.ball.pos, self.hole.pin)

    def rise(self):
        """Yards the hole is above the ball (below: less than 0); 0 on flat ground."""
        g = self.course.ground
        return 0.0 if g is None else g.z(self.hole.pin) - g.z(self.ball.pos)

    def change_club(self, step):
        """The next (step 1) or previous (-1) club, longest first; the kind of
        shot stays if the new club can play it."""
        keys = self.clubs()
        i = keys.index(self.setup.club) if self.setup.club in keys else 0
        if self.setup.club == "P" and step > 0 and len(keys) > 1:
            self.setup.club = keys[-2]   # off the putter (c): the shortest club, for a chip, not the driver
        else:
            self.setup.club = keys[(i + step) % len(keys)]
        kinds = self.kinds()
        if self.setup.kind not in kinds:
            self.setup.kind = kinds[0]
        if self.setup.club == "P" and not self.setup.length:
            self.setup.length = self.to_pin + engine.PUTT_PAST

    def change_kind(self, step):
        kinds = self.kinds()
        i = kinds.index(self.setup.kind) if self.setup.kind in kinds else 0
        self.setup.kind = kinds[(i + step) % len(kinds)]

    def aim(self, degrees):
        self.setup.bearing = (self.setup.bearing + degrees) % 360

    def aim_at_flag(self):
        self.setup.bearing = heading(self.ball.pos, self.hole.pin)

    def change_power(self, delta):
        self.setup.power = max(POWER_MIN, min(1.0, round(self.setup.power + delta, 2)))

    def change_length(self, delta):
        self.setup.length = max(LENGTH_MIN, min(LENGTH_MAX, self.setup.length + delta))

    def off_flag(self):
        """Degrees the line is right of the flag (negative: left)."""
        return (self.setup.bearing - heading(self.ball.pos, self.hole.pin) + 180) % 360 - 180

    def swing(self, power=None, timing=None):
        """The setup as a swing: at its power, or the swing meter's, and timing.
        A putt on the meter rolls as far as its power: 100% the length set."""
        s = self.setup
        if s.club == "P":
            return Swing(engine.PUTTER, s.bearing, "putt", 1.0, s.length * (1.0 if power is None else power), timing)
        return Swing(engine.club(s.club), s.bearing, s.kind, s.power if power is None else power, 0.0, timing)

    def preview(self):
        """(where it lands, where it stops, whether it drops) if struck just as set up."""
        return self.player.preview(self.ball, self.swing())

    def carry(self):
        """How far it flies as set up, in still air (a putt: its length)."""
        if self.setup.club == "P":
            return self.setup.length
        return self.player.carry(self.swing(), self.ball.lie)

    def carry_spot(self):
        """Where it'd come down in still air, struck as meant, before any curve."""
        return step(self.ball.pos, self.setup.bearing, self.carry())

    # ----------------------------------------------------------------------
    # Hitting it

    def hit(self, power=None, timing=None):
        """Strike the shot as set up (the swing meter's power and timing, if
        played): the shots it makes (and any drop), the ball moved on."""
        ball, stats = self.ball, self.stats[self.hole_i]
        if ball.strokes >= MAX_STROKES:
            shots = self.player.pickup(ball)
        else:
            first = ball.strokes == 0
            shots = self.player.strike(ball, self.swing(power, timing))
            self.last_club = self.setup.club if self.setup.club != "P" else self.last_club
            shot = shots[0]
            stats.penalties += shot.penalty
            if shot.kind == "putt":
                stats.putts += 1
            if first and self.hole.par > 3:
                stats.fairway = shot.lie == "fairway"
            if (shot.lie == "green" or shot.holed) and ball.strokes <= self.hole.par - 2:
                stats.green = True
        self.shots += shots
        if ball.holed:
            self.cards[self.hole_i] = ball.strokes
        else:
            self.suggest()
        return shots

    def totals(self):
        """(strokes, par, to par) for the holes played."""
        played = [(c, h.par) for c, h in zip(self.cards, self.holes) if c is not None]
        strokes, par = sum(c for c, _ in played), sum(p for _, p in played)
        return strokes, par, strokes - par
