"""Courses from OpenStreetMap, through the Overpass API.

One query brings everything mapped inside a course's boundary: its holes
(golf=hole, a line from the tee to the green), greens, tees, fairways,
bunkers, water, woods and single trees. A second brings what's around it, to
draw: houses, roads, lawns, parking, pools, flowerbeds, gorse, woods, water
and the coastline. The trees mappers left out come from satellite land
cover (landcover.py), and how high the ground is from lidar or terrain
tiles (elevation.py). The raw responses are cached for good under
~/.cache/golf-cli/courses/, so a course downloads once. A course plays
without its surroundings if they can't be had (they're tried again next time).

Map data © OpenStreetMap contributors, available under the Open Database
License (ODbL).
"""

import json
import math
import os
import threading

from .course import TEE_DEPTH, TEE_SETS, TREE_RADIUS, Course, Feature, Hole, Tee, Tree
from .files import CACHE_DIR, write_atomic
from .geo import Polygon, Projection, assemble_rings, dist, path_length, project_onto
from . import elevation, landcover
from .net import fetch, query_url

SERVERS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
COURSE_DIR = os.path.join(CACHE_DIR, "courses")
TIMEOUT = 180    # seconds: a big course takes the server up to a minute

QUERY = """[out:json][timeout:150];
{kind}({id});map_to_area->.a;
({kind}({id});
 way["golf"](area.a);relation["golf"](area.a);node["golf"](area.a);
 way["natural"~"^(water|wood|scrub|sand|beach|tree_row)$"](area.a);relation["natural"~"^(water|wood)$"](area.a);
 way["landuse"="forest"](area.a);relation["landuse"="forest"](area.a);
 way["waterway"](area.a);node["natural"="tree"](area.a););
out geom;"""

AROUND = """[out:json][timeout:120][bbox:{s:.6f},{w:.6f},{n:.6f},{e:.6f}];
(way["building"];relation["building"]["type"="multipolygon"];
 way["highway"];
 way["natural"~"^(water|wood|scrub|heath|sand|beach|tree_row|coastline|grassland)$"];
 relation["natural"~"^(water|wood|scrub|heath)$"];
 node["natural"~"^(tree|shrub)$"];
 way["landuse"~"^(forest|residential|grass|meadow|flowerbed|commercial|retail|village_green|recreation_ground)$"];
 relation["landuse"~"^(forest|residential)$"];
 way["leisure"~"^(garden|park|swimming_pool)$"];
 way["amenity"="parking"];
 way["waterway"];way["barrier"="hedge"];);
out geom;"""
BOUNDS = "[out:json][timeout:30];{kind}({id});out bb;"
AROUND_MARGIN = 0.0027   # degrees of latitude (about 300 m) of surroundings round the course's outline

# Tags to surfaces: golf=* first, then natural=*, landuse=*.
GOLF_KINDS = {"fairway": "fairway", "green": "green", "tee": "tee", "bunker": "bunker", "rough": "rough",
              "water_hazard": "water", "lateral_water_hazard": "water", "cartpath": "path", "path": "path"}
NATURAL_KINDS = {"water": "water", "wood": "trees", "scrub": "trees", "tree_row": "trees", "sand": "bunker",
                 "beach": "bunker", "heath": "heath", "grassland": "lawn"}
LAWNS = ("residential", "grass", "meadow", "commercial", "retail", "village_green", "recreation_ground")
ROAD_WIDTHS = {"motorway": 14.0, "trunk": 12.0, "primary": 10.0, "secondary": 9.0, "tertiary": 8.0,
               "residential": 6.5, "unclassified": 6.0, "living_street": 5.5, "service": 4.0, "track": 3.0}
FOOTPATHS = ("footway", "path", "cycleway", "steps", "bridleway", "pedestrian")
LINE_WIDTHS = {"water": 3.0, "trees": 7.0, "path": 2.0, "hedge": 1.5}   # yards, for features mapped as lines
WATERWAY_WIDTHS = {"river": 15.0, "canal": 8.0}           # anything else (a stream, a ditch) is LINE_WIDTHS's
LEAVES = {"needleleaved": "needle", "broadleaved": "broad"}
YARDS_PER_METER = 1.0936133
GREEN_RADIUS = 14.0     # yards: a green drawn around a hole's end when none is mapped
MIN_HOLES = 3           # fewer than this isn't a layout worth playing


