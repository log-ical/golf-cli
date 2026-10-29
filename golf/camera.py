"""The camera: how a shot moves on screen (its flight, the hop as it lands,
its roll, and how long each takes), the maps each camera shows (made ahead,
in other processes), the broadcast director that cuts between them, and
looking around (l)."""

import collections
import concurrent.futures
import functools
import itertools
import math
import multiprocessing
import os
import pickle
import queue
import signal
import threading

from . import panels, render
from . import term as tty
from .geo import along, alongs, bezier, dist, heading, lerp, path_length, step
from .settings import settings

CLOSE_KINDS = ("putt", "chip", "pitch", "bunker", "drop", "pickup")
CLOSE_RANGE = 50     # yards from the hole: shots and balls in this close the view in on the green
CUP_RANGE = 4.0      # and putts from in this close (12 ft), closer still on the cup
TRAIL, TRAIL_NOW = 0.4, 0.65  # how strongly a trail tints the ground: past shots, the one in the air
HEIGHT = 0.3          # a ball in the air is drawn this much of its height up the screen from its shadow
LIFT_MAX = 3          # rows at most
LEAD = 0.22           # how far ahead of the ball in the air the fairway camera looks, of the screen's length
FOLLOW, SETTLE = 0.12, 0.45   # seconds the camera takes to catch up: with a ball in the air, otherwise
HOP, HOP_TIME = 0.12, 0.6   # a landing ball's first bounce: its height, of the flight's, and seconds at most


def flight_time(shot):
    if shot.kind in ("putt", "drop", "pickup"):
        return 0.0
    if shot.kind in panels.FULL_SWINGS:
        return 3.0 + shot.flown / 150
    return 0.9 + shot.flown / 25


def rolled(shot):
    """Yards the ball rolled, along the way it went."""
    return path_length(shot.path) if shot.path else dist(shot.land, shot.end)


def roll_at(shot, t):
    """Where the rolling ball is a fraction t (0-1) of the way along its roll."""
    if shot.path:
        return along(shot.path, t * path_length(shot.path))
    return lerp(shot.land, shot.end, t)


def roll_time(shot):
    if shot.kind == "putt":
        return min(7.0, 1.2 + rolled(shot) * 3 * 0.09)
    if shot.kind == "drop":
        return 1.0
    run = rolled(shot)
    return min(4.0, 0.5 + run / 8) if run > 0.5 else 0.0


def pause_time(shot):
    t = 1.4
    if shot.holed or shot.penalty:
        t += 1.0
    return t


def apex(shot):
    """Yards up the ball goes at the top of its flight: a full swing's for
    its carry, times how high its kind of shot flies (a chip low, a flop high)."""
    if shot.kind in ("putt", "drop", "pickup"):
        return 0.0
    return (12 + shot.flown * 0.07) * shot.loft


def _carried(t):
    """How far along its flight the ball is a fraction t of the way through
    it: the air slows it down as it goes."""
    return 1 - (1 - t) ** 1.6


def ball_at(shot, e):
    """Where the ball is (over the ground) e seconds into a shot."""
    f, r = flight_time(shot), roll_time(shot)
    e = max(0.0, e)
    if e < f:
        return bezier(shot.start, shot.bend, shot.land, _carried(e / f))
    if r > 0 and e < f + r:
        t = (e - f) / r
        if shot.kind == "drop":   # carried from where it went in to where it's dropped
            return lerp(shot.start, shot.end, 1 - (1 - t) ** 2)
        return roll_at(shot, 1 - (1 - t) ** 2)   # slowing as it rolls out
    return shot.end


def height_at(shot, e):
    """Yards up the ball is e seconds into a shot: its flight, then a hop as it
    lands and runs out."""
    f = flight_time(shot)
    if 0 < e < f:
        t = e / f
        return apex(shot) * 4 * t * (1 - t)
    hop = min(HOP_TIME, roll_time(shot) * 0.3) if f > 0 else 0.0
    if hop and f <= e < f + hop:
        t = (e - f) / hop
        return apex(shot) * HOP * 4 * t * (1 - t)
    return 0.0


