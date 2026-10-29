"""Single player: your round on the screen, a shot at a time.

Aim the shot, choose the club, the kind of shot and how hard, and watch the
line on the map: how much it tells you is the help setting's (game.HELP).
With the caddie, a dotted path to where it'd land and stop, struck as meant
in the wind as it usually blows; with yardages, only where it'd come down in
still air; with none, just the line. Then swing, with the swing meter (a press starts the backswing, the
next sets the power where the meter is, the last strikes it: early hooks
and pulls it, late slices and pushes it) or, simply, at the power set. The
ball flies on the broadcast camera, as in zen.
"""

import math
import time

from . import engine, panels
from .engine import OVERSWING
from .geo import dist, heading, lerp, step
from .scoring import name as score_name, to_par
from .camera import (CLOSE_RANGE, CUP_RANGE, HEIGHT, LIFT_MAX, LOOK_KEYS, TRAIL, TRAIL_NOW, Look, Scene, Views, away_map,
                     ball_at, direct, flight_time, height_at, high_detail, look_key, pause_time, roll_time, trail,
                     trail_tints)
from .render import FLAG
from .settings import PACES, save_settings, settings
from .term import BOLD, DIM, RESET, center, shorten
from .ui import BOX_BG, BOX_FG, FRAME, MIN_MAP, fit_keys, help_screen, with_footer

AIM, FINE = 1.0, 0.2          # degrees a press turns the line (half the first on and round the green)
POWER_STEP = 0.05
FOOT = 1 / 3                  # yards
RISE_SHOWN = FOOT             # how far the hole must be above or below the ball for the setup to say so
BREAK_SHOWN = 0.004           # how steep a slope across a putt's line must be (0.4%) to be read as a break
AIM_COLOR = (255, 236, 120)
INTRO, SUMMARY = 2.4, 8.0     # seconds (the summary: unless a key moves on sooner)
SETTLE = 0.6                  # seconds a new shot's setup ignores space: a press meant to skip what came before
INFO_ROWS = 5                 # under the map: the ball, the setup, where it'd go, the swing, a gap
# The swing meter, in the units of power: up from 0 at RISE a second, to
# OVERSWING at most; then down through the strike at 0 (a perfect one), at
# the meter timing setting's fall a second. Its window either side of 0 is
# as early or late as it gets (at normal, a fifth of a second either way),
# and a strike within SWEET of the window of 0 is pure. A window and a
# quarter past 0, it's struck late whether pressed or not.
RISE = 1.0
TIMINGS = {"easy": (0.75, 0.3), "normal": (1.0, 0.2), "hard": (1.5, 0.12)}   # (fall a second, window)
SWEET = 0.15
LATE_PAST = 1.25
DOWN = 0.5                    # seconds at least from setting the power to the strike mark
TAP = 0.08                    # seconds: presses closer together than this are a held key repeating
LEAVE = 3.0                   # seconds to press b (or q) again to leave a round in play


class Meter:
    """The three-press swing meter, run on the clock; timing: a TIMINGS key."""

    def __init__(self, now, timing="normal"):
        self.t0, self.power, self.locked, self.top = now, None, None, None
        self.fall, self.window = TIMINGS.get(timing, TIMINGS["normal"])
        self.late = -self.window * LATE_PAST

    def value(self, now):
        if self.power is None:
            return min(OVERSWING, (now - self.t0) * RISE)
        return self.top - (now - self.locked) * self.fall

    def lock(self, power, now):
        """Set the power: the meter comes down from it (from higher for a soft
        one, so there's always DOWN seconds to see the mark coming)."""
        self.power, self.locked, self.top = power, now, max(power, DOWN * self.fall)

    def press(self, now):
        """Set the power (the first press after starting), or strike: (power, timing)."""
        if self.power is None:
            self.lock(self.value(now), now)
            return None
        timing = max(-1.0, min(1.0, -self.value(now) / self.window))
        return self.power, 0.0 if abs(timing) < SWEET else timing

    def tick(self, now):
        """(power, timing) if the meter's run out without a press, else None."""
        if self.power is None and (now - self.t0) * RISE >= OVERSWING:
            self.lock(OVERSWING, self.t0 + OVERSWING / RISE)   # all the way back
        if self.power is not None and self.value(now) <= self.late:
            return self.power, 1.0
        return None


