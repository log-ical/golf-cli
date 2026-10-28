import random
import statistics
import unittest

from tests.helpers import ROOT, tiny_course  # noqa: F401
from golf import clubs, osm, players, sim
from golf import weather as wx
from golf.geo import dist


def course():
    return osm.parse(tiny_course(), "relation", 1)[0]


class ModelTest(unittest.TestCase):
    def test_strokes_to_go_rise_with_distance_and_worse_lies(self):
        yards = (20, 80, 150, 250, 450)
        fairway = [sim.expected("fairway", y) for y in yards]
        self.assertEqual(fairway, sorted(fairway))
        for y in yards:
            self.assertLess(sim.expected("fairway", y), sim.expected("rough", y))
            self.assertLess(sim.expected("rough", y), sim.expected("trees", y))
            self.assertLess(sim.expected("rough", y), sim.expected("water", y))
        self.assertAlmostEqual(sim.expected("green", 1), 1.04)   # 3 feet

    def test_putts_drop_less_often_the_longer_they_are(self):
        chances = [sim.make_chance(f) for f in (1, 3, 6, 10, 20, 40, 80, 200)]
        self.assertEqual(chances, sorted(chances, reverse=True))
        self.assertGreater(chances[0], 0.99)
        self.assertLess(sim.make_chance(10, putting=1.8), sim.make_chance(10))

    def test_club_choice(self):
        self.assertEqual(clubs.club_for(150, "fairway").name, "9-iron")
        self.assertEqual(clubs.club_for(60, "fairway").name, "Lob wedge 60°")    # swung softer
        self.assertEqual(clubs.club_for(400, "fairway").name, "3-wood")      # no driver off the deck
        self.assertEqual(clubs.club_for(400, "trees").name, "5-iron")
        self.assertEqual(clubs.playable("tee")[0].name, "Driver")


class HoleTest(unittest.TestCase):
    def setUp(self):
        self.c = course()
        self.golfers = players.group(3, "tour", random.Random(5))

    def test_every_ball_is_holed_and_the_score_adds_up(self):
        for seed in range(30):
            hp = sim.play_hole(self.c, self.c.holes[0], self.golfers, [0, 1, 2], wx.Weather(), random.Random(seed))
            for i in range(3):
                mine = [s for s in hp.shots if s.player == i]
                self.assertTrue(mine[-1].holed)
                self.assertEqual(hp.scores[i], mine[-1].number)
                strokes = sum(1 for s in mine if s.kind != "drop") + sum(s.penalty for s in mine)
                self.assertEqual(strokes, hp.scores[i])
                for a, b in zip(mine, mine[1:]):   # each shot starts where the last left the ball
                    self.assertLess(dist(a.end if not a.penalty or b.kind == "drop" else a.start, b.start), 1e-6)

    def test_order_of_play(self):
        hp = sim.play_hole(self.c, self.c.holes[0], self.golfers, [2, 0, 1], wx.Weather(), random.Random(3))
        self.assertEqual([s.player for s in hp.shots[:3]], [2, 0, 1])   # the honor, then the rest
        # After the tee, whoever is farthest out plays (drops follow the shot they're for).
        balls = {}
        for s in hp.shots:
            if s.kind != "drop" and len(balls) == 3:
                waiting = {p: d for p, d in balls.items() if d > 0}
                self.assertEqual(s.from_pin, max(waiting.values()))
            balls[s.player] = s.to_pin

    def test_penalty_drops_land_on_dry_ground(self):
        weekend = players.group(4, "weekend", random.Random(1))
        drops = 0
        for seed in range(60):
            hp = sim.play_hole(self.c, self.c.holes[0], weekend, [0, 1, 2, 3], wx.Weather(), random.Random(seed))
            for s in hp.shots:
                if s.kind == "drop":
                    drops += 1
                    self.assertNotIn(self.c.surface_at(s.end), ("water", "ob"))
        self.assertGreater(drops, 0)

    def test_tee_shots_find_the_fairway_and_go_the_distance(self):
        g = [players.Golfer("Even Keel", miss=0.0)]
        drives = []
        for seed in range(40):
            hp = sim.play_hole(self.c, self.c.holes[0], g, [0], wx.Weather(), random.Random(seed))
            drives.append(hp.shots[0])
        self.assertGreater(statistics.mean(s.length for s in drives), 240)
        self.assertGreaterEqual(sum(s.lie == "fairway" for s in drives), 18)   # tour pros hit about 60%


class RoundTest(unittest.TestCase):
    def test_same_seed_same_round_and_honors(self):
        c = course()
        g = players.group(3, "mixed", random.Random(2))
        a, b = sim.Round(c, g, wx.Weather(), seed=42), sim.Round(c, g, wx.Weather(), seed=42)
        for j in range(len(c.holes)):
            pa, pb = a.play(j), b.play(j)
            self.assertEqual([(s.player, s.end) for s in pa.shots], [(s.player, s.end) for s in pb.shots])
        first = a.play(0)
        best = min(range(3), key=lambda i: (first.scores[i], first.order.index(i)))
        self.assertEqual(a.play(1).order[0], best)
        self.assertNotEqual([s.end for s in sim.Round(c, g, wx.Weather(), seed=43).play(0).shots],
                            [s.end for s in first.shots])

    def test_levels_score_in_order(self):
        c = course()
        totals = {}
        for level in ("tour", "weekend"):
            scores = []
            for seed in range(25):
                rnd = sim.Round(c, players.group(2, level, random.Random(seed)), wx.Weather(), seed)
                scores += [sum(rnd.play(j).scores[i] for j in range(len(c.holes))) for i in range(2)]
            totals[level] = statistics.mean(scores) - c.par
        self.assertLess(totals["tour"], 1.5)           # around par
        self.assertGreater(totals["weekend"], totals["tour"] + 2)


class WeatherTest(unittest.TestCase):
    def test_wind_components(self):
        w = wx.Weather(wind_mph=10, wind_from=0)   # from the north, blowing south
        tail, cross = w.wind_on(180)                # a shot south: straight downwind
        self.assertAlmostEqual(tail, 10)
        self.assertAlmostEqual(cross, 0, places=9)
        tail, cross = w.wind_on(90)                 # a shot east: blowing it right
        self.assertAlmostEqual(tail, 0, places=9)
        self.assertAlmostEqual(cross, 10)
        self.assertLess(wx.carry_factor(w, -10), 1 < wx.carry_factor(w, 10) and 1.0)
        self.assertAlmostEqual(wx.drift(10, 150), 8)

    def test_live_reads_open_meteo(self):
        from unittest import mock
        reply = {"current": {"temperature_2m": 61.5, "wind_speed_10m": 12.0, "wind_direction_10m": 250,
                             "wind_gusts_10m": 20.0, "weather_code": 61}}
        with mock.patch.object(wx, "fetch_json", return_value=reply) as f:
            w = wx.live(36.56, -121.94)
        self.assertIn("latitude=36.5600", f.call_args[0][0])
        self.assertEqual((w.temp_f, w.wind_mph, w.wind_from, w.source, w.sky, w.wet),
                         (61.5, 12.0, 250.0, "live", "rain", True))
        with mock.patch.object(wx, "fetch_json", return_value={}):
            self.assertEqual(wx.live(0, 0).temp_f, 70.0)   # missing numbers: a mild day

    def test_random_days_vary(self):
        days = [wx.random_day(random.Random(i)) for i in range(50)]
        self.assertGreater(len({round(d.wind_mph) for d in days}), 8)
        self.assertTrue(all(d.gust_mph >= d.wind_mph for d in days))


if __name__ == "__main__":
    unittest.main()
