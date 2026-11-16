import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from weather_watch import games as games_mod
from weather_watch.games import parse_events
from weather_watch import history
from weather_watch.site import UnhealthyData, build_payload
from weather_watch import weather as weather_mod
from weather_watch import nws as nws_mod
from weather_watch.nws import grid_to_hourly, parse_valid_time
from weather_watch.scoring import score
from weather_watch.weather import GameWeather, summarize_window


def wx(**kw):
    base = dict(
        temp_f=65, feels_min_f=65, feels_max_f=65, wind_mph=5, gust_mph=8,
        rain_in=0.0, snow_in=0.0, weather_code=1,
    )
    base.update(kw)
    return GameWeather(**base)


class ScoringTests(unittest.TestCase):
    def test_mild_day_scores_zero(self):
        self.assertEqual(score(wx()).total, 0)

    def test_each_factor_contributes(self):
        self.assertEqual(score(wx(feels_min_f=10)).parts["cold"], 30)
        self.assertEqual(score(wx(feels_max_f=100)).parts["heat"], 22.5)
        self.assertEqual(score(wx(wind_mph=25, gust_mph=20)).parts["wind"], 30)
        self.assertEqual(score(wx(rain_in=0.5)).parts["rain"], 20)
        self.assertEqual(score(wx(snow_in=2)).parts["snow"], 30)
        self.assertEqual(score(wx(weather_code=95)).parts["storm"], 20)

    def test_blizzard_beats_drizzle(self):
        blizzard = score(wx(feels_min_f=5, wind_mph=25, gust_mph=40, snow_in=3, weather_code=75))
        drizzle = score(wx(rain_in=0.05, weather_code=51))
        self.assertGreater(blizzard.total, drizzle.total)
        self.assertEqual(set(blizzard.tags), {"COLD", "WIND", "SNOW"})


class WindowTests(unittest.TestCase):
    def test_summarizes_four_game_hours(self):
        times = [f"2026-11-22T{h:02d}:00" for h in range(16, 24)]
        n = len(times)
        fc = {"hourly": {
            "time": times,
            "temperature_2m": list(range(40, 40 + n)),
            "apparent_temperature": list(range(30, 30 + n)),
            "rain": [0.1] * n,
            "showers": [0.0] * n,
            "snowfall": [0.0] * n,
            "wind_speed_10m": [10, 10, 12, 30, 11, 10, 10, 10],
            "wind_gusts_10m": [15] * n,
            "weather_code": [3, 3, 61, 63, 61, 3, 3, 95],
        }}
        kickoff = datetime(2026, 11, 22, 18, 25, tzinfo=timezone.utc)  # 18:00-21:00
        w = summarize_window(fc, kickoff)
        self.assertEqual(w.temp_f, 42)
        self.assertEqual(w.feels_min_f, 32)
        self.assertEqual(w.feels_max_f, 35)
        self.assertEqual(w.wind_mph, 30)
        self.assertEqual(w.gust_mph, 30)  # gust never reported below sustained wind
        self.assertAlmostEqual(w.rain_in, 0.4)
        self.assertEqual(w.weather_code, 63)  # the 23:00 storm is after the game

    def test_out_of_range_returns_none(self):
        fc = {"hourly": {"time": ["2026-11-22T00:00"]}}
        self.assertIsNone(summarize_window(fc, datetime(2026, 12, 25, tzinfo=timezone.utc)))


class ParseTests(unittest.TestCase):
    def test_parse_espn_event(self):
        payload = {"events": [{
            "id": "1", "name": "Miami Dolphins at Buffalo Bills", "shortName": "MIA @ BUF",
            "date": "2026-11-22T18:00Z",
            "competitions": [{
                "timeValid": True, "neutralSite": False,
                "venue": {"fullName": "Highmark Stadium",
                          "address": {"city": "Orchard Park", "state": "NY", "country": "USA"},
                          "indoor": False},
                "status": {"type": {"state": "pre"}},
            }],
        }, {
            "id": "2", "name": "x", "shortName": "NYJ @ LAC", "date": "2026-11-22T21:05Z",
            "competitions": [{"venue": {"fullName": "SoFi Stadium", "indoor": False,
                                        "address": {"city": "Inglewood", "state": "CA"}}}],
        }]}
        g, sofi = parse_events("nfl", payload)
        self.assertEqual(g.kickoff, datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc))
        self.assertEqual(g.location_label, "Orchard Park, NY")
        self.assertFalse(g.indoor)
        self.assertTrue(sofi.indoor)


