"""Off-Pi tests for the Weather app's data side (and its screens, when pygame is installed).

Run on a Mac/PC:  python3 -m unittest discover tests
No network: fetches are faked with canned Open-Meteo / ADMIRALTY responses.
"""
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "apps", "weather"))
import weather_data as wd  # noqa: E402

LOC = {"name": "Herne Bay", "lat": 51.37, "lon": 1.13, "tide_station": "Herne Bay"}
INLAND = {"name": "Maidenhead", "lat": 51.52, "lon": -0.72}

# Hunstanton, Wed 7 Oct 2026 - as the API returns them (UTC; BBC shows 04:06, 11:12, 17:07, 23:30 BST)
HUNSTANTON = [
    {"EventType": "HighWater", "DateTime": "2026-10-07T03:06:08.333", "Height": 6.19},
    {"EventType": "LowWater", "DateTime": "2026-10-07T10:12:00", "Height": 1.56},
    {"EventType": "HighWater", "DateTime": "2026-10-07T16:07:00", "Height": 6.49},
    {"EventType": "LowWater", "DateTime": "2026-10-07T22:30:00", "Height": 2.07},
]


def local(s):
    return datetime.fromisoformat(s).replace(tzinfo=wd.LOCAL)


def open_meteo(start="2026-10-07T00:00", hours=48):
    t0 = datetime.fromisoformat(start)
    times = [(t0 + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(hours)]
    days = sorted({t[:10] for t in times})
    return {
        "current": {"time": "2026-10-07T09:15", "temperature_2m": 13.5, "apparent_temperature": 12.7,
                    "weather_code": 61, "is_day": 1, "wind_speed_10m": 5.8, "wind_direction_10m": 326,
                    "wind_gusts_10m": 11.1, "precipitation": 0.6},
        "hourly": {"time": times, "temperature_2m": [14.0] * hours, "weather_code": [3] * hours,
                   "is_day": [1] * hours, "wind_speed_10m": [float(i % 20) for i in range(hours)],
                   "wind_direction_10m": [270] * hours, "wind_gusts_10m": [float(i % 20 + 6) for i in range(hours)],
                   "precipitation_probability": [i % 100 for i in range(hours)]},
        "daily": {"time": days, "weather_code": [61] * len(days), "temperature_2m_max": [16.0] * len(days),
                  "temperature_2m_min": [9.5] * len(days), "wind_speed_10m_max": [12.2] * len(days),
                  "wind_gusts_10m_max": [23.5] * len(days), "wind_direction_10m_dominant": [337] * len(days),
                  "precipitation_probability_max": [100] * len(days),
                  "sunrise": [d + "T07:13" for d in days], "sunset": [d + "T18:27" for d in days]},
    }


class FakeApi:
    """Answers weather_data's fetches like Open-Meteo and ADMIRALTY would, and records them."""

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def __call__(self, url, headers=None, timeout=20):
        self.calls.append((url, headers))
        if self.fail:
            raise self.fail
        if url.startswith(wd.OPEN_METEO):
            return open_meteo()
        if url.endswith("/TidalEvents?duration=7"):
            return HUNSTANTON
        if "?name=" in url:
            return {"features": [{"properties": {"Id": "0104", "Name": "Herne Bay"}}]}
        raise AssertionError("unexpected url " + url)


class TestWind(unittest.TestCase):
    def test_beaufort_boundaries(self):
        self.assertEqual(wd.beaufort(0), (0, "Calm"))
        self.assertEqual(wd.beaufort(3.4), (1, "Light air"))
        self.assertEqual(wd.beaufort(3.6), (2, "Light breeze"))      # rounds to 4 kn
        self.assertEqual(wd.beaufort(16)[0], 4)
        self.assertEqual(wd.beaufort(17)[0], 5)
        self.assertEqual(wd.beaufort(64)[0], 12)
        self.assertEqual(wd.beaufort(None)[0], 0)

    def test_compass(self):
        self.assertEqual(wd.compass(0), "N")
        self.assertEqual(wd.compass(359), "N")
        self.assertEqual(wd.compass(326), "NW")
        self.assertEqual(wd.compass(247), "WSW")
        self.assertEqual(wd.compass(None), "N")

    def test_weather_url_asks_for_knots_and_uk_time(self):
        url = wd.weather_url(INLAND)
        self.assertIn("wind_speed_unit=kn", url)
        self.assertIn("timezone=Europe%2FLondon", url)
        self.assertIn("wind_gusts_10m", url)

    def test_parse_weather_and_hours_ahead(self):
        w = wd.parse_weather(open_meteo())
        self.assertEqual(w["current"]["time"], local("2026-10-07T09:15"))
        hours = wd.hours_ahead(w, local("2026-10-07T09:20"), 12)
        self.assertEqual(len(hours), 12)
        self.assertEqual(hours[0]["time"], local("2026-10-07T09:00"), "starts with the hour we're in")
        self.assertEqual(wd.today(w, local("2026-10-08T12:00"))["date"].day, 8)
        self.assertEqual(w["days"][0]["sunrise"], local("2026-10-07T07:13"))


class TestTides(unittest.TestCase):
    def setUp(self):
        self.events = wd.parse_tides(HUNSTANTON)

    def test_parse_converts_utc_to_local(self):
        self.assertEqual([e["time"].astimezone(wd.LOCAL).strftime("%H:%M") for e in self.events],
                         ["04:06", "11:12", "17:07", "23:30"])
        self.assertEqual([e["kind"] for e in self.events], ["high", "low", "high", "low"])

    def test_parse_skips_junk_and_keeps_missing_heights(self):
        evs = wd.parse_tides([{"EventType": "HighWater", "DateTime": "2026-10-07T03:06:00"},
                              {"EventType": "Something", "DateTime": "2026-10-07T04:00:00"},
                              {"EventType": "LowWater"}])
        self.assertEqual(len(evs), 1)
        self.assertIsNone(evs[0]["height"])

    def test_height_follows_a_cosine_between_high_and_low(self):
        pts = wd.curve_points(self.events)
        hw, lw = self.events[0], self.events[1]
        self.assertAlmostEqual(wd.height_at(pts, hw["time"]), 6.19)
        mid = hw["time"] + (lw["time"] - hw["time"]) / 2
        self.assertAlmostEqual(wd.height_at(pts, mid), (6.19 + 1.56) / 2)
        # BBC showed 2.4 m at 09:20 BST
        self.assertAlmostEqual(wd.height_at(pts, local("2026-10-07T09:20")), 2.4, delta=0.15)

    def test_curve_reaches_midnight_before_the_first_event(self):
        pts = wd.curve_points(self.events)
        self.assertIsNotNone(wd.height_at(pts, local("2026-10-07T00:00")))
        self.assertIsNotNone(wd.height_at(pts, local("2026-10-07T23:59")))
        self.assertIsNone(wd.height_at(wd.curve_points(self.events[:1]), local("2026-10-07T04:06")))

    def test_state_and_next_events(self):
        now = local("2026-10-07T09:20")
        self.assertEqual(wd.tide_state(self.events, now), "falling")
        self.assertEqual(wd.tide_state(self.events, local("2026-10-07T12:00")), "rising")
        self.assertIsNone(wd.tide_state(self.events, local("2026-10-08T12:00")))
        self.assertEqual(wd.next_event(self.events, now, "high")["height"], 6.49)
        self.assertEqual(len(wd.day_events(self.events, now.date())), 4)

    def test_find_station_prefers_exact_name(self):
        feats = [{"properties": {"Id": "1", "Name": "Herne Bay Pier"}},
                 {"properties": {"Id": "2", "Name": "HERNE BAY"}}]
        self.assertEqual(wd.find_station(feats, "Herne Bay"), {"id": "2", "name": "HERNE BAY"})
        self.assertEqual(wd.find_station(feats[:1], "herne bay")["id"], "1")
        self.assertIsNone(wd.find_station(feats, "Hunstanton"))

    def test_merge_keeps_recent_past_events(self):
        now = datetime(2026, 10, 8, 9, 0, tzinfo=wd.UTC)
        old = [{"DateTime": "2026-10-01T00:00:00"}, {"DateTime": "2026-10-07T16:07:00"},
               {"DateTime": "2026-10-08T05:00:00"}]
        new = [{"DateTime": "2026-10-08T05:00:00"}, {"DateTime": "2026-10-08T11:00:00"}]
        self.assertEqual([e["DateTime"] for e in wd.merge_events(old, new, now)],
                         ["2026-10-07T16:07:00", "2026-10-08T05:00:00", "2026-10-08T11:00:00"])
        self.assertEqual(wd.merge_events(old, [], now), old)


class TestStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.key = os.path.join(self.dir, "key")
        self.t = 1_791_360_000.0                    # 2026-10-07 ~09:20 BST

    def store(self, api, locs=(INLAND, LOC)):
        return wd.Store(list(locs), cache_dir=self.dir + "/cache", key_file=self.key, fetch=api,
                        clock=lambda: self.t)

    def test_fetches_weather_caches_it_and_waits_before_refetching(self):
        api = FakeApi()
        s = self.store(api, [INLAND])
        self.assertEqual(s.refresh(), 1)
        self.assertEqual(s.get(INLAND, "weather")["data"]["current"]["wind_speed_10m"], 5.8)
        self.assertEqual(s.refresh(), 0, "fresh - nothing to do")
        self.t += wd.WEATHER_EVERY + 1
        self.assertEqual(s.refresh(), 1)
        # a new Store (app restart) starts from the cache
        self.assertIsNotNone(self.store(FakeApi(), [INLAND]).get(INLAND, "weather"))

    def test_no_key_means_no_tide_fetch(self):
        api = FakeApi()
        s = self.store(api)
        s.refresh()
        self.assertFalse(any("admiralty" in url for url, _ in api.calls))
        self.assertEqual(s.error(LOC, "tides"), "no tide key")

    def test_tides_find_the_station_once_then_fetch_events(self):
        with open(self.key, "w") as f:
            f.write("secret\n")
        api = FakeApi()
        s = self.store(api, [LOC])
        s.refresh()
        entry = s.get(LOC, "tides")
        self.assertEqual(entry["station"]["id"], "0104")
        self.assertEqual(len(entry["events"]), 4)
        tide_calls = [(u, h) for u, h in api.calls if "admiralty" in u]
        self.assertEqual(tide_calls[0][1], {"Ocp-Apim-Subscription-Key": "secret"})
        self.t += wd.TIDES_EVERY + 1
        api.calls.clear()
        s.refresh()
        self.assertFalse(any("?name=" in u for u, _ in api.calls), "station id is remembered")

    def test_failure_keeps_old_data_and_backs_off(self):
        s = self.store(FakeApi(), [INLAND])
        s.refresh()
        self.t += wd.WEATHER_EVERY + 1
        s.fetch = FakeApi(fail=urllib.error.URLError("down"))
        self.assertEqual(s.refresh(), 1)
        self.assertEqual(s.error(INLAND, "weather"), "offline")
        self.assertIsNotNone(s.get(INLAND, "weather"), "old data kept")
        self.assertEqual(s.refresh(), 0, "waits before retrying")
        self.t += wd.RETRY_AFTER + 1
        s.fetch = FakeApi()
        s.refresh()
        self.assertIsNone(s.error(INLAND, "weather"))

    def test_rejected_key_is_explained(self):
        err = urllib.error.HTTPError("u", 401, "no", {}, None)
        self.assertEqual(wd.explain(err), "tide key rejected")


class TestConfig(unittest.TestCase):
    def test_locations_json(self):
        cfg = wd.load_config()
        self.assertTrue(cfg["locations"])
        for loc in cfg["locations"]:
            self.assertTrue(-90 <= loc["lat"] <= 90 and -180 <= loc["lon"] <= 180, loc)
        self.assertEqual(len({wd.slug(l["name"]) for l in cfg["locations"]}), len(cfg["locations"]))


try:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame  # noqa: F401
    import weather_display
except ImportError:
    weather_display = None


@unittest.skipUnless(weather_display, "pygame not installed")
class TestScreens(unittest.TestCase):
    """Draw every page of every location (with and without data) and poke the controls."""

    def setUp(self):
        import pygame
        self.cfg = {"locations": [INLAND, LOC]}
        self.store = wd.Store(self.cfg["locations"], cache_dir=tempfile.mkdtemp(), key_file="/nonexistent",
                              fetch=FakeApi())
        self.app = weather_display.WeatherDisplay(self.cfg, self.store, screen=pygame.Surface((480, 800)))
        self.now = local("2026-10-07T09:20")

    def fill(self):
        self.store.refresh()
        self.store.entries[(wd.slug(LOC["name"]), "tides")] = {
            "fetched": 0, "station": {"id": "0104", "name": "Herne Bay"},
            "events": HUNSTANTON + [{"EventType": "HighWater", "DateTime": "2026-10-08T04:01:00", "Height": 6.7}]}

    def test_every_page_draws_with_and_without_data(self):
        for filled in (False, True):
            if filled:
                self.fill()
            for i in range(len(self.cfg["locations"])):
                self.app.loc_i = i
                for page in self.app.pages():
                    self.app.page = page
                    self.app.draw(self.now)

    def test_tides_page_only_at_the_coast(self):
        self.assertNotIn("Tides", self.app.pages(INLAND))
        self.assertIn("Tides", self.app.pages(LOC))

    def tap_button(self, name):
        self.app.draw(self.now)
        r = self.app.buttons[name]
        self.app.touch_down(r.center)
        self.app.touch_up(r.center)

    def test_taps_switch_location_and_page(self):
        self.fill()
        self.tap_button("loc1")
        self.assertEqual(self.app.loc_i, 1)
        self.tap_button("page:Tides")
        self.assertEqual(self.app.page, "Tides")
        self.tap_button("tide-next")
        self.assertEqual(self.app.tide_day, 1)
        self.tap_button("loc0")                      # inland: no Tides page, back to Now
        self.assertEqual(self.app.page, "Now")

    def test_redraws_only_when_something_changes(self):
        now = self.now
        self.assertTrue(self.app.frame(now), "first frame")
        self.assertFalse(self.app.frame(now + timedelta(seconds=20)), "same minute, nothing new")
        self.assertTrue(self.app.frame(now + timedelta(minutes=1)), "clock and countdowns move on")
        now += timedelta(minutes=1)
        self.store.refresh()                         # weather arrives
        self.assertTrue(self.app.frame(now))
        self.assertFalse(self.app.frame(now))
        self.tap_button("page:Wind")
        self.assertTrue(self.app.frame(now), "page changed")
        self.app.last_touch -= weather_display.DIM_AFTER + 1
        self.assertTrue(self.app.frame(now), "dimmed")
        self.assertFalse(self.app.frame(now))

    def test_pages_cycle_after_a_minute_untouched(self):
        self.app.cycle_seconds = 15
        self.app.loc_i = 1                           # coastal: Now, Wind, Week, Tides
        self.app.tick()
        self.assertEqual(self.app.page, "Now", "touched recently - leave it alone")
        self.app.last_touch -= weather_display.CYCLE_IDLE + 1
        self.app.last_cycle -= 16
        seen = []
        for _ in range(5):
            self.app.tick()
            seen.append(self.app.page)
            self.app.last_cycle -= 16
        self.assertEqual(seen, ["Wind", "Week", "Tides", "Now", "Wind"])
        self.app.tick()
        self.app.tick()
        self.assertEqual(self.app.page, "Week", "no faster than every cycle_seconds")

    def test_swipe_turns_pages(self):
        self.app.draw(self.now)
        self.app.touch_down((400, 400))
        self.app.touch_up((100, 410))
        self.assertEqual(self.app.page, "Wind")
        self.app.touch_down((100, 400))
        self.app.touch_up((400, 390))
        self.assertEqual(self.app.page, "Now")


if __name__ == "__main__":
    unittest.main()
