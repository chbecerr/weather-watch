"""Is the sun up? Used to count cloud cover only when it can actually be seen.

Position of the sun from NOAA's general solar calculations (accurate to about a
minute for sunrise and sunset), so no outside service is needed.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

SUNRISE_SUNSET_ELEVATION = -0.833  # degrees: the sun's disc touching the horizon, allowing for refraction


def solar_elevation(lat: float, lon: float, when: datetime) -> float:
    """Degrees of the sun's centre above the horizon (negative = below). lon is east-positive."""
    when = when.astimezone(timezone.utc)
    day_of_year = when.timetuple().tm_yday
    hour = when.hour + when.minute / 60 + when.second / 3600
    gamma = 2 * math.pi / 365 * (day_of_year - 1 + (hour - 12) / 24)  # fractional year, radians
    eq_time = 229.18 * (  # equation of time, minutes
        0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma)
    )
    declination = (
        0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma)
    )
    solar_minutes = hour * 60 + eq_time + 4 * lon  # true solar time, minutes since solar midnight
    hour_angle = math.radians(solar_minutes / 4 - 180)
    phi = math.radians(lat)
    cos_zenith = math.sin(phi) * math.sin(declination) + math.cos(phi) * math.cos(declination) * math.cos(hour_angle)
    return 90 - math.degrees(math.acos(max(-1.0, min(1.0, cos_zenith))))


def sun_is_up(lat: float, lon: float, when: datetime) -> bool:
    return solar_elevation(lat, lon, when) > SUNRISE_SUNSET_ELEVATION


def daylight_fraction(lat: float, lon: float, hour_starts: list[datetime], samples_per_hour: int = 4) -> float:
    """Share of the given hours (each a UTC start time) during which the sun is up."""
    times = [h + timedelta(minutes=60 * k / samples_per_hour) for h in hour_starts for k in range(samples_per_hour)]
    if not times:
        return 0.0
    return sum(sun_is_up(lat, lon, t) for t in times) / len(times)
