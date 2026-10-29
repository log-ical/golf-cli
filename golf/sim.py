"""A round shot by shot: made-up shots, played on a real course.

Away from the green a golfer chooses a club and a line the way a good player
thinks: the choice that leaves the fewest strokes to come, on average, over
where that shot usually ends up. What a spot is worth comes from the tour's
strokes-to-hole-out from each distance and lie (FAIRWAY, PUTTS), so water,
bunkers and trees get avoided, doglegs get played round, and long holes get
laid up, and a shot uphill gets more club. Then the swing happens: its usual scatter, the golfer's own curve,
now and then a real miss (a slice, a chunk, a shank), and the wind and the
ground do the rest. Near the green they chip or pitch, and on it they putt,
reading the slopes (by trying the shot, engine.Striker.read), and holing
out about as often as tour players do from each distance.

The swings themselves, and everything that happens to the ball after, are
the shot engine's (engine.py), which the player's shots go through too.

A group plays in golf's order: the honor on the tee (the best score on the
hole before goes first), then whoever is farthest from the hole.

Everything comes from one seed, so a round can be played again exactly.
"""

import dataclasses
import math
import random

from . import clubs, engine
from . import weather as wx
from .engine import BY_TYPE, GUST, MAX_STROKES, PUTT_PAST, PUTTER, SCATTER, Ball, Shot, Striker, Swing  # noqa: F401
from .geo import ahead_on_path, dist, heading, lerp, step

SHORT_GAME = 45       # yards from the hole inside which it's a chip or a pitch, not a full swing
FAN = 40              # degrees either side of the hole's line that a tee shot or lay-up looks for fairway
SAMPLES = ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1), (1.6, 0.6), (-1.6, 0.6), (1.6, -0.6), (-1.6, -0.6))

# Tour strokes to hole out: from the fairway by yards, putting by feet (rounded from Broadie's baselines).
FAIRWAY = ((5, 2.1), (10, 2.18), (20, 2.4), (40, 2.6), (60, 2.7), (80, 2.75), (100, 2.8), (120, 2.85), (140, 2.91),
           (160, 2.98), (180, 3.08), (200, 3.19), (220, 3.32), (240, 3.45), (260, 3.58), (280, 3.69), (300, 3.78),
           (350, 3.95), (400, 4.08), (450, 4.25), (500, 4.45), (600, 4.85))
PUTTS = ((1, 1.0), (2, 1.01), (3, 1.04), (4, 1.13), (5, 1.23), (6, 1.34), (8, 1.5), (10, 1.61), (15, 1.78), (20, 1.87),
         (30, 1.98), (40, 2.06), (50, 2.14), (60, 2.21), (90, 2.4))
MAKES = ((1, 0.995), (2, 0.99), (3, 0.96), (4, 0.88), (5, 0.77), (6, 0.66), (7, 0.58), (8, 0.5), (9, 0.45),
         (10, 0.4), (12, 0.32), (15, 0.23), (20, 0.15), (25, 0.1), (30, 0.07), (40, 0.045), (50, 0.03), (60, 0.02),
         (80, 0.012), (100, 0.008))
LIE_EXTRA = {"fairway": 0.0, "tee": 0.0, "rough": 0.22, "bunker": 0.45, "trees": 0.75}


@dataclasses.dataclass
class HolePlay:
    hole: object
    order: list          # tee order (honors)
    shots: list
    scores: list         # strokes per golfer


def interp(table, x):
    if x <= table[0][0]:
        return table[0][1]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    (x0, y0), (x1, y1) = table[-2], table[-1]
    return y1 + (y1 - y0) * (x - x1) / (x1 - x0)


def expected(lie, yards):
    """Strokes a tour player needs to hole out from a lie and distance."""
    if lie == "green":
        return interp(PUTTS, yards * 3)
    if lie == "water":   # a penalty, then a drop in the rough
        return 1 + interp(FAIRWAY, yards) + LIE_EXTRA["rough"]
    extra = LIE_EXTRA.get(lie, LIE_EXTRA["rough"])
    if lie == "bunker" and yards < 60:
        extra = 0.15   # greenside: tour players are good from there
    return interp(FAIRWAY, yards) + extra