class NwsTests(unittest.TestCase):
    def test_parse_valid_time(self):
        start, hours = parse_valid_time("2026-11-15T18:00:00+00:00/P1DT6H")
        self.assertEqual(start, datetime(2026, 11, 15, 18, tzinfo=timezone.utc))
        self.assertEqual(hours, 30)
        self.assertEqual(parse_valid_time("2026-11-15T18:00:00+00:00/PT2H")[1], 2)

    def test_grid_converted_to_hourly_table(self):
        grid = {
            "temperature": {"uom": "wmoUnit:degC", "values": [
                {"validTime": "2026-11-22T18:00:00+00:00/PT2H", "value": 0}]},
            "windSpeed": {"uom": "wmoUnit:km_h-1", "values": [
                {"validTime": "2026-11-22T18:00:00+00:00/PT2H", "value": 16.09344}]},
            "quantitativePrecipitation": {"uom": "wmoUnit:mm", "values": [
                {"validTime": "2026-11-22T18:00:00+00:00/PT2H", "value": 25.4}]},
            "snowfallAmount": {"uom": "wmoUnit:mm", "values": [
                {"validTime": "2026-11-22T18:00:00+00:00/PT2H", "value": 0}]},
            "weather": {"values": [
                {"validTime": "2026-11-22T18:00:00+00:00/PT1H",
                 "value": [{"weather": "thunderstorms", "coverage": "likely"}]},
                {"validTime": "2026-11-22T19:00:00+00:00/PT1H",
                 "value": [{"weather": "thunderstorms", "coverage": "slight_chance"}]}]},
        }
        h = grid_to_hourly(grid)["hourly"]
        self.assertEqual(h["time"], ["2026-11-22T18:00", "2026-11-22T19:00"])
        self.assertEqual(h["temperature_2m"], [32, 32])
        self.assertAlmostEqual(h["wind_speed_10m"][0], 10)
        self.assertAlmostEqual(h["rain"][0], 0.5)  # 1" over 2 hours, spread evenly
        self.assertEqual(h["weather_code"], [95, 0])  # slight chance doesn't count


class LinkAndTimezoneTests(unittest.TestCase):
    def event(self, links):
        e = ev("1", "pre")
        e["links"] = links
        return {"events": [e]}

    def test_espn_game_link_is_kept(self):
        link = [{"rel": ["summary", "desktop", "event"], "href": "https://www.espn.com/nfl/game/_/gameId/1/a-b"}]
        (g,) = parse_events("nfl", self.event(link))
        self.assertEqual(g.link, "https://www.espn.com/nfl/game/_/gameId/1/a-b")

    def test_non_espn_or_missing_links_are_dropped(self):
        for links in ([], None, [{"rel": ["summary"], "href": "javascript:alert(1)"}],
                      [{"rel": ["summary"], "href": "https://evil.example/x"}],
                      [{"rel": ["clubhouse"], "href": "https://www.espn.com/nfl/team"}]):
            (g,) = parse_events("nfl", self.event(links))
            self.assertEqual(g.link, "", links)

    def test_forecast_carries_the_stadiums_timezone(self):
        points = {"properties": {"forecastGridData": "https://api.weather.gov/gridpoints/CAE/65,61", "timeZone": "America/New_York"}}
        grid = {"properties": {"temperature": {"uom": "wmoUnit:degC", "values": [
            {"validTime": "2026-11-22T00:00:00+00:00/PT2H", "value": 10}]}}}
        fake = lambda url, *a, **k: points if "/points/" in url else grid
        with mock.patch.object(nws_mod, "get_json", side_effect=fake), \
             mock.patch.object(nws_mod, "load_cache", return_value={}), mock.patch.object(nws_mod, "save_cache"):
            out = nws_mod.fetch_forecasts([(34.0, -81.03)])
        self.assertEqual(out[(34.0, -81.03)]["timezone"], "America/New_York")
        self.assertIn("hourly", out[(34.0, -81.03)])


