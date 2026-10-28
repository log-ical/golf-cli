"""How high the ground is: the hills a hole plays over, the banks round a
green, and the slopes a putt breaks on.

In the US, from the USGS's 3D Elevation Program: bare-earth heights from
airborne lidar (1 m and finer; the trees and houses taken out), asked for
in 2 m cells of Web Mercator: enough to read a green's contours.
Anywhere else (or when the USGS's server is down), from the terrain tiles
Mapzen made for Amazon's open data (SRTM and the best national data they
could get: tens of meters, so the hills, not the greens). The heights round
a course are cached with it.

USGS 3DEP: public domain. Terrain tiles: Mapzen, from SRTM, GMTED2010, NED,
ETOPO1 and others (github.com/tilezen/joerd/blob/master/docs/attribution.md).
"""

import array
import base64
import itertools
import json
import math
import struct
import time
import zlib

from .files import write_atomic
from .geo import YARDS_PER_METER, segment_distance
from .net import fetch

USGS = ("https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
        "?bbox={},{},{},{}&bboxSR=3857&imageSR=3857&size={},{}&format=tiff&pixelType=F32&f=image")
TILES = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
R = 6378137.0          # the Web Mercator sphere's radius, meters
CELL = 2.0             # Web Mercator meters to a cell asked of the USGS (1.5 m on the ground at 40°)
CHUNK = 800            # cells each way in one request
ZOOM = 15              # the terrain tiles' most detailed (5 m cells at the equator)
MARGIN = 0.002         # degrees round the course's outline
TRIES = 3              # the USGS's server often answers a busy moment with a 502
LOW, HIGH = -500.0, 9000.0   # meters: outside this, a cell has no height (the USGS's no-data)
GAPS = 0.2             # more of a window than this without heights: not the USGS's to give (outside the US)
GREEN_TILT = 0.04      # rise a yard: the most a green's surface tilts, taken as a whole
GREEN_BLEND = 4.0      # yards round a green a tamed tilt fades out over


def _bilinear(z, w, h, fx, fy):
    """The heights z (w by h, row by row) at fractional cell (fx, fy), blended."""
    fx, fy = max(0.0, min(w - 1.001, fx)), max(0.0, min(h - 1.001, fy))
    j, i = int(fx), int(fy)
    a, b = fx - j, fy - i
    r0 = i * w + j
    r1 = r0 + w
    return (z[r0] * (1 - a) + z[r0 + 1] * a) * (1 - b) + (z[r1] * (1 - a) + z[r1 + 1] * a) * b


def merc(lat, lon):
    """Web Mercator (x, y) meters of a latitude and longitude."""
    lat = max(-85.0, min(85.0, lat))
    return math.radians(lon) * R, math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R


def unmerc(x, y):
    """The latitude and longitude of Web Mercator (x, y)."""
    return math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2), math.degrees(x / R)


class Heights:
    """Heights in meters on a Web Mercator grid: cell (i, j) is row i down
    from the top edge, column j from the left, `cell` meters each way."""

    def __init__(self, left, top, cell, w, h, z, source=""):
        self.left, self.top, self.cell, self.w, self.h = left, top, cell, w, h
        self.z = z               # array("f"), row by row
        self.source = source     # "usgs" or "tiles"

    def at(self, lat, lon):
        x, y = merc(lat, lon)
        return _bilinear(self.z, self.w, self.h, (x - self.left) / self.cell - 0.5, (self.top - y) / self.cell - 0.5)

    def to_json(self):
        """Centimeters, each as the step from the one before it in its row
        (so the gentle ground compresses well)."""
        base = min(self.z)
        cms = [int(round((v - base) * 100)) for v in self.z]
        steps = [(cms[k] - (cms[k - 1] if k % self.w else 0)) & 0xFFFF for k in range(len(cms))]
        data = struct.pack("<%dH" % len(steps), *steps)
        return {"left": self.left, "top": self.top, "cell": self.cell, "w": self.w, "h": self.h, "base": base,
                "source": self.source, "cm": base64.b64encode(zlib.compress(data, 6)).decode()}

    @classmethod
    def from_json(cls, d):
        w, h, base = int(d["w"]), int(d["h"]), float(d["base"])
        raw = zlib.decompress(base64.b64decode(d["cm"]))
        if len(raw) != 2 * w * h:
            raise ValueError("heights don't fill the window")
        steps = struct.unpack("<%dH" % (w * h), raw)
        z = array.array("f")
        for i in range(h):
            z.extend(base + (cm & 0xFFFF) / 100 for cm in itertools.accumulate(steps[i * w:(i + 1) * w]))
        return cls(float(d["left"]), float(d["top"]), float(d["cell"]), w, h, z, str(d.get("source", "")))