def make_chance(feet, putting=1.0):
    return max(0.004, interp(MAKES, feet * putting)) if feet * putting <= 100 else 0.004


class Player(Striker):
    """One golfer's play on one hole: their decisions; the engine's swings."""

    def value(self, p, ball):
        """Strokes to come from where a shot finished (its penalties included)."""
        s = self.surface(p)
        if s == "ob":   # stroke and distance: the penalty, and this shot again
            return 1 + expected(ball.lie, dist(ball.pos, self.hole.pin))
        return expected(s, dist(p, self.hole.pin))

    # ----------------------------------------------------------------------
    # Full swings

    def plans(self, ball):
        """(kind, club or None, where it should finish) worth considering."""
        pos, pin, lie = ball.pos, self.hole.pin, ball.lie
        d = dist(pos, pin)
        options = clubs.playable(lie)
        longest = clubs.carry(options[0], lie, self.g.power) + options[0].roll * clubs.LIES[lie].roll
        out = []
        if d <= longest * 1.1:
            center = self.hole.green.centroid()
            for blend in (0.0, 0.35, 0.7):   # at the flag, or toward the fat of the green
                out.append(("approach", None, lerp(pin, center, blend)))
        if d > longest * 0.85 or lie == "trees":
            kind = "tee" if lie == "tee" else "punch" if lie == "trees" else "layup"
            for club in options[:7] if lie != "trees" else options:
                reach = clubs.carry(club, lie, self.g.power) + club.roll * clubs.LIES[lie].roll
                target = ahead_on_path(self.hole.path, pos, reach)
                if dist(target, pin) < 30:
                    continue   # that's an approach
                b = heading(pos, target)
                for off in (0, -12, 12, -24, 24):
                    out.append((kind, club, step(target, b + 90, off)))
                # Any fairway in reach, in a fan about that line: a hole's
                # mapped line can cut a dogleg's corner.
                for turn in range(-FAN, FAN + 1, 5):
                    spot = step(pos, b + turn, reach)
                    if turn and self.surface(spot) == "fairway":
                        out.append((kind, club, spot))
        if not out:
            out.append(("approach", None, pin))
        return out

    def club_and_carry(self, kind, club, aim, ball, tail):
        """The club for a plan and the carry swung for (in still air)."""
        lie = ball.lie
        f = wx.carry_factor(self.weather, tail)
        if club is None:   # an approach: as far as the aim (as it plays: uphill, farther), less a little roll
            need = max(10.0, dist(ball.pos, aim) - 2 + self.rise(ball.pos, aim)) / f
            club = clubs.club_for(need, lie, self.g.power)
            return club, min(need, clubs.carry(club, lie, self.g.power))
        return club, clubs.carry(club, lie, self.g.power)

    def cost(self, plan, ball):
        kind, club, aim = plan
        b = heading(ball.pos, aim)
        tail, _ = self.weather.wind_on(b, GUST)
        club, fly = self.club_and_carry(kind, club, aim, ball, tail)
        fly = fly * wx.carry_factor(self.weather, tail) - self.rise(ball.pos, aim)
        lie = clubs.LIES.get(ball.lie, clubs.LIES["rough"])
        reach = fly + (club.roll * lie.roll if kind != "approach" else 2)
        center = step(ball.pos, b, min(dist(ball.pos, aim), reach))
        spread = club.spread * self.g.accuracy * lie.spread
        across = fly * math.tan(math.radians(spread * 1.05))   # as the engine's swings scatter
        along = fly * SCATTER * self.g.accuracy
        total = 0.0
        for a, l in SAMPLES:
            total += self.value(step(step(center, b, l * along), b + 90, a * across), ball)
        return total / len(SAMPLES)

    def decide(self, ball):
        """(the swing for this ball, what the shot is for)."""
        pos = ball.pos
        d = dist(pos, self.hole.pin)
        if ball.lie == "green":
            return self.putting(ball), "putt"
        if d <= SHORT_GAME or (ball.lie == "bunker" and d <= 60):
            return self.short_game(ball)
        kind, club, aim = min(self.plans(ball), key=lambda p: self.cost(p, ball))
        b = heading(pos, aim)
        # Played for the wind they feel: clubbed up or down, aimed off.
        tail, cross = self.weather.wind_on(b, GUST)
        club, fly = self.club_and_carry(kind, club, aim, ball, tail)
        expect = fly * wx.carry_factor(self.weather, tail) - self.rise(pos, aim)
        line = b - math.degrees(math.atan2(wx.drift(cross, expect), max(expect, 1)))
        power = fly / max(1.0, clubs.carry(club, ball.lie, self.g.power))
        return Swing(club, line, "full", min(1.0, power)), kind

    def short_game(self, ball):
        """A chip, pitch or splash at the hole, hit to finish a little past it."""
        pos, pin = ball.pos, self.hole.pin
        d = dist(pos, pin)
        if ball.lie == "bunker":
            kind, keys = ("blast", ("SW", "LW")) if d <= 30 else ("pitch", ("SW", "GW", "PW"))
        elif ball.lie in ("fairway", "tee") and d <= 25:
            kind, keys = "chip", ("PW", "9i") if d > 12 else ("SW", "PW")
        else:
            kind, keys = "pitch", ("LW", "SW", "GW", "PW") if ball.lie != "fairway" else ("SW", "GW", "PW")
        t = BY_TYPE[kind]
        lie = ball.lie if ball.lie in clubs.LIES else "rough"
        want = (d + 0.3) / (1 + t.roll)   # the carry that runs out to just past the hole
        reach = lambda key: clubs.carry(engine.club(key), lie, self.g.power) * t.carry
        # The first that gets there, else the one that goes farthest.
        key = next((k for k in keys if reach(k) * 1.05 >= want), max(keys, key=reach))
        club, full = engine.club(key), reach(key)
        power = want / max(1.0, full)
        swing = Swing(club, heading(pos, pin), kind, min(1.05, power))
        return self.read(ball, swing, 0.3), None

    def putting(self, ball):
        """A putt at the hole, read to finish PUTT_PAST beyond it."""
        pos, pin = ball.pos, self.hole.pin
        b = heading(pos, pin)
        return self.read(ball, Swing(PUTTER, b, "putt", length=dist(pos, pin) + PUTT_PAST), PUTT_PAST)

    def play(self, ball):
        """The next shot (and any drop after it) for this golfer's ball."""
        if ball.strokes >= MAX_STROKES:
            return self.pickup(ball)
        swing, kind = self.decide(ball)
        return self.strike(ball, swing, kind)


