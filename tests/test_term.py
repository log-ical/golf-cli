"""The terminal layer (from scoreboard-cli): drawing, keys, signals, text,
and the symbol sets for terminals with fewer glyphs."""

import fcntl
import io
import os
import pathlib
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time
import unittest
from unittest import mock

from tests.helpers import ROOT
from golf import settings as cfg
from golf import term
from golf.term import RESET, Colors, Terminal, center, printable, shorten, visible_len

plain = lambda s: re.sub(r"\x1b\[[0-9;]*m", "", s)


def drawn():
    """Every non-ASCII character in the app's string literals, so a new one
    can't slip in without a substitute (docstrings and comments aside)."""
    chars = set()
    for path in pathlib.Path(ROOT, "golf").rglob("*.py"):
        for line in path.read_text().splitlines():
            code = line.split("#", 1)[0]
            for lit in re.findall(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'', code):
                chars |= {ch for ch in lit if ord(ch) > 127}
    return "".join(sorted(chars))


def reference_draw(lines, rows):
    out = ["\x1b[H"]
    for i in range(rows):
        out.append((lines[i] if i < len(lines) else "") + RESET + "\x1b[K")
        if i < rows - 1:
            out.append("\r\n")
    return "".join(out)


class FakeTerminal(Terminal):
    def __init__(self, cols, rows):
        with mock.patch("sys.stdin", mock.Mock(fileno=lambda: 0)):
            super().__init__()
        self.cols, self.rows = cols, rows

    def size(self):
        return self.cols, self.rows

    def draw_captured(self, lines):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.draw(lines)
        return out.getvalue()


class SymbolsTest(unittest.TestCase):
    def setUp(self):
        self.saved = term.symbols

    def tearDown(self):
        term.symbols = self.saved

    def test_substitutes_are_one_column(self):
        chars = drawn()
        self.assertIn("▶", chars)   # the flag: the scan finds what's drawn
        for mode in ("console", "ascii"):
            term.set_symbols(mode)
            for ch in chars:
                self.assertEqual(visible_len(term.translate(ch)), 1, (mode, ch))
        term.set_symbols("ascii")
        self.assertTrue(plain(term.translate(chars + " Montréal")).isascii())

    def test_console_keeps_what_its_fonts_have(self):
        term.set_symbols("console")
        self.assertEqual(term.translate("┌─┐│█▀▄·°"), "┌─┐│█▀▄·°")
        self.assertEqual(term.translate("● ▶ ↗ ✓"), "* > / +")
        term.set_symbols("full")
        self.assertEqual(term.translate("● ▶"), "● ▶")

    def test_options_switch_the_set(self):
        saved = dict(cfg.settings)
        try:
            menu = cfg.OptionsMenu(Colors("16"))
            menu.sel = [k for k, _, _ in cfg.OPTIONS].index("symbols")
            cfg.settings["symbols"] = "console"
            menu.handle_key("right")
            self.assertEqual((cfg.settings["symbols"], term.symbols), ("ascii", "ascii"))
        finally:
            cfg.settings.clear()
            cfg.settings.update(saved)


class DrawTest(unittest.TestCase):
    def test_first_frame_is_the_full_screen_then_only_changes(self):
        t = FakeTerminal(20, 4)
        lines = ["one", "\x1b[1mtwo" + RESET]
        self.assertEqual(t.draw_captured(lines), term.SYNC_BEGIN + reference_draw(lines, 4) + term.SYNC_END)
        self.assertEqual(t.draw_captured(lines), "")
        out = t.draw_captured(["one", "three"])
        self.assertEqual(out, term.SYNC_BEGIN + "\x1b[2;1Hthree" + RESET + "\x1b[K" + term.SYNC_END)

    def test_a_new_set_of_symbols_redraws_rows_that_havent_changed(self):
        saved = term.symbols
        try:
            t = FakeTerminal(20, 2)
            t.draw_captured(["● ball", "x"])
            term.set_symbols("ascii")
            self.assertIn("* ball", t.draw_captured(["● ball", "y"]))   # the same line, the new symbols
        finally:
            term.symbols = saved

    def test_lines_wider_than_the_screen_are_clipped(self):
        self.assertEqual(term.clip("\x1b[1mabcdef" + RESET, 3), "\x1b[1mabc")
        t = FakeTerminal(6, 2)
        self.assertNotIn("much", t.draw_captured(["top", "a footer much too long"]))

    def test_control_characters_never_reach_the_terminal(self):
        t = FakeTerminal(40, 2)
        t.draw_captured(["", ""])   # the full repaint has its own line breaks; a changed row has none
        out = t.draw_captured(["a\nb\x07c", "x\ud800y"])
        self.assertNotRegex(out, "[\x00-\x09\x0b-\x1a\x1c-\x1f\ud800-\udfff]")
        self.assertIn("a b c", out)


