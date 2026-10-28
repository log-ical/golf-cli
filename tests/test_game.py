import re
import unittest

from tests.helpers import ROOT, Plane, tiny_course  # noqa: F401
from tests.test_screens import FakeTerm
from golf import engine, game, osm, play, players
from golf import settings as cfg
from golf import weather as wx
from golf.geo import dist
from golf.term import Colors, visible_len

_SGR = re.compile(r"\x1b\[[0-9;]*m")


def a_game(level="scratch", seed=1, help="caddie"):
    c = osm.parse(tiny_course(), "relation", 1)[0]
    return game.Game(c, players.you(level), level, wx.Weather(), seed, help=help)


class GameTest(unittest.TestCase):
    def test_the_caddie_sets_it_up(self):
        g = a_game()
        self.assertEqual(g.setup.club, "D")          # a par 4 from the tee
        self.assertEqual(g.setup.kind, "full")
        self.assertLess(abs(g.off_flag()), 15)
        g.aim(10)
        g.change_club(1)
        g.take_suggestion()
        self.assertEqual(g.setup.club, "D")

    def test_with_yardages_you_judge_it(self):
        g = a_game(help="yardages", seed=2)
        self.assertIsNone(g.suggested)               # the caddie isn't asked until you do
        self.assertAlmostEqual(g.off_flag(), 0, places=6)   # at the flag
        self.assertEqual(g.setup.club, "D")          # nothing carries 412 yards
        g.ball.pos, g.ball.lie = g.hole.pin[0] - 150, "fairway"
        g.ball.pos = (g.hole.pin[0] - 150, g.hole.pin[1])
        g.suggest()
        carry = g.carry()
        self.assertGreaterEqual(carry, 150)          # the shortest club that carries it, in still air
        longer = [k for k in g.clubs() if k != "P"]
        shorter = longer[longer.index(g.setup.club) + 1]
        g.change_club(1)
        self.assertEqual(g.setup.club, shorter)
        self.assertLess(g.carry(), 150)
        land, end, holed = play.aim_points(g)
        self.assertEqual((land, end, holed), (g.carry_spot(), g.carry_spot(), False))
        g.take_suggestion()
        self.assertIsNotNone(g.suggested)

    def test_with_no_help_its_all_yours(self):
        g = a_game(help="none", seed=2)
        self.assertFalse(g.caddie_on_hand)
        g.setup.club = "3W"
        g.hit(timing=0.0)
        if not g.ball.holed and g.ball.lie not in ("green",) and g.to_pin > game.SHORT:
            self.assertEqual(g.setup.club in ("3W", g.clubs()[0]), True)   # the club hit last, if it can be hit
        before = g.setup
        g.take_suggestion()
        self.assertIs(g.setup, before)                # no caddie to ask
        self.assertEqual(play.aim_points(g), (None, None, False))

    def test_changing_the_setup(self):
        g = a_game("tour")
        g.change_club(1)
        self.assertEqual(g.setup.club, "3W")
        g.change_club(-1)
        g.change_club(-1)
        self.assertEqual(g.setup.club, "LW")         # round to the shortest
        kinds = g.kinds()
        seen = {g.setup.kind}
        for _ in kinds:
            g.change_kind(1)
            seen.add(g.setup.kind)
        self.assertEqual(seen, set(kinds))
        g.aim_at_flag()
        self.assertAlmostEqual(g.off_flag(), 0, places=6)
        g.aim(-3)
        self.assertAlmostEqual(g.off_flag(), -3, places=6)
        g.setup.power = 0.3
        g.change_power(-0.5)
        self.assertEqual(g.setup.power, game.POWER_MIN)
        g.change_power(5)
        self.assertEqual(g.setup.power, 1.0)

    def test_a_hole_played_through(self):
        g = a_game()
        for _ in range(20):
            if g.ball.holed:
                break
            g.hit(timing=0.0)
        self.assertTrue(g.ball.holed)
        strokes = g.cards[0]
        self.assertEqual(strokes, g.ball.strokes)
        st = g.stats[0]
        self.assertGreaterEqual(st.putts, 0)
        self.assertIsNotNone(st.fairway)             # a par 4: the tee shot counted
        self.assertEqual(g.totals(), (strokes, 4, strokes - 4))
        g.next_hole()
        self.assertEqual((g.hole_i, g.ball.strokes), (1, 0))
        self.assertEqual(g.setup.club in g.clubs(), True)

    def test_putting_setup(self):
        g = a_game()
        g.ball.pos, g.ball.lie = g.hole.pin[0] - 4, g.hole.pin[1]
        g.ball.pos = (g.hole.pin[0] - 4, g.hole.pin[1])
        g.ball.lie = "green"
        g.suggest()
        self.assertEqual((g.setup.club, g.setup.kind), ("P", "putt"))
        self.assertAlmostEqual(g.setup.length, 4 + engine.PUTT_PAST, delta=0.01)   # the caddie's: a bit past
        g.help = "yardages"
        g.suggest()
        self.assertAlmostEqual(g.setup.length, 4, delta=0.01)                      # yours: the distance
        self.assertAlmostEqual(g.swing(power=0.5).length, 2, delta=0.01)          # on the meter: half of it
        g.change_length(-100)
        self.assertEqual(g.setup.length, game.LENGTH_MIN)
        land, end, holed = g.preview()
        self.assertLess(dist(g.ball.pos, end), 0.5)

    def test_picking_up(self):
        g = a_game()
        g.ball.strokes = engine.MAX_STROKES
        shots = g.hit()
        self.assertEqual(shots[0].kind, "pickup")
        self.assertEqual(g.cards[0], engine.MAX_STROKES + 1)


