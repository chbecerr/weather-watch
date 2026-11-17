"""The weekly digest: how last week's forecasts held up, and what's coming.

Written as GitHub-flavoured Markdown for a GitHub issue; the issue is assigned to the repository
owner, so GitHub emails it. Run by .github/workflows/digest.yml:

    python -m weather_watch.digest --data data.json --results results.json --out digest.md
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import archive, scorecard

LOOKBACK = timedelta(days=8)
ET = ZoneInfo("America/New_York")
TOP_N = 5


def _load(path: str | None) -> dict:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _kickoff(iso: str) -> str:
    when = datetime.fromisoformat(iso).astimezone(ET)
    return f"{when.strftime('%a')} {when.strftime('%I:%M %p').lstrip('0')} ET"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _signed(value: float | None, unit: str) -> str:
    return "n/a" if value is None else f"{value:+g}{unit}"


def _number(value: float | None, unit: str) -> str:
    return "n/a" if value is None else f"{value:g}{unit}"


def _results_section(games: dict, now: datetime) -> tuple[list[str], bool]:
    by_lead = scorecard.evaluate(games, now - LOOKBACK, now)
    summaries = {label: scorecard.summarize(c) for label, c in by_lead.items() if c}
    if not summaries:
        return [
            "## How the forecasts held up",
            "",
            "No finished games have both a recorded forecast and observed weather yet. Forecasts are saved for every upcoming "
            "game and checked once it's over, so the first results appear after this week's games.",
            "",
        ], False
    lines = ["## How the forecasts held up", "", "Games from the last 8 days, checked against the nearest weather station for the "
             "4 hours from kickoff. Errors are the typical (median) size of the miss.", ""]
    lines += ["| Forecast made | Games | Coldest feels-like off by | Wind off by | Rain or snow called |", "|---|---|---|---|---|"]
    names = {"72h": "3 days ahead", "24h": "1 day ahead", "final": "just before kickoff"}
    for label in scorecard.LABELS:
        s = summaries.get(label)
        if not s:
            continue
        w = s["wet"]
        called = w["hit"] + w["miss"] + w["false alarm"] + w["dry"]
        right = w["hit"] + w["dry"]
        lines.append(
            f"| {names[label]} | {s['games']} | {_number(s['feels_min_abs_error'], '°F')} | {_number(s['wind_abs_error'], ' mph')} "
            f"| {right} of {called} right ({w['miss']} missed, {w['false alarm']} false alarms) |"
        )
    lines.append("")
    ranking = summaries.get("24h") or summaries.get("final") or next(iter(summaries.values()))
    label = next(l for l in scorecard.LABELS if summaries.get(l) is ranking)
    lines += ["### Did it rank the roughest games correctly?", ""]
    corr = ranking["rank_correlation"]
    lines.append(
        f"Using the forecast from {names[label]}: "
        + ("the order of the games from calm to rough matched what happened with a correlation of "
           f"**{corr:g}** (1 is a perfect match, 0 is no relationship)." if corr is not None
           else "there wasn't enough variety in the weather to compare orderings.")
    )
    if ranking["top_k"]:
        lines.append(f"- Forecast's roughest {ranking['top_k']}: {', '.join(ranking['forecast_top'])}")
        lines.append(f"- Actually roughest {ranking['top_k']}: {', '.join(ranking['observed_top'])}")
        lines.append(f"- **{ranking['top_overlap']} of {ranking['top_k']}** were on both lists.")
    lines.append("")
    misses = scorecard.biggest_misses(by_lead.get("24h") or by_lead.get("final") or [], 3)
    misses = [m for m in misses if abs(m["observed_partial"] - m["forecast_partial"]) >= 5]
    if misses:
        lines += ["### Biggest surprises", ""]
        for m in misses:
            f, o = m["forecast"], m["observed"]
            lines.append(
                f"- **{m['game']}**: forecast feels like {f['feels_min_f']}° with {f['wind_mph']} mph wind; "
                f"observed {o['feels_min_f']}° and {o['wind_mph']} mph (station {m['station']})."
            )
        lines.append("")
    return lines, True


def _coming_section(data: dict, site_url: str | None) -> list[str]:
    games = [g for g in data.get("games", []) if g.get("week") == "this" and g.get("weather") and not g.get("indoor")]
    if not games:
        return []
    def line(i: int, g: dict, key: str, label: str) -> str:
        w = g["weather"]
        return (f"{i}. **{g['game']}** ({_kickoff(g['kickoff_utc'])}): {label} {g[key]:g}. "
                f"{w['temp_f']}° (feels {w['feels_like_min_f']}°), wind {w['wind_mph']} mph, "
                f"{w.get('precip_chance') if w.get('precip_chance') is not None else '?'}% chance of precipitation.")
    wild = sorted(games, key=lambda g: (-g["score"], g["kickoff_utc"]))[:TOP_N]
    # equal pleasantness: the calmer game first, then the earlier kickoff (as on the site)
    nice = sorted((g for g in games if g.get("pleasant") is not None), key=lambda g: (-g["pleasant"], g["score"], g["kickoff_utc"]))[:TOP_N]
    out = ["## Coming up", "", "### Wildest weather", ""]
    out += [line(i, g, "score", "wildness") for i, g in enumerate(wild, 1)]
    out += ["", "### Nicest weather", ""]
    out += [line(i, g, "pleasant", "pleasantness") for i, g in enumerate(nice, 1)]
    out.append("")
    if site_url:
        out += [f"Full rankings, filters and details: {site_url}/", ""]
    return out


def build(data: dict, results: dict, now: datetime, site_url: str | None = None) -> tuple[str, str, bool]:
    """(title, markdown body, whether there were results to report)."""
    games = results.get("games") if isinstance(results.get("games"), dict) else {}
    today = now.astimezone(ET)
    title = f"Weather Watch weekly check: {today.strftime('%B')} {today.day}, {today.year}"
    section, has_results = _results_section(games, now)
    body = section + _coming_section(data, site_url)
    body += [
        "---",
        "*Observed means the nearest National Weather Service station's measurements, which can differ from conditions at the stadium. "
        "Stations rarely report rain or snow amounts, so only whether it rained or snowed is checked, and the roughness comparison "
        "covers cold, heat and sustained wind. This issue was posted automatically.*",
    ]
    return title, "\n".join(body) + "\n", has_results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="weather_watch.digest", description=__doc__)
    p.add_argument("--data", help="the site's data.json")
    p.add_argument("--results", help="the site's results.json (the forecast archive)")
    p.add_argument("--site-url", help="the site's address, linked from the digest")
    p.add_argument("--out", required=True, help="where to write the Markdown")
    p.add_argument("--github-output", help="a GitHub Actions output file: gets title= and has_results=")
    args = p.parse_args(argv)
    now = datetime.now(timezone.utc)
    title, body, has_results = build(_load(args.data), _load(args.results), now, args.site_url)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(body)
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as fh:
            fh.write(f"title={title}\nhas_results={'true' if has_results else 'false'}\n")
    print(f"{title} ({'with' if has_results else 'without'} results) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
