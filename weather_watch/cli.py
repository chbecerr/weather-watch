"""weather-watch: rank this week's NFL, FBS and FCS games by how extreme the stadium weather is."""

from __future__ import annotations

import argparse
import json
import sys
from typing import NamedTuple
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import archive, observations
from . import games as games_mod
from . import nws
from . import weather as wx
from .http import NetworkError
from .pleasantness import band, pleasantness
from .scoring import score
from .solar import daylight_fraction
from .history import NOTABLE_MOVE, load_previous
from .weather import load_cache, save_cache
from .site import SOON_DAYS, UnhealthyData, build_payload, write_payload


LEAGUE_NAMES = {"NFL": "NFL", "CFB": "FBS", "FCS": "FCS"}


FORECAST_SAFE_DAYS = SOON_DAYS  # the Weather Service looks ~7 days ahead; inside this, a missing forecast is a problem


class Report(NamedTuple):
    rows: list[dict]  # games with a forecast (and indoor games), most extreme first
    week_info: dict  # {week: {league: {"label", "first", "last"}}}
    hidden: dict  # {week: outdoor games left out because there is no forecast for them}
    hidden_soon: dict  # the part of `hidden` that should have had one (kicks off within FORECAST_SAFE_DAYS)


def build_report(leagues: list[str], weeks: tuple[str, ...] = ("this", "next")) -> Report:
    """Rows for the requested weeks, plus week labels and counts of games left out."""
    tagged: list[tuple[str, games_mod.Game]] = []
    seen: set[str] = set()  # an FBS-vs-FCS game is listed by both leagues; keep the first
    week_info: dict[str, dict] = {w: {} for w in weeks}
    failures: list[Exception] = []
    for lg in leagues:
        try:
            lw = games_mod.fetch_weeks(lg)
        except Exception as err:  # one league failing shouldn't sink the other
            failures.append(err)
            print(f"warning: couldn't load {lg.upper()} schedule: {err}", file=sys.stderr)
            continue
        for key in weeks:
            wg = getattr(lw, key)
            week_info[key][lg.upper()] = {
                "label": wg.label,
                "first": wg.first.isoformat() if wg.first else None,
                "last": wg.last.isoformat() if wg.last else None,
            }
            for g in wg.games:
                if g.espn_id in seen:
                    continue
                seen.add(g.espn_id)
                tagged.append((key, g))
    if failures and len(failures) == len(leagues):
        raise failures[0]

    places = {(g.city, g.state, g.country) for _, g in tagged if not g.indoor}
    coords = wx.geocode_many(places)
    points = sorted({c for c in coords.values() if c})
    forecasts = nws.fetch_forecasts(points) if points else {}

    rows = []
    hidden = {w: 0 for w in weeks}
    hidden_soon = {w: 0 for w in weeks}
    soon = datetime.now(timezone.utc) + timedelta(days=FORECAST_SAFE_DAYS)
    for key, g in tagged:
        row = {
            "id": f"{g.league}:{g.espn_id}",
            "week": key,
            "week_label": week_info[key][g.league.upper()]["label"],
            "league": g.league.upper(),
            "link": g.link,
            "venue_tz": None,
            "lat": None,  # the city's coordinates: used to find the nearest weather station afterwards
            "lon": None,
            "game": g.short_name,
            "matchup": g.name,
            "kickoff_utc": g.kickoff.isoformat(),
            "time_tba": not g.time_known,
            "venue": g.venue,
            "location": g.location_label,
            "indoor": g.indoor,
            "status": g.status,
            "score": 0.0,
            "factors": {},
            "tags": [],
            "pleasant": None,  # 0-100, only for outdoor games with a forecast
            "pleasant_parts": {},
            "daylight": None,  # share of the game with the sun up
            "weather": None,
            "note": "",
        }
        if g.indoor:
            row["note"] = "indoor"
        else:
            c = coords.get((g.city, g.state, g.country))
            fc = forecasts.get(c) if c else None
            w = wx.summarize_window(fc, g.kickoff) if fc else None
            if w is None:
                hidden[key] += 1  # no forecast: leave the game out
                if g.kickoff <= soon:
                    hidden_soon[key] += 1
                continue
            s_ = score(w)
            row.update(score=s_.total, factors=s_.parts, tags=s_.tags, venue_tz=fc.get("timezone"), lat=round(c[0], 4), lon=round(c[1], 4))
            hour_starts = [datetime.fromisoformat(h["t"].replace("Z", "+00:00")) for h in w.hours]
            daylight = daylight_fraction(c[0], c[1], hour_starts)
            nice = pleasantness(w, daylight)
            row.update(pleasant=nice.total, pleasant_parts=nice.parts, daylight=round(daylight, 2))
            row["weather"] = {
                "temp_f": round(w.temp_f),
                "feels_like_min_f": round(w.feels_min_f),
                "feels_like_max_f": round(w.feels_max_f),
                "wind_mph": round(w.wind_mph),
                "gust_mph": round(w.gust_mph),
                "rain_in": round(w.rain_in, 2),
                "snow_in": round(w.snow_in, 1),
                "precip_chance": None if w.precip_chance is None else round(w.precip_chance),
                "cloud_cover": None if w.cloud_cover is None else round(w.cloud_cover),
                "hourly": w.hours,
                "conditions": w.conditions,
            }
            if not g.time_known:
                row["note"] = "kickoff time TBA; forecast uses placeholder time"
        rows.append(row)

    rows.sort(key=lambda r: (-(r["score"] or 0), r["kickoff_utc"]))
    return Report(rows, week_info, hidden, hidden_soon)


