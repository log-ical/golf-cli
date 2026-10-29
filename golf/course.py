"""A golf course: its holes, and what the ground is anywhere on it.

The ground is one of SURFACES. Where mapped shapes overlap, the first kind in
PRIORITY wins (a bunker cut into a fairway is a bunker); anywhere inside the
course's boundary that isn't mapped is rough, and outside it is out of bounds.

A course also has what's mapped around it, which is only drawn: houses,
roads, lawns, parking, pools and the sea. Some features play as another
surface (SURFACE_OF): flowerbeds, gorse and hedges as trees, a building
(the clubhouse) as out of bounds.

Where its heights are known, a green tilted steeper than a green is built is
eased back (elevation.Ground.tame), and a pin on a slope steeper than
PIN_SLOPE is moved to the flattest spot on its green, as greenkeepers cut
the cup where a ball can stop near it.
"""

import collections
import dataclasses
import math

from .elevation import Ground
from .geo import Polygon, Projection, bbox, dist, edge_distance, path_length, project_onto, segment_distance

SURFACES = ("tee", "fairway", "green", "rough", "bunker", "water", "trees", "ob")
PRIORITY = ("ob", "water", "bunker", "green", "tee", "fairway", "trees", "rough")
SURFACE_OF = {"building": "ob", "flowers": "trees", "heath": "trees", "hedge": "trees"}
DRAWN_ONLY = ("path", "road", "parking", "lawn", "pool")   # kinds that don't change how a ball plays
TREE_RADIUS = 4.0      # yards of canopy round a mapped single tree, unless it says
CELL = 40.0            # yards per side of the lookup grid's cells
REACH = 500.0          # yards round the holes and the boundary the grid covers (a lake's far shore can be miles off)
PIN_SLOPE = 0.03       # rise a yard: a pin on a steeper slope than this is moved
PIN_EDGE = 4.0         # yards in from the green's edge a moved pin is, at least
PIN_STRAY = 0.002      # what each yard from where the map put it costs a spot, in slope

Tree = collections.namedtuple("Tree", "x y r leaf")   # leaf: "needle", "broad" or None
Tee = collections.namedtuple("Tee", "color pos")      # a teeing ground: its markers' color, its middle

# Tee colors, back to front (how far back each usually is, 0 the championship tees).
TEE_DEPTH = {"black": 0, "blue": 1, "silver": 1.5, "white": 2, "gold": 3, "yellow": 3, "green": 3.5,
             "orange": 3.5, "red": 4, "purple": 4, "pink": 4.5}
TEE_RGB = {"black": (40, 40, 44), "blue": (60, 112, 236), "silver": (190, 196, 204), "white": (244, 244, 240),
           "gold": (232, 188, 40), "yellow": (240, 222, 60), "green": (44, 176, 70), "orange": (240, 140, 40),
           "red": (222, 52, 52), "purple": (150, 80, 200), "pink": (244, 120, 180)}
# The colors a hole's tees get, back to front, when the map doesn't say, by how many there are.
TEE_SETS = {1: ("white",), 2: ("blue", "red"), 3: ("blue", "white", "red"), 4: ("blue", "white", "gold", "red"),
            5: ("black", "blue", "white", "gold", "red"), 6: ("black", "blue", "white", "gold", "red", "pink")}


@dataclasses.dataclass
class Feature:
    """A mapped area (poly) or a line with a width (a stream, a row of trees,
    a road). kind is one of SURFACES, a kind in SURFACE_OF, or one of
    DRAWN_ONLY. leaf says what trees are in a wood ("needle", "broad");
    tone picks a building's roof color; color is a tee's."""
    kind: str
    poly: Polygon = None
    line: list = None
    width: float = 0.0
    leaf: str = None
    tone: int = 0
    color: str = None    # a tee's markers' color, if the map says

    @property
    def surface(self):
        """How a ball on it plays, or None if it's only drawn."""
        return None if self.kind in DRAWN_ONLY else SURFACE_OF.get(self.kind, self.kind)

    @property
    def box(self):
        if self.poly is not None:
            return self.poly.box
        x0, y0, x1, y1 = bbox(self.line)
        h = self.width / 2
        return (x0 - h, y0 - h, x1 + h, y1 + h)

    def contains(self, x, y):
        if self.poly is not None:
            return self.poly.contains(x, y)
        x0, y0, x1, y1 = self.box
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        h = self.width / 2
        return any(segment_distance((x, y), a, b) <= h for a, b in zip(self.line, self.line[1:]))


