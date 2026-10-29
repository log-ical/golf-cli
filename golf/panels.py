"""The words around the map: what each shot did, the group's standing, the
scorecard, the hole and the weather."""

import math

from . import scoring
from .geo import dist, heading
from .term import BOLD, DIM, RESET, REVERSE, shorten, visible_len

METERS_PER_YARD = 0.9144
FULL_SWINGS = ("tee", "approach", "layup", "punch")
KIND_NAMES = {"chip": "Chip", "pitch": "Pitch", "bunker": "Bunker shot", "putt": "Putt"}
MISS_WORDS = {"slice": "sliced", "hook": "hooked", "push": "pushed right", "pull": "pulled left",
              "chunk": "chunked", "thin": "thinned", "shank": "SHANKED", "skull": "skulled", "lip out": "lips out",
              "stays in": "stays in the bunker"}
LIE_WORDS = {"tee": "tee box", "fairway": "fairway", "green": "green", "rough": "rough", "bunker": "bunker",
             "water": "in the water", "trees": "in the trees", "ob": "out of bounds"}
ARROWS = "→↘↓↙←↖↑↗"   # screen directions, clockwise from right
YELLOW, RED = "\x1b[33m", "\x1b[31m"


def yards(yd, units):
    return "{:,} m".format(round(yd * METERS_PER_YARD)) if units == "meters" else "{:,} yds".format(round(yd))


def feet(yd, units):
    if units == "meters":
        return "%.1f m" % (yd * METERS_PER_YARD)
    return "%d ft" % max(1, round(yd * 3))


def left(yd, lie, units):
    """What's left to the hole, in feet on the green."""
    return feet(yd, units) if lie == "green" else yards(yd, units)


def rgb_fg(colors, rgb, text, bold=False):
    return (BOLD if bold else "") + colors.fg(rgb) + text + RESET


# --------------------------------------------------------------------------
# Shots

def shot_text(shot, hole, units):
    """A shot in words: "Driver · 291 yds · fairway · 164 yds to go"."""
    if shot.kind == "pickup":
        return "Picks up · counts %d" % shot.number
    if shot.kind == "drop":
        return "%s · %s · %s to go" % ("Free relief off the wrong green" if shot.miss == "relief" else
                                       "Takes a drop · 1-stroke penalty", LIE_WORDS.get(shot.lie, shot.lie),
                                       left(shot.to_pin, shot.lie, units))
    parts = []
    if shot.kind == "putt":
        parts.append("Putt from %s" % feet(shot.from_pin, units))
        if not shot.holed:
            if shot.miss:
                parts.append(MISS_WORDS[shot.miss])
            b = heading(shot.start, shot.end)
            ahead = math.cos(math.radians(heading(shot.start, hole.pin) - b)) * dist(shot.start, shot.end)
            word = "past" if ahead > dist(shot.start, hole.pin) else "short"
            if shot.lie != "green":
                parts.append("off the green: %s" % LIE_WORDS.get(shot.lie, shot.lie))
            else:
                parts.append("%s %s" % (feet(shot.to_pin, units), word))
    else:
        parts.append(shot.club if shot.kind in FULL_SWINGS else KIND_NAMES.get(shot.kind, shot.kind.title()))
        if shot.miss:
            parts.append(MISS_WORDS.get(shot.miss, shot.miss))
        if shot.kind in FULL_SWINGS:
            parts.append(yards(shot.length, units))
        if not shot.holed:
            if shot.lie == "green":
                parts.append("on the green, %s" % feet(shot.to_pin, units))
            elif shot.lie in ("water", "ob"):
                parts.append(LIE_WORDS[shot.lie] + (" · stroke and distance" if shot.lie == "ob" else " · penalty"))
            else:
                parts.append("%s · %s to go" % (LIE_WORDS.get(shot.lie, shot.lie), yards(shot.to_pin, units)))
    if shot.holed:
        parts.append("IN THE HOLE! " + scoring.name(shot.number, hole.par))
    return " · ".join(parts)