class Ground:
    """The heights in a course's own yards (geo.Projection's): z(p), yards
    above sea level; slope(p), how it rises (yards a yard) to the east and
    north. Over a course, yards to Web Mercator is as good as linear.

    A green the heights have tilted more than GREEN_TILT (a green's polygon
    a few yards off where the lidar has it, or cells too big to tell it from
    the bank it's cut into) is eased back to that (tame), its undulations
    kept: no green is built so steep that a ball won't stop on it."""

    def __init__(self, heights, proj):
        self.heights = heights
        self.w, self.h = heights.w, heights.h
        self.cells = array.array("f", heights.z)   # its own, to tame
        mx, my = merc(proj.lat0, proj.lon0)
        cos = math.cos(math.radians(proj.lat0))
        self.ax = R * math.radians(1 / proj.kx) / heights.cell          # cells a yard east
        self.ay = R * math.radians(1 / proj.ky) / cos / heights.cell    # cells a yard north
        self.bx = (mx - heights.left) / heights.cell - 0.5
        self.by = (heights.top - my) / heights.cell - 0.5

    def z(self, p):
        return _bilinear(self.cells, self.w, self.h, self.bx + p[0] * self.ax, self.by - p[1] * self.ay) * YARDS_PER_METER

    def tilt(self, poly):
        """(rise a yard east, north) of the plane that best fits the ground in a polygon."""
        x0, y0, x1, y1 = poly.box
        pts = [(x0 + i + 0.5, y0 + j + 0.5) for i in range(int(x1 - x0) + 1) for j in range(int(y1 - y0) + 1)]
        pts = [p for p in pts if poly.contains(*p)]
        if len(pts) < 6:
            return 0.0, 0.0
        cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        zs = [self.z(p) for p in pts]
        cz = sum(zs) / len(zs)
        sxx = sum((p[0] - cx) ** 2 for p in pts)
        syy = sum((p[1] - cy) ** 2 for p in pts)
        sxy = sum((p[0] - cx) * (p[1] - cy) for p in pts)
        sxz = sum((p[0] - cx) * (z - cz) for p, z in zip(pts, zs))
        syz = sum((p[1] - cy) * (z - cz) for p, z in zip(pts, zs))
        det = sxx * syy - sxy * sxy
        if det <= 0:
            return 0.0, 0.0
        return (sxz * syy - syz * sxy) / det, (syz * sxx - sxz * sxy) / det

    def tame(self, poly):
        """Ease a green's tilt back to GREEN_TILT if it's more, fading out
        over GREEN_BLEND yards round it."""
        gx, gy = self.tilt(poly)
        steep = math.hypot(gx, gy)
        if steep <= GREEN_TILT * 1.01:
            return False
        k = 1 - GREEN_TILT / steep           # the part of the tilt to take out
        x0, y0, x1, y1 = poly.box
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        edges = [(a, b) for ring in poly.rings for a, b in zip(ring, ring[1:] + ring[:1])]
        fx0, fx1 = sorted((self.bx + (x0 - GREEN_BLEND) * self.ax, self.bx + (x1 + GREEN_BLEND) * self.ax))
        fy0, fy1 = sorted((self.by - (y0 - GREEN_BLEND) * self.ay, self.by - (y1 + GREEN_BLEND) * self.ay))
        for i in range(max(0, int(fy0)), min(self.h, int(fy1) + 2)):
            for j in range(max(0, int(fx0)), min(self.w, int(fx1) + 2)):
                x, y = (j - self.bx) / self.ax, (self.by - i) / self.ay   # the cell's middle, in yards
                if poly.contains(x, y):
                    f = 1.0
                else:
                    d = min(segment_distance((x, y), a, b) for a, b in edges)
                    if d >= GREEN_BLEND:
                        continue
                    f = 1 - d / GREEN_BLEND
                self.cells[i * self.w + j] -= f * k * (gx * (x - cx) + gy * (y - cy)) / YARDS_PER_METER
        return True

    def slope(self, p, d=1.0):
        x, y = p
        return ((self.z((x + d, y)) - self.z((x - d, y))) / (2 * d),
                (self.z((x, y + d)) - self.z((x, y - d))) / (2 * d))