def trail(shot, e, step_yards):
    """Points along a shot's path up to e seconds in, step_yards or so apart."""
    f, r = flight_time(shot), roll_time(shot)
    pts = []
    if f > 0:
        n = max(4, int(shot.flown / max(step_yards, 0.05)) + 1)
        upto = _carried(min(1.0, e / f))
        for k in range(n + 1):
            u = k / n
            if u > upto:
                break
            pts.append(bezier(shot.start, shot.bend, shot.land, u))
    if r > 0 and e > f and shot.kind != "drop":
        run = rolled(shot)
        n = max(2, int(run / max(step_yards, 0.05)) + 1)
        upto = min(1.0, (e - f) / r)
        upto = 1 - (1 - upto) ** 2
        ts = [k / n for k in range(n + 1) if k / n <= upto]
        if shot.path:   # (one walk along the way it rolled, for them all)
            pts += alongs(shot.path, [t * run for t in ts])
        else:
            pts += [lerp(shot.land, shot.end, t) for t in ts]
    return pts


_trails = {}   # (shot's id, its look) -> (the shot, its trail's tints)


def trail_tints(shot, rgb, strength, step_yards):
    """A shot played's whole trail, as tints for render.HoleMap.lines: the
    same tuple from frame to frame (the map keeps what it's drawn of it)."""
    key = (id(shot), rgb, strength, step_yards)
    got = _trails.get(key)
    if got is None or got[0] is not shot:   # (an id can be a new shot's, once the old one's gone)
        if len(_trails) > 200:   # (a long one close up is thousands of points)
            _trails.clear()
        got = _trails[key] = (shot, tuple((p, rgb, strength) for p in trail(shot, 1e9, step_yards)))
    return got[1]


# Processes the maps are drawn in, each with the course (some 80-100 MB): two
# drawn at once (where the camera is and where it's going), without slowing
# the screen. 0: on the worker thread, here.
WORKERS = max(1, min(2, (os.cpu_count() or 2) - 1))


