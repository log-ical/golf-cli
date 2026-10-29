import array
import base64
import json
import os
import math
import random
import socket
import struct
import tempfile
import time
import unittest
import urllib.error
import zlib
from unittest import mock

from tests.helpers import ROOT, at, heights, tiny_course  # noqa: F401
from golf import course as golf_course, elevation, osm
from golf.geo import YARDS_PER_METER, Projection


def png(w, h, rgb, alpha=False, filters=(0, 1, 2, 3, 4)):
    """An 8-bit PNG of rgb (bytes of R, G, B), each row filtered the next of filters' ways."""
    bpp = 4 if alpha else 3
    rows = []
    for y in range(h):
        px = rgb[y * w * 3:(y + 1) * w * 3]
        rows.append(bytes(b for x in range(w) for b in px[x * 3:x * 3 + 3] + (b"\xff" if alpha else b"")))
    raw, prev = bytearray(), bytes(w * bpp)
    for y, line in enumerate(rows):
        f = filters[y % len(filters)]
        out = bytearray()
        for i, v in enumerate(line):
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], prev[i - bpp] if i >= bpp else 0
            if f == 0:
                pred = 0
            elif f == 1:
                pred = a
            elif f == 2:
                pred = b
            elif f == 3:
                pred = (a + b) // 2
            else:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
            out.append((v - pred) & 255)
        raw += bytes([f]) + out
        prev = line
    chunk = lambda kind, body: struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    head = struct.pack(">IIBBBBB", w, h, 8, 6 if alpha else 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head) + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b"")


def tiff(w, h, values, tile=None):
    """A little-endian, uncompressed 32-bit float TIFF: tiled (tile x tile) or one strip."""
    data = b""
    if tile:
        offsets, counts = [], []
        for ty in range(math.ceil(h / tile)):
            for tx in range(math.ceil(w / tile)):
                block = [values[(ty * tile + i) * w + tx * tile + j] if ty * tile + i < h and tx * tile + j < w else 0.0
                         for i in range(tile) for j in range(tile)]
                offsets.append(8 + len(data))
                data += struct.pack("<%df" % len(block), *block)
                counts.append(4 * len(block))
    else:
        offsets, counts = [8], [4 * w * h]
        data = struct.pack("<%df" % (w * h), *values)
    extra = b""
    ifd_at = 8 + len(data)
    entries = [(256, 3, 1, w), (257, 3, 1, h), (258, 3, 1, 32), (259, 3, 1, 1), (339, 3, 1, 3)]
    n = len(entries) + 2 + (2 if tile else 1)
    after = ifd_at + 2 + 12 * n + 4
    def longs(vals):
        nonlocal extra
        if len(vals) == 1:
            return vals[0]
        at = after + len(extra)
        extra += struct.pack("<%dI" % len(vals), *vals)
        return at
    if tile:
        entries += [(322, 3, 1, tile), (323, 3, 1, tile), (324, 4, len(offsets), longs(offsets)),
                    (325, 4, len(counts), longs(counts))]
    else:
        entries += [(273, 4, 1, offsets[0]), (278, 3, 1, h), (279, 4, 1, counts[0])]
    ifd = struct.pack("<H", len(entries)) + b"".join(struct.pack("<HHII", *e) for e in sorted(entries)) + b"\0\0\0\0"
    head = b"II*\x00" + struct.pack("<I", ifd_at)
    return head + data + ifd + extra


