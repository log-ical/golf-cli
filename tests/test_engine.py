import dataclasses
import math
import random
import statistics
import unittest

from tests.helpers import ROOT, Plane, at, tiny_course  # noqa: F401
from golf import clubs, engine, osm, players, sim
from golf import weather as wx
from golf.engine import Ball, Swing
from golf.geo import dist, heading, step


def course():
    return osm.parse(tiny_course(), "relation", 1)[0]


def golfer(**kw):
    base = dict(name="Test Pro", power=1.0, accuracy=1.0, miss=0.0, shape=0.0, short=1.0, putting=1.0)
    base.update(kw)
    return players.Golfer(**base)


class TypesTest(unittest.TestCase):
    def test_what_each_level_can_play(self):
        self.assertNotIn("draw", engine.types_for("7i", "fairway", "weekend"))
        self.assertIn("draw", engine.types_for("7i", "fairway", "scratch"))
        self.assertNotIn("stinger", engine.types_for("3W", "fairway", "scratch"))
        self.assertIn("stinger", engine.types_for("3W", "fairway", "tour"))
        self.assertNotIn("flop", engine.types_for("LW", "rough", "weekend"))
        self.assertEqual(engine.types_for("SW", "bunker", "weekend")[0], "blast")
        self.assertEqual(engine.types_for("P", "green", "weekend"), ["putt"])
        self.assertEqual(engine.types_for("D", "tee", "tour")[0], "full")

    def test_clubs_by_lie(self):
        self.assertEqual(engine.club_keys("green", 5), ["P"])
        self.assertEqual(engine.club_keys("tee", 400)[0], "D")
        self.assertNotIn("D", engine.club_keys("fairway", 200))
        self.assertIn("P", engine.club_keys("fairway", 15))    # putting from off the green
        self.assertNotIn("P", engine.club_keys("fairway", 80))
        self.assertNotIn("P", engine.club_keys("bunker", 15))


class StrikeTest(unittest.TestCase):
    def setUp(self):
        self.c = course()
        self.hole = self.c.holes[0]

    def strikes(self, swing, n=60, g=None, weather=None, lie="tee", pos=None):
        out = []
        for seed in range(n):
            st = engine.Striker(self.c, self.hole, g or golfer(), 0, weather or wx.Weather(), random.Random(seed))
            out.append(st.strike(Ball(pos or self.hole.tee, lie), swing)[0])
        return out

    def test_the_preview_is_where_it_goes_on_average(self):
        b = heading(self.hole.tee, self.hole.pin)
        swing = Swing(engine.club("7i"), b, "full", 1.0, timing=0.0)
        st = engine.Striker(self.c, self.hole, golfer(), 0, wx.Weather(), random.Random(0))
        land, end, holed = st.preview(Ball(self.hole.tee, "tee"), swing)
        shots = self.strikes(swing)
        mean = (statistics.mean(s.land[0] for s in shots), statistics.mean(s.land[1] for s in shots))
        self.assertLess(dist(mean, land), 4)
        self.assertAlmostEqual(dist(self.hole.tee, land), 178, delta=2)   # a 7-iron's carry
        self.assertFalse(holed)

    def test_timing_sends_it_left_or_right(self):
        b = heading(self.hole.tee, self.hole.pin)
        side = lambda s: _right_of(self.hole.tee, b, s.end)
        early = statistics.mean(side(s) for s in self.strikes(Swing(engine.club("7i"), b, timing=-1.0)))
        late = statistics.mean(side(s) for s in self.strikes(Swing(engine.club("7i"), b, timing=1.0)))
        good = statistics.mean(side(s) for s in self.strikes(Swing(engine.club("7i"), b, timing=0.0)))
        self.assertLess(early, -15)
        self.assertGreater(late, 15)
        self.assertLess(abs(good), 5)

    def test_power_and_wind(self):
        b = heading(self.hole.tee, self.hole.pin)
        carry = lambda **kw: statistics.mean(s.flown for s in self.strikes(Swing(engine.club("7i"), b, **kw), 30))
        self.assertAlmostEqual(carry(power=0.5) / carry(power=1.0), 0.5, delta=0.05)
        into = wx.Weather(wind_mph=15, wind_from=b)
        behind = wx.Weather(wind_mph=15, wind_from=(b + 180) % 360)
        across = wx.Weather(wind_mph=15, wind_from=(b - 90) % 360)   # from the left
        flown = lambda w, kind="full": statistics.mean(
            s.flown for s in self.strikes(Swing(engine.club("7i"), b, kind), 30, weather=w))
        self.assertLess(flown(into), 160)
        self.assertGreater(flown(behind), 185)
        drift = lambda kind: statistics.mean(_right_of(self.hole.tee, b, s.land) for s in self.strikes(
            Swing(engine.club("5i"), b, kind), 30, weather=across))
        self.assertGreater(drift("full"), 10)
        self.assertLess(drift("punch"), drift("full") * 0.7)      # a low shot rides it out
        self.assertLess(flown(wx.Weather(), "punch"), flown(wx.Weather()))

    def test_putts(self):
        pin = self.hole.pin
        made = lambda feet, length=None: sum(s.holed for s in self.strikes(
            Swing(engine.PUTTER, 90, "putt", length=length or feet / 3 + engine.PUTT_PAST), 200,
            g=golfer(putting=1.0), lie="green", pos=step(pin, 270, feet / 3))) / 200
        self.assertGreater(made(3), 0.85)
        self.assertLess(made(30), 0.2)
        self.assertGreater(made(10), made(30))
        self.assertEqual(made(3, length=10), 0)   # rammed 30 feet past: it doesn't drop

    def test_short_game_goes_about_the_distance_hit(self):
        pin = self.hole.pin
        start = step(pin, 270, 20)   # 20 yards short, on the fairway
        b = heading(start, pin)
        st = engine.Striker(self.c, self.hole, golfer(), 0, wx.Weather(), random.Random(0))
        t = engine.BY_TYPE["chip"]
        want = 20.3 / (1 + t.roll)
        power = want / (clubs.carry(engine.club("PW"), "fairway") * t.carry)
        land, end, _ = st.preview(Ball(start, "fairway"), Swing(engine.club("PW"), b, "chip", power))
        self.assertLess(dist(end, pin), 3)
        shots = self.strikes(Swing(engine.club("PW"), b, "chip", power), 40, lie="fairway", pos=start)
        self.assertLess(statistics.mean(s.to_pin for s in shots), 3.5)


