import json
import os
import tempfile
import unittest
from unittest import mock

from tests.helpers import ROOT, at, tiny_around, tiny_course, two_loops  # noqa: F401
from golf import osm
from golf.geo import dist


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.layouts = osm.parse(tiny_course(), "relation", 1)
        self.course = self.layouts[0]

    def test_layouts_by_course_name(self):
        self.assertEqual([(c.layout, len(c.holes)) for c in self.layouts], [("", 3), ("Short", 3)])
        self.assertEqual(self.course.name, "Tiny Links")
        self.assertEqual(self.course.par, 10)
        self.assertAlmostEqual(self.course.center[0], 40.0, places=2)

    def test_hole_details(self):
        h1 = self.course.holes[0]
        self.assertEqual((h1.number, h1.par, h1.name, h1.handicap), (1, 4, "First", 3))
        self.assertAlmostEqual(dist(h1.pin, at(self.course, 412, 2)), 0, delta=0.01)   # the mapped pin
        self.assertTrue(h1.green.contains(*h1.pin))
        self.assertAlmostEqual(dist(h1.tee, at(self.course, 0, 0)), 0, delta=0.01)
        self.assertGreater(h1.yards, 400)

    def test_hole_drawn_backwards_is_turned_round(self):
        h2 = self.course.holes[1]
        self.assertAlmostEqual(dist(h2.tee, at(self.course, 420, 80)), 0, delta=0.01)
        self.assertTrue(h2.green.contains(*h2.pin))
        self.assertLess(dist(h2.pin, at(self.course, 250, 80)), 1)

    def test_par_from_length_and_green_made_up_when_missing(self):
        self.assertEqual(self.course.holes[2].par, 3)
        short = self.layouts[1]
        self.assertEqual([h.par for h in short.holes], [3, 3, 3])
        self.assertTrue(all(h.green.contains(*h.pin) for h in short.holes))

    def test_repeated_numbers_chain_into_courses(self):
        a, b = osm.parse(two_loops(), "way", 1)
        self.assertEqual((len(a.holes), len(b.holes)), (3, 3))
        # The longer course first, each kept together.
        self.assertTrue(all(abs(h.tee[1] - at(a, 0, 0)[1]) < 1 for h in a.holes))
        self.assertTrue(all(abs(h.tee[1] - at(a, 0, 300)[1]) < 1 for h in b.holes))
        self.assertEqual((a.layout, b.layout), ("course 1", "course 2"))

    def test_course_without_holes_is_an_error(self):
        data = {"elements": [{"type": "way", "id": 5, "tags": {"name": "Bare"},
                              "geometry": [{"lat": 0, "lon": 0}, {"lat": 0, "lon": 0.01},
                                           {"lat": 0.01, "lon": 0.01}, {"lat": 0, "lon": 0}]}]}
        with self.assertRaises(osm.CourseError):
            osm.parse(data, "way", 5)
        with self.assertRaises(osm.CourseError):
            osm.parse(data, "way", 6)


class SurfaceTest(unittest.TestCase):
    def setUp(self):
        self.c = osm.parse(tiny_course(), "relation", 1)[0]

    def test_surfaces(self):
        on = lambda x, y: self.c.surface_at(at(self.c, x, y))
        self.assertEqual(on(0, 0), "tee")
        self.assertEqual(on(150, 0), "fairway")
        self.assertEqual(on(250, 10), "bunker")      # a bunker in the fairway is a bunker
        self.assertEqual(on(410, 0), "green")
        self.assertEqual(on(300, -50), "water")
        self.assertEqual(on(100, -50), "water")      # the creek, mapped as a line
        self.assertEqual(on(200, 40), "trees")       # under the single tree
        self.assertEqual(on(200, 60), "rough")
        self.assertEqual(on(700, 0), "ob")           # outside the boundary


class TeesTest(unittest.TestCase):
    def setUp(self):
        self.c = osm.parse(tiny_course(), "relation", 1)[0]

    def test_tees_back_to_front_in_their_colors(self):
        h1 = self.c.holes[0]
        self.assertEqual([t.color for t in h1.tees], ["blue", "pink"])   # the usual for two, unless tagged
        self.assertAlmostEqual(dist(h1.tees[1].pos, at(self.c, 60, 0)), 0, delta=0.5)
        self.assertEqual([t.color for t in self.c.holes[1].tees], [])     # no tee box mapped

    def test_playing_from_a_set(self):
        h1 = self.c.holes[0]
        forward = h1.played_from("red")          # no red: the nearest, pink
        self.assertEqual(forward.color, "pink")
        self.assertAlmostEqual(h1.yards - forward.yards, 60, delta=2)
        self.assertEqual(forward.pin, h1.pin)
        self.assertIs(self.c.holes[1].played_from("red"), self.c.holes[1])   # nothing to choose from
        sets = dict(self.c.tee_colors())
        self.assertEqual(set(sets), {"blue", "pink"})
        self.assertLess(sets["pink"], sets["blue"])