class Views:
    """Each hole's maps at each camera level, made ahead of time: the closer
    ones take a while. A worker thread takes the maps asked for, most wanted
    first, and has them drawn in WORKERS processes (or draws them itself).
    The whole hole is made at once if it's wanted before it's ready, since
    there's nothing else to show."""
    KEEP = 14       # maps kept, the oldest dropped first (a few megabytes each)
    KEEP_LOOK = 10  # and looking around's, kept apart: looking about doesn't push out the hole's own

    def __init__(self, colors, workers=None):
        self.colors = colors
        self.workers = WORKERS if workers is None else workers
        self.pool, self.pool_course = None, None    # the processes, and the course they have
        self.pool_lock = threading.Lock()           # over those: the worker starts them, close() stops them
        self.closed = False
        self.slots = threading.Semaphore(max(1, self.workers))   # a map being drawn in each process, at most
        self.ready = {}                 # key -> HoleMap, oldest first
        self.asked = set()              # keys given to the worker
        self.failed = {}                # key -> how many times making it failed
        self.ways = {}                  # (hole, size) -> how its whole hole is turned
        self.wanted = set()             # the look-around maps the camera wants now: others asked for aren't made
        self.size = None                # the (cols, rows) last asked for: maps for other sizes aren't made
        self.lock = threading.Lock()    # over ready and asked: the worker and get() both make maps
        self.jobs = queue.PriorityQueue()   # (priority, order, job): a look around first, it's being waited on
        self._order = itertools.count()
        threading.Thread(target=self._work, args=(self.jobs,), daemon=True).start()

    def close(self):
        """Stop the worker and the processes (whatever they're drawing) and let the maps go."""
        self.closed = True
        self.jobs.put((-1, next(self._order), None))
        with self.lock:
            self.ready.clear()
        with self.pool_lock:
            self._stop_pool()

    def _stop_pool(self):
        """(with pool_lock held)"""
        pool, self.pool, self.pool_course = self.pool, None, None
        if pool is not None:
            for p in list((getattr(pool, "_processes", None) or {}).values()):   # not waited for
                p.terminate()
            pool.shutdown(wait=False, cancel_futures=True)

    def _processes(self, course):
        """The processes to draw the course's maps in, started if need be; None to draw them here."""
        with self.pool_lock:
            if not self.workers or self.closed:
                return None
            if self.pool is not None and self.pool_course is course:
                return self.pool
            self._stop_pool()
            try:
                methods = multiprocessing.get_all_start_methods()   # (not fork: this process has threads)
                context = multiprocessing.get_context("forkserver" if "forkserver" in methods else "spawn")
                self.pool = concurrent.futures.ProcessPoolExecutor(
                    self.workers, mp_context=context, initializer=_drawer, initargs=(pickle.dumps(course, protocol=5),))
                self.pool_course = course
            except Exception:   # no processes to be had: drawn here from now on
                self.workers = 0
            return self.pool

    def _give_up_processes(self, pool):
        """The processes have stopped (one was killed, say): draw here from now on."""
        with self.pool_lock:
            if self.pool is pool:   # (not if they were stopped for another course's)
                self.workers = 0
                self._stop_pool()

    def _way(self, course, hole, cols, rows, aspect, quad):
        """How the hole's maps are turned: like its whole hole's (which takes no drawing to know)."""
        way = settings["layout"]
        size = (id(hole), cols, rows, round(aspect, 3), quad, way)
        if size not in self.ways:
            self.ways[size] = render.HoleMap(course, hole, cols, rows, aspect, way=way, quad=quad).way
        return self.ways[size]

    def _key(self, course, hole, cols, rows, aspect, level, quad, center=None, scale=None):
        way = settings["layout"] if level == "hole" else self._way(course, hole, cols, rows, aspect, quad)
        spot = (round(center[0], 2), round(center[1], 2), round(scale, 5)) if level == "look" else None
        return (id(hole), cols, rows, round(aspect, 3), level, way, quad, settings["relief"], spot)

    def get(self, course, hole, cols, rows, aspect, level, quad, wait=False, center=None, scale=None, rank=None):
        """The map, or None while it's being made (unless wait). A look-around
        map ("look") is `scale` yards a pixel round the course point `center`;
        with a rank, it's one the camera wants (see look), made before those
        of a higher rank, and not at all once the camera's moved on."""
        self.size = (cols, rows)
        key = self._key(course, hole, cols, rows, aspect, level, quad, center, scale)
        way, relief = key[5], key[7]
        with self.lock:
            m = self.ready.pop(key, None)
            if m is not None:
                self.ready[key] = m   # the newest again: the maps in use are the last to go
        args = (course, hole, cols, rows, aspect, way, level, quad, relief, center, scale)
        if m is None and wait:
            m = self._make(key, args)
        elif m is None:
            with self.lock:
                if key not in self.asked:
                    self.asked.add(key)
                    # A look around's newest first (the camera's moved on from the older ones), the rest in turn.
                    order = next(self._order)
                    if level == "look":
                        self.jobs.put((0, (rank or 0, -order), (key, args, rank is not None)))
                    else:
                        self.jobs.put((1, order, (key, args)))
        return m

    def look(self, course, hole, cols, rows, aspect, quad, spots):
        """Looking around: the map round the first of spots ((course point,
        scale)), or None while it's made; the others (where the camera's
        going) made after it. Any asked for before and not among them now
        aren't made: the camera's moved on."""
        with self.lock:
            self.wanted = {self._key(course, hole, cols, rows, aspect, "look", quad, c, s) for c, s in spots}
        maps = [self.get(course, hole, cols, rows, aspect, "look", quad, center=c, scale=s, rank=i)
                for i, (c, s) in enumerate(spots)]
        return maps[0]

    def preview(self, course, hole, cols, rows, aspect, quad, center, scale):
        """A stand-in for the look-around map round `center` at `scale` while
        it's made: just the screen's worth, drawn from the hole's maps that
        are (render.HoleMap.borrow). None if there are none yet."""
        size = (id(hole), cols, rows, round(aspect, 3))
        with self.lock:
            maps = [m for k, m in self.ready.items() if k[:4] == size and k[6] == quad and m.hole is hole]
        palette = render.PALETTE16 if self.colors.mode == "16" else render.PALETTE
        maps = [m for m in maps if id(palette) in m._terrain]   # (drawn in these colors)
        if not maps:
            return None
        m = render.HoleMap(course, hole, cols, rows, aspect, way=self._way(course, hole, cols, rows, aspect, quad),
                           level="look", quad=quad, relief=settings["relief"], center=center, scale=scale, world=1.0)
        m.borrow(maps, palette)
        return m

    def _make(self, key, args):
        course, hole, cols, rows, aspect, way, level, quad, relief, center, scale = args
        m = render.HoleMap(course, hole, cols, rows, aspect, way=way, level=level, quad=quad, relief=relief,
                           center=center, scale=scale)
        m.prepare(self.colors)
        return self._keep(key, m)

    def _keep(self, key, m):
        with self.lock:
            if self.closed:
                return m
            self.ready[key] = m
            for looks, keep in ((False, self.KEEP), (True, self.KEEP_LOOK)):   # each kind's oldest go first
                kind = [k for k in self.ready if (k[4] == "look") == looks]
                for old in kind[:max(0, len(kind) - keep)]:
                    del self.ready[old]
                    self.asked.discard(old)
        return m

    def _work(self, jobs):
        while True:
            self.slots.acquire()   # a process free (the queue keeps the rest in order till one is)
            job = jobs.get()[2]
            sent = False
            try:
                if job is None:   # closed
                    return
                key, args = job[:2]
                if key in self.ready:
                    pass
                elif self.size is not None and args[2:4] != self.size or job[2:] == (True,) and key not in self.wanted:
                    with self.lock:   # for a size the screen's no longer at (a resize), or a place the camera's
                        self.asked.discard(key)   # left: asked again if need be
                else:
                    pool = self._processes(args[0])
                    if pool is not None:
                        mode = self.colors.mode
                        try:
                            drawing = pool.submit(_draw, *args[1:], mode)
                        except Exception:   # the processes are gone (or wouldn't start)
                            self._give_up_processes(pool)
                        else:
                            drawing.add_done_callback(functools.partial(self._drawn, jobs, key, args, mode, pool))
                            sent = True
                    if not sent:
                        self._make(key, args)
            except Exception:   # a map that can't be made: the whole hole stands in for it (asked again, twice)
                self._failed(key)
            finally:
                if not sent:
                    self.slots.release()
                    jobs.task_done()

    def _drawn(self, jobs, key, args, mode, pool, drawing):
        """A map back from a process: kept, unless it couldn't be drawn (or
        the processes stopped: drawn here, from then on)."""
        try:
            course, hole, cols, rows, aspect, way, level, quad, relief, center, scale = args
            m = render.HoleMap(course, hole, cols, rows, aspect, way=way, level=level, quad=quad, relief=relief,
                               center=center, scale=scale)
            m.take(render.PALETTE16 if mode == "16" else render.PALETTE, drawing.result())
            self._keep(key, m)
        except concurrent.futures.CancelledError:   # closed, or stopped for another course
            with self.lock:
                self.asked.discard(key)
        except concurrent.futures.process.BrokenProcessPool:
            self._give_up_processes(pool)
            with self.lock:
                self.asked.discard(key)   # (asked again: drawn here)
        except Exception:
            self._failed(key)
        finally:
            self.slots.release()
            jobs.task_done()

    def _failed(self, key):
        with self.lock:
            self.failed[key] = self.failed.get(key, 0) + 1
            if self.failed[key] <= 2:
                self.asked.discard(key)

    def settle(self):
        """Wait until every map asked for is made (for tests)."""
        self.jobs.join()


