"""The hole, drawn from above: the real shapes of its fairway, green, bunkers,
water and trees, with the flag, the balls and their flights on top.

A HoleMap turns the course so the hole runs across the screen (tee on the
left) or up it (tee at the bottom), whichever shows it bigger, and fills
everything around it too. Each text cell is four pixels in high detail (two
across, two down, drawn with quadrant blocks in the two colors that fit them
best) or two in standard detail (half blocks), for terminals whose fonts
lack the quadrants.

The terrain is drawn once per view. Shapes are filled a pixel row at a time
(scanlines), lowest-priority ground first, so what's on top matches what the
simulation says the ground is (course.PRIORITY). Then it's shaded: mowing
stripes, a first cut of rough round the fairways and a fringe round the
greens, bunker lips, shallows at the water's edge, tree canopies and their
shadows, and a soft texture in the rough, the woods and the water.

Where the course's heights are known, its slopes are shaded too (relief):
in a few hard steps, so the hills and banks show without blurring the pixels,
lit from the top left like the shadows; and on a close-up of a green, the
putting surface gets contour lines, CONTOUR apart (CUP_CONTOUR on the cup's),
to read the break by.

Closer views are drawn bigger than the screen, so a camera can follow the ball
about them (aim()): the fairway camera's covers the whole hole, the green's
the green and round it, the cup's a few yards round the hole (short putts). Each frame only redraws the rows with something on
them, or that the camera has moved.
"""

import itertools
import math
import threading

from .course import TEE_RGB
from .geo import Frame, bbox, dist, heading, lerp, step
from .term import BOLD, RESET

# Painted in this order, so later ground covers earlier: under the course's
# own ground, the reverse of course.PRIORITY; lawns only go where the course
# isn't, and buildings over everything.
LAYERS = ("lawn", "rough", "flowers", "heath", "trees", "hedge", "parking", "road", "path", "fairway", "tee",
          "green", "bunker", "water", "pool", "building")
PALETTE = {
    "ob": (52, 74, 48), "lawn": (74, 120, 58), "rough": (68, 128, 50), "cut": (78, 146, 58),
    "fairway": (88, 166, 66), "fringe": (106, 190, 82), "green": (124, 212, 100), "tee": (100, 182, 80),
    "bunker": (226, 208, 158), "water": (44, 108, 192), "sea": (30, 82, 158), "pool": (96, 200, 232),
    "trees": (26, 68, 36), "trees2": (32, 78, 40), "pines": (16, 52, 42), "shrub": (40, 84, 42),
    "hedge": (34, 82, 38), "heath": (86, 98, 50), "flowers": (40, 86, 40), "path": (140, 140, 128),
    "road": (74, 74, 78), "parking": (98, 98, 102), "stake": (245, 245, 245),
    "b0": (150, 150, 154), "b1": (178, 98, 72), "b2": (124, 94, 74), "b3": (92, 102, 116), "b4": (204, 198, 186),
}
SHALLOWS = (92, 150, 212)
BLOOMS = ((232, 92, 152), (250, 172, 204), (250, 250, 250), (214, 58, 92))   # azaleas and the like
GORSE = (222, 198, 64)
# The 16-color palette has two greens: make the ground tell apart anyway.
PALETTE16 = {
    "ob": (0, 0, 0), "lawn": (0, 170, 0), "rough": (0, 170, 0), "cut": (0, 170, 0), "fairway": (85, 255, 85),
    "fringe": (85, 255, 85), "green": (85, 255, 255), "tee": (85, 255, 85), "bunker": (255, 255, 85),
    "water": (0, 0, 170), "sea": (0, 0, 170), "pool": (85, 255, 255), "trees": (0, 0, 0), "trees2": (0, 0, 0),
    "pines": (0, 0, 0), "shrub": (0, 0, 0), "hedge": (0, 0, 0), "heath": (170, 85, 0), "flowers": (255, 85, 255),
    "path": (170, 170, 170), "road": (85, 85, 85), "parking": (85, 85, 85), "stake": (255, 255, 255),
    "b0": (170, 170, 170), "b1": (170, 0, 0), "b2": (170, 85, 0), "b3": (85, 85, 85), "b4": (255, 255, 255),
}
TREES = ("trees", "trees2", "pines", "shrub", "hedge")   # kinds of tree, for the shading
BUILDINGS = ("b0", "b1", "b2", "b3", "b4")
CANOPY_ON = ("rough", "ob", "lawn", "heath") + TREES   # what a single tree's canopy can cover
FLAG = (235, 50, 50)
STRIPE = 10.0        # yards between the fairway's mowing stripes
GREEN_STRIPE = 4.0   # and the green's
CUT = 2.2            # yards of first cut round a fairway
FRINGE = 1.4         # yards of fringe round a green
SHORE = 1.2          # yards of shallows at the water's edge
SHADOW = 2.5         # yards a tree's shadow reaches (the sun is up and to the left)
HOUSE_SHADOW = 4.0   # and a building's
STAKE = 9.0          # yards between the white stakes along the course's boundary
CROWN = 4.0          # yards between the crowns of a wood's trees
MARKER_GAP = 2.5     # yards either side of a teeing ground's middle its markers stand
MARGIN_SIDE = 40.0   # yards shown either side of the hole
MARGIN_BACK = 15.0   # behind the tee
MARGIN_LONG = 25.0   # beyond the green
FAIRWAY_ZOOM = 2.5   # how much closer the fairway camera is than the whole hole
CLOSE_SPAN = 34.0    # yards a close-up shows across the screen's shorter side
CLOSE_WORLD = 1.7    # how much bigger than the screen a close-up is drawn, for the camera to move about in
CUP_SPAN = 7.0       # yards the cup's close-up shows across the screen's shorter side
CONTOUR = 0.11       # yards (4 inches) of height between a green's contour lines
CUP_CONTOUR = 0.056  # and (2 inches) on the cup's close-up, where a short putt's break is a matter of inches
CONTOUR_SHADE = 0.9  # how dark they are
SLOPE_SPAN = (1.2, 3.0)   # yards either side a slope is measured over: close up, and farther off
SLOPE_GAIN = (2.5, 2.0)   # and how much it's made of, to show
SLOPE_STEPS = (0.06, 0.2)  # how much lighter or darker than flat ground a slope must be for each step of shade
SLOPE_SHADE = 0.07   # what a step of shade is
SUN_UP = math.radians(40)
UNSHADED = ("water", "sea", "pool", "stake") + ("trees", "trees2", "pines", "shrub", "hedge") + \
    ("b0", "b1", "b2", "b3", "b4")   # flat, or too busy to shade
