"""The golfers: made-up names, each with a game of their own.

A golfer's numbers say how they differ from a solid tour player (1.0 is
that player): how far they hit it, how straight, how often a swing goes
badly wrong and which way their ball curves, how close they chip and how
well they putt. LEVELS gives the range each number is drawn from.
"""

import dataclasses

FIRST = ("Alden", "Bram", "Cal", "Dario", "Emmett", "Finn", "Gus", "Hale", "Idris", "Jonah", "Kip", "Lars",
         "Mateo", "Niall", "Otis", "Pierce", "Quinn", "Rafe", "Soren", "Teddy", "Ulric", "Vaughn", "Wes", "Xavi",
         "Yusuf", "Zeke", "Ines", "Mara", "Tove", "Wren")
LAST = ("Ashdown", "Barrowfield", "Castellane", "Draycott", "Eskdale", "Fairbanks", "Glenholm", "Hartwell",
        "Inverness", "Kettering", "Lindqvist", "Marchetti", "Northcote", "Okonkwo", "Pemberton", "Quillfeather",
        "Ravensworth", "Sandoval", "Thistlewood", "Underhill", "Varga", "Whitlock", "Yarborough", "Zeller",
        "Brightwater", "Coldstream", "Hollins", "Mossgiel")
BALLS = ((255, 255, 255), (255, 214, 64), (255, 128, 160), (110, 210, 255))   # one per golfer in a group

# level -> (low, high) for each number
LEVELS = {
    "tour": {"power": (0.97, 1.05), "accuracy": (1.0, 1.25), "miss": (0.03, 0.06), "short": (1.0, 1.25),
             "putting": (1.0, 1.12)},
    "scratch": {"power": (0.88, 0.95), "accuracy": (1.15, 1.4), "miss": (0.04, 0.07), "short": (1.3, 1.6),
                "putting": (1.15, 1.35)},
    "weekend": {"power": (0.72, 0.85), "accuracy": (1.7, 2.3), "miss": (0.12, 0.2), "short": (2.0, 2.6),
                "putting": (1.55, 1.9)},
}


@dataclasses.dataclass(frozen=True)
class Golfer:
    name: str
    power: float = 1.0      # times the bag's carries
    accuracy: float = 1.0   # times a club's spread (bigger is wilder)
    miss: float = 0.03      # chance a full swing goes badly wrong
    shape: float = 0.0      # degrees the ball curves on a good swing: + fades right, - draws left
    short: float = 1.0      # times a chip's or pitch's distance from the hole
    putting: float = 1.0    # a putt is as hard as one this many times as long for a tour player
    ball: tuple = BALLS[0]

    @property
    def short_name(self):
        first, _, last = self.name.partition(" ")
        return "%s. %s" % (first[:1], last) if last else first

    @property
    def tendency(self):
        """Which way their misses go: a fader slices, a drawer hooks."""
        return "slice" if self.shape > 0.3 else "hook" if self.shape < -0.3 else None


def you(level, name="You"):
    """The player's golfer: the middle of a level, curving it neither way."""
    r = LEVELS.get(level, LEVELS["scratch"])
    mid = lambda k: round(sum(r[k]) / 2, 3)
    return Golfer(name=name, power=mid("power"), accuracy=mid("accuracy"), miss=mid("miss"), shape=0.0,
                  short=mid("short"), putting=mid("putting"), ball=BALLS[0])


def group(n, level, rng):
    """n golfers of a level ("tour", "scratch", "weekend", or "mixed" for
    one of each in turn), with different names."""
    names = set()
    out = []
    for i in range(n):
        lv = ("tour", "scratch", "weekend")[i % 3] if level == "mixed" else level
        r = LEVELS.get(lv, LEVELS["tour"])
        while True:
            name = "%s %s" % (rng.choice(FIRST), rng.choice(LAST))
            if name not in names:
                names.add(name)
                break
        pick = lambda k: round(rng.uniform(*r[k]), 3)
        out.append(Golfer(name=name, power=pick("power"), accuracy=pick("accuracy"), miss=pick("miss"),
                          shape=round(rng.choice((-1, 1)) * rng.uniform(0, 1.6), 2), short=pick("short"),
                          putting=pick("putting"), ball=BALLS[i % len(BALLS)]))
    return out
