"""User settings (saved as JSON) and the options menu."""

import json
import os
import textwrap

from .files import CONFIG_DIR, write_atomic
from .term import BOLD, DIM, RESET, REVERSE, center, detect_color_mode, set_symbols, visible_len

CONFIG_PATH = os.path.join(CONFIG_DIR, "settings.json")

DEFAULTS = {
    "mode": "single player",
    "player_level": "scratch",
    "swing": "meter",
    "meter": "normal",
    "help": "yardages",
    "pace": "normal",
    "camera": "broadcast",
    "golfers": 3,
    "level": "tour",
    "weather": "live",
    "holes": "all",
    "tees": "white",
    "units": "yards",
    "detail": "auto",
    "satellite_trees": True,
    "relief": "on",
    "green_view": True,
    "trails": True,
    "layout": "auto",
    "course_only": False,
    "show_header": True,
    "show_shot": True,
    "show_group": True,
    "scorecard": "auto",
    "side_panel": "auto",
    "show_keys": True,
    "color": "auto",
    "symbols": "auto",
    "last_course": "",       # "way:871993734": the course list starts on it
}

# What each setting can be (the first of a list is where it starts, as DEFAULTS says).
CHOICES = {
    "mode": ["single player", "zen"],
    "player_level": ["weekend", "scratch", "tour"],
    "swing": ["meter", "simple"],
    "meter": ["easy", "normal", "hard"],
    "help": ["caddie", "yardages", "none"],
    "pace": ["slow", "normal", "quick", "rapid"],
    "camera": ["broadcast", "whole hole"],
    "golfers": [1, 2, 3, 4],
    "level": ["tour", "scratch", "weekend", "mixed"],
    "weather": ["live", "random", "calm", "windy", "wet"],
    "holes": ["all", "front 9", "back 9"],
    "units": ["yards", "meters"],
    "detail": ["auto", "high", "standard"],
    "satellite_trees": [True, False],
    "relief": ["on", "greens only", "off"],
    "green_view": [True, False],
    "trails": [True, False],
    "layout": ["auto", "across", "up"],
    "course_only": [False, True],
    "show_header": [True, False],
    "show_shot": [True, False],
    "show_group": [True, False],
    "scorecard": ["auto", "nine", "off"],
    "side_panel": ["auto", "off"],
    "show_keys": [True, False],
    "color": ["auto", "truecolor", "256", "16"],
    "symbols": ["auto", "full", "console", "ascii"],
}
LABELS = {
    "swing": "Your swing", "meter": "Meter timing", "help": "Help (single player)", "pace": "Pace between shots",
    "units": "Distances", "camera": "Camera", "green_view": "Close-up on the green", "layout": "Hole layout",
    "detail": "Map detail", "relief": "Slopes on the map", "trails": "Shot trails",
    "satellite_trees": "Trees from satellite", "course_only": "Course only (z)", "show_header": "Title and weather",
    "show_shot": "What each shot did", "show_group": "The group's standing", "scorecard": "Scorecard",
    "side_panel": "Side panel (wide windows)", "show_keys": "Key help", "color": "Color mode", "symbols": "Symbols",
}
# The options menu (o), in sections: what can change at any time. The rest
# (the game, your level, the group, the weather, the holes and the tees) are
# the round's, chosen on its setup screen before it starts.
SECTIONS = (
    ("Playing", ("swing", "meter", "help", "pace", "units")),
    ("Camera and map", ("camera", "green_view", "layout", "detail", "relief", "trails", "satellite_trees")),
    ("On screen", ("course_only", "show_header", "show_shot", "show_group", "scorecard", "side_panel", "show_keys")),
    ("Terminal", ("color", "symbols")),
)
OPTIONS = [(k, LABELS[k], CHOICES[k]) for _, keys in SECTIONS for k in keys]   # (key, label, choices)

