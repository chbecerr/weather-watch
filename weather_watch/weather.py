"""Geocode stadium cities and pull hourly forecasts from Open-Meteo (no API key)."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timedelta, timezone

from .http import get_json

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

HOURLY_VARS = [
    "temperature_2m",
    "apparent_temperature",
    "rain",
    "showers",
    "snowfall",
    "wind_speed_10m",
    "wind_gusts_10m",
    "weather_code",
]

GAME_HOURS = 4  # kickoff hour + 3 more ~= a football game

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico",
}

# WMO weather codes -> short description (subset Open-Meteo returns).
WMO = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Rain showers", 81: "Rain showers", 82: "Violent rain showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorms", 96: "Thunderstorms w/ hail", 99: "Thunderstorms w/ hail",
}


@dataclass
class GameWeather:
    temp_f: float
    feels_min_f: float
    feels_max_f: float
    wind_mph: float
    gust_mph: float
    rain_in: float
    snow_in: float
    weather_code: int

    @property
    def conditions(self) -> str:
        return WMO.get(self.weather_code, f"WMO {self.weather_code}")


def geocode(city: str, state: str, country: str) -> tuple[float, float] | None:
    if not city:
        return None
    # The geocoder only matches place names, so "Chestnut Hill" not "Chestnut Hill, MA".
    data = get_json(GEOCODE_URL, {"name": city, "count": 20, "language": "en"})
    results = data.get("results") or []
    if not results:
        return None
    is_us = country in ("", "USA", "United States")
    state_name = US_STATES.get(state.upper(), state) if state else ""

    def pick(pred):
        return next((r for r in results if pred(r)), None)

    best = None
    if is_us:
        best = pick(lambda r: r.get("country_code") == "US" and r.get("admin1") == state_name)
        best = best or pick(lambda r: r.get("country_code") == "US")
    else:
        best = pick(lambda r: (r.get("country") or "").lower() == country.lower())
    best = best or results[0]
    return best["latitude"], best["longitude"]


def _cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "weather-watch" / "geocode.json"


def _load_cache() -> dict[str, list[float]]:
    try:
        return json.loads(_cache_path().read_text())
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict[str, list[float]]) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=1, sort_keys=True))
    except OSError:
        pass  # caching is best-effort


def geocode_many(places: set[tuple[str, str, str]]) -> dict[tuple[str, str, str], tuple[float, float] | None]:
    """Geocode venue cities, using an on-disk cache (stadiums don't move)."""
    cache = _load_cache()
    key = "|".join
    out: dict[tuple[str, str, str], tuple[float, float] | None] = {}
    missing = []
    for p in sorted(places):
        hit = cache.get(key(p))
        if hit:
            out[p] = (hit[0], hit[1])
        else:
            missing.append(p)
    if missing:
        with ThreadPoolExecutor(max_workers=4) as pool:
            found = list(pool.map(lambda p: _safe_geocode(*p), missing))
        for p, c in zip(missing, found):
            out[p] = c
            if c:
                cache[key(p)] = list(c)
        _save_cache(cache)
    return out


def _safe_geocode(city, state, country):
    try:
        return geocode(city, state, country)
    except Exception:
        return None


def fetch_forecasts(coords: list[tuple[float, float]], chunk: int = 50) -> list[dict]:
    """Hourly forecasts (UTC) for many points; Open-Meteo accepts comma-separated lists."""
    out: list[dict] = []
    for i in range(0, len(coords), chunk):
        part = coords[i : i + chunk]
        data = get_json(
            FORECAST_URL,
            {
                "latitude": ",".join(f"{lat:.4f}" for lat, _ in part),
                "longitude": ",".join(f"{lon:.4f}" for _, lon in part),
                "hourly": ",".join(HOURLY_VARS),
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "precipitation_unit": "inch",
                "timezone": "GMT",
                "past_days": 1,
                "forecast_days": 16,
            },
        )
        out.extend(data if isinstance(data, list) else [data])
    return out


def summarize_window(forecast: dict, kickoff: datetime, hours: int = GAME_HOURS) -> GameWeather | None:
    """Collapse the hourly forecast covering the game into one summary."""
    h = forecast.get("hourly") or {}
    times = h.get("time") or []
    start = kickoff.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    wanted = {(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(hours)}
    idx = [i for i, t in enumerate(times) if t in wanted]
    if not idx:
        return None

    def col(name):
        vals = [h[name][i] for i in idx if h.get(name) and h[name][i] is not None]
        return vals or [0.0]

    codes = col("weather_code")
    return GameWeather(
        temp_f=col("temperature_2m")[0],
        feels_min_f=min(col("apparent_temperature")),
        feels_max_f=max(col("apparent_temperature")),
        wind_mph=max(col("wind_speed_10m")),
        gust_mph=max(max(col("wind_gusts_10m")), max(col("wind_speed_10m"))),
        rain_in=sum(col("rain")) + sum(col("showers")),
        snow_in=sum(col("snowfall")),
        weather_code=int(max(codes)),  # WMO codes rise roughly with severity
    )
