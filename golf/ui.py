"""Pieces the screens share: the keys along the bottom, a page of keys,
a list that scrolls, and work done off the screen's thread."""

import queue
import textwrap
import threading

from . import term as tty
from .term import BOLD, DIM, RESET, center, printable, shorten, visible_len

BOX_FG, BOX_BG = (240, 240, 230), (24, 40, 28)
FRAME = 1 / 30        # seconds between frames while a ball moves


def help_screen(cols, rows, title, sections):
    """A page of keys: sections of (heading, ((keys, what they do), ...)),
    what each does wrapped beside its keys, closer together on a short screen."""
    key_w = max(len(k) for _, keys in sections for k, _ in keys)
    width = max(20, min(cols - 2, 96))
    blocks = []
    for head, keys in sections:
        block = [BOLD + head + RESET] if head else []
        for k, what in keys:
            wrapped = textwrap.wrap(what, max(10, width - key_w - 5)) or [""]
            block.append("  " + BOLD + k.ljust(key_w) + RESET + "   " + wrapped[0])
            block += [" " * (key_w + 5) + w for w in wrapped[1:]]
        blocks.append(block)
    room = rows - 1
    body = [r for b in blocks for r in b + [""]][:-1]   # a blank line between the sections
    head = [center(BOLD + title + RESET, cols), ""]
    if len(head) + len(body) > room:
        body = [r for b in blocks for r in b]
    if len(head) + len(body) > room:
        head = head[:1]
    left = " " * max(0, (cols - min(width, max(visible_len(r) for r in body))) // 2)
    lines = [""] * max(0, (room - len(head) - len(body)) // 2) + head
    lines += [tty.clip(left + r, cols) for r in body]
    return with_footer(lines[:room], rows, cols, "any key: back")


def fit_keys(choices, cols):
    """The first of the key helps (longest first) that fits a screen cols wide; the last cut to fit."""
    for keys in choices:
        if visible_len(keys) + 2 <= cols:
            return keys
    return shorten(choices[-1], cols - 2)


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


class Quit(Exception):
    """The viewer quit from somewhere that can't just return it (waiting on the weather)."""


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
MIN_MAP = 8           # rows the map keeps before the panels under it give way