class CourseError(Exception):
    pass


def cache_path(kind, osm_id):
    return os.path.join(COURSE_DIR, "%s-%d.json" % (kind, osm_id))


def around_path(kind, osm_id):
    return os.path.join(COURSE_DIR, "%s-%d-around.json" % (kind, osm_id))


def cover_path(kind, osm_id):
    return os.path.join(COURSE_DIR, "%s-%d-cover.json" % (kind, osm_id))


def heights_path(kind, osm_id):
    return os.path.join(COURSE_DIR, "%s-%d-heights.json" % (kind, osm_id))


def download(kind, osm_id, refresh=False):
    """The course's raw Overpass data: from the cache unless refresh, else
    from the first server that answers."""
    def whole(data):
        return any(el.get("tags", {}).get("golf") == "hole" for el in data.get("elements", ()))
    return _get(cache_path(kind, osm_id), QUERY.format(kind=kind, id=osm_id), refresh, whole)


def bounds(data, kind, osm_id):
    """The course's bounding box from its download, or None."""
    me = next((el for el in data.get("elements") or () if el["type"] == kind and el["id"] == osm_id), None)
    return (me or {}).get("bounds")


def download_around(b, kind, osm_id, refresh=False):
    """What's mapped around a course whose bounding box is b (see bounds), or
    None if it can't be had now."""
    if not b:
        return None
    m_lat = AROUND_MARGIN
    m_lon = AROUND_MARGIN / max(0.2, math.cos(math.radians((b["minlat"] + b["maxlat"]) / 2)))
    query = AROUND.format(s=b["minlat"] - m_lat, w=b["minlon"] - m_lon, n=b["maxlat"] + m_lat, e=b["maxlon"] + m_lon)
    try:
        return _get(around_path(kind, osm_id), query, refresh, lambda d: True)
    except CourseError:
        return None


def _get(path, query, refresh, keep):
    """A query's answer, cached at path once keep(answer) says it's worth it."""
    if not refresh and path:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    err = None
    for server in SERVERS:
        try:
            raw = fetch(query_url(server, data=query), timeout=TIMEOUT)
            data = json.loads(raw)
        except Exception as e:
            err = e
            continue
        remark = data.get("remark") or ""
        if "error" in remark.lower():   # the server gave up part way: don't cache half a course
            err = CourseError(remark.strip()[:100])
            continue
        if keep(data):
            write_atomic(path, raw)
        return data
    raise CourseError("couldn't reach OpenStreetMap: %s" % err)


def load(kind, osm_id, refresh=False, satellite=True):
    """The course's layouts (see parse), downloading it, its surroundings,
    its land cover (unless not satellite) and its heights if need be. None
    downloaded yet: the course's outline comes first (a second's query), so
    they can all download at once."""
    cover = (lambda box: landcover.load(cover_path(kind, osm_id), box)) if satellite else (lambda box: None)
    heights = lambda box: elevation.load(heights_path(kind, osm_id), box)
    both = refresh or not (os.path.exists(cache_path(kind, osm_id)) or os.path.exists(around_path(kind, osm_id)))
    if both:
        try:
            box = _get(None, BOUNDS.format(kind=kind, id=osm_id), True, lambda d: False)
            box = bounds(box, kind, osm_id)
        except CourseError:
            box = None
        if box:
            got = {}
            jobs = {"around": lambda: download_around(box, kind, osm_id, refresh), "cover": lambda: cover(box),
                    "heights": lambda: heights(box)}
            workers = [threading.Thread(target=lambda k=k, f=f: got.update({k: f()}), daemon=True)
                       for k, f in jobs.items()]
            for w in workers:
                w.start()
            data = download(kind, osm_id, refresh)
            for w in workers:
                w.join()
            return parse(data, kind, osm_id, got.get("around"), got.get("cover"), got.get("heights"))
    data = download(kind, osm_id, refresh)
    box = bounds(data, kind, osm_id)
    got = {}
    workers = [threading.Thread(target=lambda: got.update(heights=heights(box)), daemon=True)]
    for w in workers:
        w.start()
    around, cv = download_around(box, kind, osm_id, refresh), cover(box)
    for w in workers:
        w.join()
    return parse(data, kind, osm_id, around, cv, got.get("heights"))


