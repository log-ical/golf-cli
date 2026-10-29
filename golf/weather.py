"""Wind, temperature and rain: the course's real weather right now (from
Open-Meteo, free and keyless), a random day, or none at all, and what each
does to a shot.

Rules of thumb from the range: a headwind costs about 1% of carry per mph and
a tailwind gives back half that; a 10 mph crosswind moves a 150-yard shot
about 8 yards; every 10°F above 70 adds about 1%. Wet ground halves the roll.
"""

import dataclasses
import math
import time

from .net import fetch_json, query_url

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
CODES = ((0, "clear"), (1, "mostly clear"), (2, "partly cloudy"), (3, "overcast"), (45, "fog"), (51, "drizzle"),
         (61, "rain"), (66, "freezing rain"), (71, "snow"), (80, "showers"), (85, "snow showers"),
         (95, "thunderstorms"))
WET = 51          # weather codes from here up mean wet ground
DRIFT = 0.8       # yards of drift per mph of crosswind per 150 yards of carry
LIVE_KEEP = 600   # seconds the live weather's kept (Open-Meteo's own updates are 15 minutes apart)


@dataclasses.dataclass(frozen=True)
class Weather:
    temp_f: float = 70.0
    wind_mph: float = 0.0
    wind_from: float = 0.0    # compass bearing the wind blows from
    gust_mph: float = 0.0
    code: int = 0             # WMO weather code
    source: str = "calm"      # "live", "random" or "calm"

    @property
    def sky(self):
        return next((name for c, name in reversed(CODES) if self.code >= c), "clear")

    @property
    def wet(self):
        return self.code >= WET

    def wind_on(self, bearing, gust=0.0):
        """(tail, cross) mph on a shot toward `bearing`: tail positive with the
        wind behind it, cross positive blowing it right. `gust` (0 to 1) is how
        far toward the gusts this moment's wind is."""
        speed = self.wind_mph + max(0.0, self.gust_mph - self.wind_mph) * gust
        to = math.radians((self.wind_from + 180) - bearing)
        return speed * math.cos(to), speed * math.sin(to)


def carry_factor(weather, tail):
    """Times the carry, for the temperature and a tail (or head) wind of `tail` mph."""
    wind = 0.005 * tail if tail > 0 else 0.01 * tail
    return max(0.6, 1 + wind + (weather.temp_f - 70) * 0.001)


def drift(cross, carry):
    """Yards a crosswind of `cross` mph moves a shot of `carry` yards."""
    return DRIFT * cross * carry / 150


def roll_factor(weather):
    return 0.5 if weather.wet else 1.0


_live = {}   # (lat, lon) -> (when it was asked, the weather then)


def live(lat, lon):
    """The weather at a course now (as Open-Meteo had it in the last
    LIVE_KEEP seconds: another round there doesn't wait for it again)."""
    key, now = (round(lat, 3), round(lon, 3)), time.monotonic()
    if key in _live and now - _live[key][0] < LIVE_KEEP:
        return _live[key][1]
    weather = _live_now(lat, lon)
    _live[key] = (now, weather)
    return weather


def _live_now(lat, lon):
    data = fetch_json(query_url(OPEN_METEO, latitude="%.4f" % lat, longitude="%.4f" % lon,
                                current="temperature_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m,weather_code",
                                wind_speed_unit="mph", temperature_unit="fahrenheit"), timeout=15)
    now = data.get("current") or {}
    num = lambda k, d=0.0: float(now[k]) if isinstance(now.get(k), (int, float)) else d
    return Weather(temp_f=num("temperature_2m", 70.0), wind_mph=num("wind_speed_10m"),
                   wind_from=num("wind_direction_10m"), gust_mph=num("wind_gusts_10m"),
                   code=int(num("weather_code")), source="live")


def windy_day(rng):
    """A day the wind blows: 15 to 25 mph, gusting higher."""
    wind = rng.uniform(15, 25)
    return Weather(temp_f=round(rng.uniform(50, 75)), wind_mph=round(wind, 1), wind_from=rng.uniform(0, 360),
                   gust_mph=round(wind * rng.uniform(1.3, 1.6), 1), code=rng.choice((1, 2, 3)), source="random")


def wet_day(rng):
    """A day it rains: soft ground, a breeze."""
    wind = rng.uniform(4, 14)
    return Weather(temp_f=round(rng.uniform(45, 65)), wind_mph=round(wind, 1), wind_from=rng.uniform(0, 360),
                   gust_mph=round(wind * rng.uniform(1.2, 1.5), 1), code=rng.choice((61, 63, 80)), source="random")


def random_day(rng):
    """A plausible day for golf: mostly fair, now and then blowing hard or wet."""
    wind = rng.choice((rng.uniform(0, 6), rng.uniform(4, 12), rng.uniform(8, 22)))
    code = rng.choices((0, 1, 2, 3, 61, 80), weights=(30, 25, 20, 12, 7, 6))[0]
    temp = rng.uniform(50, 90) - (8 if code >= WET else 0)
    return Weather(temp_f=round(temp), wind_mph=round(wind, 1), wind_from=rng.uniform(0, 360),
                   gust_mph=round(wind * rng.uniform(1.2, 1.7), 1), code=code, source="random")