class CoverTest(unittest.TestCase):
    def test_tiles_and_cells(self):
        from golf import landcover
        self.assertEqual(landcover.tile_name(40.1, -83.1), "N39W084")
        self.assertEqual(landcover.tile_name(-33.9, 18.4), "S36E018")
        cover = landcover.Cover(1.0, 0.0, 0.5, 2, 2, bytes([10, 30, 10, 10]))
        self.assertEqual(cover.at(0.9, 0.1), 10)
        self.assertEqual(cover.at(0.9, 0.9), 30)
        self.assertEqual(cover.at(5, 5), 0)
        self.assertAlmostEqual(cover.trees(0.25, 0.25), 1.0)       # in the middle of a wooded cell
        self.assertAlmostEqual(cover.trees(0.75, 0.5), 0.5)         # half way to the grass
        again = landcover.Cover.from_json(json.loads(json.dumps(cover.to_json())))
        self.assertEqual((again.w, again.h, again.cells), (2, 2, cover.cells))

    def test_woods_from_space_play_as_trees(self):
        from golf import landcover
        from tests.helpers import ll
        north, west = ll(-100, 300)["lat"], ll(-100, 300)["lon"]
        step = 1 / 12000
        w, h = 80, 60
        south_edge = ll(0, 30)["lat"]          # trees north of y=30, grass south of it
        cells = bytes(10 if north - (i + 0.5) * step > south_edge else 30 for i in range(h) for j in range(w))
        c = osm.parse(tiny_course(), "relation", 1, cover=landcover.Cover(north, west, step, w, h, cells))[0]
        on = lambda x, y: c.surface_at(at(c, x, y))
        self.assertEqual(on(150, 70), "trees")
        self.assertEqual(on(150, -40), "rough")
        self.assertEqual(on(150, 0), "fairway")   # the course's own ground stays as mapped
        self.assertEqual(on(410, 0), "green")


class AroundTest(unittest.TestCase):
    def setUp(self):
        self.c = osm.parse(tiny_course(), "relation", 1, tiny_around())[0]

    def test_kinds(self):
        kinds = {f.kind for f in self.c.features}
        self.assertTrue({"building", "flowers", "heath", "road", "bunker"} <= kinds)
        self.assertEqual(len(self.c.coast), 1)
        self.assertEqual(len(self.c.trees), 2)            # the one in both downloads once
        pine = next(t for t in self.c.trees if t.leaf)
        self.assertEqual(pine.leaf, "needle")
        self.assertAlmostEqual(pine.r, 6 * 1.0936, places=2)   # half a 12 m crown, in yards

    def test_how_they_play(self):
        on = lambda x, y: self.c.surface_at(at(self.c, x, y))
        self.assertEqual(on(30, 108), "ob")       # the clubhouse
        self.assertEqual(on(100, 62), "trees")    # in the azaleas
        self.assertEqual(on(680, 60), "ob")       # a beach out of bounds is out of bounds
        self.assertEqual(on(680, 120), "ob")
        self.assertEqual(on(150, -60), "trees")   # under the pine's wide crown...
        self.assertEqual(on(150, -66), "trees")
        self.assertEqual(on(150, -75), "rough")   # ...and past it

    def test_load_gets_both_at_once(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(osm, "COURSE_DIR", d):
            asked = []

            def fake_fetch(url, timeout=0):
                asked.append(url)
                if "out%20bb" in url or "out+bb" in url:
                    return json.dumps({"elements": [{"type": "relation", "id": 1, "bounds": {
                        "minlat": 39.99, "minlon": -75.01, "maxlat": 40.01, "maxlon": -74.99}}]}).encode()
                return json.dumps(tiny_around() if "bbox" in url else tiny_course()).encode()
            # (the heights and the land cover are their own tests')
            with mock.patch.object(osm, "fetch", fake_fetch), mock.patch.object(osm.elevation, "load", lambda *a: None), \
                    mock.patch.object(osm.landcover, "load", lambda *a: None):
                c = osm.load("relation", 1)[0]
                self.assertIn("building", {f.kind for f in c.features})
                self.assertEqual(len(asked), 3)            # the outline, the course, around it
                osm.load("relation", 1)
                self.assertEqual(len(asked), 3)            # then both from the cache
            self.assertTrue(os.path.exists(osm.around_path("relation", 1)))


class CacheTest(unittest.TestCase):
    def test_download_uses_the_cache_and_keeps_only_whole_courses(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(osm, "COURSE_DIR", d):
            calls = []

            def fake_fetch(url, timeout=0):
                calls.append(url)
                return json.dumps(tiny_course()).encode()
            with mock.patch.object(osm, "fetch", fake_fetch):
                self.assertEqual(len(osm.download("relation", 1)["elements"]), len(tiny_course()["elements"]))
                osm.download("relation", 1)
            self.assertEqual(len(calls), 1)
            self.assertTrue(os.path.exists(osm.cache_path("relation", 1)))

            partial = dict(tiny_course(), remark="runtime error: Query timed out")
            with mock.patch.object(osm, "fetch", lambda url, timeout=0: json.dumps(partial).encode()):
                with self.assertRaises(osm.CourseError):
                    osm.download("relation", 2)
            self.assertFalse(os.path.exists(osm.cache_path("relation", 2)))


if __name__ == "__main__":
    unittest.main()