_course = None   # in a drawing process: the course it draws


def _drawer(course):
    """Start a drawing process: the course (pickled) to draw. It's quiet:
    Ctrl-C is for the game to act on, and nothing it says belongs on the screen."""
    global _course
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    quiet = os.open(os.devnull, os.O_WRONLY)
    os.dup2(quiet, 1)
    os.dup2(quiet, 2)
    _course = pickle.loads(course)


def _draw(hole, cols, rows, aspect, way, level, quad, relief, center, scale, mode):
    """In a drawing process: a map's terrain (render.HoleMap.drawn)."""
    m = render.HoleMap(_course, hole, cols, rows, aspect, way=way, level=level, quad=quad, relief=relief,
                       center=center, scale=scale)
    return m.drawn(render.PALETTE16 if mode == "16" else render.PALETTE)


ZOOMS = (0.5, 0.7, 1.0, 1.4, 2.0, 2.8, 4.0, 5.6, 8.0, 11.0, 16.0, 22.0, 32.0)   # looking around: times the
                                                                                # whole hole's scale
PAN = 1 / 6           # of the screen an arrow moves the camera, looking around


class Look:
    """Looking around (l): a free camera, moved with the arrows and zoomed
    with + and -, anywhere round the hole, from twice the whole hole's span
    to the cup's. Its maps are drawn LOOK_WORLD times the screen round points
    half a screen apart, so the camera moves about one while the next is
    made: the next one along the way it's moving, or zooming, made ahead.
    Till the one for where the camera is has been made, the screen's drawn
    from the maps there are, a little rougher (Views.preview)."""

    def __init__(self):
        self.on, self.hole, self.center, self.zoom, self.shown, self.heading = False, None, None, 2, None, (0, 0)
        self.zooming = 0       # +1 zooming in, -1 out, 0 moving or still
        self.drawing = False   # the map for where the camera is isn't made yet
        self._preview = None   # (where, its stand-in)

    def start(self, m, whole, aspect):
        """Look around from where the map m's camera is."""
        self.on, self.hole, self.center, self.shown, self.heading = True, whole.hole, m.center(), m, (0, 0)
        self.zooming = 0
        ratio = whole.k / m.k
        self.zoom = min(range(self.most(whole, aspect) + 1), key=lambda i: abs(math.log(ZOOMS[i] / ratio)))

    def stop(self):
        self.on, self.shown, self.drawing, self._preview = False, None, False, None

    @staticmethod
    def most(whole, aspect):
        """The closest zoom: the cup's close-up."""
        closest = render.span_scale(render.CUP_SPAN, whole.cols, whole.rows, aspect)
        return max(i for i, z in enumerate(ZOOMS) if i == 0 or whole.k / z >= closest * 0.99)

    def pan(self, dx, dy, whole):
        """Move the camera dx and dy screens (right and down), no farther than
        the whole hole's map and as much again round it."""
        z = ZOOMS[self.zoom]
        x, y = whole.to_pixel(self.center)
        x = max(-whole.w / 2, min(whole.w * 1.5, x + dx * whole.cols * whole.px / z))
        y = max(-whole.h / 2, min(whole.h * 1.5, y + dy * whole.rows * 2 / z))
        self.center, self.heading = whole.to_course(x, y), (dx and math.copysign(1, dx), dy and math.copysign(1, dy))
        self.zooming = 0

    def zoom_by(self, step, whole, aspect):
        """In (step 1) or out (-1), no closer than the cup's close-up."""
        self.zoom, self.heading = max(0, min(self.most(whole, aspect), self.zoom + step)), (0, 0)
        self.zooming = step

    def label(self):
        z = ZOOMS[self.zoom]
        return "looking · %s×%s" % (("%.1f" % z).rstrip("0").rstrip("."), " · drawing…" if self.drawing else "")

    def view(self, views, course, hole, cols, rows, aspect, quad, whole):
        """The map to show, aimed at the camera: the one drawn round the grid
        point nearest it once it's made, till then a stand-in drawn from the
        maps there are."""
        if hole is not self.hole:   # on to another hole: back to the director
            self.stop()
            return None
        x, y = whole.to_pixel(self.center)

        def spot(zoom, ahead=(0, 0)):
            """(the grid point nearest the camera at a zoom, or the next one along, and the zoom's scale)"""
            z = ZOOMS[zoom]
            sx, sy = whole.cols * whole.px / 2 / z, whole.rows / z   # half a screen, in the whole hole map's pixels
            gx, gy = round(x / sx) + ahead[0], round(y / sy) + ahead[1]
            return whole.to_course(gx * sx, gy * sy), whole.k / z
        spots = [spot(self.zoom)]
        if self.heading != (0, 0):
            spots.append(spot(self.zoom, self.heading))
        if self.zooming and 0 <= self.zoom + self.zooming <= self.most(whole, aspect):
            spots.append(spot(self.zoom + self.zooming))
        m = views.look(course, hole, cols, rows, aspect, quad, spots)
        self.drawing = m is None
        if m is None:
            where = (self.center, self.zoom, cols, rows, quad, views.colors.mode)
            if self._preview is None or self._preview[0] != where:
                self._preview = (where, views.preview(course, hole, cols, rows, aspect, quad, self.center,
                                                      whole.k / ZOOMS[self.zoom]))
            m = self._preview[1] or whole
        else:
            self._preview = None
        self.shown = m
        m.aim(self.center)
        return m


