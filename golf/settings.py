"""User settings (saved as JSON) and the options menu."""

import json
import os
import textwrap

from .files import CONFIG_DIR, write_atomic
from .term import BOLD, DIM, RESET, REVERSE, center, detect_color_mode, set_symbols

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

# (key, label, choices)
OPTIONS = [
    ("mode", "Game", ["single player", "zen"]),
    ("player_level", "Your level", ["weekend", "scratch", "tour"]),
    ("swing", "Your swing", ["meter", "simple"]),
    ("meter", "Meter timing", ["easy", "normal", "hard"]),
    ("help", "Help", ["caddie", "yardages", "none"]),
    ("pace", "Pace between shots", ["slow", "normal", "quick", "rapid"]),
    ("camera", "Camera", ["broadcast", "whole hole"]),
    ("golfers", "Zen: golfers in the group", [1, 2, 3, 4]),
    ("level", "Zen: golfers' level", ["tour", "scratch", "weekend", "mixed"]),
    ("weather", "Weather", ["live", "random", "calm", "windy", "wet"]),
    ("holes", "Holes", ["all", "front 9", "back 9"]),
    ("units", "Distances", ["yards", "meters"]),
    ("detail", "Map detail", ["auto", "high", "standard"]),
    ("satellite_trees", "Trees from satellite", [True, False]),
    ("relief", "Slopes on the map", ["on", "greens only", "off"]),
    ("green_view", "Close-up on the green", [True, False]),
    ("trails", "Shot trails", [True, False]),
    ("layout", "Hole layout", ["auto", "across", "up"]),
    ("course_only", "Course only (z)", [False, True]),
    ("show_header", "Title and weather", [True, False]),
    ("show_shot", "What each shot did", [True, False]),
    ("show_group", "The group's standing", [True, False]),
    ("scorecard", "Scorecard", ["auto", "nine", "off"]),
    ("side_panel", "Side panel (wide windows)", ["auto", "off"]),
    ("show_keys", "Key help", [True, False]),
    ("color", "Color mode", ["auto", "truecolor", "256", "16"]),
    ("symbols", "Symbols", ["auto", "full", "console", "ascii"]),
]

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
              " quadrants. Auto: high unless the symbols are console or ASCII.",
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
CHOICES = {k: choices for k, _, choices in OPTIONS}

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
    try:
        os.replace(CONFIG_PATH, CONFIG_PATH + ".bad")
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
        head = ["", center(BOLD + "OPTIONS" + RESET, cols), ""]
        options = []
        for i, (k, label, _) in enumerate(OPTIONS):
            val = "‹ %s ›" % fmt_value(settings[k])
            row = "  %-*s   %-14s" % (label_w, label, val)
            if i == self.sel:
                row = REVERSE + BOLD + row + RESET
            options.append(center(row, cols))
        hint = textwrap.wrap(HINTS.get(OPTIONS[self.sel][0], ""), max(20, cols - 4))
        tail = [""] + [center(DIM + h + RESET, cols) for h in hint]
        # A short window scrolls the options, keeping the one being changed in view.
        room = max(1, rows - 1 - len(head) - len(tail))
        if len(options) > room:
            top = max(0, min(self.sel - room // 2, len(options) - room))
            options = options[top:top + room]
        lines = head + options + tail
        if len(lines) > rows - 1:
            lines = options + tail   # shorter still: the heading goes first
        while len(lines) < rows - 1:
            lines.append("")
        return lines[:rows - 1] + [center(DIM + "↑/↓ choose   ←/→ change   o/Esc close" + RESET, cols)]
