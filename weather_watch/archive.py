"""A record of what we forecast for each game and what the weather then actually was.

The Weather Service keeps observations for only about a week, so this has to be collected as
games finish: a game we miss can't be checked later. Each run:

1. saves the current forecast of every upcoming outdoor game, at three lead times
   ("72h", "24h", "final": the last forecast made at least that far ahead; "final" is the last
   run before kickoff), because a forecast three days out is the one people actually plan with;
2. for games that have finished, fetches the observed conditions from the nearest weather
   station and stores them next to the forecasts.

The file is published with the site (results.json) and carried forward by the next run, the
same way the score history is. weather_watch.scorecard reads it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Callable

from .weather import GAME_HOURS

LEADS = (("72h", timedelta(hours=72)), ("24h", timedelta(hours=24)))  # plus "final", the last one before kickoff
OBSERVE_AFTER = timedelta(hours=GAME_HOURS + 1)  # from the start of the kickoff hour: the 4-hour window, plus an hour of slack
OBSERVATIONS_KEPT = timedelta(days=6)  # the Weather Service keeps observations for about a week
MAX_ATTEMPTS = 6  # about 18 hours of runs: a game whose stations never have usable data is given up on
MAX_FETCHES_PER_RUN = 60
KEEP = timedelta(days=30)
VERSION = 1


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(timezone.utc)


def _window_start(kickoff: datetime) -> datetime:
    return kickoff.replace(minute=0, second=0, microsecond=0)


def load(path: str | os.PathLike | None) -> dict:
    """{game id: record} from a previously published results.json; anything unreadable means a fresh start."""
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        games = data.get("games", {})
        return games if isinstance(games, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def to_payload(games: dict, now: datetime) -> dict:
    return {"version": VERSION, "generated_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"), "games": games}


def snapshot(row: dict, now: datetime) -> dict:
    """The parts of a forecast worth checking later."""
    w = row["weather"]
    return {
        "at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "feels_min_f": w["feels_like_min_f"],
        "feels_max_f": w["feels_like_max_f"],
        "wind_mph": w["wind_mph"],
        "gust_mph": w["gust_mph"],
        "rain_in": w["rain_in"],
        "snow_in": w["snow_in"],
        "chance": w.get("precip_chance"),
        "clouds": w.get("cloud_cover"),
        "score": row["score"],
        "pleasant": row.get("pleasant"),
    }


def record_forecasts(games: dict, rows: list[dict], now: datetime) -> int:
    """Save the current forecast of every upcoming outdoor game; returns how many were touched."""
    touched = 0
    for row in rows:
        if row.get("indoor") or not row.get("weather") or row.get("lat") is None:
            continue
        kickoff = _parse(row["kickoff_utc"])
        lead = kickoff - now
        if lead <= timedelta(0):
            continue  # already started: the forecast is no longer a forecast
        record = games.setdefault(row["id"], {"forecasts": {}, "observed": None, "observed_status": None, "attempts": 0})
        record.update(
            league=row["league"], game=row["game"], matchup=row["matchup"], kickoff_utc=row["kickoff_utc"],
            venue=row["venue"], location=row["location"], lat=row["lat"], lon=row["lon"],
        )
        snap = snapshot(row, now)
        for label, ahead in LEADS:
            if lead >= ahead:
                record["forecasts"][label] = snap
        record["forecasts"]["final"] = snap
        touched += 1
    return touched


def observe_finished(
    games: dict, now: datetime, fetch: Callable[[float, float, datetime], dict | None]
) -> tuple[int, int]:
    """Fetch observed conditions for games whose window has passed. Returns (found, given up on)."""
    found = gave_up = fetches = 0
    for record in games.values():
        if record.get("observed") or record.get("observed_status") == "unavailable":
            continue
        try:
            start = _window_start(_parse(record["kickoff_utc"]))
        except (KeyError, TypeError, ValueError):
            continue
        if now < start + OBSERVE_AFTER:
            continue  # not over yet
        if now > start + OBSERVATIONS_KEPT or record.get("attempts", 0) >= MAX_ATTEMPTS:
            record["observed_status"] = "unavailable"
            gave_up += 1
            continue
        if fetches >= MAX_FETCHES_PER_RUN:
            break
        fetches += 1
        observed = fetch(record["lat"], record["lon"], start)
        if observed:
            record["observed"], record["observed_status"] = observed, "ok"
            found += 1
        else:
            record["attempts"] = record.get("attempts", 0) + 1
    return found, gave_up


def prune(games: dict, now: datetime) -> int:
    old = []
    for game_id, record in games.items():
        try:
            if now - _parse(record["kickoff_utc"]) > KEEP:
                old.append(game_id)
        except (KeyError, TypeError, ValueError):
            old.append(game_id)
    for game_id in old:
        del games[game_id]
    return len(old)