HINTS = {
    "mode": "Single player: you play the round, shot by shot. Zen: sit back and watch a group of made-up golfers"
            " play it. On the course list, Tab switches it too.",
    "player_level": "Weekend: full swings, punches, chips, pitches and splashes. Scratch: draws, fades and flops"
                    " too. Tour: the stinger as well. The better you are, the straighter and longer you hit it.",
    "swing": "Meter: press to swing, again to set the power, again at the mark to strike it (early hooks it, late"
             " slices it). Simple: the power set, and your level's usual scatter.",
    "meter": "How much room the swing meter gives you at the strike mark: easy, four tenths of a second either"
             " side (and a slower meter); normal, two tenths; hard, under a tenth. The green round the | is the"
             " room you have, brightest where the strike is pure.",
    "help": "Caddie: each shot set up the caddie's way, and the map shows where it'll stop. Yardages: aimed at the"
            " flag with the club that carries it in still air, the map showing that carry; the wind, the roll and"
            " the trouble are yours (r asks the caddie). None: just your line, and the club you hit last.",
    "pace": "How long the round lingers between shots, on the tee and after each hole. The shots themselves"
            " always fly in real time. In a round, + and - change it too.",
    "camera": "Broadcast: cuts to each golfer, follows the ball down the fairway and closes in on the green, as on"
              " TV. Whole hole: the whole hole always in view.",
    "golfers": "How many play together in zen. Takes effect from the next round.",
    "level": "Tour pros shoot around par; scratch amateurs a few over; weekend golfers slice it into the trees."
             " Mixed: one of each. From the next round.",
    "weather": "Live: the course's weather right now (Open-Meteo). Random: any day. Calm: no wind. Windy: 15-25 mph."
               " Wet: rain, and the ground soft. From the next round.",
    "holes": "The whole course, or just its front or back nine. From the next round.",
    "detail": "High: four pixels to a character (quadrant blocks). Standard: two (half blocks), for fonts without"
              " quadrants. Auto: high unless the symbols are console or ASCII (whose fonts lack the quadrants: with"
              " those it's always standard).",
    "green_view": "Chips and putts are shown close up on the green.",
    "satellite_trees": "Fill in the trees OpenStreetMap leaves out (often all those between the holes) from ESA"
                       " WorldCover's satellite land cover. They play as trees. From the next course loaded.",
    "relief": "On: the hills, banks and hollows shaded by their slope, lit from the top left, and contour lines on"
              " the green close up, 4 inches of height apart, to read putts by. Greens only: just the contour"
              " lines. Off: neither. The slopes play the same either way (where the course's heights are known).",
    "trails": "Each shot's flight stays drawn until the hole is done.",
    "layout": "Across: tee on the left. Up: tee at the bottom. Auto: whichever shows the hole bigger.",
    "course_only": "Just the course: the hole fills the screen, with nothing else on it. z switches it in a round.",
    "show_header": "The course, the hole and the weather at the top.",
    "show_shot": "A line under the map saying what the last shot did.",
    "show_group": "Each golfer's score and where their ball is.",
    "scorecard": "Auto: all 18 holes if they fit, else the nine being played. Nine: always just the nine. Off: none.",
    "side_panel": "In a window 130 or more columns wide, the group and the hole's shots go beside the map.",
    "show_keys": "The keys at the bottom of the screen.",
    "symbols": "auto: console-safe on the Linux text console, ASCII without Unicode, full Unicode elsewhere.",
}

PACES = {"slow": 0.5, "normal": 1.0, "quick": 2.0, "rapid": 4.0}

settings = dict(DEFAULTS)


def _valid(key, value):
    """value if the app can use it for key, else None."""
    if type(value) is not type(DEFAULTS[key]) or (key in CHOICES and value not in CHOICES[key]):
        return None
    return value


def load_settings():
    """Load the saved settings over the defaults, and return what was wrong
    with them (for a warning). A file that can't be used whole is moved aside
    to settings.json.bad before anything is saved over it, so a typo in a
    hand edit doesn't quietly lose the rest."""
    try:
        with open(CONFIG_PATH, encoding="utf-8-sig") as f:   # -sig: a BOM from an editor is fine
            text = f.read()
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:   # unreadable, or not UTF-8
        return _set_aside([str(e)])
    try:
        saved = json.loads(text)
    except (ValueError, RecursionError) as e:
        saved, problems = {}, [str(e)]
    else:
        problems = [] if isinstance(saved, dict) else ["not a JSON object"]
    for k, v in (saved.items() if isinstance(saved, dict) else ()):
        if k in DEFAULTS:
            ok = _valid(k, v)
            if ok is None:
                problems.append("%s: %s" % (k, json.dumps(v)[:40]))
            else:
                settings[k] = ok
    return _set_aside(problems) if problems else []