def _points(geometry):
    return [(g["lat"], g["lon"]) for g in geometry or () if g and "lat" in g]


def _shape(el, proj):
    """An element's outline in yards: ("area", Polygon) for closed ways and
    multipolygons, ("line", points) for open ways, else None."""
    if el["type"] == "way":
        pts = _points(el.get("geometry"))
        if len(pts) < 2:
            return None
        xy = [proj(*p) for p in pts]
        if pts[0] == pts[-1] and len(pts) >= 4:
            return "area", Polygon([xy[:-1]])
        return "line", xy
    if el["type"] == "relation":
        lines = [_points(m.get("geometry")) for m in el.get("members", ())
                 if m.get("type") == "way" and m.get("role") in ("outer", "inner", "")]
        rings = assemble_rings([l for l in lines if l])
        if rings:
            return "area", Polygon([[proj(*p) for p in r] for r in rings])
    return None


def _kind(tags):
    if tags.get("golf") in GOLF_KINDS:
        return GOLF_KINDS[tags["golf"]]
    if tags.get("building", "no") != "no":
        return "building"   # the clubhouse too
    if tags.get("golf"):
        return None   # a driving range, pine straw: nothing to draw them as
    if tags.get("leisure") == "swimming_pool":
        return "pool"
    if tags.get("natural") in NATURAL_KINDS:
        return NATURAL_KINDS[tags["natural"]]
    landuse = tags.get("landuse")
    if landuse == "forest":
        return "trees"
    if landuse == "flowerbed" or tags.get("leisure") == "garden":
        return "flowers"
    if landuse in LAWNS or tags.get("leisure") == "park":
        return "lawn"
    if tags.get("amenity") == "parking":
        return "parking"
    highway = tags.get("highway")
    if highway in ROAD_WIDTHS:
        return "road"
    if highway in FOOTPATHS:
        return "path"
    if tags.get("barrier") == "hedge":
        return "hedge"
    if tags.get("waterway"):
        return "water"
    return None


def _tree(el, p, tags):
    """A single tree (or shrub) node: its canopy's size from the map if it
    says (diameter_crown, in meters), else one about TREE_RADIUS, a little
    different for each tree."""
    if tags.get("natural") == "shrub":
        return Tree(p[0], p[1], 1.6, "shrub")
    try:
        r = float(str(tags.get("diameter_crown", "")).split()[0]) / 2 * YARDS_PER_METER
    except (ValueError, IndexError):
        r = TREE_RADIUS * (0.75 + 0.5 * ((el["id"] * 2654435761) % 1000) / 1000)
    return Tree(p[0], p[1], max(1.5, min(12.0, r)), LEAVES.get(tags.get("leaf_type")))


def _int(s):
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return None