def meter_bar(meter, target, now, width, colors, timing="normal"):
    """The meter as a bar from where it's struck late to OVERSWING: the
    strike mark (|) in its window (green, brightest where it's pure), the
    power set up (▼), the backswing so far (filled) and, coming down, where
    the meter is (the bright cell)."""
    m = meter or Meter(now, timing)
    low = m.late
    col = lambda v: max(0, min(width - 1, round((v - low) / (OVERSWING - low) * (width - 1))))
    value = meter.value(now) if meter else None
    cells = []
    for i in range(width):
        v = low + (OVERSWING - low) * i / (width - 1)
        rgb = (230, 80, 70) if v > 1.0 else (240, 210, 90) if v >= m.window else (140, 200, 255) if v < -m.window \
            else (120, 255, 140) if abs(v) < m.window * SWEET else (70, 190, 100)
        if meter is None:
            ch = "·"
        elif meter.power is None:
            ch = "█" if 0 <= v <= value else "▒" if -m.window <= v < 0 else "·"
        else:
            ch = "▒" if -m.window <= v <= meter.power else "·"
        cells.append(colors.fg(rgb) + ch + RESET)
    cells[col(0)] = BOLD + colors.fg((120, 255, 140)) + "|" + RESET
    cells[col(min(target, 1.0))] = BOLD + colors.fg((255, 255, 255)) + "▼" + RESET
    if meter is not None and meter.power is not None:
        cells[col(value)] = BOLD + colors.fg((255, 255, 255)) + "█" + RESET
    return "".join(cells)


def timing_words(timing):
    """How a swing on the meter was struck: "pure", "a touch late"..."""
    if timing is None:
        return ""
    if timing == 0:
        return "pure"
    side = "early" if timing < 0 else "late"
    return ("a touch " + side) if abs(timing) < 0.5 else side if abs(timing) < 1 else "way " + side


def on_meter(game):
    """Whether this shot is played on the swing meter: every shot with the
    meter, putts too unless the caddie's setting them up."""
    if settings["swing"] != "meter":
        return False
    return game.setup.club != "P" or game.help != "caddie"


def describe_aim(game):
    """"2.0° left of the flag"."""
    word = "hole" if game.setup.club == "P" else "flag"
    off = game.off_flag()
    if abs(off) < 0.1:
        return "at the " + word
    return "%.1f° %s of the %s" % (abs(off), "right" if off > 0 else "left", word)


def aim_shot(game):
    """The shot as set up, as far as the help says (an engine.Shot, not
    played): with "caddie", struck as meant in the wind as it usually blows,
    flying and rolling out; with "yardages", where it'd come down in still
    air (its roll, the wind and the trouble left to you); with "none", None."""
    if game.help == "caddie":
        return game.planned()
    if game.help == "yardages":
        return game.still_air()
    return None


def aim_points(game):
    """(where it lands, where it stops, whether it drops) of aim_shot; Nones with no help."""
    shot = aim_shot(game)
    return (shot.land, shot.end, shot.holed) if shot else (None, None, False)


def read_words(rise, across, units):
    """How a putt looks: "2 in uphill · breaks right to left, 1.5%"."""
    if abs(rise) < 1 / 36:
        slope = "level"
    elif units == "meters":
        slope = "%d cm %s" % (round(abs(rise) * 91.44), "uphill" if rise > 0 else "downhill")
    else:
        inches = abs(rise) * 36
        size = "%d in" % round(inches) if inches < 12 else panels.feet(abs(rise), units)
        slope = "%s %s" % (size, "uphill" if rise > 0 else "downhill")
    if abs(across) < BREAK_SHOWN:
        return slope + " · straight"
    return "%s · breaks %s, %.1f%%" % (slope, "right to left" if across > 0 else "left to right", abs(across) * 100)