@dataclasses.dataclass
class Hole:
    number: int
    par: int
    path: list               # course yards from the tee, through any dogleg, to the green
    green: Polygon
    pin: tuple
    name: str = ""
    handicap: int = None
    tees: list = dataclasses.field(default_factory=list)   # [Tee], back to front
    color: str = None        # the tees it's played from (None: where the hole's line starts)

    @property
    def tee(self):
        return self.path[0]

    def tee_for(self, color):
        """The hole's tee of a color, or the one nearest it, back to front."""
        if not self.tees:
            return None
        want = TEE_DEPTH.get(color, 2)
        return min(self.tees, key=lambda t: (abs(TEE_DEPTH.get(t.color, 2) - want), TEE_DEPTH.get(t.color, 2)))

    def played_from(self, color):
        """The hole as played from a color of tees: starting there, its line
        from the point it reaches level with them."""
        tee = self.tee_for(color)
        if tee is None:
            return self
        start, _ = project_onto(self.path, tee.pos)
        run, ahead = 0.0, []
        for a, b in zip(self.path, self.path[1:]):
            run += dist(a, b)
            if run > start + 1:
                ahead.append(b)
        return dataclasses.replace(self, path=[tee.pos] + (ahead or self.path[-1:]), color=tee.color)

    @property
    def yards(self):
        """The hole's length as played: along its path, ending at the pin."""
        return round(path_length(self.path[:-1] + [self.pin]))