def play_hole(course, hole, golfers, order, weather, rng):
    balls = [Ball(hole.tee) for _ in golfers]
    players = [Player(course, hole, g, i, weather, rng) for i, g in enumerate(golfers)]
    shots = []
    while True:
        left = [i for i in order if not balls[i].holed]
        if not left:
            break
        teeing = [i for i in left if balls[i].strokes == 0]
        # Honors on the tee, then farthest from the hole (the first in order on a tie).
        i = teeing[0] if teeing else max(left, key=lambda i: dist(balls[i].pos, hole.pin))
        shots += players[i].play(balls[i])
    return HolePlay(hole, list(order), shots, [b.strokes for b in balls])


class Round:
    """A round for a group, simulated a hole at a time as it's wanted."""

    def __init__(self, course, golfers, weather, seed, holes=None):
        self.course, self.golfers, self.weather, self.seed = course, golfers, weather, seed
        self.holes = list(holes if holes is not None else course.holes)
        self.played = []

    def play(self, i):
        """The play on the i-th hole of the round."""
        while len(self.played) <= i:
            j = len(self.played)
            hole = self.holes[j]
            rng = random.Random("%s:%d:%d" % (self.seed, j, hole.number))
            self.played.append(play_hole(self.course, hole, self.golfers, self.honors(j), self.weather, rng))
        return self.played[i]

    def honors(self, j):
        """Tee order on the j-th hole: the best score on the hole before first,
        ties keeping the order they teed off in there."""
        if j == 0:
            return list(range(len(self.golfers)))
        prev = self.played[j - 1]
        return sorted(prev.order, key=lambda i: (prev.scores[i], prev.order.index(i)))