def setup_lines(game, land, end, holed, units, width):
    """The ball, the shot as set up, and (as far as the help goes) where it'd go."""
    s, ball = game.setup, game.ball
    head = "Stroke %d · %s · %s to the %s" % (ball.strokes + 1, panels.LIE_WORDS.get(ball.lie, ball.lie),
                                             panels.left(game.to_pin, ball.lie, units),
                                             "hole" if ball.lie == "green" else "flag")
    rise = game.rise()
    if game.help != "none" and abs(rise) >= RISE_SHOWN and not (s.club == "P" and game.help == "yardages"):
        head += " · %s %s" % (panels.feet(abs(rise), units), "uphill" if rise > 0 else "downhill")
    if s.club == "P":
        setup = "Putter   Line ←→ %s   Length ↑↓ %s" % (describe_aim(game), panels.feet(s.length, units))
    else:
        club = engine.club(s.club)
        setup = "Club ↑↓ %s (%s carry)   Shot s %s   Power -/+ %d%%   Aim ←→ %s" % (
            club.name, panels.yards(game.player.carry(engine.Swing(club, 0, "full"), ball.lie), units),
            engine.BY_TYPE[s.kind].name, round(s.power * 100), describe_aim(game))
    if game.help == "caddie":
        surface = game.player.surface(end)
        left = dist(end, game.hole.pin)
        if s.club == "P":
            ahead = math.cos(math.radians(game.off_flag())) * dist(ball.pos, end) - game.to_pin
            where = "drops" if holed else "finishes %s %s" % (panels.feet(left, units), "past" if ahead > 0 else "short")
            if surface != "green":
                where += " · " + panels.LIE_WORDS.get(surface, surface)
        else:
            where = "carries %s · %s" % (panels.yards(dist(ball.pos, land), units), "in the hole!" if holed else
                                          "stops %s from the flag · %s" % (panels.left(left, surface, units),
                                                                           panels.LIE_WORDS.get(surface, surface)))
        cad = game.suggested
        note = "Struck as meant: %s   ·   caddie (r): %s%s" % (
            where, engine.club(cad.club).name, "" if cad.kind in ("full", "putt") else
            ", " + engine.BY_TYPE[cad.kind].name.lower())
    elif s.club == "P":
        rolls = game.flat_roll(s.length)   # (less than its length over the fringe)
        past = rolls - game.to_pin
        note = "Rolls %s on the flat (%s %s the hole)" % (panels.feet(rolls, units), panels.feet(abs(past), units),
                                                          "past" if past >= 0 else "short of")
        if game.help == "yardages":   # what you'd see crouched behind it
            note += " · " + read_words(game.rise(), game.break_across(), units)
    else:
        note = "This swing carries %s in still air: the wind, the bounce and the roll are yours to judge" % (
            panels.yards(game.carry(), units))
    if game.help == "yardages":
        note += "   ·   r: ask the caddie"
    return [shorten(head, width), BOLD + shorten(setup, width) + RESET, DIM + shorten(note, width) + RESET]


def aim_marks(game, m, shot):
    """The shot as set up on the map, dotted: as far as the help goes
    (aim_shot), its flight curving as it will and its roll, with a cross where
    it comes down (where it stops, for a putt; none where it drops). With no
    help, the line alone, as long as the shot to the flag."""
    ball = game.ball.pos
    if shot is None:
        path = [ball, step(ball, game.setup.bearing, game.to_pin)]
        n = int(game.to_pin / max(m.pixel_yards(), 0.05))
        dots = [lerp(path[0], path[1], k / max(n, 1)) for k in range(0, n + 1)]
    else:
        dots = trail(shot, 1e9, m.pixel_yards())
    tints = [(p, AIM_COLOR, 0.75) for p in dots[::2]]   # every other pixel: dotted
    if shot is None:
        return tints, []
    cross = shot.end if game.setup.club == "P" else shot.land
    return tints, [] if shot.holed and cross is shot.end else [(cross, "×", AIM_COLOR, True)]