class FormatsTest(unittest.TestCase):
    def test_png(self):
        rng = random.Random(1)
        w, h = 7, 10
        rgb = bytes(rng.randrange(256) for _ in range(w * h * 3))
        for alpha in (False, True):
            self.assertEqual(elevation.png_rgb(png(w, h, rgb, alpha)), (w, h, rgb))

    def test_tiff(self):
        w, h = 5, 7
        vals = [float(i) - 3.5 for i in range(w * h)]
        for tile in (None, 4):
            gw, gh, z = elevation._tiff_floats(tiff(w, h, vals, tile))
            self.assertEqual((gw, gh), (w, h))
            self.assertEqual(list(z), vals)

    def test_heights_keep(self):
        rng = random.Random(2)
        z = array.array("f", [rng.uniform(-3, 400) for _ in range(12 * 9)])
        hts = elevation.Heights(-100.0, 500.0, 2.0, 12, 9, z, "usgs")
        back = elevation.Heights.from_json(hts.to_json())
        self.assertEqual((back.left, back.top, back.cell, back.w, back.h, back.source), (-100.0, 500.0, 2.0, 12, 9, "usgs"))
        self.assertEqual(back.z, z)                                    # exactly

    def test_older_heights_are_read_and_kept_again(self):
        z = array.array("f", [100.0 + 0.01 * i for i in range(12 * 9)])
        cms = [round((v - 100.0) * 100) for v in z]
        steps = [cms[k] - (cms[k - 1] if k % 12 else cms[k - 12] if k else 0) for k in range(len(cms))]
        old = {"v": 2, "left": -100.0, "top": 500.0, "cell": 2.0, "w": 12, "h": 9, "base": 100.0, "source": "usgs",
               "cm": base64.b64encode(zlib.compress(struct.pack("<%di" % len(steps), *steps))).decode()}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "heights.json")
            with open(path, "w") as f:
                json.dump(old, f)
            got = elevation.load(path, None)
            self.assertLess(max(abs(a - b) for a, b in zip(z, got.z)), 0.006)   # to the centimeter
            with open(path) as f:
                self.assertEqual(json.load(f)["v"], 3)                        # kept again, the quicker way
            self.assertEqual(elevation.load(path, None).z, got.z)

    def test_cells_without_heights(self):
        z = array.array("f", [10.0, 11.0, -3.4e38, 12.0, 13.0])
        self.assertTrue(elevation._fill(z))
        self.assertEqual(z[2], 12.0)
        self.assertFalse(elevation._fill(array.array("f", [-3.4e38] * 3 + [1.0])))


class GroundTest(unittest.TestCase):
    def test_in_course_yards(self):
        hts = heights(lambda x, y: 100 + 0.03 * x - 0.01 * y)
        c = osm.parse(tiny_course(), "relation", 1, heights=hts)[0]
        proj = Projection(*c.center)
        for p in ((0, 0), (300, 50), (-40, -120)):
            q = at(c, *p)
            self.assertAlmostEqual(c.ground.z(q), hts.at(*proj.back(*q)) * YARDS_PER_METER, delta=0.01)
            self.assertAlmostEqual(c.ground.z(q), (100 + 0.03 * p[0] - 0.01 * p[1]) * YARDS_PER_METER, delta=0.05)
        gx, gy = c.ground.slope(at(c, 200, 0))
        self.assertAlmostEqual(gx, 0.03 * YARDS_PER_METER, delta=0.002)   # yards a yard
        self.assertAlmostEqual(gy, -0.01 * YARDS_PER_METER, delta=0.002)

    def test_a_steep_green_eased(self):
        hts = heights(lambda x, y: 0.12 * x)                # rising east, steeper than any green
        c = osm.parse(tiny_course(), "relation", 1, heights=hts)[0]
        green = c.holes[0].green
        gx, gy = c.ground.tilt(green)
        self.assertAlmostEqual(math.hypot(gx, gy), elevation.GREEN_TILT, delta=0.003)
        far = at(c, 200, 0)
        self.assertAlmostEqual(c.ground.slope(far)[0], 0.12 * YARDS_PER_METER, delta=0.005)   # the fairway as it was
        flat = osm.parse(tiny_course(), "relation", 1, heights=heights(lambda x, y: 0.02 * x))[0]
        self.assertAlmostEqual(flat.ground.tilt(flat.holes[0].green)[0], 0.02 * YARDS_PER_METER, delta=0.003)

    def test_the_cup_cut_where_its_flat(self):
        # Flat to x=405, then a slope (eased, but still too steep for a cup): the pin's mapped at x=412.
        c = osm.parse(tiny_course(), "relation", 1, heights=heights(lambda x, y: 0.15 * max(0.0, x - 405)))[0]
        hole = c.holes[0]
        self.assertTrue(hole.green.contains(*hole.pin))
        self.assertLess(hole.pin[0], at(c, 408, 0)[0])        # moved back off the slope
        self.assertLessEqual(math.hypot(*c.ground.slope(hole.pin, 1.5)), golf_course.PIN_SLOPE)
        level = osm.parse(tiny_course(), "relation", 1, heights=heights(lambda x, y: 50.0))[0]
        self.assertAlmostEqual(level.holes[0].pin[0], at(level, 412, 2)[0], delta=0.01)   # left where it's mapped


