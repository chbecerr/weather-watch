"""weather-watch: rank this week's NFL + FBS games by how extreme the stadium weather is."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import games as games_mod
from . import nws
from . import weather as wx
from .http import NetworkError
from .scoring import score


def build_report(leagues: list[str], days: int, source: str = "auto") -> list[dict]:
    games: list[games_mod.Game] = []
    failures: list[Exception] = []
    for lg in leagues:
        try:
            games.extend(games_mod.fetch_games(lg, days=days))
        except Exception as err:  # one league failing shouldn't sink the other
            failures.append(err)
            print(f"warning: couldn't load {lg.upper()} schedule: {err}", file=sys.stderr)
    if failures and len(failures) == len(leagues):
        raise failures[0]

    outdoor = [g for g in games if not g.indoor]
    places = {(g.city, g.state, g.country) for g in outdoor}
    coords = wx.geocode_many(places)

    points = sorted({c for c in coords.values() if c})
    forecasts = get_forecasts(points, source)

    rows = []
    for g in games:
        row = {
            "league": g.league.upper(),
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
            "weather": None,
            "note": "",
            "source": None,
        }
        if g.indoor:
            row["note"] = "indoor"
        else:
            c = coords.get((g.city, g.state, g.country))
            fc = forecasts.get(c) if c else None
            w = wx.summarize_window(fc, g.kickoff) if fc else None
            if w is None:
                row["note"] = "no forecast (location not found or too far out)"
                row["score"] = None
            else:
                s = score(w)
                row.update(score=s.total, factors=s.parts, tags=s.tags)
                row["source"] = fc.get("source", "open-meteo")
                row["weather"] = {
                    "temp_f": round(w.temp_f),
                    "feels_like_min_f": round(w.feels_min_f),
                    "feels_like_max_f": round(w.feels_max_f),
                    "wind_mph": round(w.wind_mph),
                    "gust_mph": round(w.gust_mph),
                    "rain_in": round(w.rain_in, 2),
                    "snow_in": round(w.snow_in, 1),
                    "conditions": w.conditions,
                }
                if not g.time_known:
                    row["note"] = "kickoff time TBA; forecast uses placeholder time"
        rows.append(row)

    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), r["kickoff_utc"]))
    return rows


def get_forecasts(points: list[tuple[float, float]], source: str) -> dict:
    """Forecasts keyed by point. 'auto' tries Open-Meteo, then falls back to NWS."""
    if not points:
        return {}
    if source in ("auto", "open-meteo"):
        try:
            # In auto mode, give up quickly so a blocked network falls back fast.
            kw = {"retries": 2, "timeout": 10.0} if source == "auto" else {}
            return dict(zip(points, wx.fetch_forecasts(points, **kw)))
        except NetworkError as err:
            if source == "open-meteo":
                raise
            print(
                f"warning: {err}\n"
                "         falling back to the US National Weather Service "
                "(US venues only, ~7 days ahead)",
                file=sys.stderr,
            )
    return nws.fetch_forecasts(points)


def _fmt_kickoff(iso: str, tz: ZoneInfo, tba: bool) -> str:
    dt = datetime.fromisoformat(iso).astimezone(tz)
    return dt.strftime("%a %m/%d") + ("   TBA" if tba else dt.strftime(" %I:%M%p").lower().replace(" 0", "  "))


def print_table(rows: list[dict], tz: ZoneInfo) -> None:
    if not rows:
        print("No games found.")
        return
    header = (
        f"{'#':>3}  {'Score':>5}  {'Lg':<3}  {'Game':<11}  {'Kickoff':<16}  "
        f"{'Temp':>4} {'Feel':>4}  {'Wind':>7}  {'Precip':<11}  {'Flags':<16}  Venue"
    )
    print(header)
    print("-" * len(header))
    for i, r in enumerate(rows, 1):
        w = r["weather"]
        if w:
            if w["snow_in"] >= 0.1:
                precip = f"{w['snow_in']}\" snow"
            elif w["rain_in"] >= 0.01:
                precip = f"{w['rain_in']:.2f}\" rain"
            else:
                precip = "-"
            wx_cols = (
                f"{w['temp_f']:>3}° {w['feels_like_min_f']:>3}°  "
                f"{w['wind_mph']:>2}g{w['gust_mph']:<3}  {precip:<11}  {' '.join(r['tags']):<16}"
            )
        else:
            wx_cols = f"({r['note']})".ljust(4 + 1 + 4 + 2 + 7 + 2 + 11 + 2 + 16)
        sc = "    -" if r["score"] is None else f"{r['score']:5.1f}"
        venue = r["venue"]
        if r["location"] not in venue:
            venue = f"{venue}, {r['location']}"
        print(
            f"{i:>3}  {sc}  {r['league']:<3}  {r['game'][:11]:<11}  "
            f"{_fmt_kickoff(r['kickoff_utc'], tz, r['time_tba']):<16}  {wx_cols}  {venue}"
        )
    names = {"open-meteo": "Open-Meteo", "nws": "US National Weather Service"}
    used = sorted({names.get(r["source"], r["source"]) for r in rows if r["source"]})
    print("\nTemp = at kickoff; Feel = coldest feels-like during the game; Wind = max sustained g gust (mph).")
    if used:
        print(f"Forecast source: {' + '.join(used)}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="weather-watch", description=__doc__)
    p.add_argument("--league", choices=["nfl", "cfb", "all"], default="all")
    p.add_argument("--days", type=int, default=7, help="look-ahead window in days (default 7, max 16)")
    p.add_argument("--top", type=int, default=0, help="only show the N most extreme games")
    p.add_argument("--include-indoor", action="store_true", help="also list dome/indoor games")
    p.add_argument("--tz", default="America/New_York", help="timezone for kickoff times")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    p.add_argument(
        "--source", choices=["auto", "open-meteo", "nws"], default="auto",
        help="weather source; auto tries Open-Meteo, then the National Weather Service",
    )
    args = p.parse_args(argv)

    leagues = ["nfl", "cfb"] if args.league == "all" else [args.league]
    try:
        rows = build_report(leagues, days=min(max(args.days, 1), 16), source=args.source)
    except NetworkError as err:
        print(f"error: {err}", file=sys.stderr)
        print(
            "Check that this computer can open HTTPS sites (VPN, firewall, proxy or\n"
            "antivirus 'HTTPS scanning' can block or stall connections).",
            file=sys.stderr,
        )
        return 1
    if not args.include_indoor:
        rows = [r for r in rows if not r["indoor"]]
    if args.top:
        rows = rows[: args.top]

    if args.json:
        json.dump(rows, sys.stdout, indent=2)
        print()
    else:
        print_table(rows, ZoneInfo(args.tz))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
