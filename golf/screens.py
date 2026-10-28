"""The course list, loading a course, and watching a round."""

import collections
import math
import os
import queue
import random
import threading
import time

from . import catalog, game, osm, panels, players, render, sim
from . import term as tty
from .scoring import name as score_name, standings, to_par
from . import weather as wx
from .geo import bezier, dist, heading, lerp, step
from .course import TEE_DEPTH, TEE_RGB
from .settings import CHOICES, PACES, save_settings, settings, step_pace
from .term import BOLD, DIM, RESET, REVERSE, center, printable, shorten, visible_len

TITLE = (96, 180, 72)
BOX_FG, BOX_BG = (240, 240, 230), (24, 40, 28)
YELLOW, RED = "\x1b[33m", "\x1b[31m"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
FRAME = 1 / 30        # seconds between frames while a ball moves
CREDIT = "Map data © OpenStreetMap contributors · trees: © ESA WorldCover · heights: USGS, Mapzen · weather: Open-Meteo"
CLOSE_KINDS = ("putt", "chip", "pitch", "bunker", "drop", "pickup")
CLOSE_RANGE = 50     # yards from the hole: shots and balls in this close the view in on the green
TRAIL, TRAIL_NOW = 0.4, 0.65  # how strongly a trail tints the ground: past shots, the one in the air


def with_footer(lines, rows, cols, text):
    lines = list(lines)
    while len(lines) < rows - 1:
        lines.append("")
    return lines[:rows - 1] + [center(DIM + text + RESET, cols)]


def scroll_window(body, want, top, room):
    """The part of body that fits in room rows, scrolled from top just enough
    to show row `want`. Returns (rows, new top)."""
    if len(body) <= room:
        return body, 0
    room = max(1, room - 2)   # a row above and below says there's more
    top = max(min(top, want), want - room + 1)
    top = max(0, min(top, len(body) - room))
    more = lambda arrow, n: DIM + (arrow + " more" if n else "") + RESET
    return [more("▲", top)] + body[top:top + room] + [more("▼", len(body) - top - room)], top


class Job:
    """A function run on a daemon thread, so the screen never waits on it.
    result() is None until it's done, then ("ok", value) or ("err", text)."""

    def __init__(self, fn):
        self._q = queue.Queue()
        self._done = None
        threading.Thread(target=self._run, args=(fn,), daemon=True).start()

    def _run(self, fn):
        try:
            self._q.put(("ok", fn()))
        except Exception as e:   # the text can carry a server's words
            self._q.put(("err", printable(str(e)) or type(e).__name__))

    def result(self):
        if self._done is None:
            try:
                self._done = self._q.get_nowait()
            except queue.Empty:
                pass
        return self._done


# --------------------------------------------------------------------------
# The course list

