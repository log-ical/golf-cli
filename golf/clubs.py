"""The bag: how far each club goes, how much it runs out and how straight.

Carries are a tour player's stock full swings in still air at 70°F; each
golfer scales them by their own power. The lie a ball sits on changes what
can be hit from it and how well (LIES).
"""

import dataclasses


@dataclasses.dataclass(frozen=True)
class Club:
    key: str
    name: str
    carry: float      # yards in the air
    roll: float       # yards it runs out on a firm fairway
    spread: float     # degrees: one standard deviation of where it starts and how it curves
    loft: float = 0   # degrees of loft on its face (wedges go by theirs: their names say it)


BAG = (
    Club("D", "Driver", 290, 22, 2.6, 10.5),
    Club("3W", "3-wood", 262, 16, 2.4, 15),
    Club("5W", "5-wood", 245, 10, 2.3, 18),
    Club("3H", "3-hybrid", 230, 8, 2.2, 21),
    Club("4i", "4-iron", 215, 7, 2.1, 22),
    Club("5i", "5-iron", 203, 6, 2.0, 25),
    Club("6i", "6-iron", 191, 5, 1.95, 28),
    Club("7i", "7-iron", 178, 4, 1.9, 32),
    Club("8i", "8-iron", 165, 3, 1.85, 36),
    Club("9i", "9-iron", 152, 3, 1.8, 41),
    Club("PW", "Pitching wedge 46°", 140, 2, 1.75, 46),
    Club("GW", "Gap wedge 50°", 126, 2, 1.75, 50),
    Club("SW", "Sand wedge 56°", 108, 1, 1.8, 56),
    Club("LW", "Lob wedge 60°", 88, 1, 1.9, 60),
)


@dataclasses.dataclass(frozen=True)
class Lie:
    distance: float   # times a club's carry
    spread: float     # times its spread
    roll: float       # times its roll
    longest: str      # the longest club that can be hit from it


LIES = {
    "tee": Lie(1.0, 1.0, 1.0, "D"),
    "fairway": Lie(1.0, 1.0, 1.0, "3W"),
    "rough": Lie(0.9, 1.35, 0.5, "5W"),
    "bunker": Lie(0.85, 1.5, 0.4, "5i"),
    "trees": Lie(0.55, 2.2, 0.6, "5i"),   # a punch out, or threading a gap
}


def playable(lie):
    """The clubs that can be hit from a lie, longest first."""
    lie = LIES.get(lie, LIES["rough"])
    i = next(i for i, c in enumerate(BAG) if c.key == lie.longest)
    return BAG[i:]


def carry(club, lie, power=1.0):
    """How far a full swing with club flies from lie for a golfer of power."""
    return club.carry * power * LIES.get(lie, LIES["rough"]).distance


def club_for(needed, lie, power=1.0):
    """The club to fly a ball `needed` yards from lie: the shortest one that
    gets there with a full swing (a wedge swung softer below its distance),
    else the longest there is."""
    clubs = playable(lie)
    for club in reversed(clubs):
        if carry(club, lie, power) >= needed:
            return club
    return clubs[0]
