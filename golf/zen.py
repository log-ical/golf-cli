"""Zen: a round played back shot by shot, on the broadcast camera, with
the group's standing and the scorecard filling in underneath."""

import time

from . import panels, render
from .camera import (HEIGHT, LIFT_MAX, LOOK_KEYS, TRAIL, TRAIL_NOW, Look, Scene, Views, ball_at, direct,
                     flight_time, height_at, high_detail, look_key, pause_time, roll_time, trail,
                     trail_tints)
from .geo import dist, heading
from .panels import YELLOW
from .scoring import name as score_name, standings, to_par
from .settings import PACES, save_settings, settings, step_pace
from .term import BOLD, DIM, RESET, center, shorten
from .ui import BOX_BG, BOX_FG, FRAME, MIN_MAP, fit_keys, help_screen, with_footer

ZEN_HELP = (
    ("Watching", (("space", "pause, or play on"), ("→", "on to what's next (a shot in the air lands)"),
                  ("←", "this shot again, or the one before"), ("]  [", "the next hole; the start of this one (twice: "
                                                                         "the one before)"),
                  ("+  -", "the pace between shots (the shots always take their real time)"))),
    ("Camera", (("c", "broadcast, or the whole hole"), ("g", "close-up on the green, on or off"),
                ("l", "look around: ←↑↓→ move the camera, + and - zoom, l or Esc back"),
                ("z", "course only: nothing but the course"), ("t", "shot trails, on or off"))),
    ("", (("o  b  q", "options · back to the courses · quit"), ("Ctrl-L", "draw the screen again"))),
)


# --------------------------------------------------------------------------
# Watching a round
#
# Shots play in real time whatever the pace: a drive hangs about five
# seconds in the air, as it would. The pace stretches or shrinks the rest:
# the hole's introduction, each golfer settling over the ball, the pause
# after a shot, and the hole's summary.

INTRO, ADDRESS, SUMMARY = 2.4, 1.3, 4.0   # seconds at the normal pace
NOTE = 2.5            # seconds the note on how to leave course only shows


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
            if self.shown == 0 and (self.phase == "intro" or self.phase == "address" and self.elapsed < 1.0) \
                    and self.hole_i > 0:   # at the hole's start already: the one before
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
            now = DIM + "%s · %d" % ("picked up" if s.kind == "pickup" else "holed out", s.number) + RESET
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


def watch_round(term, rnd, colors, menu):
    """Play a round back. Returns "again" (a new round), "back" or "quit"."""
    pb = Playback(rnd)
    views, ui = Views(colors), {}
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
                continue
            if not ui.get("help"):   # (the help stops the round)
                pb.tick(dt, PACES.get(settings["pace"], 1.0))
            if pb.phase in ("pause", "summary") and len(rnd.played) <= pb.hole_i + 1 < len(rnd.holes):
                rnd.play(pb.hole_i + 1)   # simulated while nothing moves, not as the next hole starts
            term.draw(round_frame(term, pb, colors, views, ui, dt))
            moving = pb.phase == "shot" and not pb.paused or getattr(ui.get("look"), "drawing", False)
            key = term.read_key(FRAME if moving or ui.get("cam_moving") else 0.1)
            if key is not None and ui.pop("help", False):
                continue   # any key closes the help
            if key is None or look_key(ui, key):
                continue
            if key == "?":
                ui["help"] = True
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
    finally:
        views.close()   # its worker, and the maps


def round_frame(term, pb, colors, views, ui=None, dt=0.0):
    """One frame of the round view."""
    ui = {} if ui is None else ui
    cols, rows = term.size()
    if ui.get("help"):
        return help_screen(cols, rows, "Zen: the keys", ZEN_HELP)
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
                              two_lines=side, holes=len(rnd.holes)) if show("show_group") else []
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

    quad, aspect = high_detail(), term.pixel_aspect()
    look = ui.setdefault("look", Look())
    ui["aspect"] = aspect
    m = None
    if look.on:
        ui["whole"] = whole = views.get(rnd.course, hole, map_w, map_h, aspect, "hole", quad, wait=True)
        m = look.view(views, rnd.course, hole, map_w, map_h, aspect, quad, whole)
        if m is not None:
            ui.update(map=m, shot=None)
    if m is None:
        m = direct(pb.scene(), views, rnd.course, map_w, map_h, aspect, quad, ui, dt)
        ui["whole"] = views.get(rnd.course, hole, map_w, map_h, aspect, "hole", quad, wait=True)
    ui["cam_moving"] = m.level != "hole" and not look.on and pb.phase in ("address", "pause") or look.drawing
    close = m.close

    # The map's marks: trails, the flag, balls at rest, the ball in the air.
    done = pb.done_shots()
    rest = {}
    for s in done:
        rest[s.player] = s
    marks, labels, tints, trails = [], [], [], []
    step_yards = m.pixel_yards() * 0.5   # half a pixel: an unbroken line of them
    if settings["trails"]:
        trails = [trail_tints(s, golfers[s.player].ball, TRAIL, step_yards) for s in done
                  if s.kind not in ("drop", "pickup")]
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
    if pb.phase == "address" and current.player not in rest:   # teed up
        marks.append((current.start, "●", golfers[current.player].ball, True))
    if current is not None and m.level == "fairway" and pb.phase in ("address", "pause") and not current.holed:
        # The broadcast's yardage, by the ball.
        at, left = (current.start, current.from_pin) if pb.phase == "address" else (current.end, current.to_pin)
        lie = "green" if dist(at, hole.pin) < 1 or (pb.phase == "pause" and current.lie == "green") else ""
        labels.append((at, panels.left(left, lie, units), golfers[current.player].ball))
    pointers = []   # the ball in play, if the camera can't show it
    if moving is not None:
        lift = height_at(current, pb.elapsed)
        if lift > 0.5:
            tints.append((moving, (0, 0, 0), 0.55))   # its shadow on the ground
        rows_up = min(LIFT_MAX, round(lift * HEIGHT / m.k / 2))
        marks.append((moving, "●", golfers[current.player].ball, True, rows_up))
        pointers.append((moving, golfers[current.player].ball))
    elif current is not None and not current.holed and pb.phase in ("address", "pause"):
        pointers.append((current.start if pb.phase == "address" else current.end, golfers[current.player].ball))
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
    elif course_only and look.on:
        box = ([look.label()], BOX_FG, BOX_BG) if look.drawing else None
    map_lines = m.lines(colors, marks, labels, box, tints, pointers, trails)
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
        camera = {"hole": "", "fairway": "   ·   fairway camera", "green": "   ·   green camera",
                  "cup": "   ·   on the cup"}.get(m.level, "")
        if look.on:
            camera = "   ·   " + look.label()
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
    if look.on:
        choices = (LOOK_KEYS + "   space pause", LOOK_KEYS)
    elif pb.phase == "done":
        choices = ("Enter new round  ←/[ replay  l look around  o options  b back  q quit", "Enter again  b back  q quit")
    else:
        choices = ("space pause  ←/→ shots  [ ] holes  +/- pace  c camera  l look around  z course only  ? keys  "
                   "o options  b back  q quit",
                   "space pause  ←/→ shots  [ ] holes  +/- pace  c camera  l look  z course only  ? keys  b back",
                   "space pause  ←/→ shot  [ ] hole  +/- pace  l look  ? keys  b back",
                   "space ←/→ [ ] +/- l ? b q")
    return with_footer(lines, rows, cols, fit_keys(choices, cols))
