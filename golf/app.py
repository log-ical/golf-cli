"""Entry point: argument parsing and the screen loop."""

import argparse
import sys

from . import catalog
from .screens import course_list_screen, play_course
from .settings import CHOICES, CONFIG_PATH, OptionsMenu, load_settings, save_settings, settings
from .term import SYMBOL_SETS, Colors, Terminal, detect_color_mode, set_symbols


def main(argv=None):
    p = argparse.ArgumentParser(prog="golf", description="Golf in the terminal on real courses from OpenStreetMap: "
                                                         "play them yourself, or watch made-up golfers play them.")
    p.add_argument("course", nargs="?", help="start on the first course whose name contains this (e.g. augusta)")
    p.add_argument("--golfers", type=int, choices=CHOICES["golfers"], help="how many in the group (saved)")
    p.add_argument("--level", choices=CHOICES["level"], help="how good they are (saved)")
    p.add_argument("--weather", choices=CHOICES["weather"], help="live, random or calm (saved)")
    p.add_argument("--seed", type=int, help="play this round again: the same seed gives the same shots")
    p.add_argument("--play", dest="mode", action="store_const", const="single player",
                   help="single player: you play the round (saved)")
    p.add_argument("--zen", dest="mode", action="store_const", const="zen",
                   help="zen: watch made-up golfers play it (saved)")
    p.add_argument("--you", choices=CHOICES["player_level"], help="your level in single player (saved)")
    p.add_argument("--color", choices=["truecolor", "256", "16"], help="color mode (overrides the saved option)")
    p.add_argument("--symbols", choices=SYMBOL_SETS,
                   help="symbol set: full Unicode, console-safe or ascii (overrides the saved option)")
    args = p.parse_args(argv)

    if not sys.stdin.isatty():
        sys.exit("golf needs an interactive terminal.")

    problems = load_settings()
    if problems:   # printed before the screen is taken over, so it's there after quitting
        print("golf: %s: %s. Using the defaults for those; the file is kept as settings.json.bad."
              % (CONFIG_PATH, "; ".join(problems)), file=sys.stderr)
    for k in ("golfers", "level", "weather"):
        if getattr(args, k) is not None:
            settings[k] = getattr(args, k)
    if args.mode:
        settings["mode"] = args.mode
    if args.you:
        settings["player_level"] = args.you
    save_settings()
    start = None
    if args.course:
        q = args.course.lower()
        start = next((e for e in catalog.played() + list(catalog.FAMOUS) if q in e.name.lower()), None)
        if start is None:
            sys.exit("golf: no course in the list matches %r (search for it with / in the app)." % args.course)
    mode = args.color or (settings["color"] if settings["color"] != "auto" else detect_color_mode())
    colors = Colors(mode)
    set_symbols(args.symbols or settings["symbols"])
    menu = OptionsMenu(colors)

    try:
        with Terminal() as term:
            while True:
                entry = start or course_list_screen(term, menu, colors)
                start = None
                if entry is None:
                    break
                term.draw([])
                if not play_course(term, entry, menu, colors, args.seed):
                    break
                args.seed = None
    except KeyboardInterrupt:
        pass
    finally:
        save_settings()   # an option changed in a menu still open (Ctrl-C, a signal) isn't lost