def update_archive(rows: list[dict], previous_path: str | None, out_path: str) -> None:
    """Add this run to the forecast archive and write it. Never raises: the archive is the one thing
    that can't be rebuilt (the Weather Service keeps observations for only a week), so a problem
    in here must not lose it or block the site."""
    now = datetime.now(timezone.utc)
    games = archive.load(previous_path)
    stations = load_cache(observations.STATIONS_CACHE)
    note = ""
    try:
        recorded = archive.record_forecasts(games, rows, now)
        found, gave_up = archive.observe_finished(
            games, now, lambda lat, lon, start: observations.fetch_observed(lat, lon, start, cache=stations)
        )
        removed = archive.prune(games, now)
        note = f"{recorded} upcoming games recorded, {found} finished games now have observations, {gave_up} given up on, {removed} pruned"
    except Exception as err:  # noqa: BLE001 - see the docstring
        note = f"warning: stopped early ({type(err).__name__}: {err})"
    save_cache(stations, observations.STATIONS_CACHE)
    write_payload(archive.to_payload(games, now), out_path, compact=True)
    with_obs = sum(1 for r in games.values() if r.get("observed"))
    print(f"forecast archive: {note}; {len(games)} games stored, {with_obs} with observations", file=sys.stderr)


def rank_nicest(rows: list[dict]) -> list[dict]:
    """Best first. Equal pleasantness (the worst games all bottom out at 0): the less wild game
    first, then the earlier kickoff. Dome games have no pleasantness score and go last."""
    return sorted(rows, key=lambda r: (r["pleasant"] is None, -(r["pleasant"] or 0), r["score"] or 0, r["kickoff_utc"]))


def _fmt_kickoff(iso: str, tz: ZoneInfo, tba: bool) -> str:
    dt = datetime.fromisoformat(iso).astimezone(tz)
    return dt.strftime("%a %m/%d") + ("   TBA" if tba else dt.strftime(" %I:%M%p").lower().replace(" 0", "  "))


