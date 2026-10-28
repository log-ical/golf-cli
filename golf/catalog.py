"""Courses to play: famous ones mapped hole by hole in OpenStreetMap, the
ones played before, and a search of OpenStreetMap by name (Nominatim) or
by where they are (Overpass).

Nominatim asks for at most one search a second and an app that says who it
is; a person typing searches stays well inside that.
"""

import dataclasses
import json
import os
import time

from .files import write_atomic
from .net import fetch_json, query_url
from .osm import COURSE_DIR, SERVERS, cache_path

NOMINATIM = "https://nominatim.openstreetmap.org/search"
INDEX = os.path.join(COURSE_DIR, "index.json")
SEARCH_GAP = 1.1   # seconds between searches: Nominatim allows one a second
OVERPASS = SERVERS[0]


@dataclasses.dataclass(frozen=True)
class Entry:
    name: str
    place: str
    kind: str        # OSM element type: "way" or "relation"
    osm_id: int

    @property
    def key(self):
        return "%s:%d" % (self.kind, self.osm_id)


# Checked: each has its holes mapped (golf=hole) with greens and tees.
FAMOUS = (
    Entry("Augusta National Golf Club", "Augusta, Georgia", "way", 871993734),
    Entry("Pebble Beach Golf Links", "Pebble Beach, California", "relation", 3741806),
    Entry("Old Course", "St Andrews, Scotland", "way", 1019045811),
    Entry("TPC Sawgrass", "Ponte Vedra Beach, Florida", "relation", 1783123),
    Entry("Port Royal Golf Course", "Southampton, Bermuda", "way", 174882622),
    Entry("Pinehurst No. 2", "Pinehurst, North Carolina", "way", 1358696570),
    Entry("Oakmont Country Club", "Oakmont, Pennsylvania", "relation", 6174192),
    Entry("Shinnecock Hills Golf Club", "Southampton, New York", "way", 689056680),
    Entry("Winged Foot Golf Club", "Mamaroneck, New York", "way", 122734591),
    Entry("Torrey Pines South Course", "San Diego, California", "way", 35679036),
    Entry("Whistling Straits", "Sheboygan, Wisconsin", "way", 205111637),
    Entry("Kiawah Island Ocean Course", "Kiawah Island, South Carolina", "relation", 17647608),
    Entry("Harbour Town Golf Links", "Hilton Head Island, South Carolina", "relation", 4813858),
    Entry("Muirfield Village Golf Club", "Dublin, Ohio", "relation", 337418),
    Entry("Quail Hollow Club", "Charlotte, North Carolina", "way", 877659537),
    Entry("East Lake Golf Club", "Atlanta, Georgia", "way", 25587469),
    Entry("Valhalla Golf Club", "Louisville, Kentucky", "way", 59737644),
    Entry("Royal Birkdale Golf Club", "Southport, England", "way", 25720345),
)


def search(text):
    """Golf courses named like text, best matches first; if text names a
    town or an area instead, the courses mapped in and around it.

    Nominatim matches places by name, so "pebble" alone finds hamlets:
    "pebble golf" goes first, then the text as typed, a second apart as
    Nominatim asks. The second also says where text is, for the courses
    around it (one Overpass query over its bounding box)."""
    queries = [text] if "golf" in text.lower() else [text + " golf", text]
    out, seen, place = [], set(), None
    for i, q in enumerate(queries):
        if i:
            time.sleep(SEARCH_GAP)
        entries, where = _search(q)
        place = place or where
        for e in entries:
            if e.key not in seen:
                seen.add(e.key)
                out.append(e)
    if place and len(out) < 3:
        for e in around(*place):
            if e.key not in seen:
                seen.add(e.key)
                out.append(e)
    return out


def around(box, label):
    """The golf courses mapped inside a bounding box (south, north, west,
    east), named, largest areas first; `label` is their place."""
    s, n, w, e = box
    # At least ~10 km across, at most ~80, around the box's middle.
    lat, lon = (s + n) / 2, (w + e) / 2
    half_lat = min(0.36, max(0.045, (n - s) / 2))
    half_lon = min(0.5, max(0.06, (e - w) / 2))
    q = ('[out:json][timeout:30];(way["leisure"="golf_course"]["name"](%f,%f,%f,%f);'
         'relation["leisure"="golf_course"]["name"](%f,%f,%f,%f););out tags bb 40;') % (
        (lat - half_lat, lon - half_lon, lat + half_lat, lon + half_lon) * 2)
    data = fetch_json(query_url(OVERPASS, data=q), timeout=40)
    found = []
    for el in data.get("elements") or ():
        tags, b = el.get("tags") or {}, el.get("bounds") or {}
        size = abs((b.get("maxlat", 0) - b.get("minlat", 0)) * (b.get("maxlon", 0) - b.get("minlon", 0)))
        town = tags.get("addr:city") or label
        found.append((size, Entry(tags["name"], town, el["type"], int(el["id"]))))
    return [e for _, e in sorted(found, key=lambda x: -x[0])]


def _search(q):
    """(golf courses Nominatim finds for q, (bounding box, name) of the
    first other place it finds, or None)."""
    data = fetch_json(query_url(NOMINATIM, q=q, format="jsonv2", limit="25", addressdetails="1"), timeout=20)
    out, where = [], None
    for r in data if isinstance(data, list) else ():
        if where is None and r.get("type") != "golf_course" and r.get("category") in ("place", "boundary"):
            try:
                box = tuple(float(v) for v in r["boundingbox"])
                where = (box, (r.get("display_name") or "").split(",")[0])
            except (KeyError, TypeError, ValueError):
                pass
    for r in data if isinstance(data, list) else ():
        if r.get("type") != "golf_course" or r.get("osm_type") not in ("way", "relation"):
            continue   # a course mapped as a single point has no holes to play
        try:
            osm_id = int(r["osm_id"])
        except (KeyError, TypeError, ValueError):
            continue
        a = r.get("address") or {}
        town = a.get("city") or a.get("town") or a.get("village") or a.get("hamlet") or a.get("county") or ""
        place = ", ".join(v for v in (town, a.get("state"), a.get("country")) if v)
        name = r.get("name") or (r.get("display_name") or "").split(",")[0] or "Golf course"
        out.append(Entry(name, place, r["osm_type"], osm_id))
    return out, where


def played():
    """Courses played before (downloaded and cached), most recent first."""
    try:
        with open(INDEX, encoding="utf-8") as f:
            index = json.load(f)
        entries = [Entry(str(v["name"]), str(v.get("place", "")), v["kind"], int(v["osm_id"]))
                   for v in index if v.get("kind") in ("way", "relation")]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []
    return [e for e in entries if os.path.exists(cache_path(e.kind, e.osm_id))]


def remember(entry):
    """Put a course at the top of the played list."""
    entries = [entry] + [e for e in played() if e.key != entry.key]
    try:
        write_atomic(INDEX, json.dumps([dataclasses.asdict(e) for e in entries[:50]], indent=1).encode())
    except OSError:
        pass


def is_cached(entry):
    return os.path.exists(cache_path(entry.kind, entry.osm_id))