def summary_box(game):
    hole, strokes = game.hole, game.cards[game.hole_i]
    st = game.stats[game.hole_i]
    bits = ["%d putt%s" % (st.putts, "" if st.putts == 1 else "s")]
    if st.fairway is not None:
        bits.append("fairway" if st.fairway else "missed the fairway")
    bits.append("green in regulation" if st.green else "missed the green")
    if st.penalties:
        bits.append("%d penalt%s" % (st.penalties, "y" if st.penalties == 1 else "ies"))
    _, _, diff = game.totals()
    played = sum(1 for c in game.cards if c is not None)
    return ["Hole %d · Par %d" % (hole.number, hole.par), "", "%s · %d" % (score_name(strokes, hole.par), strokes),
            " · ".join(bits), "", "%s through %d" % (to_par(diff), played),
            "Enter: the card" if game.over else "Enter: next hole"]


def final_box(game):
    strokes, _, diff = game.totals()
    fairways = [s.fairway for s in game.stats if s.fairway is not None]
    return ["Final · %s" % game.course.name, "", "%d · %s" % (strokes, to_par(diff)), "",
            "Fairways %d/%d · Greens %d/%d · Putts %d" % (sum(fairways), len(fairways),
                                                           sum(s.green for s in game.stats), len(game.stats),
                                                           sum(s.putts for s in game.stats)),
            "", "seed %d: golf --seed %d for the same day's luck" % (game.seed, game.seed),
            "Enter new round · b courses"]


class Screen:
    """The round on screen: the phase ("intro", "aim", "meter", "flight",
    "result", "summary", "done") and its clock, the swing meter, the shots
    of the last swing in the air, and the cameras."""

    def __init__(self, game, colors):
        self.game, self.colors = game, colors
        self.views, self.ui = Views(colors), {}
        self.phase, self.at = "intro", time.monotonic()
        self.meter = None
        self.timing = None                  # how the last swing on the meter was struck
        self.flying, self.fly_i = [], 0
        self.look = "hole"                  # the camera to aim on: "hole", "target", "ball" (v)
        self.last_go = -1e9                 # when space, Enter or → was last pressed
        self.leaving = None                 # ("back" or "quit", when): asked once, to leave the round

    def go(self, phase, now):
        self.phase, self.at = phase, now

    def tick(self, now, pace):
        g = self.game
        if self.phase == "intro" and now - self.at >= INTRO / pace:
            self.go("aim", now)
        elif self.phase == "meter":
            struck = self.meter.tick(now)
            if struck:
                self.strike(now, *struck)
        elif self.phase == "flight":
            shot = self.flying[self.fly_i]
            if now - self.at >= flight_time(shot) + roll_time(shot):
                if self.fly_i + 1 < len(self.flying):
                    self.fly_i, self.at = self.fly_i + 1, now
                else:
                    self.go("result", now)
        elif self.phase == "result" and now - self.at >= pause_time(self.flying[-1]) / pace:
            self.go("summary" if g.ball.holed else "aim", now)
        elif self.phase == "summary" and now - self.at >= SUMMARY / pace:
            self.advance(now)

    def strike(self, now, power=None, timing=None):
        self.flying, self.fly_i = self.game.hit(power, timing), 0
        self.timing = timing
        self.meter = None
        self.go("flight", now)

    def advance(self, now):
        """On from a hole's summary: the next hole, or the card."""
        if self.game.over:
            self.go("done", now)
        else:
            self.game.next_hole()
            self.go("intro", now)

    def scene(self, now):
        g = self.game
        after = g.holes[g.hole_i + 1] if g.hole_i + 1 < len(g.holes) else None
        if self.phase == "flight":
            return Scene(g.hole, after, "shot", self.flying[self.fly_i], now - self.at)
        if self.phase == "result":
            return Scene(g.hole, after, "pause", self.flying[-1], now - self.at)
        return Scene(g.hole, after, "summary" if self.phase in ("summary", "done") else "intro", None, 0.0)

    def aim_view(self, args, land, end, dt):
        """The map to aim on: close up on the green for the short game, if it
        shows the ball, the hole and where the shot goes all at once; else the
        whole hole, or the fairway camera on the target (with the ball too, if
        they fit) or ahead of the ball (v). Looking around (l), the look's."""
        g, views, ui = self.game, self.views, self.ui
        course, cols, rows, aspect, quad = args
        whole = views.get(course, g.hole, cols, rows, aspect, "hole", quad, wait=True)
        ui["whole"] = whole
        look = ui.setdefault("look", Look())
        if look.on:
            m = look.view(views, course, g.hole, cols, rows, aspect, quad, whole)
            if m is not None:
                ui.update(map=m, shot=None)
                return m
        m, target = whole, None
        keep = [g.ball.pos, land, end, g.hole.pin]
        if settings["green_view"] and g.to_pin <= CLOSE_RANGE:
            close = [views.get(course, g.hole, cols, rows, aspect, "green", quad)]
            if g.setup.club == "P" or g.ball.lie == "green":   # (the cup's made ahead once on the green)
                cup = views.get(course, g.hole, cols, rows, aspect, "cup", quad)
                if g.setup.club == "P" and g.to_pin <= CUP_RANGE:
                    close.insert(0, cup)
            for near in close:
                if near is not None and near.fits(keep):
                    m, target = near, near.middle(keep)
                    break
        if m is whole and not whole.covers(g.ball.pos):   # a ball off the hole's map: one round it
            away = away_map(views, course, g.hole, cols, rows, aspect, quad, g.ball.pos, whole.k)
            if away is not None:   # the ball, and where it's going if that fits too
                both = [g.ball.pos, land]
                m, target = away, away.middle(both) if away.fits(both) else g.ball.pos
        if m is whole and self.look != "hole":
            fairway = views.get(course, g.hole, cols, rows, aspect, "fairway", quad)
            if fairway is not None:
                m = fairway
                if self.look == "ball":
                    target = step(g.ball.pos, g.setup.bearing, 40)
                else:
                    both = [g.ball.pos, land, end]
                    target = fairway.middle(both) if fairway.fits(both, margin=3) else land
        if target is None:
            ui.update(map=m, shot=None)
            return m
        if ui.get("map") is not m or ui.get("shot") != "aim":
            cam = target
        else:
            cam = lerp(ui["cam"], target, 1 - math.exp(-dt / 0.25))
        ui.update(map=m, shot="aim", cam=cam)
        m.aim(cam)
        return m


