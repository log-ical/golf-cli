"""Where the trees are, from space: ESA WorldCover's land cover (10 m cells,
from the Sentinel satellites), for the trees OpenStreetMap doesn't have.

Mappers map a course's fairways, greens and bunkers, and often skip the
trees between the holes. WorldCover classes every 10 m of the world (tree
cover, shrubs, grass, water, built up...), and is served as cloud-optimized
GeoTIFFs, 3° by 3° each: a course needs the tile's header and the one or two
1024-cell blocks it lies in (deflate-compressed, ~50 KB each), read with
HTTP range requests. The cells round a course are cached with it.

© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data
(2021) processed by ESA WorldCover consortium. CC BY 4.0.
"""

import base64
import concurrent.futures
import json
import math
import os
import struct
import zlib

from .files import write_atomic
from .net import fetch_range

URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{}_Map.tif"
TREES, SHRUBS = 10, 20       # WorldCover classes
TILE_DEG = 3                 # degrees a WorldCover file covers, each way
HEAD = 65536                 # bytes read for a file's header (its tile index is in them)
MARGIN = 0.004               # degrees round the course's outline
AT_ONCE = 4                  # blocks asked for at once


class Cover:
    """WorldCover's classes in a lat/lon window: cell (i, j) is row i from
    the window's north edge, column j from its west, `step` degrees each."""

    def __init__(self, north, west, step, w, h, cells):
        self.north, self.west, self.step, self.w, self.h, self.cells = north, west, step, w, h, cells

    def at(self, lat, lon):
        """The class of the cell a point is in (0 outside the window)."""
        i, j = int((self.north - lat) / self.step), int((lon - self.west) / self.step)
        return self.cells[i * self.w + j] if 0 <= i < self.h and 0 <= j < self.w else 0

    def trees(self, lat, lon):
        """How wooded a point is, 0 to 1: the tree cells round it, blended
        (so a wood's edge runs between cells, not along them)."""
        y, x = (self.north - lat) / self.step - 0.5, (lon - self.west) / self.step - 0.5
        i, j = math.floor(y), math.floor(x)
        fy, fx = y - i, x - j
        w, cells = self.w, self.cells
        if 0 <= i < self.h - 1 and 0 <= j < w - 1:   # all four cells in the window (the usual case, quicker)
            k, v = i * w + j, 0.0
            if cells[k] == TREES:
                v += (1 - fy) * (1 - fx)
            if cells[k + 1] == TREES:
                v += (1 - fy) * fx
            if cells[k + w] == TREES:
                v += fy * (1 - fx)
            if cells[k + w + 1] == TREES:
                v += fy * fx
            return v
        v = 0.0
        for di, wy in ((0, 1 - fy), (1, fy)):
            for dj, wx in ((0, 1 - fx), (1, fx)):
                ii, jj = i + di, j + dj
                if 0 <= ii < self.h and 0 <= jj < self.w and self.cells[ii * self.w + jj] == TREES:
                    v += wy * wx
        return v

    def to_json(self):
        return {"north": self.north, "west": self.west, "step": self.step, "w": self.w, "h": self.h,
                "cells": base64.b64encode(zlib.compress(bytes(self.cells))).decode()}

    @classmethod
    def from_json(cls, d):
        cells = zlib.decompress(base64.b64decode(d["cells"]))
        if len(cells) != d["w"] * d["h"]:
            raise ValueError("cover cells don't fill the window")
        return cls(float(d["north"]), float(d["west"]), float(d["step"]), int(d["w"]), int(d["h"]), cells)


def tile_name(lat, lon):
    """The WorldCover file a point is in: named for its south-west corner."""
    la, lo = math.floor(lat / TILE_DEG) * TILE_DEG, math.floor(lon / TILE_DEG) * TILE_DEG
    return "%s%02d%s%03d" % ("N" if la >= 0 else "S", abs(la), "E" if lo >= 0 else "W", abs(lo))


def _header(url):
    """(width, tile size, tile offsets, tile byte counts, pixel scale, west, north) of a file's full image."""
    head = fetch_range(url, 0, HEAD - 1)
    if head[:4] != b"II*\x00":
        raise ValueError("not a little-endian TIFF")
    off = struct.unpack_from("<I", head, 4)[0]
    n = struct.unpack_from("<H", head, off)[0]
    tags = {}
    for k in range(n):
        tag, typ, cnt, val = struct.unpack_from("<HHII", head, off + 2 + 12 * k)
        fmt, size = {3: ("H", 2), 4: ("I", 4), 12: ("d", 8)}.get(typ, (None, 0))
        if fmt is None:
            continue
        at = off + 2 + 12 * k + 8 if cnt * size <= 4 else val
        if at + cnt * size > len(head):   # beyond what was read: fetch it
            data = fetch_range(url, at, at + cnt * size - 1)
            tags[tag] = struct.unpack("<%d%s" % (cnt, fmt), data)
        else:
            tags[tag] = struct.unpack_from("<%d%s" % (cnt, fmt), head, at)
    if tags.get(259, (0,))[0] != 8 or tags.get(258, (0,))[0] != 8:
        raise ValueError("not 8-bit deflate tiles")
    return (tags[256][0], tags[322][0], tags[324], tags[325], tags[33550][0], tags[33922][3], tags[33922][4])


def download(bounds):
    """The cover round a course whose bounding box (Overpass's: minlat, minlon,
    maxlat, maxlon) is bounds, MARGIN degrees more each way."""
    south, west = bounds["minlat"] - MARGIN, bounds["minlon"] - MARGIN
    north, east = bounds["maxlat"] + MARGIN, bounds["maxlon"] + MARGIN
    step = 1 / 12000
    w, h = int(math.ceil((east - west) / step)), int(math.ceil((north - south) / step))
    cells = bytearray(w * h)
    names = {tile_name(lat, lon) for lat in (south, north - 1e-9) for lon in (west, east - 1e-9)}
    for name in names:
        url = URL.format(name)
        width, size, offsets, counts, scale, x0, y0 = _header(url)
        across = math.ceil(width / size)
        # Each of the window's rows and columns: the file's row or column it's in, if it's in the file.
        rows = [(i, row) for i, row in ((i, int((y0 - (north - (i + 0.5) * step)) / scale)) for i in range(h))
                if 0 <= row < width]
        cols = [(j, col) for j, col in ((j, int((west + (j + 0.5) * step - x0) / scale)) for j in range(w))
                if 0 <= col < width]
        keys = sorted({br * across + bc for br in {r // size for _, r in rows} for bc in {c // size for _, c in cols}})
        with concurrent.futures.ThreadPoolExecutor(AT_ONCE) as pool:   # the blocks it takes, all at once
            got = pool.map(lambda k: zlib.decompress(fetch_range(url, offsets[k], offsets[k] + counts[k] - 1)), keys)
            blocks = dict(zip(keys, got))
        for i, row in rows:
            for j, col in cols:
                cells[i * w + j] = blocks[(row // size) * across + col // size][(row % size) * size + col % size]
    return Cover(north, west, step, w, h, bytes(cells))


def load(path, bounds):
    """The cover cached at path, else downloaded (and cached); None if neither can be had."""
    try:
        with open(path, encoding="utf-8") as f:
            return Cover.from_json(json.load(f))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if not bounds:
        return None
    try:
        cover = download(bounds)
    except Exception:
        return None
    try:
        write_atomic(path, json.dumps(cover.to_json()).encode())
    except OSError:
        pass
    return cover


def exists(path):
    return os.path.exists(path)
