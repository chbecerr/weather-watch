import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from weather_watch import cli as cli_mod
from weather_watch import games as games_mod
from weather_watch.games import parse_events
from weather_watch import history
from weather_watch.site import UnhealthyData, build_payload
from weather_watch import weather as weather_mod
from weather_watch import nws as nws_mod
from weather_watch.nws import grid_to_hourly, parse_valid_time
from weather_watch.scoring import SCORE_VERSION, score
from weather_watch.weather import GameWeather, summarize_window


def wx(**kw):
    base = dict(
        temp_f=65, feels_min_f=65, feels_max_f=65, wind_mph=5, gust_mph=8,
        rain_in=0.0, snow_in=0.0, weather_code=1,
    )
    base.update(kw)
    return GameWeather(**base)


class ScoringTests(unittest.TestCase):
    # Reference conditions per severity tier. wind cases set gusts to 0 so only sustained wind is scored.
    TIERS = {
        15: {"cold": dict(feels_min_f=20), "heat": dict(feels_max_f=95), "wind": dict(wind_mph=20, gust_mph=0),
             "rain": dict(rain_in=0.3), "snow": dict(snow_in=1)},
        40: {"cold": dict(feels_min_f=5), "heat": dict(feels_max_f=105), "wind": dict(wind_mph=30, gust_mph=0),
             "rain": dict(rain_in=0.8), "snow": dict(snow_in=3)},
        80: {"cold": dict(feels_min_f=-15), "heat": dict(feels_max_f=112), "wind": dict(wind_mph=40, gust_mph=0),
             "rain": dict(rain_in=1.5), "snow": dict(snow_in=6)},
    }

    def test_mild_day_scores_zero(self):
        self.assertEqual(score(wx()).total, 0)

    def test_nothing_scores_at_or_below_its_comfort_level(self):
        calm = score(wx(feels_min_f=40, feels_max_f=85, wind_mph=10, gust_mph=20, rain_in=0, snow_in=0))
        self.assertEqual(calm.total, 0)

    def test_the_same_severity_scores_the_same_for_every_factor(self):
        for points, cases in self.TIERS.items():
            for factor, kwargs in cases.items():
                with self.subTest(points=points, factor=factor):
                    self.assertAlmostEqual(score(wx(**kwargs)).parts[factor], points, places=1)

    def test_gust_reference_points(self):
        for gust, points in ((35, 10), (46, 40), (58, 80)):
            with self.subTest(gust=gust):
                self.assertAlmostEqual(score(wx(wind_mph=0, gust_mph=gust)).parts["wind"], points, places=1)

    def test_gusts_add_only_a_quarter_of_the_smaller_wind_component(self):
        w = score(wx(wind_mph=20, gust_mph=35)).parts["wind"]  # sustained 15 pts, gust 10 pts
        self.assertAlmostEqual(w, 15 + 0.25 * 10, places=1)
        self.assertAlmostEqual(score(wx(wind_mph=0, gust_mph=46)).parts["wind"], 40, places=1)  # gusts alone still count in full

    def test_storm_is_a_flat_bonus_for_thunderstorm_codes(self):
        for code, expected in ((3, 0), (80, 0), (95, 20), (96, 20), (99, 20)):
            self.assertEqual(score(wx(weather_code=code)).parts["storm"], expected, code)

    def test_every_factor_keeps_rising_with_conditions_even_past_its_last_reference_point(self):
        series = {
            "cold": [dict(feels_min_f=f) for f in (35, 20, 5, -15, -30, -48)],
            "heat": [dict(feels_max_f=f) for f in (86, 95, 105, 112, 118, 125)],
            "wind": [dict(wind_mph=m, gust_mph=0) for m in (11, 20, 30, 40, 50, 60)],
            "rain": [dict(rain_in=r) for r in (0.02, 0.1, 0.3, 0.8, 1.5, 3.0)],
            "snow": [dict(snow_in=s) for s in (0.1, 1, 3, 6, 10, 20)],
        }
        for factor, steps in series.items():
            pts = [score(wx(**kw)).parts[factor] for kw in steps]
            with self.subTest(factor=factor):
                self.assertEqual(pts, sorted(set(pts)), pts)  # strictly increasing
        self.assertGreater(score(wx(feels_min_f=-48)).parts["cold"], 80)  # -48 is worse than -15, not capped at it

    def test_compound_conditions_outrank_any_single_severe_one(self):
        blizzard = score(wx(feels_min_f=5, wind_mph=25, gust_mph=40, snow_in=3, weather_code=75))
        singles = [score(wx(**kw)).total for kw in (dict(feels_min_f=5), dict(snow_in=3), dict(wind_mph=30, gust_mph=0), dict(rain_in=0.8))]
        self.assertGreater(blizzard.total, max(singles))
        self.assertEqual(set(blizzard.tags), {"COLD", "WIND", "SNOW"})

    def test_blizzard_beats_drizzle(self):
        self.assertGreater(score(wx(feels_min_f=5, wind_mph=25, gust_mph=40, snow_in=3)).total,
                           score(wx(rain_in=0.05, weather_code=51)).total)

    def test_a_tag_appears_from_5_points(self):
        self.assertEqual(score(wx(feels_min_f=32)).tags, ["COLD"])   # 8 degrees below 40 -> 6 pts
        self.assertEqual(score(wx(feels_min_f=36)).tags, [])         # 4 degrees below 40 -> 3 pts


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


