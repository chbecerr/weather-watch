"""Geocode stadium cities and summarize a game-window forecast."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timedelta, timezone

from .http import get_json

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

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

# WMO weather codes -> short description (nws.py maps NWS conditions onto these).
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


def _cache_path(name: str = "geocode.json") -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "weather-watch" / name


def load_cache(name: str = "geocode.json") -> dict:
    try:
        return json.loads(_cache_path(name).read_text())
    except (OSError, ValueError):
        return {}


def save_cache(cache: dict, name: str = "geocode.json") -> None:
    try:
        path = _cache_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=1, sort_keys=True))
    except OSError:
        pass  # caching is best-effort


def geocode_many(places: set[tuple[str, str, str]]) -> dict[tuple[str, str, str], tuple[float, float] | None]:
    """Geocode venue cities, using an on-disk cache (stadiums don't move)."""
    cache = load_cache()
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
        save_cache(cache)
    return out


def _safe_geocode(city, state, country):
    try:
        return geocode(city, state, country)
    except Exception:
        return None


def summarize_window(forecast: dict, kickoff: datetime, hours: int = GAME_HOURS) -> GameWeather | None:
    """Collapse the hourly forecast (see nws.grid_to_hourly) covering the game into one summary."""
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