HELP = (
    ("Aiming", (("←  →", "aim (a putt: its line)"), (",  .", "aim finely"),
                ("↑  ↓", "the club (a putt: its length, a foot at a time)"),
                ("-  +", "how hard (a putt: its length, five feet at a time)"), ("s  S", "the kind of shot (S: back through them)"),
                ("c", "off the putter, for a chip"), ("f", "aim at the flag"), ("r", "the caddie's play"))),
    ("Swinging", (("space", "swing: with the meter, again to set the power, again at the | to strike it"),
                  ("Esc", "on the meter: not this swing after all"))),
    ("Camera", (("v", "aim on the whole hole, the target, or ahead of the ball"),
                ("l", "look around: ←↑↓→ move the camera, + and - zoom, l, Esc or space back"),
                ("z", "course only: nothing but the course"))),
    ("", (("o  b  q", "options · back to the courses · quit (b and q twice, once the round's started)"),
          ("Ctrl-L", "draw the screen again"))),
)


def frame(term, scr, now, dt):
    """One frame of the round you're playing."""
    g, colors = scr.game, scr.colors
    cols, rows = term.size()
    if scr.ui.get("help"):
        return help_screen(cols, rows, "Single player: the keys", HELP)
    units, hole = settings["units"], g.hole
    course_only = settings["course_only"]
    show = lambda key: settings[key] and not course_only
    top = 2 if show("show_header") else 0
    bottom = 1 if show("show_keys") else 0
    info = 0 if course_only else INFO_ROWS
    card = []
    if not course_only and settings["scorecard"] != "off":
        card = panels.scorecard(g.holes, [g.golfer], [g.cards], g.hole_i, colors, cols,
                                nine=settings["scorecard"] == "nine")
    budget = rows - top - bottom - info
    if card and budget - len(card) >= MIN_MAP:
        budget -= len(card)
    else:
        card = []
    map_h = max(3, budget)
    args = (g.course, cols, map_h, term.pixel_aspect(), high_detail())
    aiming = scr.phase in ("aim", "meter")
    plan = aim_shot(g) if aiming else None
    land, end, holed = (plan.land, plan.end, plan.holed) if plan else (None, None, False)
    scr.ui["aspect"] = args[3]
    scr.ui["whole"] = scr.views.get(g.course, hole, cols, map_h, args[3], "hole", args[4], wait=True)   # (to look from)
    look = scr.ui.setdefault("look", Look())
    if aiming:
        m = scr.aim_view(args, land or g.carry_spot(), end or g.carry_spot(), dt)
    else:
        m = None
        if look.on:
            course_, cols_, rows_, aspect, quad = args
            whole = scr.ui["whole"] = scr.views.get(course_, hole, cols_, rows_, aspect, "hole", quad, wait=True)
            m = look.view(scr.views, course_, hole, cols_, rows_, aspect, quad, whole)
        if m is None:
            m = direct(scr.scene(now), scr.views, *args, scr.ui, dt)
        else:
            scr.ui.update(map=m, shot=None)

    # The map's marks: trails, the flag, the ball, the shot set up or in the air.
    marks, labels, tints, trails = [], [], [], []
    step_yards = m.pixel_yards() * 0.5
    done = g.shots[:len(g.shots) - len(scr.flying) + scr.fly_i] if scr.phase == "flight" else g.shots
    if settings["trails"]:
        trails = [trail_tints(s, g.golfer.ball, TRAIL, step_yards) for s in done if s.kind not in ("drop", "pickup")]
    if aiming:
        more_tints, more_marks = aim_marks(g, m, plan)
        tints += more_tints
        marks += more_marks
        if m.close and not holed and g.help == "caddie":
            labels.append((end, panels.left(dist(end, hole.pin), "green", units), AIM_COLOR))
    marks.append((hole.pin, "▶", FLAG, True))   # over the aiming cross
    pointers = []   # the ball, if the camera can't show it
    if scr.phase == "flight":
        shot, e = scr.flying[scr.fly_i], now - scr.at
        moving = ball_at(shot, e)
        if shot.kind not in ("drop", "pickup"):
            tints += [(p, g.golfer.ball, TRAIL_NOW) for p in trail(shot, e, step_yards)]
        lift = height_at(shot, e)
        if lift > 0.5:
            tints.append((moving, (0, 0, 0), 0.55))   # its shadow
        marks.append((moving, "●", g.golfer.ball, True, min(LIFT_MAX, round(lift * HEIGHT / m.k / 2))))
        pointers.append((moving, g.golfer.ball))
    elif not g.ball.holed and scr.phase != "done":
        marks.append((g.ball.pos, "●", g.golfer.ball, True))
        pointers.append((g.ball.pos, g.golfer.ball))
        if scr.phase == "result" and m.level != "hole":
            labels.append((g.ball.pos, panels.left(g.to_pin, g.ball.lie, units), g.golfer.ball))
    box = None
    if scr.phase == "intro":
        box = ([panels.hole_title(g.course, hole, units)], BOX_FG, BOX_BG)
    elif scr.phase == "summary":
        box = (summary_box(g), BOX_FG, BOX_BG)
    elif scr.phase == "done":
        box = (final_box(g), BOX_FG, BOX_BG)
    elif look.on and look.drawing and course_only:
        box = ([look.label()], BOX_FG, BOX_BG)
    map_lines = m.lines(colors, marks, labels, box, tints, pointers, trails)
    if course_only:
        return map_lines

    # Put it together.
    lines = []
    if top:
        _, _, diff = g.totals()
        title = "%s  ·  %s  ·  you %s" % (g.course.name, panels.hole_title(g.course, hole, units), to_par(diff))
        weather = panels.weather_text(g.weather, heading(g.ball.pos, hole.pin), m.way, units, m.frame.bearing)
        lines += [center(BOLD + shorten(title, cols) + RESET, cols), center(DIM + shorten(weather, cols) + RESET, cols)]
    lines += map_lines
    width = cols - 2
    if aiming:
        rows_ = setup_lines(g, land, end, holed, units, width)
        if not on_meter(g):
            rows_.append(DIM + ("space to putt" if g.setup.club == "P" else "space to swing") + RESET)
        else:
            stage = None if scr.meter is None else "up" if scr.meter.power is None else "down"
            hint = {None: "space: swing", "up": "space: set the power", "down": "space: strike at the |"}[stage]
            target = 1.0 if g.setup.club == "P" else g.setup.power
            rows_.append(meter_bar(scr.meter, target, now, max(10, min(50, width - 26)), colors, settings["meter"]) +
                         "  " + DIM + hint + RESET)
    elif scr.phase in ("flight", "result"):
        shot = scr.flying[scr.fly_i if scr.phase == "flight" else -1]
        e = now - scr.at
        if scr.phase == "flight" and e < flight_time(shot) + roll_time(shot) * 0.6:
            struck = timing_words(scr.timing) if scr.fly_i == 0 else ""
            rows_ = [panels.rgb_fg(colors, g.golfer.ball, "● ", True) + BOLD + g.golfer.short_name + RESET + "  …" +
                     (DIM + "  struck " + struck + RESET if struck else "")]
        else:
            text = panels.shot_line(scr.flying[0], [g.golfer], hole, units, colors, width)
            if len(scr.flying) > 1 and scr.phase == "result":
                text += DIM + "  ·  " + shorten(panels.shot_text(scr.flying[-1], hole, units), 40) + RESET
            rows_ = [text]
    else:
        rows_ = []
    lines += [" " + r for r in rows_] + [""] * (info - len(rows_))
    lines += [" " + r for r in card]
    if not bottom:
        return lines[:rows]
    if scr.leaving and now - scr.leaving[1] < LEAVE:
        again = "b" if scr.leaving[0] == "back" else "q"
        choices = ("%s again to %s (this round isn't kept)   ·   any other key to play on" % (
            again, "leave the round" if scr.leaving[0] == "back" else "quit"), "%s again to %s" % (again, scr.leaving[0]))
    elif look.on:
        choices = (LOOK_KEYS,)
    elif scr.phase == "aim" and g.setup.club == "P":
        choices = ("←/→ line  ,/. fine  ↑/↓ length  -/+ 5 ft  c club  r caddie  l look  ? keys  space putt  b back",
                   "←/→ line  ↑/↓ length  -/+ 5 ft  r caddie  l look  ? keys  space putt")
    elif scr.phase == "aim":
        choices = ("←/→ aim  ,/. fine  ↑/↓ club  s shot  -/+ power  f flag  r caddie  v view  l look  ? keys  "
                   "space swing  b back",
                   "←/→ aim  ↑/↓ club  s shot  -/+ power  r caddie  v view  l look  ? keys  space swing",
                   "←/→ ↑/↓ s -/+ r v l ? space")
    else:
        choices = ({"meter": "space: set the power, then strike at the |   ·   Esc: not this swing", "flight": "space: skip  l look around",
                    "result": "space: next shot  l look around", "intro": "space: play",
                    "summary": "Enter: next hole  l look around",
                    "done": "Enter new round  b back  q quit"}[scr.phase],)
    if not g.caddie_on_hand:
        choices = tuple(k.replace("r caddie  ", "").replace(" r ", " ") for k in choices)
    return with_footer(lines, rows, cols, fit_keys(choices, cols))


