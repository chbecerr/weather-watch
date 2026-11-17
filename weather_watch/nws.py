"""Hourly forecasts from the US National Weather Service (api.weather.gov).

US locations only, about 7 days ahead. The NWS gridpoint data is converted into
a simple hourly table (°F, mph, inches, WMO-style weather codes) that the
scoring code reads.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from .http import get_json
from .weather import load_cache, save_cache

POINTS_URL = "https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
CACHE_NAME = "nws_grids-v2.json"  # v2 also stores each point's time zone

_DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")


def parse_valid_time(valid_time: str) -> tuple[datetime, int]:
    """'2026-11-15T18:00:00+00:00/PT6H' -> (start in UTC, hours)."""
    start_s, dur = valid_time.split("/")
    m = _DURATION.match(dur)
    if not m:
        raise ValueError(f"unsupported duration: {dur}")
    days, hours, minutes = (int(x or 0) for x in m.groups())
    total = days * 24 + hours + (1 if minutes else 0)
    start = datetime.fromisoformat(start_s).astimezone(timezone.utc)
    return start, max(total, 1)


def _convert(value: float, uom: str) -> float:
    unit = (uom or "").split(":")[-1]
    if unit == "degC":
        return value * 9 / 5 + 32
    if unit == "km_h-1":
        return value / 1.609344
    if unit == "m_s-1":
        return value * 2.236936
    if unit == "mm":
        return value / 25.4
    return value


def _hourly(prop: dict | None, amount: bool = False) -> dict[str, float]:
    """Expand NWS interval values into {"YYYY-MM-DDTHH:00": value} per hour.

    Amounts (precip, snow) are spread evenly across their interval.
    """
    out: dict[str, float] = {}
    if not prop:
        return out
    uom = prop.get("uom", "")
    for v in prop.get("values", []):
        if v.get("value") is None:
            continue
        start, hours = parse_valid_time(v["validTime"])
        val = _convert(v["value"], uom)
        if amount:
            val /= hours
        for i in range(hours):
            out[(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00")] = val
    return out


# NWS "weather" type -> WMO code, most severe wins.
_WX_CODES = {
    "thunderstorms": 95,
    "blowing_snow": 75,
    "snow": 73,
    "snow_showers": 85,
    "freezing_rain": 66,
    "freezing_drizzle": 56,
    "sleet": 66,
    "rain": 63,
    "rain_showers": 80,
    "drizzle": 53,
    "fog": 45,
    "freezing_fog": 48,
}


# Low-confidence coverages ("slight chance of thunderstorms") don't count as the
# hour's weather: a mention of "slight chance of thunderstorms" isn't a storm.
_UNLIKELY = {"slight_chance", "chance", "isolated", "few", "patchy"}


def _weather_codes(grid: dict) -> dict[str, int]:
    codes: dict[str, int] = {}
    for hour, cover in _hourly(grid.get("skyCover")).items():
        codes[hour] = 0 if cover < 25 else 2 if cover < 60 else 3
    for v in (grid.get("weather") or {}).get("values", []):
        kinds = [
            w.get("weather")
            for w in (v.get("value") or [])
            if w.get("weather") and w.get("coverage") not in _UNLIKELY
        ]
        code = max((_WX_CODES.get(k, 0) for k in kinds), default=0)
        if not code:
            continue
        start, hours = parse_valid_time(v["validTime"])
        for i in range(hours):
            key = (start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00")
            codes[key] = max(codes.get(key, 0), code)
    return codes


def grid_to_hourly(grid: dict) -> dict:
    """Convert NWS gridpoint properties into a dict of hourly lists."""
    temp = _hourly(grid.get("temperature"))
    feels = _hourly(grid.get("apparentTemperature"))
    wind = _hourly(grid.get("windSpeed"))
    gust = _hourly(grid.get("windGust"))
    qpf = _hourly(grid.get("quantitativePrecipitation"), amount=True)
    snow = _hourly(grid.get("snowfallAmount"), amount=True)
    pop = _hourly(grid.get("probabilityOfPrecipitation"))  # a percentage, so not spread out like amounts
    sky = _hourly(grid.get("skyCover"))  # percent of the sky covered by cloud
    codes = _weather_codes(grid)

    times = sorted(temp)
    hourly = {k: [] for k in (
        "time", "temperature_2m", "apparent_temperature", "rain", "showers",
        "snowfall", "wind_speed_10m", "wind_gusts_10m", "weather_code", "precip_probability", "cloud_cover",
    )}
    for t in times:
        sn = snow.get(t, 0.0)
        hourly["time"].append(t)
        hourly["temperature_2m"].append(temp[t])
        hourly["apparent_temperature"].append(feels.get(t, temp[t]))
        # QPF is liquid-equivalent and includes snow (~10:1 snow-to-liquid).
        hourly["rain"].append(max(0.0, qpf.get(t, 0.0) - sn / 10))
        hourly["showers"].append(0.0)
        hourly["snowfall"].append(sn)
        hourly["wind_speed_10m"].append(wind.get(t, 0.0))
        hourly["wind_gusts_10m"].append(gust.get(t, wind.get(t, 0.0)))
        hourly["weather_code"].append(codes.get(t, 0))
        hourly["precip_probability"].append(pop.get(t))  # None = the Weather Service gave no value
        hourly["cloud_cover"].append(sky.get(t))
    return {"hourly": hourly}


def _point(lat: float, lon: float, cache: dict) -> dict | None:
    """{"grid": forecast URL, "tz": IANA time zone} for a location, cached."""
    key = f"{lat:.4f},{lon:.4f}"
    hit = cache.get(key)
    if isinstance(hit, dict) and hit.get("grid"):
        return hit
    try:
        props = get_json(POINTS_URL.format(lat=lat, lon=lon))["properties"]
    except Exception:
        return None  # outside the US (404) or a transient failure
    if not props.get("forecastGridData"):
        return None
    cache[key] = {"grid": props["forecastGridData"], "tz": props.get("timeZone")}
    return cache[key]


def fetch_forecasts(coords: list[tuple[float, float]]) -> dict[tuple[float, float], dict | None]:
    """NWS forecasts keyed by (lat, lon); None where NWS has no data (e.g. non-US)."""
    cache = load_cache(CACHE_NAME)

    def one(c):
        point = _point(c[0], c[1], cache)
        if not point:
            return None
        try:
            forecast = grid_to_hourly(get_json(point["grid"])["properties"])
        except Exception:
            return None
        forecast["timezone"] = point.get("tz")
        return forecast

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one, coords))
    save_cache(cache, CACHE_NAME)
    return dict(zip(coords, results))