# What the director needs to know: the hole (and the one after, to make its
# maps ahead), the phase ("intro", "address", "shot", "pause", "summary",
# "done"), the shot about to be, being or just played, and the seconds into it.
Scene = collections.namedtuple("Scene", "hole after phase shot elapsed")


AWAY_GRID = 25.0      # yards: a ball off the hole's maps gets one drawn round the nearest point this far apart


def away_map(views, course, hole, cols, rows, aspect, quad, p, scale):
    """A map at `scale` round course point p, for a ball off the hole's own
    maps; None until it's made. It's drawn round the nearest point of a grid
    (so the maps for nearby spots are the same one), no farther apart than a
    quarter of the screen: the map's always got p on it."""
    grid = min(AWAY_GRID, 0.25 * min(cols * aspect, rows * 2) * scale)
    snap = (round(p[0] / grid) * grid, round(p[1] / grid) * grid)
    return views.get(course, hole, cols, rows, aspect, "look", quad, center=snap, scale=scale)


def direct(scene, views, course, cols, rows, aspect, quad, ui, dt):
    """The map to show and where its camera points, as a TV director would:
    the whole hole to open and close it; for a full shot, the fairway camera
    cut to the golfer at address, chasing the ball through the air with room
    ahead of it, and settling where it stops; the green camera for the short
    game, between the ball and the hole. ui keeps the camera from frame to
    frame."""
    hole, phase, shot, elapsed = scene.hole, scene.phase, scene.shot, scene.elapsed
    levels = ["hole"] + (["fairway"] if settings["camera"] == "broadcast" else []) + \
        (["green"] if settings["green_view"] else [])   # the ones in use
    whole = views.get(course, hole, cols, rows, aspect, "hole", quad, wait=True)
    fairway = views.get(course, hole, cols, rows, aspect, "fairway", quad) if "fairway" in levels else None
    green = views.get(course, hole, cols, rows, aspect, "green", quad) if "green" in levels else None
    if phase in ("pause", "summary") and scene.after is not None:
        for level in levels:   # the next hole's, while nothing moves
            views.get(course, scene.after, cols, rows, aspect, level, quad)
    m = whole
    if shot is not None:
        short = settings["green_view"] and shot.kind in CLOSE_KINDS and dist(shot.start, hole.pin) <= CLOSE_RANGE
        # The cup's close-up for a short putt, made ahead once a ball is on the green.
        on_green = short and (shot.kind == "putt" or shot.lie == "green")
        cup = views.get(course, hole, cols, rows, aspect, "cup", quad) if on_green else None
        keep = (shot.start, shot.end, hole.pin)   # a close-up shows all these at once, or it isn't used
        if cup is not None and shot.kind == "putt" and dist(shot.start, hole.pin) <= CUP_RANGE and cup.fits(keep):
            m = cup
        elif short and green is not None and green.fits(keep):
            m = green
        elif fairway is not None:
            m = fairway
    ball = None if shot is None else ball_at(shot, elapsed) if phase == "shot" else \
        shot.start if phase == "address" else shot.end
    flying = phase == "shot" and elapsed < flight_time(shot)
    if ball is not None and not flying and not m.covers(ball):
        # A ball off the map (a wild one, far out of bounds): a map of its own round it, once it's made.
        m = away_map(views, course, hole, cols, rows, aspect, quad, ball, m.k) or m
    if m.level == "hole":
        ui.update(map=m, shot=None)
        return m
    if m.level in render.CLOSE_LEVELS:
        target = lerp(ball, hole.pin, 0.5)
    else:
        span = m.cols * m.px * m.kx if m.way == "across" else m.rows * 2 * m.k
        if phase == "address":        # looking down the hole from behind the ball
            target = step(ball, heading(ball, hole.pin), LEAD * span)
        elif flying:
            target = step(ball, heading(shot.start, shot.land), LEAD * span)
        else:
            target = ball
    if ui.get("map") is not m or ui.get("shot") is not shot:
        cam = target   # a cut: a new golfer, or another camera
    else:
        cam = lerp(ui["cam"], target, 1 - math.exp(-dt / (FOLLOW if flying else SETTLE)))
    ui.update(map=m, shot=shot, cam=cam)
    m.aim(cam)
    return m