def shot_line(shot, golfers, hole, units, colors, width):
    """The golfer's ball and name, then what the shot did."""
    g = golfers[shot.player]
    head = rgb_fg(colors, g.ball, "● ", True) + BOLD + g.short_name + RESET + "  "
    n = 2 + len(g.short_name) + 2
    text = shot_text(shot, hole, units)
    style = YELLOW + BOLD if shot.holed else (RED if shot.penalty or shot.miss in ("shank",) else "")
    return head + style + shorten(text, max(0, width - n)) + RESET


# --------------------------------------------------------------------------
# The hole, the weather

def hole_title(course, hole, units):
    parts = ["Hole %d" % hole.number]
    if hole.name:
        parts.append(hole.name)
    parts += ["Par %d" % hole.par, yards(hole.yards, units) + (" (%s)" % hole.color if hole.color else "")]
    if hole.handicap:
        parts.append("Hcp %d" % hole.handicap)
    return " · ".join(parts)


def wind_words(weather, bearing):
    """How the wind plays on a shot toward `bearing`: "helping", "into",
    "left to right" or "right to left"."""
    rel = (weather.wind_from + 180 - bearing) % 360
    if rel < 45 or rel >= 315:
        return "helping"
    if 135 <= rel < 225:
        return "into"
    return "left to right" if rel < 180 else "right to left"


