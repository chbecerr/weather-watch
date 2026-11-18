# weather-watch

Finds this week's NFL, FBS and FCS football games and ranks them by how
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
python3 -m weather_watch                 # this week's outdoor NFL, FBS and FCS games
python3 -m weather_watch --week next     # next week's (late-week games may have no forecast yet)
python3 -m weather_watch --top 10        # just the 10 nastiest
python3 -m weather_watch --nicest        # rank by pleasantness (best first) instead of wildness
python3 -m weather_watch --league fcs    # nfl | cfb (FBS) | fcs | all
python3 -m weather_watch --include-indoor --tz America/Chicago
python3 -m weather_watch --json          # machine-readable, includes score breakdown
```

Or install it with `pip install .` and run `weather-watch`.

## How it works

1. **Games**: ESPN's public scoreboard API (`site.api.espn.com`). FBS is
   ESPN group 80 and FCS is group 81. A game ESPN lists under both (FBS vs FCS,
   e.g. Wofford at Ole Miss) is shown once, as an FBS game. Weeks are ESPN's own
   (NFL Thursday to Monday-night games, college Tuesday to Saturday). "This week" is ESPN's current week; once every
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
- **Wildest / Nicest weather:** a switch at the top changes the whole ranking. *Nicest* uses
  the pleasantness score below, its own set of filter chips (Great, Mild, Sunny, Calm, Dry),
  and green-to-red bands. Dome games have no pleasantness score; "Include dome games" lists
  them at the bottom, unscored. The choice goes in the link (`?mode=nice&nice=mild,calm`) and
  is remembered in your browser.
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

### Map

The **Map** toggle shows one day's games as dots on a US map, colored by feels-like temperature, wind or chance of precipitation. The shading between dots is a kernel-smoothed estimate (about 100 miles of influence) that fades out where there are no games, so it shows regional patterns, not a forecast for places without a stadium. It needs no map tiles or libraries: `docs/map.js` projects the state outlines in `docs/us-states.json` (built by `tools/build_us_outline.py` from US Census data via us-atlas, see `docs/NOTICES.txt`) and paints the shading on a canvas. Its tests run with `node --test tests/js/*.test.js`.

## Forecast check and the weekly email

Every forecast is saved, and once a game is over it is checked against what the nearest National
Weather Service station actually measured (`weather_watch/archive.py`, `observations.py`,
`scorecard.py`). This can't be done after the fact: the Weather Service keeps observations for only
about a week, so a game that isn't captured can't be checked later.

- **What is saved:** the forecast of every upcoming outdoor game at three lead times (3 days ahead,
  1 day ahead, and the last run before kickoff), then the observed feels-like temperature, sustained
  wind, cloud cover and whether it rained or snowed during the 4 hours from kickoff. It lives in
  `results.json`, published with the site and carried forward by each run (kept 30 days). The run stops
  rather than start a fresh archive if it can't fetch the old one, unless the site has none yet.
- **What can and can't be checked:** stations rarely report rain or snow *amounts* (and gusts only when
  it gusts), so the scorecard checks temperature, wind, clouds and "did it rain or snow". The full
  wildness score can't be verified; a *partial* score (cold, heat and sustained wind, same ramps) is
  computed from the forecast and from the observation, so the two are comparable.
- **The digest:** `.github/workflows/digest.yml` runs weekly (Tuesdays) and posts a GitHub issue:
  typical forecast errors, rain/snow calls right and wrong, whether the forecast ranked the roughest games
  in the right order (rank correlation and top-5 overlap), the biggest surprises, and the coming week's
  wildest and nicest games. The issue is assigned to, and mentions, the repository owner, so GitHub
  emails it; there are no secrets to set up. Make sure email is on under GitHub Settings > Notifications.
  A scheduled run posts only when there are results; from the Actions tab you can run it by hand, and
  by default that is a dry run that shows the digest in the run's summary (tick "post" to send it).
- **Limits:** a station can be some miles from the stadium and its conditions can differ; stations that
  report no wind are skipped in favour of the next nearest; the first results appear after the first
  games finish, and averages mean little until a few weeks have been collected.

## Score

Higher is more extreme, and a calm, dry, mild game scores 0. Each factor is a ramp
through reference points that mean the same thing for every factor, so 20 points of
cold is as extreme as 20 points of wind:

| Points | Meaning | Cold (feels-like) | Heat (feels-like) | Wind (sustained) | Gusts | Rain | Snow |
|---|---|---|---|---|---|---|---|
| 15 | noticeable: affects the game or crowd | 20°F | 95°F | 20 mph | ~37 mph | 0.3″ | 1″ |
| 40 | severe: about NWS advisory level | 5°F | 105°F | 30 mph | 46 mph | 0.8″ | 3″ |
| 80 | extreme: about warning level | −15°F | 112°F | 40 mph | 58 mph | 1.5″ | 6″ |

Plus a flat 20 for thunderstorms (lightning delays). Nothing scores below its comfort
level (feels-like between 40°F and 85°F, wind under 10 mph, gusts under 20 mph,
no precipitation). Between reference points the ramp is a straight line, and past the
last one it keeps climbing, so −48°F scores higher than −15°F. Sustained wind and
gusts are combined as the larger plus a quarter of the smaller, because gusts track
sustained wind and shouldn't be counted twice. Rain and snow are totals during the
game, not rates. A tag (COLD, WIND, …) appears when a factor contributes 5 or more points.

These reference points are judgment calls, loosely anchored on typical National Weather
Service criteria (which vary by region), not measurements: there is no ground truth for
"how extreme is a game's weather". They are in `weather_watch/scoring.py`, in one place,
and the tests pin them down. Cold is scored on the feels-like temperature, which already
includes wind chill, and wind is scored too, so wind in the cold counts twice on purpose:
it hurts the crowd and the passing game in different ways.

## Pleasantness

A second score, 0 to 100, for how nice it is to sit in the stands for three hours. It is
**not** the extremeness score turned upside down: most games are completely calm by that
measure (it floors at 0), so flipping it would tie dozens of games. Pleasantness has its
own factors and ramps. It starts at 100 and subtracts points:

| Factor | What it uses | Points lost |
|---|---|---|
| Temperature | the feels-like temperature, hour by hour, so a swing counts | 0 from 60 to 72°F; 10 at 50°F, 25 at 40°F, 40 at 32°F, 65 at 20°F; 17 at 82°F, 42 at 90°F, 80 at 100°F |
| Wind | sustained wind and gusts (larger plus a quarter of the smaller) | 0 up to 6 mph; 8 at 10, 22 at 15, 40 at 20, 70 at 30 mph |
| Precipitation | chance and amount (larger plus a quarter of the smaller); thunderstorms +25 | chance: 6 at 30%, 14 at 50%, 24 at 70%, 35 at 100%; rain 12 at 0.1″, 30 at 0.3″, 55 at 0.8″ |
| Clouds | average cloud cover, **only while the sun is up** | 0 under 30%, up to 12 when overcast |

Heat costs more than the same distance of cold, because you can add layers but not take
them off. The sun position is calculated (`weather_watch/solar.py`, NOAA's formulas; checked
against the astral library at 8 places on 5 dates, worst difference 2.5 minutes), so a night
game's clouds cost nothing. The score stops at 0, so when games tie (the roughest ones do) the less wild one ranks nicer, then the earlier kickoff. Bands: 95+ great, 80-94 nice, 60-79 meh, under 60 rough. Like the
other score, the reference points are judgment calls, are in `weather_watch/pleasantness.py`
in one place, and the tests pin them down. Changing them doesn't need a `SCORE_VERSION` bump,
because no pleasantness history is kept.

The site keeps each game's score history, so changing the formula means bumping
`SCORE_VERSION`: the history from the old formula is then discarded instead of showing
every game as having jumped.

## Caveats

- Forecasts are for the city, not the exact stadium, and get less reliable the
  further out the game is.
- Retractable-roof stadiums use whatever ESPN reports for the game.
- Games with a TBA kickoff use ESPN's placeholder time and are noted as such.

## Tests

```bash
python3 -m unittest
```