def _center(el):
    pts = _points(el.get("geometry")) or [p for m in el.get("members", ()) for p in _points(m.get("geometry"))]
    if el.get("bounds"):
        b = el["bounds"]
        return ((b["minlat"] + b["maxlat"]) / 2, (b["minlon"] + b["maxlon"]) / 2)
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def parse(data, kind, osm_id, around=None, cover=None, heights=None):
    """The course's layouts, each a Course, the main one first; around: its
    surroundings' data (download_around), if any; cover, its land cover;
    heights, its elevation.Heights.

    A boundary can hold more than one course: Augusta's par-3 course, TPC
    Sawgrass's Stadium and Dye's Valley. Holes are grouped by their
    golf:course:name; holes with the same number left over (Sawgrass names
    neither course) are chained into courses by walking from each hole's
    green to the nearest next tee.
    """
    elements = list(data.get("elements") or [])
    seen = {(el["type"], el["id"]) for el in elements}
    elements += [el for el in (around or {}).get("elements") or () if (el["type"], el["id"]) not in seen]
    me = next((el for el in elements if el["type"] == kind and el["id"] == osm_id), None)
    if me is None:
        raise CourseError("OpenStreetMap has no %s %d" % (kind, osm_id))
    center = _center(me)
    if center is None:
        raise CourseError("the course has no outline in OpenStreetMap")
    proj = Projection(*center)
    tags = me.get("tags", {})
    name = tags.get("name") or "Golf course"
    shape = _shape(me, proj)
    boundary = shape[1] if shape and shape[0] == "area" else None

    features, trees, pins, holes, coast = [], [], [], [], []
    for el in elements:
        if el is me:
            continue
        t = el.get("tags", {})
        if el["type"] == "node":
            if "lat" not in el:
                continue
            p = proj(el["lat"], el["lon"])
            if t.get("golf") == "pin":
                pins.append(p)
            elif t.get("natural") in ("tree", "shrub"):
                trees.append(_tree(el, p, t))
            continue
        if t.get("natural") == "coastline":
            shape = _shape(el, proj)
            if shape:
                coast.append(shape[1] if shape[0] == "line" else shape[1].rings[0] + shape[1].rings[0][:1])
            continue
        if t.get("golf") == "hole":
            shape = _shape(el, proj)
            if shape:
                pts = shape[1] if shape[0] == "line" else shape[1].rings[0]
                holes.append((t, pts))
            continue
        k = _kind(t)
        shape = _shape(el, proj) if k else None
        if not shape:
            continue
        leaf, tone = LEAVES.get(t.get("leaf_type")), el["id"] % 5
        if shape[0] == "area" and k not in ("path", "road", "hedge"):
            features.append(Feature(k, poly=shape[1], leaf=leaf, tone=tone, color=_tee_color(t) if k == "tee" else None))
        else:   # a line, or a road or path mapped as a closed way (drawn along it)
            pts = shape[1] if shape[0] == "line" else shape[1].rings[0] + shape[1].rings[0][:1]
            width = ROAD_WIDTHS.get(t.get("highway")) or WATERWAY_WIDTHS.get(t.get("waterway")) or \
                LINE_WIDTHS.get(k, 3.0)
            features.append(Feature(k, line=pts, width=width, leaf=leaf))

    greens = [f.poly for f in features if f.kind == "green"]
    layouts = []
    for label, group in _layouts(holes, name):
        made = [_hole(t, pts, greens, pins, features) for t, pts in group]
        made = [h for h in made if h]
        _tees(made, [f for f in features if f.kind == "tee" and f.poly is not None])
        if len(made) >= MIN_HOLES:
            layouts.append((label, sorted(made, key=lambda h: h.number)))
    if not layouts:
        raise CourseError("%s isn't mapped hole by hole in OpenStreetMap" % name)
    place = ", ".join(v for v in (tags.get("addr:city"), tags.get("addr:state"), tags.get("addr:country")) if v)
    return [Course(name, hs, features, trees, boundary, place=place, source=(kind, osm_id), layout=label,
                   center=center, coast=coast, cover=cover, heights=heights) for label, hs in layouts]


def _layouts(holes, course_name):
    """[(label, [(tags, points)])]: the holes split into courses, the main
    course first (named like the boundary, else the most holes, longest)."""
    by_name = {}
    for t, pts in holes:
        if _int(t.get("ref")) is None:
            continue
        by_name.setdefault(t.get("golf:course:name") or "", []).append((t, pts))
    out = []
    for label, group in by_name.items():
        chains = _chains(group)
        for i, chain in enumerate(chains):
            out.append((label or ("" if len(chains) == 1 else "course %d" % (i + 1)), chain))

    def rank(item):
        label, chain = item
        named = bool(label) and (label.lower() in course_name.lower() or course_name.lower() in label.lower())
        return (not named, -len(chain), -sum(path_length(pts) for _, pts in chain))
    return sorted(out, key=rank)


