#!/usr/bin/env python3
"""The README's screenshots, made again: the app's own screens, drawn on a
pretend terminal and saved as PNGs in images/.

    python3 tools/screenshots.py            # all of them
    python3 tools/screenshots.py putting    # one (or a few) by name

Needs Pillow (see ansi2png.py). The courses come from the app's cache, and
are downloaded the first time, as the app would; your settings and played
list aren't read or changed. Each scene is a fixed seed, so a screenshot
changes only when the app does.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import ansi2png  # noqa: E402
from golf import catalog, game, osm, play, players, screens, sim  # noqa: E402
from golf import term as tty  # noqa: E402
from golf import weather as wx  # noqa: E402
from golf.geo import heading, step  # noqa: E402
from golf.settings import DEFAULTS, OptionsMenu, settings  # noqa: E402

OUT = os.path.join(ROOT, "images")
COLS, ROWS = 120, 40
STEP = 1 / 30   # seconds between frames, as the app draws a moving ball
COURSES = {e.name: e for e in catalog.FAMOUS}
NICE_DAY = wx.Weather(temp_f=74, wind_mph=6, wind_from=225, gust_mph=11, code=2, source="random")


class Screen:
    """Stands in for term.Terminal: a size, the frames drawn, keys from a list."""

    def __init__(self, cols=COLS, rows=ROWS, keys=()):
        self.cols, self.rows, self.keys, self.frames = cols, rows, list(keys), []

    def size(self):
        return self.cols, self.rows

    def pixel_aspect(self):
        return ansi2png.CW / (ansi2png.CH / 2)

    def invalidate(self):
        pass

    def draw(self, lines):
        self.frames.append(list(lines))

    def read_key(self, timeout):
        if not self.keys:
            raise _Done
        return self.keys.pop(0)


class _Done(Exception):
    pass


def save(name, lines, cols=COLS, rows=ROWS):
    lines = [tty.clip(tty.translate(line), cols) for line in lines]
    path = os.path.join(OUT, name + ".png")
    ansi2png.render(lines, cols, rows, path)
    print("saved", os.path.relpath(path, ROOT))


def course(name, layout=0):
    e = COURSES[name]
    c = osm.load(e.kind, e.osm_id)[layout]
    c.name = e.name
    return c


def holes(c, tees="white"):
    return [h.played_from(tees) for h in c.holes]


def reset(**changes):
    settings.clear()
    settings.update(DEFAULTS, symbols="full", **changes)
    tty.set_symbols("full")


# --------------------------------------------------------------------------
# Zen: a round played back until the moment wanted

def zen_frame(rnd, colors, hole_i, until, cols=COLS, rows=ROWS, limit=20000):
    """The frame when until(playback) first holds on the round's hole_i-th
    hole, its playback run on frame by frame as the app's is (so the camera
    has followed the ball there), with every map it asks for made."""
    pb = screens.Playback(rnd)
    while pb.hole_i < hole_i:
        pb.jump_hole(1)
    views, ui, term = screens.Views(colors), {}, Screen(cols, rows)
    try:
        for _ in range(limit):
            if pb.phase in ("pause", "summary") and len(rnd.played) <= pb.hole_i + 1 < len(rnd.holes):
                rnd.play(pb.hole_i + 1)
            lines = screens.round_frame(term, pb, colors, views, ui, STEP)
            views.settle()
            if until(pb):
                return screens.round_frame(term, pb, colors, views, ui, STEP)
            pb.tick(STEP, 1.0)
        raise RuntimeError("the moment never came")
    finally:
        views.close()


def in_flight(pb, player, number=None, at=0.6):
    """Golfer `player`'s shot (their number-th on the hole) at `at` of its flight."""
    s = pb.current()
    return pb.phase == "shot" and s.player == player and (number is None or s.number == number) and \
        pb.elapsed >= screens.flight_time(s) * at


def scene_augusta_drives(colors):
    """Augusta's 2nd on the whole-hole camera: the last of three drives down the dogleg."""
    reset(camera="whole hole")
    c = course("Augusta National Golf Club")
    rnd = sim.Round(c, players.group(3, "tour", _rng(11)), NICE_DAY, 11, holes(c))
    order = rnd.play(1).order
    save("augusta-drives", zen_frame(rnd, colors, 1, lambda pb: in_flight(pb, order[-1], 1, at=0.8)))