def handle(scr, key, now, menu):
    """Act on a key: a return value ends the round."""
    g = scr.game
    if scr.ui.pop("help", False):
        return None   # any key closes the help
    if key == "?" and scr.phase != "meter":
        scr.ui["help"] = True
        return None
    go = key in (" ", "enter", "right")   # the keys that move the round on
    gap, before = 1e9, scr.last_go
    if go:   # how long since the last of them: a held key repeats every few hundredths of a second
        gap, scr.last_go = now - scr.last_go, now
    look = scr.ui.setdefault("look", Look())
    if look.on and key == "esc":   # done looking (b still leaves)
        look.stop()
        return None
    if scr.phase == "meter" and key in ("b", "esc", "\x7f"):   # not this swing after all
        scr.meter = None
        scr.go("aim", now)
        return None
    if key in ("q", "Q", "b", "esc", "\x7f"):
        out = "quit" if key in ("q", "Q") else "back"
        if g.over or scr.phase == "done" or not (g.shots or any(c is not None for c in g.cards)):
            return out   # nothing to lose
        if scr.leaving and scr.leaving[0] == out and now - scr.leaving[1] < LEAVE:
            return out
        scr.leaving = (out, now)   # the footer asks for it again
        return None
    scr.leaving = None
    if scr.phase != "meter" and look_key(scr.ui, key):
        return None
    if look.on and key in (" ", "enter"):   # done looking: back to the shot (not swinging it)
        look.stop()
        return None
    if look.on and key not in ("o", "z"):   # the shot stays as it is while looking
        return None
    if key == "o":
        menu.toggle()
    elif key == "z":
        settings["course_only"] = not settings["course_only"]
        save_settings()
    elif scr.phase == "intro" and key in (" ", "enter"):
        scr.go("aim", now)
    elif scr.phase == "aim":
        putting = g.setup.club == "P"
        turn = AIM * (0.5 if putting or g.to_pin <= CLOSE_RANGE else 1.0)
        settled = now - scr.at >= SETTLE and gap >= SETTLE
        if key == "right" and not settled:
            pass   # still moving the round on (→ skips the shot and the pause): not aiming yet
        elif key in ("left", "right"):
            g.aim(-turn if key == "left" else turn)
            if key == "right":
                scr.last_go = before   # aiming, not moving the round on: it doesn't hold a swing back
        elif key in (",", "."):
            g.aim(-FINE if key == "," else FINE)
        elif key in ("up", "down"):
            if putting:
                g.change_length(FOOT if key == "up" else -FOOT)
            else:
                g.change_club(-1 if key == "up" else 1)
        elif key in ("+", "=", "-", "_"):
            up = key in ("+", "=")
            if putting:
                g.change_length(5 * FOOT if up else -5 * FOOT)
            else:
                g.change_power(POWER_STEP if up else -POWER_STEP)
        elif key == "c":
            g.change_club(1)
        elif key in ("s", "\t"):
            g.change_kind(1)
        elif key == "S":
            g.change_kind(-1)
        elif key == "f":
            g.aim_at_flag()
        elif key == "r":
            g.take_suggestion()
        elif key == "v":
            scr.look = {"hole": "target", "target": "ball", "ball": "hole"}[scr.look]
        elif key in (" ", "enter"):
            if not settled:
                pass   # only just come up, or space held, or still being pressed to skip what came before
            elif not on_meter(g):
                scr.strike(now)
            else:
                scr.meter = Meter(now, settings["meter"])
                scr.go("meter", now)
    elif scr.phase == "meter" and key in (" ", "enter"):
        if gap >= TAP:   # (not the key repeating)
            struck = scr.meter.press(now)
            if struck:
                scr.strike(now, *struck)
    elif scr.phase == "flight" and go:
        scr.fly_i = len(scr.flying) - 1
        scr.go("result", now)
    elif scr.phase == "result" and go:
        scr.go("summary" if g.ball.holed else "aim", now)
    elif scr.phase == "summary" and go:
        scr.advance(now)
    elif scr.phase == "done" and key == "enter":
        return "again"
    return None


def play_round(term, game, colors, menu):
    """Play a round. Returns "again" (a new round), "back" or "quit"."""
    scr = Screen(game, colors)
    last = time.monotonic()
    try:
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
                scr.at += time.monotonic() - now   # the menu stops the round's clock
                if scr.meter:
                    scr.meter = None
                    scr.go("aim", scr.at)
                if not menu.open and settings["help"] != game.help:   # more help, or less, from the next shot
                    game.help = settings["help"]
                    if not game.ball.holed:
                        game.suggest()
                last = time.monotonic()
                continue
            if scr.ui.get("help"):
                scr.at += dt   # the help stops the round's clock
            else:
                scr.tick(now, PACES.get(settings["pace"], 1.0))
            term.draw(frame(term, scr, now, dt))
            busy = scr.phase in ("flight", "meter") or scr.ui.get("shot") == "aim" or \
                getattr(scr.ui.get("look"), "drawing", False)   # (the map looked for drawn: shown as it's done)
            key = term.read_key(FRAME if busy else 0.1)
            if key == "\x0c":
                term.invalidate()
            elif key is not None:
                out = handle(scr, key, time.monotonic(), menu)
                if out:
                    return out
    finally:
        scr.views.close()   # its worker, and the maps
