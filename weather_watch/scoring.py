"""Turn a game-window forecast into a single "how extreme is this" score.

Each factor contributes points only past a comfort threshold, so a mild,
dry, calm afternoon scores 0. Rough calibration:

    feels-like 10°F          -> 30 pts cold
    feels-like 100°F         -> 22 pts heat
    25 mph sustained wind    -> 30 pts (+ gusts above 20 mph)
    0.5" of rain in the game -> 20 pts
    2" of snow in the game   -> 30 pts
    thunderstorms            -> +20 (lightning delays)
"""

from __future__ import annotations

from dataclasses import dataclass

from .weather import GameWeather

COLD_BELOW_F = 40
HEAT_ABOVE_F = 85
WIND_ABOVE_MPH = 10
GUST_ABOVE_MPH = 20


@dataclass
class Score:
    total: float
    parts: dict[str, float]

    @property
    def tags(self) -> list[str]:
        return [k.upper() for k, v in sorted(self.parts.items(), key=lambda kv: -kv[1]) if v >= 5]


def score(w: GameWeather) -> Score:
    parts = {
        "cold": max(0.0, COLD_BELOW_F - w.feels_min_f) * 1.0,
        "heat": max(0.0, w.feels_max_f - HEAT_ABOVE_F) * 1.5,
        "wind": max(0.0, w.wind_mph - WIND_ABOVE_MPH) * 2.0
        + max(0.0, w.gust_mph - GUST_ABOVE_MPH) * 0.75,
        "rain": w.rain_in * 40.0,
        "snow": w.snow_in * 15.0,
        "storm": 20.0 if w.weather_code >= 95 else 0.0,
    }
    parts = {k: round(v, 1) for k, v in parts.items()}
    return Score(total=round(sum(parts.values()), 1), parts=parts)
