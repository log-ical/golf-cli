import json
import os
import random
import re
import tempfile
import unittest
from unittest import mock

from tests.helpers import ROOT, tiny_course  # noqa: F401
from golf import camera, catalog, osm, panels, players, render, scoring, screens, sim, zen
from golf import settings as cfg
from golf import weather as wx
from golf.geo import dist, step
from golf.term import Colors, visible_len

_SGR = re.compile(r"\x1b\[[0-9;]*m")


def a_round(n=3, level="tour", seed=1):
    c = osm.parse(tiny_course(), "relation", 1)[0]
    return sim.Round(c, players.group(n, level, random.Random(seed)), wx.Weather(wind_mph=9, wind_from=200), seed)


class FakeTerm:
    def __init__(self, cols, rows):
        self.cols, self.rows = cols, rows

    def size(self):
        return self.cols, self.rows

    def pixel_aspect(self):
        return 1.0


class PlaybackTest(unittest.TestCase):
    def test_plays_every_shot_then_moves_on(self):
        rnd = a_round()
        pb = zen.Playback(rnd)
        seen = []
        for _ in range(100000):
            if pb.phase == "shot":
                seen.append((pb.hole_i, pb.shown))
            pb.tick(0.1, 4.0)
            if pb.phase == "done":
                break
        self.assertEqual(pb.phase, "done")
        for j in range(len(rnd.holes)):
            self.assertEqual(sorted({s for h, s in seen if h == j}), list(range(len(rnd.play(j).shots))))

    def test_the_pace_never_hurries_a_shot(self):
        for pace in (0.5, 4.0):
            pb = zen.Playback(a_round())
            pb.skip()
            pb.skip()
            self.assertEqual(pb.phase, "shot")
            pb.tick(1.0, pace)
            self.assertAlmostEqual(pb.elapsed, 1.0)          # in the air: real time
            pb.skip()
            self.assertEqual(pb.phase, "pause")
            pb.tick(0.25, pace)
            self.assertAlmostEqual(pb.elapsed, 0.25 * pace)  # between shots: at the pace
        shot = a_round().play(0).shots[0]
        self.assertGreater(camera.flight_time(shot), 4)     # a drive hangs about five seconds

    def test_pause_skip_back_and_holes(self):
        pb = zen.Playback(a_round())
        pb.paused = True
        pb.tick(10, 1.0)
        self.assertEqual((pb.phase, pb.elapsed), ("intro", 0.0))   # nothing moves while paused
        pb.skip()
        self.assertEqual((pb.phase, pb.shown), ("address", 0))
        pb.skip()
        self.assertEqual((pb.phase, pb.shown), ("shot", 0))
        pb.skip()
        self.assertEqual((pb.phase, pb.shown), ("pause", 1))
        pb.back()
        self.assertEqual((pb.phase, pb.shown), ("address", 0))     # the shot again
        pb.jump_hole(1)
        self.assertEqual((pb.hole_i, pb.phase), (1, "intro"))
        pb.back()
        self.assertEqual(pb.hole_i, 0)                             # into the hole before, its last shot
        self.assertEqual(pb.shown, len(pb.play.shots) - 1)
        pb.jump_hole(-1)
        self.assertEqual((pb.hole_i, pb.shown, pb.phase), (0, 0, "intro"))

    def test_cards_show_only_whats_been_played(self):
        rnd = a_round()
        pb = zen.Playback(rnd)
        self.assertTrue(all(s is None for card in pb.cards() for s in card))
        pb.jump_hole(1)
        cards = pb.cards()
        self.assertEqual([c[0] for c in cards], rnd.play(0).scores)
        self.assertTrue(all(c[1] is None for c in cards))

    def test_ball_moves_from_start_to_end_and_flies(self):
        shot = a_round().play(0).shots[0]
        self.assertEqual(camera.ball_at(shot, 0), shot.start)
        self.assertEqual(camera.ball_at(shot, 1e9), shot.end)
        f = camera.flight_time(shot)
        mid = camera.ball_at(shot, f / 2)
        self.assertNotIn(mid, (shot.start, shot.end))
        self.assertGreater(dist(shot.start, mid), dist(mid, shot.land))   # the air slows it down
        self.assertGreater(camera.height_at(shot, f / 2), 20)
        hop = camera.height_at(shot, f + 0.1)   # it lands, hops and runs out along the way it rolled
        self.assertTrue(0 < hop < camera.height_at(shot, f / 2) / 5)
        self.assertEqual(camera.height_at(shot, f + camera.HOP_TIME + 0.01), 0)
        self.assertLess(dist(camera.ball_at(shot, f + camera.roll_time(shot) * 0.9999), shot.end), 0.01)
        self.assertGreater(len(camera.trail(shot, 1e9, 2.0)), 10)