def _set_aside(problems):
    try:   # the file itself, so a settings.json linked from a dotfiles folder stays a link
        real = os.path.realpath(CONFIG_PATH)
        os.replace(real, real + ".bad")
    except OSError:
        pass
    return problems


def save_settings():
    """Save the settings; False if that failed. Atomic, so a full disk, a
    Ctrl-C or another instance saving at once can't leave half a file."""
    try:
        # realpath: a settings.json linked from a dotfiles folder stays a link
        write_atomic(os.path.realpath(CONFIG_PATH), json.dumps(settings, indent=2).encode())
        return True
    except Exception:
        return False


def step_pace(direction):
    keys = CHOICES["pace"]
    i = keys.index(settings["pace"]) if settings["pace"] in keys else 1
    settings["pace"] = keys[max(0, min(len(keys) - 1, i + direction))]


def fmt_value(value):
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


class OptionsMenu:
    def __init__(self, colors):
        self.colors = colors
        self.sel = 0
        self.open = False

    def toggle(self):
        self.open = not self.open
        if not self.open:
            save_settings()

    def handle_key(self, key):
        if key in ("o", "esc", "enter", "q"):
            self.toggle()
            return
        if key in ("up", "k"):
            self.sel = (self.sel - 1) % len(OPTIONS)
        elif key in ("down", "j"):
            self.sel = (self.sel + 1) % len(OPTIONS)
        elif key in ("left", "right", "h", "l", " ", "-", "+", "="):
            direction = -1 if key in ("left", "h", "-") else 1
            k, _, choices = OPTIONS[self.sel]
            i = choices.index(settings[k]) if settings[k] in choices else 0
            settings[k] = choices[(i + direction) % len(choices)]
            if k == "color":
                self.colors.mode = detect_color_mode() if settings[k] == "auto" else settings[k]
            elif k == "symbols":
                set_symbols(settings[k])

    def render(self, cols, rows):
        label_w = max(len(o[1]) for o in OPTIONS)
        val_w = max(len(fmt_value(v)) for _, _, choices in OPTIONS for v in choices) + 4
        head = ["", center(BOLD + "OPTIONS" + RESET, cols), ""]
        body, at, i = [], 0, 0   # rows, and the one that's chosen
        for title, keys in SECTIONS:
            if body:
                body.append("")
            body.append(center(DIM + BOLD + ("%-*s" % (label_w + val_w + 5, title)) + RESET, cols))
            for k in keys:
                row = "  %-*s   %-*s" % (label_w, LABELS[k], val_w, "‹ %s ›" % fmt_value(settings[k]))
                if i == self.sel:
                    row, at = REVERSE + BOLD + row + RESET, len(body)
                body.append(center(row, cols))
                i += 1
        hint = textwrap.wrap(HINTS.get(OPTIONS[self.sel][0], ""), max(20, cols - 4))
        tail = [""] + [center(DIM + h + RESET, cols) for h in hint]
        # A short window scrolls the options, keeping the one being changed in view.
        room = max(1, rows - 1 - len(head) - len(tail))
        if len(body) > room:
            top = max(0, min(at - room // 2, len(body) - room))
            body = body[top:top + room]
        lines = head + body + tail
        if len(lines) > rows - 1:
            lines = body + tail   # shorter still: the heading goes first
        while len(lines) < rows - 1:
            lines.append("")
        foot = "↑/↓ choose   ←/→ change   o/Esc close   (the game, your level, the group and the weather: on each " \
               "round's setup)"
        if visible_len(foot) > cols:
            foot = "↑/↓ choose   ←/→ change   o/Esc close"
        return lines[:rows - 1] + [center(DIM + foot + RESET, cols)]