class MeterTest(unittest.TestCase):
    def test_power_then_timing(self):
        m = play.Meter(0.0)
        self.assertIsNone(m.press(0.8))                         # the power, where the meter was
        self.assertAlmostEqual(m.power, 0.8 * play.RISE)
        power, timing = m.press(0.8 + m.power / m.fall)         # struck right at the mark
        self.assertAlmostEqual(timing, 0.0, places=6)
        m = play.Meter(0.0)
        m.press(0.5)
        _, early = m.press(0.5)                                 # struck at once: as early as it gets
        self.assertEqual(early, -1.0)
        m = play.Meter(0.0)
        m.press(0.8)
        mark = 0.8 + m.power / m.fall
        self.assertEqual(m.press(mark + 0.02)[1], 0.0)          # 20 ms late is still pure
        _, late = m.press(mark + 0.1)
        self.assertAlmostEqual(late, 0.5, places=6)             # half the window late
        self.assertEqual(play.timing_words(late), "late")
        self.assertEqual(play.timing_words(-0.3), "a touch early")

    def test_the_timing_settings(self):
        room = {}
        for timing in ("easy", "normal", "hard"):
            m = play.Meter(0.0, timing)
            room[timing] = m.window / m.fall                    # seconds either side of the mark
        self.assertGreater(room["easy"], room["normal"])
        self.assertGreater(room["normal"], room["hard"])
        self.assertAlmostEqual(room["normal"], 0.2)

    def test_running_out_is_late(self):
        m = play.Meter(0.0)
        self.assertIsNone(m.tick(0.5))
        self.assertIsNone(m.tick(play.OVERSWING / play.RISE + 0.1))   # all the way back: on its way down
        power, timing = m.tick(10)
        self.assertEqual((power, timing), (play.OVERSWING, 1.0))


class ScreenTest(unittest.TestCase):
    def setUp(self):
        self.saved = dict(cfg.settings)
        cfg.settings["camera"] = "whole hole"   # quicker; the cameras have their own tests

    def tearDown(self):
        cfg.settings.clear()
        cfg.settings.update(self.saved)

    def test_a_round_on_screen(self):
        g = a_game()
        scr = play.Screen(g, Colors("256"))
        now = 0.0
        phases = set()
        for size in ((120, 40), (80, 24), (40, 12)):
            term = FakeTerm(*size)
            for _ in range(400):
                lines = play.frame(term, scr, now, 0.05)
                phases.add(scr.phase)
                self.assertLessEqual(len(lines), size[1])
                for line in lines:
                    self.assertLessEqual(visible_len(line), size[0] + 2, _SGR.sub("", line))
                if scr.phase == "done":
                    break
                if scr.phase == "aim":
                    play.handle(scr, " ", now, None)
                    play.handle(scr, " ", now + 0.9, None)
                    play.handle(scr, " ", now + 0.9 + 0.9 / play.TIMINGS["normal"][0], None)
                else:
                    play.handle(scr, " ", now, None)
                now += 1.0
                scr.tick(now, 1.0)
        self.assertTrue({"intro", "aim", "flight", "result", "summary"} <= phases)

    def test_the_setup_shows(self):
        g = a_game(help="yardages")
        scr = play.Screen(g, Colors("256"))
        scr.go("aim", 0.0)
        text = "\n".join(_SGR.sub("", l) for l in play.frame(FakeTerm(120, 40), scr, 0.0, 0.0))
        self.assertIn("Driver", text)
        self.assertIn("Full swing", text)
        self.assertIn("ask the caddie", text)
        self.assertIn("carries", text)
        self.assertNotIn("from the flag", text)       # where it stops is yours to judge
        self.assertIn("×", text)                      # where it'd come down, in still air
        self.assertNotIn("uphill", text)
        g.course.ground = Plane(0.01, 0)              # rising toward the green, 4 yards
        text = "\n".join(_SGR.sub("", l) for l in play.frame(FakeTerm(120, 40), scr, 0.0, 0.0))
        self.assertIn("12 ft uphill", text)
        g.help = "none"
        text = "\n".join(_SGR.sub("", l) for l in play.frame(FakeTerm(120, 40), scr, 0.0, 0.0))
        self.assertNotIn("uphill", text)              # yours to see
        g.help = "yardages"
        g.course.ground = None
        cfg.settings["course_only"] = True
        bare = play.frame(FakeTerm(120, 40), scr, 0.0, 0.0)
        self.assertEqual(len(bare), 40)
        self.assertNotIn("Driver", "\n".join(_SGR.sub("", l) for l in bare))


if __name__ == "__main__":
    unittest.main()