class FrameTest(unittest.TestCase):
    def setUp(self):
        self.saved = dict(cfg.settings)

    def tearDown(self):
        cfg.settings.clear()
        cfg.settings.update(self.saved)

    def frames(self, pb, size, views, steps=60, skips=3):
        ui = {}
        for _ in range(steps):
            lines = zen.round_frame(FakeTerm(*size), pb, views.colors, views, ui, 0.05)
            yield lines, ui
            for _ in range(skips):
                pb.skip()

    def test_frames_fit_every_window(self):
        cfg.settings["camera"] = "whole hole"   # the panels fit the same round any map (and this is quicker)
        rnd = a_round(4, "mixed", 7)
        views = camera.Views(Colors("truecolor"))
        for cols, rows in ((80, 24), (100, 30), (140, 45), (200, 60), (40, 12)):
            for lines, _ in self.frames(zen.Playback(rnd), (cols, rows), views):
                self.assertLessEqual(len(lines), rows)
                for l in lines:
                    self.assertLessEqual(visible_len(l), cols + 2, (cols, rows, _SGR.sub("", l)))

    def test_the_director_uses_every_camera(self):
        rnd = a_round(3, "tour", 2)
        views = camera.Views(Colors("256"))
        pb = zen.Playback(rnd)
        zen.round_frame(FakeTerm(90, 30), pb, views.colors, views, {}, 0.0)
        views.settle()
        used = {}
        for lines, ui in self.frames(pb, (90, 30), views, steps=80, skips=1):
            used.setdefault(ui["map"].level, pb.phase)
        self.assertLessEqual({"hole", "fairway", "green"}, set(used))   # (and on the cup, if a putt's short)
        self.assertLessEqual(set(used), {"hole", "fairway", "green", "cup"})
        cfg.settings["camera"] = "whole hole"
        cfg.settings["green_view"] = False
        pb = zen.Playback(rnd)
        levels = {ui["map"].level for _, ui in self.frames(pb, (90, 30), views, steps=60, skips=1)}
        self.assertEqual(levels, {"hole"})

    def test_a_new_golfer_is_a_cut_and_the_ball_is_followed(self):
        rnd = a_round(2, "tour", 3)
        views = camera.Views(Colors("256"))
        pb = zen.Playback(rnd)
        term, ui = FakeTerm(120, 40), {}
        zen.round_frame(term, pb, views.colors, views, ui, 0.0)
        views.settle()
        pb.skip()                                           # the first golfer at address
        zen.round_frame(term, pb, views.colors, views, ui, 0.0)
        m, first = ui["map"], m_off(ui)
        self.assertEqual(m.level, "fairway")
        self.assertTrue(m.contains(pb.current().start))
        pb.skip()
        for _ in range(40):                                 # two seconds of the drive
            pb.tick(0.05, 1.0)
            zen.round_frame(term, pb, views.colors, views, ui, 0.05)
        self.assertGreater(m_off(ui)[0], first[0])          # the camera went with it
        self.assertTrue(m.contains(camera.ball_at(pb.current(), pb.elapsed)))

    def test_zen_and_the_panels(self):
        cfg.settings["camera"] = "whole hole"
        rnd = a_round()
        views = camera.Views(Colors("256"))
        pb = zen.Playback(rnd)
        pb.skip()
        text = lambda lines: "\n".join(_SGR.sub("", l) for l in lines)
        full = zen.round_frame(FakeTerm(100, 40), pb, views.colors, views, {}, 0)
        self.assertIn("Tiny Links", text(full))
        self.assertIn("Par", text(full))
        cfg.settings["course_only"] = True
        only = zen.round_frame(FakeTerm(100, 40), pb, views.colors, views, {}, 0)
        self.assertEqual(len(only), 40)                      # all map
        self.assertNotIn("Tiny Links", text(only))
        self.assertTrue(all(visible_len(l) == 100 for l in only))
        cfg.settings.update(course_only=False, show_header=False, scorecard="off", show_group=False, show_keys=False)
        bare = zen.round_frame(FakeTerm(100, 40), pb, views.colors, views, {}, 0)
        self.assertNotIn("Tiny Links", text(bare))
        self.assertEqual(len(bare), 40)                      # the map takes what the panels left
        self.assertIn(rnd.golfers[pb.current().player].short_name, text(bare[-1:]))
        cfg.settings.update(scorecard="nine")
        card = text(zen.round_frame(FakeTerm(150, 40), pb, views.colors, views, {}, 0))
        self.assertNotIn(" 18", card.split("Par")[0][-80:])

    def test_frame_shows_the_hole_and_scores(self):
        rnd = a_round()
        pb = zen.Playback(rnd)
        pb.jump_hole(1)
        views = camera.Views(Colors("256"))
        text = "\n".join(_SGR.sub("", l) for l in zen.round_frame(FakeTerm(120, 40), pb, views.colors, views))
        self.assertIn("Tiny Links", text)
        self.assertIn("Hole 2", text)
        self.assertIn("Par", text)
        for g in rnd.golfers:
            self.assertIn(g.short_name, text)

    def test_looking_around_and_the_keys(self):
        rnd = a_round(2, "tour", 3)
        views = camera.Views(Colors("256"))
        pb = zen.Playback(rnd)
        term, ui = FakeTerm(120, 40), {}
        zen.round_frame(term, pb, views.colors, views, ui, 0.0)
        pb.skip()
        zen.round_frame(term, pb, views.colors, views, ui, 0.0)
        start = ui["map"].center()
        self.assertTrue(camera.look_key(ui, "l"))
        self.assertFalse(camera.look_key(ui, "n"))         # not a key for looking: the round's
        for key in ("right", "right", "-"):
            self.assertTrue(camera.look_key(ui, key))
        pb.paused = True
        for _ in range(2):
            lines = zen.round_frame(term, pb, views.colors, views, ui, 0.05)
            views.settle()
        lines = zen.round_frame(term, pb, views.colors, views, ui, 0.05)
        m = ui["map"]
        self.assertEqual(m.level, "look")
        self.assertGreater(m.to_pixel(m.center())[0], m.to_pixel(start)[0])   # moved down the hole
        self.assertIn("looking · ", _SGR.sub("", "\n".join(lines)))
        self.assertIn("LOOKING AROUND", _SGR.sub("", lines[-1]))
        self.assertTrue(camera.look_key(ui, "esc"))
        self.assertFalse(ui["look"].on)
        ui["help"] = True
        self.assertIn("Zen: the keys", _SGR.sub("", "\n".join(zen.round_frame(term, pb, views.colors, views, ui))))

    def test_a_ball_off_the_map_gets_a_map_of_its_own(self):
        rnd = a_round(1, "tour", 3)
        views = camera.Views(Colors("256"))
        hole = rnd.holes[0]
        shot = rnd.play(0).shots[0]
        far = (shot.start[0] + 30, shot.start[1] - 400)       # sliced a quarter mile out of bounds
        wild = sim.Shot(0, 1, "tee", "Driver", shot.start, far, far, shot.start, "ob", 500.0, 400.0)
        scene = camera.Scene(hole, None, "pause", wild, 0.0)
        ui = {}
        args = (rnd.course, 100, 30, 1.0, True)
        for _ in range(2):
            m = camera.direct(scene, views, *args, ui, 0.0)
            views.settle()
        m = camera.direct(scene, views, *args, ui, 0.0)
        self.assertEqual(m.level, "look")
        self.assertTrue(m.contains(far))

    def test_close_ups_show_the_ball_and_the_hole_at_once(self):
        rnd = a_round(1, "tour", 3)
        views = camera.Views(Colors("256"))
        hole = rnd.holes[0]
        pin = hole.pin
        putt = lambda d: sim.Shot(0, 3, "putt", "Putter", (pin[0] - d, pin[1]), (pin[0] - d, pin[1]),
                                  (pin[0] + 0.3, pin[1]), (pin[0], pin[1]), "green", 0.3, d)
        args = (rnd.course, 100, 30, 1.0, True)
        for d, level in ((3.0, "cup"), (3.9, "cup"), (25.0, "green")):
            scene = camera.Scene(hole, None, "address", putt(d), 0.0)
            for _ in range(2):
                camera.direct(scene, views, *args, {}, 0.0)
                views.settle()
            m = camera.direct(scene, views, *args, {}, 0.0)
            self.assertEqual(m.level, level, d)
            self.assertTrue(m.contains(scene.shot.start) and m.contains(pin), d)

    def test_looking_around_copes(self):
        rnd = a_round(1, "tour", 3)
        hole = rnd.holes[0]
        views = camera.Views(Colors("256"))
        args = (rnd.course, hole, 90, 30, 1.0, True)
        whole = views.get(*args[:5], "hole", True, wait=True)
        cup = views.get(*args[:5], "cup", True, wait=True)
        look = camera.Look()
        look.start(cup, whole, 1.0)
        most = look.zoom
        look.zoom_by(1, whole, 1.0)
        self.assertEqual(look.zoom, most)                     # already as close as it goes: + doesn't zoom out
        look.drawing = True
        look.stop()
        self.assertFalse(look.drawing)
        look.start(whole, whole, 1.0)
        look.view(views, *args[:2], 90, 30, 1.0, True, whole)
        views.settle()
        self.assertEqual(look.view(views, *args[:2], 90, 30, 1.0, True, whole).level, "look")
        small = views.get(rnd.course, hole, 60, 20, 1.0, "hole", True, wait=True)   # the window got smaller
        m = look.view(views, rnd.course, hole, 60, 20, 1.0, True, small)
        self.assertEqual((m.cols, m.rows), (60, 20))          # not the old size's map, while the new one's made
        views.close()

    def test_the_newest_look_is_made_first_and_a_failure_is_tried_again(self):
        rnd = a_round(1, "tour", 3)
        hole = rnd.holes[0]
        views = camera.Views(Colors("256"))
        views.jobs.put((-2, 0, None))                         # hold the worker up: stopped, to look in its queue
        views.settle()
        for i in range(3):
            views.get(rnd.course, hole, 60, 20, 1.0, "look", True, center=(i * 10.0, 0.0), scale=1.0)
        views.get(rnd.course, hole, 60, 20, 1.0, "fairway", True)
        queued = sorted(views.jobs.queue)
        self.assertEqual([job[1][9] for _, _, job in queued[:3]], [(20.0, 0.0), (10.0, 0.0), (0.0, 0.0)])
        self.assertEqual(queued[-1][2][1][6], "fairway")
        broken = queued[0][2][0]
        with mock.patch.object(camera.render, "HoleMap", side_effect=RuntimeError):
            camera.Views._work(views, _Once(queued[0][2]))
        self.assertNotIn(broken, views.asked)                 # it can be asked for again

    def test_a_stand_in_while_a_look_is_drawn(self):
        rnd = a_round(1, "tour", 3)
        hole = rnd.holes[0]
        views = camera.Views(Colors("256"))
        whole = views.get(rnd.course, hole, 60, 20, 1.0, "hole", True, wait=True)
        c, k = whole.center(), whole.k / 2
        look = views.get(rnd.course, hole, 60, 20, 1.0, "look", True, wait=True, center=c, scale=k)
        stand_in = views.preview(rnd.course, hole, 60, 20, 1.0, True, c, k)
        self.assertEqual((stand_in.cols, stand_in.rows, stand_in.w, stand_in.h), (60, 20, 120, 40))   # the screen's
        pixels, theirs = stand_in.terrain(render.PALETTE)[0], look.terrain(render.PALETTE)[0]
        same = sum(pixels[y][x] == theirs[int(py)][int(px)] for y in range(stand_in.h) for x in range(stand_in.w)
                   for px, py in [look.to_pixel(stand_in.to_course(x + 0.5, y + 0.5))])
        self.assertGreater(same, 0.97 * stand_in.w * stand_in.h)   # drawn from the look at its own scale
        far = views.preview(rnd.course, hole, 60, 20, 1.0, True, step(c, 90, 2000), k)
        self.assertEqual(set(far.terrain(render.PALETTE)[0][0]), {render.PALETTE["ob"]})   # none has it
        views.close()

    def test_looks_the_camera_has_left_arent_drawn(self):
        rnd = a_round(1, "tour", 3)
        hole = rnd.holes[0]
        views = camera.Views(Colors("256"))
        views.jobs.put((-2, 0, None))                         # hold the worker up, to look in its queue
        views.settle()
        args = (rnd.course, hole, 60, 20, 1.0, True)
        views.look(*args, [((0.0, 0.0), 1.0), ((30.0, 0.0), 1.0)])
        queued = sorted(views.jobs.queue)
        self.assertEqual([job[1][9] for _, _, job in queued], [(0.0, 0.0), (30.0, 0.0)])   # where it is, first
        views.look(*args, [((90.0, 0.0), 1.0)])              # the camera moved on
        left = queued[0][2]
        camera.Views._work(views, _Once(left))
        self.assertNotIn(left[0], views.ready)               # not drawn
        self.assertNotIn(left[0], views.asked)               # (and asked again if it's wanted again)
        camera.Views._work(views, _Once(sorted(views.jobs.queue)[0][2]))
        self.assertEqual(len(views.ready), 1)                # where it is now: drawn

    def test_looking_makes_the_next_zoom_ahead(self):
        rnd = a_round(1, "tour", 3)
        hole = rnd.holes[0]
        views = camera.Views(Colors("256"))
        whole = views.get(rnd.course, hole, 60, 20, 1.0, "hole", True, wait=True)
        look = camera.Look()
        look.start(whole, whole, 1.0)
        look.zoom_by(1, whole, 1.0)
        m = look.view(views, rnd.course, hole, 60, 20, 1.0, True, whole)
        self.assertEqual(len(views.wanted), 2)               # this zoom, and the next one in
        self.assertTrue(look.drawing and m.level == "look" and (m.cols, m.rows) == (60, 20))   # a stand-in meanwhile
        views.settle()
        self.assertFalse(look.view(views, rnd.course, hole, 60, 20, 1.0, True, whole) is m)
        self.assertFalse(look.drawing)
        views.close()

    def test_maps_drawn_in_other_processes_are_the_same(self):
        rnd = a_round(1, "tour", 3)
        hole = rnd.holes[0]
        here, there = camera.Views(Colors("256")), camera.Views(Colors("256"), workers=2)
        for level in ("fairway", "green", "cup"):
            for views in (here, there):
                views.get(rnd.course, hole, 60, 20, 1.0, level, True)
        here.settle()
        there.settle()
        for level in ("fairway", "green", "cup"):
            a, b = (v.get(rnd.course, hole, 60, 20, 1.0, level, True) for v in (here, there))
            self.assertIsNotNone(b, level)
            self.assertEqual(a.lines(Colors("256")), b.lines(Colors("256")), level)
        self.assertIsNotNone(there.pool)                      # (drawn there, not here)
        there.close()
        here.close()

    def test_maps_are_drawn_here_once_the_processes_die(self):
        import signal
        import time
        rnd = a_round(1, "tour", 3)
        hole = rnd.holes[0]
        views = camera.Views(Colors("256"), workers=1)
        views.get(rnd.course, hole, 60, 20, 1.0, "fairway", True)
        views.settle()
        for p in list(views.pool._processes.values()):    # killed while idle (out of memory, say)
            os.kill(p.pid, signal.SIGKILL)
        until = time.monotonic() + 10
        while not getattr(views.pool, "_broken", True) and time.monotonic() < until:
            time.sleep(0.02)
        for _ in range(3):                                   # (each failure's asked again)
            views.get(rnd.course, hole, 60, 20, 1.0, "green", True)
            views.settle()
        self.assertIsNotNone(views.get(rnd.course, hole, 60, 20, 1.0, "green", True))
        self.assertEqual((views.workers, views.pool), (0, None))
        views.close()

    def test_closed_while_the_processes_start_leaves_none(self):
        import threading
        import time
        rnd = a_round(1, "tour", 3)
        hole = rnd.holes[0]
        views = camera.Views(Colors("256"), workers=1)
        real = camera.pickle.dumps

        sending = threading.Event()

        def slow(*a, **k):
            sending.set()
            time.sleep(0.3)                                  # the round ends while the course is sent
            return real(*a, **k)
        with mock.patch.object(camera.pickle, "dumps", side_effect=slow):
            views.get(rnd.course, hole, 60, 20, 1.0, "fairway", True)
            sending.wait(5)
            views.close()
            views.settle()
        self.assertIsNone(views.pool)
        self.assertEqual(views.ready, {})

    def test_the_maps_in_use_are_kept_longest(self):
        rnd = a_round(1, "tour", 3)
        hole = rnd.holes[0]
        views = camera.Views(Colors("256"))
        views.KEEP = 3
        whole = views.get(rnd.course, hole, 40, 12, 1.0, "hole", True, wait=True)
        for i in range(5):                                   # looking around, while the whole hole's still shown
            views.get(rnd.course, hole, 40, 12, 1.0, "look", True, wait=True, center=(i * 9.0, 0.0), scale=1.0)
            self.assertIs(views.get(rnd.course, hole, 40, 12, 1.0, "hole", True), whole)
        views.close()

    def test_an_away_map_has_the_ball_on_it_at_any_scale(self):
        rnd = a_round(1, "tour", 3)
        hole = rnd.holes[0]
        views = camera.Views(Colors("256"))
        rng = random.Random(4)
        for scale in (0.12, 0.5, 2.0):
            for _ in range(6):
                p = (rng.uniform(-300, 300), rng.uniform(-300, 300))
                camera.away_map(views, rnd.course, hole, 60, 20, 1.0, True, p, scale)
                views.settle()
                m = camera.away_map(views, rnd.course, hole, 60, 20, 1.0, True, p, scale)
                self.assertTrue(m.covers(p), (scale, p))
        views.close()