class GeocodeTests(unittest.TestCase):
    RESULTS = {"results": [
        {"name": "Columbia", "country_code": "US", "country": "United States", "admin1": "Missouri", "latitude": 38.95, "longitude": -92.33},
        {"name": "Columbia", "country_code": "US", "country": "United States", "admin1": "South Carolina", "latitude": 34.0, "longitude": -81.03},
        {"name": "Mexico City", "country_code": "MX", "country": "Mexico", "admin1": "CDMX", "latitude": 19.4, "longitude": -99.1},
    ]}

    def geocode(self, *place, payload=None):
        with mock.patch.object(weather_mod, "get_json", return_value=self.RESULTS if payload is None else payload):
            return weather_mod.geocode(*place)

    def test_picks_the_town_in_the_venues_state(self):
        self.assertEqual(self.geocode("Columbia", "SC", "USA"), (34.0, -81.03))
        self.assertEqual(self.geocode("Columbia", "MO", "USA"), (38.95, -92.33))

    def test_refuses_a_same_named_town_in_the_wrong_state(self):
        self.assertIsNone(self.geocode("Columbia", "TN", "USA"))

    def test_refuses_when_state_is_unknown_or_city_missing(self):
        self.assertIsNone(self.geocode("Columbia", "", "USA"))
        self.assertIsNone(self.geocode("", "SC", "USA"))
        self.assertIsNone(self.geocode("Nowhere", "SC", "USA", payload={}))

    def test_abroad_requires_matching_country(self):
        self.assertEqual(self.geocode("Mexico City", "", "Mexico"), (19.4, -99.1))
        self.assertIsNone(self.geocode("Mexico City", "", "Canada"))


