"""Flat geometry in yards: the projection from latitude and longitude, polygons
and paths.

A course is a few kilometres across at most, so an equirectangular
projection about its center, scaled for the earth's shape at its latitude,
is accurate to well under a yard. x runs east and
y north, both in yards.
"""

import math

YARDS_PER_METER = 1.0936133


def meters_per_degree(lat):
    """(meters per degree of latitude, of longitude) at a latitude, on the WGS 84 ellipsoid."""
    r = math.radians(lat)
    return (111132.95 - 559.82 * math.cos(2 * r) + 1.175 * math.cos(4 * r),
            111412.84 * math.cos(r) - 93.5 * math.cos(3 * r))


class Projection:
    """Latitude and longitude to yards from an origin, and back."""

    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0
        m_lat, m_lon = meters_per_degree(lat0)
        self.kx, self.ky = m_lon * YARDS_PER_METER, m_lat * YARDS_PER_METER

    def __call__(self, lat, lon):
        return ((lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky)

    def back(self, x, y):
        return (self.lat0 + y / self.ky, self.lon0 + x / self.kx)


def dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def heading(a, b):
    """Compass bearing from a to b in degrees: 0 north, 90 east."""
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360


def step(p, bearing, d):
    """The point d yards from p on a compass bearing."""
    r = math.radians(bearing)
    return (p[0] + d * math.sin(r), p[1] + d * math.cos(r))


def lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def bezier(a, c, b, t):
    """The point at t on the quadratic curve from a to b bent toward c."""
    u = 1 - t
    return (u * u * a[0] + 2 * u * t * c[0] + t * t * b[0], u * u * a[1] + 2 * u * t * c[1] + t * t * b[1])


def bbox(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def path_length(path):
    return sum(dist(a, b) for a, b in zip(path, path[1:]))


def along(path, d):
    """The point d yards along a path from its start (its end if past it)."""
    for a, b in zip(path, path[1:]):
        seg = dist(a, b)
        if d <= seg and seg > 0:
            return lerp(a, b, d / seg)
        d -= seg
    return path[-1]


def project_onto(path, p):
    """(yards along the path to the point nearest p, distance from p to it)."""
    best, best_d, run = 0.0, None, 0.0
    for a, b in zip(path, path[1:]):
        seg = dist(a, b)
        if seg == 0:
            continue
        t = max(0.0, min(1.0, ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / seg ** 2))
        d = dist(p, lerp(a, b, t))
        if best_d is None or d < best_d:
            best, best_d = run + t * seg, d
        run += seg
    return best, (best_d if best_d is not None else dist(p, path[0]))


def ahead_on_path(path, p, reach):
    """Where a shot of `reach` yards from p meets the path further along: the
    first point past p's spot on the path that far from p, else the end."""
    start, _ = project_onto(path, p)
    total = path_length(path)
    if dist(p, path[-1]) <= reach:
        return path[-1]
    # Walk forward until the path is `reach` from p; a yard at a time is
    # plenty for a course (and paths are a few hundred yards).
    d = start
    while d < total:
        q = along(path, d)
        if dist(p, q) >= reach:
            return q
        d += 1.0
    return path[-1]


def ring_area(ring):
    """Signed area (positive counterclockwise)."""
    s = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        s += x0 * y1 - x1 * y0
    return s / 2


class Polygon:
    """One or more rings, filled by the even-odd rule: an inner ring (a
    clearing in a wood, an island green's water) is a hole in the outer one."""

    def __init__(self, rings):
        self.rings = [r for r in rings if len(r) >= 3]
        pts = [p for r in self.rings for p in r]
        self.box = bbox(pts) if pts else (0, 0, 0, 0)

    def __bool__(self):
        return bool(self.rings)

    def contains(self, x, y):
        x0, y0, x1, y1 = self.box
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        inside = False
        for ring in self.rings:
            px, py = ring[-1]
            for qx, qy in ring:
                if (qy > y) != (py > y) and x < (px - qx) * (y - qy) / (py - qy) + qx:
                    inside = not inside
                px, py = qx, qy
        return inside

    def centroid(self):
        """The area-weighted center of the largest ring."""
        ring = max(self.rings, key=lambda r: abs(ring_area(r)))
        a = ring_area(ring)
        if abs(a) < 1e-9:
            return (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))
        cx = cy = 0.0
        for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
            f = x0 * y1 - x1 * y0
            cx += (x0 + x1) * f
            cy += (y0 + y1) * f
        return (cx / (6 * a), cy / (6 * a))



def segment_distance(p, a, b):
    seg2 = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2
    if seg2 == 0:
        return dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / seg2))
    return dist(p, lerp(a, b, t))


def assemble_rings(lines):
    """Closed rings from the pieces of a multipolygon's outline: OSM splits
    long boundaries into several ways, which join end to end (either way
    round). Pieces that never close are dropped."""
    pending = [list(l) for l in lines if len(l) >= 2]
    rings = []
    while pending:
        ring = pending.pop(0)
        while ring[0] != ring[-1]:
            for i, piece in enumerate(pending):
                if piece[0] == ring[-1]:
                    ring += piece[1:]
                elif piece[-1] == ring[-1]:
                    ring += piece[-2::-1]
                elif piece[-1] == ring[0]:
                    ring = piece[:-1] + ring
                elif piece[0] == ring[0]:
                    ring = piece[:0:-1] + ring
                else:
                    continue
                pending.pop(i)
                break
            else:
                break   # an open piece: nothing joins it
        if ring[0] == ring[-1] and len(ring) >= 4:
            rings.append(ring[:-1])
    return rings


class Frame:
    """A view of the course turned so that a bearing points one way on screen.

    Maps course yards to (along, across): `along` the yards from the origin
    in the direction of `bearing`, `across` the yards to the right of it.
    """

    def __init__(self, origin, bearing):
        self.origin, self.bearing = origin, bearing
        r = math.radians(bearing)
        self.ux, self.uy = math.sin(r), math.cos(r)   # along
        self.rx, self.ry = math.cos(r), -math.sin(r)  # to the right

    def to_frame(self, p):
        dx, dy = p[0] - self.origin[0], p[1] - self.origin[1]
        return (dx * self.ux + dy * self.uy, dx * self.rx + dy * self.ry)

    def to_course(self, along_, across):
        return (self.origin[0] + along_ * self.ux + across * self.rx,
                self.origin[1] + along_ * self.uy + across * self.ry)