class _Once:
    """A job queue with one job, then the worker's told to stop."""

    def __init__(self, job):
        self.items = [(0, 0, job), (-1, 0, None)]

    def get(self):
        return self.items.pop(0)

    def task_done(self):
        pass


def m_off(ui):
    return ui["map"].off


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.saved = dict(cfg.settings)

    def tearDown(self):
        cfg.settings.clear()
        cfg.settings.update(self.saved)

    def test_holes_and_tees(self):
        c = osm.parse(tiny_course(), "relation", 1)[0]
        cfg.settings.update(holes="all", tees="red")
        holes = screens.round_holes(c)
        self.assertEqual(len(holes), 3)
        self.assertEqual(holes[0].color, "pink")
        self.assertEqual(screens.tee_choice(c)[1], "pink")
        cfg.settings["tees"] = "black"
        self.assertEqual(screens.tee_choice(c)[1], "blue")

    def test_the_screen(self):
        c = osm.parse(tiny_course(), "relation", 1)[0]

        class Keys(FakeTerm):
            def __init__(self, keys):
                super().__init__(100, 30)
                self.keys, self.frames = list(keys), []

            def draw(self, lines):
                self.frames.append(lines)

            def read_key(self, timeout):
                return self.keys.pop(0) if self.keys else "q"

            def invalidate(self):
                pass
        cfg.settings.update(mode="single player", weather="live")
        term = Keys(["down", "down", "right", "enter"])   # past Game and Tees (3 holes: no Holes) to Weather
        self.assertTrue(screens.round_setup(term, c, cfg.OptionsMenu(Colors("256")), Colors("256")))
        self.assertEqual(cfg.settings["weather"], "random")
        text = "\n".join(_SGR.sub("", l) for l in term.frames[0])
        self.assertIn("Tiny Links", text)
        self.assertIn("Your level", text)
        self.assertNotIn("Their level", text)
        self.assertFalse(screens.round_setup(Keys(["b"]), c, cfg.OptionsMenu(Colors("256")), Colors("256")))


