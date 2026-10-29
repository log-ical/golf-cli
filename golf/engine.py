"""The shot engine: what happens when a golfer swings.

Everything that decides where a ball goes is here, for the made-up golfers
and for the player alike. A golfer asks for a Swing (a club, a line, a kind
of shot, how hard, and, with the swing meter, how well it was timed), and
the engine works out the rest from what's in play: the lie (what can be hit
from it, and how well), the club's carry, roll and spread, the shot type's
height, curve and roll, the golfer's power and accuracy, their usual curve
and how often a swing goes badly wrong, the wind (a headwind costs more
carry than a tailwind gives; a crosswind pushes the ball sideways; a low
shot rides it out), the temperature, and wet ground. Then the ball lands
and runs out over whatever it lands on, into bunkers and water, through the
cup if it's going slowly enough. Water and out of bounds cost a stroke and
a drop.

Where the course's heights are known (elevation.py), a shot to lower ground
carries farther and one uphill comes up short, about a yard for every yard
of drop; and the ground's slopes work on a rolling ball: uphill takes its
speed, downhill gives it back, and a slope across its line bends it, more
the slower it goes, so putts break and die at the hole. A ball won't stop
on a slope steeper than the grass can hold it on.

Chips and putts are played the same way, along a line: a putt only drops
if it rolls over the cup no faster than one that would finish a few feet
past. Missing a read is part of a putt's scatter, which grows with its
length.
"""

import dataclasses
import math

from . import clubs
from . import weather as wx
from .geo import along, dist, heading, lerp, project_onto, segment_distance, step

CUP = 0.06            # yards: the cup's radius (2.125 in)
MAX_STROKES = 12      # a golfer who hasn't holed out by here picks up
WRONG_GREEN = 40      # yards from the hole a green is another hole's (nearer: a part of its own, mapped apart)
SCATTER = 0.045       # a full swing's carry: one standard deviation, as a fraction
SHORT_SCATTER = 0.07  # a chip's or pitch's
SHORT_AIM = 2.2       # degrees: a chip's or pitch's line, one standard deviation
PUTT_AIM, PUTT_READ = 1.5, 0.04   # a putt's line, degrees: always, and more per foot of putt (misreads)
PUTT_PACE = 0.07      # a putt's length, a fraction of it, one standard deviation
PUTT_PAST = 0.5       # yards past the hole a putt is struck to finish
TIMING_LINE, TIMING_CURVE = 6.0, 10.0   # degrees a mistimed swing (timing 1) starts off line, and curves
# On the swing meter the timing says which way it goes, and the golfer's own
# scatter is still most of the rest: a pure strike (timing 0) is a little
# straighter than their usual, and goes badly wrong less often.
METER_SCATTER, METER_MISS = 0.8, 0.6
OVERSWING = 1.1       # the most power a swing has (more than a full swing, less control)
CURVE_LOFT = (1.15, 0.009)   # a club curves the ball this much, less this much for each degree of its loft:
                             # a driver's sidespin bends it most, a wedge's backspin keeps it straighter
