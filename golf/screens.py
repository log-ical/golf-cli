"""The course list, loading a course, and setting up a round; the round
itself is zen.py's (watching) or play.py's (playing)."""

import os
import random
import textwrap
import time

from . import catalog, game, osm, panels, players, sim
from . import weather as wx
from .course import TEE_DEPTH, TEE_RGB
from .settings import CHOICES, HINTS, save_settings, settings
from .term import BOLD, DIM, RESET, REVERSE, center, shorten, visible_len
from .play import play_round
from .ui import Job, Quit, scroll_window, with_footer
from .zen import watch_round

TITLE = (96, 180, 72)
YELLOW, RED = "\x1b[33m", "\x1b[31m"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
CREDIT = "Map data © OpenStreetMap contributors · trees: © ESA WorldCover · heights: USGS, Mapzen · weather: Open-Meteo"


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
        lines.append(center(DIM + shorten(CREDIT, cols) + RESET, cols))
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
        lines += ["", center(BOLD + "Enter: tee off" + RESET, cols), ""]
        hint = HINTS.get(items[sel][0], "Which set of tees the holes are played from." if items[sel][0] == "tees"
                         else "")
        lines += [center(DIM + h + RESET, cols) for h in textwrap.wrap(hint, max(20, min(cols - 4, 90)))]
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
            raise Quit
        weather = r[1] if r[0] == "ok" else wx.random_day(rng)
    return weather