class LeagueMergeTests(unittest.TestCase):
    """build_report with ESPN and the weather service replaced by stand-ins."""

    def report(self, leagues, listings):
        def weeks(league):
            games = [parse_events(league, {"events": [ev(i, "pre") for i in ids]})
                     for ids in [listings[league]]][0]
            wg = games_mod.WeekGames(label="Week 12", games=games, first=None, last=None)
            return games_mod.LeagueWeeks(this=wg, next=games_mod.WeekGames("Week 13", [], None, None))
        hourly = {"time": ["2026-11-22T18:00"], "temperature_2m": [50], "apparent_temperature": [50], "rain": [0],
                  "showers": [0], "snowfall": [0], "wind_speed_10m": [5], "wind_gusts_10m": [8], "weather_code": [1]}
        with mock.patch.object(games_mod, "fetch_weeks", side_effect=weeks), \
             mock.patch.object(weather_mod, "geocode_many", side_effect=lambda places: {p: (1.0, 2.0) for p in places}), \
             mock.patch.object(nws_mod, "fetch_forecasts", return_value={(1.0, 2.0): {"hourly": hourly, "timezone": "America/Chicago"}}):
            return cli_mod.build_report(leagues, ("this",))

    def test_game_listed_by_both_college_leagues_appears_once_as_fbs(self):
        rows, info, hidden, _ = self.report(["cfb", "fcs"], {"cfb": ["shared", "fbs-only"], "fcs": ["shared", "fcs-only"]})
        self.assertEqual(sorted(r["id"] for r in rows), ["cfb:fbs-only", "cfb:shared", "fcs:fcs-only"])
        self.assertEqual(set(info["this"]), {"CFB", "FCS"})  # both leagues still get their own week label

    def test_fcs_alone_keeps_its_games(self):
        rows, _, _, _ = self.report(["fcs"], {"fcs": ["a", "shared"]})
        self.assertEqual(sorted(r["id"] for r in rows), ["fcs:a", "fcs:shared"])
        self.assertTrue(all(r["league"] == "FCS" for r in rows))