def course_list_screen(term, menu, colors):
    """Return the catalog.Entry to play, or None to quit."""
    played = catalog.played()
    sel_key = settings["last_course"]
    results, searched, search, typing, err = None, "", None, False, None
    sel, top = 0, 0
    while True:
        cols, rows = term.size()
        if menu.open:
            term.draw(menu.render(cols, rows))
            key = term.read_key(0.5)
            if key == "\x0c":
                term.invalidate()
            elif key:
                menu.handle_key(key)
            continue
        if search is not None and search.result():
            status, value = search.result()
            results, err, search = (value, None, None) if status == "ok" else ([], value, None)
            sel, top = 0, 0

        sections = []
        if results is not None:
            sections.append(("Search results for “%s”" % searched, results))
        if played:
            sections.append(("Played before", played))
        sections.append(("Famous courses", [e for e in catalog.FAMOUS if e.key not in {p.key for p in played}]))
        body, items = [], []      # rows; (row index, entry) for each one that can be chosen
        name_w = min(44, max([len(e.name) for _, es in sections for e in es] + [10]))
        for title, entries in sections:
            if body:
                body.append("")
            body.append(BOLD + title + RESET)
            if not entries:
                body.append(DIM + ("  searching…" if search else "  nothing found that's mapped as a course") + RESET)
            for e in entries:
                mark = "✓" if catalog.is_cached(e) else " "
                row = "  %s %-*s  %s" % (mark, name_w, shorten(e.name, name_w), shorten(e.place, 36))
                items.append((len(body), e, row))
                body.append(row)
        if sel_key:   # start on the course played last
            sel = next((i for i, (_, e, _) in enumerate(items) if e.key == sel_key), sel)
            sel_key = None
        sel = max(0, min(sel, len(items) - 1))
        width = max(visible_len(r) for r in body) + 4
        for i, (at, e, row) in enumerate(items):
            if i == sel:
                body[at] = REVERSE + BOLD + panels.pad(row, width - 4) + RESET
        lines = [""] + [center(colors.fg(TITLE) + BOLD + r + RESET, cols) for r in panels.big_text("GOLF")]
        lines += [center(DIM + "Real courses from OpenStreetMap" + RESET, cols), ""]
        modes = [(m, "Single player: you play" if m == "single player" else "Zen: watch a round")
                 for m in ("single player", "zen")]
        lines.append(center("   ".join((REVERSE + BOLD + " %s " % label + RESET) if m == settings["mode"]
                                       else DIM + " %s " % label + RESET for m, label in modes), cols))
        lines.append("")
        if typing:
            lines.append(center("Search for a course: " + BOLD + searched + RESET + "▏", cols))
        elif search:
            lines.append(center(DIM + "Searching OpenStreetMap for “%s”… %s" % (searched, SPINNER[int(time.time() * 10) % 10]) + RESET, cols))
        elif err:
            lines.append(center(RED + "Search failed: %s" % err[:60] + RESET, cols))
        else:
            lines.append(center(DIM + "/ to search any course by name" + RESET, cols))
        lines.append("")
        room = rows - len(lines) - 2
        want = items[sel][0] if items else 0
        window, top = scroll_window(body, want, top, room)
        left = max(0, (cols - width) // 2)
        lines += [" " * left + r for r in window]
        lines = with_footer(lines, rows - 1, cols, "↑/↓ choose   Enter play   Tab mode   / search   o options   q quit")
        lines.append(center(DIM + CREDIT + RESET, cols))
        term.draw(lines)

        key = term.read_key(0.1 if search else 0.5)
        if key is None:
            continue
        if typing:
            if key == "enter":
                typing = False
                if searched.strip():
                    search = Job(lambda q=searched.strip(): catalog.search(q))
                    results, err = None, None
            elif key == "esc":
                typing = False
            elif key in ("\x7f", "\x08"):
                searched = searched[:-1]
            elif len(key) == 1 and key.isprintable() and len(searched) < 60:
                searched += key
            continue
        if key in ("q", "Q", "esc"):
            return None
        if key == "\x0c":
            term.invalidate()
        elif key in ("up", "k"):
            sel -= 1
        elif key in ("down", "j"):
            sel += 1
        elif key in ("pgup", "pgdn"):
            sel += (rows // 2) * (1 if key == "pgdn" else -1)
        elif key in ("home", "end"):
            sel = 0 if key == "home" else len(items) - 1
        elif key in ("/", "s"):
            typing, searched = True, ""
        elif key in ("\t", "m"):
            settings["mode"] = "zen" if settings["mode"] == "single player" else "single player"
            save_settings()
        elif key == "o":
            menu.toggle()
        elif key == "enter" and items:
            return items[sel][1]


# --------------------------------------------------------------------------
# Loading a course

def wait_for(term, job, text, menu):
    """Show a spinner with text (a heading, then lines under it) and the
    seconds waited until job is done. Returns its result, or "back" /
    "quit" if the viewer left first."""
    start = time.monotonic()
    while True:
        r = job.result()
        if r:
            return r
        cols, rows = term.size()
        spin = SPINNER[int(time.time() * 10) % len(SPINNER)]
        waited = int(time.monotonic() - start)
        head = text[0] + " " + spin + ("  %ds" % waited if waited >= 3 else "")
        lines = [""] * (rows // 2 - 2) + [center(BOLD + head + RESET, cols)]
        lines += [center(DIM + t + RESET, cols) for t in text[1:]]
        term.draw(with_footer(lines, rows, cols, "b back   q quit"))
        key = term.read_key(0.1)
        if key in ("q", "Q"):
            return "quit"
        if key in ("b", "esc", "\x7f"):
            return "back"


def message(term, text, sub=""):
    """Show an error until a key: True to retry (r), False to go back, None to quit."""
    while True:
        cols, rows = term.size()
        lines = [""] * (rows // 2 - 2) + [center(RED + BOLD + shorten(text, cols - 4) + RESET, cols)]
        if sub:
            lines.append(center(DIM + shorten(sub, cols - 4) + RESET, cols))
        term.draw(with_footer(lines, rows, cols, "r retry   b back   q quit"))
        key = term.read_key(0.5)
        if key == "r":
            return True
        if key in ("b", "esc", "\x7f", "enter"):
            return False
        if key in ("q", "Q"):
            return None


def choose_layout(term, layouts, units):
    """Pick one of a boundary's courses; None to go back."""
    sel = 0
    while True:
        cols, rows = term.size()
        lines = [""] * max(1, rows // 2 - len(layouts) - 2)
        lines += [center(BOLD + layouts[0].name + RESET, cols), center(DIM + "has more than one course" + RESET, cols), ""]
        for i, c in enumerate(layouts):
            label = c.layout or "Course %d" % (i + 1)
            row = "  %-18s  %2d holes · par %d · %s  " % (label[:18], len(c.holes), c.par,
                                                          panels.yards(c.yards, units))
            lines.append(center(REVERSE + BOLD + row + RESET if i == sel else row, cols))
        term.draw(with_footer(lines, rows, cols, "↑/↓ choose   Enter play   b back"))
        key = term.read_key(0.5)
        if key in ("up", "k"):
            sel = (sel - 1) % len(layouts)
        elif key in ("down", "j"):
            sel = (sel + 1) % len(layouts)
        elif key == "enter":
            return layouts[sel]
        elif key in ("b", "esc", "q"):
            return None


def play_course(term, entry, menu, colors, seed=None):
    """Load a course and play rounds on it. True to go back to the list,
    False to quit."""
    while True:
        cached = catalog.is_cached(entry)
        job = Job(lambda: osm.load(entry.kind, entry.osm_id, satellite=settings["satellite_trees"]))
        text = ["Loading %s" % entry.name, ""] + ([] if cached else [
            "Downloading the course and what's around it from OpenStreetMap:",
            "the first time can take a minute or two. It's kept for next time."])
        if cached and not os.path.exists(osm.heights_path(entry.kind, entry.osm_id)):
            text += ["Getting how high the ground is, for the first time: it's kept for next time."]
        r = wait_for(term, job, text, menu)
        if r in ("back", "quit"):
            return r == "back"
        if r[0] == "ok":
            break
        retry = message(term, "Couldn't load %s" % entry.name, r[1])
        if retry is None:
            return False
        if not retry:
            return True
    layouts = r[1]
    course = layouts[0] if len(layouts) == 1 else choose_layout(term, layouts, settings["units"])
    if course is None:
        return True
    course.name = entry.name   # the list's name for it (OSM's can be a club's or a resort's)
    catalog.remember(catalog.Entry(entry.name, entry.place or course.place, entry.kind, entry.osm_id))
    settings["last_course"] = entry.key
    save_settings()

    while True:
        go = round_setup(term, course, menu, colors)
        if go is None:
            return False
        if not go:
            return True
        rnd = new_round(term, course, menu, seed)
        if rnd is None:
            return True
        seed = None   # the seed asked for is the first round's
        if isinstance(rnd, game.Game):
            from .play import play_round   # (play builds on this module)
            outcome = play_round(term, rnd, colors, menu)
        else:
            outcome = watch_round(term, rnd, colors, menu)
        if outcome != "again":
            return outcome == "back"


def new_round(term, course, menu, seed=None):
    """A round in the game mode chosen: yours (a game.Game), or a new group's
    to watch (a sim.Round); in the weather the options ask for. None to go back."""
    seed = seed if seed is not None else random.randrange(10 ** 9)
    rng = random.Random(seed)
    weather = round_weather(term, course, menu, rng)
    if weather is None:
        return None
    holes = round_holes(course)
    if settings["mode"] == "single player":
        level = settings["player_level"]
        return game.Game(course, players.you(level), level, weather, seed, holes, settings["help"])
    return sim.Round(course, players.group(settings["golfers"], settings["level"], rng), weather, seed, holes)


def round_holes(course):
    """The holes the round's settings say: all, or the front or back nine,
    each played from the tees chosen."""
    holes = course.holes
    if settings["holes"] != "all" and len(holes) > 9:
        front = settings["holes"] == "front 9"
        holes = [h for h in holes if (h.number <= 9) == front] or holes
    return [h.played_from(settings["tees"]) for h in holes]


def tee_choice(course):
    """The course's sets of tees ([(color, yards)]) and the one the settings
    pick (the nearest set to the color asked for), or ([], None)."""
    sets = course.tee_colors()
    if not sets:
        return [], None
    want = TEE_DEPTH.get(settings["tees"], 2)
    pick = settings["tees"] if settings["tees"] in dict(sets) else \
        min(sets, key=lambda s: abs(TEE_DEPTH.get(s[0], 2) - want))[0]
    return sets, pick


SETUP = (
    ("mode", "Game", None),
    ("holes", "Holes", None),
    ("tees", "Tees", None),
    ("weather", "Weather", None),
    ("player_level", "Your level", "single player"),
    ("swing", "Your swing", "single player"),
    ("meter", "Meter timing", "single player"),
    ("help", "Help", "single player"),
    ("golfers", "Golfers in the group", "zen"),
    ("level", "Their level", "zen"),
    ("pace", "Pace between shots", None),
)


def round_setup(term, course, menu, colors):
    """Set the round up: True to tee off, False to go back, None to quit."""
    sel = 0
    while True:
        cols, rows = term.size()
        if menu.open:
            term.draw(menu.render(cols, rows))
            key = term.read_key(0.5)
            if key:
                menu.handle_key(key)
            continue
        items = [(k, label) for k, label, mode in SETUP if mode in (None, settings["mode"])
                 and (k != "holes" or len(course.holes) > 9)]
        sets, tee = tee_choice(course)
        if not sets:
            items = [i for i in items if i[0] != "tees"]
        sel = max(0, min(sel, len(items) - 1))
        holes = round_holes(course)
        units = settings["units"]
        rgb = TEE_RGB.get(tee)
        about = "%d holes · par %d · %s" % (len(holes), sum(h.par for h in holes),
                                           panels.yards(sum(h.yards for h in holes), units))
        if tee:
            about += " from the " + (colors.fg(rgb) + "●" + RESET if rgb else "") + " %s tees" % tee
        lines = [""] * max(1, rows // 2 - len(items) // 2 - 6)
        lines += [center(BOLD + course.name + RESET, cols), center(DIM + (course.layout or "") + RESET, cols),
                  center(about, cols), ""]
        label_w = max(len(label) for _, label in items)
        rows_ = []
        for i, (k, label) in enumerate(items):
            on = REVERSE + BOLD if i == sel else ""
            if k == "tees":
                dot = RESET + colors.fg(rgb) + "●" + RESET + on if rgb else " "
                value = "‹ %s %s · %s ›" % (dot, tee, panels.yards(sum(h.yards for h in holes), units))
            else:
                v = settings[k]
                value = "‹ %s ›" % ("on" if v is True else "off" if v is False else v)
            rows_.append(on + "  %-*s   %s  " % (label_w, label, value))
        width = max(visible_len(r) for r in rows_)
        left = " " * max(0, (cols - width) // 2)
        lines += [left + panels.pad(r, width) + RESET for r in rows_]
        lines += ["", center(DIM + "Enter: tee off" + RESET, cols)]
        term.draw(with_footer(lines, rows, cols, "↑/↓ choose   ←/→ change   Enter tee off   o options   b back   q quit"))
        key = term.read_key(0.5)
        if key in ("q", "Q"):
            return None
        if key in ("b", "esc", "\x7f"):
            return False
        if key == "enter":
            return True
        if key == "o":
            menu.toggle()
        elif key == "\x0c":
            term.invalidate()
        elif key in ("up", "k"):
            sel = (sel - 1) % len(items)
        elif key in ("down", "j"):
            sel = (sel + 1) % len(items)
        elif key in ("left", "right", "h", "l", " "):
            step = -1 if key in ("left", "h") else 1
            k = items[sel][0]
            if k == "tees":
                colors_ = [c for c, _ in sets]
                settings["tees"] = colors_[(colors_.index(tee) + step) % len(colors_)]
            else:
                choices = CHOICES[k]
                i = choices.index(settings[k]) if settings[k] in choices else 0
                settings[k] = choices[(i + step) % len(choices)]
            save_settings()


def round_weather(term, course, menu, rng):
    """The weather the options ask for, None to go back."""
    weather = wx.Weather()
    days = {"random": wx.random_day, "windy": wx.windy_day, "wet": wx.wet_day}
    if settings["weather"] in days:
        weather = days[settings["weather"]](rng)
    elif settings["weather"] == "live" and course.center:
        job = Job(lambda: wx.live(*course.center))
        r = wait_for(term, job, ["Checking the weather at %s" % course.name], menu)
        if r == "back":
            return None
        if r == "quit":
            raise KeyboardInterrupt
        weather = r[1] if r[0] == "ok" else wx.random_day(rng)
    return weather


# --------------------------------------------------------------------------
# Watching a round
#
# Shots play in real time whatever the pace: a drive hangs about five
# seconds in the air, as it would. The pace stretches or shrinks the rest:
# the hole's introduction, each golfer settling over the ball, the pause
# after a shot, and the hole's summary.

INTRO, ADDRESS, SUMMARY = 2.4, 1.3, 4.0   # seconds at the normal pace
HEIGHT = 0.3          # a ball in the air is drawn this much of its height up the screen from its shadow
LIFT_MAX = 3          # rows at most
LEAD = 0.22           # how far ahead of the ball in the air the fairway camera looks, of the screen's length
FOLLOW, SETTLE = 0.12, 0.45   # seconds the camera takes to catch up: with a ball in the air, otherwise
MIN_MAP = 8           # rows the map keeps before the panels under it give way
NOTE = 2.5            # seconds the note on how to leave course only shows


def flight_time(shot):
    if shot.kind in ("putt", "drop", "pickup"):
        return 0.0
    if shot.kind in panels.FULL_SWINGS:
        return 3.0 + shot.flown / 150
    return 0.9 + shot.flown / 25


def roll_time(shot):
    if shot.kind == "putt":
        return min(7.0, 1.2 + dist(shot.start, shot.end) * 3 * 0.09)
    if shot.kind == "drop":
        return 1.0
    run = dist(shot.land, shot.end)
    return min(4.0, 0.5 + run / 8) if run > 0.5 else 0.0


def pause_time(shot):
    t = 1.4
    if shot.holed or shot.penalty:
        t += 1.0
    return t


def apex(shot):
    """Yards up the ball goes at the top of its flight: a full swing's for
    its carry, times how high its kind of shot flies (a chip low, a flop high)."""
    if shot.kind in ("putt", "drop", "pickup"):
        return 0.0
    return (12 + shot.flown * 0.07) * shot.loft


def _carried(t):
    """How far along its flight the ball is a fraction t of the way through
    it: the air slows it down as it goes."""
    return 1 - (1 - t) ** 1.6


def ball_at(shot, e):
    """Where the ball is (over the ground) e seconds into a shot."""
    f, r = flight_time(shot), roll_time(shot)
    e = max(0.0, e)
    if e < f:
        return bezier(shot.start, shot.bend, shot.land, _carried(e / f))
    if r > 0 and e < f + r:
        t = (e - f) / r
        return lerp(shot.land, shot.end, 1 - (1 - t) ** 2)   # slowing as it rolls out
    return shot.end


def height_at(shot, e):
    """Yards up the ball is e seconds into a shot."""
    f = flight_time(shot)
    if not 0 < e < f:
        return 0.0
    t = e / f
    return apex(shot) * 4 * t * (1 - t)


def trail(shot, e, step_yards):
    """Points along a shot's path up to e seconds in, step_yards or so apart."""
    f, r = flight_time(shot), roll_time(shot)
    pts = []
    if f > 0:
        n = max(4, int(shot.flown / max(step_yards, 0.05)) + 1)
        upto = _carried(min(1.0, e / f))
        for k in range(n + 1):
            u = k / n
            if u > upto:
                break
            pts.append(bezier(shot.start, shot.bend, shot.land, u))
    if r > 0 and e > f and shot.kind != "drop":
        run = dist(shot.land, shot.end)
        n = max(2, int(run / max(step_yards, 0.05)) + 1)
        upto = min(1.0, (e - f) / r)
        upto = 1 - (1 - upto) ** 2
        for k in range(n + 1):
            t = k / n
            if t > upto:
                break
            pts.append(lerp(shot.land, shot.end, t))
    return pts


class Playback:
    """Where the viewer is in the round: a hole, how many of its shots have
    been shown, and the phase of the next (the hole's intro, a golfer at
    address, the shot itself, the pause after it, the hole's summary). The
    shot's phase runs in real time, the others at the pace; nothing runs
    while paused."""

    def __init__(self, rnd):
        self.rnd = rnd
        self.hole_i, self.shown = 0, 0
        self.phase, self.at = "intro", 0.0   # the phase, and the (phase) time it began
        self.clock, self.paused = 0.0, False

    @property
    def play(self):
        return self.rnd.play(self.hole_i)

    @property
    def elapsed(self):
        return self.clock - self.at

    def length(self):
        """How long the current phase lasts (at the normal pace)."""
        shots = self.play.shots
        if self.phase == "intro":
            return INTRO
        if self.phase == "address":
            return ADDRESS
        if self.phase == "shot":
            s = shots[self.shown]
            return flight_time(s) + roll_time(s)
        if self.phase == "pause":
            return pause_time(shots[self.shown - 1])
        if self.phase == "summary":
            return SUMMARY
        return float("inf")   # done

    def tick(self, dt, pace=1.0):
        """Run dt seconds on: shots in real time, the rest at pace."""
        if self.paused:
            return
        while dt > 0 and self.phase != "done":
            rate = 1.0 if self.phase == "shot" else pace
            left = (self.length() - self.elapsed) / rate   # seconds until the phase is over
            if dt < left:
                self.clock += dt * rate
                return
            self.clock += left * rate
            dt -= left
            self.next_phase(self.clock)

    def next_phase(self, at=None):
        at = self.clock if at is None else at
        shots = self.play.shots
        if self.phase == "intro":
            self.phase = "address" if shots else "summary"
        elif self.phase == "address":
            self.phase = "shot"
        elif self.phase == "shot":
            self.shown += 1
            self.phase = "pause"
        elif self.phase == "pause":
            self.phase = "address" if self.shown < len(shots) else "summary"
        elif self.phase == "summary":
            if self.hole_i + 1 < len(self.rnd.holes):
                self.hole_i, self.shown, self.phase = self.hole_i + 1, 0, "intro"
            else:
                self.phase = "done"
        self.at = at

    def skip(self):
        """→: on to what's next now (the shot in the air lands)."""
        if self.phase != "done":
            self.next_phase()

    def back(self):
        """←: this shot again if it's well under way, else the shot before
        (into the hole before from a hole's start)."""
        target = self.shown if self.phase == "shot" and self.elapsed > 0.6 else self.shown - 1
        if target < 0:
            if self.hole_i == 0:
                self.shown, self.phase, self.at = 0, "intro", self.clock
                return
            self.hole_i -= 1
            target = len(self.play.shots) - 1
        self.shown, self.phase, self.at = max(0, target), "address", self.clock

    def jump_hole(self, direction):
        """] the next hole's start; [ this hole's start (or the one before's)."""
        if direction > 0:
            if self.hole_i + 1 < len(self.rnd.holes):
                self.hole_i += 1
                self.shown, self.phase, self.at = 0, "intro", self.clock
            else:
                self.shown, self.phase, self.at = len(self.play.shots), "done", self.clock
        else:
            if self.shown == 0 and self.phase in ("intro", "address", "shot") and self.hole_i > 0:
                self.hole_i -= 1
            self.shown, self.phase, self.at = 0, "intro", self.clock

    # What's on screen -----------------------------------------------------

    def scene(self):
        after = self.rnd.holes[self.hole_i + 1] if self.hole_i + 1 < len(self.rnd.holes) else None
        return Scene(self.play.hole, after, self.phase, self.current(), self.elapsed)

    def current(self):
        """The shot about to be played, being played or just played, or None."""
        shots = self.play.shots
        if self.phase in ("address", "shot"):
            return shots[self.shown]
        if self.phase == "pause":
            return shots[self.shown - 1]
        return None

    def done_shots(self):
        """The shots of this hole already shown in full."""
        shots = self.play.shots
        return shots if self.phase in ("summary", "done") else shots[:self.shown]

    def cards(self):
        """Each golfer's strokes per hole, as far as shown (None: not yet)."""
        n = len(self.rnd.golfers)
        cards = [[None] * len(self.rnd.holes) for _ in range(n)]
        for j in range(self.hole_i):
            for i, s in enumerate(self.rnd.play(j).scores):
                cards[i][j] = s
        for s in self.done_shots():
            if s.holed:
                cards[s.player][self.hole_i] = s.number
        return cards


def standing(pb, cards, units):
    """(to par, holes done, what's happening on this hole) per golfer."""
    holes = pb.rnd.holes
    out = []
    last = {}
    for s in pb.done_shots():
        last[s.player] = s
    for i, card in enumerate(cards):
        done = [j for j, s in enumerate(card) if s is not None]
        to_par = sum(card[j] - holes[j].par for j in done)
        s = last.get(i)
        if s is None:
            now = DIM + "on the tee" + RESET if pb.phase not in ("summary", "done") else ""
        elif s.holed:
            now = DIM + "holed out · %d" % s.number + RESET
        else:
            n = s.number + s.penalty
            now = DIM + "%d stroke%s · %s · %s" % (n, "" if n == 1 else "s", panels.LIE_WORDS.get(s.lie, s.lie),
                                                   panels.left(s.to_pin, s.lie, units)) + RESET
        out.append((to_par, len(done), now))
    return out


def before_text(shot, golfers, hole, units, colors, width):
    """The shot about to be played, or in the air, without giving away where it goes."""
    g = golfers[shot.player]
    if shot.kind == "putt":
        what = "Putting from %s" % panels.feet(shot.from_pin, units)
    elif shot.kind == "drop":
        what = "Taking a drop"
    elif shot.kind == "pickup":
        what = "Picking up"
    else:
        name = shot.club if shot.kind in panels.FULL_SWINGS else panels.KIND_NAMES.get(shot.kind, shot.kind)
        what = "%s · %s to go" % (name, panels.yards(shot.from_pin, units))
    head = panels.rgb_fg(colors, g.ball, "● ", True) + BOLD + g.short_name + RESET + "  "
    return head + shorten(what + " …", max(0, width - len(g.short_name) - 4))


def summary_box(pb, units):
    hole = pb.play.hole
    lines = ["Hole %d · Par %d" % (hole.number, hole.par)]
    golfers = pb.rnd.golfers
    w = max(len(g.short_name) for g in golfers)
    for i in sorted(range(len(golfers)), key=lambda i: pb.play.scores[i]):
        s = pb.play.scores[i]
        lines.append("%-*s  %2d  %s" % (w, golfers[i].short_name, s, score_name(s, hole.par)))
    return lines


def final_box(pb, cards):
    holes, golfers = pb.rnd.holes, pb.rnd.golfers
    totals = [sum(s for s in card if s is not None) for card in cards]
    pars = [sum(h.par for h, s in zip(holes, card) if s is not None) for card in cards]
    lines = ["Final · %s" % pb.rnd.course.name, ""]
    w = max(len(g.short_name) for g in golfers)
    for place, i in standings([t - p for t, p in zip(totals, pars)]):
        lines.append("%-3s %-*s  %3s  %d" % (place, w, golfers[i].short_name, to_par(totals[i] - pars[i]), totals[i]))
    lines += ["", "seed %d: golf --seed %d plays it again" % (pb.rnd.seed, pb.rnd.seed), "Enter new round · b courses"]
    return lines


class Views:
    """Each hole's maps at each camera level, made ahead of time on a worker
    thread: the closer ones take a second or two. The whole hole is made at
    once if it's wanted before it's ready, since there's nothing else to show."""
    KEEP = 24   # maps kept, the oldest dropped first

    def __init__(self, colors):
        self.colors = colors
        self.ready = {}                 # key -> HoleMap, oldest first
        self.asked = set()              # keys given to the worker
        self.ways = {}                  # (hole, size) -> how its whole hole is turned
        self.jobs = queue.Queue()
        threading.Thread(target=self._work, daemon=True).start()

    def get(self, course, hole, cols, rows, aspect, level, quad, wait=False):
        """The map, or None while it's being made (unless wait)."""
        way, relief = settings["layout"], settings["relief"]
        if level != "hole":   # turned like the whole hole's (which takes no drawing to know)
            size = (id(hole), cols, rows, round(aspect, 3), quad, way)
            if size not in self.ways:
                self.ways[size] = render.HoleMap(course, hole, cols, rows, aspect, way=way, quad=quad).way
            way = self.ways[size]
        key = (id(hole), cols, rows, round(aspect, 3), level, way, quad, relief)
        m = self.ready.get(key)
        if m is None and wait:
            m = self._make(key, (course, hole, cols, rows, aspect, way, level, quad, relief))
        elif m is None and key not in self.asked:
            self.asked.add(key)
            self.jobs.put((key, (course, hole, cols, rows, aspect, way, level, quad, relief)))
        return m

    def _make(self, key, args):
        course, hole, cols, rows, aspect, way, level, quad, relief = args
        m = render.HoleMap(course, hole, cols, rows, aspect, way=way, level=level, quad=quad, relief=relief)
        m.prepare(self.colors)
        self.ready[key] = m
        while len(self.ready) > self.KEEP:
            old = next(iter(self.ready))
            self.ready.pop(old, None)
            self.asked.discard(old)
        return m

    def _work(self):
        while True:
            key, args = self.jobs.get()
            try:
                if key not in self.ready:
                    self._make(key, args)
            except Exception:   # a map that can't be made: the whole hole stands in for it
                pass
            finally:
                self.jobs.task_done()

    def settle(self):
        """Wait until every map asked for is made (for tests)."""
        self.jobs.join()


# What the director needs to know: the hole (and the one after, to make its
# maps ahead), the phase ("intro", "address", "shot", "pause", "summary",
# "done"), the shot about to be, being or just played, and the seconds into it.
Scene = collections.namedtuple("Scene", "hole after phase shot elapsed")


def direct(scene, views, course, cols, rows, aspect, quad, ui, dt):
    """The map to show and where its camera points, as a TV director would:
    the whole hole to open and close it; for a full shot, the fairway camera
    cut to the golfer at address, chasing the ball through the air with room
    ahead of it, and settling where it stops; the green camera for the short
    game, between the ball and the hole. ui keeps the camera from frame to
    frame."""
    hole, phase, shot, elapsed = scene.hole, scene.phase, scene.shot, scene.elapsed
    levels = ["hole"] + (["fairway"] if settings["camera"] == "broadcast" else []) + \
        (["green"] if settings["green_view"] else [])   # the ones in use
    whole = views.get(course, hole, cols, rows, aspect, "hole", quad, wait=True)
    fairway = views.get(course, hole, cols, rows, aspect, "fairway", quad) if "fairway" in levels else None
    green = views.get(course, hole, cols, rows, aspect, "green", quad) if "green" in levels else None
    if phase in ("pause", "summary") and scene.after is not None:
        for level in levels:   # the next hole's, while nothing moves
            views.get(course, scene.after, cols, rows, aspect, level, quad)
    m = whole
    if shot is not None:
        short = settings["green_view"] and shot.kind in CLOSE_KINDS and dist(shot.start, hole.pin) <= CLOSE_RANGE
        if short and green is not None and green.covers(shot.start) and green.covers(shot.end):
            m = green
        elif fairway is not None:
            m = fairway
    if m.level == "hole":
        ui.update(map=m, shot=None)
        return m
    if phase == "shot":
        ball = ball_at(shot, elapsed)
    else:
        ball = shot.start if phase == "address" else shot.end
    flying = phase == "shot" and elapsed < flight_time(shot)
    if m.level == "green":
        target = lerp(ball, hole.pin, 0.5)
    else:
        span = m.cols * m.px * m.kx if m.way == "across" else m.rows * 2 * m.k
        if phase == "address":        # looking down the hole from behind the ball
            target = step(ball, heading(ball, hole.pin), LEAD * span)
        elif flying:
            target = step(ball, heading(shot.start, shot.land), LEAD * span)
        else:
            target = ball
    if ui.get("map") is not m or ui.get("shot") is not shot:
        cam = target   # a cut: a new golfer, or another camera
    else:
        cam = lerp(ui["cam"], target, 1 - math.exp(-dt / (FOLLOW if flying else SETTLE)))
    ui.update(map=m, shot=shot, cam=cam)
    m.aim(cam)
    return m


def watch_round(term, rnd, colors, menu):
    """Play a round back. Returns "again" (a new round), "back" or "quit"."""
    pb = Playback(rnd)
    views, ui = Views(colors), {}
    last = time.monotonic()
    while True:
        now = time.monotonic()
        dt, last = min(0.5, now - last), now
        if menu.open:
            cols, rows = term.size()
            term.draw(menu.render(cols, rows))
            key = term.read_key(0.25)
            if key == "\x0c":
                term.invalidate()
            elif key:
                menu.handle_key(key)
            continue
        pb.tick(dt, PACES.get(settings["pace"], 1.0))
        if pb.phase in ("pause", "summary") and len(rnd.played) <= pb.hole_i + 1 < len(rnd.holes):
            rnd.play(pb.hole_i + 1)   # simulated while nothing moves, not as the next hole starts
        term.draw(round_frame(term, pb, colors, views, ui, dt))
        moving = pb.phase == "shot" and not pb.paused
        key = term.read_key(FRAME if moving or ui.get("cam_moving") else 0.1)
        if key is None:
            continue
        if key in ("q", "Q"):
            return "quit"
        if key in ("b", "esc", "\x7f"):
            return "back"
        if key == "\x0c":
            term.invalidate()
        elif key == " ":
            pb.paused = not pb.paused
        elif key in ("right", "n"):
            pb.skip()
        elif key in ("left", "p"):
            pb.back()
        elif key in ("]", "pgdn"):
            pb.jump_hole(1)
        elif key in ("[", "pgup"):
            pb.jump_hole(-1)
        elif key in ("+", "=", "-", "_"):
            step_pace(1 if key in ("+", "=") else -1)
            save_settings()
        elif key in ("z", "g", "t", "c"):
            if key == "c":
                settings["camera"] = "whole hole" if settings["camera"] == "broadcast" else "broadcast"
            else:
                k = {"z": "course_only", "g": "green_view", "t": "trails"}[key]
                settings[k] = not settings[k]
                if k == "course_only" and settings["course_only"]:
                    ui["note"] = now + NOTE
            save_settings()
        elif key == "o":
            menu.toggle()
        elif key == "enter" and pb.phase == "done":
            return "again"


def high_detail():
    """Whether maps use quadrant blocks (four pixels to a cell)."""
    if settings["detail"] == "auto":
        return tty.symbols == "full"
    return settings["detail"] == "high"


def round_frame(term, pb, colors, views, ui=None, dt=0.0):
    """One frame of the round view."""
    ui = {} if ui is None else ui
    cols, rows = term.size()
    rnd, units = pb.rnd, settings["units"]
    hp = pb.play
    hole, golfers = hp.hole, rnd.golfers
    cards = pb.cards()
    current = pb.current()
    course_only = settings["course_only"]
    show = lambda key: settings[key] and not course_only
    side = not course_only and settings["side_panel"] == "auto" and cols >= 130 and (show("show_group") or show("show_shot"))
    side_w = 46 if side else 0
    map_w = cols - side_w - (1 if side else 0)
    top = 2 if show("show_header") else 0
    bottom = 1 if show("show_keys") else 0
    playing = current.player if current and pb.phase in ("address", "shot") else None
    group = panels.group_rows(golfers, standing(pb, cards, units), colors, side_w if side else cols - 2, playing,
                              two_lines=side) if show("show_group") else []
    # Under the map: what the shot did, then (unless they're beside it) the
    # group, then the scorecard. The map keeps MIN_MAP rows: the scorecard
    # gives way first.
    blocks = []
    if show("show_shot"):
        blocks.append(("shot", 1))
    if group and not side:
        blocks.append(("group", len(group) + 1))
    card = []
    if not course_only and settings["scorecard"] != "off":
        card = panels.scorecard(rnd.holes, golfers, cards, pb.hole_i, colors, cols,
                                nine=settings["scorecard"] == "nine")
        blocks.append(("card", len(card) + 1))
    budget = rows - top - bottom
    while blocks and budget - sum(n for _, n in blocks) < MIN_MAP:
        blocks.pop()
    map_h = max(3, budget - sum(n for _, n in blocks))

    quad = high_detail()
    m = direct(pb.scene(), views, rnd.course, map_w, map_h, term.pixel_aspect(), quad, ui, dt)
    ui["cam_moving"] = m.level != "hole" and pb.phase in ("address", "pause")
    close = m.level == "green"

    # The map's marks: trails, the flag, balls at rest, the ball in the air.
    done = pb.done_shots()
    rest = {}
    for s in done:
        rest[s.player] = s
    marks, labels, tints = [], [], []
    step_yards = m.pixel_yards() * 0.5   # half a pixel: an unbroken line of them
    if settings["trails"]:
        for s in done:
            if s.kind not in ("drop", "pickup"):
                tints += [(p, golfers[s.player].ball, TRAIL) for p in trail(s, 1e9, step_yards)]
    moving = None
    if pb.phase == "shot":
        moving = ball_at(current, pb.elapsed)
        if current.kind not in ("drop", "pickup"):
            tints += [(p, golfers[current.player].ball, TRAIL_NOW) for p in trail(current, pb.elapsed, step_yards)]
    marks.append((hole.pin, "▶", render.FLAG, True))
    for i, s in rest.items():
        if not s.holed and not (pb.phase == "shot" and current.player == i):
            marks.append((s.end, "●", golfers[i].ball, True))
            if close and s.lie == "green":
                labels.append((s.end, panels.feet(s.to_pin, units), golfers[i].ball))
    if current is not None and m.level == "fairway" and pb.phase in ("address", "pause") and not current.holed:
        # The broadcast's yardage, by the ball.
        at, left = (current.start, current.from_pin) if pb.phase == "address" else (current.end, current.to_pin)
        lie = "green" if dist(at, hole.pin) < 1 or (pb.phase == "pause" and current.lie == "green") else ""
        labels.append((at, panels.left(left, lie, units), golfers[current.player].ball))
    if moving is not None:
        lift = height_at(current, pb.elapsed)
        if lift > 0.5:
            tints.append((moving, (0, 0, 0), 0.55))   # its shadow on the ground
        rows_up = min(LIFT_MAX, round(lift * HEIGHT / m.k / 2))
        marks.append((moving, "●", golfers[current.player].ball, True, rows_up))
    box = None
    if pb.phase == "intro":
        box = ([panels.hole_title(rnd.course, hole, units)], BOX_FG, BOX_BG)
    elif pb.phase == "summary":
        box = (summary_box(pb, units), BOX_FG, BOX_BG)
    elif pb.phase == "done":
        box = (final_box(pb, cards), BOX_FG, BOX_BG)
    elif course_only and pb.paused:
        box = (["Paused · space to play"], BOX_FG, BOX_BG)
    elif course_only and time.monotonic() < ui.get("note", 0):
        box = (["Course only · z for the rest"], BOX_FG, BOX_BG)
    map_lines = m.lines(colors, marks, labels, box, tints)
    if course_only:
        return map_lines

    # Put it together.
    lines = []
    if top:
        paused = "   PAUSED" if pb.paused else ""
        title = "%s  ·  %s" % (rnd.course.name, panels.hole_title(rnd.course, hole, units))
        if len(title) + len(paused) > cols:
            title = panels.hole_title(rnd.course, hole, units)
        title = BOLD + shorten(title, cols - len(paused)) + RESET + (YELLOW + BOLD + paused + RESET if paused else "")
        camera = {"hole": "", "fairway": "   ·   fairway camera", "green": "   ·   green camera"}[m.level]
        weather = panels.weather_text(rnd.weather, heading(hole.tee, hole.pin), m.way, units) + \
            "   ·   %s pace%s" % (settings["pace"], camera)
        lines += [center(title, cols), center(DIM + shorten(weather, cols) + RESET, cols)]
    if side:
        panel = group + ([""] if group else [])
        if show("show_shot"):
            panel.append(BOLD + "This hole" + RESET)
            log = [panels.shot_line(s, golfers, hole, units, colors, side_w - 2) for s in done]
            room = map_h - len(panel)
            panel += log[-room:] if room > 0 else []
        for r in range(map_h):
            lines.append(map_lines[r] + " " + (panel[r] if r < len(panel) else ""))
    else:
        pad = max(0, (cols - map_w) // 2)
        lines += [" " * pad + l for l in map_lines]
    for name, _ in blocks:
        if name == "shot":
            if current is None:
                text = DIM + shorten("Hole %d done" % hole.number if pb.phase == "summary" else
                                     "Final" if pb.phase == "done" else
                                     "On the tee: %s" % ", ".join(golfers[i].short_name for i in hp.order),
                                     cols - 2) + RESET
            elif pb.phase == "address" or (pb.phase == "shot" and
                                           pb.elapsed < flight_time(current) + roll_time(current) * 0.6):
                text = before_text(current, golfers, hole, units, colors, cols - 2)
            else:
                text = panels.shot_line(current, golfers, hole, units, colors, cols - 2)
            lines.append(" " + text)
        else:
            lines.append("")
            lines += [" " + r for r in (group if name == "group" else card)]
    if not bottom:
        return lines[:rows]
    keys = "space pause  ←/→ shots  [ ] holes  +/- pace  c camera  z course only  t trails  o options  b back  q quit"
    if cols < len(keys) + 2:
        keys = "space pause  ←/→ shot  [ ] hole  +/- pace  z course only  o options  b back  q quit"
    if cols < len(keys) + 2:
        keys = "space ←/→ [ ] +/- z o b q"
    if pb.phase == "done":
        keys = "Enter new round  ←/[ replay  o options  b back  q quit"
        if cols < len(keys) + 2:
            keys = "Enter again  b back  q quit"
    return with_footer(lines, rows, cols, keys)
