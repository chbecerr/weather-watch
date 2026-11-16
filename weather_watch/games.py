"""Fetch NFL and FBS games from ESPN's public scoreboard API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .http import get_json

BASE = "https://site.api.espn.com/apis/site/v2/sports/football"

LEAGUES = {
    # key: (path, extra query params)
    "nfl": ("nfl", {}),
    "cfb": ("college-football", {"groups": "80", "limit": "500"}),  # group 80 = FBS
}

# Venues ESPN flags as outdoor that are effectively sheltered from the weather.
COVERED_VENUES = {
    "SoFi Stadium",  # fixed translucent roof canopy
}


@dataclass
class Game:
    league: str
    name: str
    short_name: str
    kickoff: datetime  # UTC
    time_known: bool
    venue: str
    city: str
    state: str
    country: str
    indoor: bool
    neutral_site: bool
    status: str  # pre / in / post
    espn_id: str
    extra: dict = field(default_factory=dict)

    @property
    def location_label(self) -> str:
        if self.country and self.country not in ("USA", "United States"):
            return f"{self.city}, {self.country}"
        return f"{self.city}, {self.state}" if self.state else self.city


def _parse_time(s: str) -> datetime:
    # ESPN uses e.g. "2026-11-22T18:00Z"
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def parse_events(league: str, payload: dict) -> list[Game]:
    games = []
    for ev in payload.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        venue = comp.get("venue") or {}
        addr = venue.get("address") or {}
        games.append(
            Game(
                league=league,
                name=ev.get("name", ""),
                short_name=ev.get("shortName", ""),
                kickoff=_parse_time(ev["date"]),
                time_known=bool(comp.get("timeValid", True)),
                venue=venue.get("fullName", "Unknown venue"),
                city=addr.get("city", ""),
                state=addr.get("state", ""),
                country=addr.get("country", "USA"),
                indoor=bool(venue.get("indoor", False))
                or venue.get("fullName") in COVERED_VENUES,
                neutral_site=bool(comp.get("neutralSite", False)),
                status=((comp.get("status") or {}).get("type") or {}).get("state", "pre"),
                espn_id=str(ev.get("id", "")),
            )
        )
    return games


def _fetch(league: str, params: dict | None = None) -> dict:
    path, extra = LEAGUES[league]
    return get_json(f"{BASE}/{path}/scoreboard", {**extra, **(params or {})})


def fetch_games(league: str, days: int = 7, now: datetime | None = None) -> list[Game]:
    """Games kicking off within the next `days` days (plus any still in progress).

    ESPN's scoreboard doesn't accept date ranges for football, so we take the
    current week and the following one and filter by kickoff time.
    """
    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(hours=4)  # include games in progress
    window_end = now + timedelta(days=days)

    current = _fetch(league)
    week = (current.get("week") or {}).get("number")
    season_type = (current.get("season") or {}).get("type")

    payloads = [current]
    if week is not None and season_type is not None:
        try:
            nxt = _fetch(league, {"seasontype": season_type, "week": week + 1})
            if not nxt.get("events") and season_type == 2:
                # Regular season over: try the first postseason week.
                nxt = _fetch(league, {"seasontype": 3, "week": 1})
            payloads.append(nxt)
        except Exception:
            pass  # Next week not published yet; current week is still useful.

    seen: set[str] = set()
    games: list[Game] = []
    for p in payloads:
        for g in parse_events(league, p):
            if g.espn_id in seen or g.status == "post":
                continue
            if window_start <= g.kickoff <= window_end:
                seen.add(g.espn_id)
                games.append(g)
    return sorted(games, key=lambda g: g.kickoff)
