import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from golf.geo import Projection  # noqa: E402

ORIGIN = (40.0, -75.0)
_proj = Projection(*ORIGIN)


def at(course, x, y):
    """Helper yards (from ORIGIN) as the course's own yards (from its center)."""
    p = ll(x, y)
    return Projection(*course.center)(p["lat"], p["lon"])


def ll(x, y):
    """A {lat, lon} node for course yards (x east, y north) from ORIGIN."""
    lat, lon = _proj.back(x, y)
    return {"lat": lat, "lon": lon}


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def circle(cx, cy, r, n=16):
    import math
    pts = [(cx + r * math.cos(i / n * math.tau), cy + r * math.sin(i / n * math.tau)) for i in range(n)]
    return pts + pts[:1]


def way(id_, pts, **tags):
    return {"type": "way", "id": id_, "tags": tags, "geometry": [ll(*p) for p in pts]}


def node(id_, x, y, **tags):
    return {"type": "node", "id": id_, "tags": tags, **ll(x, y)}


def tiny_course():
    """Overpass data for a made-up three-hole course, in yards from ORIGIN:

    hole 1, par 4: tee (0, 0) east to a green round (410, 0) (a forward tee,
    tagged pink, round (60, 0)), down a fairway
    split into two ways of a multipolygon, with a bunker cut into it at
    (250, 10), a pond south of it and a tree north; a creek crosses short.
    hole 2, par 3: mapped green to tee (backwards), from (420, 80) west to (250, 80).
    hole 3: no par given (180 yards: a 3), from (440, 130) east.
    A par-3 course's hole 1 is mapped inside too, named as its own course.
    """
    half_a = [(40, -18), (380, -18), (380, 18)]
    half_b = [(380, 18), (40, 18), (40, -18)]
    els = [
        {"type": "relation", "id": 1, "tags": {"leisure": "golf_course", "name": "Tiny Links", "type": "multipolygon"},
         "members": [{"type": "way", "ref": 10, "role": "outer", "geometry": [ll(*p) for p in rect(-50, -150, 650, 150)]}]},
        way(100, [(0, 0), (200, 2), (405, 0)], golf="hole", ref="1", par="4", name="First", handicap="3"),
        {"type": "relation", "id": 2, "tags": {"golf": "fairway", "type": "multipolygon"},
         "members": [{"type": "way", "ref": 21, "role": "outer", "geometry": [ll(*p) for p in half_a]},
                     {"type": "way", "ref": 22, "role": "outer", "geometry": [ll(*p) for p in half_b]}]},
        way(101, circle(410, 0, 15), golf="green"),
        way(102, rect(245, 5, 255, 15), golf="bunker"),
        way(103, rect(280, -70, 340, -30), natural="water"),
        way(104, [(100, -80), (100, -25)], waterway="stream"),
        way(105, rect(-8, -5, 8, 5), golf="tee"),
        way(108, rect(55, -5, 65, 5), golf="tee", tee="pink"),
        node(106, 200, 40, natural="tree"),
        node(107, 412, 2, golf="pin"),
        way(200, [(250, 80), (420, 80)], golf="hole", ref="2", par="3"),
        way(201, circle(250, 80, 12), golf="green"),
        way(210, [(440, 130), (620, 130)], golf="hole", ref="3"),
        way(211, circle(620, 130, 10), golf="green"),
        way(300, [(500, -100), (600, -100)], golf="hole", ref="1", par="3", **{"golf:course:name": "Short"}),
        way(301, [(500, -100), (560, -100), (600, -100)], golf="hole", ref="2", par="3",
            **{"golf:course:name": "Short"}),
        way(302, [(560, -100), (500, -100)], golf="hole", ref="3", par="3", **{"golf:course:name": "Short"}),
        way(303, circle(600, -100, 10), golf="green"),
    ]
    return {"elements": els}


def tiny_around():
    """What's mapped round tiny_course: a clubhouse and a flowerbed inside
    its boundary, gorse, a road and a beach outside it, a tree with its crown
    mapped, and the coast along y=200 (drawn east to west: the land, on its
    left, is south; the sea north)."""
    return {"elements": [
        way(500, rect(20, 100, 40, 115), building="yes", golf="clubhouse"),
        way(501, rect(90, 55, 110, 70), landuse="flowerbed"),
        way(502, rect(660, 100, 700, 140), natural="heath"),
        way(503, [(700, -160), (700, 160)], highway="residential"),
        way(504, rect(660, 40, 700, 80), natural="beach"),
        node(505, 150, -60, natural="tree", diameter_crown="12", leaf_type="needleleaved"),
        way(506, [(800, 200), (-300, 200)], natural="coastline"),
        node(106, 200, 40, natural="tree"),   # in the course's data too: counted once
    ]}


def two_loops():
    """Two unnamed courses in one boundary, holes 1-3 each, their numbers
    repeated: course A runs along y=0, course B along y=300."""
    els = [{"type": "way", "id": 1, "tags": {"leisure": "golf_course", "name": "Twin"},
            "geometry": [ll(*p) for p in rect(-100, -100, 1200, 400)]}]
    i = 10
    for y, starts in ((0, (0, 400, 800)), (300, (0, 380, 760))):
        for n, x in enumerate(starts, 1):
            length = 330 if y == 0 else 300
            els.append(way(i, [(x, y), (x + length, y)], golf="hole", ref=str(n)))
            els.append(way(i + 1, circle(x + length, y, 12), golf="green"))
            i += 2
    return {"elements": els}


def heights(fn, cell=2.0, box=(-60, -160, 660, 160)):
    """elevation.Heights over tiny_course's ground (box: helper yards x0, y0,
    x1, y1), each cell fn(x, y) meters high at its middle (helper yards)."""
    import array
    from golf import elevation
    x0, y0 = elevation.merc(*_proj.back(box[0], box[1]))
    x1, y1 = elevation.merc(*_proj.back(box[2], box[3]))
    w, h = int((x1 - x0) / cell) + 1, int((y1 - y0) / cell) + 1
    top = y0 + h * cell
    z = array.array("f")
    for i in range(h):
        my = top - (i + 0.5) * cell
        for j in range(w):
            mx = x0 + (j + 0.5) * cell
            lat, lon = elevation.unmerc(mx, my)
            z.append(fn(*_proj(lat, lon)))
    return elevation.Heights(x0, top, cell, w, h, z, "test")


class Plane:
    """A course.ground tilted evenly: rising gx yards a yard east, gy north."""

    def __init__(self, gx, gy):
        self.gx, self.gy = gx, gy

    def z(self, p):
        return self.gx * p[0] + self.gy * p[1]

    def slope(self, p, d=1.0):
        return self.gx, self.gy