def wind_arrow(weather, bearing, way):
    """The screen arrow the wind blows along, on a map with the hole running
    `way` ("across": toward the right; "up": toward the top)."""
    rel = math.radians((weather.wind_from + 180 - bearing) % 360)
    if way == "across":
        dx, dy = math.cos(rel), math.sin(rel)
    else:
        dx, dy = math.sin(rel), -math.cos(rel)
    angle = math.degrees(math.atan2(dy, dx)) % 360   # clockwise from right, y down
    return ARROWS[int((angle + 22.5) // 45) % 8]


def weather_text(weather, bearing, way, units, map_bearing=None):
    """The wind (as it plays on a shot toward bearing, and its arrow on a map
    turned to map_bearing, the same if not given), the temperature and sky."""
    if weather.wind_mph < 1:
        wind = "No wind"
    else:
        speed = "%d km/h" % round(weather.wind_mph * 1.609) if units == "meters" else "%d mph" % round(weather.wind_mph)
        arrow = wind_arrow(weather, bearing if map_bearing is None else map_bearing, way)
        wind = "Wind %s %s %s" % (arrow, speed, wind_words(weather, bearing))
        if weather.gust_mph > weather.wind_mph + 3:
            g = weather.gust_mph * (1.609 if units == "meters" else 1)
            wind += ", gusting %d" % round(g)
    temp = "%d°C" % round((weather.temp_f - 32) * 5 / 9) if units == "meters" else "%d°F" % round(weather.temp_f)
    source = {"live": "live weather", "random": "made-up weather", "calm": "calm"}[weather.source]
    return "%s · %s %s · %s" % (wind, temp, weather.sky, source)


# --------------------------------------------------------------------------
# The group and the scorecard

def group_rows(golfers, standing, colors, width, playing=None, two_lines=False, holes=18):
    """A row per golfer, best first: place, name, score to par and thru (F
    once all the round's holes are done), and where their ball is on this
    hole (with two_lines, on a row of its own under the name). standing[i]
    is (to par, holes done, what's happening now) for golfer i."""
    rows = []
    places = scoring.standings([s[0] for s in standing])
    name_w = max(len(g.short_name) for g in golfers)
    for place, i in places:
        g = golfers[i]
        to_par, thru, now = standing[i]
        mark = "▶" if i == playing else " "
        thru_text = "F" if thru >= holes else "thru %d" % thru if thru else ""
        head = "%s %-3s " % (mark, place if len(golfers) > 1 else "")
        line = head + rgb_fg(colors, g.ball, "●", True) + " " + BOLD + g.short_name.ljust(name_w) + RESET
        score = scoring.to_par(to_par)
        col = (255, 95, 95) if to_par < 0 else None
        line += "  " + (rgb_fg(colors, col, score.rjust(3), True) if col else BOLD + score.rjust(3) + RESET)
        line += "  " + DIM + thru_text.ljust(8) + RESET
        used = len(head) + 2 + name_w + 2 + 3 + 2 + 8
        if two_lines:
            rows += [line, " " * (len(head) + 2) + (_cut(now, width - len(head) - 2) if now else "")]
            continue
        if now:
            line += "  " + _cut(now, width - used - 2)
        rows.append(line)
    return rows


def _cut(styled, width):
    """A dim note cut to width (it's plain text inside one style)."""
    plain = styled.replace(DIM, "").replace(RESET, "")
    return DIM + shorten(plain, max(0, width)) + RESET


def scorecard(holes, golfers, cards, current, colors, width, nine=False):
    """Hole numbers, pars, and each golfer's scores (cards[i][j]: strokes on
    holes[j], or None if not yet played). All 18 if they fit (and not
    nine), else the nine with the current hole."""
    n = len(holes)
    name_w = min(12, max(len(g.short_name) for g in golfers))
    nines = [list(range(0, min(9, n))), list(range(9, n))] if n > 9 else [list(range(n))]
    nines = [x for x in nines if x]
    total_w = name_w + 3 + sum(len(x) * 3 + 5 for x in nines) + 5   # "● name " and the nines, then Tot
    if (nine or total_w + 1 > width) and len(nines) > 1:             # (+1: it's shown a space in)
        nines = [x for x in nines if current in x] or nines[:1]
    head, par = " " * (name_w + 3), "Par".ljust(name_w + 3)
    rows = [[] for _ in golfers]
    for k, nine in enumerate(nines):
        for j in nine:
            num = "%3d" % holes[j].number
            head += (REVERSE + BOLD + num + RESET) if j == current else DIM + num + RESET
            par += DIM + "%3d" % holes[j].par + RESET
        label = "Out" if k == 0 and len(nines) > 1 else "In" if len(nines) > 1 else "Tot"
        if n > 9 and len(nines) == 1:
            label = "Out" if nine[0] == 0 else "In"
        head += DIM + "  %3s" % label + RESET
        par += DIM + "  %3d" % sum(holes[j].par for j in nine) + RESET
        for i, card in enumerate(cards):
            for j in nine:
                s = card[j]
                if s is None:
                    rows[i].append(DIM + "  ·" + RESET)
                else:
                    col = scoring.color(s, holes[j].par)
                    rows[i].append(rgb_fg(colors, col, "%3d" % s, True) if col else "%3d" % s)
            played = [card[j] for j in nine if card[j] is not None]
            rows[i].append("  %3s" % (sum(played) if played else ""))
    if len(nines) > 1:
        head += DIM + "  Tot" + RESET
        par += DIM + "  %3d" % sum(h.par for h in holes) + RESET
        for i, card in enumerate(cards):
            played = [s for s in card if s is not None]
            rows[i].append(BOLD + "  %3s" % (sum(played) if played else "") + RESET)
    out = [head, par]
    for g, r in zip(golfers, rows):
        out.append(rgb_fg(colors, g.ball, "●", True) + " " + g.short_name[:name_w].ljust(name_w) + " " + "".join(r))
    return out


# --------------------------------------------------------------------------
# A title in block letters

BIG = {
    "G": ["████", "█   ", "█ ██", "█  █", "████"],
    "O": ["████", "█  █", "█  █", "█  █", "████"],
    "L": ["█   ", "█   ", "█   ", "█   ", "████"],
    "F": ["████", "█   ", "███ ", "█   ", "█   "],
    " ": ["  ", "  ", "  ", "  ", "  "],
}


def big_text(text):
    return ["  ".join(BIG.get(ch, BIG[" "])[r].replace("█", "██").replace(" ", "  ") for ch in text) for r in range(5)]


def pad(line, width):
    """line padded with spaces to width visible columns."""
    return line + " " * max(0, width - visible_len(line))