class SlopeTest(unittest.TestCase):
    def setUp(self):
        self.c = course()
        self.hole = self.c.holes[0]   # runs east, to a green round (410, 0)

    def striker(self, ground, seed=0, g=None):
        self.c.ground = ground
        return engine.Striker(self.c, self.hole, g or golfer(), 0, wx.Weather(), random.Random(seed))

    def test_downhill_carries_farther(self):
        b = heading(self.hole.tee, self.hole.pin)
        swing = Swing(engine.club("7i"), b, timing=0.0)
        flown = {}
        for name, ground in (("flat", None), ("down", Plane(-0.1, 0)), ("up", Plane(0.1, 0))):
            land, _, _ = self.striker(ground).preview(Ball(self.hole.tee, "tee"), swing)
            flown[name] = dist(self.hole.tee, land)
        self.assertAlmostEqual(flown["flat"], 178, delta=2)
        self.assertGreater(flown["down"], flown["flat"] + 12)   # 18 yards lower: about that much farther
        self.assertLess(flown["up"], flown["flat"] - 12)

    def test_putts_break_and_run(self):
        pin = self.hole.pin
        start = step(pin, 180, 6)                           # south of the hole, putting north
        swing = Swing(engine.PUTTER, 0, "putt", length=6)
        _, flat, _ = self.striker(None).preview(Ball(start, "green"), swing, cup=False)
        _, broke, _ = self.striker(Plane(0.03, 0)).preview(Ball(start, "green"), swing, cup=False)
        self.assertLess(abs(flat[0] - start[0]), 0.01)
        self.assertLess(broke[0], start[0] - 0.5)          # higher on the right: it falls away left (west)
        east = Swing(engine.PUTTER, 90, "putt", length=6)
        st = self.striker(Plane(0.03, 0))
        _, up, _ = st.preview(Ball(start, "green"), east, cup=False)
        _, down, _ = st.preview(Ball(start, "green"), dataclasses.replace(east, bearing=270), cup=False)
        self.assertLess(dist(start, up), 5)                 # uphill dies short of its length
        self.assertGreater(dist(start, down), 7)            # downhill runs on

    def test_the_read(self):
        pin = self.hole.pin
        start = step(pin, 180, 10 / 3)                      # 10 feet below the hole, across a slope
        ball = Ball(start, "green")
        self.c.ground = Plane(0.03, 0)
        p = sim.Player(self.c, self.hole, golfer(), 0, wx.Weather(), random.Random(0))
        swing = p.putting(ball)
        self.assertGreater(swing.bearing, 3)                # played up the slope, right of the hole
        self.assertTrue(p.preview(ball, swing)[2])          # and struck as meant, it drops
        made = 0
        for seed in range(100):
            p = sim.Player(self.c, self.hole, golfer(), 0, wx.Weather(), random.Random(seed))
            b = Ball(start, "green")
            made += p.strike(b, p.putting(b))[0].holed
        self.assertGreater(made, 25)                        # about as often as on the flat
        self.c.ground = None
        self.assertEqual(sim.Player(self.c, self.hole, golfer(), 0, wx.Weather(), random.Random(0)).putting(ball).bearing,
                         heading(start, pin))               # nothing to read

    def test_too_steep_to_stop_on(self):
        start = at(self.c, 200, 0)                          # on the fairway
        swing = Swing(engine.PUTTER, 90, "putt", length=0.3)
        _, flat, _ = self.striker(None).preview(Ball(start, "fairway"), swing)
        _, steep, _ = self.striker(Plane(0, 0.25)).preview(Ball(start, "fairway"), swing)
        self.assertLess(dist(start, flat), 0.5)
        self.assertLess(steep[1], start[1] - 3)             # off down the slope, south
        _, green, _ = self.striker(Plane(0.4, 0)).preview(Ball(step(self.hole.pin, 180, 5), "green"), swing)
        self.assertLess(dist(step(self.hole.pin, 180, 5), green), 3)   # a green's no steeper than greens are built


def _right_of(origin, bearing, p):
    """Yards p is right of the line from origin on bearing."""
    return dist(origin, p) * math.sin(math.radians(heading(origin, p) - bearing))


if __name__ == "__main__":
    unittest.main()