PAN_KEYS = {"left": (-PAN, 0), "right": (PAN, 0), "up": (0, -PAN), "down": (0, PAN)}
LOOK_KEYS = "LOOKING AROUND   ←↑↓→ move   +/- zoom   l or Esc: back to the play"


def look_key(ui, key):
    """Act on a key for looking around (l starts and stops it; looking, the
    arrows move the camera, + and - zoom it, Esc stops): whether it was one."""
    look, whole, m = ui.setdefault("look", Look()), ui.get("whole"), ui.get("map")
    if key == "l":
        if look.on:
            look.stop()
        elif whole is not None and m is not None:
            look.start(m, whole, ui.get("aspect", 1.0))
        return True
    if not look.on or whole is None:
        return False
    if key in PAN_KEYS:
        look.pan(*PAN_KEYS[key], whole)
    elif key in ("+", "=", "-", "_"):
        look.zoom_by(1 if key in ("+", "=") else -1, whole, ui.get("aspect", 1.0))
    elif key == "esc":
        look.stop()
    else:
        return False
    return True


def high_detail():
    """Whether maps use quadrant blocks (four pixels to a cell): unless the
    setting's standard, whenever the symbols have them (the console's and
    ASCII's fonts don't)."""
    return settings["detail"] != "standard" and tty.symbols == "full"