GUST = 0.5            # the wind as it usually blows: halfway to its gusts (a swing's is drawn from 0 to 1)
MISSES = ("slice", "hook", "push", "pull", "chunk", "thin", "shank")
MISS_ODDS = (0.25, 0.15, 0.15, 0.12, 0.15, 0.15, 0.03)
# How far a landing ball runs on each surface (times the club's roll), and
# what each yard of running costs it as it goes; a chip's or putt's run is
# reckoned per yard, from how far it was hit to run.
RUN_OUT = {"fairway": 1.0, "tee": 1.0, "green": 0.5, "rough": 0.25, "trees": 0.3}
RUN_COST = {"fairway": 1.0, "tee": 1.0, "green": 0.8, "rough": 3.0, "trees": 4.0}
SHORT_COST = {"green": 1.0, "fairway": 1.3, "tee": 1.3, "rough": 3.5, "trees": 5.0}
PUTT_COST = {"green": 1.0, "fairway": 1.6, "tee": 1.6, "rough": 4.0}
# The most run a ball can have left as it reaches the cup and still drop
# (yards): a putt finishing five feet past drops; a long iron's bounce rarely.
CAPTURE = {"putt": 1.7, "short": 1.2, "full": 0.6}
PLAYS_LIKE = 1.0      # yards more carry for each yard lower it comes down than it was hit from
DROP_MOST = 0.3       # the most that changes a carry, as a part of it
# How hard it is to roll a ball over the ground: what a slope does to it is
# its rise over this (times what each surface's grass takes). A green
# running about 11 on the stimpmeter slows a ball as a 5% slope would, and
# a rolling ball feels 5/7 of a slope (the rest of it goes to its spin);
# a fairway's run is reckoned as a full swing's is.
GRIP = {"putt": 0.07, "short": 0.07, "full": 0.14}
GREEN_STEEP = 0.06    # the steepest a green's putting surface is built (steeper in the heights: a bank, or
                      # the heights being a little off it)
MAX_RUN = 250         # yards a ball rolls down the longest slope before it's taken to have stopped
TURN_MOST = 25        # degrees a slope can turn a rolling ball in one stride
READS = 8             # tries at a putt's or chip's line and length, reading the slopes

WOODS = ("D", "3W", "5W", "3H")
LONG = ("3W", "5W", "3H", "4i", "5i")
IRONS = ("3H", "4i", "5i", "6i", "7i", "8i", "9i", "PW", "GW", "SW", "LW")
WEDGES = ("PW", "GW", "SW", "LW")
CHIPPERS = ("7i", "8i", "9i", "PW", "GW", "SW", "LW")


@dataclasses.dataclass(frozen=True)
class ShotType:
    key: str
    name: str
    carry: float      # times the club's full carry
    roll: float       # full swings: times the club's roll; short shots: run, as a part of the carry
    height: float     # times a full swing's height (low shots ride the wind out)
    curve: float      # degrees it curves (+ right)
    spread: float     # times the club's spread
    wind: float       # times what the wind does to it
    clubs: tuple      # the clubs it can be played with ("P": the putter)
    level: int = 0    # 0 anyone, 1 scratch and up, 2 tour
    short: bool = False
    lies: tuple = ()  # only from these lies (none: any but the green)


TYPES = (
    ShotType("full", "Full swing", 1.0, 1.0, 1.0, 0, 1.0, 1.0, tuple(c.key for c in clubs.BAG)),
    ShotType("punch", "Punch", 0.72, 2.6, 0.45, 0, 0.85, 0.5, IRONS[:-2]),
    ShotType("draw", "Draw", 1.02, 1.4, 0.95, -7, 1.05, 1.0, WOODS + IRONS[:-4], 1),
    ShotType("fade", "Fade", 0.97, 0.7, 1.05, 7, 1.0, 1.0, WOODS + IRONS[:-4], 1),
    ShotType("stinger", "Stinger", 0.9, 2.2, 0.4, 0, 0.9, 0.35, LONG, 2),
    ShotType("chip", "Chip", 0.3, 1.4, 0.15, 0, 1.0, 0.1, CHIPPERS, 0, True),
    ShotType("pitch", "Pitch", 0.55, 0.35, 0.5, 0, 1.0, 0.4, WEDGES, 0, True),
    ShotType("flop", "Flop", 0.45, 0.08, 1.3, 0, 1.5, 0.6, ("SW", "LW"), 1, True),
    ShotType("blast", "Splash", 0.27, 0.3, 0.6, 0, 1.3, 0.3, ("SW", "LW"), 0, True, ("bunker",)),
    ShotType("putt", "Putt", 1.0, 1.0, 0.0, 0, 1.0, 0.0, ("P",), 0, False, ("green", "fairway", "tee", "rough")),
)
BY_TYPE = {t.key: t for t in TYPES}
LEVELS = {"weekend": 0, "scratch": 1, "tour": 2}
PUTTER = clubs.Club("P", "Putter", 0, 0, 0)
PUTTING_RANGE = 30    # yards from the hole within which a putter can be used off the green


