# weather-watch

Finds this week's NFL and FBS college football games and ranks them by how
extreme the weather will be at the stadium during the game.

```
  #  Score  Lg   Game         Kickoff           Temp Feel     Wind  Precip       Flags             Venue
--------------------------------------------------------------------------------------------------------
  1   50.6  CFB  UGA @ SC     Sat 11/21  7:30pm   52°  42°  22g44   0.23" rain   WIND RAIN         Williams-Brice Stadium, Columbia, SC
  2   34.3  CFB  BYU @ KU     Sat 11/21 12:00pm   63°  55°  22g33   0.01" rain   WIND              David Booth Kansas Memorial Stadium, Lawrence, KS
  3   32.9  CFB  UNM @ WYO    Fri 11/20  9:00pm   34°  19°  12g30   -            COLD WIND         War Memorial Stadium, Laramie, WY
```

Pure Python standard library, no API keys. Network access needed to `site.api.espn.com`,
`api.weather.gov` and `geocoding-api.open-meteo.com` (city lookup only).

## Usage

```bash
python3 -m weather_watch                 # this week's outdoor NFL + FBS games
python3 -m weather_watch --week next     # next week's (late-week games may have no forecast yet)
python3 -m weather_watch --top 10        # just the 10 nastiest
python3 -m weather_watch --league nfl    # nfl | cfb | all
python3 -m weather_watch --include-indoor --tz America/Chicago
python3 -m weather_watch --json          # machine-readable, includes score breakdown
```

Or install it with `pip install .` and run `weather-watch`.

## How it works

1. **Games**: ESPN's public scoreboard API (`site.api.espn.com`). FBS is
   ESPN group 80. Weeks are ESPN's own (NFL Thursday to Monday-night games,
   college Tuesday to Saturday). "This week" is ESPN's current week; once every
   game in it has been played (NFL on Tuesday and Wednesday) it moves on to the
   following week. On a Monday that means NFL's "this week" is just that night's
   game. Finished games are dropped.
2. **Location**: the venue city is geocoded with Open-Meteo's geocoder (a result is only
   accepted if its state matches the venue's, so a same-named town elsewhere can't be
   picked by mistake; games it can't place are left out) and
   cached in `~/.cache/weather-watch/geocode-v2.json`, so later runs skip this step.
3. **Weather**: hourly forecasts from the US National Weather Service
   (`api.weather.gov`, no key needed), which covers US venues about 7 days
   ahead. The tool looks at the 4 hours starting at kickoff. Games with no
   forecast (outside the US, or more than ~7 days out) are left out; the table
   and the website say how many.
4. **Indoor venues** (ESPN's `indoor` flag, plus SoFi Stadium) are skipped by default.

## Website

`docs/` is a static site (plain HTML/CSS/JS, no build step) that shows the
rankings as a sortable, filterable list that works on phones, with a
This week / Next week switch.

- A GitHub Actions workflow (`.github/workflows/pages.yml`) runs the tool every
  3 hours with `--site-data docs/data.json` and publishes `docs/` to GitHub Pages.
  If the weather data can't be fetched the run fails and the previous site stays up.
- **Score history:** each run downloads the live site's previous `data.json` (`--previous`)
  and adds the new scores to every game's history (kept 8 days). The page shows a ▲/▼ chip
  for games whose score moved 5+ points since about a day ago, and a small history line in
  the game's details. It appears once the site has several hours of history.
- **Weather filters:** chips for Wind, Cold, Heat, Rain, Snow, Storms and Precip likely
  show games forecast to have, at some point during the game: sustained wind over 10 mph,
  feels-like 32° or colder, feels-like 90° or hotter, 0.05″+ rain, 0.1″+ snow, thunderstorms,
  or a 50%+ chance of precipitation. Several chips mean "any of these". Each chip shows how
  many of the games in view match it, and the choice goes in the URL (`?event=snow,wind`).
  These cutoffs are in `docs/app.js` (`EVENTS`) and are separate from the score's flags.
- **Chance of precipitation:** the Weather Service's highest chance during the game
  (`precip_chance` in the data). It's shown on each game but doesn't affect the score.
- **Page features:** a link to each game on ESPN, kickoff in the stadium's time zone
  (when it differs from yours), and an "Only my teams" filter (star a team inside a game's
  details; saved in your browser only). Filters live in the URL, so a link like
  `?week=next&league=NFL&sort=wind` keeps them.
- One-time setup: **Settings > Pages > Build and deployment > Source: GitHub Actions**,
  then merge to `main`. Use the Actions tab's "Run workflow" button for an on-demand refresh.
- Preview locally:

```bash
python3 -m weather_watch --site-data docs/data.json
python3 -m http.server -d docs 8000   # open http://localhost:8000
```

## Score

Each factor adds points only past a comfort threshold, so a calm, dry, mild
game scores 0. Higher is more extreme.

| Factor | Points |
|---|---|
| Cold | 1 per °F the coldest feels-like temperature is below 40°F |
| Heat | 1.5 per °F the hottest feels-like temperature is above 85°F |
| Wind | 2 per mph of sustained wind over 10, plus 0.75 per mph of gusts over 20 |
| Rain | 40 per inch during the game |
| Snow | 15 per inch during the game |
| Thunderstorm | +20 (lightning delays) |

A tag (COLD, WIND, …) appears when a factor contributes 5 or more points.
The weights are in `weather_watch/scoring.py`.

## Caveats

- Forecasts are for the city, not the exact stadium, and get less reliable the
  further out the game is.
- Retractable-roof stadiums use whatever ESPN reports for the game.
- Games with a TBA kickoff use ESPN's placeholder time and are noted as such.

## Tests

```bash
python3 -m unittest
```