class DownloadTest(unittest.TestCase):
    US = {"minlat": 40.0, "minlon": -83.2, "maxlat": 40.01, "maxlon": -83.19}
    SCOTLAND = {"minlat": 56.34, "minlon": -2.82, "maxlat": 56.35, "maxlon": -2.8}

    def test_lidar_first_then_tiles(self):
        with mock.patch.object(elevation, "_usgs", return_value="lidar") as usgs, \
                mock.patch.object(elevation, "_tiles", return_value="tiles"):
            self.assertEqual(elevation.download(self.US), ("lidar", True))
            self.assertEqual(elevation.download(self.SCOTLAND), ("tiles", True))
            self.assertEqual(usgs.call_count, 1)             # not asked outside the US
        with mock.patch.object(elevation, "_usgs", return_value=None), \
                mock.patch.object(elevation, "_tiles", return_value="tiles"):
            self.assertEqual(elevation.download(self.US), ("tiles", True))    # not the USGS's: kept
        with mock.patch.object(elevation, "_usgs", side_effect=OSError("502")), \
                mock.patch.object(elevation, "_tiles", return_value="tiles"):
            self.assertEqual(elevation.download(self.US), ("tiles", False))   # the USGS is down: asked again next time

    def test_tiles_stand_in_while_the_usgs_is_down(self):
        tiles = elevation.Heights(0.0, 10.0, 5.0, 2, 2, array.array("f", [1.0, 2.0, 3.0, 4.0]), "tiles")
        lidar = elevation.Heights(0.0, 10.0, 2.0, 2, 2, array.array("f", [5.0, 6.0, 7.0, 8.0]), "usgs")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "heights.json")
            with mock.patch.object(elevation, "download", return_value=(tiles, False)) as down:
                self.assertEqual(elevation.load(path, self.US).source, "tiles")
                self.assertEqual(elevation.load(path, self.US).source, "tiles")   # kept: not downloaded again
                self.assertEqual(down.call_count, 1)
            with mock.patch.object(elevation.time, "time", return_value=time.time() + elevation.RETRY + 1), \
                    mock.patch.object(elevation, "download", return_value=(lidar, True)):
                self.assertEqual(elevation.load(path, self.US).source, "usgs")    # a day on: the USGS again
            with mock.patch.object(elevation, "download", side_effect=AssertionError):
                got = elevation.load(path, self.US)
                self.assertEqual((got.source, got.retry), ("usgs", 0.0))       # and that's kept for good

    def test_only_a_busy_server_is_asked_again(self):
        busy = urllib.error.HTTPError("u", 502, "busy", None, None)
        for failure, calls in ((busy, elevation.TRIES), (ConnectionResetError(), elevation.TRIES),
                               (TimeoutError(), elevation.TRIES), (ConnectionRefusedError(), 1),
                               (socket.gaierror(), 1), (urllib.error.HTTPError("u", 404, "no", None, None), 1)):
            with mock.patch.object(elevation, "fetch", side_effect=failure) as fetch, \
                    mock.patch.object(elevation.time, "sleep"):
                with self.assertRaises(type(failure)):
                    elevation._usgs(0.0, 0.0, 100.0, 100.0)
                self.assertEqual(fetch.call_count, calls, failure)
            if isinstance(failure, urllib.error.HTTPError):
                failure.close()


if __name__ == "__main__":
    unittest.main()