class PostseasonTests(unittest.TestCase):
    CALENDAR = [
        {"label": "Regular Season", "value": "2", "entries": [{"label": "Week 18", "value": "18"}]},
        {"label": "Postseason", "value": "3", "entries": [
            {"label": "Wild Card", "value": "1"}, {"label": "Divisional Round", "value": "2"},
            {"label": "Conference Championship", "value": "3"}, {"label": "Pro Bowl", "value": "4"},
            {"label": "Super Bowl", "value": "5"}]},
    ]

    def current(self, week, season_type, events):
        return {"week": {"number": week}, "season": {"type": season_type}, "events": events,
                "leagues": [{"calendar": self.CALENDAR}]}

    def test_nfl_rounds_are_named_from_espns_calendar(self):
        current = self.current(18, 2, [ev("a", "post")])
        weeks = {(3, 1): [ev("wc", "pre")], (3, 2): [ev("div", "pre")]}
        with mock.patch.object(games_mod, "_fetch", fake_espn(current, weeks)):
            lw = games_mod.fetch_weeks("nfl")
        self.assertEqual((lw.this.label, ids(lw.this)), ("Wild Card", ["wc"]))
        self.assertEqual((lw.next.label, ids(lw.next)), ("Divisional Round", ["div"]))

    def test_pro_bowl_gap_is_stepped_over_to_the_super_bowl(self):
        current = self.current(3, 3, [ev("cc", "post")])
        weeks = {(3, 5): [ev("sb", "pre")]}  # nothing published for week 4 (Pro Bowl)
        with mock.patch.object(games_mod, "_fetch", fake_espn(current, weeks)):
            lw = games_mod.fetch_weeks("nfl")
        self.assertEqual((lw.this.label, ids(lw.this)), ("Super Bowl", ["sb"]))

    def test_college_postseason_is_one_bucket_called_postseason(self):
        # ESPN's college feed puts every bowl and playoff game in postseason "week 1" and calls it
        # "Bowls", which is wrong for FCS, so we don't use that label.
        current = self.current(15, 2, [ev("cc", "post")])
        weeks = {(3, 1): [ev("bowl1", "pre"), ev("bowl2", "pre")]}
        for league in ("cfb", "fcs"):
            with mock.patch.object(games_mod, "_fetch", fake_espn(current, weeks)):
                lw = games_mod.fetch_weeks(league)
            self.assertEqual((lw.this.label, ids(lw.this)), ("Postseason", ["bowl1", "bowl2"]), league)
            self.assertEqual((lw.next.label, lw.next.games), ("Postseason", []), league)  # nothing after the bucket

    def test_after_the_last_game_both_weeks_are_empty_not_an_error(self):
        current = self.current(5, 3, [ev("sb", "post")])
        with mock.patch.object(games_mod, "_fetch", fake_espn(current, {})):
            lw = games_mod.fetch_weeks("nfl")
        self.assertEqual((lw.this.games, lw.next.games), ([], []))


class OffseasonHealthTests(unittest.TestCase):
    NOW = datetime(2026, 2, 20, 12, 0, tzinfo=timezone.utc)

    def row(self, days_out, week="this"):
        when = self.NOW + timedelta(days=days_out)
        return {"week": week, "indoor": False, "weather": {"temp_f": 40}, "kickoff_utc": when.isoformat()}

    def test_games_months_away_with_no_forecast_are_not_an_outage(self):
        # Offseason: this week's games are far off, nothing could have a forecast, so nothing is "hidden soon".
        out = build_payload([], {}, {"this": 14}, now=self.NOW, hidden_soon={"this": 0})
        self.assertEqual(out["hidden"], {"this": 14})

    def test_near_term_games_all_missing_forecasts_is_an_outage(self):
        with self.assertRaises(UnhealthyData):
            build_payload([], {}, {"this": 14}, now=self.NOW, hidden_soon={"this": 3})

    def test_a_far_out_row_does_not_vouch_for_a_broken_forecast_source(self):
        # A game 20 days out can't have a forecast, so having one proves nothing about today's near-term games.
        with self.assertRaises(UnhealthyData):
            build_payload([self.row(20)], {}, {"this": 2}, now=self.NOW, hidden_soon={"this": 2})

    def test_one_working_near_term_forecast_is_enough(self):
        build_payload([self.row(2)], {}, {"this": 5}, now=self.NOW, hidden_soon={"this": 4})

    def test_report_counts_only_near_term_games_as_hidden_soon(self):
        far, near = "2099-01-01T00:00Z", "2020-01-01T00:00Z"  # far future / long past: independent of today's date
        def weeks(league):
            games = parse_events(league, {"events": [ev("far", "pre", far), ev("near", "pre", near)]})
            return games_mod.LeagueWeeks(this=games_mod.WeekGames("Week 1", games, None, None),
                                         next=games_mod.WeekGames("Week 2", [], None, None))
        with mock.patch.object(games_mod, "fetch_weeks", side_effect=weeks), \
             mock.patch.object(weather_mod, "geocode_many", side_effect=lambda places: {p: (1.0, 2.0) for p in places}), \
             mock.patch.object(nws_mod, "fetch_forecasts", return_value={}):  # the forecast source returns nothing
            report = cli_mod.build_report(["nfl"], ("this",))
        self.assertEqual((report.hidden["this"], report.hidden_soon["this"]), (2, 1))