class KeysTest(unittest.TestCase):
    def keys(self, data):
        r, w = os.pipe()
        try:
            os.write(w, data)
            with mock.patch("sys.stdin", mock.Mock(fileno=lambda: r)):
                t = Terminal()
            out = []
            while select.select([r], [], [], 0)[0]:
                out.append(t.read_key(0))
            return out
        finally:
            os.close(r)
            os.close(w)

    def test_escape_sequences_are_read_whole(self):
        self.assertEqual(self.keys(b"\x1b[A\x1bOB\x1b[1;2C"), ["up", "down", "right"])
        self.assertEqual(self.keys(b"\x1b[5~\x1b[6~\x1b[H\x1bOF"), ["pgup", "pgdn", "home", "end"])
        self.assertEqual(self.keys(b"\x1b[15~\x1bOPq"), [None, None, "q"])   # F5, F1, then q
        self.assertEqual(self.keys(b"\x1b"), ["esc"])
        self.assertEqual(self.keys(b" ]\r"), [" ", "]", "enter"])


CHILD = """
import sys
sys.path.insert(0, %r)
from golf.term import Terminal
with Terminal() as t:
    t.draw(["ready"])
    while True:
        t.read_key(0.2)
"""


class SignalsTest(unittest.TestCase):
    """The terminal is handed back on every way out."""

    def test_terminate_restores_the_terminal(self):
        master, slave = os.openpty()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
            before = termios.tcgetattr(slave)
            child = subprocess.Popen([sys.executable, "-c", CHILD % ROOT], stdin=slave, stdout=slave,
                                     stderr=slave, start_new_session=True)
            out, end = b"", time.monotonic() + 5
            while b"ready" not in out and time.monotonic() < end:
                if select.select([master], [], [], 0.05)[0]:
                    out += os.read(master, 65536)
            child.send_signal(signal.SIGTERM)
            self.assertEqual(child.wait(5), 128 + signal.SIGTERM)
            self.assertEqual(termios.tcgetattr(slave), before)
        finally:
            os.close(master)
            os.close(slave)


class TextTest(unittest.TestCase):
    def test_visible_len_center_shorten(self):
        self.assertEqual(visible_len("\x1b[1m\x1b[38;2;1;2;3mab" + RESET + "▀"), 3)
        self.assertEqual(center("\x1b[1mab" + RESET, 6), "  \x1b[1mab" + RESET)
        self.assertEqual(shorten("Driver · sliced · 268 yds · in the trees", 30), "Driver · sliced · 268 yds…")
        self.assertEqual(shorten("abcdefgh", 4), "abc…")

    def test_printable(self):
        self.assertEqual(printable("Golf Club de\nGenève\t(x)"), "Golf Club de Genève (x)")
        self.assertEqual(printable("A\x1b[2J\x07B"), "A[2JB")

    def test_options_menu_keeps_the_selection_in_view(self):
        menu = cfg.OptionsMenu(Colors("16"))
        for rows in range(2, 21):
            for sel in range(len(cfg.OPTIONS)):
                menu.sel = sel
                lines = menu.render(80, rows)
                self.assertEqual(len(lines), rows)
                self.assertTrue(any("\x1b[7m" in l and cfg.OPTIONS[sel][1] in l for l in lines), (rows, sel))


if __name__ == "__main__":
    unittest.main()
