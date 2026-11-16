"""Score history, so the site can show how a game's ranking has moved as forecasts update.

Nothing is stored separately: each published data.json carries every game's
recent scores, and the next run reads the previous file back and adds to it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from .scoring import SCORE_VERSION

RETENTION = timedelta(days=8)  # forecasts reach ~7 days ahead, plus a day of slack
MIN_SPACING = timedelta(hours=2)  # a re-run within this of the last sample replaces it
TREND_MIN_AGE = timedelta(hours=5)  # need a baseline at least this old to call it a trend...
TREND_MAX_AGE = timedelta(hours=26)  # ...and we compare to roughly a day ago, no older


def load_previous(path: str | os.PathLike | None) -> dict[str, list[list]]:
    """{game id: [[iso time, score], ...]} from a previously published data.json.

    A missing or unreadable file just means "no history yet". So does a file written
    under a different scoring formula: its scores aren't comparable with today's, and
    carrying them over would show every game as having jumped.
    """
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("score_version", 1) != SCORE_VERSION:
            return {}
        games = data.get("games", [])
    except (OSError, ValueError, AttributeError):
        return {}
    out: dict[str, list[list]] = {}
    for g in games:
        hist = g.get("history") if isinstance(g, dict) else None
        if g.get("id") and isinstance(hist, list):
            out[g["id"]] = [s for s in hist if isinstance(s, list) and len(s) == 2]
    return out


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(timezone.utc)


def extend(samples: list[list], score: float, now: datetime) -> list[list]:
    """Add the current score, drop samples older than RETENTION, keep samples spaced out."""
    keep = []
    for t, v in samples:
        try:
            if now - _parse(t) <= RETENTION:
                keep.append([t, v])
        except (TypeError, ValueError):
            continue  # skip garbage rather than fail the whole run
    now_iso = now.isoformat(timespec="seconds")
    if keep and now - _parse(keep[-1][0]) < MIN_SPACING:
        keep[-1] = [now_iso, score]
    else:
        keep.append([now_iso, score])
    return keep


def trend(samples: list[list], now: datetime) -> dict | None:
    """Change since the oldest sample that is between ~5 and ~26 hours old."""
    if not samples:
        return None
    current = samples[-1][1]
    baseline = None
    for t, v in samples[:-1]:
        age = now - _parse(t)
        if TREND_MIN_AGE <= age <= TREND_MAX_AGE:
            baseline = (age, v)
            break  # samples are oldest-first, so this is the oldest one in range
    if baseline is None:
        return None
    age, prev = baseline
    return {"delta": round(current - prev, 1), "hours": round(age.total_seconds() / 3600), "prev": prev}


def apply(rows: list[dict], previous: dict[str, list[list]], now: datetime) -> None:
    """Attach "history" and "trend" to every game that has a forecast."""
    for row in rows:
        if row.get("weather") is None or row.get("score") is None:
            continue
        samples = extend(previous.get(row["id"], []), row["score"], now)
        row["history"] = samples
        row["trend"] = trend(samples, now)
