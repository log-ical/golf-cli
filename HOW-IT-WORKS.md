# How it works

The [README](README.md) covers playing. This is what goes on underneath: the shot engine every swing
goes through, how the zen golfers (and your caddie) decide, the camera, and where the courses come from.

- [The shot engine](#the-shot-engine)
- [How the zen golfers play](#how-the-zen-golfers-play)
- [The camera](#the-camera)
- [The courses](#the-courses)
- [The code](#the-code)

## The shot engine

Every swing, yours and the zen golfers', goes through the same engine (`golf/engine.py`). A golfer asks
for a club, a line, a kind of shot and how hard (and, on the swing meter, how well it was timed). The engine
works out the rest from what's in play.

**The lie.** It decides what can be hit from it, and how well:

| Lie | Longest club | Distance | Spread | Roll |
|-----|--------------|----------|--------|------|
| tee | driver | full | normal | normal |
| fairway | 3-wood | full | normal | normal |
| rough | 5-wood | 90% | 1.35× | half |
| bunker | 5-iron | 85% | 1.5× | 40% |
| trees | 5-iron | 55% | 2.2× | 60% |

Flowerbeds, gorse and hedges play like the trees.

**The club.** Each has a carry, a roll and a spread. Carries are a tour player's stock swings in still air
at 70°F: driver 290 yards, 7-iron 178, the wedges by their loft (pitching 46°, gap 50°, sand 56°, lob 60°).
A club also sets how much the ball curves: a driver's sidespin bends it most, and a wedge's backspin
keeps it straighter (a lob wedge curves a little over half as much as a driver).

**The kind of shot.** Each has its own distance, height, curve and roll, and its own share of the wind:

| Shot | Distance | Height | Curve | Roll | Wind |
|------|----------|--------|-------|------|------|
| Full swing | 100% | normal | — | normal | all of it |
| Punch | 72% | 45% | — | 2.6× | half |
| Draw / Fade | 102% / 97% | normal | 7° left / right | more / less | all of it |
| Stinger | 90% | 40% | — | 2.2× | a third |
| Chip | 30% | low | — | runs most of the way | a tenth |
| Pitch | 55% | half | — | stops quicker | 40% |
| Flop | 45% | high | — | stops dead | 60% |
| Splash | 27% | 60% | — | a little | a third |

A softer swing goes straighter. An overswing (past 100%) goes a little farther and less straight, and goes
wrong more often.

**The golfer.** Their power and accuracy, their usual curve (a fader's ball goes right, and their misses are
slices), how often a swing goes badly wrong, and their touch around and on the green. On the swing meter,
your timing decides which way it goes. An early strike starts left and hooks (up to 6° off line, and about
10° of curve with a driver, less with the shorter clubs). A late strike starts right and slices. Most of the
golfer's usual scatter is still there, but a pure strike is a little straighter than their usual, and goes
badly wrong less often.

**The misses.** Now and then a swing goes badly wrong, more often from a bad lie or a mistimed meter:

- a slice or a hook
- a push or a pull
- a chunk, a thin or a shank
- around the green, a skulled chip, or a splash that stays in the bunker

**The weather.**
- A headwind costs about 1% of carry per mph; a tailwind gives back half that.
- A 10 mph crosswind moves a 150-yard shot about 8 yards (less for a low shot).
- Every 10°F above 70 adds about 1% of carry.
- Wet ground halves the roll.
- Gusts vary from shot to shot.

**The ground.** A landing ball runs out on the fairway, stops quickly in the rough, plugs in a bunker,
and kicks into hazards. Where the course's heights are known:

- A shot to lower ground carries about a yard farther for each yard it drops; uphill comes up that much
  short.
- A rolling ball slows going uphill and speeds up going down.
- A slope across its line bends it, more the slower it goes, so putts break most as they die at the hole.
- A ball won't stop on a slope steeper than the grass can hold: it trickles off down the bank.
- Greens are held to how greens are built: 4% at most overall, 6% in any one spot, since the heights can be
  a few yards off.

The engine keeps every point a rolling ball passes through, so on screen a putt curls along the line it
really took, and a drive hops once as it lands and runs out along its own path.

**The cup.** A ball drops only if it rolls over the cup slowly enough. A putt that would have finished up
to about five feet past drops. A chip running out sometimes drops, and a long iron's bounce rarely does. A
putt that crosses the cup too fast lips out.

**Penalties.** Water costs a stroke and a drop on dry ground, back the way the ball came in. Out of bounds
(past the course's boundary, a house, the clubhouse) is stroke and distance. A ball on another hole's green
gets free relief: the nearest spot off it, no nearer the hole. A golfer who hasn't holed out by 12 strokes
picks up.

## How the zen golfers play

A golfer picks a club and a line the way a good player thinks (`golf/sim.py`). For each club they could hit
and each place they could aim, they work out where the shot usually ends up and what that leaves. Then they
take the choice with the fewest strokes left on average. "Strokes left" comes from the tour's
strokes-to-hole-out from each distance and lie (Mark Broadie's strokes-gained baselines). That's why they
lay up short of water, play away from bunkers, follow a dogleg, and go for par 5s in two when the odds say
so.

They allow for the wind they feel (clubbing up or down, aiming off). Around the green they chip and pitch to
finish just past the hole, and they putt to finish a foot or so past it. They read a putt or chip by trying
it: they adjust the line and the pace until it rolls through the hole. Their stroke then scatters it a
little in line and pace, more the longer it is, so they drop about as often as tour players do: nearly all
from 3 feet, 4 in 10 from 10, 1 in 7 from 20.

Each made-up golfer has a game of their own, drawn from their level's range:

| Level | Power | Spread | Goes wrong | Roughly shoots |
|-------|-------|--------|------------|----------------|
| tour | 97–105% | 1.0–1.25× | 3–6% of swings | around par |
| scratch | 88–95% | 1.15–1.4× | 4–7% | a few over |
| weekend | 72–85% | 1.7–2.3× | 12–20% | around 90 (80 to 110), with plenty of slices into the trees |

Your caddie is the same thinking, run on your ball. When you play, your level gives you the middle of its
range, with no curve of your own.

A round comes from one seed, shown on the final card. Running `golf <course> --seed N` with the same options
plays that round again shot for shot. In single player it gives the same luck for the same swings.

## The camera

The broadcast camera (`direct()` in `golf/camera.py`) works like a TV director:

- **At address** it cuts to the golfer's ball, looking down the hole.
- **In the air** it chases the ball, with room ahead of it.
- **Around the green** chips and putts get the green camera, and short putts the cup camera.
- **The summary** goes back to the whole hole.

A close-up is only used if it can show the ball, the hole and where the shot finishes all at once.
Otherwise the camera stays wider. If the ball is somewhere the hole's maps don't reach (a wild slice far out
of bounds), an arrow at the screen's edge points to it, and once the ball stops it gets a map of its own.

Every map is drawn once and kept (`golf/render.py`). The closer ones are drawn bigger than the screen, so
the camera can move about them without redrawing. They're made ahead of time, in two processes of their own
that each have a copy of the course (so they draw at once, on other cores, without slowing the screen): the
next hole's while the group walks off the green, and a look-around map before the others. Where processes
can't be had, a thread draws them instead.

**Looking around** (`l`) draws maps at any of 13 zoom levels, from twice the whole hole down to the cup's
close-up. Each map is 1.6 screens wide and centered on a grid of points half a screen apart, so the camera can
always move a little before the next one is needed. The next map in the direction you're moving or zooming is
made ahead, and maps for places the camera has already left are dropped from the queue. Until the map for
where the camera is has been drawn, the screen is filled in from the maps there are, the nearest in scale
first. That's a little blocky, but the camera never waits.

The maps are drawn from above, pixel by pixel. Each shape is filled a pixel row at a time, lowest-priority
ground first, so what's on top is what the ball plays off. Then the shading goes on:

- mowing stripes
- the first cut of rough and the green's fringe
- bunker lips and the shallows at the water's edge
- tree crowns and their shadows (lit from the upper left)
- roofs
- the slopes (in a few hard steps), and contour lines on the greens close up

In high detail each character holds four pixels: the quadrant block and two colors that draw them best.

## The courses

**The holes** come from OpenStreetMap. A course needs its holes mapped (`golf=hole`, a line from tee to
green) to be playable. Many are, and the course list marks the ones already downloaded with ✓. A course
downloads once, from the Overpass API, along with what's mapped around it, its trees and its heights (below),
all at once: a minute or two in all. It's kept in `~/.cache/golf-cli/courses/`. If the surroundings can't be
had, it plays without them and they're tried again next time.

When one boundary holds more than one course (Augusta's par-3 course, Sawgrass's Stadium and Dye's Valley),
you choose which to play. Holes the mappers didn't assign to a course are grouped by walking from each green
to the nearest next tee.

**The trees.** Mappers often leave out the trees between the holes, so they come from space: ESA
WorldCover's satellite land cover (every 10 m of the world classed as trees, grass, water...). It's read a
block at a time from cloud-optimized files, for the few kilometres round the course, and kept with it. Where
the course's own ground isn't mapped (its rough, and everything round it), those woods are drawn and play
as trees. *Trees from satellite* in the options turns them off.

**The heights.**
- **In the US** they come from lidar: the USGS's 3D Elevation Program has the bare ground (trees and houses
  taken out) to a metre or better, asked for in 2 m cells. That's enough to read a green by.
- **Elsewhere**, or when the USGS's server is down, they come from Mapzen's terrain tiles (SRTM and the best
  national data, tens of metres). Those show the hills but not the greens' contours.

Either way they're kept with the course (2 to 9 MB for a US course, stored so they read back in a tenth of a
second). If the USGS's server is down, or there's no network, it isn't asked again and again: the terrain
tiles stand in for a day, and then it's asked once more. A pin the map puts on a slope steeper than 3% is
moved to the flattest part of its green, the way greenkeepers cut the cup.

**What's filled in.** Anything OpenStreetMap doesn't have is made up:
- rough wherever nothing else is mapped
- a par from the hole's length
- the pin in the middle of the green
- a round green where a hole ends without one
- a size for each tree that doesn't give its crown's, with some variety

Anything outside the course's boundary is out of bounds.

**Tees.** A hole's tee boxes are back to front in the usual colors (black, blue, white, gold, red, and pink
for a sixth), unless the map tags them (`tee=blue`). Each box has its two markers in its color on the map.
A hole with fewer boxes is played from the nearest set to the one chosen.

## The code

    bin/golf                launcher (works through a symlink)
    golf/
      app.py                argument parsing, screen loop
      screens.py            the course list, loading a course, the round's setup
      zen.py                zen's round view: the playback, the group, the scorecard
      play.py               single player's round view: aiming, the swing meter, the flight
      camera.py             how a shot moves on screen; the maps each camera shows, made ahead in their own
                            processes; the broadcast director; looking around
      ui.py                 what the screens share: the keys along the bottom, the keys page, jobs off the
                            screen's thread
      game.py               a round you play: the caddie, your setup, your scorecard and stats
      engine.py             the shot engine: swings, shot types, flight, wind, slopes, roll, the cup, penalties
      sim.py                the zen golfers' strategy (and your caddie's), and their rounds
      clubs.py              the bag: carries, roll, spread; lies; club choice
      players.py            the made-up golfers and their levels
      weather.py            live, random or calm weather, and what it does to a shot
      course.py             Course and Hole, and what the ground is anywhere on a course
      osm.py                courses from OpenStreetMap (Overpass): download, cache, parse; tees
      landcover.py          the trees from satellite land cover (ESA WorldCover's GeoTIFFs, by range request)
      elevation.py          the heights: USGS lidar or terrain tiles, the ground in course yards, steep greens
                            eased
      catalog.py            famous courses, played ones, search (Nominatim, Overpass)
      geo.py                flat geometry in yards: projection, polygons, paths, frames
      render.py             the hole map: terrain, shading, quadrant or half blocks, the camera's view, balls,
                            trails, the flag and arrows to what's off screen
      panels.py             shot descriptions, the group, the scorecard, the wind
      scoring.py            score names, to-par, standings, scorecard colors
      term.py net.py        terminal I/O and colors; HTTP
      settings.py files.py  settings and the options menu; where things are kept on disk
    tests/                  unittest suite on a small made-up course; never touches the network
    tools/
      screenshots.py        the README's screenshots, made again from the app's own screens
      ansi2png.py           a frame of the app as a PNG, as a terminal shows it