class PanelsTest(unittest.TestCase):
    def setUp(self):
        self.hole = osm.parse(tiny_course(), "relation", 1)[0].holes[0]

    def shot(self, **kw):
        base = dict(player=0, number=2, kind="approach", club="8-iron", start=(0, 0), land=(150, 0),
                    end=(152, 0), bend=(80, 0), lie="green", to_pin=6.0, from_pin=158.0)
        base.update(kw)
        return sim.Shot(**base)

    def test_shot_words(self):
        y = "yards"
        self.assertEqual(panels.shot_text(self.shot(), self.hole, y), "8-iron · 152 yds · on the green, 18 ft")
        self.assertIn("IN THE HOLE! Eagle", panels.shot_text(self.shot(holed=True, lie="holed"), self.hole, y))
        sliced = panels.shot_text(self.shot(kind="tee", club="Driver", number=1, miss="slice", lie="trees",
                                            to_pin=170), self.hole, y)
        self.assertEqual(sliced, "Driver · sliced · 152 yds · in the trees · 170 yds to go")
        wet = panels.shot_text(self.shot(lie="water", penalty=1, to_pin=30), self.hole, y)
        self.assertIn("in the water · penalty", wet)
        self.assertIn("139 m", panels.shot_text(self.shot(lie="fairway", to_pin=30), self.hole, "meters"))

    def test_putt_words(self):
        pin = self.hole.pin
        past = self.shot(kind="putt", club="Putter", start=(pin[0] - 5, pin[1]), land=(pin[0] - 5, pin[1]),
                         end=(pin[0] + 1, pin[1]), from_pin=5, to_pin=1)
        self.assertEqual(panels.shot_text(past, self.hole, "yards"), "Putt from 15 ft · 3 ft past")
        short = self.shot(kind="putt", club="Putter", start=(pin[0] - 5, pin[1]), land=(pin[0] - 5, pin[1]),
                          end=(pin[0] - 1, pin[1]), from_pin=5, to_pin=1)
        self.assertEqual(panels.shot_text(short, self.hole, "yards"), "Putt from 15 ft · 3 ft short")

    def test_wind(self):
        w = wx.Weather(wind_mph=12, wind_from=270, gust_mph=20)   # from the west
        self.assertEqual(panels.wind_words(w, 90), "helping")
        self.assertEqual(panels.wind_words(w, 270), "into")
        self.assertEqual(panels.wind_words(w, 0), "left to right")
        self.assertEqual(panels.wind_arrow(w, 90, "across"), "→")
        self.assertEqual(panels.wind_arrow(w, 90, "up"), "↑")
        self.assertEqual(panels.wind_arrow(w, 0, "across"), "↓")
        self.assertIn("gusting 20", panels.weather_text(w, 90, "across", "yards"))
        self.assertIn("No wind", panels.weather_text(wx.Weather(), 0, "across", "yards"))

    def test_scorecard_fits_or_shows_a_nine(self):
        c = osm.parse(tiny_course(), "relation", 1)[0]
        holes = (c.holes * 6)[:18]
        golfers = players.group(2, "tour", random.Random(1))
        cards = [[4] * 10 + [None] * 8, [3] * 10 + [None] * 8]
        wide = panels.scorecard(holes, golfers, cards, 10, Colors("truecolor"), 120)
        self.assertIn("Out", _SGR.sub("", wide[0]))
        self.assertIn("In", _SGR.sub("", wide[0]))
        narrow = panels.scorecard(holes, golfers, cards, 10, Colors("truecolor"), 60)
        self.assertNotIn("Out", _SGR.sub("", narrow[0]))
        self.assertTrue(all(visible_len(r) <= 60 for r in narrow))

    def test_scoring(self):
        self.assertEqual([scoring.name(s, 4) for s in (1, 2, 3, 4, 5, 6, 8)],
                         ["Hole in one", "Eagle", "Birdie", "Par", "Bogey", "Double bogey", "+4"])
        self.assertEqual([scoring.to_par(n) for n in (-3, 0, 2)], ["-3", "E", "+2"])
        self.assertEqual(scoring.standings([2, -1, 2, 0]), [("1", 1), ("2", 3), ("T3", 0), ("T3", 2)])
        self.assertIsNone(scoring.color(4, 4))
        self.assertEqual(scoring.color(9, 4), scoring.WORSE)


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.saved = cfg.CONFIG_PATH, dict(cfg.settings)
        cfg.CONFIG_PATH = os.path.join(self.dir.name, "settings.json")
        cfg.settings.clear()
        cfg.settings.update(cfg.DEFAULTS)

    def tearDown(self):
        cfg.CONFIG_PATH = self.saved[0]
        cfg.settings.clear()
        cfg.settings.update(self.saved[1])
        self.dir.cleanup()

    def test_round_trip(self):
        cfg.settings["golfers"] = 4
        self.assertTrue(cfg.save_settings())
        cfg.settings["golfers"] = 1
        self.assertEqual(cfg.load_settings(), [])
        self.assertEqual(cfg.settings["golfers"], 4)

    def test_bad_values_are_reported_and_the_file_kept(self):
        with open(cfg.CONFIG_PATH, "w") as f:
            json.dump({"golfers": 9, "level": "tour", "weather": True}, f)
        problems = cfg.load_settings()
        self.assertEqual(len(problems), 2)
        self.assertTrue(os.path.exists(cfg.CONFIG_PATH + ".bad"))
        self.assertEqual(cfg.settings["golfers"], cfg.DEFAULTS["golfers"])

    def test_pace_steps_stop_at_the_ends(self):
        for _ in range(5):
            cfg.step_pace(1)
        self.assertEqual(cfg.settings["pace"], "rapid")
        for _ in range(5):
            cfg.step_pace(-1)
        self.assertEqual(cfg.settings["pace"], "slow")

    def test_the_menu_is_what_can_change_any_time(self):
        keys = [k for k, _, _ in cfg.OPTIONS]
        for k in ("mode", "player_level", "golfers", "level", "weather", "holes"):   # the round's: on its setup
            self.assertNotIn(k, keys)
        self.assertEqual(set(cfg.CHOICES), set(cfg.DEFAULTS) - {"tees", "last_course"})
        text = _SGR.sub("", "\n".join(cfg.OptionsMenu(Colors("16")).render(100, 50)))
        self.assertTrue(all(title in text for title, _ in cfg.SECTIONS))

    def test_menu_changes_values(self):
        menu = cfg.OptionsMenu(Colors("16"))
        menu.toggle()
        for _ in range([k for k, _, _ in cfg.OPTIONS].index("pace")):
            menu.handle_key("down")
        menu.handle_key("right")
        self.assertEqual(cfg.settings["pace"], "quick")
        lines = menu.render(80, 24)
        self.assertEqual(len(lines), 24)
        menu.handle_key("esc")
        self.assertFalse(menu.open)


