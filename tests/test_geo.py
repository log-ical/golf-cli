import math
import unittest

from tests.helpers import ROOT  # noqa: F401  (sets sys.path)
from golf import geo


class ProjectionTest(unittest.TestCase):
    def test_round_trip_and_scale(self):
        proj = geo.Projection(56.34, -2.8)
        x, y = proj(56.35, -2.79)
        lat, lon = proj.back(x, y)
        self.assertAlmostEqual(lat, 56.35, places=9)
        self.assertAlmostEqual(lon, -2.79, places=9)
        # A hundredth of a degree of latitude is about 1,209 yards anywhere.
        self.assertAlmostEqual(proj(56.35, -2.8)[1], 1209, delta=3)


class PathTest(unittest.TestCase):
    path = [(0, 0), (100, 0), (100, 100)]   # a dogleg left... as drawn, north after 100 east

    def test_along_and_length(self):
        self.assertEqual(geo.path_length(self.path), 200)
        self.assertEqual(geo.along(self.path, 150), (100, 50))
        self.assertEqual(geo.along(self.path, 500), (100, 100))

    def test_project_onto(self):
        d, off = geo.project_onto(self.path, (120, 40))
        self.assertAlmostEqual(d, 140)
        self.assertAlmostEqual(off, 20)

    def test_ahead_on_path_follows_the_dogleg(self):
        p = geo.ahead_on_path(self.path, (0, 0), 120)
        self.assertAlmostEqual(geo.dist((0, 0), p), 120, delta=1.0)
        self.assertEqual(p[0], 100)   # round the corner, not straight on
        self.assertEqual(geo.ahead_on_path(self.path, (90, 90), 50), (100, 100))

    def test_heading_and_step(self):
        self.assertAlmostEqual(geo.heading((0, 0), (0, 10)), 0)
        self.assertAlmostEqual(geo.heading((0, 0), (10, 0)), 90)
        x, y = geo.step((0, 0), 90, 10)
        self.assertAlmostEqual(x, 10)
        self.assertAlmostEqual(y, 0, places=9)


class PolygonTest(unittest.TestCase):
    def test_even_odd_with_a_hole(self):
        outer = [(0, 0), (10, 0), (10, 10), (0, 10)]
        inner = [(4, 4), (6, 4), (6, 6), (4, 6)]
        p = geo.Polygon([outer, inner])
        self.assertTrue(p.contains(1, 1))
        self.assertFalse(p.contains(5, 5))
        self.assertFalse(p.contains(11, 5))

    def test_centroid(self):
        p = geo.Polygon([[(0, 0), (4, 0), (4, 2), (0, 2)]])
        self.assertEqual(p.centroid(), (2, 1))

    def test_assemble_rings_joins_pieces_either_way_round(self):
        a = [(0, 0), (1, 0), (1, 1)]
        b = [(0, 0), (0, 1), (1, 1)]   # runs the other way: has to be reversed to join
        rings = geo.assemble_rings([a, b])
        self.assertEqual(len(rings), 1)
        self.assertEqual(sorted(rings[0]), [(0, 0), (0, 1), (1, 0), (1, 1)])
        self.assertEqual(geo.assemble_rings([[(0, 0), (1, 0)]]), [])   # never closes


class FrameTest(unittest.TestCase):
    def test_round_trip_and_axes(self):
        f = geo.Frame((10, 10), 90)   # looking east
        self.assertEqual(tuple(round(v, 9) for v in f.to_frame((20, 10))), (10, 0))
        self.assertEqual(tuple(round(v, 9) for v in f.to_frame((10, 0))), (0, 10))   # south is to the right
        p = f.to_course(*f.to_frame((3.5, -7)))
        self.assertTrue(math.isclose(p[0], 3.5) and math.isclose(p[1], -7))


if __name__ == "__main__":
    unittest.main()
