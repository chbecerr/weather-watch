"""Turn a game-window forecast into a single "how extreme is this" score.

Every factor is a ramp through reference points, and the reference points mean the
same thing for every factor, so 20 points of cold is as extreme as 20 points of wind:

    points   meaning
    ~15      noticeable: it affects the game or the crowd
    ~40      severe:     roughly the level where the National Weather Service issues advisories
    ~80      extreme:    roughly warning level

    factor                       15 points      40 points      80 points
    cold (coldest feels-like)    20°F           5°F            -15°F
    heat (hottest feels-like)    95°F           105°F          112°F
    wind (sustained)             20 mph         30 mph         40 mph
    gusts                        about 37 mph   46 mph         58 mph
    rain (during the game)       0.3"           0.8"           1.5"
    snow (during the game)       1"             3"             6"
    thunderstorms                a flat 20 (lightning delays)

Nothing scores below its comfort level (feels-like above 40°F and below 85°F, wind
under 10 mph, gusts under 20 mph), so a calm, dry, mild game is 0. Past the last
reference point a ramp keeps climbing at its last slope, so worse is always higher.

These reference points are judgment calls, loosely anchored on typical NWS criteria
(which vary by region), not measurements: there is no ground truth for "how extreme
is a game's weather". They live here, in one place, and the tests pin them down.
Changing them changes every score, so bump SCORE_VERSION when you do: the site keeps
each game's score history and discards it when the version changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from .weather import GameWeather

SCORE_VERSION = 2  # 1 was the original linear weights

Ramp = tuple[tuple[float, float], ...]  # (value, points), ascending; the first point is the comfort limit

COLD: Ramp = ((0, 0), (20, 15), (35, 40), (55, 80))  # value = degrees below 40°F (feels-like)
HEAT: Ramp = ((85, 0), (95, 15), (105, 40), (112, 80))  # value = feels-like °F
WIND: Ramp = ((10, 0), (20, 15), (30, 40), (40, 80))  # value = sustained mph
GUST: Ramp = ((20, 0), (35, 10), (46, 40), (58, 80))  # value = gust mph
RAIN: Ramp = ((0, 0), (0.1, 3), (0.3, 15), (0.8, 40), (1.5, 80))  # value = inches during the game
SNOW: Ramp = ((0, 0), (0.1, 1.5), (1, 15), (3, 40), (6, 80), (10, 100))  # value = inches during the game

COLD_COMFORT_F = 40
GUST_SHARE = 0.25  # the smaller of sustained/gust adds this much: gusts track sustained wind, so don't double count
STORM_POINTS = 20.0
THUNDERSTORM_CODE = 95  # WMO weather code


def ramp(points: Ramp, x: float) -> float:
    if x <= points[0][0]:
        return 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    (x0, y0), (x1, y1) = points[-2], points[-1]
    return y1 + (y1 - y0) / (x1 - x0) * (x - x1)


@dataclass
class Score:
    total: float
    parts: dict[str, float]

    @property
    def tags(self) -> list[str]:
        return [k.upper() for k, v in sorted(self.parts.items(), key=lambda kv: -kv[1]) if v >= 5]


def score(w: GameWeather) -> Score:
    sustained, gust = ramp(WIND, w.wind_mph), ramp(GUST, w.gust_mph)
    parts = {
        "cold": ramp(COLD, COLD_COMFORT_F - w.feels_min_f),
        "heat": ramp(HEAT, w.feels_max_f),
        "wind": max(sustained, gust) + GUST_SHARE * min(sustained, gust),
        "rain": ramp(RAIN, w.rain_in),
        "snow": ramp(SNOW, w.snow_in),
        "storm": STORM_POINTS if w.weather_code >= THUNDERSTORM_CODE else 0.0,
    }
    parts = {k: round(v, 1) for k, v in parts.items()}
    return Score(total=round(sum(parts.values()), 1), parts=parts)
