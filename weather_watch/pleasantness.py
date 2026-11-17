"""A 0-100 "how nice is it to sit there" score: the companion to the extremeness score.

It is not the extremeness score turned upside down. Most games are completely calm by
that measure (it floors at zero), which would leave dozens of games tied. Pleasantness
has its own factors and its own ramps, and separates mild games from each other.

Audience: a fan sitting in the stands for three hours. People sit still, so they feel
cold more than players do, and they can't take layers off in the heat, so heat costs
more than the same distance of cold. Start at 100 and subtract:

    temperature  by hour, from the feels-like temperature; best is 60-72°F
    wind         light air is fine; it starts to cost from about 6 mph
    precip       chance of precipitation and amount; thunderstorms cost extra
    clouds       overcast costs a little, but only while the sun is up

    reference conditions (calm, dry, daytime and sunny unless stated)         score
    60-72°F                                                                    100
    50°F          40°F          32°F          20°F                             90   75   60   35
    82°F          90°F          100°F                                          83   58   20
    68°F, overcast, daytime  (the same at night: clouds can't be seen)         88   (100)
    68°F, 15 mph sustained wind with 25 mph gusts                              about 75
    68°F, 0.3" of rain: with no chance of rain given / with a 60% chance       70 / 65

Bands used on the site: 85+ great, 70-84 nice, 50-69 meh, under 50 rough.

Like the extremeness score, the reference points are judgment calls: there is no ground
truth for "pleasant". They live here, in one place, and the tests pin them down. Dome
games have no weather, so they have no pleasantness score.
"""

from __future__ import annotations

from dataclasses import dataclass

from .weather import GameWeather

Ramp = tuple[tuple[float, float], ...]  # (value, points lost), ascending; 0 before the first point

COLD: Ramp = ((0, 0), (10, 10), (20, 25), (28, 40), (40, 65), (55, 90))  # value = degrees below 60°F (feels-like)
HEAT: Ramp = ((72, 0), (80, 12), (85, 25), (90, 42), (95, 60), (100, 80))  # value = feels-like °F
WIND: Ramp = ((6, 0), (10, 8), (15, 22), (20, 40), (30, 70))  # sustained mph
GUST: Ramp = ((15, 0), (25, 10), (35, 30), (46, 60))  # gust mph
CHANCE: Ramp = ((10, 0), (30, 6), (50, 14), (70, 24), (100, 35))  # chance of precipitation, %
RAIN: Ramp = ((0, 0), (0.02, 2), (0.1, 12), (0.3, 30), (0.8, 55), (1.5, 80))  # inches during the game
SNOW: Ramp = ((0, 0), (0.1, 5), (1, 30), (3, 60), (6, 85))  # inches during the game
CLOUDS: Ramp = ((30, 0), (50, 3), (75, 7), (100, 12))  # average cloud cover %, in daylight

IDEAL_LOW_F, IDEAL_HIGH_F = 60, 72
SECONDARY_SHARE = 0.25  # of two related penalties (sustained wind vs gusts; chance vs amount) the smaller adds this much
STORM_PENALTY = 25.0
THUNDERSTORM_CODE = 95  # WMO weather code


def _ramp(points: Ramp, x: float) -> float:
    if x <= points[0][0]:
        return 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    (x0, y0), (x1, y1) = points[-2], points[-1]
    return y1 + (y1 - y0) / (x1 - x0) * (x - x1)  # keep going at the last slope


def _combine(a: float, b: float) -> float:
    return max(a, b) + SECONDARY_SHARE * min(a, b)


def band(total: float) -> str:
    return "great" if total >= 85 else "nice" if total >= 70 else "meh" if total >= 50 else "rough"


@dataclass
class Pleasantness:
    total: float  # 0-100, higher is nicer
    parts: dict[str, float]  # points lost to each factor

    @property
    def band(self) -> str:
        return band(self.total)


def _temperature_penalty(feels: float) -> float:
    return _ramp(COLD, IDEAL_LOW_F - feels) + _ramp(HEAT, feels)  # only one of the two is ever non-zero


def pleasantness(w: GameWeather, daylight: float = 1.0) -> Pleasantness:
    """Score a game's weather. `daylight` is the share of the game with the sun up (see solar.py)."""
    feels = [h["feels_f"] for h in w.hours] or [(w.feels_min_f + w.feels_max_f) / 2]
    precip = _combine(
        _ramp(CHANCE, w.precip_chance) if w.precip_chance is not None else 0.0,
        max(_ramp(RAIN, w.rain_in), _ramp(SNOW, w.snow_in)),
    ) + (STORM_PENALTY if w.weather_code >= THUNDERSTORM_CODE else 0.0)
    clouds = _ramp(CLOUDS, w.cloud_cover) * daylight if w.cloud_cover is not None else 0.0
    parts = {
        "temp": sum(_temperature_penalty(f) for f in feels) / len(feels),  # hour by hour, so a swing counts
        "wind": _combine(_ramp(WIND, w.wind_mph), _ramp(GUST, w.gust_mph)),
        "precip": precip,
        "clouds": clouds,
    }
    parts = {k: round(v, 1) for k, v in parts.items()}
    return Pleasantness(total=round(max(0.0, min(100.0, 100 - sum(parts.values()))), 1), parts=parts)
