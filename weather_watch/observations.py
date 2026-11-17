"""What the weather actually was: measurements from the station nearest a stadium.

Used to check the forecasts after the fact. Stations report every 5-10 minutes in SI units;
we convert, average per clock hour (the forecast is hourly, and the single highest 2-minute
wind reading would be biased high), and summarise the same 4 hours the forecast covers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .http import get_json
from .nws import POINTS_URL, convert_unit
from .weather import GAME_HOURS

STATIONS_CACHE = "nws_stations.json"
OBSERVATIONS_URL = "https://api.weather.gov/stations/{station}/observations"
MAX_STATIONS = 3  # nearest first; some stations report no wind or sky, so we may need to look further
MIN_READINGS = 3  # readings with both temperature and wind needed before we trust a station

# METAR sky cover in eighths -> percent (midpoints: FEW 1-2/8, SCT 3-4/8, BKN 5-7/8, OVC 8/8)
CLOUD_PERCENT = {"CLR": 0, "SKC": 0, "FEW": 19, "SCT": 44, "BKN": 75, "OVC": 100, "VV": 100}
SNOW_WORDS = ("snow", "flurr", "sleet", "ice pellet")
STORM_WORDS = ("thunder",)


def _value(props: dict, key: str) -> float | None:
    field = props.get(key)
    if not isinstance(field, dict) or field.get("value") is None:
        return None
    return convert_unit(field["value"], field.get("unitCode"))


def _sky_cover(props: dict) -> float | None:
    """Cloud cover from the layers, or None if the reading carries no sky information
    (an empty list means "not reported", not "clear")."""
    amounts = [CLOUD_PERCENT[layer["amount"]] for layer in props.get("cloudLayers") or [] if layer.get("amount") in CLOUD_PERCENT]
    return max(amounts) if amounts else None


def _weather_text(props: dict) -> str:
    present = " ".join(str(w.get("weather") or "") for w in props.get("presentWeather") or [] if isinstance(w, dict))
    return f"{present} {props.get('textDescription') or ''}".lower().replace("_", " ")


def _is_rain(text: str) -> bool:
    """"Showers" alone means rain, but "snow showers" doesn't."""
    return "rain" in text or "drizzle" in text or ("shower" in text and not any(w in text for w in SNOW_WORDS))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def summarize(features: list[dict], window_start: datetime, hours: int = GAME_HOURS) -> dict | None:
    """Summarise a station's readings for [window_start, window_start + hours), or None if too few to trust.

    temperature, feels-like and wind are averaged per clock hour first, then the extremes taken across
    the hours, so they are comparable with an hourly forecast.
    """
    end = window_start + timedelta(hours=hours)
    per_hour: dict[datetime, dict[str, list[float]]] = {}
    gusts: list[float] = []
    clouds: list[float] = []
    text = ""
    usable = 0
    for feature in features:
        props = feature.get("properties") or {}
        try:
            when = datetime.fromisoformat(props["timestamp"]).astimezone(timezone.utc)
        except (KeyError, TypeError, ValueError):
            continue
        if not window_start <= when < end:
            continue
        temp, wind = _value(props, "temperature"), _value(props, "windSpeed")
        bucket = per_hour.setdefault(when.replace(minute=0, second=0, microsecond=0), {"temp": [], "feels": [], "wind": []})
        if temp is not None:
            chill, heat = _value(props, "windChill"), _value(props, "heatIndex")
            bucket["temp"].append(temp)
            bucket["feels"].append(chill if chill is not None else heat if heat is not None else temp)
        if wind is not None:
            bucket["wind"].append(wind)
        if temp is not None and wind is not None:
            usable += 1
        gust = _value(props, "windGust")
        if gust is not None:
            gusts.append(gust)
        sky = _sky_cover(props)
        if sky is not None:
            clouds.append(sky)
        text += " " + _weather_text(props)
    hourly = [per_hour[h] for h in sorted(per_hour)]
    temps = [_mean(b["temp"]) for b in hourly if b["temp"]]
    feels = [_mean(b["feels"]) for b in hourly if b["feels"]]
    winds = [_mean(b["wind"]) for b in hourly if b["wind"]]
    if usable < MIN_READINGS or not temps or not winds:
        return None
    return {
        "temp_f": round(temps[0]),  # the first hour: closest to kickoff
        "feels_min_f": round(min(feels)),
        "feels_max_f": round(max(feels)),
        "wind_mph": round(max(winds)),
        "gust_mph": round(max(gusts)) if gusts else None,  # reported only when it actually gusts
        "cloud_cover": round(_mean(clouds)) if clouds else None,
        "rain": _is_rain(text),
        "snow": any(w in text for w in SNOW_WORDS),
        "storm": any(w in text for w in STORM_WORDS),
        "readings": usable,
    }


def _stations(lat: float, lon: float, cache: dict) -> list[str]:
    """Nearest station ids for a point (cached: stations don't move)."""
    key = f"{lat:.4f},{lon:.4f}"
    if key not in cache:
        props = get_json(POINTS_URL.format(lat=lat, lon=lon))["properties"]
        features = get_json(props["observationStations"])["features"]
        cache[key] = [f["properties"]["stationIdentifier"] for f in features[: MAX_STATIONS + 2]]
    return cache[key]


def fetch_observed(
    lat: float, lon: float, window_start: datetime, hours: int = GAME_HOURS, cache: dict | None = None
) -> dict | None:
    """Observed conditions for the window, from the nearest station that has usable data (or None)."""
    cache = {} if cache is None else cache
    end = window_start + timedelta(hours=hours)
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    try:
        stations = _stations(lat, lon, cache)
    except Exception:
        return None  # try again on the next run
    for station in stations[:MAX_STATIONS]:
        try:
            features = get_json(
                OBSERVATIONS_URL.format(station=station),
                {"start": window_start.astimezone(timezone.utc).strftime(stamp), "end": end.astimezone(timezone.utc).strftime(stamp), "limit": 200},
            )["features"]
        except Exception:
            continue
        summary = summarize(features, window_start, hours)
        if summary:
            summary["station"] = station
            return summary
    return None
