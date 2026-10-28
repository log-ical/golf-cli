"""Scores: names for them, to-par, and the colors scorecards use."""

NAMES = {-4: "Condor", -3: "Albatross", -2: "Eagle", -1: "Birdie", 0: "Par", 1: "Bogey", 2: "Double bogey",
         3: "Triple bogey"}
# Scorecard colors by strokes against par, as on TV: under par in reds and gold.
COLORS = {-3: (255, 215, 0), -2: (255, 190, 40), -1: (255, 95, 95), 0: None, 1: (120, 170, 255),
          2: (90, 120, 230)}
WORSE = (80, 90, 200)   # triple bogey and beyond


def name(strokes, par):
    if strokes == 1:
        return "Hole in one"
    diff = strokes - par
    return NAMES.get(diff, "+%d" % diff if diff > 0 else "%d" % diff)


def to_par(n):
    """-3, E, +2."""
    return "E" if n == 0 else "%+d" % n


def color(strokes, par):
    """The scorecard color for a score, or None for par."""
    diff = strokes - par
    return COLORS[max(-3, diff)] if diff in COLORS or diff < -3 else WORSE


def standings(totals):
    """Places for scores to par (lowest first, ties shared): [(place, index)]
    as "1", "T2", ... with the golfers' indexes, best first."""
    order = sorted(range(len(totals)), key=lambda i: totals[i])
    out = []
    for i in order:
        place = 1 + sum(1 for t in totals if t < totals[i])
        tied = sum(1 for t in totals if t == totals[i]) > 1
        out.append((("T%d" if tied else "%d") % place, i))
    return out
