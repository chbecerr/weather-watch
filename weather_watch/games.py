"""Fetch NFL, FBS and FCS games from ESPN's public scoreboard API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .http import get_json

BASE = "https://site.api.espn.com/apis/site/v2/sports/football"

LEAGUES = {
    # key: (path, extra query params)
    "nfl": ("nfl", {}),
    "cfb": ("college-football", {"groups": "80", "limit": "500"}),  # group 80 = FBS
    "fcs": ("college-football", {"groups": "81", "limit": "500"}),  # group 81 = FCS
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
    link: str = ""  # ESPN's page for the game

    @property
    def location_label(self) -> str:
        if self.country and self.country not in ("USA", "United States"):
            return f"{self.city}, {self.country}"
        return f"{self.city}, {self.state}" if self.state else self.city


def _parse_time(s: str) -> datetime:
    # ESPN uses e.g. "2026-11-22T18:00Z"
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s).astimezone(timezone.utc)


ESPN_LINK_PREFIX = "https://www.espn.com/"


def _game_link(ev: dict) -> str:
    """ESPN's own page for the game; anything that isn't an espn.com link is ignored."""
    for link in ev.get("links") or []:
        href = link.get("href") or ""
        if "summary" in (link.get("rel") or []) and href.startswith(ESPN_LINK_PREFIX):
            return href
    return ""


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
                link=_game_link(ev),
            )
        )
    return games


def _fetch(league: str, params: dict | None = None) -> dict:
    path, extra = LEAGUES[league]
    return get_json(f"{BASE}/{path}/scoreboard", {**extra, **(params or {})})


@dataclass
class WeekGames:
    """One league's games for one football week, using ESPN's own week numbering."""

    label: str  # "Week 11", "Postseason", ...
    games: list[Game]  # games not yet finished
    first: datetime | None  # kickoff range of every game ESPN lists for the week
    last: datetime | None


@dataclass
class LeagueWeeks:
    this: WeekGames
    next: WeekGames


_SEASON_LABELS = {1: "Preseason Week", 2: "Week"}


def _label(season_type: int, week: int) -> str:
    if season_type == 3:
        return "Postseason"
    return f"{_SEASON_LABELS.get(season_type, 'Week')} {week}"


def _load(league: str, season_type: int, week: int) -> list[Game]:
    return parse_events(league, _fetch(league, {"seasontype": season_type, "week": week}))


def _candidates(season_type: int, week: int) -> list[tuple[int, int]]:
    """Weeks that could follow (season_type, week), crossing into the next phase."""
    if season_type == 2:
        return [(2, week + 1), (3, 1)]
    if season_type == 3:
        return [(3, week + 1), (3, week + 2)]
    return [(season_type, week + 1), (2, 1)]


def _following(league: str, season_type: int, week: int) -> tuple[tuple[int, int], list[Game]]:
    """The next week ESPN has games for, or an empty week if it has none yet."""
    for st, wk in _candidates(season_type, week):
        try:
            found = _load(league, st, wk)
        except Exception:
            continue  # that week isn't published yet
        if found:
            return (st, wk), found
    st, wk = _candidates(season_type, week)[0]
    return (st, wk), []


def _week(season_type: int | None, week: int | None, all_games: list[Game], label: str | None = None) -> WeekGames:
    times = [g.kickoff for g in all_games]
    return WeekGames(
        label=label or _label(season_type, week),
        games=[g for g in all_games if g.status != "post"],
        first=min(times) if times else None,
        last=max(times) if times else None,
    )


def fetch_weeks(league: str) -> LeagueWeeks:
    """This week's and next week's games, following ESPN's definition of a week.

    "This week" is ESPN's current week. Once every game in it has been played
    (e.g. NFL on Tuesday), it moves on to the following week so you're never
    looking at an empty list. Finished games are dropped.
    """
    current = _fetch(league)
    week = (current.get("week") or {}).get("number")
    season_type = (current.get("season") or {}).get("type")
    current_games = parse_events(league, current)

    if week is None or season_type is None:  # unexpected payload: show what's unfinished
        return LeagueWeeks(
            this=_week(None, None, current_games, label="This week"),
            next=_week(None, None, [], label="Next week"),
        )

    if any(g.status != "post" for g in current_games):
        this_key, this_games = (season_type, week), current_games
    else:
        this_key, this_games = _following(league, season_type, week)
    next_key, next_games = _following(league, *this_key)
    return LeagueWeeks(
        this=_week(*this_key, this_games),
        next=_week(*next_key, next_games),
    )