class CatalogTest(unittest.TestCase):
    def test_search_keeps_courses_with_outlines(self):
        replies = [
            [{"type": "golf_course", "osm_type": "relation", "osm_id": 7, "name": "Links",
              "address": {"town": "Seaside", "state": "Fife", "country": "Scotland"}},
             {"type": "golf_course", "osm_type": "node", "osm_id": 8, "name": "A point"},
             {"type": "restaurant", "osm_type": "way", "osm_id": 9, "name": "Lunch"}],
            [{"type": "golf_course", "osm_type": "relation", "osm_id": 7, "name": "Links"}],
        ]
        with mock.patch.object(catalog, "fetch_json", side_effect=replies), \
                mock.patch.object(catalog.time, "sleep"):
            found = catalog.search("links")
        self.assertEqual([(e.name, e.place, e.key) for e in found], [("Links", "Seaside, Fife, Scotland", "relation:7")])

    def test_search_by_place_falls_back_to_the_courses_around_it(self):
        replies = [
            [],
            [{"type": "town", "category": "place", "osm_type": "relation", "osm_id": 1, "name": "Seaside",
              "display_name": "Seaside, Fife", "boundingbox": ["56.3", "56.4", "-2.9", "-2.7"]}],
            {"elements": [{"type": "way", "id": 3, "tags": {"name": "Small"},
                           "bounds": {"minlat": 0, "maxlat": 0.001, "minlon": 0, "maxlon": 0.001}},
                          {"type": "way", "id": 4, "tags": {"name": "Big", "addr:city": "Seaside"},
                           "bounds": {"minlat": 0, "maxlat": 0.01, "minlon": 0, "maxlon": 0.01}}]},
        ]
        with mock.patch.object(catalog, "fetch_json", side_effect=replies), \
                mock.patch.object(catalog.time, "sleep"):
            found = catalog.search("seaside")
        self.assertEqual([e.name for e in found], ["Big", "Small"])

    def test_played_list(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(osm, "COURSE_DIR", d), \
                mock.patch.object(catalog, "INDEX", os.path.join(d, "index.json")):
            a, b = catalog.Entry("A", "", "way", 1), catalog.Entry("B", "", "relation", 2)
            for e in (a, b):
                open(osm.cache_path(e.kind, e.osm_id), "w").close()
            catalog.remember(a)
            catalog.remember(b)
            catalog.remember(a)
            self.assertEqual([e.key for e in catalog.played()], ["way:1", "relation:2"])
            os.remove(osm.cache_path("relation", 2))   # its download is gone: not listed
            self.assertEqual([e.key for e in catalog.played()], ["way:1"])


if __name__ == "__main__":
    unittest.main()