class HourlyTests(unittest.TestCase):
    def forecast(self):
        times = [f"2026-11-22T{h:02d}:00" for h in range(16, 24)]
        n = len(times)
        return {"hourly": {
            "time": times,
            "temperature_2m": [40, 39, 38, 37, 36, 35, 34, 33],
            "apparent_temperature": [32, 31, 30, 29, 28, 27, 26, 25],
            "rain": [0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07],
            "showers": [0.0] * n,
            "snowfall": [0.0, 0.0, 0.14, 0.26, 0.0, 0.0, 0.0, 0.0],
            "wind_speed_10m": [10, 12, 14, 16, 18, 20, 22, 24],
            "wind_gusts_10m": [15, 11, 20, 25, 30, 35, 40, 45],  # the 17:00 gust (11) is below that hour's sustained wind (12)
            "weather_code": [3] * n,
            "precip_probability": [10, 10, 60, 60, None, None, None, None],
        }}

    def test_each_hour_of_the_game_window_is_kept_in_order(self):
        w = summarize_window(self.forecast(), datetime(2026, 11, 22, 17, 30, tzinfo=timezone.utc))  # hours 17-20
        self.assertEqual([h["t"] for h in w.hours], ["2026-11-22T17:00Z", "2026-11-22T18:00Z", "2026-11-22T19:00Z", "2026-11-22T20:00Z"])
        self.assertEqual([h["temp_f"] for h in w.hours], [39, 38, 37, 36])
        self.assertEqual([h["feels_f"] for h in w.hours], [31, 30, 29, 28])
        self.assertEqual([h["wind_mph"] for h in w.hours], [12, 14, 16, 18])

    def test_values_are_rounded_and_gusts_never_below_sustained_wind(self):
        w = summarize_window(self.forecast(), datetime(2026, 11, 22, 17, 0, tzinfo=timezone.utc))
        self.assertEqual([h["gust_mph"] for h in w.hours], [12, 20, 25, 30])  # 17:00 gust 11 is raised to the wind speed, 12
        self.assertEqual([h["rain_in"] for h in w.hours], [0.01, 0.02, 0.03, 0.04])
        self.assertEqual([h["snow_in"] for h in w.hours], [0.0, 0.14, 0.26, 0.0])

    def test_missing_chance_stays_none_not_zero(self):
        w = summarize_window(self.forecast(), datetime(2026, 11, 22, 19, 0, tzinfo=timezone.utc))  # hours 19-22
        self.assertEqual([h["chance"] for h in w.hours], [60, None, None, None])

    def test_hours_agree_with_the_summary(self):
        w = summarize_window(self.forecast(), datetime(2026, 11, 22, 16, 0, tzinfo=timezone.utc))
        self.assertEqual(max(h["wind_mph"] for h in w.hours), w.wind_mph)
        self.assertEqual(min(h["feels_f"] for h in w.hours), w.feels_min_f)
        self.assertEqual(round(sum(h["rain_in"] for h in w.hours), 2), round(w.rain_in, 2))
        self.assertAlmostEqual(sum(h["snow_in"] for h in w.hours), w.snow_in, delta=0.02)  # small snow isn't rounded away