class Course:
    def __init__(self, name, holes, features, trees=(), boundary=None, place="", source=None, layout="",
                 center=None, coast=(), cover=None, heights=None):
        self.name, self.place, self.layout = name, place, layout
        self.center = center     # (latitude, longitude): for the weather, the land cover and the heights
        self.cover = cover       # landcover.Cover: the trees OSM doesn't have (None: none)
        self._proj = Projection(*center) if center else None
        # elevation.Ground: how high the ground is (None: it's all flat)
        self.ground = Ground(heights, self._proj) if heights is not None and self._proj else None
        self.holes = holes
        self.features = features
        self.trees = [t if isinstance(t, Tree) else Tree(t[0], t[1], TREE_RADIUS, None) for t in trees]
        self.boundary = boundary
        self.coast = list(coast)  # coastlines, as lines with the land on their left (OSM's way round)
        self.source = source     # (OSM element type, id): where it came from
        self._grid = {}          # (i, j) -> [Feature] overlapping that cell
        self._tree_grid = {}     # (i, j) -> [Tree] whose canopy reaches into that cell
        self._sides = {}         # (i, j) -> True all inside the boundary, False all outside, None both
        rank = {k: i for i, k in enumerate(PRIORITY)}
        pts = [p for h in holes for p in h.path] + [p for r in (boundary.rings if boundary else ()) for p in r]
        x0, y0, x1, y1 = bbox(pts) if pts else (0, 0, 0, 0)
        reach = (x0 - REACH, y0 - REACH, x1 + REACH, y1 + REACH)
        # Lines (a creek, a row of trees) are looked up a segment at a time,
        # so a long one sits only in the cells it runs through.
        pieces = [g for f in features if f.surface in rank
                  for g in ([f] if f.poly is not None else
                            [Feature(f.kind, line=[a, b], width=f.width) for a, b in zip(f.line, f.line[1:])])]
        for f in sorted(pieces, key=lambda f: rank[f.surface]):
            for key in _cells(_clip(f.box, reach)):
                self._grid.setdefault(key, []).append(f)
        for t in self.trees:
            for key in _cells((t.x - t.r, t.y - t.r, t.x + t.r, t.y + t.r)):
                self._tree_grid.setdefault(key, []).append(t)
        if self.ground is not None:
            for f in features:
                if f.kind == "green" and f.poly is not None:
                    self.ground.tame(f.poly)
            for h in holes:
                h.pin = self.cut_cup(h)

    def cut_cup(self, hole):
        """Where a hole's pin goes: where the map has it, unless that's on a
        slope; then the flattest spot (a yard round it as flat too) PIN_EDGE
        in from the green's edge, the nearer the better."""
        flat = lambda p: sum(math.hypot(*self.ground.slope(q, 1.5)) for q in
                             (p, (p[0] + 1, p[1]), (p[0] - 1, p[1]), (p[0], p[1] + 1), (p[0], p[1] - 1))) / 5
        if flat(hole.pin) <= PIN_SLOPE:
            return hole.pin
        green = hole.green
        x0, y0, x1, y1 = green.box
        edges = [(a, b) for ring in green.rings for a, b in zip(ring, ring[1:] + ring[:1])]
        best, best_cost = hole.pin, None
        for i in range(int(x1 - x0) + 1):
            for j in range(int(y1 - y0) + 1):
                p = (x0 + i + 0.5, y0 + j + 0.5)
                if not green.contains(*p) or edge_distance(p, edges) < PIN_EDGE:
                    continue
                cost = flat(p) + PIN_STRAY * dist(p, hole.pin)
                if best_cost is None or cost < best_cost:
                    best, best_cost = p, cost
        return best

    def tee_colors(self):
        """[(color, yards)]: the course's sets of tees, back to front, and how
        long the course plays from each."""
        colors = {t.color for h in self.holes for t in h.tees}
        out = [(c, sum(h.played_from(c).yards for h in self.holes)) for c in colors]
        return sorted(out, key=lambda x: (TEE_DEPTH.get(x[0], 2), -x[1]))

    @property
    def par(self):
        return sum(h.par for h in self.holes)

    @property
    def yards(self):
        return sum(h.yards for h in self.holes)

    def surface_at(self, p):
        """What the ground is at p (course yards)."""
        x, y = p
        key = (int(x // CELL), int(y // CELL))
        found = "rough"
        for f in self._grid.get(key, ()):   # in PRIORITY order: the first hit wins
            if f.contains(x, y):
                found = f.surface
                break
        if found == "rough" and (any(dist(p, (t.x, t.y)) <= t.r for t in self._tree_grid.get(key, ()))
                                 or self.wooded(p)):
            found = "trees"   # under a mapped tree, or in a wood seen from space (fairways stay fairways)
        if found != "water" and not self.inside(p, key):
            return "ob"       # whatever's out there (water out there is still a penalty area)
        return found

    def wooded(self, p):
        """Whether the land cover has trees at p: where its blend of cells is
        over half, give or take a little for each yard (a ragged edge)."""
        if self.cover is None or self._proj is None:
            return False
        v = self.cover.trees(*self._proj.back(*p))
        if v <= 0.3 or v >= 0.7:
            return v >= 0.7
        n = ((int(p[0]) * 374761393 + int(p[1]) * 668265263) & 0xFFFF) / 0xFFFF
        return v + (n - 0.5) * 0.4 > 0.5

    def inside(self, p, key=None):
        """Whether p is inside the course's boundary (everywhere is, without one)."""
        if self.boundary is None:
            return True
        key = key or (int(p[0] // CELL), int(p[1] // CELL))
        side = self._sides.get(key, 0)
        if side == 0:   # not worked out yet: the cell's corners, and whether the boundary crosses it
            i, j = key
            corners = [self.boundary.contains(x, y) for x in (i * CELL, (i + 1) * CELL)
                       for y in (j * CELL, (j + 1) * CELL)]
            cx0, cy0, cx1, cy1 = i * CELL, j * CELL, (i + 1) * CELL, (j + 1) * CELL
            crossed = any(min(a[0], b[0]) <= cx1 and max(a[0], b[0]) >= cx0 and   # an edge near it, at least
                          min(a[1], b[1]) <= cy1 and max(a[1], b[1]) >= cy0
                          for ring in self.boundary.rings for a, b in zip(ring, ring[1:] + ring[:1]))
            side = self._sides[key] = corners[0] if len(set(corners)) == 1 and not crossed else None
        return side if side is not None else self.boundary.contains(*p)


def _clip(box, to):
    """box cut to the box `to` (empty if they don't meet)."""
    x0, y0, x1, y1 = max(box[0], to[0]), max(box[1], to[1]), min(box[2], to[2]), min(box[3], to[3])
    return (x0, y0, x1, y1) if x0 <= x1 and y0 <= y1 else None


def _cells(box):
    if box is None:
        return
    x0, y0, x1, y1 = box
    for i in range(int(x0 // CELL), int(x1 // CELL) + 1):
        for j in range(int(y0 // CELL), int(y1 // CELL) + 1):
            yield (i, j)