# --------------------------------------------------------------------------
# The USGS's lidar

def _tiff_floats(b):
    """(width, height, heights) of an uncompressed 32-bit float TIFF, tiled or in strips."""
    if b[:4] != b"II*\x00":
        raise ValueError("not a little-endian TIFF")
    off = struct.unpack_from("<I", b, 4)[0]
    tags = {}
    for k in range(struct.unpack_from("<H", b, off)[0]):
        tag, typ, cnt, val = struct.unpack_from("<HHII", b, off + 2 + 12 * k)
        fmt = {3: "H", 4: "I"}.get(typ)
        if fmt:
            size = struct.calcsize(fmt)
            at = off + 2 + 12 * k + 8 if cnt * size <= 4 else val
            tags[tag] = struct.unpack_from("<%d%s" % (cnt, fmt), b, at)
    w, h = tags[256][0], tags[257][0]
    if tags.get(259, (1,))[0] != 1 or tags.get(258, (0,))[0] != 32 or tags.get(339, (0,))[0] != 3:
        raise ValueError("not uncompressed 32-bit floats")
    z = array.array("f", bytes(4 * w * h))
    if 322 in tags:
        tw, th = tags[322][0], tags[323][0]
        across = math.ceil(w / tw)
        for t, o in enumerate(tags[324]):
            ty, tx = divmod(t, across)
            block = struct.unpack_from("<%df" % (tw * th), b, o)
            for i in range(th):
                y = ty * th + i
                if y >= h:
                    break
                n = min(tw, w - tx * tw)
                z[y * w + tx * tw:y * w + tx * tw + n] = array.array("f", block[i * tw:i * tw + n])
    else:
        rows = tags.get(278, (h,))[0]
        for s, o in enumerate(tags[273]):
            n = min(rows, h - s * rows) * w
            z[s * rows * w:s * rows * w + n] = array.array("f", struct.unpack_from("<%df" % n, b, o))
    return w, h, z


def _usgs(x0, y0, x1, y1):
    """The USGS's heights over a Web Mercator box, CELL meters a cell, in
    CHUNK-cell requests; None where it has none (most of it: outside the US)."""
    w, h = int(math.ceil((x1 - x0) / CELL)), int(math.ceil((y1 - y0) / CELL))
    top = y0 + h * CELL
    z = array.array("f", bytes(4 * w * h))
    for cy in range(0, h, CHUNK):
        for cx in range(0, w, CHUNK):
            cw, ch = min(CHUNK, w - cx), min(CHUNK, h - cy)
            left, up = x0 + cx * CELL, top - cy * CELL
            url = USGS.format(left, up - ch * CELL, left + cw * CELL, up, cw, ch)
            for k in range(TRIES):
                try:
                    gw, gh, got = _tiff_floats(fetch(url, timeout=90))
                    break
                except (OSError, ValueError, KeyError, struct.error):
                    if k == TRIES - 1:
                        raise
                    time.sleep(3 * (k + 1))
            if (gw, gh) != (cw, ch):
                raise ValueError("the USGS sent %dx%d cells, not %dx%d" % (gw, gh, cw, ch))
            for i in range(ch):
                z[(cy + i) * w + cx:(cy + i) * w + cx + cw] = got[i * cw:(i + 1) * cw]
    if not _fill(z):
        return None
    return Heights(x0, top, CELL, w, h, z, "usgs")