# Quadrant characters by which of a cell's pixels take the foreground color:
# top left 8, top right 4, bottom left 2, bottom right 1.
QUADS = {8: "▘", 4: "▝", 2: "▖", 1: "▗", 12: "▀", 3: "▄", 10: "▌", 5: "▐", 9: "▚", 6: "▞", 14: "▛",
         13: "▜", 11: "▙", 7: "▟", 15: "█"}

CLOSE_LEVELS = ("green", "cup")   # the close-ups: labelled balls, contour lines
LOOK_WORLD = 1.6     # how much bigger than the screen a look-around map is drawn, each way (it's drawn round
                     # points half a screen apart: the screen's always on the nearest one's)
POINTERS = "→↘↓↙←↖↑↗"   # toward something off the screen, clockwise from right

_cache = {}          # terrain per view, see HoleMap.terrain (for another map of the same view)
_CACHE_MAX = 8
_cache_lock = threading.Lock()   # maps are made on a worker thread and on the screen's


class HoleMap:
    """A hole, or part of it, in cols x rows cells.

    aspect: a half-block pixel's width over its height (Terminal.pixel_aspect).
    way: "across", "up", or "auto" for whichever shows the hole bigger (a
    closer view should be turned like its hole's map: pass that one's way).
    quad: high detail, four pixels to a cell.
    """

    def __init__(self, course, hole, cols, rows, aspect=1.0, way="auto", level="hole", quad=True, relief="on",
                 center=None, scale=None):
        """level: "hole", the whole hole on screen; "fairway", closer, drawn
        the whole hole's size for the camera to follow the ball down it; or
        "green", a close-up of the green, drawn a little bigger than the screen;
        or "cup", closer still round the hole, for short putts; or "look",
        `scale` yards a pixel round the course point `center`, drawn
        LOOK_WORLD times the screen each way (looking around, l).
        relief: "on", the slopes shaded and the greens' contours drawn;
        "greens only", just the contours; "off", neither."""
        self.course, self.hole, self.level, self.relief = course, hole, level, relief
        self.cols, self.rows = max(1, cols), max(1, rows)
        self.px = 2 if quad else 1                # pixels across a cell (always two down)
        vw, vh = self.cols * self.px, self.rows * 2
        pa = aspect / self.px                     # a pixel's width over its height
        self.frame = Frame(hole.tee, heading(hole.tee, hole.pin))
        g = hole.green.box
        green = [(g[0], g[1]), (g[0], g[3]), (g[2], g[1]), (g[2], g[3]), hole.pin]
        if level == "look":
            self.way = way if way in ("across", "up") else "across"
            self.k = scale
            self.wc, self.wr = math.ceil(self.cols * LOOK_WORLD), math.ceil(self.rows * LOOK_WORLD)
            a0, c0 = a1, c1 = self.frame.to_frame(center)
        elif level in CLOSE_LEVELS:
            self.way = way if way in ("across", "up") else "across"
            self.k = (CLOSE_SPAN if level == "green" else CUP_SPAN) / min(pa * vw, vh)
            self.wc, self.wr = math.ceil(self.cols * CLOSE_WORLD), math.ceil(self.rows * CLOSE_WORLD)
            a0, c0, a1, c1 = bbox([self.frame.to_frame(p) for p in (green if level == "green" else [hole.pin])])
        else:
            a0, c0, a1, c1 = bbox([self.frame.to_frame(p) for p in list(hole.path) + green])
            a0, a1 = a0 - MARGIN_BACK, a1 + MARGIN_LONG
            c0, c1 = c0 - MARGIN_SIDE, c1 + MARGIN_SIDE
            # Yards per pixel down the screen (k), and k * pa across it, so
            # the shapes come out square on screen.
            fits = {"across": max((a1 - a0) / (pa * vw), (c1 - c0) / vh),
                    "up": max((c1 - c0) / (pa * vw), (a1 - a0) / vh)}
            self.way = way if way in fits else min(fits, key=fits.get)
            self.k = fits[self.way]
            self.wc, self.wr = self.cols, self.rows
            if level == "fairway":
                self.k /= FAIRWAY_ZOOM
                long_, wide = (a1 - a0, c1 - c0) if self.way == "across" else (c1 - c0, a1 - a0)
                self.wc = max(self.cols, math.ceil(long_ / (self.k * pa) / self.px))
                self.wr = max(self.rows, math.ceil(wide / self.k / 2))
        self.kx = self.k * pa
        self.w, self.h = self.wc * self.px, self.wr * 2   # the whole drawing, in pixels
        mid_a, mid_c = (a0 + a1) / 2, (c0 + c1) / 2
        if self.way == "across":   # along the hole is screen x, its right side screen y (down)
            self.a_left, self.c_top = mid_a - self.kx * self.w / 2, mid_c - self.k * self.h / 2
        else:                      # along the hole is up the screen, its right side screen x
            self.c_left, self.a_top = mid_c - self.kx * self.w / 2, mid_a + self.k * self.h / 2
        self.off = ((self.wc - self.cols) // 2, (self.wr - self.rows) // 2)   # the camera: the screen's top left cell
        # Close enough to read a green by: the balls labelled, the contours drawn.
        self.close = level in CLOSE_LEVELS or level == "look" and self.k <= CLOSE_SPAN / min(pa * vw, vh) * 1.5
        # And close enough for the cup's finer contours.
        self.fine = level == "cup" or level == "look" and self.k <= CUP_SPAN / min(pa * vw, vh) * 2
        self._rows = {}            # (row, column offset, color mode) -> that row drawn without marks
        self._terrain = {}         # id(palette) -> terrain()

    # ----------------------------------------------------------------------
    # Course yards <-> pixels and cells

    def to_pixel(self, p):
        a, c = self.frame.to_frame(p)
        if self.way == "across":
            return ((a - self.a_left) / self.kx, (c - self.c_top) / self.k)
        return ((c - self.c_left) / self.kx, (self.a_top - a) / self.k)

    def to_course(self, x, y):
        if self.way == "across":
            return self.frame.to_course(self.a_left + x * self.kx, self.c_top + y * self.k)
        return self.frame.to_course(self.a_top - y * self.k, self.c_left + x * self.kx)

    def cell(self, p):
        """The (col, row) on screen a course point is drawn in, or None if off it."""
        x, y = self.to_pixel(p)
        col, row = int(math.floor(x / self.px)) - self.off[0], int(math.floor(y / 2)) - self.off[1]
        if 0 <= col < self.cols and 0 <= row < self.rows:
            return col, row
        return None

    def contains(self, p):
        """Whether p is on screen."""
        return self.cell(p) is not None

    def covers(self, p):
        """Whether p is in the drawing, on screen or where the camera can go."""
        x, y = self.to_pixel(p)
        return 0 <= x < self.w and 0 <= y < self.h

    def aim(self, p):
        """Move the camera to center the screen on course point p, as far as
        the drawing goes."""
        x, y = self.to_pixel(p)
        col = round(x / self.px - self.cols / 2)
        row = round(y / 2 - self.rows / 2)
        self.off = (max(0, min(self.wc - self.cols, col)), max(0, min(self.wr - self.rows, row)))

    def center(self):
        """The course point in the middle of the screen, where the camera is."""
        return self.to_course((self.off[0] + self.cols / 2) * self.px, (self.off[1] + self.rows / 2) * 2)

    def fits(self, points, margin=1):
        """Whether the camera can show all the course points at once, `margin`
        cells in from the screen's edges: they're all in the drawing, and no
        farther apart than the screen less its margins."""
        xy = [self.to_pixel(p) for p in points]
        if not all(0 <= x < self.w and 0 <= y < self.h for x, y in xy):
            return False
        xs, ys = [x for x, _ in xy], [y for _, y in xy]
        return (max(xs) - min(xs)) / self.px <= self.cols - 1 - 2 * margin and \
            (max(ys) - min(ys)) / 2 <= self.rows - 1 - 2 * margin

    def middle(self, points):
        """The course point in the middle of the points as they're drawn: aimed
        at, the camera shows them all if they fit."""
        xy = [self.to_pixel(p) for p in points]
        xs, ys = [x for x, _ in xy], [y for _, y in xy]
        return self.to_course((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)

    def _edge(self, p):
        """(col, row, arrow): where on the screen's edge to point toward an off-screen course point."""
        x, y = self.to_pixel(p)
        dx = x / self.px - self.off[0] - self.cols / 2
        dy = y / 2 - self.off[1] - self.rows / 2
        # Its direction as it looks: a cell is k * 2 yards tall and kx * px wide.
        arrow = POINTERS[int((math.degrees(math.atan2(dy * 2 * self.k, dx * self.px * self.kx)) % 360 + 22.5)
                             // 45) % 8]
        t = min((self.cols / 2 - 0.5) / abs(dx) if dx else 1e9, (self.rows / 2 - 0.5) / abs(dy) if dy else 1e9)
        col = max(0, min(self.cols - 1, math.floor(self.cols / 2 + dx * t)))
        row = max(0, min(self.rows - 1, math.floor(self.rows / 2 + dy * t)))
        return col, row, arrow

    def pixel_yards(self):
        """The smaller of a pixel's width and height, in yards."""
        return min(self.k, self.kx)

    # ----------------------------------------------------------------------
    # Terrain

    def terrain(self, palette):
        """(pixel colors, cells): rows of (r, g, b), and rows of (character,
        foreground, background) cells drawing them. Made once per view."""
        key = (id(self.course), id(self.hole), self.wc, self.wr, self.px, self.way, round(self.k, 6),
               round(self.kx, 6), self.level, id(palette), self.relief, self.to_course(0, 0))
        mine = self._terrain.get(id(palette))
        if mine is not None:
            return mine
        with _cache_lock:
            hit = _cache.get(key)
        # The ids are only good while the course and hole are alive: a new one can be given an old one's.
        if hit is not None and hit[0] is self.course and hit[1] is self.hole:
            made = hit[2]
        else:
            pixels = self._paint(self._kinds(), palette)
            cells = [[self._cell(pixels, x, y) for x in range(self.wc)] for y in range(self.wr)]
            made = ([_Row(line) for line in pixels], cells)
            with _cache_lock:
                _cache.pop(key, None)
                while len(_cache) >= _CACHE_MAX:
                    _cache.pop(next(iter(_cache)), None)
                _cache[key] = (self.course, self.hole, made)
        self._terrain[id(palette)] = made   # kept with the map: it's drawn again every frame it's shown
        return made

    def prepare(self, colors):
        """Draw the terrain now (it's otherwise drawn on the first frame that shows it)."""
        self.terrain(PALETTE16 if colors.mode == "16" else PALETTE)

    def _cell(self, pixels, x, y, paint=None):
        """The cell drawing the pixels at cell (x, y), with `paint` ({(px, py): rgb}) over them."""
        px = self.px
        spots = [(px * x + i, 2 * y + j) for j in (0, 1) for i in range(px)]
        colors = [paint.get(s, pixels[s[1]][s[0]]) if paint else pixels[s[1]][s[0]] for s in spots]
        if px == 1:
            top, bottom = colors
            return (" ", None, top) if top == bottom else ("▀", top, bottom)
        return _fit4(colors)

    def _kinds(self):
        """Each pixel's ground, by filling the shapes in layer order. A lit
        part of a tree's canopy is its kind with a "*"."""
        w, h = self.w, self.h
        course = self.course
        grid = [["rough"] * w for _ in range(h)]
        if course.boundary is not None:
            grid = [["ob"] * w for _ in range(h)]
            self._fill(grid, [self._ring(r) for r in course.boundary.rings], "rough")
        corners = [self.to_course(0, 0), self.to_course(w, 0), self.to_course(0, h), self.to_course(w, h)]
        vx0, vy0, vx1, vy1 = bbox(corners)
        near = lambda box: not (box[2] < vx0 or box[0] > vx1 or box[3] < vy0 or box[1] > vy1)
        if course.coast:
            self._sea(grid, near)
        for layer in LAYERS:
            for f in course.features:
                if f.kind != layer or not near(f.box):
                    continue
                kind = {"trees": "pines" if f.leaf == "needle" else "trees", "building": BUILDINGS[f.tone % 5]}.get(
                    layer, layer)
                only = ("ob",) if layer == "lawn" else None   # lawns round the course, not over it
                if f.poly is not None:
                    self._fill(grid, [self._ring(r) for r in f.poly.rings], kind, only)
                else:
                    for quad in _buffer(f.line, f.width):
                        self._fill(grid, [self._ring(quad)], kind, only)
            if layer == "trees" and course.cover is not None:
                self._cover(grid)
            if layer == "trees":   # single trees: under the fairways, like the lookups
                for t in course.trees:
                    if vx0 - t.r <= t.x <= vx1 + t.r and vy0 - t.r <= t.y <= vy1 + t.r:
                        kind = {"needle": "pines", "shrub": "shrub"}.get(t.leaf) or \
                            ("trees2" if _hash(round(t.x * 7), round(t.y * 7)) < 0.5 else "trees")
                        self._disc(grid, (t.x, t.y), t.r, kind)
        if course.boundary is not None:
            self._stakes(grid)
        self._markers(grid)
        return grid

    def _markers(self, grid):
        """Each teeing ground's two markers, in its color, either side of its middle."""
        for hole in self.course.holes:
            if not hole.tees:
                continue
            b = heading(hole.path[0], hole.pin)
            for tee in hole.tees:
                for side in (-1, 1):
                    x, y = self.to_pixel(step(tee.pos, b + 90 * side, MARKER_GAP))
                    x, y = int(math.floor(x)), int(math.floor(y))
                    if 0 <= x < self.w and 0 <= y < self.h:
                        grid[y][x] = "mark:" + tee.color

    def _cover(self, grid):
        """The woods the land cover has, where nothing's mapped (as course.wooded)."""
        wooded = self.course.wooded
        for y in range(self.h):
            row = grid[y]
            for x in range(self.w):
                if row[x] in ("rough", "ob", "lawn") and wooded(self.to_course(x + 0.5, y + 0.5)):
                    row[x] = "trees"

    def _sea(self, grid, near):
        """The sea, from the coastlines (which have the land on their left):
        the regions of the drawing the coast cuts it into, each on one side."""
        w, h = self.w, self.h
        segments, wall = [], set()
        for line in self.course.coast:
            for a, b in zip(line, line[1:]):
                if not near(bbox([a, b])):
                    continue
                pa, pb = self.to_pixel(a), self.to_pixel(b)
                segments.append((a, b, pa, pb))
                n = int(max(abs(pb[0] - pa[0]), abs(pb[1] - pa[1])) * 2) + 1
                for k in range(n + 1):
                    x = int(math.floor(pa[0] + (pb[0] - pa[0]) * k / n))
                    y = int(math.floor(pa[1] + (pb[1] - pa[1]) * k / n))
                    if 0 <= x < w and 0 <= y < h:
                        wall.add(y * w + x)
        if not wall:
            return   # the coast is off the drawing: no telling which side it's on
        region = [0] * (w * h)
        for start in range(w * h):
            if region[start] or start in wall:
                continue
            label = start + 1
            region[start] = label
            todo, members, shore = [start], [], []
            while todo:
                i = todo.pop()
                members.append(i)
                x = i % w
                for j in (i - w, i + w, i - 1 if x else -1, i + 1 if x + 1 < w else -1):
                    if 0 <= j < w * h and not region[j]:
                        if j in wall:
                            if len(shore) < 9:
                                shore.append(i)
                        else:
                            region[j] = label
                            todo.append(j)
            votes = 0
            for i in shore:   # which side of the nearest bit of coast it's on
                px, py = i % w + 0.5, i // w + 0.5
                a, b, pa, pb = min(segments, key=lambda s: _seg_d2((px, py), s[2], s[3]))
                p = self.to_course(px, py)
                votes += 1 if (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) < 0 else -1
            if votes > 0:
                for i in members:
                    if grid[i // w][i % w] == "ob":
                        grid[i // w][i % w] = "sea"

    def _stakes(self, grid):
        """White stakes along the course's boundary, out of the sea."""
        for ring in self.course.boundary.rings:
            for a, b in zip(ring, ring[1:] + ring[:1]):
                d = dist(a, b)
                for k in range(int(d // STAKE) + 1):
                    x, y = self.to_pixel(lerp(a, b, k * STAKE / d) if d else a)
                    x, y = int(math.floor(x)), int(math.floor(y))
                    if 0 <= x < self.w and 0 <= y < self.h and grid[y][x] in ("rough", "ob", "lawn"):
                        grid[y][x] = "stake"

    def _ring(self, ring):
        return [self.to_pixel(p) for p in ring]

    def _fill(self, grid, rings, kind, only=None):
        """Fill pixels whose centers are inside the rings (even-odd), if
        they're of a kind in `only` (any kind without it)."""
        ys = [p[1] for r in rings for p in r]
        if not ys:
            return
        y0, y1 = max(0, int(math.floor(min(ys)))), min(self.h - 1, int(math.ceil(max(ys))))
        edges = [(r[i - 1], r[i]) for r in rings for i in range(len(r))]
        for y in range(y0, y1 + 1):
            cy = y + 0.5
            xs = sorted((a[0] + (cy - a[1]) * (b[0] - a[0]) / (b[1] - a[1]))
                        for a, b in edges if (a[1] > cy) != (b[1] > cy))
            row = grid[y]
            for i in range(0, len(xs) - 1, 2):
                x0 = max(0, int(math.ceil(xs[i] - 0.5)))
                x1 = min(self.w - 1, int(math.ceil(xs[i + 1] - 0.5)) - 1)
                for x in range(x0, x1 + 1):
                    if only is None or row[x] in only:
                        row[x] = kind

    def _disc(self, grid, center, r, kind):
        """A tree's canopy, on rough or round the course (not on a fairway),
        lit on its upper left."""
        cx, cy = self.to_pixel(center)
        rx, ry = max(r / self.kx, 0.5), max(r / self.k, 0.5)
        for y in range(max(0, int(cy - ry)), min(self.h - 1, int(cy + ry)) + 1):
            for x in range(max(0, int(cx - rx)), min(self.w - 1, int(cx + rx)) + 1):
                dx, dy = (x + 0.5 - cx) / rx, (y + 0.5 - cy) / ry
                if dx * dx + dy * dy <= 1 and grid[y][x].rstrip("*") in CANOPY_ON:
                    grid[y][x] = kind + ("*" if dx + dy < -0.45 else "")

    def _single_trees(self, w, h):
        """The pixels under single trees' canopies (they're shaded as one crown each)."""
        out = set()
        for t in self.course.trees:
            cx, cy = self.to_pixel((t.x, t.y))
            rx, ry = max(t.r / self.kx, 0.5), max(t.r / self.k, 0.5)
            if -rx <= cx < w + rx and -ry <= cy < h + ry:
                for y in range(max(0, int(cy - ry)), min(h - 1, int(cy + ry)) + 1):
                    for x in range(max(0, int(cx - rx)), min(w - 1, int(cx + rx)) + 1):
                        out.add(y * w + x)
        return out

    def _paint(self, kinds, palette):
        """The pixels' colors: the ground's, shaded."""
        if palette is PALETTE16:
            return [[TEE_RGB[k[5:]] if k.startswith("mark:") else palette[k.rstrip("*")] for k in row]
                    for row in kinds]
        w, h = self.w, self.h
        flat = [k for row in kinds for k in row]
        steps = lambda yards: max(1, round(yards / ((self.k + self.kx) / 2)))
        wet = ("water", "sea")
        fringe = _spread(flat, w, h, ("green",), ("fairway", "rough", "tee", "trees", "trees2"), steps(FRINGE))
        cut = _spread(flat, w, h, ("fairway",), ("rough",), steps(CUT))
        shallows = _spread(flat, w, h, tuple(k for k in PALETTE if k not in wet), wet, steps(SHORE))
        lips = _edges(flat, w, h, ("bunker",))
        canopy = _edges(flat, w, h, TREES + tuple(k + "*" for k in TREES))
        roofs = _edges(flat, w, h, BUILDINGS)
        decks = _edges(flat, w, h, ("pool",))
        tall = set(TREES) | {k + "*" for k in TREES}
        single = self._single_trees(w, h)
        near = {}   # a crown's cell -> the crowns in it and round it: (a, c, shade)

        def crowns(a, c):
            """A wood's canopy from above at (a, c): bright at a crown's top,
            dark in the gaps, each crown a shade of its own."""
            cell = (math.floor(a / CROWN), math.floor(c / CROWN))
            spots = near.get(cell)
            if spots is None:
                ca, cc = cell
                spots = near[cell] = [((i + 0.2 + 0.6 * _hash(i, j)) * CROWN, (j + 0.2 + 0.6 * _hash(j + 7, i)) * CROWN,
                                       0.88 + 0.24 * _hash(i + 3, j + 5))
                                      for i in (ca - 1, ca, ca + 1) for j in (cc - 1, cc, cc + 1)]
            best, tone = 9.0 * CROWN * CROWN, 1.0
            for sa, sc, shade in spots:
                d = (a - sa) * (a - sa) + (c - sc) * (c - sc)
                if d < best:
                    best, tone = d, shade
            best /= CROWN * CROWN
            return (0.62 + 0.5 * (1 - best * 1.6 if best < 0.625 else 0.0)) * tone

        def reach(yards):
            sx, sy = max(1, round(yards / self.kx)), max(1, round(yards / self.k))
            n = max(sx, sy)
            return [(round(t * sx / n), round(t * sy / n)) for t in range(1, n + 1)]
        # What's in a tree's or a building's shadow: down and to the right of it, as far as it reaches.
        shadowed = bytearray(w * h)
        for spots, casts in ((reach(SHADOW), tall), (reach(HOUSE_SHADOW), set(BUILDINGS))):
            for i, k in enumerate(flat):
                if k in casts:
                    x, y = i % w, i // w
                    for dx, dy in spots:
                        if x + dx < w and y + dy < h:
                            shadowed[i + dy * w + dx] = 1
        relief = self._relief(flat)
        across = self.way == "across"
        out = []
        for y in range(h):
            line = []
            for x in range(w):
                i = y * w + x
                kind = flat[i]
                lit = kind.endswith("*")
                kind = kind.rstrip("*")
                if across:
                    a, c = self.a_left + (x + 0.5) * self.kx, self.c_top + (y + 0.5) * self.k
                else:
                    a, c = self.a_top - (y + 0.5) * self.k, self.c_left + (x + 0.5) * self.kx
                if i in fringe:
                    kind = "fringe"
                elif i in cut:
                    kind = "cut"
                if kind.startswith("mark:"):
                    line.append(TEE_RGB[kind[5:]])
                    continue
                r, g, b = palette[kind]
                f = 1.0
                if kind in ("fairway", "tee"):
                    f = 1.0 if int(math.floor(a / STRIPE)) & 1 else 0.9
                elif kind == "green":
                    f = 1.0 if int(math.floor(a / GREEN_STRIPE)) & 1 else 0.95
                elif kind in ("rough", "cut", "ob", "lawn"):
                    f = 0.94 + 0.1 * _noise(a, c, 3.0)
                elif kind in TREES:
                    if lit:          # a single tree's sunlit side
                        f = 1.2
                    elif i in single:
                        f = 0.92
                    else:            # a wood: crown after crown, dark between them
                        f = crowns(a, c)
                    f *= 0.72 if i in canopy else 1.0
                elif kind == "bunker":
                    f = (0.95 + 0.07 * _hash(x, y)) * (0.84 if i in lips else 1.0)
                elif kind in wet:
                    f = 0.93 + 0.12 * _noise(a, c, 5.0 if kind == "water" else 9.0)
                    if i in shallows:
                        r, g, b = SHALLOWS
                elif kind == "flowers":
                    n = _hash(x, y)
                    if n < 0.4:
                        r, g, b = BLOOMS[int(n * 10) % len(BLOOMS)]
                elif kind == "heath":
                    f = 0.85 + 0.25 * _noise(a, c, 2.0)
                    if _hash(x, y) < 0.14:
                        r, g, b = GORSE
                elif kind in ("road", "parking"):
                    f = 0.95 + 0.08 * _hash(x, y)
                elif kind == "pool" and i in decks:
                    r, g, b = (226, 226, 214)
                elif kind in BUILDINGS and i in roofs:
                    up = y and flat[i - w] not in BUILDINGS or x and flat[i - 1] not in BUILDINGS
                    f = 1.18 if up else 0.72   # lit on the sun's side, dark on the other
                if relief is not None and not lit:
                    f *= relief(i, kind)
                if shadowed[i] and kind not in tall and kind not in wet and kind not in BUILDINGS and kind != "stake":
                    f *= 0.7   # in a shadow
                if f > 1:
                    line.append((min(255, int(r * f)), min(255, int(g * f)), min(255, int(b * f))))
                else:
                    line.append((int(r * f), int(g * f), int(b * f)))
            out.append(line)
        return out

    def _relief(self, flat):
        """f(pixel index, its kind): how much the ground's slope there lightens
        or darkens it; None for no relief."""
        ground = self.course.ground
        if ground is None or self.relief not in ("on", "greens only"):
            return None
        w, h = self.w, self.h
        close = self.close
        apart = CUP_CONTOUR if self.fine else CONTOUR
        if self.relief == "greens only" and not close:
            return None
        zs = [ground.z(self.to_course(x + 0.5, y + 0.5)) for y in range(h) for x in range(w)]
        span, gain = SLOPE_SPAN[0 if close else 1], SLOPE_GAIN[0 if close else 1]
        sx, sy = max(1, round(span / self.kx)), max(1, round(span / self.k))
        lx, ly, lz = -math.cos(SUN_UP) / math.sqrt(2), -math.cos(SUN_UP) / math.sqrt(2), math.sin(SUN_UP)
        low, high = SLOPE_STEPS

        def contour(i):
            band = math.floor(zs[i] / apart)
            x, y = i % w, i // w
            return any(math.floor(zs[j] / apart) < band for j, ok in
                       ((i + 1, x + 1 < w), (i - 1, x > 0), (i + w, y + 1 < h), (i - w, y > 0)) if ok)

        def shade(i, kind):
            if kind in UNSHADED or kind.startswith("mark:"):
                return 1.0
            if close and kind == "green":
                return CONTOUR_SHADE if contour(i) else 1.0
            if self.relief != "on":
                return 1.0
            x, y = i % w, i // w
            x0, x1, y0, y1 = max(0, x - sx), min(w - 1, x + sx), max(0, y - sy), min(h - 1, y + sy)
            if x1 == x0 or y1 == y0:
                return 1.0
            gx = (zs[y * w + x1] - zs[y * w + x0]) / ((x1 - x0) * self.kx) * gain   # rise a yard, right
            gy = (zs[y1 * w + x] - zs[y0 * w + x]) / ((y1 - y0) * self.k) * gain    # and down
            lit = (-gx * lx - gy * ly + lz) / math.sqrt(gx * gx + gy * gy + 1) - lz
            steps = (lit > low) + (lit > high) - (lit < -low) - (lit < -high)
            return 1 + SLOPE_SHADE * steps
        return shade

    # ----------------------------------------------------------------------
    # Drawing

    def lines(self, colors, marks=(), labels=(), box=None, tints=(), pointers=()):
        """The rows on screen, where the camera is: terrain, with `tints`
        (course point, rgb, strength 0-1) coloring single pixels (the trails
        and shadows), `marks` on top, each (course point, character, rgb, bold)
        or with a fifth, rows to draw it higher up (a ball in the air), later
        ones over earlier; `labels` (course point, text, rgb) written beside
        their point; `pointers` (course point, rgb), an arrow at the screen's
        edge toward each one that's off it (a ball the camera can't show);
        and `box`, (text lines, rgb, background rgb), in the middle of it all."""
        palette = PALETTE16 if colors.mode == "16" else PALETTE
        pixels, cells = self.terrain(palette)
        oc, orow = self.off
        paint = {}   # cell -> {pixel: rgb}
        for p, rgb, strength in tints:
            x, y = self.to_pixel(p)
            x, y = int(math.floor(x)), int(math.floor(y))
            if 0 <= x < self.w and 0 <= y < self.h:
                spots = paint.setdefault((x // self.px, y // 2), {})
                base = spots.get((x, y), pixels[y][x])
                spots[(x, y)] = rgb if colors.mode == "16" else _mix(base, rgb, strength)
        changed = {c: self._cell(pixels, c[0], c[1], spots) for c, spots in paint.items()}
        over = {}
        for p, rgb in pointers:
            if self.cell(p) is None:
                over[self._edge(p)[:2]] = (self._edge(p)[2], rgb, True, None)
        for mark in marks:
            p, ch, rgb, bold = mark[:4]
            c = self.cell(p)
            if c:
                over[(c[0], max(0, c[1] - (mark[4] if len(mark) > 4 else 0)))] = (ch, rgb, bold, None)
        for p, text, rgb in labels:
            c = self.cell(p)
            if not c:
                continue
            col, row = c
            start = col + 2 if col + 2 + len(text) <= self.cols else col - 1 - len(text)
            for i, ch in enumerate(text):
                if 0 <= start + i < self.cols and (start + i, row) not in over:
                    over[(start + i, row)] = (ch, rgb, True, None)
        if box:
            text, rgb, back = box
            w = min(self.cols, max(len(t) for t in text) + 4)
            top = max(0, (self.rows - len(text) - 2) // 2)
            left = max(0, (self.cols - w) // 2)
            for r, t in enumerate([""] + list(text) + [""]):
                if top + r < self.rows:
                    t = (" " + t).ljust(w)[:w]
                    for i, ch in enumerate(t):
                        over[(left + i, top + r)] = (ch, rgb, r == 1, back)
        out = []
        for r in range(self.rows):
            wr = orow + r
            mine = {c - oc: v for (c, rr), v in changed.items() if rr == wr and oc <= c < oc + self.cols}
            ov = {c: v for (c, rr), v in over.items() if rr == r}
            if not mine and not ov:
                key = (wr, oc, colors.mode)
                line = self._rows.get(key)
                if line is None:
                    if len(self._rows) > 4 * self.wr * max(1, self.wc - self.cols + 1):
                        self._rows.clear()
                    line = self._rows[key] = _row(cells[wr][oc:oc + self.cols], {}, colors)
                out.append(line)
                continue
            row = cells[wr][oc:oc + self.cols]
            if mine:
                row = [mine.get(x, cell) for x, cell in enumerate(row)]
            out.append(_row(row, ov, colors))
        return out


class _Row:
    """A row of pixel colors, kept as bytes (r, g, b each, a few megabytes
    less a map than tuples), read back as (r, g, b) tuples."""
    __slots__ = ("b",)

    def __init__(self, colors):
        self.b = bytes(itertools.chain.from_iterable(colors))

    def __getitem__(self, x):
        i, b = 3 * x, self.b
        return (b[i], b[i + 1], b[i + 2])

    def __len__(self):
        return len(self.b) // 3

    def __iter__(self):
        b = self.b
        return ((b[i], b[i + 1], b[i + 2]) for i in range(0, len(b), 3))

    def __eq__(self, other):
        return isinstance(other, _Row) and self.b == other.b


def span_scale(span, cols, rows, aspect):
    """Yards a pixel for a map that shows `span` yards across the screen's
    shorter side (as the close-ups do; the same in either detail)."""
    return span / min(aspect * max(1, cols), max(1, rows) * 2)


def surface(kind):
    """How a ball plays on a drawn kind of ground (as course.surface_at says)."""
    kind = kind.rstrip("*")
    if kind.startswith("mark:"):
        return "tee"
    if kind in TREES or kind in ("flowers", "heath"):
        return "trees"
    if kind in BUILDINGS or kind in ("lawn", "sea", "stake"):
        return "ob"
    if kind in ("fringe", "cut", "path", "road", "parking", "pool"):
        return {"fringe": "fairway", "cut": "rough"}.get(kind, "rough")
    return kind


def _hash(x, y):
    """A steady pseudo-random number in [0, 1) for a pair of integers: texture."""
    n = (x * 374761393 + y * 668265263) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return (n ^ (n >> 16)) / 0x100000000


def _noise(a, c, scale):
    """Smooth value noise in [0, 1) at course-frame yards (a, c), varying over `scale` yards."""
    x, y = a / scale, c / scale
    xi, yi = math.floor(x), math.floor(y)
    fx, fy = x - xi, y - yi
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    top = _hash(xi, yi) + (_hash(xi + 1, yi) - _hash(xi, yi)) * fx
    bottom = _hash(xi, yi + 1) + (_hash(xi + 1, yi + 1) - _hash(xi, yi + 1)) * fx
    return top + (bottom - top) * fy


def _spread(flat, w, h, sources, into, steps):
    """Pixels of the kinds `into` within `steps` pixels of one of `sources`
    (flat: the kinds, a row after another, w wide)."""
    into, sources, n = set(into), set(sources), len(flat)
    frontier, seen = [], set()
    for i, k in enumerate(flat):
        if k in sources:
            x = i % w
            for j in (i - w, i + w, i - 1 if x else -1, i + 1 if x + 1 < w else -1):
                if 0 <= j < n and flat[j] in into and j not in seen:
                    seen.add(j)
                    frontier.append(j)
    for _ in range(steps - 1):
        nxt = []
        for i in frontier:
            x = i % w
            for j in (i - w, i + w, i - 1 if x else -1, i + 1 if x + 1 < w else -1):
                if 0 <= j < n and flat[j] in into and j not in seen:
                    seen.add(j)
                    nxt.append(j)
        frontier = nxt
    return seen


def _edges(flat, w, h, kinds):
    """Pixels of the kinds next to a pixel of another kind."""
    kinds = set(kinds)
    out = set()
    n = len(flat)
    for i, k in enumerate(flat):
        if k in kinds:
            x = i % w
            for j in (i - w, i + w, i - 1 if x else -1, i + 1 if x + 1 < w else -1):
                if 0 <= j < n and flat[j] not in kinds:
                    out.add(i)
                    break
    return out


def _seg_d2(p, a, b):
    """Squared distance from p to the segment a-b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy or 1)))
    x, y = a[0] + t * dx - p[0], a[1] + t * dy - p[1]
    return x * x + y * y


def _fit4(px):
    """The quadrant cell that best draws four pixels (top left, top right,
    bottom left, bottom right) in two colors: the split of them into two
    groups that leaves the least squared error round each group's mean
    (which is the split whose groups' sums, squared over their sizes, add up
    to the most)."""
    a, b, c, d = px
    if a == b and a == c and a == d:
        return (" ", None, a)
    tr, tg, tb = a[0] + b[0] + c[0] + d[0], a[1] + b[1] + c[1] + d[1], a[2] + b[2] + c[2] + d[2]
    best, mask, fg = -1.0, 0, None
    # Splits with the top left pixel in the foreground group: its mask, and that group's pixels.
    for m, group in ((8, (a,)), (12, (a, b)), (10, (a, c)), (9, (a, d)), (14, (a, b, c)), (13, (a, b, d)),
                     (11, (a, c, d))):
        n = len(group)
        sr = sg = sb = 0
        for p in group:
            sr += p[0]
            sg += p[1]
            sb += p[2]
        ur, ug, ub = tr - sr, tg - sg, tb - sb
        score = (sr * sr + sg * sg + sb * sb) / n + (ur * ur + ug * ug + ub * ub) / (4 - n)
        if score > best:
            best, mask, fg = score, m, (sr // n, sg // n, sb // n, ur // (4 - n), ug // (4 - n), ub // (4 - n))
    return (QUADS[mask], fg[:3], fg[3:])


def _mix(a, b, f):
    return (round(a[0] + (b[0] - a[0]) * f), round(a[1] + (b[1] - a[1]) * f), round(a[2] + (b[2] - a[2]) * f))


def _intensity(code, cur):
    """Color escape `code` to follow `cur`. The Linux console takes a bright
    16-color foreground (9x) as bold plus the dark color, and the bold stays:
    a dark color after it (3x) needs normal intensity (22) too."""
    if isinstance(cur, str) and cur.startswith("\x1b[9") and len(code) == 5 and code[2] == "3":
        return "\x1b[22;" + code[2:]
    return code


def _row(cells, over, colors):
    """One text row from its cells, with those in `over` (col -> (character,
    rgb, bold, background or None)) written over the ground's color."""
    fg, bg = colors.fg, colors.bg
    out, cur_fg, cur_bg = [], None, None
    for x, (ch, f, b) in enumerate(cells):
        if x in over:
            mark, rgb, bold, back = over[x]
            ground = back or (b if f is None else _mix(f, b, 0.5))
            out.append(RESET + (BOLD if bold else "") + fg(rgb) + bg(ground) + mark + RESET)
            cur_fg = cur_bg = None
            continue
        if f is not None:
            code = fg(f)
            if code != cur_fg:
                out.append(_intensity(code, cur_fg))
                cur_fg = code
        code = bg(b)
        if code != cur_bg:
            out.append(code)
            cur_bg = code
        out.append(ch)
    return "".join(out) + RESET


def _buffer(line, width):
    """Quads covering a line `width` yards wide."""
    h = width / 2
    out = []
    for a, b in zip(line, line[1:]):
        d = dist(a, b)
        if d == 0:
            continue
        nx, ny = -(b[1] - a[1]) / d * h, (b[0] - a[0]) / d * h
        out.append([(a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny), (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)])
    return out
