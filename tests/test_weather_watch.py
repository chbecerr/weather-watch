import unittest
from datetime import datetime, timezone

from weather_watch.games import parse_events
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


if __name__ == "__main__":
    unittest.main()