@dataclasses.dataclass(frozen=True)
class Swing:
    """What a golfer asks for. club: a clubs.Club (PUTTER to putt); bearing:
    the compass line it starts on; kind: a TYPES key; power: how hard, 1 a
    full swing (up to OVERSWING); length: a putt's, in yards; timing: from
    the swing meter, -1 (early: pulled and hooked) to 1 (late: pushed and
    sliced), 0 perfect, or None for the golfer's usual scatter."""
    club: object
    bearing: float
    kind: str = "full"
    power: float = 1.0
    length: float = 0.0
    timing: float = None


@dataclasses.dataclass
class Shot:
    player: int          # index in the group
    number: int          # the stroke this was: 1 off the tee
    kind: str            # "tee", "approach", "layup", "punch", "chip", "pitch", "bunker", "flop", "putt", "drop",
                         # "pickup": what the shot was for
    club: str            # the club's name ("" for drops and pick-ups)
    start: tuple
    land: tuple          # where it came down (where it started rolling)
    end: tuple
    bend: tuple          # the flight's control point: geo.bezier(start, bend, land)
    lie: str             # where it finished (a surface, "holed")
    to_pin: float        # yards left after it
    from_pin: float      # yards left before it
    holed: bool = False
    penalty: int = 0     # strokes added: 1 for water or out of bounds
    miss: str = ""       # what went wrong: one of MISSES, "lip out", "skull", "stays in"
    style: str = "full"  # the TYPES key it was played as
    loft: float = 1.0    # how high it flew, times a full swing
    path: tuple = ()     # the points it rolled through, from where it came down to where it stopped (none: straight)

    @property
    def flown(self):
        return dist(self.start, self.land)

    @property
    def length(self):
        return dist(self.start, self.end)


@dataclasses.dataclass
class Ball:
    pos: tuple
    lie: str = "tee"
    strokes: int = 0
    holed: bool = False


def club_keys(lie, yards):
    """The clubs (keys) that can be played from a lie `yards` from the hole."""
    if lie == "green":
        return ["P"]
    keys = [c.key for c in clubs.playable(lie)]
    if lie in BY_TYPE["putt"].lies and yards <= PUTTING_RANGE:
        keys.append("P")
    return keys


def curve_factor(club):
    """How much a club curves the ball, times what it's swung to curve it (a driver about 1.05, a 6-iron 0.9)."""
    return CURVE_LOFT[0] - CURVE_LOFT[1] * club.loft


def club(key):
    return PUTTER if key == "P" else next(c for c in clubs.BAG if c.key == key)


def types_for(club_key, lie, level):
    """The shot types a golfer of a level ("weekend", "scratch", "tour") can
    play with a club from a lie, the usual first."""
    lv = LEVELS.get(level, 2)
    out = []
    for t in TYPES:
        if club_key in t.clubs and t.level <= lv and (lie in t.lies if t.lies else lie != "green"):
            out.append(t.key)
    if lie == "bunker" and "blast" in out:   # out of the sand, the splash is the usual
        out.remove("blast")
        out.insert(0, "blast")
    return out