T0 = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def iso(hours_ago):
    return (T0 - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


class HistoryTests(unittest.TestCase):
    def test_extend_appends_and_prunes_old_samples(self):
        old = [[iso(24 * 9), 5.0], [iso(24), 10.0]]
        out = history.extend(old, 30.0, T0)
        self.assertEqual([v for _, v in out], [10.0, 30.0])  # the 9-day-old sample is gone

    def test_extend_replaces_a_sample_from_a_rerun_within_two_hours(self):
        out = history.extend([[iso(1), 10.0]], 12.0, T0)
        self.assertEqual([v for _, v in out], [12.0])

    def test_extend_ignores_garbage_samples(self):
        out = history.extend([["not a date", 1], [iso(6), 2.0]], 3.0, T0)
        self.assertEqual([v for _, v in out], [2.0, 3.0])

    def test_trend_compares_to_about_a_day_ago(self):
        samples = [[iso(48), 1.0], [iso(24), 12.3], [iso(9), 20.0], [iso(3), 30.0], [iso(0), 34.4]]
        t = history.trend(samples, T0)
        self.assertEqual(t, {"delta": 22.1, "hours": 24, "prev": 12.3})  # not the 48h-old sample

    def test_trend_uses_oldest_available_when_under_a_day_of_history(self):
        t = history.trend([[iso(9), 10.0], [iso(3), 11.0], [iso(0), 40.0]], T0)
        self.assertEqual((t["hours"], t["delta"]), (9, 30.0))

    def test_no_trend_without_a_baseline_old_enough(self):
        self.assertIsNone(history.trend([[iso(0), 5.0]], T0))
        self.assertIsNone(history.trend([[iso(3), 5.0], [iso(0), 9.0]], T0))

    def test_load_previous_reads_history_and_tolerates_junk(self):
        import json, os, tempfile
        with tempfile.TemporaryDirectory() as d:
            good = os.path.join(d, "data.json")
            with open(good, "w") as fh:
                json.dump({"games": [{"id": "NFL:1", "history": [[iso(3), 4.0]]}, {"id": "NFL:2"}]}, fh)
            self.assertEqual(history.load_previous(good), {"NFL:1": [[iso(3), 4.0]]})
            bad = os.path.join(d, "bad.json")
            with open(bad, "w") as fh:
                fh.write("{oops")
            self.assertEqual(history.load_previous(bad), {})
        self.assertEqual(history.load_previous(None), {})
        self.assertEqual(history.load_previous("/nonexistent/data.json"), {})

    def test_apply_skips_games_without_a_forecast(self):
        rows = [
            {"id": "NFL:1", "weather": {"temp_f": 1}, "score": 30.0},
            {"id": "NFL:2", "weather": None, "score": 0.0},
        ]
        history.apply(rows, {"NFL:1": [[iso(24), 10.0]]}, T0)
        self.assertEqual(rows[0]["trend"]["delta"], 20.0)
        self.assertNotIn("history", rows[1])

    def test_payload_adds_history_only_when_given_previous(self):
        row = {"id": "NFL:1", "week": "this", "indoor": False, "weather": {"temp_f": 1}, "score": 5.0}
        out = build_payload([dict(row)], {}, {"this": 0}, now=T0)
        self.assertNotIn("history", out["games"][0])
        out = build_payload([dict(row)], {}, {"this": 0}, now=T0, previous={})
        self.assertEqual(len(out["games"][0]["history"]), 1)


class SiteDataTests(unittest.TestCase):
    def row(self, week="this", indoor=False):
        return {"week": week, "indoor": indoor, "weather": None if indoor else {"temp_f": 50}}

    def test_payload_wraps_rows_with_timestamp(self):
        now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
        out = build_payload([self.row()], {"this": {"NFL": {"label": "Week 11"}}}, {"this": 0}, now=now)
        self.assertEqual(out["generated_at"], "2026-11-20T12:00:00+00:00")
        self.assertEqual(out["weeks"]["this"]["NFL"]["label"], "Week 11")
        self.assertEqual(len(out["games"]), 1)

    def test_refuses_when_this_weeks_outdoor_games_all_lack_forecasts(self):
        with self.assertRaises(UnhealthyData):
            build_payload([self.row(indoor=True)], {}, {"this": 3})

    def test_allows_missing_forecasts_for_next_week_only(self):
        build_payload([self.row()], {}, {"this": 0, "next": 5})

    def test_allows_partial_coverage_and_empty_weeks(self):
        build_payload([self.row()], {}, {"this": 2})
        build_payload([], {}, {"this": 0})


def ev(game_id, state, when="2026-11-22T18:00Z"):
    return {
        "id": game_id, "name": game_id, "shortName": game_id, "date": when,
        "competitions": [{
            "venue": {"fullName": "V", "address": {"city": "C", "state": "S"}},
            "status": {"type": {"state": state}},
        }],
    }


def fake_espn(current, weeks):
    """Stand-in for games._fetch: no params = current scoreboard, else look up the week."""
    def fetch(league, params=None):
        if not params:
            return current
        return {"events": weeks.get((params["seasontype"], params["week"]), [])}
    return fetch


def ids(week_games):
    return [g.espn_id for g in week_games.games]


class WeekTests(unittest.TestCase):
    def test_monday_keeps_current_week_with_only_the_unplayed_game(self):
        current = {"week": {"number": 10}, "season": {"type": 2},
                   "events": [ev("a", "post", "2026-11-13T01:15Z"), ev("mnf", "pre", "2026-11-17T01:15Z")]}
        weeks = {(2, 11): [ev("c", "pre"), ev("d", "pre")]}
        with mock.patch.object(games_mod, "_fetch", fake_espn(current, weeks)):
            lw = games_mod.fetch_weeks("nfl")
        self.assertEqual((lw.this.label, ids(lw.this)), ("Week 10", ["mnf"]))
        self.assertEqual((lw.next.label, ids(lw.next)), ("Week 11", ["c", "d"]))
        self.assertEqual(lw.this.first, datetime(2026, 11, 13, 1, 15, tzinfo=timezone.utc))  # finished games still set the range
        self.assertEqual(lw.this.last, datetime(2026, 11, 17, 1, 15, tzinfo=timezone.utc))

    def test_finished_week_rolls_forward(self):
        current = {"week": {"number": 10}, "season": {"type": 2}, "events": [ev("a", "post"), ev("b", "post")]}
        weeks = {(2, 11): [ev("c", "pre")], (2, 12): [ev("e", "pre")]}
        with mock.patch.object(games_mod, "_fetch", fake_espn(current, weeks)):
            lw = games_mod.fetch_weeks("nfl")
        self.assertEqual((lw.this.label, ids(lw.this)), ("Week 11", ["c"]))
        self.assertEqual((lw.next.label, ids(lw.next)), ("Week 12", ["e"]))

    def test_end_of_regular_season_moves_into_postseason(self):
        current = {"week": {"number": 18}, "season": {"type": 2}, "events": [ev("a", "post")]}
        weeks = {(3, 1): [ev("wc", "pre")], (3, 2): [ev("div", "pre")]}
        with mock.patch.object(games_mod, "_fetch", fake_espn(current, weeks)):
            lw = games_mod.fetch_weeks("nfl")
        self.assertEqual((lw.this.label, ids(lw.this)), ("Postseason", ["wc"]))
        self.assertEqual((lw.next.label, ids(lw.next)), ("Postseason", ["div"]))

    def test_unpublished_next_week_is_empty_not_an_error(self):
        current = {"week": {"number": 10}, "season": {"type": 2}, "events": [ev("a", "pre")]}
        with mock.patch.object(games_mod, "_fetch", fake_espn(current, {})):
            lw = games_mod.fetch_weeks("cfb")
        self.assertEqual(ids(lw.this), ["a"])
        self.assertEqual((lw.next.games, lw.next.first), ([], None))


if __name__ == "__main__":
    unittest.main()