def print_table(rows: list[dict], tz: ZoneInfo, week_info: dict, hidden: int = 0, nicest: bool = False) -> None:
    if not rows:
        print("No games with a forecast yet.")
        if hidden:
            print(f"({hidden} game{'s' if hidden != 1 else ''} not shown: no forecast yet)")
        return
    header = (
        f"{'#':>3}  {'Nice' if nicest else 'Score':>5}  {'Lg':<3}  {'Game':<11}  {'Kickoff':<16}  "
        f"{'Temp':>4} {'Feel':>4}  {'Wind':>7}  {'Precip':<11}  {'Flags':<16}  Venue"
    )
    print(header)
    print("-" * len(header))
    for i, r in enumerate(rows, 1):
        w = r["weather"]
        value = r["pleasant"] if nicest else r["score"]
        flags = band(r["pleasant"]).upper() if nicest and r["pleasant"] is not None else " ".join(r["tags"])
        if w:
            if w["snow_in"] >= 0.1:
                precip = f"{w['snow_in']}\" snow"
            elif w["rain_in"] >= 0.01:
                precip = f"{w['rain_in']:.2f}\" rain"
            else:
                precip = "-"
            wx_cols = (
                f"{w['temp_f']:>3}° {w['feels_like_min_f']:>3}°  "
                f"{w['wind_mph']:>2}g{w['gust_mph']:<3}  {precip:<11}  {flags:<16}"
            )
        else:
            wx_cols = f"({r['note']})".ljust(4 + 1 + 4 + 2 + 7 + 2 + 11 + 2 + 16)
        sc = "    -" if value is None or (nicest and r["indoor"]) else f"{value:5.1f}"
        venue = r["venue"]
        if r["location"] not in venue:
            venue = f"{venue}, {r['location']}"
        print(
            f"{i:>3}  {sc}  {r['league']:<3}  {r['game'][:11]:<11}  "
            f"{_fmt_kickoff(r['kickoff_utc'], tz, r['time_tba']):<16}  {wx_cols}  {venue}"
        )
    print("\nTemp = at kickoff; Feel = coldest feels-like during the game; Wind = max sustained g gust (mph).")
    labels = " · ".join(
        f"{LEAGUE_NAMES.get(lg, lg)} {info['label']}" for lg, info in week_info.items() if info["first"]
    )
    print(f"Showing: {labels or 'no games scheduled'}")
    if hidden:
        print(f"{hidden} game{'s' if hidden != 1 else ''} not shown: no forecast yet (US National Weather Service looks about 7 days ahead).")
    else:
        print("Forecast: US National Weather Service (US venues only, about 7 days ahead).")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="weather-watch", description=__doc__)
    p.add_argument("--league", choices=["nfl", "cfb", "fcs", "all"], default="all", help="cfb = FBS")
    p.add_argument(
        "--week", choices=["this", "next"], default="this",
        help="football week to show, using ESPN's weeks (default: this)",
    )
    p.add_argument("--top", type=int, default=0, help="only show the first N games of the ranking")
    p.add_argument(
        "--nicest", action="store_true",
        help="rank by pleasantness (0-100, best first) instead of by how extreme the weather is",
    )
    p.add_argument("--include-indoor", action="store_true", help="also list dome/indoor games")
    p.add_argument("--tz", default="America/New_York", help="timezone for kickoff times")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    p.add_argument("--site-data", metavar="PATH", help="write the website's data file (all games) to PATH")
    p.add_argument(
        "--previous-results", metavar="PATH",
        help="with --site-data: the previously published results.json (the forecast archive) to add to",
    )
    p.add_argument(
        "--results-out", metavar="PATH",
        help="with --site-data: write the updated forecast archive here (published as results.json)",
    )
    p.add_argument(
        "--previous", metavar="PATH",
        help="with --site-data: the previously published data file, used to carry score history forward",
    )
    args = p.parse_args(argv)

    leagues = ["nfl", "cfb", "fcs"] if args.league == "all" else [args.league]
    weeks = ("this", "next") if args.site_data else (args.week,)
    try:
        rows, week_info, hidden, hidden_soon = build_report(leagues, weeks)
    except NetworkError as err:
        print(f"error: {err}", file=sys.stderr)
        print(
            "Check that this computer can open HTTPS sites (VPN, firewall, proxy or\n"
            "antivirus 'HTTPS scanning' can block or stall connections).",
            file=sys.stderr,
        )
        return 1

    if args.site_data:
        previous = load_previous(args.previous)
        try:
            payload = build_payload(rows, week_info, hidden, previous=previous, hidden_soon=hidden_soon)
        except UnhealthyData as err:
            print(f"error: not writing {args.site_data}: {err}", file=sys.stderr)
            return 1
        write_payload(payload, args.site_data)
        if args.results_out:
            update_archive(rows, args.previous_results, args.results_out)
        trends = [g["trend"] for g in payload["games"] if g.get("trend")]
        notable = sum(1 for t in trends if abs(t["delta"]) >= NOTABLE_MOVE)
        print(
            f"wrote {len(rows)} games to {args.site_data} "
            f"({len(trends)} compared with an earlier score, {notable} moved {NOTABLE_MOVE:g}+ points)",
            file=sys.stderr,
        )
        return 0

    if not args.include_indoor:
        rows = [r for r in rows if not r["indoor"]]
    if args.nicest:
        rows = rank_nicest(rows)
    if args.top:
        rows = rows[: args.top]

    if args.json:
        json.dump(rows, sys.stdout, indent=2)
        print()
    else:
        print_table(rows, ZoneInfo(args.tz), week_info[args.week], hidden[args.week], nicest=args.nicest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
