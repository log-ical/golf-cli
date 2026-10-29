"""A frame of the app (rows of text with its color escapes) as a PNG, the way
a terminal would show it. Block and quadrant characters are drawn as
rectangles, so the map's pixels come out sharp whatever the font.

Needs Pillow (a development tool only: the app itself uses the standard
library alone) and a monospaced font: JetBrains Mono if fc-match finds it,
else DejaVu Sans Mono.
"""

import functools
import re
import subprocess
import unicodedata

from PIL import Image, ImageDraw, ImageFont

CW, CH = 9, 18          # a cell's size in points: a half block is then square, as the app assumes
FONT_SIZE = 15
SCALE = 2               # pixels per point in the PNG (2: sharp on high-density screens)

DEFAULT_FG = (208, 208, 208)
DEFAULT_BG = (22, 22, 28)
PALETTE16 = [(0, 0, 0), (205, 49, 49), (13, 188, 121), (229, 168, 16), (36, 114, 200), (188, 63, 188),
             (17, 168, 205), (229, 229, 229), (102, 102, 102), (241, 76, 76), (35, 209, 139), (245, 245, 67),
             (59, 142, 234), (214, 112, 214), (41, 184, 219), (255, 255, 255)]
DIM_TOWARD_BG = 0.45

_SGR = re.compile(r"\x1b\[([0-9;]*)m")
_OTHER = re.compile(r"\x1b\[[0-9;?]*[A-Za-ln-z]")
# Each block character's quarters: 8 upper left, 4 upper right, 2 lower left, 1 lower right.
QUADS = {"▘": 8, "▝": 4, "▖": 2, "▗": 1, "▀": 12, "▄": 3, "▌": 10, "▐": 5, "▚": 9, "▞": 6, "▛": 14,
         "▜": 13, "▙": 11, "▟": 7, "█": 15}


def _font_file(family, style):
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", "%s:style=%s" % (family, style)],
                             capture_output=True, text=True, timeout=10).stdout
        return out or None
    except (OSError, subprocess.SubprocessError):
        return None


@functools.lru_cache(maxsize=None)
def fonts():
    """(regular, bold, fallback) fonts at the PNG's size."""
    size = FONT_SIZE * SCALE
    load = lambda family, style: ImageFont.truetype(_font_file(family, style) or "DejaVuSansMono.ttf", size)
    return load("JetBrains Mono", "Regular"), load("JetBrains Mono", "Bold"), load("DejaVu Sans Mono", "Book")


@functools.lru_cache(maxsize=None)
def _has_glyph(font, ch):
    """Whether the font draws ch as itself, not as its box for a missing one."""
    try:
        mask = font.getmask(ch)
        missing = font.getmask("\U0010fffd")
        return mask.getbbox() is not None and (mask.size != missing.size or bytes(mask) != bytes(missing))
    except Exception:
        return False


def _xterm256(n):
    if n < 16:
        return PALETTE16[n]
    if n >= 232:
        v = 8 + (n - 232) * 10
        return (v, v, v)
    n -= 16
    steps = (0, 95, 135, 175, 215, 255)
    return (steps[n // 36], steps[n // 6 % 6], steps[n % 6])


def cells(line):
    """A row's cells: (character, foreground, background, bold), with dim and
    reverse video worked in."""
    out = []
    fg = bg = None
    bold = dim = rev = False
    pos = 0
    line = _OTHER.sub("", line)
    for m in list(_SGR.finditer(line)) + [None]:
        for ch in line[pos:m.start() if m else len(line)]:
            if unicodedata.combining(ch):
                continue
            f, b = fg or DEFAULT_FG, bg or DEFAULT_BG
            if rev:
                f, b = b, f
            if dim:
                f = tuple(round(x + (y - x) * DIM_TOWARD_BG) for x, y in zip(f, b))
            out.append((ch, f, b, bold))
            if unicodedata.east_asian_width(ch) in ("W", "F"):
                out.append(("", f, b, bold))   # the second column of a wide character
        if m is None:
            return out
        pos = m.end()
        codes = [int(c) if c else 0 for c in m.group(1).split(";")]
        i = 0
        while i < len(codes):
            c = codes[i]
            if c == 0:
                fg = bg = None
                bold = dim = rev = False
            elif c in (1, 2, 22):
                bold, dim = (True, dim) if c == 1 else (bold, True) if c == 2 else (False, False)
            elif c in (7, 27):
                rev = c == 7
            elif c in (38, 48) and i + 1 < len(codes):
                if codes[i + 1] == 2:
                    rgb, i = tuple(codes[i + 2:i + 5]), i + 4
                else:
                    rgb, i = _xterm256(codes[i + 2]), i + 2
                fg, bg = (rgb, bg) if c == 38 else (fg, rgb)
            elif c in (39, 49):
                fg, bg = (None, bg) if c == 39 else (fg, None)
            elif 30 <= c <= 37 or 90 <= c <= 97:
                fg = PALETTE16[c - 30 if c < 90 else c - 82]
            elif 40 <= c <= 47 or 100 <= c <= 107:
                bg = PALETTE16[c - 40 if c < 100 else c - 92]
            i += 1
    return out


def render(lines, cols, rows, path):
    """Save lines (one per screen row) as a PNG of a cols x rows terminal."""
    s, cw, ch = SCALE, CW * SCALE, CH * SCALE
    img = Image.new("RGB", (cols * cw, rows * ch), DEFAULT_BG)
    draw = ImageDraw.Draw(img)
    regular, bold_font, fallback = fonts()
    blank = (" ", DEFAULT_FG, DEFAULT_BG, False)
    for r in range(rows):
        row = cells(lines[r]) if r < len(lines) else []
        for c in range(cols):
            char, fg, bg, bold = row[c] if c < len(row) else blank
            x0, y0 = c * cw, r * ch
            draw.rectangle([x0, y0, x0 + cw - 1, y0 + ch - 1], fill=bg)
            if char in ("", " "):
                continue
            if char in QUADS:
                mask, xm, ym = QUADS[char], x0 + cw // 2, y0 + ch // 2
                for bit, (a, b, c2, d) in ((8, (x0, y0, xm, ym)), (4, (xm, y0, x0 + cw, ym)),
                                           (2, (x0, ym, xm, y0 + ch)), (1, (xm, ym, x0 + cw, y0 + ch))):
                    if mask & bit:
                        draw.rectangle([a, b, c2 - 1, d - 1], fill=fg)
                continue
            font = bold_font if bold else regular
            if not _has_glyph(font, char):
                font = fallback
            draw.text((x0 + cw / 2, y0 + ch * 0.52), char, font=font, fill=fg, anchor="mm")
    img.save(path, optimize=True)
    return img