def _fill(z):
    """Give the cells with no height (or a nonsense one) the median of the
    rest; False if too many have none."""
    good = sorted(v for v in z if LOW < v < HIGH)
    if len(good) < len(z) * (1 - GAPS):
        return False
    mid = good[len(good) // 2]
    for i, v in enumerate(z):
        if not LOW < v < HIGH:
            z[i] = mid
    return True


# --------------------------------------------------------------------------
# The terrain tiles

def png_rgb(data):
    """(width, height, pixels as bytes of R, G, B) of an 8-bit RGB or RGBA PNG."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, head = 8, [], None
    while pos < len(data):
        n, kind = struct.unpack_from(">I4s", data, pos)
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            head = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + n
    if head is None:
        raise ValueError("no IHDR")
    w, h, depth, color, _, _, interlace = head
    if depth != 8 or color not in (2, 6) or interlace:
        raise ValueError("not 8-bit RGB")
    bpp = 3 if color == 2 else 4
    raw = zlib.decompress(b"".join(idat))
    stride = w * bpp
    out = bytearray(w * h * 3)
    prev = bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if f == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                line[i] = (line[i] + ((line[i - bpp] if i >= bpp else 0) + prev[i]) // 2) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b, c = prev[i], prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        elif f:
            raise ValueError("bad PNG filter")
        if bpp == 3:
            out[y * w * 3:(y + 1) * w * 3] = line
        else:
            for x in range(w):
                out[(y * w + x) * 3:(y * w + x) * 3 + 3] = line[x * 4:x * 4 + 3]
        prev = line
    return w, h, bytes(out)


def _tiles(x0, y0, x1, y1):
    """The terrain tiles' heights over a Web Mercator box."""
    n = 2 ** ZOOM
    world = 2 * math.pi * R
    size = world / n                                    # meters a tile spans
    tx0, tx1 = int((x0 + world / 2) // size), int((x1 + world / 2) // size)
    ty0, ty1 = int((world / 2 - y1) // size), int((world / 2 - y0) // size)
    px = 256
    w, h = (tx1 - tx0 + 1) * px, (ty1 - ty0 + 1) * px
    z = array.array("f", bytes(4 * w * h))
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            tw, th, rgb = png_rgb(fetch(TILES.format(z=ZOOM, x=tx, y=ty), timeout=30))
            if (tw, th) != (px, px):
                raise ValueError("a %dx%d tile" % (tw, th))
            for i in range(px):
                row = ((ty - ty0) * px + i) * w + (tx - tx0) * px
                for j in range(px):
                    k = (i * px + j) * 3
                    z[row + j] = rgb[k] * 256 + rgb[k + 1] + rgb[k + 2] / 256 - 32768
    _fill(z)
    return Heights(tx0 * size - world / 2, world / 2 - ty0 * size, size / px, w, h, z, "tiles")


# --------------------------------------------------------------------------

def download(bounds):
    """(heights, whether to keep them) round a course whose bounding box
    (Overpass's: minlat, minlon, maxlat, maxlon) is bounds: the USGS's
    where it has them, else the terrain tiles (not kept if the USGS was
    only out of reach, so it's asked again next time)."""
    x0, y0 = merc(bounds["minlat"] - MARGIN, bounds["minlon"] - MARGIN)
    x1, y1 = merc(bounds["maxlat"] + MARGIN, bounds["maxlon"] + MARGIN)
    keep = True
    if -180 <= bounds["minlon"] <= -64 and 17 <= bounds["minlat"] <= 72:   # the US's, if anyone's
        try:
            got = _usgs(x0, y0, x1, y1)
            if got is not None:
                return got, True
        except (OSError, ValueError, KeyError, struct.error):
            keep = False
    return _tiles(x0, y0, x1, y1), keep


def load(path, bounds):
    """The heights cached at path, else downloaded (and cached); None if neither can be had."""
    try:
        with open(path, encoding="utf-8") as f:
            return Heights.from_json(json.load(f))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if not bounds:
        return None
    try:
        heights, keep = download(bounds)
    except Exception:
        return None
    if keep:
        try:
            write_atomic(path, json.dumps(heights.to_json()).encode())
        except OSError:
            pass
    return heights
