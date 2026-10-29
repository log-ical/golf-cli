import collections
import math
import re
import unittest

from tests.helpers import ROOT, at, tiny_around, tiny_course  # noqa: F401
from golf import osm, render
from golf.geo import dist
from golf.term import Colors, visible_len

_SGR = re.compile(r"\x1b\[[0-9;]*m")


class MapTest(unittest.TestCase):
    def setUp(self):
        self.c = osm.parse(tiny_course(), "relation", 1)[0]
        self.hole = self.c.holes[0]

    def test_size_and_way(self):
        for cols, rows, way in ((100, 20, "across"), (30, 40, "up")):
            m = render.HoleMap(self.c, self.hole, cols, rows)
            self.assertEqual(m.way, way)   # whichever shows the hole bigger
            lines = m.lines(Colors("truecolor"))
            self.assertEqual(len(lines), rows)
            self.assertTrue(all(visible_len(l) == cols for l in lines))

    def test_tee_and_pin_are_on_the_map_the_right_way_round(self):
        for way in ("across", "up"):
            m = render.HoleMap(self.c, self.hole, 80, 20, way=way)
            tee, pin = m.cell(self.hole.tee), m.cell(self.hole.pin)
            self.assertIsNotNone(tee)
            self.assertIsNotNone(pin)
            if way == "across":
                self.assertLess(tee[0], pin[0])
            else:
                self.assertGreater(tee[1], pin[1])
            x, y = m.to_pixel(self.hole.pin)
            back = m.to_course(x, y)
            self.assertAlmostEqual(back[0], self.hole.pin[0], places=6)

    def test_terrain_agrees_with_the_lookups(self):
        """The scanline fill paints what surface_at says is there."""
        m = render.HoleMap(self.c, self.hole, 120, 30)
        kinds = m._kinds()
        agree, total, seen = 0, 0, collections.Counter()
        for y in range(0, m.h, 2):
            for x in range(0, m.w, 2):
                look = self.c.surface_at(m.to_course(x + 0.5, y + 0.5))
                seen[look] += 1
                agree += render.surface(kinds[y][x]) == look
                total += 1
        self.assertGreater(agree / total, 0.97)   # the odd pixel on an edge may differ
        self.assertGreater(len(seen), 5)

    def test_marks_labels_box_and_tints(self):
        m = render.HoleMap(self.c, self.hole, 80, 16, quad=False)
        col = Colors("256")
        text = lambda line: _SGR.sub("", line)
        plain = m.lines(col)
        marked = m.lines(col, marks=[(self.hole.pin, "▶", render.FLAG, True)],
                         labels=[(self.hole.pin, "12 ft", (255, 255, 255))],
                         tints=[(at(self.c, 150, 40), (255, 255, 255), 0.5)])
        pin_row, tint_row = m.cell(self.hole.pin)[1], m.cell(at(self.c, 150, 40))[1]
        self.assertNotEqual(pin_row, tint_row)
        self.assertIn("▶", text(marked[pin_row]))
        self.assertIn("12 ft", text(marked[pin_row]))
        self.assertNotEqual(marked[tint_row], plain[tint_row])   # the tinted pixel's color
        self.assertEqual(text(marked[tint_row]), text(plain[tint_row]))
        untouched = [r for r in range(16) if r not in (pin_row, tint_row)]
        self.assertTrue(all(marked[r] == plain[r] for r in untouched))
        boxed = m.lines(col, box=(["Hole 1 · Par 4"], (255, 255, 255), (0, 0, 0)))
        self.assertTrue(any("Hole 1 · Par 4" in text(l) for l in boxed))
        self.assertTrue(all(visible_len(l) == 80 for l in boxed))

    def test_a_ball_in_the_air_is_drawn_above_its_spot(self):
        m = render.HoleMap(self.c, self.hole, 80, 20)
        spot = at(self.c, 200, 0)
        col, row = m.cell(spot)
        lines = m.lines(Colors("256"), marks=[(spot, "●", (255, 255, 255), True, 2)])
        self.assertIn("●", _SGR.sub("", lines[row - 2]))
        self.assertNotIn("●", _SGR.sub("", lines[row]))

    def test_quadrants(self):
        a, b = (10, 10, 10), (200, 200, 200)
        self.assertEqual(render._fit4([a, a, a, a]), (" ", None, a))
        self.assertEqual(render._fit4([a, b, b, a]), ("▚", a, b))
        self.assertEqual(render._fit4([a, a, b, b]), ("▀", a, b))
        ch, f, bg = render._fit4([a, a, b, (190, 190, 190)])   # three colors: the closest two groups
        self.assertEqual(ch, "▀")
        self.assertEqual(f, a)
        high = render.HoleMap(self.c, self.hole, 60, 15)
        standard = render.HoleMap(self.c, self.hole, 60, 15, quad=False)
        self.assertEqual((high.w, standard.w), (120, 60))
        self.assertAlmostEqual(high.kx * 2, standard.kx)   # twice the pixels across, half the yards each

    def test_shading(self):
        m = render.HoleMap(self.c, self.hole, 120, 30, level="green", way="across")
        pixels, _ = m.terrain(render.PALETTE)
        kinds = m._kinds()
        fringe = render.PALETTE["fringe"]
        # Just off the green there's fringe; well off it, none.
        edge = [pixels[y][x] for y in range(m.h) for x in range(m.w) if kinds[y][x] != "green"
                and any(kinds[y][min(m.w - 1, x + d)] == "green" for d in (1, 2))]
        self.assertTrue(any(p == fringe for p in edge))
        self.assertGreater(len({p for row in pixels for p in row}), 20)   # textured, not flat

    def test_surroundings(self):
        c = osm.parse(tiny_course(), "relation", 1, tiny_around())[0]
        m = render.HoleMap(c, c.holes[0], 60, 40, way="across")
        kinds = m._kinds()
        def kind_at(x, y):
            px, py = m.to_pixel(at(c, x, y))
            self.assertTrue(0 <= px < m.w and 0 <= py < m.h, (x, y))
            return kinds[int(py)][int(px)]
        self.assertEqual(kind_at(200, 260), "sea")             # north of the coast
        self.assertNotEqual(kind_at(200, 175), "sea")          # south of it, between it and the course
        self.assertIn(kind_at(30, 108), render.BUILDINGS)
        self.assertEqual(kind_at(100, 62), "flowers")
        self.assertIn(kind_at(150, -60).rstrip("*"), ("pines",))
        flat = [k for row in kinds for k in row]
        self.assertGreater(flat.count("stake"), 10)            # along the boundary
        pixels, _ = m.terrain(render.PALETTE)
        blooms = {p for row in pixels for p in row} & set(render.BLOOMS)
        self.assertTrue(blooms)

    def test_tee_markers(self):
        m = render.HoleMap(self.c, self.hole, 120, 30, way="across", level="fairway")
        m.aim(self.hole.tee)
        kinds = [k for row in m._kinds() for k in row]
        self.assertIn("mark:blue", kinds)
        self.assertIn("mark:pink", kinds)
        pixels, _ = m.terrain(render.PALETTE)
        self.assertIn(render.TEE_RGB["pink"], {p for row in pixels for p in row})

    def test_trees_are_darker_than_the_rough(self):
        c = osm.parse(tiny_course(), "relation", 1, tiny_around())[0]
        m = render.HoleMap(c, c.holes[0], 120, 30, way="across", level="fairway")
        pixels, _ = m.terrain(render.PALETTE)
        kinds = m._kinds()
        tone = lambda want: [sum(pixels[y][x]) for y in range(m.h) for x in range(m.w)
                             if render.surface(kinds[y][x]) == want and kinds[y][x] != "cut"]
        trees, rough = tone("trees"), tone("rough")
        self.assertTrue(trees and rough)
        self.assertLess(max(trees), sorted(rough)[len(rough) // 10])   # the brightest tree under nearly all the rough

    def test_cameras(self):
        hole_view = render.HoleMap(self.c, self.hole, 60, 15)
        fairway = render.HoleMap(self.c, self.hole, 60, 15, way=hole_view.way, level="fairway")
        self.assertEqual((hole_view.wc, hole_view.wr), (60, 15))
        self.assertGreater(fairway.wc, 60)                    # drawn bigger than the screen
        self.assertAlmostEqual(fairway.k * render.FAIRWAY_ZOOM, hole_view.k)
        self.assertTrue(fairway.covers(self.hole.tee) and fairway.covers(self.hole.pin))
        fairway.aim(self.hole.tee)
        self.assertTrue(fairway.contains(self.hole.tee))
        self.assertFalse(fairway.contains(self.hole.pin))     # 400 yards on: off screen
        left = fairway.off
        fairway.aim(self.hole.pin)
        self.assertGreater(fairway.off[0], left[0])           # the camera moved down the hole
        self.assertTrue(fairway.contains(self.hole.pin))
        fairway.aim(at(self.c, 5000, 0))                      # it stops at the drawing's edge
        self.assertEqual(fairway.off[0], fairway.wc - fairway.cols)
        lines = fairway.lines(Colors("truecolor"))
        self.assertEqual(len(lines), 15)
        self.assertTrue(all(visible_len(l) == 60 for l in lines))

    def test_what_fits_on_screen(self):
        m = render.HoleMap(self.c, self.hole, 80, 20, level="green", way="across")
        pin = self.hole.pin
        self.assertTrue(m.fits([pin, at(self.c, 390, 0)]))
        self.assertFalse(m.fits([pin, at(self.c, 340, 0)]))   # in the drawing, but too far apart for the screen
        self.assertFalse(m.fits([pin, self.hole.tee]))        # not even in the drawing
        both = [pin, at(self.c, 385, 4)]
        m.aim(m.middle(both))
        self.assertTrue(all(m.contains(p) for p in both))

    def test_an_arrow_points_at_what_is_off_screen(self):
        m = render.HoleMap(self.c, self.hole, 60, 15, way="across", level="fairway")
        m.aim(self.hole.tee)
        lines = [_SGR.sub("", l) for l in m.lines(Colors("truecolor"), pointers=[(self.hole.pin, (255, 255, 255))])]
        self.assertEqual(lines[7][-1], "→")                   # the pin's off to the right, down the hole
        lines = [_SGR.sub("", l) for l in m.lines(Colors("truecolor"), pointers=[(self.hole.tee, (255, 255, 255))])]
        self.assertFalse(any(ch in render.POINTERS for l in lines for ch in l))   # the tee's on screen: no arrow

    def test_a_map_keeps_its_terrain(self):
        m = render.HoleMap(self.c, self.hole, 60, 15)
        m.prepare(Colors("256"))
        render._cache.clear()                                  # (other maps pushed it out of the shared cache)
        made = m.terrain(render.PALETTE)
        self.assertIs(m.terrain(render.PALETTE), made)
        self.assertIsInstance(made[0][0][0], tuple)            # (kept as bytes, read back as colors)
        self.assertEqual(len(made[0]), m.h)
        self.assertEqual(len(made[0][0]), m.w)

    def test_arrows_reach_both_edges(self):
        m = render.HoleMap(self.c, self.hole, 60, 15, way="across", level="fairway")
        m.aim(self.hole.pin)
        self.assertEqual(m._edge(self.hole.tee)[::2], (0, "←"))
        m.aim(self.hole.tee)
        self.assertEqual(m._edge(self.hole.pin)[::2], (59, "→"))

    def test_looking_around(self):
        whole = render.HoleMap(self.c, self.hole, 60, 15)
        spot = at(self.c, 200, 30)
        m = render.HoleMap(self.c, self.hole, 60, 15, way=whole.way, level="look", center=spot, scale=whole.k / 4)
        self.assertEqual((m.wc, m.k), (96, whole.k / 4))
        m.aim(spot)
        self.assertLess(dist(m.center(), spot), m.k * 3)
        self.assertFalse(m.close)
        near = render.HoleMap(self.c, self.hole, 60, 15, way=whole.way, level="look", center=self.hole.pin,
                              scale=render.span_scale(render.CUP_SPAN, 60, 15, 1.0))
        self.assertTrue(near.close and near.fine)              # as close as the cup's: its contours
        self.assertEqual(len(near.lines(Colors("256"))), 15)

    def test_close_up_is_roomy(self):
        m = render.HoleMap(self.c, self.hole, 80, 20, level="green", way="across")
        self.assertTrue(m.covers(at(self.c, 395, 6)))
        self.assertTrue(m.covers(at(self.c, 380, 0)))         # 30 yards short: still in the drawing
        self.assertAlmostEqual(min(m.cols * m.px * m.kx, m.rows * 2 * m.k), render.CLOSE_SPAN, places=6)
        self.assertFalse(m.covers(self.hole.tee))

    def test_relief(self):
        class Bumps:   # rolling ground, a yard up and down every 30 yards east
            def z(self, p):
                return math.sin(p[0] / 5)

            def zs(self, points):
                return [self.z(p) for p in points]

        self.c.ground = Bumps()
        render._cache.clear()

        def terrain(level, relief):
            m = render.HoleMap(self.c, self.hole, 80, 20, way="across", level=level, relief=relief)
            return m.terrain(render.PALETTE)[0], m._kinds()
        off, _ = terrain("hole", "off")
        self.assertNotEqual(terrain("hole", "on")[0], off)              # the hills shaded
        self.assertEqual(terrain("hole", "greens only")[0], off)        # contours only close up
        close, kinds = terrain("green", "off")
        contoured, _ = terrain("green", "greens only")
        changed = {kinds[y][x] for y in range(len(close)) for x in range(len(close[0])) if close[y][x] != contoured[y][x]}
        self.assertEqual(changed, {"green"})                            # on the putting surface, nowhere else
        self.c.ground = None
        render._cache.clear()
        self.assertEqual(terrain("hole", "on")[0], off)                 # flat: nothing to shade

    def test_trails_kept_drawn_are_as_if_drawn_afresh(self):
        m = render.HoleMap(self.c, self.hole, 60, 16, level="fairway", way="across")
        colors = Colors("truecolor")
        line = lambda a, b, rgb: tuple(((a + (b - a) * t / 200, t * 0.1), rgb, 0.4) for t in range(201))
        first, second = line(0, 300, (250, 250, 250)), line(100, 400, (250, 60, 60))
        flying = [((150 + t, 3.0), (90, 160, 250), 0.65) for t in range(40)]
        marks = [(self.hole.pin, "▶", render.FLAG, True)]
        for trails in ([first], [first, second], [second]):
            for cam in (self.hole.tee, self.hole.pin, self.hole.tee):
                m.aim(cam)
                kept = m.lines(colors, marks, (), None, flying, (), trails)
                afresh = render.HoleMap(self.c, self.hole, 60, 16, level="fairway", way="across")
                afresh.aim(cam)
                self.assertEqual(kept, afresh.lines(colors, marks, (), None, [t for g in trails for t in g] + flying))

    def test_sixteen_colors(self):
        m = render.HoleMap(self.c, self.hole, 40, 10)
        lines = m.lines(Colors("16"))
        self.assertTrue(all("\x1b[38;2" not in l for l in lines))


if __name__ == "__main__":
    unittest.main()