class PrecipChanceTests(unittest.TestCase):
    def grid(self):
        return {
            "temperature": {"uom": "wmoUnit:degC", "values": [
                {"validTime": "2026-11-22T12:00:00+00:00/PT12H", "value": 10}]},
            "probabilityOfPrecipitation": {"uom": "wmoUnit:percent", "values": [
                {"validTime": "2026-11-22T12:00:00+00:00/PT6H", "value": 20},
                {"validTime": "2026-11-22T18:00:00+00:00/PT3H", "value": 70}]},
        }

    def test_chance_is_carried_to_every_hour_of_its_interval_and_unknown_is_none(self):
        h = grid_to_hourly(self.grid())["hourly"]
        by_hour = dict(zip(h["time"], h["precip_probability"]))
        self.assertEqual(by_hour["2026-11-22T12:00"], 20)   # not divided up like rainfall
        self.assertEqual(by_hour["2026-11-22T17:00"], 20)
        self.assertEqual(by_hour["2026-11-22T19:00"], 70)
        self.assertIsNone(by_hour["2026-11-22T21:00"])      # no value given for this hour

    def test_game_window_takes_the_highest_chance(self):
        fc = grid_to_hourly(self.grid())
        kickoff = datetime(2026, 11, 22, 16, 30, tzinfo=timezone.utc)  # hours 16, 17, 18, 19
        self.assertEqual(summarize_window(fc, kickoff).precip_chance, 70)
        early = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)      # hours 12-15
        self.assertEqual(summarize_window(fc, early).precip_chance, 20)

    def test_no_chance_data_gives_none_not_zero(self):
        grid = self.grid()
        del grid["probabilityOfPrecipitation"]
        fc = grid_to_hourly(grid)
        kickoff = datetime(2026, 11, 22, 16, 0, tzinfo=timezone.utc)
        self.assertIsNone(summarize_window(fc, kickoff).precip_chance)
        old_style = {"hourly": {"time": ["2026-11-22T16:00"], "temperature_2m": [50], "apparent_temperature": [50],
                                "rain": [0], "showers": [0], "snowfall": [0], "wind_speed_10m": [5],
                                "wind_gusts_10m": [8], "weather_code": [1]}}
        self.assertIsNone(summarize_window(old_style, kickoff).precip_chance)  # forecasts without the field still work


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
                json.dump({"score_version": SCORE_VERSION,
                           "games": [{"id": "NFL:1", "history": [[iso(3), 4.0]]}, {"id": "NFL:2"}]}, fh)
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


class ScoreVersionTests(unittest.TestCase):
    def test_payload_records_the_scoring_version(self):
        self.assertEqual(build_payload([], {}, {"this": 0})["score_version"], SCORE_VERSION)

    def test_history_from_another_scoring_formula_is_discarded(self):
        import json, os, tempfile
        game = {"id": "NFL:1", "history": [[iso(3), 4.0]]}
        with tempfile.TemporaryDirectory() as d:
            for name, version, expect_kept in (("current.json", SCORE_VERSION, True),
                                               ("older.json", SCORE_VERSION - 1, False),
                                               ("unversioned.json", None, False)):  # files from before versioning are version 1
                path = os.path.join(d, name)
                payload = {"games": [game]}
                if version is not None:
                    payload["score_version"] = version
                with open(path, "w") as fh:
                    json.dump(payload, fh)
                self.assertEqual(bool(history.load_previous(path)), expect_kept, name)


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
