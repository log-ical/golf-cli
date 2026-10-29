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

    def test_yardages_set_a_soft_wedge_inside_its_full_carry(self):
        g = a_game(help="yardages")
        g.ball.pos, g.ball.lie = (g.hole.pin[0] - 60, g.hole.pin[1]), "fairway"
        g.suggest()
        self.assertEqual((g.setup.club, g.setup.kind), ("LW", "full"))
        self.assertLess(g.setup.power, 1.0)
        self.assertAlmostEqual(g.carry(), 60, delta=1)       # it carries the distance, not the club's full 80-odd

    def test_off_the_putter_is_a_chip(self):
        g = a_game(help="none")
        g.ball.pos, g.ball.lie = (g.hole.pin[0] - 8, g.hole.pin[1]), "fairway"
        g.suggest()
        self.assertEqual(g.setup.club, "P")
        g.change_club(1)                                      # c
        self.assertEqual((g.setup.club, g.setup.kind), ("LW", "chip"))
        _, end, _ = g.preview()
        self.assertLess(dist(end, g.hole.pin), 4)             # set up to run out to the hole

    def test_a_putt_from_the_fringe_is_set_to_get_there(self):
        g = a_game(help="yardages")
        g.ball.pos, g.ball.lie = g.hole.pin, "green"
        surface = g.player.surface
        g.player.surface = lambda p: "fairway" if p[0] < g.hole.pin[0] - 4 else surface(p)   # 4 yards of fringe
        g.ball.pos = (g.hole.pin[0] - 8, g.hole.pin[1])
        g.suggest()
        self.assertGreater(g.setup.length, 8 + 0.5 + 2)       # more than the distance: the fringe takes some
        self.assertAlmostEqual(g.flat_roll(g.setup.length), 8.5, delta=0.3)

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
        self.assertAlmostEqual(g.setup.length, 4 + 2 / 3, delta=0.01)              # yours: a bit past, to the foot
        self.assertAlmostEqual(g.swing(power=0.5).length, 7 / 3, delta=0.01)      # on the meter: half of it
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

    def test_a_new_setup_ignores_a_late_skip(self):
        # Space pressed to skip the pause after a shot, just as the next setup
        # comes up on its own, mustn't swing.
        g = a_game()
        scr = play.Screen(g, Colors("256"))
        scr.go("aim", 10.0)
        play.handle(scr, " ", 10.0 + play.SETTLE / 2, None)
        self.assertEqual((scr.phase, g.ball.strokes), ("aim", 0))
        play.handle(scr, " ", 10.0 + play.SETTLE * 1.5 + 0.01, None)   # a pause since the last press: it swings
        self.assertEqual(scr.phase, "meter" if play.on_meter(g) else "flight")

    def test_holding_space_never_swings(self):
        for swing in ("meter", "simple"):
            cfg.settings["swing"] = swing
            g = a_game(help="yardages")
            scr = play.Screen(g, Colors("256"))
            scr.go("result", 0.0)
            t = 0.0
            while t < 3.0:                               # the key held down: repeating 30 times a second
                play.handle(scr, " ", t, None)
                t += 1 / 30
            self.assertEqual((scr.phase, g.ball.strokes), ("aim", 0), swing)

    def test_esc_on_the_meter_is_not_this_swing_and_leaving_asks_twice(self):
        cfg.settings["swing"] = "meter"
        g = a_game(help="yardages")
        scr = play.Screen(g, Colors("256"))
        scr.go("aim", 0.0)
        play.handle(scr, " ", 1.0, None)
        self.assertEqual(scr.phase, "meter")
        self.assertIsNone(play.handle(scr, "esc", 1.2, None))
        self.assertEqual((scr.phase, scr.meter, g.ball.strokes), ("aim", None, 0))
        self.assertEqual(play.handle(scr, "b", 1.3, None), "back")   # nothing played yet: nothing to lose
        g.hit()
        scr.go("aim", 2.0)
        self.assertIsNone(play.handle(scr, "b", 3.0, None))           # a round in play: are you sure?
        self.assertIn("b again to leave the round", _SGR.sub("", play.frame(FakeTerm(120, 40), scr, 3.0, 0)[-1]))
        self.assertEqual(play.handle(scr, "b", 3.5, None), "back")
        self.assertIsNone(play.handle(scr, "q", 4.0, None))
        self.assertIsNone(play.handle(scr, "s", 4.1, None))           # anything else: playing on
        self.assertIsNone(play.handle(scr, "q", 4.2, None))
        self.assertEqual(play.handle(scr, "q", 4.3, None), "quit")

    def test_esc_while_looking_only_stops_looking(self):
        g = a_game(help="yardages")
        g.hit()                                          # a round in play
        scr = play.Screen(g, Colors("256"))
        scr.go("aim", 0.0)
        play.frame(FakeTerm(120, 40), scr, 1.0, 0.0)
        play.handle(scr, "l", 1.0, None)
        self.assertTrue(scr.ui["look"].on)
        self.assertIsNone(play.handle(scr, "esc", 1.1, None))
        self.assertFalse(scr.ui["look"].on)
        self.assertIsNone(scr.leaving)                   # not asked about leaving

    def test_tapping_right_keeps_aiming(self):
        g = a_game(help="yardages")
        scr = play.Screen(g, Colors("256"))
        scr.go("aim", 0.0)
        start = g.setup.bearing
        for i in range(10):
            play.handle(scr, "right", 1.0 + i * 0.1, None)
        self.assertAlmostEqual((g.setup.bearing - start) % 360, 10 * play.AIM)
        play.handle(scr, " ", 2.0, None)                 # and a swing straight after
        self.assertNotEqual(scr.phase, "aim")

    def test_looking_from_the_intro(self):
        g = a_game()
        scr = play.Screen(g, Colors("256"))
        scr.go("intro", 0.0)
        play.frame(FakeTerm(120, 40), scr, 0.1, 0.0)
        play.handle(scr, "l", 0.2, None)
        self.assertTrue(scr.ui["look"].on)
        self.assertIs(scr.ui["look"].hole, g.hole)

    def test_plus_leaves_the_caddies_extra_alone(self):
        g = a_game()
        g.setup.power = 1.05
        g.change_power(0.05)
        self.assertEqual(g.setup.power, 1.05)
        g.change_power(-0.05)
        self.assertEqual(g.setup.power, 1.0)

    def test_a_soft_swing_still_gives_time_to_strike(self):
        m = play.Meter(0.0)
        m.press(0.1)                                     # 10% power
        self.assertEqual(m.power, 0.1)
        self.assertGreater(m.value(0.1 + play.DOWN - 0.25), m.window)   # the mark's still to come...
        self.assertEqual(m.press(0.1 + play.DOWN), (0.1, 0.0))           # ...and struck on it, it's pure

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

    def test_reading_a_putt(self):
        self.assertEqual(play.read_words(0.0, 0.0, "yards"), "level · straight")
        self.assertEqual(play.read_words(1 / 12, 0.015, "yards"), "3 in uphill · breaks right to left, 1.5%")
        self.assertEqual(play.read_words(-1.0, -0.02, "yards"), "3 ft downhill · breaks left to right, 2.0%")
        g = a_game(help="yardages")
        g.ball.pos, g.ball.lie = (g.hole.pin[0], g.hole.pin[1] - 3), "green"   # 3 yards south, putting north
        g.suggest()
        g.course.ground = Plane(0.02, 0)               # higher to the east: on the right, so it breaks to the left
        self.assertGreater(g.break_across(), 0.019)
        scr = play.Screen(g, Colors("256"))
        scr.go("aim", 0.0)
        text = "\n".join(_SGR.sub("", l) for l in play.frame(FakeTerm(140, 40), scr, 0.0, 0.0))
        self.assertIn("breaks right to left, 2.0%", text)

    def test_the_caddie_shows_the_putt_breaking(self):
        g = a_game(help="caddie")
        g.ball.pos, g.ball.lie = (g.hole.pin[0], g.hole.pin[1] - 4), "green"
        g.course.ground = g.player.ground = Plane(0.02, 0)
        g.suggest()
        plan = play.aim_shot(g)
        self.assertTrue(plan.holed and plan.path)       # read to drop, along a curve
        m = play.Screen(g, Colors("256")).views.get(g.course, g.hole, 120, 30, 1.0, "green", True, wait=True)
        tints, marks = play.aim_marks(g, m, plan)
        self.assertGreater(max(p[0] for p, _, _ in tints), g.ball.pos[0] + 0.1)   # bowed up the slope, not straight
        self.assertEqual(marks, [])                     # no cross where it drops

    def test_keys_help_and_looking_around(self):
        g = a_game()
        scr = play.Screen(g, Colors("256"))
        scr.go("aim", 0.0)
        term = FakeTerm(120, 40)
        play.frame(term, scr, 1.0, 0.0)
        play.handle(scr, "?", 1.0, None)
        self.assertIn("Single player: the keys", "".join(_SGR.sub("", l) for l in play.frame(term, scr, 1.0, 0.0)))
        play.handle(scr, "x", 1.0, None)               # any key: back
        self.assertNotIn("help", scr.ui)
        bearing = g.setup.bearing
        play.handle(scr, "l", 1.0, None)
        self.assertTrue(scr.ui["look"].on)
        play.handle(scr, "right", 1.0, None)           # the camera moves, the aim doesn't
        play.handle(scr, "+", 1.0, None)
        self.assertEqual(g.setup.bearing, bearing)
        play.frame(term, scr, 1.0, 0.0)                # (its map asked for, and made)
        scr.views.settle()
        play.frame(term, scr, 1.0, 0.0)
        self.assertEqual(scr.ui["map"].level, "look")
        play.handle(scr, " ", 1.0, None)               # done looking: no swing
        self.assertFalse(scr.ui["look"].on)
        self.assertEqual((scr.phase, g.ball.strokes), ("aim", 0))
        play.handle(scr, "esc", 1.0, None)
        self.assertEqual(play.handle(scr, "esc", 1.0, None), "back")   # (looking, Esc only stops looking)


if __name__ == "__main__":
    unittest.main()