class Striker:
    """One golfer's swings on one hole."""

    def __init__(self, course, hole, golfer, index, weather, rng):
        self.course, self.hole, self.g, self.i = course, hole, golfer, index
        self.weather, self.rng = weather, rng
        self.ground = getattr(course, "ground", None)
        self._surface = {}

    def surface(self, p):
        key = (round(p[0] * 2), round(p[1] * 2))   # to half a yard: the same spots get asked about again
        s = self._surface.get(key)
        if s is None:   # at the half yard's own spot, so the answer can't depend on what was asked first
            s = self._surface[key] = self.course.surface_at((key[0] / 2, key[1] / 2))
        return s

    def carry(self, swing, lie):
        """How far a swing flies from a lie in still air at 70°F."""
        t = BY_TYPE[swing.kind]
        return clubs.carry(swing.club, lie, self.g.power) * t.carry * min(OVERSWING, swing.power)

    def rise(self, a, b):
        """Yards b is above a, for what it does to a carry: up, it plays longer."""
        if self.ground is None:
            return 0.0
        return (self.ground.z(b) - self.ground.z(a)) * PLAYS_LIKE

    # ----------------------------------------------------------------------
    # Striking it

    def strike(self, ball, swing, kind=None):
        """Play a swing from the ball: the shot (and any drop after it).
        kind: what the shot is for, for the words about it; worked out if not given."""
        t = BY_TYPE[swing.kind]
        if swing.kind == "putt":
            return self.putt(ball, swing)
        if t.short:
            return self.short(ball, swing, kind)
        return self.full(ball, swing, kind)

    def preview(self, ball, swing, cup=True, trace=None):
        """(where it lands, where it stops, whether it drops) for a swing
        struck just as meant, in the wind as it usually blows: what the player
        is shown when aiming. Not cup: as if the hole weren't there. trace: a
        list to put the points it rolls through in."""
        shot = self.planned(ball, swing, cup, trace)
        return shot.land, shot.end, shot.holed

    def planned(self, ball, swing, cup=True, trace=None):
        """The shot a swing makes struck just as meant, in the wind as it
        usually blows, as a Shot (not played: the ball stays where it is).
        Not cup: as if the hole weren't there. trace: a list to put every
        point it rolls through in."""
        t = BY_TYPE[swing.kind]
        if swing.kind == "putt":
            land = ball.pos
            rolled = [land]
            end, holed = self.run(land, swing.bearing, swing.length, "putt", cup, rolled)
            bend = lerp(land, end, 0.5)
        else:
            land, bend = self._flight(ball, swing, 0.0, self.g.shape * (not t.short) + t.curve, 1.0, GUST)
            rolled = [land]
            if t.short:
                roll, how = self._short_run(swing, land, ball), "short"
            else:
                lie = clubs.LIES.get(ball.lie, clubs.LIES["rough"])
                roll, how = swing.club.roll * t.roll * lie.roll * wx.roll_factor(self.weather), "full"
            end, holed = self.run(land, heading(bend, land), roll, how, cup, rolled)
        if trace is not None:
            trace.extend(rolled[1:])
        return self._unplayed(ball, swing, land, end, bend, holed, rolled)

    def still_air(self, ball, swing):
        """The shot a swing makes struck as meant in still air at 70°F, over
        flat ground, as far as where it comes down (its curve, but no wind, no
        slopes and no roll): what a yardage book and the club's numbers say."""
        t = BY_TYPE[swing.kind]
        if swing.kind == "putt":
            end = step(ball.pos, swing.bearing, swing.length)
            return self._unplayed(ball, swing, ball.pos, end, lerp(ball.pos, end, 0.5), False, ())
        land, bend = self._flight(ball, swing, 0.0, self.g.shape * (not t.short) + t.curve, 1.0, 0.0,
                                  weather=wx.Weather(), ground=False)
        return self._unplayed(ball, swing, land, land, bend, False, ())

    def _unplayed(self, ball, swing, land, end, bend, holed, rolled):
        t = BY_TYPE[swing.kind]
        pin = self.hole.pin
        return Shot(self.i, ball.strokes + 1, _kind(ball, swing), swing.club.name, ball.pos, land, end, bend,
                    "holed" if holed else self.surface(end), 0.0 if holed else dist(end, pin), dist(ball.pos, pin),
                    holed=holed, style=swing.kind, loft=t.height, path=_rolled(rolled, end))

    def read(self, ball, swing, past):
        """The swing with its line and length (a putt's length, else its
        power) worked out by trying it, as a golfer reads the slopes: so that
        struck as meant it rolls through the hole and on `past` yards. On flat
        ground it's as it is."""
        if self.ground is None:
            return swing
        start, pin = ball.pos, self.hole.pin
        best, best_miss = swing, None
        for _ in range(READS):
            path = [start]
            self.preview(ball, swing, cup=False, trace=path)
            at, off = project_onto(path, pin)        # the nearest it comes to the hole
            near, end = along(path, at), path[-1]
            # How far on from there it stops, along the way it was going (back down a bank it
            # couldn't climb: short); stopping short of the hole, how far short.
            if dist(near, end) < 0.01:
                on = -dist(near, pin)
            else:
                way = heading(along(path, max(0.0, at - 0.5)), near) if at > 0.01 else swing.bearing
                on = dist(near, end) * math.cos(math.radians(heading(near, end) - way))
                if on < 0:
                    on -= dist(near, pin)
            miss = off + abs(on - past) * 0.3
            if best_miss is None or miss < best_miss:
                best, best_miss = swing, miss
            if off < CUP * 0.5 and abs(on - past) < 0.15:
                break
            turn = (heading(start, pin) - heading(start, near) + 180) % 360 - 180 if dist(start, near) > 0.3 else 0.0
            to = dist(start, pin)
            scale = max(0.6, min(1.7, (to + past) / max(0.1, to + on)))
            if swing.kind == "putt":
                swing = dataclasses.replace(swing, bearing=(swing.bearing + turn) % 360,
                                            length=min(swing.length * scale, 3 * to + 5))
            else:
                swing = dataclasses.replace(swing, bearing=(swing.bearing + turn) % 360,
                                            power=max(0.05, min(OVERSWING, swing.power * scale)))
        return best

    def _flight(self, ball, swing, start, curve, length, gust, weather=None, ground=True):
        """(where it lands, the flight's bend) for a swing from the ball that
        starts `start` degrees off its line, curves `curve` degrees, and flies
        `length` times its carry, through the wind of a moment `gust` (0-1)
        of the way to its gusts (in weather, the day's if not given; over the
        ground's slopes unless not ground)."""
        t = BY_TYPE[swing.kind]
        weather = weather or self.weather
        pos = ball.pos
        fly = self.carry(swing, ball.lie)
        line = swing.bearing
        curve *= curve_factor(swing.club)
        tail, cross = weather.wind_on(line + start + curve / 2, gust)
        flat = carry = max(1.0, fly * length * wx.carry_factor(weather, tail * t.wind))
        come_down = lambda carry: step(step(pos, line + start + curve / 2, carry), line + 90,
                                       wx.drift(cross * t.wind, carry))
        land = come_down(carry)
        for _ in range(2 if ground and self.ground is not None else 0):   # lower ground: farther (again, nearer)
            carry = flat - max(-DROP_MOST * flat, min(DROP_MOST * flat, self.rise(pos, land)))
            land = come_down(carry)
        bend = step(pos, line + start, carry * 0.55)
        return land, bend

    def full(self, ball, swing, kind=None):
        pos, rng, g = ball.pos, self.rng, self.g
        t = BY_TYPE[swing.kind]
        lie = clubs.LIES.get(ball.lie, clubs.LIES["rough"])
        power = min(OVERSWING, max(0.05, swing.power))
        spread = swing.club.spread * g.accuracy * lie.spread * t.spread * (0.75 + 0.25 * min(1.0, power))
        if power > 1:
            spread *= 1 + (power - 1) * 5   # going after it: less control
        if swing.timing is None:   # the golfer's usual
            start, curve = rng.gauss(0, spread * 0.9), rng.gauss(g.shape + t.curve, spread)
            odds = g.miss * lie.spread
        else:                      # how the swing meter was played, and most of the golfer's own
            start = swing.timing * TIMING_LINE + rng.gauss(0, spread * METER_SCATTER)
            curve = g.shape + t.curve + swing.timing * TIMING_CURVE + rng.gauss(0, spread * METER_SCATTER)
            odds = g.miss * lie.spread * (METER_MISS + 3 * abs(swing.timing))
        odds *= 1 + max(0.0, power - 1) * 8
        length, run, miss = rng.gauss(1, SCATTER * g.accuracy), 1.0, ""
        if rng.random() < odds:
            weights = list(MISS_ODDS)
            if g.tendency:
                weights[MISSES.index(g.tendency)] *= 2.2
            if swing.timing:   # a mistimed swing misses the way it was mistimed
                for m in (("hook", "pull") if swing.timing < 0 else ("slice", "push")):
                    weights[MISSES.index(m)] *= 3
            miss = rng.choices(MISSES, weights=weights)[0]
            if miss == "slice":
                curve, length = curve + rng.uniform(10, 20), length * 0.93
            elif miss == "hook":
                curve, length, run = curve - rng.uniform(10, 20), length * 0.97, 1.6
            elif miss == "push":
                start += rng.uniform(5, 9)
            elif miss == "pull":
                start -= rng.uniform(5, 9)
            elif miss == "chunk":
                length, run = length * rng.uniform(0.5, 0.8), 0.5
            elif miss == "thin":
                length, run = length * rng.uniform(0.75, 0.9), 2.5
            else:   # shank
                start, length = start + rng.uniform(30, 45), length * rng.uniform(0.35, 0.55)
        land, bend = self._flight(ball, swing, start, curve, length, rng.random())
        roll = swing.club.roll * t.roll * run * lie.roll * wx.roll_factor(self.weather)
        path = [land]
        end, holed = self.run(land, heading(bend, land), roll, "full", trace=path)
        kind = kind or _kind(ball, swing)
        return self.finish(ball, kind, swing.club.name, land, end, bend, miss, holed, swing.kind, t.height, path)

    def short(self, ball, swing, kind=None):
        """A chip, pitch, flop or splash: flown part of the way and run out."""
        pos, rng, g = ball.pos, self.rng, self.g
        t = BY_TYPE[swing.kind]
        lie_factor = {"rough": 1.35, "trees": 1.7}.get(ball.lie, 1.0)
        aim = SHORT_AIM * g.short * t.spread * lie_factor
        if swing.timing is None:
            start = rng.gauss(0, aim)
        else:
            start = swing.timing * TIMING_LINE * 0.5 + rng.gauss(0, aim * METER_SCATTER)
        length, miss = rng.gauss(1, SHORT_SCATTER * g.short * t.spread * lie_factor), ""
        odds = g.miss * (0.9 if swing.kind == "blast" else 0.6)
        if swing.timing is not None:
            odds *= METER_MISS + 3 * abs(swing.timing)
        stays = False
        if rng.random() < odds:
            if swing.kind == "blast" and rng.random() < 0.5:
                miss, length, stays = "stays in", 0.0, True
            elif rng.random() < 0.5:
                miss, length = "chunk", rng.uniform(0.3, 0.6)
            else:
                miss, length = "skull", rng.uniform(1.8, 2.6)
        if stays:
            spot = step(pos, swing.bearing, rng.uniform(1, 3))
            return self.finish(ball, kind or "bunker", swing.club.name, spot, spot, lerp(pos, spot, 0.5), miss,
                               False, swing.kind, t.height)
        land, bend = self._flight(ball, swing, start, 0.0, length, rng.random())
        run = self._short_run(swing, land, ball) * (2.5 if miss == "skull" else 1.0)
        path = [land]
        end, holed = self.run(land, heading(bend, land), run, "short", trace=path)
        kind = kind or _kind(ball, swing)
        return self.finish(ball, kind, swing.club.name, land, end, bend, miss, holed, swing.kind, t.height, path)

    def _short_run(self, swing, land, ball):
        t = BY_TYPE[swing.kind]
        return dist(ball.pos, land) * t.roll * wx.roll_factor(self.weather) ** 0.5

    def putt(self, ball, swing):
        pos, rng, g = ball.pos, self.rng, self.g
        feet = swing.length * 3
        line = PUTT_AIM + PUTT_READ * feet
        if swing.timing is None:
            off = rng.gauss(0, line * g.putting)
        else:
            off = swing.timing * line * 1.5 + rng.gauss(0, line * g.putting * METER_SCATTER)
        length = swing.length * max(0.2, rng.gauss(1, PUTT_PACE * g.putting)) + rng.gauss(0, 0.05)
        path = [pos]
        end, holed = self.run(pos, swing.bearing + off, max(0.05, length), "putt", trace=path)
        miss = ""
        if not holed and dist(end, self.hole.pin) < 0.7 and self._passed(path):
            miss = "lip out"
        return self.finish(ball, "putt", "Putter", pos, end, lerp(pos, end, 0.5), miss, holed, "putt", 0.0, path)

    def _passed(self, path):
        """Whether a putt rolling along path went over the cup (and stayed out)."""
        return any(segment_distance(self.hole.pin, a, b) < CUP * 1.6 for a, b in zip(path, path[1:]))

    # ----------------------------------------------------------------------
    # The ball on the ground

    def run(self, land, bearing, roll, how, cup=True, trace=None):
        """Where a ball landing at `land` stops, running `roll` (yards, before
        what the ground takes) on `bearing`, and whether it dropped in the cup
        on the way. how: "full", "short" or "putt", for how the ground slows
        it. Not cup: as if the hole weren't there. trace: a list to put the
        points it rolls through in."""
        pin, ground = self.hole.pin, self.ground
        s = self.surface(land)
        if how == "full" and cup and dist(land, pin) < CUP * 2 and s == "green":
            return pin, True
        if how == "full":
            if s not in RUN_OUT:
                return land, False   # in a bunker, the water or out of bounds: it stays
            budget, costs = roll * RUN_OUT[s], RUN_COST
        else:
            if s not in (PUTT_COST if how == "putt" else SHORT_COST):
                return land, False
            budget, costs = roll, PUTT_COST if how == "putt" else SHORT_COST
        p = land
        capture, grip = CAPTURE[how], GRIP[how]
        stride = 0.25 if how == "putt" else 1.0
        moved, gx, gy = 0.0, 0.0, 0.0
        while moved < MAX_RUN:
            s = self.surface(p)
            cost = costs.get(s)
            if cost is None:
                break   # into a hazard
            ahead = cost
            if ground is not None:
                gx, gy = ground.slope(p)
                steep = math.hypot(gx, gy)
                if s == "green" and steep > GREEN_STEEP:
                    gx, gy = gx * GREEN_STEEP / steep, gy * GREEN_STEEP / steep
                r = math.radians(bearing)
                ahead += (gx * math.sin(r) + gy * math.cos(r)) / grip   # uphill takes more, downhill gives
            if budget <= 0.02:
                if ground is None or math.hypot(gx, gy) <= grip * cost:
                    break
                # Too steep to stop on: it trickles off down the slope.
                bearing, budget = math.degrees(math.atan2(-gx, -gy)) % 360, 0.05
                continue
            go = stride if ahead <= 0 else min(stride, budget / ahead)
            nxt = step(p, bearing, go)
            left = budget - go * ahead
            if cup and s == "green" and segment_distance(pin, p, nxt) < CUP and left <= capture:
                return pin, True
            budget = max(0.0, left)
            if ground is not None:   # higher on the right, it falls away left: more as it slows
                r = math.radians(bearing)
                across = gx * math.cos(r) - gy * math.sin(r)
                turn = math.degrees(across * go / (2 * grip * max(budget, 0.1)))
                bearing = (bearing - max(-TURN_MOST, min(TURN_MOST, turn))) % 360
            p = nxt
            moved += go
            if trace is not None:
                trace.append(p)
            if self.surface(p) not in costs:
                break
        return p, False

    def finish(self, ball, kind, club, land, end, bend, miss, holed, style="full", loft=1.0, path=None):
        """The shot as played, the ball moved on, and any penalty drop. path:
        the points it rolled through (from land; to end, which is added)."""
        pin, start = self.hole.pin, ball.pos
        lie = "holed" if holed else self.surface(end)
        ball.strokes += 1
        shot = Shot(self.i, ball.strokes, kind, club, start, land, end, bend, lie, 0.0 if holed else dist(end, pin),
                    dist(start, pin), holed=holed, miss=miss, style=style, loft=loft, path=_rolled(path, end))
        out = [shot]
        if holed:
            ball.pos, ball.lie, ball.holed = pin, "green", True
        elif lie == "ob":   # stroke and distance: back where it was hit from
            shot.penalty = 1
            ball.strokes += 1
            out.append(self.drop(ball, end, start, ball.lie))
        elif lie == "water":   # a drop on dry ground back the way it came in (never on a green)
            shot.penalty = 1
            ball.strokes += 1
            spot, b = end, heading(end, start)
            for _ in range(300):
                if self.surface(spot) not in ("water", "green"):
                    break
                spot = step(spot, b, 2)
            spot = step(spot, b, 2)
            new = self.surface(spot)
            if new in ("water", "ob", "green"):   # nowhere dry: play again from where it was hit (a tee still a tee)
                spot, new = start, ball.lie
            elif new == "tee":
                new = "fairway"
            out.append(self.drop(ball, end, spot, new))
        elif lie == "green" and not self.hole.green.contains(*end) and dist(end, pin) > WRONG_GREEN:
            # On another hole's green: free relief, the nearest spot off it no nearer the hole.
            ball.pos, ball.lie = end, lie
            spot = self.off_green(end)
            if spot is not None:
                out.append(self.drop(ball, end, spot, {"tee": "fairway"}.get(self.surface(spot), self.surface(spot))))
                out[-1].miss = "relief"
        else:   # another hole's tee box (or a forward one) is no tee to tee it up on
            ball.pos, ball.lie = end, "fairway" if lie == "tee" else lie
        return out

    def off_green(self, p):
        """The nearest spot off the green p is on, a yard clear of it and no
        nearer the hole, that can be played from; None if there's none near."""
        away = heading(self.hole.pin, p)
        for d in range(1, 60):
            for turn in (0, 30, -30, 60, -60, 90, -90):
                q = step(p, away + turn, d)
                if dist(q, self.hole.pin) >= dist(p, self.hole.pin) and self.surface(q) not in \
                        ("green", "water", "ob", "bunker") and self.surface(step(q, away + turn, -1)) != "green":
                    return q
        return None

    def drop(self, ball, at, spot, lie):
        ball.pos, ball.lie = spot, lie
        return Shot(self.i, ball.strokes, "drop", "", at, spot, spot, lerp(at, spot, 0.5), lie,
                    dist(spot, self.hole.pin), dist(at, self.hole.pin), style="drop", loft=0.0)

    def pickup(self, ball):
        ball.strokes += 1
        ball.holed = True
        return [Shot(self.i, ball.strokes, "pickup", "", ball.pos, ball.pos, ball.pos, ball.pos, "holed", 0.0,
                     dist(ball.pos, self.hole.pin), holed=True, style="pickup", loft=0.0)]


ROLL_POINTS = 60      # the most points a shot's roll is kept as


def _kind(ball, swing):
    """What a shot's for, as far as the swing says (a tee shot, an approach, a chip...)."""
    if swing.kind == "putt":
        return "putt"
    if BY_TYPE[swing.kind].short:
        return {"blast": "bunker"}.get(swing.kind, swing.kind)
    return "tee" if ball.lie == "tee" else "punch" if swing.kind == "punch" else "approach"


def _rolled(path, end):
    """A roll's points (path, then end), thinned to ROLL_POINTS; () if it
    went nowhere, or only in a straight line."""
    if not path:
        return ()
    pts = list(path)
    if dist(pts[-1], end) > 1e-6:
        pts.append(end)
    if len(pts) < 3 or all(segment_distance(p, pts[0], pts[-1]) < 0.02 for p in pts):
        return ()   # straight: the shot's land and end say it
    if len(pts) > ROLL_POINTS:
        n = len(pts) - 1
        pts = [pts[round(k * n / (ROLL_POINTS - 1))] for k in range(ROLL_POINTS)]
    return tuple(pts)