def _chains(group):
    """Holes with unique numbers as they are; with repeats, a chain per
    first hole, each taking the next number's hole whose tee is nearest."""
    numbers = sorted({_int(t["ref"]) for t, _ in group})
    by_number = {n: [h for h in group if _int(h[0]["ref"]) == n] for n in numbers}
    if all(len(v) == 1 for v in by_number.values()):
        return [group]
    chains = [[h] for h in by_number[numbers[0]]]
    for n in numbers[1:]:
        free = list(by_number[n])
        # Each chain's last green, nearest tee first (greedily over all pairs).
        pairs = sorted((dist(c[-1][1][-1], h[1][0]), ci, hi) for ci, c in enumerate(chains) for hi, h in enumerate(free))
        took_c, took_h = set(), set()
        for d, ci, hi in pairs:
            if ci not in took_c and hi not in took_h:
                chains[ci].append(free[hi])
                took_c.add(ci)
                took_h.add(hi)
    return chains


def _tee_color(tags):
    """A tee's color from its tags (tee=blue, colour=pink, or a name with a color in it), or None."""
    for key in ("tee", "golf:tee", "tee:colour", "colour", "color"):
        v = str(tags.get(key, "")).strip().lower()
        if v in TEE_DEPTH:
            return v
    words = str(tags.get("name", "")).lower().replace("-", " ").split()
    return next((w for w in words if w in TEE_DEPTH), None)


def _tees(holes, boxes):
    """Give each hole its tees: the tee boxes along the start of its line
    (each box the hole it's nearest), back to front, in their colors, or the
    usual ones for so many (TEE_SETS)."""
    found = {id(h): [] for h in holes}
    for box in boxes:
        c = box.poly.centroid()
        best = None
        for h in holes:
            along, off = project_onto(h.path, c)
            if along <= 0.45 * path_length(h.path) + 15 and off <= 40:
                score = off + along * 0.1
                if best is None or score < best[0]:
                    best = (score, h)
        if best:
            found[id(best[1])].append((c, box.color))
    for h in holes:
        spots = sorted(found[id(h)], key=lambda s: -dist(s[0], h.pin))
        usual = TEE_SETS[min(len(spots), 6)] if spots else ()
        h.tees = [Tee(color or usual[min(i, len(usual) - 1)], c) for i, (c, color) in enumerate(spots)]


def _hole(t, pts, greens, pins, features):
    """A Hole from a golf=hole way, finding its green and pin; None if unusable."""
    number = _int(t.get("ref"))
    if number is None or len(pts) < 2 or path_length(pts) < 40:
        return None
    pts = list(pts)

    def green_at(p):
        inside = [g for g in greens if g.contains(*p)]
        if inside:
            return inside[0]
        near = [(dist(p, g.centroid()), g) for g in greens]
        near = [x for x in near if x[0] <= 60]
        return min(near, key=lambda x: x[0])[1] if near else None

    on_green = lambda p: any(g.contains(*p) for g in greens)
    if on_green(pts[0]) and not on_green(pts[-1]):
        pts.reverse()   # drawn from the green to the tee
    green = green_at(pts[-1])
    if green is None:   # no green mapped: a round one where the hole ends
        c = pts[-1]
        green = Polygon([[(c[0] + GREEN_RADIUS * math.cos(a / 16 * math.tau), c[1] + GREEN_RADIUS * math.sin(a / 16 * math.tau))
                          for a in range(16)]])
        features.append(Feature("green", poly=green))
    inside = [p for p in pins if green.contains(*p)]
    if inside:
        pin = min(inside, key=lambda p: dist(p, pts[-1]))
    elif green.contains(*pts[-1]):
        pin = pts[-1]
    else:
        pin = green.centroid()
    par = _int(t.get("par"))
    if par not in (3, 4, 5, 6):
        length = path_length(pts)
        par = 3 if length <= 250 else 4 if length <= 490 else 5
    return Hole(number=number, par=par, path=pts, green=green, pin=pin, name=t.get("name") or "",
                handicap=_int(t.get("handicap")))