def scene_green_close_up(colors):
    """Augusta's 1st on the green camera: the first putt rolling, the others marked with how far they've left."""
    reset()
    c = course("Augusta National Golf Club")
    rnd = sim.Round(c, players.group(3, "tour", _rng(11)), NICE_DAY, 11, holes(c))
    shots = rnd.play(0).shots
    first = next(i for i, s in enumerate(shots) if s.kind == "putt")
    save("green-close-up", zen_frame(rnd, colors, 0, lambda pb: pb.phase == "shot" and pb.shown == first and
                                      pb.elapsed >= screens.roll_time(shots[first]) * 0.6))


def scene_sawgrass_17(colors):
    """Weekend golfers on Sawgrass's island 17th, late in the round: the card filled in."""
    reset(layout="auto")
    c = course("TPC Sawgrass")
    hs = holes(c)
    i = next(k for k, h in enumerate(hs) if h.number == 17)
    rnd = sim.Round(c, players.group(3, "weekend", _rng(4)), NICE_DAY, 4, hs)
    for j in range(i + 1):
        rnd.play(j)
    last = rnd.play(i).order[-1]
    save("sawgrass-17", zen_frame(rnd, colors, i, lambda pb: in_flight(pb, last, 1, at=0.85)))


# --------------------------------------------------------------------------
# Single player

def single_frame(g, colors, setup=None, phase="aim", meter_at=None, frames=40):
    """A frame of your round, aiming (setup(game) moves the ball and sets the
    shot up first), with the swing meter that many seconds into its swing."""
    scr = play.Screen(g, colors)
    if setup:
        setup(g)
    scr.go(phase, 0.0)
    term, now = Screen(), 0.0
    try:
        for _ in range(frames):
            play.frame(term, scr, now, STEP)
            scr.views.settle()
            now += STEP
        if meter_at is not None:
            scr.meter = play.Meter(now - meter_at, settings["meter"])
            scr.phase = "meter"
        return play.frame(term, scr, now, STEP)
    finally:
        scr.views.close()


def scene_single_player(colors):
    """Pebble Beach's 7th, the caddie's play shown: where it lands and stops in the wind."""
    reset(help="caddie")
    c = course("Pebble Beach Golf Links")
    hs = holes(c)
    i = next(k for k, h in enumerate(hs) if h.number == 7)
    g = game.Game(c, players.you("scratch"), "scratch", wx.Weather(temp_f=61, wind_mph=14, wind_from=300,
                                                                    gust_mph=21, code=3, source="random"),
                  7, hs, "caddie")
    g.hole_i = i
    g.start_hole()
    save("single-player", single_frame(g, colors, meter_at=0.85))


def scene_putting(colors):
    """A 9-footer on Augusta's 1st, close on the cup, with the green's contours an inch apart."""
    reset(help="yardages")
    c = course("Augusta National Golf Club")
    g = game.Game(c, players.you("scratch"), "scratch", NICE_DAY, 7, holes(c), "yardages")

    def on_the_green(g):
        h = g.hole
        g.ball.pos, g.ball.lie, g.ball.strokes = step(h.pin, heading(h.pin, h.tee) + 25, 3.0), "green", 2
        g.suggest()
        g.aim(1.5)
    save("putting", single_frame(g, colors, on_the_green))


# --------------------------------------------------------------------------
# The course list

def scene_course_list(colors):
    reset(mode="zen")
    played = [COURSES[n] for n in ("TPC Sawgrass", "Augusta National Golf Club", "Pebble Beach Golf Links")]
    real = catalog.played
    catalog.played = lambda: played
    try:
        term = Screen(110, 36, keys=[None])
        try:
            screens.course_list_screen(term, OptionsMenu(colors), colors)
        except _Done:
            pass
        save("course-list", term.frames[-1], 110, 36)
    finally:
        catalog.played = real


def _rng(seed):
    import random
    return random.Random(seed)


SCENES = {name[6:]: fn for name, fn in sorted(globals().items()) if name.startswith("scene_")}


def main(names):
    colors = tty.Colors("truecolor")
    unknown = [n for n in names if n not in SCENES]
    if unknown:
        sys.exit("no scene %s; there are: %s" % (", ".join(unknown), ", ".join(SCENES)))
    for name in names or SCENES:
        SCENES[name](colors)


if __name__ == "__main__":
    main([n.replace("-", "_") for n in sys.argv[1:]])
