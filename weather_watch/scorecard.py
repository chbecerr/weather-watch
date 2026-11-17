"""How good were the forecasts? Compares each forecast in the archive with what was observed.

What can be checked is what weather stations measure: feels-like temperature, sustained wind,
cloud cover, and whether it rained or snowed. They almost never report rain or snow *amounts*,
so those, and therefore the full wildness score, can't be verified. Instead the "partial
score" below uses only the parts that can be: cold, heat and sustained wind, with the same
ramps as the real score. It is computed the same way from the forecast and from the
observation, so the two are comparable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median

from . import scoring

LABELS = ("72h", "24h", "final")
WET_CHANCE = 50  # a forecast "calls" rain or snow at this chance of precipitation...
WET_AMOUNT = 0.05  # ...or at this much forecast rain/snow (inches)
TOP = 5


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(timezone.utc)


def partial_score(feels_min: float, feels_max: float, wind_mph: float) -> float:
    """The part of the wildness score that weather stations can verify: cold, heat, sustained wind."""
    return round(
        scoring.ramp(scoring.COLD, scoring.COLD_COMFORT_F - feels_min)
        + scoring.ramp(scoring.HEAT, feels_max)
        + scoring.ramp(scoring.WIND, wind_mph),
        1,
    )


def forecast_wet(forecast: dict) -> bool:
    return (forecast.get("chance") or 0) >= WET_CHANCE or (forecast.get("rain_in") or 0) + (forecast.get("snow_in") or 0) >= WET_AMOUNT


def observed_wet(observed: dict) -> bool:
    return bool(observed.get("rain") or observed.get("snow") or observed.get("storm"))


def compare(record: dict, label: str) -> dict | None:
    """One game's forecast at one lead time against what happened, or None if either is missing."""
    forecast, observed = (record.get("forecasts") or {}).get(label), record.get("observed")
    if not forecast or not observed:
        return None
    wet_forecast, wet_observed = forecast_wet(forecast), observed_wet(observed)
    clouds_known = forecast.get("clouds") is not None and observed.get("cloud_cover") is not None
    return {
        "game": record["game"],
        "matchup": record.get("matchup", record["game"]),
        "league": record.get("league"),
        "kickoff_utc": record["kickoff_utc"],
        "station": observed.get("station"),
        "feels_min_error": observed["feels_min_f"] - forecast["feels_min_f"],  # positive: it was warmer than forecast
        "feels_max_error": observed["feels_max_f"] - forecast["feels_max_f"],
        "wind_error": observed["wind_mph"] - forecast["wind_mph"],  # positive: windier than forecast
        "cloud_error": observed["cloud_cover"] - forecast["clouds"] if clouds_known else None,
        "wet": "hit" if wet_forecast and wet_observed else "false alarm" if wet_forecast else "miss" if wet_observed else "dry",
        "forecast_partial": partial_score(forecast["feels_min_f"], forecast["feels_max_f"], forecast["wind_mph"]),
        "observed_partial": partial_score(observed["feels_min_f"], observed["feels_max_f"], observed["wind_mph"]),
        "forecast": forecast,
        "observed": observed,
    }


def ranks(values: list[float]) -> list[float]:
    """Ranks starting at 1, with tied values sharing the average of the ranks they span."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def rank_correlation(a: list[float], b: list[float]) -> float | None:
    """Spearman correlation (1 = same order, 0 = unrelated, -1 = reversed); None if it can't be defined."""
    if len(a) != len(b) or len(a) < 3:
        return None
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va, vb = sum((x - ma) ** 2 for x in ra), sum((y - mb) ** 2 for y in rb)
    if va == 0 or vb == 0:
        return None  # e.g. every game was equally calm
    return round(cov / (va * vb) ** 0.5, 2)


def top_overlap(comparisons: list[dict]) -> tuple[int, int, list[str], list[str]]:
    """How many of the forecast's roughest games were really among the roughest.

    Returns (overlap, k, forecast's top games, observed top games), with k at most TOP and never more
    than the number of games that were rough at all.
    """
    k = min(TOP, sum(1 for c in comparisons if c["observed_partial"] > 0), sum(1 for c in comparisons if c["forecast_partial"] > 0))
    if k == 0:
        return 0, 0, [], []
    def top(key):
        ordered = sorted(comparisons, key=lambda c: (-c[key], c["game"]))
        return [c["game"] for c in ordered[:k]]
    forecast_top, observed_top = top("forecast_partial"), top("observed_partial")
    return len(set(forecast_top) & set(observed_top)), k, forecast_top, observed_top


def _med(values: list[float]) -> float | None:
    return round(median(values), 1) if values else None


def summarize(comparisons: list[dict]) -> dict:
    """Headline numbers for a set of comparisons made at one lead time."""
    wet = {k: sum(1 for c in comparisons if c["wet"] == k) for k in ("hit", "miss", "false alarm", "dry")}
    overlap, k, f_top, o_top = top_overlap(comparisons)
    return {
        "games": len(comparisons),
        "feels_min_abs_error": _med([abs(c["feels_min_error"]) for c in comparisons]),
        "feels_min_bias": _med([c["feels_min_error"] for c in comparisons]),
        "wind_abs_error": _med([abs(c["wind_error"]) for c in comparisons]),
        "wind_bias": _med([c["wind_error"] for c in comparisons]),
        "cloud_abs_error": _med([abs(c["cloud_error"]) for c in comparisons if c["cloud_error"] is not None]),
        "wet": wet,
        "rank_correlation": rank_correlation([c["forecast_partial"] for c in comparisons], [c["observed_partial"] for c in comparisons]),
        "top_overlap": overlap,
        "top_k": k,
        "forecast_top": f_top,
        "observed_top": o_top,
    }


def evaluate(games: dict, since: datetime, until: datetime) -> dict[str, list[dict]]:
    """Comparisons per lead time for games that kicked off in [since, until)."""
    out: dict[str, list[dict]] = {label: [] for label in LABELS}
    for record in games.values():
        try:
            kickoff = _parse(record["kickoff_utc"])
        except (KeyError, TypeError, ValueError):
            continue
        if not since <= kickoff < until:
            continue
        for label in LABELS:
            c = compare(record, label)
            if c:
                out[label].append(c)
    return out


def biggest_misses(comparisons: list[dict], n: int = 3) -> list[dict]:
    """Games where the forecast's partial score was furthest from the observed one."""
    return sorted(comparisons, key=lambda c: (-abs(c["observed_partial"] - c["forecast_partial"]), c["game"]))[:n]
